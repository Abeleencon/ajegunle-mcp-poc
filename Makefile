.PHONY: install lint test eval-offline eval-live run run-mcp tf-check k8s-render k8s-params

TF := infra/terraform

install:
	pip install -e '.[dev]'

lint:
	ruff check . && ruff format --check .

test:
	pytest -q

eval-offline:
	python -m evals.run --suite retrieval --out eval-retrieval.json

eval-live: ## needs ANTHROPIC_API_KEY (or `ant auth login`); spends API credits
	python -m evals.run --suite agent --out eval-agent.json

run:
	uvicorn agent_service.api.main:app --factory --reload --port 8080

run-mcp:
	MCP_PORT=8001 python -m agent_service.mcp_server

tf-check:
	terraform -chdir=$(TF) fmt -check -recursive
	terraform -chdir=$(TF) init -backend=false -input=false
	terraform -chdir=$(TF) validate
	terraform -chdir=$(TF) test

k8s-render:
	kubectl kustomize deploy/k8s/overlays/prod

k8s-params: ## write overlay params from terraform outputs (after apply)
	@o() { terraform -chdir=$(TF) output -raw $$1; }; { \
	  echo "AWS_REGION=$$(o region)"; \
	  echo "AGENT_ROLE_ARN=$$(o agent_role_arn)"; \
	  echo "COLLECTOR_ROLE_ARN=$$(o collector_role_arn)"; \
	  echo "KNOWLEDGE_BUCKET=$$(o knowledge_bucket)"; \
	  echo "KNOWLEDGE_PREFIX=$$(o knowledge_prefix)"; \
	  echo "ANTHROPIC_API_KEY_SECRET_ARN=$$(o anthropic_secret_arn)"; \
	  echo "OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.agent.svc.cluster.local:4318"; \
	  echo "ANTHROPIC_MODEL=claude-opus-5-5"; \
	  echo "ANTHROPIC_EFFORT=medium"; \
	} > deploy/k8s/overlays/prod/params.env
