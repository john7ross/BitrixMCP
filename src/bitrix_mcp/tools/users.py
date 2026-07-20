"""Users and company structure — user.get / user.current / department.get.

Uses the raw ``user.get`` with a real filter and pagination, avoiding the old
``users_list`` failure mode that ignored the filter and timed out trying to dump
every user on the portal.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context

from ..runtime import (
    READ,
    Filter,
    Order,
    PersonalWebhook,
    Start,
    FetchAll,
    WebhookUrl,
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

    Filter supports exact NAME match, PARENT, UF_HEAD (head user id), etc.
    Returns a pagination envelope of department objects.
    """
    params: dict = {}
    if id is not None:
        params["ID"] = id
    if filter:
        params["filter"] = filter
    return await run_list(
        ctx, "department.get", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )
