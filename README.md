# Effortless Claude Code - Claude Croxy

A lightweight API proxy for Claude that adds virtual key management, usage tracking, budget controls, and multi-backend routing. Run it in front of the Anthropic API, AWS Bedrock, or local MLX models — manage everything from a single admin dashboard.

## Features

- **API Proxy** — Drop-in replacement for the Anthropic `/v1/messages` endpoint (streaming + non-streaming)
- **Virtual Keys** — Issue `sk-ccm-*` keys with per-key budgets, expiration dates, and rate limits
- **Multi-Backend** — Route requests to Anthropic API, AWS Bedrock, or local MLX models
- **Auto Routing** — Keyword-based model routing from system prompts (e.g. "quick" → Haiku, "deep reasoning" → Opus)
- **Usage Tracking** — Token counts, cost breakdowns, latency metrics per key/model/day
- **Budget Controls** — Per-key spend limits with daily/weekly/monthly reset periods
- **Local Inference** — Run Gemma 4, Qwen 2.5, Llama 3.1, Mistral Nemo on Apple Silicon via MLX
- **Admin Dashboard** — Web UI for key management, provider config, usage charts, and MLX server control
- **Auto-Start** — macOS LaunchAgent for auto-start on login, MLX server auto-resume with last used model, and watchdog thread that auto-restarts crashed MLX processes
- **Token Filter** — 3-layer hybrid PreToolUse hook: (1) regex rewrites verbose CLI commands to include output truncation, (2) local MLX model classifies whether output needs filtering, (3) local MLX model summarizes large outputs — preserving key information (error messages, file paths, anomalies, summary numbers) verbatim while cutting 60–98% of tokens, all without breaking Anthropic prompt caching
- **Claude Setup** — One-click installer for [everything-claude-code](https://github.com/affaan-m/everything-claude-code) (48 agents, 183 skills, 79 commands, 88 rules, 14 MCP servers, hooks) plus a browser for [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)

## Quick Start

### Prerequisites

- Python 3.11+
- (Optional) Apple Silicon Mac for MLX local inference

### Install

```bash
git clone https://github.com/claude-croxy/claude-croxy.git
cd claude-croxy
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

Copy the example config and edit it:

```bash
cp config.example.yaml config.yaml
```

At minimum, change the `session_secret` in `config.yaml`:

```yaml
server:
  session_secret: "replace-with-a-random-string"  # CHANGE THIS

backend:
  provider: "claude"  # claude | bedrock | mlx | auto
```

Set your Anthropic API key:

```bash
mkdir -p secrets
echo "sk-ant-YOUR-KEY" > secrets/anthropic_api_key
```

### Run

```bash
CCM_ADMIN_EMAIL=admin@example.com \
CCM_ADMIN_PASSWORD=your-secure-password \
python -m app.main
```

The server starts on `http://localhost:4000`. Open the admin dashboard at `/ui/admin`.

### Use

Generate a virtual key from the admin dashboard, then point any Anthropic-compatible client at your proxy:

```bash
curl http://localhost:4000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: sk-ccm-your-virtual-key" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CCM_ADMIN_EMAIL` | — | Initial admin account email (required on first run) |
| `CCM_ADMIN_PASSWORD` | — | Initial admin account password (required on first run) |
| `CCM_CONFIG` | `config.yaml` | Path to config file |
| `CCM_DATABASE_URL` | `sqlite:///./data.db` | Database connection string |
| `CCM_LOG_LEVEL` | `INFO` | Logging level |

### Backend Providers

**Claude** (default) — Forward to the Anthropic API. Place your API key in `secrets/anthropic_api_key`.

**AWS Bedrock** — Uses SSO device authorization via an AWS CLI profile.

#### 1. Set up an AWS profile

Install the AWS CLI and configure an SSO profile:

```bash
aws configure sso --profile bedrock-claude
```

You will be prompted for:
- **SSO start URL** — your org's AWS access portal (e.g. `https://your-org.awsapps.com/start`)
- **SSO region** — region of the SSO portal (e.g. `us-east-1`)
- **Account** and **Role** — pick the account/role that has Bedrock access

#### 2. Enable model access

The AWS role associated with your profile must have Bedrock model access enabled. In the AWS Console:

1. Go to **Amazon Bedrock → Model access** (in the region you will use, e.g. `us-east-1`)
2. Click **Manage model access**
3. Enable the Anthropic Claude models you need (Sonnet, Opus, Haiku)
4. Wait for status to show **Access granted**

The IAM role also needs the `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` permissions. A minimal policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream"
    ],
    "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.*"
  }]
}
```

#### 3. Configure Croxy

```yaml
backend:
  provider: "bedrock"
  bedrock:
    region: "us-east-1"
    aws_profile: "bedrock-claude"
    sso_start_url: "https://your-org.awsapps.com/start"
