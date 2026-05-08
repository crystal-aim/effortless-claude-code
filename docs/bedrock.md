# AWS Bedrock Backend

Route requests through AWS Bedrock instead of (or as a fallback to) the Anthropic API. Auth is via SSO device authorization using an AWS CLI profile.

## 1. Set up an AWS profile

Install the AWS CLI and configure an SSO profile:

```bash
aws configure sso --profile bedrock-claude
```

You'll be prompted for:
- **SSO start URL** — your org's AWS access portal (e.g. `https://your-org.awsapps.com/start`)
- **SSO region** — region of the SSO portal (e.g. `us-east-1`)
- **Account** and **Role** — pick one with Bedrock access

## 2. Enable model access

In the AWS Console:

1. Go to **Amazon Bedrock → Model access** (in the region you'll use, e.g. `us-east-1`)
2. Click **Manage model access**
3. Enable the Anthropic Claude models you need (Sonnet, Opus, Haiku)
4. Wait for status to show **Access granted**

The IAM role also needs `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`. Minimal policy:

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

## 3. Configure Croxy

Add to `config.yaml`:

```yaml
backend:
  provider: "bedrock"
  bedrock:
    region: "us-east-1"
    aws_profile: "bedrock-claude"
    sso_start_url: "https://your-org.awsapps.com/start"
    model_map:
      claude-opus-4-7: "global.anthropic.claude-opus-4-7"
      claude-sonnet-4-6: "global.anthropic.claude-sonnet-4-6"
      claude-haiku-4-5-20251001: "global.anthropic.claude-haiku-4-5-20251001-v1:0"
```

## 4. Complete SSO login

Open the admin dashboard → **Provider** tab → **Login with AWS SSO**. The device-authorization flow will open in your browser. The session token is cached in `sso_state.json`.

## Auto-fallback mode

To use Claude as primary and Bedrock as fallback (when Claude returns 529 overloaded):

```yaml
backend:
  provider: "auto"
  bedrock:
    # …same config as above
```
