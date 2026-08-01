"""HTTP receiver for Bitrix24 outgoing webhooks.

Split in two on purpose:
  * `handle_delivery` - pure function, no web framework. All the logic that can
    be wrong lives here and is unit-tested without a server.
  * `register` - thin Starlette glue, mounted on the MCP server's app via
    `mcp.custom_route`. Only called by the HTTP transport.

Delivery contract (from the official docs): POST,
application/x-www-form-urlencoded, PHP bracket arrays, and
`auth[application_token]` proves the call really came from your portal.
Bitrix retries whenever the handler does not answer 200, so this path must be
fast, idempotent, and must never raise.

Note: routes registered through `mcp.custom_route` are NOT covered by the SDK's
authorization, so the token check below is the only thing standing between this
endpoint and the open network. It fails closed when no token is configured.
"""
from __future__ import annotations

import hmac
from typing import Any

from .phpform import parse_php_form
from .store import EventStore

MAX_BODY = 1_000_000  # 1 MB; event payloads are tiny, anything larger is junk


class Result:
    __slots__ = ("status", "body", "stored_id", "reason")

    def __init__(self, status: int, body: str, stored_id: int | None = None,
                 reason: str = "") -> None:
        self.status, self.body, self.stored_id, self.reason = status, body, stored_id, reason

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Result(status={self.status}, reason={self.reason!r}, id={self.stored_id})"


def handle_delivery(raw_body: bytes, expected_token: str | None,
                    store: EventStore) -> Result:
    """Validate one delivery and persist it. Never raises."""
    try:
        if not expected_token:
            # Refuse to run an unauthenticated public endpoint by accident.
            return Result(503, "receiver not configured", reason="no_token_configured")
        if len(raw_body) > MAX_BODY:
            return Result(413, "payload too large", reason="too_large")

        payload = parse_php_form(raw_body.decode("utf-8", "replace"))
        auth = payload.get("auth") or {}
        token = auth.get("application_token", "") if isinstance(auth, dict) else ""
        if not hmac.compare_digest(str(token), str(expected_token)):
            # Do not echo the received token back - it would land in logs.
            return Result(403, "forbidden", reason="bad_token")

        event = str(payload.get("event") or "").upper()
        if not event:
            return Result(400, "missing event", reason="no_event")

        # The token is not data - never store the credential alongside the event.
        if isinstance(auth, dict):
            auth.pop("application_token", None)

        row_id = store.put(event, payload.get("ts"), payload)
        if row_id is None:
            return Result(200, "ok", reason="duplicate")
        return Result(200, "ok", stored_id=row_id, reason="stored")
    except Exception as exc:  # noqa: BLE001 - a 500 makes Bitrix retry forever
        return Result(200, "ok", reason=f"error:{type(exc).__name__}")


def register(mcp: Any, store: EventStore, path: str, token_getter) -> None:
    """Mount the receiver on the MCP server's Starlette app (HTTP transport only).

    `token_getter` is a callable rather than a value so the token is re-read
    from the environment on every delivery - matching how Config works.
    """
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse

    @mcp.custom_route(path, methods=["POST"])
    async def _b24_events(request: Request) -> PlainTextResponse:
        result = handle_delivery(await request.body(), token_getter(), store)
        return PlainTextResponse(result.body, status_code=result.status)
