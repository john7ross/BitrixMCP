"""Discovery tools - the knowledge half of full API coverage.

`b24_call` can already reach every REST method. These tools tell the agent
which method to reach for and what to pass it, so a signature is looked up
rather than guessed.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from pydantic import Field

from ..catalog import method_schema, method_search, scope_gaps, stats
from ..runtime import READ, PersonalWebhook, WebhookUrl, err, get_client, ok
from ..server import mcp

try:
    from mcp.server.fastmcp import Context
except Exception:  # pragma: no cover
    Context = Any  # type: ignore


@mcp.tool(name="b24_method_search", annotations=READ)
async def b24_method_search(
    query: Annotated[str, Field(description="What you are trying to do or a partial method name, e.g. 'call recording', 'crm.deal', 'move task stage'.")],
    scope: Annotated[Optional[str], Field(default=None, description="Restrict to one scope, e.g. 'crm', 'task', 'telephony'.")] = None,
    include_deprecated: Annotated[bool, Field(default=False, description="Include methods Bitrix marks as deprecated.")] = False,
    limit: Annotated[int, Field(default=15, ge=1, le=50, description="Max results.")] = 15,
    ctx: Context | None = None,
) -> str:
    """Find the right REST method for a task, across the whole documented API.

    Use this before b24_call whenever you are unsure a method exists or what it
    is named. Covers every documented method, including the many that the
    portal's own `methods` listing omits.

    Returns:
        JSON: {"results": [{"method","scope","deprecated","required_params",
        "all_params","doc"}], "count", "catalog": {...}}.
        Follow up with b24_method_schema for full parameter details.
    """
    try:
        results = method_search(query, scope=scope,
                                include_deprecated=include_deprecated, limit=limit)
        return ok({"results": results, "count": len(results), "catalog": stats()})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_method_schema", annotations=READ)
async def b24_method_schema(
    method: Annotated[str, Field(description="Exact method name, e.g. 'crm.item.list', 'telephony.externalCall.register'.")],
    ctx: Context | None = None,
) -> str:
    """Get a method's exact parameters from the official documentation.

    Call this instead of guessing a signature. Reports each parameter's name,
    type, whether it is required, and its description, plus the scope needed and
    whether the method is deprecated.

    Returns:
        JSON: {"found": true, "method", "scope", "deprecated", "doc",
        "params": [{"name","type","required","desc"}]}.
        If unknown: {"found": false, "did_you_mean": [...]} - the method may
        still be callable, since the catalog covers documented methods only.
    """
    try:
        return ok(method_schema(method))
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_scope_gaps", annotations=READ)
async def b24_scope_gaps(
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Diagnose what this webhook cannot reach, and why.

    Reads the scopes actually granted and compares them against the full
    documented API. Use this when a call fails with ERROR_METHOD_NOT_FOUND or
    ACCESS_DENIED: on a live portal those two are easy to confuse with "the
    module is not installed", and they usually mean the scope was simply not
    ticked when the webhook was created.

    Returns:
        JSON: {"granted_scopes": [...], "reachable_methods": <int>,
        "blocked_methods": <int>, "missing_scopes": {scope: method_count},
        "hint": "..."}.
    """
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        granted = await client.call_result("scope")
        if isinstance(granted, dict):
            granted = list(granted.values())
        return ok(scope_gaps([str(s) for s in (granted or [])]))
    except Exception as exc:  # noqa: BLE001
        return err(exc)
