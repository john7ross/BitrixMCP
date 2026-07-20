"""Universal tools — 100% Bitrix24 REST coverage via raw method access."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from pydantic import Field
from mcp.server.fastmcp import Context

from ..client import BitrixError, is_write_method
from ..runtime import (
    READ,
    WRITE,
    PersonalWebhook,
    WebhookUrl,
    err,
    get_client,
    ok,
    run_call,
)
from ..server import mcp


@mcp.tool(name="b24_call", annotations=WRITE)
async def b24_call(
    method: Annotated[str, Field(description="Any Bitrix24 REST method, e.g. 'crm.deal.list', 'user.get', 'tasks.task.add'.")],
    params: Annotated[Optional[dict], Field(default=None, description="Method parameters as a JSON object (nested filter/fields/select supported).")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Call ANY Bitrix24 REST method directly — the universal escape hatch.

    This is the most reliable tool and covers the entire API surface. Prefer it
    whenever a typed tool does not fit, or when a typed tool behaves oddly.

    Parameters go as a JSON POST body, so nested arrays (filter, fields, select,
    order) are passed exactly as you write them — no manual encoding needed.

    Args:
        method: REST method name (no leading slash), e.g. 'crm.deal.get'.
        params: JSON object of parameters, e.g. {"id": 42} or
            {"filter": {">=DATE_CREATE": "2026-01-01"}, "select": ["ID","TITLE"]}.
        webhook_url / personal_webhook: auth overrides (see server instructions).

    Returns:
        JSON string with the full response envelope, e.g.
        {"result": <payload>, "total": <int?>, "next": <int?>, "time": {...}}.
        On failure: {"error": true, "code": "<BITRIX_CODE>", "message": "..."}.

    Write safety: if BITRIX_READ_ONLY=1, methods that mutate data (add/update/
    delete/...) are refused with code B24_READONLY.
    """
    return await run_call(
        ctx, method, params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
    )


@mcp.tool(name="b24_batch", annotations=WRITE)
async def b24_batch(
    commands: Annotated[
        list[dict],
        Field(description=(
            "List of commands, each {'method': str, 'params'?: object, 'key'?: str}. "
            "Max 50. Use 'key' to name a command so a later command can reference "
            "its result via Bitrix syntax, e.g. params {'ID': '$result[make][ID]'}."
        )),
    ],
    halt: Annotated[bool, Field(default=False, description="Stop the whole batch on the first command error (default false = keep going).")] = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Run up to 50 REST calls in a single request (chained, with back-references).

    Reduces API-limit pressure: 50 sub-calls count as one hit. Commands run in
    order; a later command can consume an earlier one's output using Bitrix's
    ``$result[key][field]`` reference syntax placed literally inside its params.

    Args:
        commands: e.g.
            [{"key":"make","method":"crm.deal.add","params":{"fields":{"TITLE":"X"}}},
             {"key":"note","method":"crm.timeline.comment.add",
              "params":{"fields":{"ENTITY_ID":"$result[make]","ENTITY_TYPE":"deal","COMMENT":"hi"}}}]
        halt: stop on first error if true.

    Returns:
        JSON string: {"result": {key: <payload>}, "result_error": {key: err},
        "result_next": {...}, "result_total": {...}}.

    Write safety: with BITRIX_READ_ONLY=1 the whole batch is refused if any
    command is a write method.
    """
    try:
        if not commands:
            raise BitrixError("commands must be a non-empty list.", code="BAD_INPUT", method="batch")
        mapping: dict[str, tuple[str, dict | None]] = {}
        blocked: list[str] = []
        for i, c in enumerate(commands):
            method = (c or {}).get("method")
            if not method:
                raise BitrixError(f"commands[{i}] is missing 'method'.", code="BAD_INPUT", method="batch")
            key = c.get("key") or f"cmd{i}"
            mapping[key] = (method, c.get("params"))
            if is_write_method(method):
                blocked.append(method)
        # Enforce read-only guard across all sub-commands.
        from ..config import config as _cfg
        if _cfg.read_only and blocked:
            raise BitrixError(
                "Server is in read-only mode (BITRIX_READ_ONLY=1); batch contains "
                f"write methods: {sorted(set(blocked))}.",
                code="B24_READONLY", method="batch",
            )
        client = get_client(ctx, webhook_url, personal_webhook)
        result = await client.batch(mapping, halt=halt)
        return ok(result)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_test_connection", annotations=READ)
async def b24_test_connection(
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Verify the webhook works and report the acting user and portal.

    Calls ``profile`` (identity + portal) and reports the resolved webhook. Use
    this first when diagnosing auth/permission problems.

    Returns:
        JSON: {"ok": true, "portal": "...", "user": {"ID","NAME","LAST_NAME","ADMIN"...}}
        or an error envelope with the Bitrix code.
    """
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        profile = await client.call_result("profile")
        return ok({
            "ok": True,
            "webhook": client.webhook,
            "profile": profile,
        })
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_list_methods", annotations=READ)
async def b24_list_methods(
    full: Annotated[bool, Field(default=False, description="If true, list every available method; otherwise return scope summary only.")] = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Discover which REST methods/scopes this webhook can access.

    Calls ``scope`` (granted scopes) and, when ``full=true``, ``methods`` (all
    method names). Handy for figuring out what the portal exposes before writing
    a b24_call.

    Returns:
        JSON: {"scopes": [...], "methods": [...optional...]}.
    """
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        scopes = await client.call_result("scope")
        out: dict[str, Any] = {"scopes": scopes}
        if full:
            out["methods"] = await client.call_result("methods")
        return ok(out)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
