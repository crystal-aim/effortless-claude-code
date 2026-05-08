# API Proxy & Virtual Keys

Use Claude Croxy as a drop-in replacement for `https://api.anthropic.com/v1/messages` with virtual keys, per-key budgets, and usage tracking.

## How auth works

Croxy doesn't store an upstream Anthropic API key. The `claude` backend simply forwards the headers from the incoming request to `api.anthropic.com`. The split:

- **`x-cc-api-key`** (or `x-ccm-key`) — your `sk-ccm-*` virtual key. Croxy validates it (key budgets, rate limits, expiry) and **strips it** before forwarding upstream.
- **`x-api-key`** — your real `sk-ant-*` Anthropic key. Croxy leaves it untouched; Anthropic uses it to authenticate the request.

The Claude Code CLI sends both headers automatically when `ANTHROPIC_BASE_URL` points at the proxy and `ANTHROPIC_API_KEY` is your real Anthropic key. The virtual key goes into a separate `x-cc-api-key` config in the CLI.

> If you're using the **Bedrock** or **MLX** backend, no Anthropic key is needed at all — Bedrock auth is via SSO, MLX runs locally.

## 1. Generate a virtual key

Open the admin dashboard at `http://localhost:4000/ui/admin` → **Keys** tab → **New key**.

You can set per-key:
- Budget (USD) with daily / weekly / monthly reset period
- Expiration date
- Rate limit (requests per minute)

The key is shown once — copy it immediately. Format: `sk-ccm-…`.

## 2. Call the proxy (curl)

For the `claude` backend, send **both** headers — virtual key for Croxy, real Anthropic key for upstream:

```bash
curl http://localhost:4000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-cc-api-key: sk-ccm-your-virtual-key" \
  -H "x-api-key: sk-ant-your-real-anthropic-key" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

For `bedrock` / `mlx` backends you can drop `x-api-key` entirely — only `x-cc-api-key` is needed:

```bash
curl http://localhost:4000/v1/messages \
  -H "x-cc-api-key: sk-ccm-your-virtual-key" \
  …
```

Streaming (`"stream": true`) and the full Anthropic message schema are supported.

## 3. Point Claude Code / SDKs at the proxy

| Variable | Value |
|---|---|
| `ANTHROPIC_BASE_URL` | `http://localhost:4000` |
| `ANTHROPIC_API_KEY` | `sk-ant-…` (your real Anthropic key — forwarded upstream) |
| `CLAUDE_CODE_API_KEY` *(or equivalent)* | `sk-ccm-…` (your Croxy virtual key — used for budget/tracking) |

For Bedrock/MLX backends, leave `ANTHROPIC_API_KEY` unset and only set the virtual key.

## Architecture

```
Client → Croxy → Auth → Rate Limit → Route → Backend (Claude / Bedrock / MLX)
                                                   │
                                          Usage Tracking → SQLite
```

Each request logs token counts, cost, latency, model, and key. View charts in the admin dashboard's **Usage** tab.

## Auto routing

Route requests to different models by keyword in the system prompt:

```yaml
# config.yaml
routing_rules:
  - keywords: ["opus", "deep reasoning", "architect"]
    model: "claude-opus-4-7"
  - keywords: ["haiku", "quick", "cheap"]
    model: "claude-haiku-4-5-20251001"
```

See [configuration.md](configuration.md) for the full reference.
