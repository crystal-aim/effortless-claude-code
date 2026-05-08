# MLX Local Inference

Run open-source models locally on Apple Silicon. Tool calling is supported via Anthropic-to-OpenAI protocol conversion.

## Requirements

- Apple Silicon Mac (M1 or later)
- Enough RAM for the model you pick (4-bit quantized: ~5 GB for 7B, ~22 GB for 32B)

## Configure

Add to `config.yaml`:

```yaml
backend:
  provider: "mlx"
  mlx:
    base_url: "http://localhost:8899"
    timeout_seconds: 300
    port: 8899
    model_map:
      # Gemma 4 (custom-token tool protocol)
      gemma-4-e2b-it: "mlx-community/gemma-4-e2b-it-4bit"
      gemma-4-e4b-it: "mlx-community/gemma-4-e4b-it-4bit"
      # Standard OpenAI tool calling
      qwen-2.5-7b-it: "mlx-community/Qwen2.5-7B-Instruct-4bit"
      qwen-2.5-32b-it: "mlx-community/Qwen2.5-32B-Instruct-4bit"
      qwen-2.5-coder-32b-it: "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit"
      llama-3.1-8b-it: "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit"
      mistral-nemo-12b-it: "mlx-community/Mistral-Nemo-Instruct-2407-4bit"
```

The model name detection picks the tool-calling protocol:
- name contains `gemma` → Gemma 4 custom-token path
- otherwise → standard OpenAI `tool_calls`

## Download & start models

Open the admin dashboard → **MLX** tab. From there you can:
- Browse the configured `model_map` and download the underlying weights via Hugging Face
- Start / stop the MLX server with the model of your choice
- See VRAM usage and tokens-per-second in real time

The last model you used is auto-resumed when the app restarts (see [auto-start.md](auto-start.md)).

## Pricing

Local models are free — leave them at `0.0`:

```yaml
pricing:
  gemma-4-e2b-it:
    input: 0.0
    output: 0.0
  qwen-2.5-7b-it:
    input: 0.0
    output: 0.0
```

## Use as Token Filter backend

The token filter's MLX classification + summarization paths point at this same MLX server. See [token-filter.md](token-filter.md).
