# Configuration Reference

Full reference for `config.yaml` and environment variables. The minimal example in [`config.example.yaml`](../config.example.yaml) only sets `session_secret` — everything else has defaults.

## Environment variables

The app loads `.env` from the project root via `python-dotenv` at startup. Copy [`.env.example`](../.env.example) to `.env` and edit. Inline shell exports take precedence over `.env`.

| Variable | Default | Description |
|---|---|---|
| `CCM_ADMIN_EMAIL` | — | Initial admin account email (required on first run only — used to seed the admin user; afterwards stored in DB) |
| `CCM_ADMIN_PASSWORD` | — | Initial admin account password (required on first run only) |
| `CCM_CONFIG` | `config.yaml` | Path to config file |
| `CCM_DATABASE_URL` | `sqlite:///./data.db` | Database connection string |
| `CCM_LOG_LEVEL` | `INFO` | Logging level |

## `server`

```yaml
server:
  host: "0.0.0.0"
  port: 4000
  session_secret: "…"  # REQUIRED — long random string
```

Generate a secret with:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

## `upstream`

Used by the `claude` provider.

```yaml
upstream:
  base_url: "https://api.anthropic.com"
  timeout_seconds: 600
```

## `rate_limit`

```yaml
rate_limit:
  proxy_per_minute: 60
  login_per_minute: 10
```

## `backend`

```yaml
backend:
  # claude  = forward to Anthropic API
  # bedrock = call AWS Bedrock (see docs/bedrock.md)
  # mlx     = local inference on Apple Silicon (see docs/mlx.md)
  # auto    = claude primary, bedrock fallback on 529 overloaded
  provider: "claude"
```

For `bedrock` / `mlx` sub-blocks, see [bedrock.md](bedrock.md) and [mlx.md](mlx.md).

## `default_model`

```yaml
default_model: "claude-sonnet-4-6"
```

## `routing_rules`

Keyword-based routing matched against the request's system prompt.

```yaml
routing_rules:
  - keywords: ["opus", "analysis", "deep reasoning", "architect"]
    model: "claude-opus-4-7"
  - keywords: ["haiku", "quick", "cheap", "tiny"]
    model: "claude-haiku-4-5-20251001"
```

## `pricing`

USD per million tokens, used for cost tracking in the admin dashboard.

```yaml
pricing:
  claude-sonnet-4-6:
    input: 3.0
    output: 15.0
    cache_write: 3.75
    cache_read: 0.30
  claude-opus-4-7:
    input: 5.0
    output: 25.0
    cache_write: 6.25
    cache_read: 0.50
  claude-haiku-4-5-20251001:
    input: 1.0
    output: 5.0
    cache_write: 1.25
    cache_read: 0.10
  # Local MLX models — free
  gemma-4-e2b-it:
    input: 0.0
    output: 0.0
  qwen-2.5-7b-it:
    input: 0.0
    output: 0.0
```

Local MLX models default to `0.0` for all token types if not listed.

## Persisted overrides

A subset of settings can be changed at runtime through the admin dashboard and persist in the database (overriding the YAML on next start):

- `backend.provider`
- `backend.bedrock.aws_profile`
- `backend.bedrock.sso_start_url`
- `backend.bedrock.region`
- `backend.mlx.base_url`
