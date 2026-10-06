"""Run the MCP server over streamable HTTP: `python -m agent_service.mcp_server`.

It binds to 127.0.0.1 by default, so in EKS only the agent container in the same pod
can reach it. Incoming requests continue the agent's trace (W3C traceparent).
"""

import os

import uvicorn
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

from agent_service.config import get_settings
from agent_service.mcp_server.server import build_server, store_from_settings
from agent_service.telemetry import setup_tracing


def main() -> None:
    settings = get_settings()
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8001"))
    setup_tracing(settings.model_copy(update={"otel_service_name": "mcp-runbooks"}))
    server = build_server(store_from_settings(settings), host=host, port=port)
    app = OpenTelemetryMiddleware(server.streamable_http_app())
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
