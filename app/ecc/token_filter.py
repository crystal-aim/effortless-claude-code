"""Token filter hook for Claude Code.

Generates and manages a PreToolUse hook that rewrites verbose CLI commands
to include output truncation, reducing token consumption by 60-90%.
Supports hybrid filtering: regex fast path + local MLX inference fallback.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.ecc.hashes import extra_with_dict_hash, hash_path, read_install_hash
from app.ecc.hooks import (
    _entry_id,
    _load_settings,
    _write_settings,
    resolve_settings_target,
)

logger = logging.getLogger(__name__)

HOOK_ID = "pre:bash:croxy-token-filter"
HOOK_EVENT = "PreToolUse"
HOOK_MATCHER = "Bash"
SCRIPT_DEST = Path.home() / ".claude" / "croxy-token-filter.sh"
TRACKER_SOURCE = "croxy"
TRACKER_CATEGORY = "token-filter"

DEFAULT_MAX_LINES = 300
DEFAULT_TAIL_LINES = 150
DEFAULT_MLX_ENABLED = False
DEFAULT_MLX_THRESHOLD = 2000
DEFAULT_MLX_URL = "http://localhost:8899"

SETTING_MAX_LINES = "token_filter.max_lines"
SETTING_TAIL_LINES = "token_filter.tail_lines"
SETTING_MLX_ENABLED = "token_filter.mlx_enabled"
SETTING_MLX_THRESHOLD = "token_filter.mlx_threshold"
SETTING_MLX_URL = "token_filter.mlx_url"

MLX_FILTER_DEST = Path.home() / ".claude" / "croxy-mlx-filter.py"


# ---------------------------------------------------------------------------
# Script generation
# ---------------------------------------------------------------------------

_SCRIPT_TEMPLATE = r'''#!/usr/bin/env bash
# croxy-token-filter — PreToolUse hook for Claude Code
# Rewrites verbose CLI commands to truncate output, saving tokens.
# Managed by claude-croxy. Do not edit manually.
python3 -c '
import sys, json, re

try:
    stdin = json.load(sys.stdin)
except Exception:
    sys.exit(0)

cmd = stdin.get("tool_input", {}).get("command", "")
if not cmd:
    sys.exit(0)

s = cmd.strip()

# Skip: already has truncation pipe
if re.search(r"\|\s*(head|tail|wc|less|more)\b", s):
    sys.exit(0)

is_compound = bool(re.search(r"&&|\|\||;", s))
has_subst = "$(" in s or "`" in s

# Skip compound/substitution for regex path (unchanged behavior)
if is_compound or has_subst:
    # MLX can still classify compound commands for HEAD/TAIL (not SUMMARIZE)
    if not %%MLX_ENABLED%%:
        sys.exit(0)
    new = None
else:
    MAX = %%MAX_LINES%%
    TAIL = %%TAIL_LINES%%

    new = None

    # git log without -n / --max-count
    if re.match(r"git\s+log\b", s) and not re.search(r"\s(-n\s|--max-count)", s):
        new = re.sub(r"^(git\s+log)", r"\1 -n 50", s)

    # git diff
    elif re.match(r"git\s+diff\b", s):
        new = s + f" | head -{MAX}"

    # git status
    elif re.match(r"git\s+status\b", s):
        new = s + " | head -100"

    # find
    elif re.match(r"find\s", s):
        new = s + f" | head -{MAX}"

    # grep -r / rg
    elif re.match(r"(grep\s+.*(-r\b|-R\b|--recursive)|rg\s)", s):
        new = s + f" | head -{MAX}"

    # ls -R / tree
    elif re.match(r"(ls\s+.*-\w*R|tree\b)", s):
        new = s + f" | head -{MAX}"

    # cat / bat -> head
    elif re.match(r"(cat|bat)\s+(?!-)", s):
        new = re.sub(r"^(cat|bat)", f"head -{MAX}", s)

    # test runners (summary at end, use tail)
    elif re.match(r"(pytest|python\s+-m\s+pytest|jest|npx\s+jest|cargo\s+test|go\s+test|bundle\s+exec\s+rspec)\b", s):
        new = s + f" 2>&1 | tail -{TAIL}"

    # docker
    elif re.match(r"docker\s+(ps|images|logs)\b", s):
        new = s + f" | head -{MAX}"

    # ps
    elif re.match(r"ps\s+", s):
        new = s + f" | head -{MAX}"

# --- MLX classification fallback ---
if new is None and %%MLX_ENABLED%%:
    MAX = %%MAX_LINES%%
    TAIL = %%TAIL_LINES%%
    try:
        import urllib.request
        mlx_base = "%%MLX_URL%%".rstrip("/")
        mr = urllib.request.urlopen(urllib.request.Request(mlx_base + "/v1/models"), timeout=2)
        model_id = json.loads(mr.read()).get("data", [{}])[0].get("id", "unknown")
        prompt = (
            "Classify this CLI command output volume. "
            "Reply with EXACTLY one word: SKIP, HEAD, TAIL, or SUMMARIZE.\n"
            "SKIP = output is small or truncation would break it\n"
            "HEAD = long output, keep first lines (listings, search results)\n"
            "TAIL = long output, keep last lines (build/test summaries)\n"
            "SUMMARIZE = very verbose, needs intelligent summarization\n\n"
            "Command: " + s + "\nDecision:"
        )
        req_body = json.dumps({
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 5,
            "temperature": 0,
        }).encode()
        req = urllib.request.Request(
            mlx_base + "/v1/chat/completions",
            data=req_body,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=3)
        result = json.loads(resp.read())
        decision = result["choices"][0]["message"]["content"].strip().split()[0].upper()

        if decision.startswith("HEAD"):
            new = s + f" | head -{MAX}"
        elif decision.startswith("TAIL"):
            new = s + f" 2>&1 | tail -{TAIL}"
        elif decision.startswith("SUMMAR") and not is_compound and not has_subst:
            new = f"({s}) 2>&1 | python3 %%MLX_FILTER_PATH%%"
    except Exception:
        pass

if new and new != s:
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": new}}}
    print(json.dumps(out))
'
'''


_MLX_FILTER_TEMPLATE = r'''#!/usr/bin/env python3
# croxy-mlx-filter — Summarizes large CLI output via local MLX model.
# Managed by claude-croxy. Do not edit manually.
import sys
import json
import urllib.request

THRESHOLD = %%THRESHOLD%%
MLX_URL = "%%MLX_URL%%"
MAX_INPUT_CHARS = 12000
MAX_TOKENS = 800

content = sys.stdin.read()

if len(content) <= THRESHOLD:
    sys.stdout.write(content)
    sys.exit(0)

line_count = content.count("\n")
char_count = len(content)

if len(content) > MAX_INPUT_CHARS:
    half = MAX_INPUT_CHARS // 2
    model_input = content[:half] + "\n\n...[middle truncated]...\n\n" + content[-half:]
else:
    model_input = content

prompt = (
    "Extract key information from this CLI output verbatim.\n\n"
    "LIST each of these if present:\n"
    "- Error messages (quote exact text including error codes)\n"
    "- Warning messages (quote exact text)\n"
    "- File paths that are NOT part of the repeating pattern\n"
    "- Entries with unusual status, non-zero error counts, or other anomalies\n"
    "- Summary numbers: totals, durations, counts\n\n"
    "Quote exact values. Do not paraphrase. Do not say \"all X are Y\".\n\n"
    "Output:\n" + model_input + "\n\nExtracted:"
)

mlx_base = MLX_URL.rstrip("/")
try:
    mr = urllib.request.urlopen(urllib.request.Request(mlx_base + "/v1/models"), timeout=2)
    model_id = json.loads(mr.read()).get("data", [{}])[0].get("id", "unknown")
except Exception:
    model_id = "unknown"

req_body = json.dumps({
    "model": model_id,
    "messages": [{"role": "user", "content": prompt}],
    "max_tokens": MAX_TOKENS,
    "temperature": 0.1,
}).encode()

try:
    req = urllib.request.Request(
        mlx_base + "/v1/chat/completions",
        data=req_body,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    summary = result["choices"][0]["message"]["content"]
    print(f"[MLX filtered — original: {line_count} lines, {char_count} chars]\n\n{summary}")
except Exception:
    lines = content.split("\n")
    max_lines = %%MAX_LINES%%
    print("\n".join(lines[:max_lines]))
    if len(lines) > max_lines:
        print(f"\n... ({len(lines) - max_lines} more lines truncated)")
'''


def generate_mlx_filter_script(
    threshold: int = DEFAULT_MLX_THRESHOLD,
    mlx_url: str = DEFAULT_MLX_URL,
    max_lines: int = DEFAULT_MAX_LINES,
) -> str:
    return (
        _MLX_FILTER_TEMPLATE
        .replace("%%THRESHOLD%%", str(threshold))
        .replace("%%MLX_URL%%", mlx_url)
        .replace("%%MAX_LINES%%", str(max_lines))
    )


def generate_script(
    max_lines: int = DEFAULT_MAX_LINES,
    tail_lines: int = DEFAULT_TAIL_LINES,
    mlx_enabled: bool = DEFAULT_MLX_ENABLED,
    mlx_url: str = DEFAULT_MLX_URL,
    mlx_filter_path: str = str(MLX_FILTER_DEST),
) -> str:
    return (
        _SCRIPT_TEMPLATE
        .replace("%%MAX_LINES%%", str(max_lines))
        .replace("%%TAIL_LINES%%", str(tail_lines))
        .replace("%%MLX_ENABLED%%", str(mlx_enabled))
        .replace("%%MLX_URL%%", mlx_url)
        .replace("%%MLX_FILTER_PATH%%", mlx_filter_path)
    )


def _hook_entry() -> dict:
    return {
        "matcher": HOOK_MATCHER,
        "hooks": [{"type": "command", "command": str(SCRIPT_DEST)}],
        "description": "Croxy token filter — truncates verbose CLI output to save tokens",
        "id": HOOK_ID,
    }


def _is_our_entry(e: dict) -> bool:
    if _entry_id(e) == HOOK_ID:
        return True
    for h in e.get("hooks", []) or []:
        if isinstance(h, dict) and str(SCRIPT_DEST) in str(h.get("command", "")):
            return True
    return False


# ---------------------------------------------------------------------------
# Config (persisted in Setting table)
# ---------------------------------------------------------------------------

def get_config() -> dict:
    from app.db import session_scope
    from app.models import Setting

    with session_scope() as db:
        ml = db.get(Setting, SETTING_MAX_LINES)
        tl = db.get(Setting, SETTING_TAIL_LINES)
        me = db.get(Setting, SETTING_MLX_ENABLED)
        mt = db.get(Setting, SETTING_MLX_THRESHOLD)
        mu = db.get(Setting, SETTING_MLX_URL)
        ml_val = ml.value if ml else None
        tl_val = tl.value if tl else None
        me_val = me.value if me else None
        mt_val = mt.value if mt else None
        mu_val = mu.value if mu else None

    return {
        "max_lines": int(ml_val) if ml_val else DEFAULT_MAX_LINES,
        "tail_lines": int(tl_val) if tl_val else DEFAULT_TAIL_LINES,
        "mlx_enabled": me_val.lower() in ("true", "1", "yes") if me_val else DEFAULT_MLX_ENABLED,
        "mlx_threshold": int(mt_val) if mt_val else DEFAULT_MLX_THRESHOLD,
        "mlx_url": mu_val if mu_val else DEFAULT_MLX_URL,
    }


def save_config(
    max_lines: int,
    tail_lines: int,
    mlx_enabled: bool = DEFAULT_MLX_ENABLED,
    mlx_threshold: int = DEFAULT_MLX_THRESHOLD,
    mlx_url: str = DEFAULT_MLX_URL,
) -> dict:
    from app.db import session_scope
    from app.models import Setting

    pairs = [
        (SETTING_MAX_LINES, str(max_lines)),
        (SETTING_TAIL_LINES, str(tail_lines)),
        (SETTING_MLX_ENABLED, str(mlx_enabled).lower()),
        (SETTING_MLX_THRESHOLD, str(mlx_threshold)),
        (SETTING_MLX_URL, mlx_url),
    ]
    with session_scope() as db:
        for key, val in pairs:
            row = db.get(Setting, key)
            if row is None:
                db.add(Setting(key=key, value=val))
            else:
                row.value = val

    if SCRIPT_DEST.exists():
        _write_script(max_lines, tail_lines, mlx_enabled, mlx_threshold, mlx_url)

    return {
        "max_lines": max_lines,
        "tail_lines": tail_lines,
        "mlx_enabled": mlx_enabled,
        "mlx_threshold": mlx_threshold,
        "mlx_url": mlx_url,
    }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_status(target: str, project_path: Optional[str]) -> dict:
    try:
        target_path = resolve_settings_target(target, project_path)
    except ValueError:
        target_path = None

    installed = False
    if target_path:
        try:
            settings = _load_settings(target_path)
            hooks = settings.get("hooks", {}).get(HOOK_EVENT, [])
            installed = any(_is_our_entry(e) for e in hooks if isinstance(e, dict))
        except (ValueError, OSError):
            pass

    cfg = get_config()
    return {
        "installed": installed,
        "script_exists": SCRIPT_DEST.exists(),
        "script_path": str(SCRIPT_DEST),
        "mlx_filter_script_exists": MLX_FILTER_DEST.exists(),
        "mlx_filter_script_path": str(MLX_FILTER_DEST),
        "target_path": str(target_path) if target_path else None,
        "config": cfg,
    }


# ---------------------------------------------------------------------------
# Install / Uninstall
# ---------------------------------------------------------------------------

def _write_mlx_filter_script(threshold: int, mlx_url: str, max_lines: int = DEFAULT_MAX_LINES) -> None:
    MLX_FILTER_DEST.parent.mkdir(parents=True, exist_ok=True)
    MLX_FILTER_DEST.write_text(generate_mlx_filter_script(threshold, mlx_url, max_lines), encoding="utf-8")
    os.chmod(MLX_FILTER_DEST, 0o755)


def _write_script(
    max_lines: int,
    tail_lines: int,
    mlx_enabled: bool = DEFAULT_MLX_ENABLED,
    mlx_threshold: int = DEFAULT_MLX_THRESHOLD,
    mlx_url: str = DEFAULT_MLX_URL,
) -> None:
    SCRIPT_DEST.parent.mkdir(parents=True, exist_ok=True)
    SCRIPT_DEST.write_text(
        generate_script(max_lines, tail_lines, mlx_enabled, mlx_url),
        encoding="utf-8",
    )
    os.chmod(SCRIPT_DEST, 0o755)
    if mlx_enabled:
        _write_mlx_filter_script(mlx_threshold, mlx_url, max_lines)
    elif MLX_FILTER_DEST.exists():
        MLX_FILTER_DEST.unlink(missing_ok=True)


def install(
    target: str,
    project_path: Optional[str],
    backup: bool = True,
    max_lines: int = DEFAULT_MAX_LINES,
    tail_lines: int = DEFAULT_TAIL_LINES,
    mlx_enabled: bool = DEFAULT_MLX_ENABLED,
    mlx_threshold: int = DEFAULT_MLX_THRESHOLD,
    mlx_url: str = DEFAULT_MLX_URL,
) -> dict:
    target_path = resolve_settings_target(target, project_path)

    _write_script(max_lines, tail_lines, mlx_enabled, mlx_threshold, mlx_url)
    save_config(max_lines, tail_lines, mlx_enabled, mlx_threshold, mlx_url)

    has_prior = _has_tracker(str(target_path.parent))
    backup_path: Optional[str] = None
    if backup and target_path.exists() and not has_prior:
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        bak = Path(str(target_path) + f".bak.{ts}")
        shutil.copy2(target_path, bak)
        backup_path = str(bak)

    settings = _load_settings(target_path)
    hooks_block = settings.get("hooks")
    if not isinstance(hooks_block, dict):
        hooks_block = {}

    dst_list = hooks_block.get(HOOK_EVENT)
    if not isinstance(dst_list, list):
        dst_list = []

    entry = _hook_entry()
    existing_idx = None
    for i, e in enumerate(dst_list):
        if isinstance(e, dict) and _is_our_entry(e):
            existing_idx = i
            break

    if existing_idx is not None:
        dst_list[existing_idx] = entry
    else:
        dst_list.append(entry)

    hooks_block[HOOK_EVENT] = dst_list
    settings["hooks"] = hooks_block
    _write_settings(target_path, settings)

    _upsert_tracker(target, str(target_path.parent), str(target_path), backup_path, entry)

    cfg = {
        "max_lines": max_lines,
        "tail_lines": tail_lines,
        "mlx_enabled": mlx_enabled,
        "mlx_threshold": mlx_threshold,
        "mlx_url": mlx_url,
    }
    return {
        "installed": True,
        "script_path": str(SCRIPT_DEST),
        "mlx_filter_script_path": str(MLX_FILTER_DEST) if mlx_enabled else None,
        "target_path": str(target_path),
        "backup_path": backup_path,
        "config": cfg,
    }


def uninstall(target: str, project_path: Optional[str]) -> dict:
    target_path = resolve_settings_target(target, project_path)

    removed = 0
    errors: list[dict] = []

    try:
        settings = _load_settings(target_path)
        hooks_block = settings.get("hooks", {})
        dst_list = hooks_block.get(HOOK_EVENT, [])
        kept = [e for e in dst_list if not (isinstance(e, dict) and _is_our_entry(e))]
        if len(kept) != len(dst_list):
            hooks_block[HOOK_EVENT] = kept
            settings["hooks"] = hooks_block
            _write_settings(target_path, settings)
            removed += 1
    except (ValueError, OSError) as e:
        errors.append({"target": str(target_path), "error": str(e)})

    for script in (SCRIPT_DEST, MLX_FILTER_DEST):
        if script.exists():
            try:
                script.unlink()
                removed += 1
            except OSError as e:
                errors.append({"target": str(script), "error": str(e)})

    _delete_tracker(str(target_path.parent))

    return {"removed": removed, "errors": errors}


def uninstall_from_tracker(rows: list[dict]) -> dict:
    """Called by uninstaller.uninstall() for token-filter category rows."""
    removed = 0
    errors: list[dict] = []

    targets_seen: set[str] = set()
    for r in rows:
        dest_str = r.get("dest", "")
        settings_path = dest_str.split("#", 1)[0] if "#" in dest_str else dest_str
        if settings_path in targets_seen:
            continue
        targets_seen.add(settings_path)

        p = Path(settings_path)
        try:
            settings = _load_settings(p)
            hooks_block = settings.get("hooks", {})
            dst_list = hooks_block.get(HOOK_EVENT, [])
            kept = [e for e in dst_list if not (isinstance(e, dict) and _is_our_entry(e))]
            if len(kept) != len(dst_list):
                hooks_block[HOOK_EVENT] = kept
                settings["hooks"] = hooks_block
                _write_settings(p, settings)
                removed += 1
        except (ValueError, OSError) as e:
            errors.append({"id": r.get("id"), "error": str(e)})

    for script in (SCRIPT_DEST, MLX_FILTER_DEST):
        if script.exists():
            try:
                script.unlink()
                removed += 1
            except OSError as e:
                errors.append({"target": str(script), "error": str(e)})

    return {"removed": removed, "errors": errors}


# ---------------------------------------------------------------------------
# Tracker helpers
# ---------------------------------------------------------------------------

def _has_tracker(target_dir: str) -> bool:
    from app.db import session_scope
    from app.models import EccInstall

    with session_scope() as db:
        return db.query(EccInstall).filter(
            EccInstall.source == TRACKER_SOURCE,
            EccInstall.category == TRACKER_CATEGORY,
            EccInstall.target_dir == target_dir,
        ).first() is not None


def _upsert_tracker(target: str, target_dir: str, target_path: str, backup_path: Optional[str], entry: dict) -> None:
    from app.db import session_scope
    from app.models import EccInstall

    item_id = f"{HOOK_EVENT}:{HOOK_ID}"
    dest = f"{target_path}#{HOOK_EVENT}:{HOOK_ID}"
    extra = extra_with_dict_hash(None, entry)

    with session_scope() as db:
        existing = db.query(EccInstall).filter(
            EccInstall.source == TRACKER_SOURCE,
            EccInstall.category == TRACKER_CATEGORY,
            EccInstall.item_id == item_id,
            EccInstall.target_dir == target_dir,
        ).first()
        if existing is not None:
            existing.dest = dest
            existing.target = target
            existing.extra = extra
        else:
            db.add(EccInstall(
                source=TRACKER_SOURCE,
                category=TRACKER_CATEGORY,
                item_id=item_id,
                target=target,
                target_dir=target_dir,
                dest=dest,
                backup_path=backup_path,
                extra=extra,
            ))


def _delete_tracker(target_dir: str) -> None:
    from app.db import session_scope
    from app.models import EccInstall

    with session_scope() as db:
        db.query(EccInstall).filter(
            EccInstall.source == TRACKER_SOURCE,
            EccInstall.category == TRACKER_CATEGORY,
            EccInstall.target_dir == target_dir,
        ).delete(synchronize_session=False)
