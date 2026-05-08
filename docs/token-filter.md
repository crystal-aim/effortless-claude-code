# Token Filter

A 3-layer hybrid PreToolUse hook for Claude Code that rewrites verbose CLI commands to truncate output **before** it enters the conversation context — preserving key information (errors, paths, summary numbers) while cutting 60–98% of tokens. Does not break Anthropic prompt caching.

Architecture: regex fast path (<1ms) → MLX classification (~100–300ms) → MLX summarization (~1–2s).

## What gets filtered

| Command | Strategy | Savings |
|---------|----------|---------|
| `git log` (no `-n`) | Adds `-n 50` | ~90% on large repos |
| `git diff` | Appends `\| head -N` | ~80% on big diffs |
| `find` | Appends `\| head -N` | ~95% on deep trees |
| `grep -r` / `rg` | Appends `\| head -N` | ~85% |
| `cat` / `bat` | Replaced with `head -N` | ~90% on large files |
| `pytest` / `jest` / `cargo test` | Appends `\| tail -N` (keeps summary) | ~70% |
| `docker ps/images/logs` | Appends `\| head -N` | ~80% |
| `ls -R` / `tree` | Appends `\| head -N` | ~90% |
| Unmatched commands (MLX) | MLX classifies → HEAD/TAIL/SUMMARIZE | Variable |

Commands already containing `| head` / `| tail` are left untouched. Without MLX mode, compound commands (`&&`, `||`, `;`) and `$()` substitutions are skipped. With MLX mode, compound commands can be classified for HEAD/TAIL (but not SUMMARIZE).

## MLX hybrid mode

Enable in the dashboard to send unmatched commands to a local MLX model. The model returns one of:

| Decision | Action |
|----------|--------|
| **SKIP** | No modification |
| **HEAD** | Append `\| head -N` |
| **TAIL** | Append `\| tail -N` |
| **SUMMARIZE** | Pipe through `~/.claude/croxy-mlx-filter.py` for intelligent compression |

If the MLX server is unreachable, the filter falls back to head truncation. Recommended model: `gemma-4-e2b-it` (fast).

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Max lines (head) | 300 | Truncation limit for most commands |
| Tail lines | 150 | Lines kept for test runner output |
| MLX enabled | false | Enable MLX-based filtering for unmatched commands |
| MLX threshold | 2000 | Char threshold for triggering MLX summarization |
| MLX URL | `http://localhost:8899` | Local MLX server URL |

All values configurable from the **Token Filter** tab and persisted in the database.

## Install / uninstall (UI)

Admin dashboard → **Claude Setup** → **Token Filter** tab:

1. Set max/tail lines if needed
2. Click **Install Token Filter**
3. Confirm — writes `~/.claude/croxy-token-filter.sh` and adds the hook to `settings.json`

Uninstall removes both the script and the hook entry.

## Manual install

```bash
python3 -c "
from app.ecc.token_filter import generate_script
from pathlib import Path
p = Path.home() / '.claude' / 'croxy-token-filter.sh'
p.write_text(generate_script(max_lines=300, tail_lines=150))
import os; os.chmod(p, 0o755)
print(f'Written to {p}')
"
```

Then add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "~/.claude/croxy-token-filter.sh" }],
        "description": "Croxy token filter — truncates verbose CLI output to save tokens",
        "id": "pre:bash:croxy-token-filter"
      }
    ]
  }
}
```

## Benchmark

```bash
python3 benchmarks/token_filter_benchmark.py
python3 benchmarks/token_filter_benchmark.py --json
python3 benchmarks/token_filter_benchmark.py --runs 5
python3 benchmarks/token_filter_benchmark.py --skip-summarization
```

Requires an MLX server running with a loaded model.

**Command Classification** (41 test commands):

| Metric | Regex-only | Hybrid (regex + MLX) |
|--------|-----------|----------------------|
| Coverage | 19/41 (46%) | 40/41 (98%) |
| Accuracy | — | 34/41 (83%) |
| Avg latency | 0.03 ms | 371 ms |

MLX correctly classifies 21 extra commands that regex misses — `kubectl`, `terraform`, `brew`, `pip`, `du`, compound `&&` chains, etc.

**Output Summarization** (4 synthetic outputs):

| Metric | Head truncation | MLX summarization |
|--------|----------------|-------------------|
| Token savings | 27% | 98% |
| Key info preserved | 12/16 markers | 6/16 markers |
| Avg latency | <1 ms | ~22 s (7B model) |

Head truncation is lossless when key info is near the top. MLX summarization achieves much higher compression but may lose details scattered through large outputs. *Benchmarked with Qwen2.5-7B-Instruct-4bit. Smaller models like `gemma-4-e2b-it` are faster.*

## Inspiration

The regex layer was inspired by [RTK (Rust Token Killer)](https://github.com/rtk-ai/rtk). The Croxy implementation extends it with local MLX inference for intelligent classification and summarization — deployed as a Claude Code hook with no extra binary needed.
