"""CRM tools — leads, deals, contacts, companies, quotes, and SPA items.

Works two ways:
* classic entity name (``entity="deal"``) -> ``crm.deal.*`` methods;
* ``entity_type_id`` set (e.g. 1030 for a Smart Process) -> modern
  ``crm.item.*`` methods, which cover every CRM object uniformly.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context

from ..client import BitrixError
from ..runtime import (
    READ,
    WRITE,
    DESTRUCTIVE,
    Filter,
    Order,
    PersonalWebhook,
    Select,
    Start,
    FetchAll,
    WebhookUrl,
    err,
    run_call,
    run_list,
)
from ..server import mcp

Entity = Annotated[
    str,
    Field(
        default="deal",
        description="Classic CRM entity: 'lead', 'deal', 'contact', 'company', or 'quote'. Ignored when entity_type_id is set.",
    ),
]
EntityTypeId = Annotated[
    Optional[int],
    Field(
        default=None,
        description="Numeric CRM entity type id for the modern crm.item.* API (e.g. 1=lead, 2=deal, 1030+=Smart Process). Set this to work with SPA items.",
    ),
]

_CLASSIC = {"lead", "deal", "contact", "company", "quote"}


def _check_entity(entity: str, entity_type_id: Optional[int]) -> None:
    if entity_type_id is None and entity not in _CLASSIC:
        raise BitrixError(
            f"Unknown CRM entity '{entity}'. Use one of {sorted(_CLASSIC)}, or set "
            "entity_type_id for the modern crm.item.* API.",
            code="BAD_INPUT",
        )


@mcp.tool(name="b24_crm_list", annotations=READ)
async def b24_crm_list(
    entity: Entity = "deal",
    entity_type_id: EntityTypeId = None,
    filter: Filter = None,
    select: Select = None,
    order: Order = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List CRM records with filtering, sorting, and pagination.

    Args:
        entity: classic entity name (default 'deal').
        entity_type_id: set to use crm.item.list for SPA/modern objects.
        filter/select/order/start/fetch_all: standard list controls.

    Returns:
        JSON pagination envelope {items, count, total, next, has_more, truncated}.
    """
    try:
        _check_entity(entity, entity_type_id)
    except Exception as exc:  # noqa: BLE001
        return err(exc)

    params: dict = {}
    if filter:
        params["filter"] = filter
    if select:
        params["select"] = select
    if order:
        params["order"] = order

    if entity_type_id is not None:
        params["entityTypeId"] = entity_type_id
        method = "crm.item.list"
    else:
        method = f"crm.{entity}.list"
    return await run_list(
        ctx, method, params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_crm_get", annotations=READ)
async def b24_crm_get(
    id: Annotated[int, Field(description="Record id.")],
    entity: Entity = "deal",
    entity_type_id: EntityTypeId = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Fetch a single CRM record by id (all fields).

    Returns the record payload (classic entities return the fields object; the
    modern crm.item API returns {"item": {...}}).
    """
    try:
        _check_entity(entity, entity_type_id)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
    if entity_type_id is not None:
        return await run_call(ctx, "crm.item.get", {"entityTypeId": entity_type_id, "id": id},
                              webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)
    return await run_call(ctx, f"crm.{entity}.get", {"id": id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_crm_fields", annotations=READ)
async def b24_crm_fields(
    entity: Entity = "deal",
    entity_type_id: EntityTypeId = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Describe an entity's fields, including user fields (UF_*).

    Essential before add/update so you use correct field codes and enum ids.
    """
    try:
        _check_entity(entity, entity_type_id)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
    if entity_type_id is not None:
        return await run_call(ctx, "crm.item.fields", {"entityTypeId": entity_type_id},
                              webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)
    return await run_call(ctx, f"crm.{entity}.fields", None,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_crm_add", annotations=WRITE)
async def b24_crm_add(
    fields: Annotated[dict, Field(description="Field values for the new record, e.g. {'TITLE':'New deal','OPPORTUNITY':1000}.")],
    entity: Entity = "deal",
    entity_type_id: EntityTypeId = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Create a CRM record. Returns the new id (classic) or {"item": {...}} (modern).

    Writing requires a webhook with the acting user's permissions — pass
    personal_webhook to create under a specific user.
    """
    try:
        _check_entity(entity, entity_type_id)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
    if entity_type_id is not None:
        return await run_call(ctx, "crm.item.add", {"entityTypeId": entity_type_id, "fields": fields},
                              webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
    return await run_call(ctx, f"crm.{entity}.add", {"fields": fields},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_crm_update", annotations=WRITE)
async def b24_crm_update(
    id: Annotated[int, Field(description="Record id to update.")],
    fields: Annotated[dict, Field(description="Field values to change.")],
    entity: Entity = "deal",
    entity_type_id: EntityTypeId = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Update fields of an existing CRM record. Returns success/the updated item."""
    try:
        _check_entity(entity, entity_type_id)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
    if entity_type_id is not None:
        return await run_call(ctx, "crm.item.update", {"entityTypeId": entity_type_id, "id": id, "fields": fields},
                              webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
    return await run_call(ctx, f"crm.{entity}.update", {"id": id, "fields": fields},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_crm_delete", annotations=DESTRUCTIVE)
async def b24_crm_delete(
    id: Annotated[int, Field(description="Record id to delete.")],
    entity: Entity = "deal",
    entity_type_id: EntityTypeId = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Delete a CRM record. Irreversible — deletes on the portal."""
    try:
        _check_entity(entity, entity_type_id)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
    if entity_type_id is not None:
        return await run_call(ctx, "crm.item.delete", {"entityTypeId": entity_type_id, "id": id},
                              webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
    return await run_call(ctx, f"crm.{entity}.delete", {"id": id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_crm_timeline_comment_add", annotations=WRITE)
async def b24_crm_timeline_comment_add(
    entity_type: Annotated[str, Field(description="Timeline owner type: 'lead','deal','contact','company','quote', or a SPA type.")],
    entity_id: Annotated[int, Field(description="Owner record id.")],
    comment: Annotated[str, Field(description="Comment text (plain text / BB-code).", min_length=1)],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Post a comment to a CRM record's timeline. Returns the new comment id."""
    fields = {"ENTITY_ID": entity_id, "ENTITY_TYPE": entity_type, "COMMENT": comment}
    return await run_call(ctx, "crm.timeline.comment.add", {"fields": fields},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_crm_category_list", annotations=READ)
async def b24_crm_category_list(
    entity_type_id: Annotated[int, Field(description="CRM entity type id whose pipelines/categories you want (2=deal, 1030+=SPA).")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List pipelines/categories of a CRM entity type (crm.category.list).

    Returns categories (id, name, sort). A category is a pipeline; its stages
    come from b24_crm_status_list or crm.status.list with the right ENTITY_ID.
    """
    return await run_call(ctx, "crm.category.list", {"entityTypeId": entity_type_id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_crm_status_list", annotations=READ)
async def b24_crm_status_list(
    entity_id: Annotated[Optional[str], Field(default=None, description="Dictionary to fetch, e.g. 'DEAL_STAGE', 'DEAL_STAGE_7' (pipeline 7), 'STATUS' (lead statuses), 'SOURCE', 'CONTACT_TYPE'. Omit for all dictionaries.")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List CRM status dictionary entries — stages, sources, types (crm.status.list).

    Returns a pagination envelope of status entries (STATUS_ID, NAME, ENTITY_ID,
    SORT). This is how you resolve a stage code like 'C7:NEW' to a human name.
    """
    params: dict = {}
    if entity_id:
        params["filter"] = {"ENTITY_ID": entity_id}
    return await run_list(ctx, "crm.status.list", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook)


@mcp.tool(name="b24_crm_activity_list", annotations=READ)
async def b24_crm_activity_list(
    filter: Filter = None,
    select: Select = None,
    order: Order = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List CRM activities — calls, meetings, emails, tasks (crm.activity.list).

    Common filter keys: OWNER_TYPE_ID (1 lead,2 deal,3 contact,4 company),
    OWNER_ID, TYPE_ID (1 meeting,2 call,3 task,4 email), COMPLETED ('Y'/'N'),
    RESPONSIBLE_ID, '>=CREATED'. Returns a pagination envelope.
    """
    params: dict = {}
    if filter:
        params["filter"] = filter
    if select:
        params["select"] = select
    if order:
        params["order"] = order
    return await run_list(
        ctx, "crm.activity.list", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_crm_activity_add", annotations=WRITE)
async def b24_crm_activity_add(
    fields: Annotated[dict, Field(description="Activity fields. Minimum: OWNER_TYPE_ID, OWNER_ID, TYPE_ID, SUBJECT, COMMUNICATIONS. See crm.activity.fields for the full schema.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Create a CRM activity (crm.activity.add). Returns the new activity id.

    Activities are complex; call b24_call('crm.activity.fields') first to see the
    required shape (especially COMMUNICATIONS for calls/emails).
    """
    return await run_call(ctx, "crm.activity.add", {"fields": fields},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_crm_productrows_get", annotations=READ)
async def b24_crm_productrows_get(
    owner_type: Annotated[str, Field(description="Owner short type: 'D' (deal), 'L' (lead), 'Q' (quote).")],
    owner_id: Annotated[int, Field(description="Owner record id.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List the product rows of a deal/lead/quote (crm.item.productrow.list)."""
    params = {"filter": {"=ownerType": owner_type, "=ownerId": owner_id}}
    return await run_list(ctx, "crm.item.productrow.list", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook)


@mcp.tool(name="b24_crm_productrows_set", annotations=WRITE)
async def b24_crm_productrows_set(
    owner_type: Annotated[str, Field(description="Owner short type: 'D' (deal), 'L' (lead), 'Q' (quote).")],
    owner_id: Annotated[int, Field(description="Owner record id.")],
    rows: Annotated[list, Field(description="Product rows, e.g. [{'productName':'X','price':100,'quantity':2},{'productId':55,'price':10,'quantity':1}].")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Replace the product rows of a deal/lead/quote (crm.item.productrow.set)."""
    params = {"ownerType": owner_type, "ownerId": owner_id, "productRows": rows}
    return await run_call(ctx, "crm.item.productrow.set", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_crm_currency_list", annotations=READ)
async def b24_crm_currency_list(
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List portal currencies (crm.currency.list): CURRENCY, base flag, format, rate."""
    return await run_list(ctx, "crm.currency.list", None,
                          webhook_url=webhook_url, personal_webhook=personal_webhook)


@mcp.tool(name="b24_crm_requisite_list", annotations=READ)
async def b24_crm_requisite_list(
    filter: Filter = None,
    select: Select = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List requisites — legal/bank details of contacts & companies (crm.requisite.list).

    Common filter keys: ENTITY_TYPE_ID (3 contact, 4 company), ENTITY_ID, PRESET_ID.
    """
    params: dict = {}
    if filter:
        params["filter"] = filter
    if select:
        params["select"] = select
    return await run_list(
        ctx, "crm.requisite.list", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_crm_timeline_comment_list", annotations=READ)
async def b24_crm_timeline_comment_list(
    entity_type: Annotated[str, Field(description="Timeline owner type: 'deal','lead','contact','company','quote'.")],
    entity_id: Annotated[int, Field(description="Owner record id.")],
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List timeline comments of a CRM record (crm.timeline.comment.list)."""
    params = {"filter": {"ENTITY_ID": entity_id, "ENTITY_TYPE": entity_type}}
    return await run_list(
        ctx, "crm.timeline.comment.list", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_crm_activity_delete", annotations=DESTRUCTIVE)
async def b24_crm_activity_delete(
    id: Annotated[int, Field(description="Activity id to delete.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Delete a CRM activity (crm.activity.delete). Irreversible."""
    return await run_call(ctx, "crm.activity.delete", {"id": id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_crm_deal_contacts_get", annotations=READ)
async def b24_crm_deal_contacts_get(
    deal_id: Annotated[int, Field(description="Deal id.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List contacts linked to a deal (crm.deal.contact.items.get)."""
    return await run_call(ctx, "crm.deal.contact.items.get", {"id": deal_id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_crm_deal_contacts_set", annotations=WRITE)
async def b24_crm_deal_contacts_set(
    deal_id: Annotated[int, Field(description="Deal id.")],
    contact_ids: Annotated[list, Field(description="Contact ids to link, e.g. [11, 12]. Replaces the current set.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Set the contacts linked to a deal (crm.deal.contact.items.set)."""
    items = [{"CONTACT_ID": cid} for cid in contact_ids]
    return await run_call(ctx, "crm.deal.contact.items.set", {"id": deal_id, "items": items},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
