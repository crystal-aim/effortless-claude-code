# Effortless Claude Code — Claude Croxy

A local web UI to install, manage, and update Claude Code enhancements — agents, skills, commands, rules, MCP servers, and hooks — for your `~/.claude/` setup or any individual project.

> **One-click installer for [everything-claude-code](https://github.com/affaan-m/everything-claude-code)** (48 agents, 183 skills, 79 commands, 88 rules, 14 MCP servers, hooks) plus a browser for [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code).

Everything happens locally — no external plugin system, no account required.

---

## Quick Start

### Prerequisites

- Python 3.11+

### 1. Install

```bash
git clone https://github.com/crystal-aim/effortless-claude-code.git
cd effortless-claude-code
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Open `config.yaml` and replace `session_secret` with a long random string:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

That's it — no API keys or AWS setup needed for the Claude Code tool installer.

### 3. Set up admin credentials

Copy the example `.env` and edit it:

```bash
cp .env.example .env
```

Set `CCM_ADMIN_EMAIL` and `CCM_ADMIN_PASSWORD` — these seed the initial admin user on first run. The app loads `.env` automatically via `python-dotenv`.

### 4. Run

```bash
python -m app.main
```

### 5. Open the admin UI

Visit **`http://localhost:4000/ui/admin`** and sign in with the admin credentials from `.env`.

In the sidebar, click **Claude Setup** — this is the panel that installs Claude Code tools.

From here you can:

- **Sync** the upstream `everything-claude-code` repo into a local cache
- **Browse** all 48 agents, 183 skills, 79 commands, 88 rules, 14 MCP servers
- **Install** a curated preset (Starter / Web Dev / Security / Full) or hand-pick items
- Choose **user-level** (`~/.claude/`) or **project-level** (`<your-project>/.claude/`) install
- See diffs, backups, and on-disk drift in the **Installed** tab
- **Export / Import** your install profile to share between machines
- Browse the searchable **Awesome-CC** catalog for external tools

That's the whole flow — pick what you want, click install, done.

For the full feature list of the Claude Setup panel, see [docs/claude-setup.md](docs/claude-setup.md).

---

## Optional features

Claude Croxy also bundles a few extras you can opt into. Each lives in its own doc:

| Feature | What it does | Doc |
|---|---|---|
| **Token Filter** | A PreToolUse hook that auto-truncates verbose CLI output (git diff, find, pytest, …) to save 60–98% of tokens. Optional MLX backend for unmatched commands. | [docs/token-filter.md](docs/token-filter.md) |
| **API Proxy + Virtual Keys** | Drop-in replacement for `/v1/messages` with `sk-ccm-*` keys, per-key budgets, rate limits, usage tracking, and auto routing. | [docs/api-proxy.md](docs/api-proxy.md) |
| **AWS Bedrock backend** | Forward requests to Bedrock via SSO device authorization. Use as primary or as a 529-overload fallback for the Anthropic API. | [docs/bedrock.md](docs/bedrock.md) |
| **Local MLX inference** | Run Gemma 4, Qwen 2.5, Llama 3.1, Mistral Nemo locally on Apple Silicon. Tool calling supported via Anthropic-to-OpenAI conversion. | [docs/mlx.md](docs/mlx.md) |
| **Auto-Start (macOS)** | LaunchAgent to start Croxy on login, plus an MLX watchdog that resumes the last model and restarts it on crash. | [docs/auto-start.md](docs/auto-start.md) |
| **Full config reference** | Every YAML option and environment variable. | [docs/configuration.md](docs/configuration.md) |

You don't need any of these to use the Claude Code tool installer.

---

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
│   ├── claude.py        # Anthropic API backend
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
# Run with debug logging — set in .env or pass inline
python -m app.main
```

Inline shell vars take precedence over `.env`, so this is the easiest way to bump log level for one run without editing the file.

The admin dashboard is served as static files — edit `app/static/` and refresh.

## License

MIT