```

Then complete SSO login from the admin dashboard under the Provider tab.

**MLX** (local inference) — Run open-source models on Apple Silicon:

```yaml
backend:
  provider: "mlx"
  mlx:
    port: 8899
    model_map:
      gemma-4-e2b-it: "mlx-community/gemma-4-e2b-it-4bit"
      qwen-2.5-32b-it: "mlx-community/Qwen2.5-32B-Instruct-4bit"
```

Download and start models from the admin dashboard. MLX models support tool calling via Anthropic-to-OpenAI protocol conversion.

**Auto** — Uses Claude as primary, falls back to Bedrock on 529 (overloaded) responses.

### Routing Rules

Route requests to different models based on keywords in the system prompt:

```yaml
routing_rules:
  - keywords: ["opus", "deep reasoning", "architect"]
    model: "claude-opus-4-7"
  - keywords: ["haiku", "quick", "cheap"]
    model: "claude-haiku-4-5-20251001"
```

### Pricing

Configure per-model pricing (USD per million tokens) for cost tracking:

```yaml
pricing:
  claude-sonnet-4-6:
    input: 3.0
    output: 15.0
    cache_write: 3.75
    cache_read: 0.30
```

Local MLX models default to `0.0` for all token types.

## Claude Setup

The admin dashboard includes a **Claude Setup** panel (sidebar → *Claude Setup*) that installs Claude Code enhancements directly into `~/.claude/` or a project's `.claude/`. Everything happens locally — no external plugin system is used.

### Sources

- **Everything-CC** (`everything-claude-code`) — auto-install agents, skills, commands, rules, MCP servers, and hooks
- **Awesome-CC** (`awesome-claude-code`) — searchable catalog of external Claude Code tools (view only, click out to the project)

### Install targets

- **User level** — `~/.claude/` (applies to every project)
- **Project level** — `<your-project>/.claude/` (per-project, absolute path)

### Everything-CC flow

1. **Sync** — clones `affaan-m/everything-claude-code` to `~/.cache/claude-croxy/ecc-repo/`. Nothing is touched in `~/.claude/` yet.
2. **Choose items** — pick a curated preset (Starter / Web Dev / Security / Full) or tick individual items in Browse.
3. **Install** — a dry-run modal shows creates/overwrites; optionally backs up existing files to `*.bak.<timestamp>` before writing.

MCP servers merge into `~/.claude.json` (user) or `<project>/.mcp.json` (project); existing entries are preserved. Hooks add `~/.claude/plugins/everything-claude-code/` as a symlink to the cache and merge hook entries into `settings.json` (deduped by hook `id`).

### Installed + uninstall

The **Installed** sub-tab lists every tracked install with:
- `modified` badge — on-disk content differs from what we installed (user edited the file)
- `upstream changed` badge — repo has a newer version since install
- `backup` badge — original file was preserved

Uninstall restores backups when available and reverses JSON-merge installs (MCP / hooks) back to their pre-ECC state.

### Export / Import profile

Installed tab has **Export profile** (download a JSON snapshot) and **Import profile…** (apply a snapshot from another machine). Portable bundle includes: file items, MCP server IDs, hooks flag, and target preferences.

### Auto-sync

Toggle in the Sync status card — runs git pull of ECC + refresh of ACC on an interval (default 24h). State persists in the `settings` table.

## Token Filter

The admin dashboard includes a **Token Filter** tab (inside *Claude Setup*) that deploys a hybrid PreToolUse hook into Claude Code. The hook intercepts verbose CLI commands and rewrites them to include output truncation **before** they execute — so the filtered output is what enters the conversation context, not a post-hoc trim. It combines fast regex patterns for known commands with optional local MLX inference for everything else.

### What gets filtered

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

Commands that already have truncation (`| head`, `| tail`) are left untouched. Without MLX mode, compound commands (`&&`, `||`, `;`) and command substitutions (`$()`) are also skipped. With MLX mode enabled, compound commands can be classified for HEAD/TAIL truncation (but not SUMMARIZE).

### MLX Mode (hybrid filtering)

When enabled, commands not matched by the built-in regex patterns are sent to a local MLX model for classification. The model decides the best truncation strategy:

| Decision | Action |
|----------|--------|
| **SKIP** | No modification (output is small or truncation would break the command) |
| **HEAD** | Append `\| head -N` (directory listings, search results) |
| **TAIL** | Append `\| tail -N` (build/test output with summary at end) |
| **SUMMARIZE** | Pipe through MLX summarizer script for intelligent compression |

The SUMMARIZE path rewrites the command to `(cmd) 2>&1 | python3 ~/.claude/croxy-mlx-filter.py`, so the summarized output is what enters the conversation context. If the MLX server is unreachable, the filter falls back to simple head truncation.

**Architecture:** regex fast path (<1ms) → MLX classification fallback (~100-300ms) → MLX summarization (~1-2s for large output). The MLX server must be running with a loaded model — `gemma-4-e2b-it` is recommended for speed.

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| Max lines (head) | 300 | Truncation limit for most commands |
| Tail lines | 150 | Lines kept for test runner output |
| MLX enabled | false | Enable MLX-based filtering for unmatched commands |
| MLX threshold | 2000 | Character threshold for MLX summarization |
| MLX URL | `http://localhost:8899` | Local MLX server URL |

