"""MCP server instance and tool registration for the Bitrix24 gateway."""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version as _dist_version

from mcp.server.mcpserver import MCPServer

try:
    # Single source of truth: pyproject.toml, read through the installed
    # metadata. Hardcoding it here as well would guarantee the two drift.
    __version__ = _dist_version("bitrix-mcp")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0.0.0+source"

INSTRUCTIONS = """\
Universal Bitrix24 REST gateway. Two layers of tools:

1. Universal backbone — `b24_call` (any REST method), `b24_batch` (up to 50
   calls in one hit), `b24_list_methods`, `b24_test_connection`. These give
   100% coverage of the Bitrix24 API; reach for them when no typed tool fits.
2. Typed convenience tools for the common domains — CRM, tasks & scrum,
   calendar, disk, users/structure, messaging — with correct pagination,
   filters, and Bitrix quirks handled for you.

Auth: calls use the default webhook (BITRIX_WEBHOOK_URL) unless you pass
`webhook_url` or `personal_webhook`. Use `personal_webhook` to write as a
specific user. Set BITRIX_READ_ONLY=1 to block all writes.

Errors are returned verbatim (never hidden as empty results): watch for
`code: "ACCESS_DENIED"` (a permissions issue on the portal, not a bug) and
`code: "B24_READONLY"` (write blocked by read-only mode).
"""

# `version` is what clients read as `serverInfo.version`. Passing it matters:
# the 1.x FastMCP had no such parameter, so every client saw the MCP SDK's own
# version instead and could not tell which build of THIS server it was talking
# to.
mcp = MCPServer("bitrix24_mcp", instructions=INSTRUCTIONS, version=__version__)


_TOOL_MODULES = (
    "universal", "discovery", "crm", "tasks", "scrum", "calendar", "disk", "users",
    "messaging", "lists", "catalog", "bizproc", "telephony",
    "groups", "sale", "documents", "events", "telegram",
)


def _register_all() -> None:
    """Import tool modules for their @mcp.tool decorator side effects."""
    for name in _TOOL_MODULES:
        importlib.import_module(f".tools.{name}", __package__)


_register_all()
