"""Telephony — call statistics via voximplant.statistic.get.

Note: the previous wrapper's telephony tool hit a non-existent method
(ERROR_METHOD_NOT_FOUND). The correct read method for call history/stats is
voximplant.statistic.get.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from mcp.server.fastmcp import Context

from typing import Optional

from ..runtime import (
    READ,
    Filter,
    Start,
    FetchAll,
    PersonalWebhook,
    WebhookUrl,
    run_list,
)
from ..server import mcp


@mcp.tool(name="b24_telephony_statistics", annotations=READ)
async def b24_telephony_statistics(
    filter: Filter = None,
    sort_field: Annotated[Optional[str], Field(default=None, description="Field to sort by, e.g. 'CALL_START_DATE'.")] = None,
    sort_order: Annotated[str, Field(default="DESC", description="'ASC' or 'DESC' (default).")] = "DESC",
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List call statistics (voximplant.statistic.get) with pagination.

    Common filter keys: '>=CALL_START_DATE', 'PORTAL_USER_ID', 'CALL_TYPE'
    (1 outbound, 2 inbound), 'CALL_FAILED_CODE', 'PHONE_NUMBER'. Returns a
    pagination envelope of call records (CALL_ID, duration, cost, recording URL...).
    """
    params: dict = {}
    if filter:
        params["FILTER"] = filter
    if sort_field:
        params["SORT"] = sort_field
        params["ORDER"] = sort_order
    return await run_list(
        ctx, "voximplant.statistic.get", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )
