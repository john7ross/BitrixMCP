"""Document generator — crm.documentgenerator.* (templates and generation)."""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.mcpserver import Context

from ..runtime import (
    READ,
    WRITE,
    Filter,
    Start,
    FetchAll,
    PersonalWebhook,
    WebhookUrl,
    run_call,
    run_list,
)
from ..server import mcp


@mcp.tool(name="b24_documentgenerator_templates", annotations=READ)
async def b24_documentgenerator_templates(
    filter: Filter = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List document templates (crm.documentgenerator.template.list).

    Filter examples: {'active':'Y'}, {'entityTypeId':2} (deals). Returns a
    pagination envelope of templates (id, name, ...).
    """
    params: dict = {}
    if filter:
        params["filter"] = filter
    return await run_list(
        ctx, "crm.documentgenerator.template.list", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_documentgenerator_add", annotations=WRITE)
async def b24_documentgenerator_add(
    template_id: Annotated[int, Field(description="Template id to render.")],
    entity_type_id: Annotated[int, Field(description="CRM entity type id of the source (2=deal, 1=lead, 3=contact, 4=company, 1030+=SPA).")],
    entity_id: Annotated[int, Field(description="Source record id whose data fills the template.")],
    values: Annotated[Optional[dict], Field(default=None, description="Extra values to override/supply template placeholders.")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Generate a document from a template and CRM record (crm.documentgenerator.document.add).

    Returns the created document (id, downloadUrl, pdfUrl, ...).
    """
    params: dict = {"templateId": template_id, "entityTypeId": entity_type_id, "entityId": entity_id}
    if values:
        params["values"] = values
    return await run_call(ctx, "crm.documentgenerator.document.add", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
