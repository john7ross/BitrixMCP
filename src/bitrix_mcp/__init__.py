"""bitrix_mcp — universal, portable MCP server for the Bitrix24 REST API."""

from .server import __version__, mcp

__all__ = ["mcp", "__version__"]
