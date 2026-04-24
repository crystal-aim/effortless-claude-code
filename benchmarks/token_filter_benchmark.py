#!/usr/bin/env python3
"""Benchmark: Regex-only vs MLX Hybrid Token Filter.

Compares token savings, latency, and accuracy between the two filtering
approaches. Requires an MLX server running for the MLX tests.

Usage:
    python3 benchmarks/token_filter_benchmark.py
    python3 benchmarks/token_filter_benchmark.py --mlx-url http://localhost:8899
    python3 benchmarks/token_filter_benchmark.py --json
    python3 benchmarks/token_filter_benchmark.py --runs 5
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Regex classification (mirrors the logic in token_filter.py _SCRIPT_TEMPLATE)
# ---------------------------------------------------------------------------

def classify_regex(cmd: str, max_lines: int = 300, tail_lines: int = 150) -> tuple[str, Optional[str]]:
    """Return (decision, rewritten_cmd) using regex-only logic."""
    s = cmd.strip()

    if re.search(r"\|\s*(head|tail|wc|less|more)\b", s):
        return ("SKIP", None)
    if re.search(r"&&|\|\||;", s):
        return ("SKIP_COMPOUND", None)
    if "$(" in s or "`" in s:
        return ("SKIP_SUBST", None)

    if re.match(r"git\s+log\b", s) and not re.search(r"\s(-n\s|--max-count)", s):
        return ("HEAD", re.sub(r"^(git\s+log)", r"\1 -n 50", s))
    if re.match(r"git\s+diff\b", s):
        return ("HEAD", s + f" | head -{max_lines}")
    if re.match(r"git\s+status\b", s):
        return ("HEAD", s + " | head -100")
    if re.match(r"find\s", s):
        return ("HEAD", s + f" | head -{max_lines}")
    if re.match(r"(grep\s+.*(-r\b|-R\b|--recursive)|rg\s)", s):
        return ("HEAD", s + f" | head -{max_lines}")
    if re.match(r"(ls\s+.*-\w*R|tree\b)", s):
        return ("HEAD", s + f" | head -{max_lines}")
    if re.match(r"(cat|bat)\s+(?!-)", s):
        return ("HEAD", re.sub(r"^(cat|bat)", f"head -{max_lines}", s))
    if re.match(r"(pytest|python\s+-m\s+pytest|jest|npx\s+jest|cargo\s+test|go\s+test|bundle\s+exec\s+rspec)\b", s):
        return ("TAIL", s + f" 2>&1 | tail -{tail_lines}")
    if re.match(r"docker\s+(ps|images|logs)\b", s):
        return ("HEAD", s + f" | head -{max_lines}")
    if re.match(r"ps\s+", s):
        return ("HEAD", s + f" | head -{max_lines}")

    return ("MISS", None)


# ---------------------------------------------------------------------------
# MLX classification
# ---------------------------------------------------------------------------

def classify_mlx(
    cmd: str,
    max_lines: int,
    tail_lines: int,
    mlx_url: str,
    timeout: float = 3.0,
) -> tuple[str, Optional[str], float]:
    """Return (decision, rewritten_cmd, latency_ms) using MLX inference."""
    s = cmd.strip()
    prompt = (
        "Classify this CLI command output volume. "
        "Reply with EXACTLY one word: SKIP, HEAD, TAIL, or SUMMARIZE.\n"
        "SKIP = output is small or truncation would break it\n"
        "HEAD = long output, keep first lines (listings, search results)\n"
        "TAIL = long output, keep last lines (build/test summaries)\n"
        "SUMMARIZE = very verbose, needs intelligent summarization\n\n"
        f"Command: {s}\nDecision:"
    )
    req_body = json.dumps({
        "model": _MODEL_ID,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 5,
        "temperature": 0,
    }).encode()

    t0 = time.perf_counter()
    req = urllib.request.Request(
        mlx_url.rstrip("/") + "/v1/chat/completions",
        data=req_body,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    result = json.loads(resp.read())
    latency = (time.perf_counter() - t0) * 1000

    raw = result["choices"][0]["message"]["content"].strip()
    decision = raw.split()[0].upper() if raw else "SKIP"

    rewritten = None
    if decision.startswith("HEAD"):
        decision = "HEAD"
        rewritten = s + f" | head -{max_lines}"
    elif decision.startswith("TAIL"):
        decision = "TAIL"
        rewritten = s + f" 2>&1 | tail -{tail_lines}"
    elif decision.startswith("SUMMAR"):
        decision = "SUMMARIZE"
        rewritten = f"({s}) 2>&1 | python3 ~/.claude/croxy-mlx-filter.py"
    else:
        decision = "SKIP"

    return (decision, rewritten, latency)


# ---------------------------------------------------------------------------
# Output summarization
# ---------------------------------------------------------------------------

def summarize_head(content: str, max_lines: int = 300) -> tuple[str, float]:
    """Simple head truncation. Returns (result, latency_ms)."""
    t0 = time.perf_counter()
    lines = content.split("\n")
    if len(lines) <= max_lines:
        result = content
    else:
        result = "\n".join(lines[:max_lines]) + f"\n\n... ({len(lines) - max_lines} more lines truncated)"
    latency = (time.perf_counter() - t0) * 1000
    return (result, latency)


def summarize_mlx(
    content: str,
    mlx_url: str,
    threshold: int = 2000,
    timeout: float = 60.0,
) -> tuple[str, float]:
    """MLX summarization. Returns (result, latency_ms)."""
    if len(content) <= threshold:
        return (content, 0.0)

    max_input = 12000
    if len(content) > max_input:
        half = max_input // 2
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

    req_body = json.dumps({
        "model": _MODEL_ID,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 800,
        "temperature": 0.1,
    }).encode()

    t0 = time.perf_counter()
    req = urllib.request.Request(
        mlx_url.rstrip("/") + "/v1/chat/completions",
        data=req_body,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    result = json.loads(resp.read())
    latency = (time.perf_counter() - t0) * 1000

    summary = result["choices"][0]["message"]["content"]
    line_count = content.count("\n")
    char_count = len(content)
    return (f"[MLX filtered — original: {line_count} lines, {char_count} chars]\n\n{summary}", latency)


# ---------------------------------------------------------------------------
# Accuracy check
# ---------------------------------------------------------------------------

def check_accuracy(output: str, markers: list[str]) -> tuple[int, int]:
    """Return (found, total) — how many markers are preserved in output."""
    found = sum(1 for m in markers if m.lower() in output.lower())
    return (found, len(markers))


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

CLASSIFICATION_TESTS: list[dict] = [
    # Known commands — regex should handle
    {"cmd": "git log", "category": "known", "expected": "HEAD"},
    {"cmd": "git log --oneline --graph", "category": "known", "expected": "HEAD"},
    {"cmd": "git diff", "category": "known", "expected": "HEAD"},
    {"cmd": "git diff HEAD~3", "category": "known", "expected": "HEAD"},
    {"cmd": "git status", "category": "known", "expected": "HEAD"},
    {"cmd": "find . -name '*.py'", "category": "known", "expected": "HEAD"},
    {"cmd": "find /var/log -type f -mtime -7", "category": "known", "expected": "HEAD"},
    {"cmd": "grep -r TODO src/", "category": "known", "expected": "HEAD"},
    {"cmd": "rg 'import os' --type py", "category": "known", "expected": "HEAD"},
    {"cmd": "cat README.md", "category": "known", "expected": "HEAD"},
    {"cmd": "cat /etc/hosts", "category": "known", "expected": "HEAD"},
    {"cmd": "pytest tests/", "category": "known", "expected": "TAIL"},
    {"cmd": "python -m pytest -v", "category": "known", "expected": "TAIL"},
    {"cmd": "cargo test", "category": "known", "expected": "TAIL"},
    {"cmd": "docker ps -a", "category": "known", "expected": "HEAD"},
    {"cmd": "docker images", "category": "known", "expected": "HEAD"},
    {"cmd": "ls -laR /usr/local", "category": "known", "expected": "HEAD"},
    {"cmd": "tree src/", "category": "known", "expected": "HEAD"},
    {"cmd": "ps aux", "category": "known", "expected": "HEAD"},

    # Unknown commands — regex misses, MLX should classify
    {"cmd": "kubectl get pods -A", "category": "unknown", "expected": "HEAD"},
    {"cmd": "kubectl logs deployment/api --tail=1000", "category": "unknown", "expected": "TAIL"},
    {"cmd": "terraform plan", "category": "unknown", "expected": "TAIL"},
    {"cmd": "terraform state list", "category": "unknown", "expected": "HEAD"},
    {"cmd": "npm ls --all", "category": "unknown", "expected": "HEAD"},
    {"cmd": "cargo build --verbose 2>&1", "category": "unknown", "expected": "TAIL"},
    {"cmd": "make -j8", "category": "unknown", "expected": "TAIL"},
    {"cmd": "apt list --installed", "category": "unknown", "expected": "HEAD"},
    {"cmd": "brew list --versions", "category": "unknown", "expected": "HEAD"},
    {"cmd": "pip list", "category": "unknown", "expected": "HEAD"},
    {"cmd": "du -sh /usr/*", "category": "unknown", "expected": "HEAD"},
    {"cmd": "journalctl -u nginx --no-pager", "category": "unknown", "expected": "TAIL"},
    {"cmd": "lsof -i :8080", "category": "unknown", "expected": "HEAD"},
    {"cmd": "netstat -tlnp", "category": "unknown", "expected": "HEAD"},
    {"cmd": "systemctl list-units --type=service", "category": "unknown", "expected": "HEAD"},
    {"cmd": "dpkg -l", "category": "unknown", "expected": "HEAD"},
    {"cmd": "aws s3 ls s3://my-bucket --recursive", "category": "unknown", "expected": "HEAD"},
    {"cmd": "gcloud compute instances list", "category": "unknown", "expected": "HEAD"},

    # Compound commands — regex skips, MLX can classify
    {"cmd": "cd /tmp && ls -la", "category": "compound", "expected": "HEAD"},
    {"cmd": "make clean && make all", "category": "compound", "expected": "TAIL"},
    {"cmd": "git add . && git status", "category": "compound", "expected": "HEAD"},
    {"cmd": "npm install && npm run build", "category": "compound", "expected": "TAIL"},
]


def _generate_git_diff_output(n_files: int = 50) -> tuple[str, list[str]]:
    """Generate synthetic git diff output with embedded markers."""
    markers = ["ERROR_IN_AUTH", "config/database.yml", "migration_0042", "port 5432"]
    lines = ["diff --git a/src/auth.py b/src/auth.py", "--- a/src/auth.py", "+++ b/src/auth.py"]
    for i in range(n_files):
        lines.append(f"@@ -{i*10+1},5 +{i*10+1},8 @@ class Handler{i}:")
        lines.append(f"-    old_value = {i}")
        lines.append(f"+    new_value = {i * 2}")
        if i == 5:
            lines.append("+    # ERROR_IN_AUTH: fixed null check")
        if i == 20:
            lines.append("+    db_url = 'postgresql://localhost:5432/mydb'  # port 5432")
    lines.append("diff --git a/config/database.yml b/config/database.yml")
    lines.append("--- a/config/database.yml")
    lines.append("+++ b/config/database.yml")
    lines.append("+  migration: migration_0042")
    for i in range(200):
        lines.append(f" context_line_{i}: unchanged")
    return ("\n".join(lines), markers)


def _generate_find_output(n_files: int = 500) -> tuple[str, list[str]]:
    """Generate synthetic find output."""
    markers = ["secret.env", "node_modules/lodash", ".git/config", "requirements.txt"]
    lines = []
    for i in range(n_files):
        if i == 10:
            lines.append("./config/secret.env")
        elif i == 50:
            lines.append("./node_modules/lodash/index.js")
        elif i == 100:
            lines.append("./.git/config")
        elif i == 200:
            lines.append("./requirements.txt")
        else:
            lines.append(f"./src/module_{i}/file_{i}.py")
    return ("\n".join(lines), markers)


def _generate_build_log(n_lines: int = 800) -> tuple[str, list[str]]:
    """Generate synthetic verbose build log."""
    markers = ["error[E0308]", "warning: unused variable", "Build completed in 42.3s", "3 warnings"]
    lines = []
    for i in range(n_lines):
        if i == 100:
            lines.append("warning: unused variable `tmp` in src/main.rs:42")
        elif i == 250:
            lines.append("warning: unused variable `ctx` in src/handler.rs:88")
        elif i == 400:
            lines.append("error[E0308]: mismatched types in src/parser.rs:156")
        elif i == 401:
            lines.append("  expected `String`, found `&str`")
        elif i == 600:
            lines.append("warning: unused import in src/lib.rs:3")
        elif i == n_lines - 2:
            lines.append("Build completed in 42.3s — 3 warnings, 1 error")
        elif i == n_lines - 1:
            lines.append("error: could not compile `myproject` due to previous error")
        else:
            lines.append(f"   Compiling dep_{i} v0.{i}.0")
    return ("\n".join(lines), markers)


def _generate_json_api_output(n_items: int = 200) -> tuple[str, list[str]]:
    """Generate synthetic large JSON API response."""
    markers = ["user_12345", "admin@example.com", "status\": \"suspended", "error_count\": 7"]
    items = []
    for i in range(n_items):
        status = "active"
        email = f"user{i}@example.com"
        error_count = 0
        if i == 42:
            email = "admin@example.com"
            status = "suspended"
            error_count = 7
        items.append(
            f'  {{"id": "user_{10000+i}", "email": "{email}", '
            f'"status": "{status}", "error_count": {error_count}, '
            f'"created": "2024-01-{(i%28)+1:02d}", '
            f'"metadata": {{"login_count": {i*10}, "last_ip": "10.0.{i//256}.{i%256}"}}}}'
        )
    content = "[\n" + ",\n".join(items) + "\n]"
    return (content, markers)


SUMMARIZATION_TESTS = [
    {"name": "git diff (large)", "generator": _generate_git_diff_output, "args": {"n_files": 50}},
    {"name": "find results (500 files)", "generator": _generate_find_output, "args": {"n_files": 500}},
    {"name": "build log (800 lines)", "generator": _generate_build_log, "args": {"n_lines": 800}},
    {"name": "JSON API (200 items)", "generator": _generate_json_api_output, "args": {"n_items": 200}},
]


# ---------------------------------------------------------------------------
# Table formatting
# ---------------------------------------------------------------------------

def print_table(title: str, headers: list[str], rows: list[list[str]]) -> None:
    """Print an ASCII table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_line = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, col_widths)) + " |"

    print(f"\n{'=' * len(sep)}")
    print(f" {title}")
    print(f"{'=' * len(sep)}")
    print(sep)
    print(header_line)
    print(sep)
    for row in rows:
        cells = [str(c).ljust(w) for c, w in zip(row, col_widths)]
        print("| " + " | ".join(cells) + " |")
    print(sep)