All values are configurable from the Token Filter tab and persisted in the database.

### Install / Uninstall

From the admin dashboard → *Claude Setup* → **Token Filter** tab:

1. Set max/tail lines if needed
2. Click **Install Token Filter**
3. Confirm — writes `~/.claude/croxy-token-filter.sh` and adds the hook to `settings.json`

Uninstall removes both the script and the hook entry. The hook is tracked in the Installed tab alongside ECC items and can be uninstalled from there too.

### Manual install

If you prefer not to use the dashboard:

```bash
# Generate and install the hook script
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

### Benchmark

A standalone benchmark compares regex-only vs MLX hybrid filtering. Run it with:

```bash
python3 benchmarks/token_filter_benchmark.py
python3 benchmarks/token_filter_benchmark.py --json          # structured output
python3 benchmarks/token_filter_benchmark.py --runs 5        # more stable latency
python3 benchmarks/token_filter_benchmark.py --skip-summarization  # classification only
```

Requires an MLX server running with a loaded model.

**Command Classification** (41 test commands — known, unknown, compound):

| Metric | Regex-only | Hybrid (regex + MLX) |
|--------|-----------|----------------------|
| Coverage | 19/41 (46%) | 40/41 (98%) |
| Accuracy | — | 34/41 (83%) |
| Avg latency | 0.03 ms | 371 ms |

MLX correctly classifies 21 extra commands that regex misses — `kubectl`, `terraform`, `brew`, `pip`, `du`, compound `&&` chains, etc.

**Output Summarization** (4 synthetic outputs — git diff, find, build log, JSON API):

| Metric | Head truncation | MLX summarization |
|--------|----------------|-------------------|
| Token savings | 27% | 98% |
| Key info preserved | 12/16 markers | 6/16 markers |
| Avg latency | <1 ms | ~22 s (7B model) |

Head truncation is lossless when key information is near the top of the output. MLX summarization achieves much higher compression but may lose details scattered throughout large outputs. The summarization path is best reserved for very verbose outputs where 98% token savings justifies the latency tradeoff.

*Benchmarked with Qwen2.5-7B-Instruct-4bit on Apple Silicon. Smaller models like gemma-4-e2b-it are faster.*

### Inspiration

The regex layer was originally inspired by [RTK (Rust Token Killer)](https://github.com/rtk-ai/rtk). The croxy implementation goes further by adding local LLM inference (MLX) for intelligent classification and summarization of commands that regex alone cannot handle — deployed as a Claude Code hook with no extra binary needed.

## Auto-Start (macOS)

Claude Croxy can auto-start when you log in to macOS using a LaunchAgent, and automatically resume the last MLX model with a watchdog that restarts it if the process crashes.

### Setup

First, run the app manually at least once to seed the admin account:

```bash
CCM_ADMIN_EMAIL=admin@example.com \
CCM_ADMIN_PASSWORD=your-secure-password \
python -m app.main
```

Then install the LaunchAgent:

```bash
bash scripts/launchd_setup.sh install
```

The app will now auto-start on login, auto-restart if killed, and resume the last MLX model you used.

### Management Commands

```bash
bash scripts/launchd_setup.sh status    # Check if service is running
bash scripts/launchd_setup.sh restart   # Restart the service
bash scripts/launchd_setup.sh logs      # Tail log files
bash scripts/launchd_setup.sh uninstall # Remove auto-start
```

Logs are written to `logs/ccm-stdout.log` and `logs/ccm-stderr.log` in the project directory.

### MLX Watchdog

When the app starts, it automatically:
1. Resumes the last MLX model you selected (stored in the database)
2. Starts a watchdog thread that checks the MLX process every 30 seconds
3. If the MLX process crashes, the watchdog restarts it (up to 5 consecutive retries)
4. If you stop the MLX server intentionally via the admin UI, the watchdog will not restart it

The retry counter resets after 5 minutes of stable running, so transient failures are handled gracefully.

## Architecture

```
Client (Claude Code, apps, etc.)
  │
  ▼
