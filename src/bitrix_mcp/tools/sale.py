"""Online-store orders — sale.order.* (read).

Order writes go through the multi-step basket/order API and are best driven via
b24_call once you know the exact shape; these tools cover the common reads.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from mcp.server.mcpserver import Context

from ..runtime import (
    READ,
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


@mcp.tool(name="b24_sale_order_list", annotations=READ)
async def b24_sale_order_list(
    filter: Filter = None,
    select: Select = None,
    order: Order = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List store orders (sale.order.list) with pagination.

    Common filter keys: id, statusId, '>=dateInsert', userId, '>=price'.
    Returns a pagination envelope of order objects.
    """
    params: dict = {}
    if filter:
        params["filter"] = filter
    if select:
        params["select"] = select
    if order:
        params["order"] = order
    return await run_list(
        ctx, "sale.order.list", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_sale_order_get", annotations=READ)
async def b24_sale_order_get(
    id: Annotated[int, Field(description="Order id.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Fetch a single store order (sale.order.get). Returns {"order": {...}}."""
    return await run_call(ctx, "sale.order.get", {"id": id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)
