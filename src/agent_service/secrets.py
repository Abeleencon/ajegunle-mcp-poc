"""Build the Anthropic client, reading the API key from Secrets Manager when configured.

In EKS the pod's IRSA role may call secretsmanager:GetSecretValue on exactly one secret
ARN, so no API key is ever stored in a Kubernetes Secret, image, or manifest.
"""

from __future__ import annotations

from anthropic import AsyncAnthropic

from agent_service.config import Settings


def resolve_api_key(settings: Settings) -> str | None:
    if not settings.anthropic_api_key_secret_arn:
        return None  # fall back to the SDK's normal credential chain (ANTHROPIC_API_KEY, ...)
    import boto3

    sm = boto3.client("secretsmanager", region_name=settings.aws_region)
    value = sm.get_secret_value(SecretId=settings.anthropic_api_key_secret_arn)
    return value["SecretString"].strip()


def build_client(settings: Settings) -> AsyncAnthropic:
    api_key = resolve_api_key(settings)
    return AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()
