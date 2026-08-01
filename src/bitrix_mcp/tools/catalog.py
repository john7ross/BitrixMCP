"""Catalog / products — catalog.product.* and crm.product.*.

Two product surfaces exist on a portal: the modern Catalog (catalog.product.*)
and the classic CRM product list (crm.product.*). Both are wrapped.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from mcp.server.mcpserver import Context

from ..runtime import (
    READ,
    WRITE,
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


@mcp.tool(name="b24_catalog_product_list", annotations=READ)
async def b24_catalog_product_list(
    filter: Filter = None,
    select: Select = None,
    order: Order = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List catalog products (catalog.product.list) with pagination.

    Common filter keys: iblockId (catalog id), id, name. Returns a pagination
    envelope of product objects.
    """
    params: dict = {}
    if filter:
        params["filter"] = filter
    if select:
        params["select"] = select
    if order:
        params["order"] = order
    return await run_list(
        ctx, "catalog.product.list", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_catalog_product_get", annotations=READ)
async def b24_catalog_product_get(
    id: Annotated[int, Field(description="Catalog product id.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Fetch one catalog product (catalog.product.get). Returns {"product": {...}}."""
    return await run_call(ctx, "catalog.product.get", {"id": id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_catalog_product_add", annotations=WRITE)
async def b24_catalog_product_add(
    fields: Annotated[dict, Field(description="Product fields, e.g. {'iblockId':14,'name':'Widget','field...':...}.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Create a catalog product (catalog.product.add). Returns {"product": {...}}."""
    return await run_call(ctx, "catalog.product.add", {"fields": fields},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_catalog_product_update", annotations=WRITE)
async def b24_catalog_product_update(
    id: Annotated[int, Field(description="Catalog product id to update.")],
    fields: Annotated[dict, Field(description="Fields to change.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Update a catalog product (catalog.product.update)."""
    return await run_call(ctx, "catalog.product.update", {"id": id, "fields": fields},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_catalog_list", annotations=READ)
async def b24_catalog_list(
    filter: Filter = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List catalogs (catalog.catalog.list): the commercial catalogs / iblocks."""
    params: dict = {}
    if filter:
        params["filter"] = filter
    return await run_list(
        ctx, "catalog.catalog.list", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_catalog_section_list", annotations=READ)
async def b24_catalog_section_list(
    filter: Filter = None,
    order: Order = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List catalog sections/categories (catalog.section.list).

    Common filter keys: iblockId, id, name, sectionId (parent).
    """
    params: dict = {}
    if filter:
        params["filter"] = filter
    if order:
        params["order"] = order
    return await run_list(
        ctx, "catalog.section.list", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_crm_product_list", annotations=READ)
async def b24_crm_product_list(
    filter: Filter = None,
    select: Select = None,
    order: Order = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List classic CRM catalog products (crm.product.list) with pagination.

    Common filter keys: NAME, SECTION_ID, ACTIVE ('Y'/'N'), CATALOG_ID.
    """
    params: dict = {}
    if filter:
        params["filter"] = filter
    if select:
        params["select"] = select
    if order:
        params["order"] = order
    return await run_list(
        ctx, "crm.product.list", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )
