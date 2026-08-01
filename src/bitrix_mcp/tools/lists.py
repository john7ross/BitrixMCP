"""Universal Lists (Списки / Универсальные списки) — lists.* and lists.element.*.

Lists power many portal processes (registries, catalogs of records, light
workflows). Elements are generic records inside an information block (IBLOCK).
"""

from __future__ import annotations

from typing import Annotated, Optional

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

IblockTypeId = Annotated[
    str,
    Field(description="Information-block type, e.g. 'lists' (portal lists), 'bitrix_processes' (workflows), 'lists_socnet' (group lists)."),
]
IblockId = Annotated[
    int,
    Field(description="Information-block id of the list (numeric IBLOCK_ID)."),
]


@mcp.tool(name="b24_lists_get", annotations=READ)
async def b24_lists_get(
    iblock_type_id: IblockTypeId,
    iblock_code: Annotated[Optional[str], Field(default=None, description="Optional list code to fetch a single list.")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List the available Lists of a given type (lists.get).

    Returns the list definitions (IBLOCK_ID, NAME, ...). Use an IBLOCK_ID with
    b24_lists_element_list to read its records.
    """
    params: dict = {"IBLOCK_TYPE_ID": iblock_type_id}
    if iblock_code:
        params["IBLOCK_CODE"] = iblock_code
    return await run_call(ctx, "lists.get", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_lists_element_list", annotations=READ)
async def b24_lists_element_list(
    iblock_type_id: IblockTypeId,
    iblock_id: IblockId,
    filter: Filter = None,
    select: Select = None,
    order: Order = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List elements (records) of a list (lists.element.get) with pagination.

    Field codes are usually PROPERTY_<n> for custom properties plus NAME, ID,
    CREATED_BY, etc. Use b24_call('lists.field.get', ...) to inspect field codes.
    """
    params: dict = {"IBLOCK_TYPE_ID": iblock_type_id, "IBLOCK_ID": iblock_id}
    if filter:
        params["FILTER"] = filter
    if select:
        params["SELECT"] = select
    if order:
        params["ORDER"] = order
    return await run_list(
        ctx, "lists.element.get", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_lists_element_add", annotations=WRITE)
async def b24_lists_element_add(
    iblock_type_id: IblockTypeId,
    iblock_id: IblockId,
    fields: Annotated[dict, Field(description="Element fields, e.g. {'NAME':'Row 1','PROPERTY_123':'value'}.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Create a list element (lists.element.add). Returns the new element id."""
    params = {"IBLOCK_TYPE_ID": iblock_type_id, "IBLOCK_ID": iblock_id, "FIELDS": fields}
    return await run_call(ctx, "lists.element.add", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_lists_element_update", annotations=WRITE)
async def b24_lists_element_update(
    iblock_type_id: IblockTypeId,
    iblock_id: IblockId,
    element_id: Annotated[int, Field(description="Element id to update.")],
    fields: Annotated[dict, Field(description="Fields to change.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Update a list element (lists.element.update)."""
    params = {
        "IBLOCK_TYPE_ID": iblock_type_id, "IBLOCK_ID": iblock_id,
        "ELEMENT_ID": element_id, "FIELDS": fields,
    }
    return await run_call(ctx, "lists.element.update", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_lists_element_delete", annotations=DESTRUCTIVE)
async def b24_lists_element_delete(
    iblock_type_id: IblockTypeId,
    iblock_id: IblockId,
    element_id: Annotated[int, Field(description="Element id to delete.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Delete a list element (lists.element.delete). Irreversible."""
    params = {"IBLOCK_TYPE_ID": iblock_type_id, "IBLOCK_ID": iblock_id, "ELEMENT_ID": element_id}
    return await run_call(ctx, "lists.element.delete", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