┌──────────────────────────────┐
│  Claude Croxy  (FastAPI)     │
│                              │
│  Auth → Rate Limit → Route   │
│         │                    │
│         ▼                    │
│  ┌─────────────────────┐     │
│  │  Backend Router      │     │
│  │  ├─ Anthropic API    │     │
│  │  ├─ AWS Bedrock      │     │
│  │  └─ MLX (local)      │     │
│  └─────────────────────┘     │
│         │                    │
│  Usage Tracking → SQLite     │
└──────────────────────────────┘
```

## Project Structure

```
app/
├── main.py              # FastAPI app & lifespan
├── proxy.py             # /v1/messages proxy router
├── auth.py              # Key & session authentication
├── routing.py           # Keyword-based model routing
├── config.py            # YAML + DB config management
├── models.py            # SQLAlchemy models
├── db.py                # Database setup
├── rate_limit.py        # Per-key rate limiting
├── backend/
│   ├── router.py        # Backend dispatch
│   ├── claude.py       # Anthropic API backend
│   ├── bedrock.py       # AWS Bedrock backend
│   ├── mlx.py           # MLX inference + protocol conversion
│   └── mlx_server.py    # MLX subprocess management
├── admin/               # Admin API routes
├── tracking/            # Usage logging & pricing
├── ecc/                 # everything-claude-code installer
│   ├── sync.py          #   git clone/pull into cache
│   ├── catalog.py       #   scan repo → agents/skills/commands/rules
│   ├── presets.py       #   curated bundles (Starter, Web Dev, ...)
│   ├── installer.py     #   plan/apply file installs + hash tracking
│   ├── mcp.py           #   merge mcpServers into target JSON
│   ├── hooks.py         #   plugin symlink + settings.json merge
│   ├── token_filter.py  #   Hybrid token filter hook (regex + MLX)
│   ├── uninstaller.py   #   revert + restore backups
│   ├── profile.py       #   export/import portable install bundle
│   ├── auto_sync.py     #   background cron (ECC + ACC)
│   └── hashes.py        #   hash helpers for diff detection
├── acc/                 # awesome-claude-code catalog browser
└── static/              # Web dashboard (HTML/CSS/JS)
```

## Development

```bash
# Run with debug logging
CCM_LOG_LEVEL=DEBUG CCM_ADMIN_EMAIL=admin@example.com CCM_ADMIN_PASSWORD=dev123 python -m app.main
```

The admin dashboard is served as static files — edit `app/static/` and refresh.

## Credits

The Claude Setup panel integrates two upstream community projects. Neither is bundled with this repo — they are fetched on demand and cached under `~/.cache/claude-croxy/`. All credit for the content installed by this panel goes to the original authors.

- **[everything-claude-code](https://github.com/affaan-m/everything-claude-code)** by [Affaan Mustafa](https://github.com/affaan-m) — MIT.
  Source of the 48 agents, 183 skills, 79 commands, 88 rules, 14 MCP server definitions, and the hook suite. Claude Croxy only provides a local installer around it; the definitions themselves are upstream's work.

- **[awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)** by [hesreallyhim](https://github.com/hesreallyhim) — MIT.
  Source of the curated external catalog (`THE_RESOURCES_TABLE.csv`). Claude Croxy displays it as a browse-only table that links out to each project.

If upstream licensing, authorship, or structure changes, this README and the loaders in `app/ecc/` / `app/acc/` should be updated to match.

## License

MIT
