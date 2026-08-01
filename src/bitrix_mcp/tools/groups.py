"""Workgroups / social-network groups — sonet_group.*.

Fixes the old ``groups_list`` bug where the filter was ignored and the whole
portal (146+ groups) was dumped: here the filter is sent in the JSON body and
actually applied by Bitrix.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from mcp.server.mcpserver import Context

from ..runtime import (
    READ,
    WRITE,
    DESTRUCTIVE,
    Filter,
    Order,
    Select,
    Start,
    FetchAll,
    PersonalWebhook,
    WebhookUrl,
    run_call,
    run_list,
)
from ..server import mcp


@mcp.tool(name="b24_group_list", annotations=READ)
async def b24_group_list(
    filter: Filter = None,
    select: Select = None,
    order: Order = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List workgroups/projects (sonet_group.get) with a working filter.

    Filter keys include NAME, '%NAME' (substring), ACTIVE ('Y'/'N'), CLOSED,
    OWNER_ID, SUBJECT_ID. Returns a pagination envelope of group objects
    (ID, NAME, DESCRIPTION, SCRUM_MASTER_ID, ...).
    """
    params: dict = {}
    if filter:
        params["FILTER"] = filter
    if select:
        params["SELECT"] = select
    if order:
        params["ORDER"] = order
    return await run_list(
        ctx, "sonet_group.get", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_group_users", annotations=READ)
async def b24_group_users(
    group_id: Annotated[int, Field(description="Group id whose members to list.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List members of a workgroup (sonet_group.user.get).

    Returns members with USER_ID and ROLE ('A' owner/moderator, 'E' member, ...).
    """
    return await run_call(ctx, "sonet_group.user.get", {"ID": group_id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_group_create", annotations=WRITE)
async def b24_group_create(
    fields: Annotated[dict, Field(description="Group fields. Minimum NAME; common: DESCRIPTION, VISIBLE ('Y'/'N'), OPENED, SUBJECT_ID, INITIATE_PERMS.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Create a workgroup/project (sonet_group.create). Returns the new group id."""
    return await run_call(ctx, "sonet_group.create", fields,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_group_update", annotations=WRITE)
async def b24_group_update(
    group_id: Annotated[int, Field(description="Group id to update.")],
    fields: Annotated[dict, Field(description="Fields to change.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Update a workgroup (sonet_group.update)."""
    params = dict(fields)
    params["GROUP_ID"] = group_id
    return await run_call(ctx, "sonet_group.update", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_group_delete", annotations=DESTRUCTIVE)
async def b24_group_delete(
    group_id: Annotated[int, Field(description="Group id to delete.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Delete a workgroup (sonet_group.delete). Irreversible."""
    return await run_call(ctx, "sonet_group.delete", {"GROUP_ID": group_id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