# ---------------------------------------------------------------------------
# MLX server check
# ---------------------------------------------------------------------------

_MODEL_ID: str = ""


def check_mlx_server(mlx_url: str) -> tuple[bool, str]:
    """Check if MLX server is running. Returns (ok, model_id)."""
    global _MODEL_ID
    try:
        req = urllib.request.Request(mlx_url.rstrip("/") + "/v1/models")
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read())
        models = data.get("data", [])
        _MODEL_ID = models[0]["id"] if models else "unknown"
        return (True, _MODEL_ID)
    except Exception:
        return (False, "")


# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    cmd: str
    category: str
    expected: str
    regex_decision: str
    mlx_decision: str
    regex_latency_ms: float
    mlx_latency_ms: float
    mlx_correct: bool


@dataclass
class SummarizationResult:
    name: str
    original_chars: int
    original_lines: int
    head_chars: int
    head_latency_ms: float
    head_accuracy: str
    mlx_chars: int
    mlx_latency_ms: float
    mlx_accuracy: str


@dataclass
class BenchmarkResults:
    model: str
    runs: int
    classification: list[ClassificationResult] = field(default_factory=list)
    summarization: list[SummarizationResult] = field(default_factory=list)


def run_classification_benchmark(
    tests: list[dict],
    mlx_url: str,
    max_lines: int,
    tail_lines: int,
    runs: int,
) -> list[ClassificationResult]:
    results = []
    total = len(tests)

    for idx, test in enumerate(tests, 1):
        cmd = test["cmd"]
        print(f"\r  [{idx}/{total}] {cmd[:50]}...", end="", flush=True)

        t0 = time.perf_counter()
        regex_dec, _ = classify_regex(cmd, max_lines, tail_lines)
        regex_lat = (time.perf_counter() - t0) * 1000

        mlx_dec = "N/A"
        mlx_lat = 0.0
        mlx_lats: list[float] = []

        for _ in range(runs):
            try:
                dec, _, lat = classify_mlx(cmd, max_lines, tail_lines, mlx_url)
                mlx_dec = dec
                mlx_lats.append(lat)
            except Exception as e:
                mlx_dec = f"ERR"
                mlx_lats.append(0.0)
                break

        mlx_lat = sum(mlx_lats) / len(mlx_lats) if mlx_lats else 0.0

        expected = test["expected"]
        mlx_correct = mlx_dec == expected or (
            regex_dec not in ("MISS", "SKIP_COMPOUND", "SKIP_SUBST") and regex_dec == expected
        )

        results.append(ClassificationResult(
            cmd=cmd,
            category=test["category"],
            expected=expected,
            regex_decision=regex_dec,
            mlx_decision=mlx_dec,
            regex_latency_ms=round(regex_lat, 3),
            mlx_latency_ms=round(mlx_lat, 1),
            mlx_correct=mlx_correct,
        ))

    print("\r" + " " * 70 + "\r", end="")
    return results


