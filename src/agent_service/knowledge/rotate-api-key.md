# Rotate the Anthropic API key

Tags: secret, rotate, api key, credentials, secrets manager

## Steps
1. Create a new key in the Claude Console.
2. `aws secretsmanager put-secret-value --secret-id mcp-agent/anthropic-api-key --secret-string <new key>`.
3. Restart pods so they read the new value: `kubectl -n agent rollout restart deploy/mcp-agent`.
4. Confirm `/readyz` returns 200, then revoke the old key in the Console.

The pod role may only read this one secret; it cannot list or write secrets.
