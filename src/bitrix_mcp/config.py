"""Environment-driven configuration for the Bitrix24 MCP server.

All settings are read from environment variables so the same package works
unchanged whether it is launched over stdio (per-agent subprocess) or as a
shared Streamable-HTTP service. Nothing here is specific to any consuming
application — the server is a generic Bitrix24 REST gateway.
"""

from __future__ import annotations

import os


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


class Config:
    """Lazily reads process environment (re-read each access so tests/embedders
    can mutate os.environ before a call without re-importing the module)."""

    @property
    def default_webhook(self) -> str | None:
        """Default incoming webhook, e.g. https://portal.bitrix24.ru/rest/<id>/<token>/

        Used when a tool call does not supply its own ``webhook_url`` /
        ``personal_webhook`` and no ``X-B24-Webhook`` HTTP header is present.
        """
        val = os.environ.get("BITRIX_WEBHOOK_URL")
        return val.strip() if val else None

    @property
    def read_only(self) -> bool:
        """When true, any write method (add/update/delete/...) is refused with a
        clear error before it reaches the portal. Reads always pass through."""
        return _as_bool(os.environ.get("BITRIX_READ_ONLY"), default=False)

    @property
    def timeout(self) -> float:
        """Per-request HTTP timeout in seconds."""
        try:
            return float(os.environ.get("BITRIX_TIMEOUT", "60"))
        except ValueError:
            return 60.0

    @property
    def max_pages(self) -> int:
        """Hard cap on pages fetched when ``fetch_all=True`` on a list tool.

        Prevents a single call from silently walking an entire large portal
        (each page is up to 50 records, so 40 pages == 2000 records)."""
        try:
            return max(1, int(os.environ.get("BITRIX_MAX_PAGES", "40")))
        except ValueError:
            return 40


config = Config()
