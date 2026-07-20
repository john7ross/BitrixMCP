"""Entry point. Selects transport from CLI args / environment.

Examples:
    bitrix-mcp                     # stdio (default) — for local agents
    bitrix-mcp --http              # Streamable HTTP on 127.0.0.1:8000/mcp
    bitrix-mcp --http --host 0.0.0.0 --port 5015
    BITRIX_TRANSPORT=http bitrix-mcp
"""

from __future__ import annotations

import argparse
import os

from .server import mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="bitrix-mcp", description="Bitrix24 MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http"],
        default=os.environ.get("BITRIX_TRANSPORT", "stdio"),
        help="Transport to run (default: stdio, or BITRIX_TRANSPORT).",
    )
    parser.add_argument("--http", action="store_true", help="Shortcut for --transport http.")
    parser.add_argument("--host", default=os.environ.get("BITRIX_HTTP_HOST", "127.0.0.1"),
                        help="HTTP bind host (default 127.0.0.1; use 0.0.0.0 to expose on the network).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BITRIX_HTTP_PORT", "8000")),
                        help="HTTP port (default 8000).")
    args = parser.parse_args()

    transport = "streamable-http" if (args.http or args.transport in ("http", "streamable-http")) else "stdio"

    if transport == "streamable-http":
        # Configure the HTTP server before running.
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # Stateless JSON responses scale better and avoid the fragile long-lived
        # SSE session issues that plagued the previous deployment.
        mcp.settings.json_response = True
        mcp.settings.stateless_http = True
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
