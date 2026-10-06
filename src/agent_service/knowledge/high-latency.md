# High API latency

Tags: latency, p99, slow, performance, timeouts

## Symptoms
- p99 latency on `/v1/agent/invoke` above 20 seconds for 10 minutes.
- Upstream timeouts in client logs.

## Diagnosis
1. Open the trace for a slow request and compare the `chat claude-opus-5-5` spans with the `execute_tool` spans.
2. If model spans dominate, check the Anthropic status page and lower `ANTHROPIC_EFFORT` to `low` for the affected route.
3. If tool spans dominate, check the knowledge bucket's S3 request latency in CloudWatch.

## Mitigation
- Scale the deployment: `kubectl -n agent scale deploy/mcp-agent --replicas=6`.
- The HorizontalPodAutoscaler targets 70% CPU; confirm it is not pinned at `maxReplicas` (10).
