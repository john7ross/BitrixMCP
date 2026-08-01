"""Business processes — bizproc.* (templates and workflow launching)."""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.mcpserver import Context

from ..runtime import (
    READ,
    WRITE,
    PersonalWebhook,
    WebhookUrl,
    run_call,
)
from ..server import mcp


@mcp.tool(name="b24_bizproc_template_list", annotations=READ)
async def b24_bizproc_template_list(
    document_type: Annotated[Optional[list], Field(default=None, description="Filter to a document type, e.g. ['crm','CCrmDocumentDeal','DEAL'] or ['lists','BizprocDocument','iblock_44'].")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List workflow templates (bizproc.workflow.template.list).

    Returns templates (ID, NAME, DOCUMENT_TYPE, AUTO_EXECUTE). Use a template ID
    plus a concrete document id with b24_bizproc_start.
    """
    params: dict = {}
    if document_type:
        params["select"] = ["ID", "MODULE_ID", "ENTITY", "DOCUMENT_TYPE", "NAME", "AUTO_EXECUTE"]
        params["filter"] = {"DOCUMENT_TYPE": document_type}
    return await run_call(ctx, "bizproc.workflow.template.list", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_bizproc_start", annotations=WRITE)
async def b24_bizproc_start(
    template_id: Annotated[int, Field(description="Workflow template id to launch.")],
    document_id: Annotated[list, Field(description="Target document as [MODULE, ENTITY, ID], e.g. ['crm','CCrmDocumentDeal','DEAL_42'] or ['lists','BizprocDocument','123'].")],
    parameters: Annotated[Optional[dict], Field(default=None, description="Optional template parameters {code: value}.")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Launch a business process on a document (bizproc.workflow.start).

    Potentially impactful — a workflow can move stages, send mail, create tasks.
    Returns the running workflow id.
    """
    params: dict = {"TEMPLATE_ID": template_id, "DOCUMENT_ID": document_id}
    if parameters:
        params["PARAMETERS"] = parameters
    return await run_call(ctx, "bizproc.workflow.start", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
