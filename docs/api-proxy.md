# API Proxy & Virtual Keys

Use Claude Croxy as a drop-in replacement for `https://api.anthropic.com/v1/messages` with virtual keys, per-key budgets, and usage tracking.

## 1. Add your Anthropic API key

```bash
mkdir -p secrets
echo "sk-ant-YOUR-KEY" > secrets/anthropic_api_key
```

The `claude` provider (default in `config.yaml`) reads from this file.

## 2. Generate a virtual key

Open the admin dashboard at `http://localhost:4000/ui/admin` → **Keys** tab → **New key**.

You can set per-key:
- Budget (USD) with daily / weekly / monthly reset period
- Expiration date
- Rate limit (requests per minute)

The key is shown once — copy it immediately. Format: `sk-ccm-…`.

## 3. Call the proxy

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

Streaming (`"stream": true`) and the full Anthropic message schema are supported.

## 4. Point clients at the proxy

For any Anthropic SDK / Claude Code client, set:

| Variable | Value |
|---|---|
| `ANTHROPIC_BASE_URL` | `http://localhost:4000` |
| `ANTHROPIC_API_KEY` | `sk-ccm-…` (your virtual key) |

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
