"""RTK-style token filter hook for Claude Code.

Generates and manages a PreToolUse hook that rewrites verbose CLI commands
to include output truncation, reducing token consumption by 60-90%.
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

SETTING_MAX_LINES = "token_filter.max_lines"
SETTING_TAIL_LINES = "token_filter.tail_lines"


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

# Skip: compound commands
if re.search(r"&&|\|\||;", s):
    sys.exit(0)

# Skip: command substitution
if "$(" in s or "`" in s:
    sys.exit(0)

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

if new and new != s:
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": new}}}
    print(json.dumps(out))
'
'''


def generate_script(max_lines: int = DEFAULT_MAX_LINES, tail_lines: int = DEFAULT_TAIL_LINES) -> str:
    return _SCRIPT_TEMPLATE.replace("%%MAX_LINES%%", str(max_lines)).replace("%%TAIL_LINES%%", str(tail_lines))


def _hook_entry() -> dict:
    return {
        "matcher": HOOK_MATCHER,
        "hooks": [{"type": "command", "command": str(SCRIPT_DEST)}],
        "description": "Croxy token filter — truncates verbose CLI output to save tokens",
        "id": HOOK_ID,
    }


# ---------------------------------------------------------------------------
# Config (persisted in Setting table)
# ---------------------------------------------------------------------------

def get_config() -> dict:
    from app.db import session_scope
    from app.models import Setting

    with session_scope() as db:
        ml = db.get(Setting, SETTING_MAX_LINES)
        tl = db.get(Setting, SETTING_TAIL_LINES)
        ml_val = ml.value if ml else None
        tl_val = tl.value if tl else None

    return {
        "max_lines": int(ml_val) if ml_val else DEFAULT_MAX_LINES,
        "tail_lines": int(tl_val) if tl_val else DEFAULT_TAIL_LINES,
    }


def save_config(max_lines: int, tail_lines: int) -> dict:
    from app.db import session_scope
    from app.models import Setting

    with session_scope() as db:
        for key, val in [(SETTING_MAX_LINES, str(max_lines)), (SETTING_TAIL_LINES, str(tail_lines))]:
            row = db.get(Setting, key)
            if row is None:
                db.add(Setting(key=key, value=val))
            else:
                row.value = val

    if SCRIPT_DEST.exists():
        _write_script(max_lines, tail_lines)

    return {"max_lines": max_lines, "tail_lines": tail_lines}


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
            installed = any(_entry_id(e) == HOOK_ID for e in hooks if isinstance(e, dict))
        except (ValueError, OSError):
            pass

    cfg = get_config()
    return {
        "installed": installed,
        "script_exists": SCRIPT_DEST.exists(),
        "script_path": str(SCRIPT_DEST),
        "target_path": str(target_path) if target_path else None,
        "config": cfg,
    }


# ---------------------------------------------------------------------------
# Install / Uninstall
# ---------------------------------------------------------------------------

def _write_script(max_lines: int, tail_lines: int) -> None:
    SCRIPT_DEST.parent.mkdir(parents=True, exist_ok=True)
    SCRIPT_DEST.write_text(generate_script(max_lines, tail_lines), encoding="utf-8")
    os.chmod(SCRIPT_DEST, 0o755)


def install(
    target: str,
    project_path: Optional[str],
    backup: bool = True,
    max_lines: int = DEFAULT_MAX_LINES,
    tail_lines: int = DEFAULT_TAIL_LINES,
) -> dict:
    target_path = resolve_settings_target(target, project_path)

    _write_script(max_lines, tail_lines)
    save_config(max_lines, tail_lines)

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
        if isinstance(e, dict) and _entry_id(e) == HOOK_ID:
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

    return {
        "installed": True,
        "script_path": str(SCRIPT_DEST),
        "target_path": str(target_path),
        "backup_path": backup_path,
        "config": {"max_lines": max_lines, "tail_lines": tail_lines},
    }


def uninstall(target: str, project_path: Optional[str]) -> dict:
    target_path = resolve_settings_target(target, project_path)

    removed = 0
    errors: list[dict] = []

    try:
        settings = _load_settings(target_path)
        hooks_block = settings.get("hooks", {})
        dst_list = hooks_block.get(HOOK_EVENT, [])
        kept = [e for e in dst_list if not (isinstance(e, dict) and _entry_id(e) == HOOK_ID)]
        if len(kept) != len(dst_list):
            hooks_block[HOOK_EVENT] = kept
            settings["hooks"] = hooks_block
            _write_settings(target_path, settings)
            removed += 1
    except (ValueError, OSError) as e:
        errors.append({"target": str(target_path), "error": str(e)})

    if SCRIPT_DEST.exists():
        try:
            SCRIPT_DEST.unlink()
            removed += 1
        except OSError as e:
            errors.append({"target": str(SCRIPT_DEST), "error": str(e)})

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
            kept = [e for e in dst_list if not (isinstance(e, dict) and _entry_id(e) == HOOK_ID)]
            if len(kept) != len(dst_list):
                hooks_block[HOOK_EVENT] = kept
                settings["hooks"] = hooks_block
                _write_settings(p, settings)
                removed += 1
        except (ValueError, OSError) as e:
            errors.append({"id": r.get("id"), "error": str(e)})

    if SCRIPT_DEST.exists():
        try:
            SCRIPT_DEST.unlink()
            removed += 1
        except OSError as e:
            errors.append({"target": str(SCRIPT_DEST), "error": str(e)})

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
