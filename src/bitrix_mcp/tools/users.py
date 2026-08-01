"""Users and company structure — user.get / user.current / department.get.

Uses the raw ``user.get`` with a real filter and pagination, avoiding the old
``users_list`` failure mode that ignored the filter and timed out trying to dump
every user on the portal.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.mcpserver import Context

from ..config import config
from ..runtime import (
    READ,
    Filter,
    Order,
    PersonalWebhook,
    Start,
    FetchAll,
    WebhookUrl,
    err,
    get_client,
    ok,
    run_call,
    run_list,
)
from ..server import mcp


@mcp.tool(name="b24_user_get", annotations=READ)
async def b24_user_get(
    id: Annotated[Optional[int], Field(default=None, description="Shortcut to fetch one user by id (merged into the filter as ID).")] = None,
    filter: Filter = None,
    order: Order = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Find users (user.get) with a real filter and pagination.

    Filter keys include ID, ACTIVE ('Y'/'N'), NAME, LAST_NAME, EMAIL,
    UF_DEPARTMENT, WORK_POSITION, and '%'-prefixed substring variants
    (e.g. {'%LAST_NAME': 'Ivan'}). Always filter to keep the result bounded.

    Returns:
        JSON pagination envelope {items, count, total, next, has_more, truncated}.
    """
    flt = dict(filter or {})
    if id is not None:
        flt["ID"] = id
    params: dict = {}
    if flt:
        params["filter"] = flt
    if order:
        params["order"] = order
    return await run_list(
        ctx, "user.get", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_user_current", annotations=READ)
async def b24_user_current(
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Return the acting user's profile (user.current): id, name, admin flag, etc."""
    return await run_call(ctx, "user.current", None,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_user_search", annotations=READ)
async def b24_user_search(
    query: Annotated[str, Field(description="Free-text search across name/last name/email/position (FIND).", min_length=1)],
    only_active: Annotated[bool, Field(default=True, description="Restrict to active users.")] = True,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Free-text search for users (user.search).

    Better than user.get when you only have a partial name. Returns a pagination
    envelope of user objects.
    """
    params: dict = {"FIND": query}
    if only_active:
        params["ACTIVE"] = True
    return await run_list(
        ctx, "user.search", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_department_get", annotations=READ)
async def b24_department_get(
    id: Annotated[Optional[int], Field(default=None, description="Fetch one department by id.")] = None,
    filter: Filter = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List/describe company departments (department.get).

    Bitrix's department.get has **no server-side filter** — it silently
    ignores a `filter` param and returns the entire department list
    regardless of what's in it (confirmed against a live portal). An `ID`
    key (int or list) is sent the way Bitrix actually supports it (a
    top-level `ID`); any other key (NAME, %NAME substring, PARENT, UF_HEAD,
    ...) is matched client-side after fetching the full tree, so filtering
    here genuinely works instead of quietly dumping everything.
    """
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        flt = dict(filter or {})
        if id is not None:
            flt["ID"] = id
        id_filter = flt.pop("ID", None)

        if id_filter is not None and not flt:
            result = await client.call_result("department.get", {"ID": id_filter})
            items = result if isinstance(result, list) else ([result] if result else [])
            return ok({
                "items": items, "count": len(items), "total": len(items),
                "start": 0, "next": None, "has_more": False, "truncated": False,
            })

        env = await client.call_list("department.get", None, fetch_all=True, max_pages=config.max_pages)
        items = env["items"]

        if id_filter is not None:
            wanted = {int(v) for v in (id_filter if isinstance(id_filter, (list, tuple, set)) else [id_filter])}
            items = [d for d in items if int(d.get("ID", -1)) in wanted]
        for key, val in flt.items():
            substring = key.startswith("%")
            field = key[1:] if substring else key
            needle = str(val).lower()
            if substring:
                items = [d for d in items if needle in str(d.get(field, "")).lower()]
            else:
                items = [d for d in items if str(d.get(field, "")) == str(val)]

        total = len(items)
        page = items if fetch_all else items[start:start + 50]
        has_more = (not fetch_all) and (start + 50 < total)
        return ok({
            "items": page, "count": len(page), "total": total,
            "start": start, "next": (start + 50) if has_more else None,
            "has_more": has_more, "truncated": env.get("truncated", False),
        })
    except Exception as exc:  # noqa: BLE001
        return err(exc)
