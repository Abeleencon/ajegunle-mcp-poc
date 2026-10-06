"""Runtime configuration, read from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # Claude
    anthropic_model: str = "claude-opus-5-5"
    # Opus 5.5 defaults to "medium"; set explicitly so behaviour does not drift.
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    anthropic_max_tokens: int = 16000
    # When set, the API key is read from AWS Secrets Manager (via IRSA) instead of
    # the ANTHROPIC_API_KEY environment variable.
    anthropic_api_key_secret_arn: str | None = None

    # Agent loop guardrails
    agent_max_steps: int = Field(default=8, ge=1, le=32)
    agent_tool_timeout_seconds: float = 20.0

    # MCP. Empty URL means the tool server runs in-process over an in-memory transport.
    mcp_server_url: str | None = None

    # Knowledge store used by the MCP server. Empty bucket means bundled local files.
    knowledge_bucket: str | None = None
    knowledge_prefix: str = "runbooks/"
    aws_region: str = "us-east-1"

    # API
    service_api_token: str | None = None  # optional bearer token for /v1 routes
    max_input_chars: int = 8000

    # Telemetry
    otel_service_name: str = "mcp-agent"
    otel_exporter_otlp_endpoint: str | None = None
    otel_console_export: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