def run_summarization_benchmark(
    tests: list[dict],
    mlx_url: str,
    max_lines: int,
    threshold: int,
    runs: int,
) -> list[SummarizationResult]:
    results = []
    total = len(tests)

    for idx, test in enumerate(tests, 1):
        name = test["name"]
        print(f"\r  [{idx}/{total}] {name}...", end="", flush=True)

        content, markers = test["generator"](**test["args"])

        head_result, head_lat = summarize_head(content, max_lines)
        head_found, head_total = check_accuracy(head_result, markers)

        mlx_lats: list[float] = []
        mlx_result = ""
        for _ in range(runs):
            try:
                res, lat = summarize_mlx(content, mlx_url, threshold)
                mlx_result = res
                mlx_lats.append(lat)
            except Exception:
                mlx_result = "[MLX error — fallback]"
                mlx_lats.append(0.0)
                break

        mlx_lat = sum(mlx_lats) / len(mlx_lats) if mlx_lats else 0.0
        mlx_found, mlx_total = check_accuracy(mlx_result, markers)

        results.append(SummarizationResult(
            name=name,
            original_chars=len(content),
            original_lines=content.count("\n") + 1,
            head_chars=len(head_result),
            head_latency_ms=round(head_lat, 3),
            head_accuracy=f"{head_found}/{head_total}",
            mlx_chars=len(mlx_result),
            mlx_latency_ms=round(mlx_lat, 1),
            mlx_accuracy=f"{mlx_found}/{mlx_total}",
        ))

    print("\r" + " " * 70 + "\r", end="")
    return results


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def display_classification(results: list[ClassificationResult]) -> None:
    headers = ["Command", "Category", "Expected", "Regex", "MLX", "Regex ms", "MLX ms", "OK"]
    rows = []
    for r in results:
        ok = "Y" if r.mlx_correct else "N"
        rows.append([
            r.cmd[:40],
            r.category,
            r.expected,
            r.regex_decision,
            r.mlx_decision,
            f"{r.regex_latency_ms:.3f}",
            f"{r.mlx_latency_ms:.0f}",
            ok,
        ])
    print_table("Command Classification: Regex vs MLX", headers, rows)

    # Summary stats
    known = [r for r in results if r.category == "known"]
    unknown = [r for r in results if r.category == "unknown"]
    compound = [r for r in results if r.category == "compound"]

    regex_handled = sum(1 for r in results if r.regex_decision not in ("MISS", "SKIP_COMPOUND", "SKIP_SUBST"))
    mlx_handled = sum(1 for r in results if r.mlx_decision not in ("SKIP", "ERR", "N/A"))
    mlx_correct = sum(1 for r in results if r.mlx_correct)

    regex_avg_lat = sum(r.regex_latency_ms for r in results) / len(results) if results else 0
    mlx_avg_lat = sum(r.mlx_latency_ms for r in results if r.mlx_latency_ms > 0) / max(1, sum(1 for r in results if r.mlx_latency_ms > 0))

    unknown_mlx_handled = sum(1 for r in unknown if r.mlx_decision not in ("SKIP", "ERR", "N/A"))
    compound_mlx_handled = sum(1 for r in compound if r.mlx_decision not in ("SKIP", "ERR", "N/A"))

    print(f"\n  Summary:")
    print(f"    Total commands:         {len(results)}")
    print(f"    Regex handled:          {regex_handled}/{len(results)} ({regex_handled/len(results)*100:.0f}%)")
    print(f"    MLX handled (extra):    {unknown_mlx_handled + compound_mlx_handled} commands regex missed")
    print(f"      - Unknown commands:   {unknown_mlx_handled}/{len(unknown)}")
    print(f"      - Compound commands:  {compound_mlx_handled}/{len(compound)}")
    print(f"    MLX accuracy:           {mlx_correct}/{len(results)} ({mlx_correct/len(results)*100:.0f}%)")
    print(f"    Avg latency (regex):    {regex_avg_lat:.3f} ms")
    print(f"    Avg latency (MLX):      {mlx_avg_lat:.0f} ms")
    print(f"    Latency overhead:       {mlx_avg_lat - regex_avg_lat:.0f} ms per unmatched command")


