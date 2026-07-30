"""Shared runtime helpers used by every tool module.

Centralizes the three things that would otherwise be duplicated across ~50
tools: (1) resolving which webhook to use, (2) enforcing the read-only guard,
(3) turning results and errors into consistent JSON strings.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from pydantic import Field

from .client import BitrixClient, BitrixError, is_write_method
from .config import config
from .sanitize import sanitize

try:  # Context is only needed for HTTP header support; import defensively.
    from mcp.server.fastmcp import Context
except Exception:  # pragma: no cover
    Context = Any  # type: ignore

# ---------------------------------------------------------------------------
# Reusable Annotated parameter types (keep tool signatures DRY and consistent)
# ---------------------------------------------------------------------------

WebhookUrl = Annotated[
    Optional[str],
    Field(
        default=None,
        description=(
            "Override the default incoming webhook for this call "
            "(https://<portal>/rest/<user_id>/<token>/). If omitted, uses "
            "personal_webhook, then the X-B24-Webhook HTTP header, then the "
            "BITRIX_WEBHOOK_URL env default."
        ),
    ),
]

PersonalWebhook = Annotated[
    Optional[str],
    Field(
        default=None,
        description=(
            "Act as a specific user (their personal incoming webhook). Takes "
            "precedence over webhook_url. Required to write to the portal under "
            "that user's permissions."
        ),
    ),
]

Filter = Annotated[
    Optional[dict],
    Field(
        default=None,
        description=(
            "Bitrix filter object. Keys may carry operator prefixes: "
            "'>', '<', '>=', '<=', '!', '%' (substring), '=%', e.g. "
            "{\">=DATE_CREATE\": \"2026-01-01\", \"%TITLE\": \"draft\"}."
        ),
    ),
]

Select = Annotated[
    Optional[list],
    Field(
        default=None,
        description="Fields to return, e.g. ['ID','TITLE','*','UF_*']. Omit for defaults.",
    ),
]

Order = Annotated[
    Optional[dict],
    Field(
        default=None,
        description="Sort object, e.g. {'ID':'DESC'} or {'DATE_CREATE':'ASC'}.",
    ),
]

Start = Annotated[
    int,
    Field(default=0, ge=0, description="Pagination offset (page size is 50). Use 'next' from a prior response."),
]

FetchAll = Annotated[
    bool,
    Field(
        default=False,
        description=(
            "Auto-paginate and return all matching records (capped by "
            "BITRIX_MAX_PAGES, default 40 pages = 2000 records). Use a filter to keep this bounded."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Tool annotation presets
# ---------------------------------------------------------------------------

READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}


# ---------------------------------------------------------------------------
# Webhook resolution
# ---------------------------------------------------------------------------

def _header_webhook(ctx: Any) -> str | None:
    """Best-effort read of the X-B24-Webhook request header (HTTP transport only)."""
    try:
        request = ctx.request_context.request  # type: ignore[attr-defined]
        return request.headers.get("X-B24-Webhook")
    except Exception:
        return None


def resolve_webhook(
    ctx: Any,
    webhook_url: str | None,
    personal_webhook: str | None,
) -> str:
    """Pick the effective webhook by precedence:

    personal_webhook > webhook_url > X-B24-Webhook header > BITRIX_WEBHOOK_URL env.
    Raises a clear error if none is configured.
    """
    candidate = personal_webhook or webhook_url or _header_webhook(ctx) or config.default_webhook
    if not candidate:
        raise BitrixError(
            "No webhook configured. Set BITRIX_WEBHOOK_URL, pass webhook_url / "
            "personal_webhook on the call, or send an X-B24-Webhook header.",
            code="NO_WEBHOOK",
        )
    return candidate


def get_client(ctx: Any, webhook_url: str | None, personal_webhook: str | None) -> BitrixClient:
    return BitrixClient(resolve_webhook(ctx, webhook_url, personal_webhook), timeout=config.timeout)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _dumps(obj: Any) -> str:
    # Single choke point for every tool's output: credentials are stripped here
    # so no individual tool has to remember. Bitrix hands webhook URLs back in
    # ordinary read responses (document generator), and tools echo the resolved
    # webhook when reporting connection state - both would otherwise leak.
    return json.dumps(sanitize(obj), ensure_ascii=False, indent=2, default=str)


def ok(data: Any) -> str:
    return _dumps(data)


def err(exc: Exception) -> str:
    if isinstance(exc, BitrixError):
        return _dumps(exc.as_dict())
    # Some exceptions carry an empty str() - httpx connection failures in
    # particular. Reporting that verbatim turns "the network blocked us" into a
    # blank message, which reads like nothing went wrong at all.
    message = str(exc) or f"{type(exc).__name__} with no message ({exc!r})"
    return _dumps({"error": True, "code": type(exc).__name__, "message": message})


def guard_write(method: str, *, is_write: bool | None = None) -> None:
    """Raise if the read-only guard is on and the method mutates the portal."""
    if not config.read_only:
        return
    writing = is_write if is_write is not None else is_write_method(method)
    if writing:
        raise BitrixError(
            f"Server is in read-only mode (BITRIX_READ_ONLY=1); write method "
            f"'{method}' is blocked. Unset BITRIX_READ_ONLY to allow writes.",
            code="B24_READONLY",
            method=method,
        )


# ---------------------------------------------------------------------------
# High-level call helpers used by tools
# ---------------------------------------------------------------------------

async def run_call(
    ctx: Any,
    method: str,
    params: dict | None,
    *,
    webhook_url: str | None,
    personal_webhook: str | None,
    is_write: bool | None = None,
    unwrap: bool = False,
) -> str:
    """Resolve webhook, enforce guard, call, and format — for single calls.

    ``unwrap=True`` returns only the ``result`` payload; otherwise the full
    response envelope (result/total/next/time)."""
    try:
        guard_write(method, is_write=is_write)
        client = get_client(ctx, webhook_url, personal_webhook)
        data = await client.call(method, params)
        if unwrap:
            return ok(data.get("result"))
        trimmed = {k: v for k, v in data.items() if k in ("result", "total", "next", "time")}
        return ok(trimmed)
    except Exception as exc:  # noqa: BLE001 - errors become tool output, not crashes
        return err(exc)


async def run_list(
    ctx: Any,
    method: str,
    params: dict | None,
    *,
    webhook_url: str | None,
    personal_webhook: str | None,
    start: int = 0,
    fetch_all: bool = False,
) -> str:
    """Resolve webhook, enforce guard (reads pass), paginate, and format."""
    try:
        guard_write(method)  # list methods are reads; guard is a no-op but keeps intent explicit
        client = get_client(ctx, webhook_url, personal_webhook)
        env = await client.call_list(
            method, params, start=start, fetch_all=fetch_all, max_pages=config.max_pages
        )
        return ok(env)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
