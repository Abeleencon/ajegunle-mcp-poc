"""Open an MCP client session to the tool server, in-process or over streamable HTTP."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session


@asynccontextmanager
async def mcp_session(
    *, url: str | None = None, server: FastMCP | None = None
) -> AsyncIterator[ClientSession]:
    try:
        if url:
            from mcp.client.streamable_http import streamable_http_client

            async with (
                streamable_http_client(url) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                yield session
            return

        if server is None:
            from agent_service.mcp_server.server import default_server

            server = default_server()
        async with create_connected_server_and_client_session(server) as session:
            yield session
    except BaseExceptionGroup as group:
        # The MCP transports run in anyio task groups, which wrap any error raised inside
        # the session (including the caller's own, e.g. an Anthropic API error). Re-raise a
        # lone error as itself so callers can handle it by type.
        leaf: BaseException = group
        while isinstance(leaf, BaseExceptionGroup) and len(leaf.exceptions) == 1:
            leaf = leaf.exceptions[0]
        if leaf is group:
            raise
        raise leaf from None
