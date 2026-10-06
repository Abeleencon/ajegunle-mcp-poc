"""OpenTelemetry tracing setup.

Spans follow the OpenTelemetry GenAI semantic conventions:
  invoke_agent <agent>  ->  chat <model>  /  execute_tool <tool>
Prompt and completion text are never recorded; only sizes, token counts and ids.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from agent_service import __version__
from agent_service.config import Settings

TRACER_NAME = "agent_service"
_configured = False


def setup_tracing(settings: Settings, exporter: SpanExporter | None = None) -> TracerProvider:
    """Install a global TracerProvider once. `exporter` lets tests capture spans in memory."""
    global _configured
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": settings.otel_service_name, "service.version": __version__}
        )
    )
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    if settings.otel_console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    if not _configured:
        trace.set_tracer_provider(provider)
        _instrument_httpx()
        _configured = True
    return provider


def _instrument_httpx() -> None:
    # The Anthropic SDK and the MCP HTTP client both use httpx, so this gives outbound
    # spans and W3C traceparent propagation into the MCP sidecar.
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME, __version__)


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
