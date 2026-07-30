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


def _env(*names: str) -> str | None:
    """First non-empty of several environment names.

    Telegram settings accept both the prefixed form (BITRIX_TELEGRAM_CHAT_ID)
    and the bare one (TELEGRAM_CHAT_ID), because both are what people actually
    type - and a setting silently ignored because of a prefix is indistinguish-
    able from a broken feature.
    """
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


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


    # ---------------- event feed (optional, off unless configured) ----------------

    @property
    def event_db(self) -> str:
        """SQLite file holding received events and poll cursors."""
        return os.environ.get("BITRIX_EVENT_DB", "bitrix_events.sqlite3").strip()

    @property
    def event_retention_days(self) -> int:
        try:
            return max(1, int(os.environ.get("BITRIX_EVENT_RETENTION_DAYS", "14")))
        except ValueError:
            return 14

    @property
    def event_token(self) -> str | None:
        """Token from the portal's outgoing-webhook form.

        Absent means the receiver refuses every delivery: the endpoint must
        never be reachable without proof it is really your portal calling.
        """
        val = os.environ.get("BITRIX_EVENT_TOKEN")
        return val.strip() if val and val.strip() else None

    @property
    def event_path(self) -> str:
        """URL path the outgoing webhook posts to (HTTP transport only)."""
        p = os.environ.get("BITRIX_EVENT_PATH", "/b24/events").strip()
        return p if p.startswith("/") else "/" + p

    @property
    def pull_channel_enabled(self) -> bool:
        """Subscribe to the portal's Push&Pull channel for real-time events.

        Outbound connection only, so this is the one event path that works from
        a workstation behind NAT or VPN - the same mechanism the Bitrix24 mobile
        and desktop apps use.
        """
        return os.environ.get("BITRIX_PULL_CHANNEL", "").strip().lower() in ("1", "true", "yes", "on")

    @property
    def telegram_token(self) -> str | None:
        """Bot token from @BotFather. Absent means forwarding stays off."""
        return _env("BITRIX_TELEGRAM_TOKEN", "TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN")

    @property
    def telegram_chat_id(self) -> str | None:
        """Where to post: a numeric user id, a channel id (-100...), or @name."""
        return _env("BITRIX_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID")

    @property
    def telegram_allowed_users(self) -> list[str]:
        """Chat ids forwarding is permitted to reach. Empty list = no restriction.

        This is a security boundary, not a convenience setting, and it is
        readable ONLY from the environment - never from the runtime settings the
        agent can write.

        Reason: b24_telegram_configure lets an agent change chat_id, and that
        agent reads portal content - task text, comments, mail. A prompt
        injection hidden in any of it could redirect the whole event feed to a
        stranger's chat. With this list set, such a redirect is refused.
        """
        raw = _env("BITRIX_TELEGRAM_ALLOWED_USERS", "TELEGRAM_ALLOWED_USERS") or ""
        return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]

    @property
    def telegram_filter(self) -> str:
        """Which events to forward. Empty means none - see events/telegram.py.

        Deliberately not defaulted to '*': the pull channel carries a lot of
        chat noise, and a forwarder that floods on first run gets muted and
        never trusted again. The user decides what is worth an interruption.
        """
        return (_env("BITRIX_TELEGRAM_EVENTS", "TELEGRAM_EVENTS") or "").strip()

    @property
    def telegram_enabled(self) -> bool:
        raw = os.environ.get("BITRIX_TELEGRAM_ENABLED")
        if raw is None or not raw.strip():
            # No explicit switch: on as soon as a token and a target exist.
            return bool(self.telegram_token and self.telegram_chat_id)
        return raw.strip().lower() in ("1", "true", "yes", "on")

    @property
    def portal_url(self) -> str | None:
        """Portal base URL, derived from the webhook, for links in messages."""
        hook = self.default_webhook
        if not hook:
            return None
        parts = hook.split("/rest/", 1)
        return parts[0] if len(parts) == 2 else None

    @property
    def ssl_certfile(self) -> str | None:
        """TLS cert for --http. The MCP SDK's runner has no TLS options, so the
        entry point builds its own uvicorn server when these are set."""
        val = os.environ.get("BITRIX_HTTP_SSL_CERT")
        return val.strip() if val and val.strip() else None

    @property
    def ssl_keyfile(self) -> str | None:
        val = os.environ.get("BITRIX_HTTP_SSL_KEY")
        return val.strip() if val and val.strip() else None


config = Config()