def display_summarization(results: list[SummarizationResult]) -> None:
    headers = [
        "Output Type", "Original", "Head Trunc", "Head Acc",
        "MLX Summary", "MLX Acc", "MLX ms", "Savings",
    ]
    rows = []
    for r in results:
        head_pct = (1 - r.head_chars / r.original_chars) * 100 if r.original_chars else 0
        mlx_pct = (1 - r.mlx_chars / r.original_chars) * 100 if r.original_chars else 0
        better = "MLX" if mlx_pct > head_pct else "HEAD"
        rows.append([
            r.name,
            f"{r.original_chars:,} chars",
            f"{r.head_chars:,} chars ({head_pct:.0f}%)",
            r.head_accuracy,
            f"{r.mlx_chars:,} chars ({mlx_pct:.0f}%)",
            r.mlx_accuracy,
            f"{r.mlx_latency_ms:.0f}",
            better,
        ])
    print_table("Output Summarization: Head Truncation vs MLX", headers, rows)

    total_orig = sum(r.original_chars for r in results)
    total_head = sum(r.head_chars for r in results)
    total_mlx = sum(r.mlx_chars for r in results)
    avg_mlx_lat = sum(r.mlx_latency_ms for r in results) / len(results) if results else 0

    head_markers = sum(int(r.head_accuracy.split("/")[0]) for r in results)
    mlx_markers = sum(int(r.mlx_accuracy.split("/")[0]) for r in results)
    total_markers = sum(int(r.head_accuracy.split("/")[1]) for r in results)

    print(f"\n  Summary:")
    print(f"    Total original:         {total_orig:,} chars")
    print(f"    Head truncation:        {total_head:,} chars ({(1-total_head/total_orig)*100:.0f}% saved)")
    print(f"    MLX summarization:      {total_mlx:,} chars ({(1-total_mlx/total_orig)*100:.0f}% saved)")
    print(f"    Head accuracy:          {head_markers}/{total_markers} markers preserved")
    print(f"    MLX accuracy:           {mlx_markers}/{total_markers} markers preserved")
    print(f"    Avg MLX latency:        {avg_mlx_lat:.0f} ms")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark: Regex vs MLX Token Filter")
    parser.add_argument("--mlx-url", default="http://localhost:8899", help="MLX server URL")
    parser.add_argument("--max-lines", type=int, default=300, help="Max lines for head truncation")
    parser.add_argument("--tail-lines", type=int, default=150, help="Lines for tail truncation")
    parser.add_argument("--threshold", type=int, default=2000, help="Char threshold for MLX summarization")
    parser.add_argument("--runs", type=int, default=3, help="Repeat each test N times for stable latency")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--skip-summarization", action="store_true", help="Skip summarization benchmark")
    args = parser.parse_args()

    print("Token Filter Benchmark: Regex-only vs MLX Hybrid")
    print("-" * 50)

    ok, model = check_mlx_server(args.mlx_url)
    if not ok:
        print(f"\nMLX server not reachable at {args.mlx_url}")
        print("Start the server first:")
        print("  1. Open admin dashboard -> MLX tab")
        print("  2. Select gemma-4-e2b-it and click Start")
        print(f"  Or: python -m mlx_vlm server --model mlx-community/gemma-4-e2b-it-4bit --port 8899")
        sys.exit(1)

    print(f"MLX server: {args.mlx_url} (model: {model})")
    print(f"Settings: max_lines={args.max_lines}, tail_lines={args.tail_lines}, threshold={args.threshold}")
    print(f"Runs per test: {args.runs}")

    # --- Classification ---
    print(f"\nRunning classification benchmark ({len(CLASSIFICATION_TESTS)} commands)...")
    cls_results = run_classification_benchmark(
        CLASSIFICATION_TESTS, args.mlx_url, args.max_lines, args.tail_lines, args.runs,
    )

    # --- Summarization ---
    sum_results: list[SummarizationResult] = []
    if not args.skip_summarization:
        print(f"Running summarization benchmark ({len(SUMMARIZATION_TESTS)} outputs)...")
        sum_results = run_summarization_benchmark(
            SUMMARIZATION_TESTS, args.mlx_url, args.max_lines, args.threshold, args.runs,
        )

    # --- Output ---
    if args.json:
        bench = BenchmarkResults(
            model=model,
            runs=args.runs,
            classification=cls_results,
            summarization=sum_results,
        )
        print(json.dumps(asdict(bench), indent=2))
    else:
        display_classification(cls_results)
        if sum_results:
            display_summarization(sum_results)

        # Final verdict
        regex_coverage = sum(1 for r in cls_results if r.regex_decision not in ("MISS", "SKIP_COMPOUND", "SKIP_SUBST"))
        hybrid_coverage = regex_coverage + sum(
            1 for r in cls_results
            if r.regex_decision in ("MISS", "SKIP_COMPOUND", "SKIP_SUBST")
            and r.mlx_decision not in ("SKIP", "ERR", "N/A")
        )
        print(f"\n{'=' * 60}")
        print(f" VERDICT")
        print(f"{'=' * 60}")
        print(f"  Coverage:  regex {regex_coverage}/{len(cls_results)} -> hybrid {hybrid_coverage}/{len(cls_results)}")
        if sum_results:
            total_orig = sum(r.original_chars for r in sum_results)
            total_head = sum(r.head_chars for r in sum_results)
            total_mlx = sum(r.mlx_chars for r in sum_results)
            print(f"  Token savings (head):  {(1-total_head/total_orig)*100:.0f}%")
            print(f"  Token savings (MLX):   {(1-total_mlx/total_orig)*100:.0f}%")
            head_m = sum(int(r.head_accuracy.split("/")[0]) for r in sum_results)
            mlx_m = sum(int(r.mlx_accuracy.split("/")[0]) for r in sum_results)
            total_m = sum(int(r.head_accuracy.split("/")[1]) for r in sum_results)
            print(f"  Info preserved (head): {head_m}/{total_m}")
            print(f"  Info preserved (MLX):  {mlx_m}/{total_m}")
        print()


if __name__ == "__main__":
    main()
