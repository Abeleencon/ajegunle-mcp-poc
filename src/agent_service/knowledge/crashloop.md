# Pod in CrashLoopBackOff

Tags: crashloop, restart, pod, kubernetes, startup

## Symptoms
- `kubectl -n agent get pods` shows `CrashLoopBackOff` for `mcp-agent` pods.

## Diagnosis
1. `kubectl -n agent logs <pod> -c agent --previous` for the last crash.
2. `AccessDeniedException` on `secretsmanager:GetSecretValue` means the pod is not using the `mcp-agent` service account, or the IRSA role annotation is wrong.
3. `readOnlyRootFilesystem` errors mean something writes outside `/tmp`; only `/tmp` is a writable emptyDir.

## Mitigation
- Roll back with `kubectl -n agent rollout undo deploy/mcp-agent`.
