import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_service.config import Settings
from agent_service.mcp_server.server import build_server
from agent_service.mcp_server.store import LocalStore
from agent_service.telemetry import setup_tracing

_EXPORTER = InMemorySpanExporter()
setup_tracing(Settings(otel_exporter_otlp_endpoint=None), exporter=_EXPORTER)


@pytest.fixture
def spans() -> InMemorySpanExporter:
    _EXPORTER.clear()
    return _EXPORTER


@pytest.fixture
def settings() -> Settings:
    return Settings(
        anthropic_api_key_secret_arn=None,
        mcp_server_url=None,
        knowledge_bucket=None,
        service_api_token=None,
        otel_exporter_otlp_endpoint=None,
        agent_max_steps=4,
    )


@pytest.fixture
def mcp_server():
    return build_server(LocalStore())
