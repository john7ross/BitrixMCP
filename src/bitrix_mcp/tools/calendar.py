"""Calendar tools — calendar.event.* / calendar.section.*.

Fixes the old ``calendar_list`` trap: ``calendar.event.get`` requires an explicit
``ownerId``; without it the portal returns zero events even though the docs imply
a default. Here, ``owner_id`` defaults to the acting user, resolved automatically.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context

from ..runtime import (
    READ,
    WRITE,
    DESTRUCTIVE,
    PersonalWebhook,
    WebhookUrl,
    err,
    get_client,
    ok,
    run_call,
)
from ..server import mcp

OwnerType = Annotated[
    str,
    Field(default="user", description="Calendar owner type: 'user' (default) or 'group'."),
]
OwnerId = Annotated[
    Optional[int],
    Field(default=None, description="Calendar owner id. Defaults to the acting user (resolved via user.current) for owner_type='user'."),
]


async def _resolve_owner(client, owner_id: Optional[int], owner_type: str) -> int:
    if owner_id is not None:
        return owner_id
    if owner_type == "user":
        me = await client.call_result("user.current")
        return int(me["ID"])
    raise ValueError("owner_id is required when owner_type != 'user'.")


@mcp.tool(name="b24_calendar_event_list", annotations=READ)
async def b24_calendar_event_list(
    date_from: Annotated[str, Field(description="Range start, 'YYYY-MM-DD' (or 'YYYY-MM-DD HH:MM:SS').")],
    date_to: Annotated[str, Field(description="Range end, 'YYYY-MM-DD'.")],
    owner_id: OwnerId = None,
    owner_type: OwnerType = "user",
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List calendar events in a date range (calendar.event.get).

    owner_id defaults to the acting user. Returns {"count": n, "events": [...]}
    with fields like NAME, DATE_FROM, DATE_TO, ATTENDEE_LIST, etc.
    """
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        owner = await _resolve_owner(client, owner_id, owner_type)
        events = await client.call_result("calendar.event.get", {
            "type": owner_type, "ownerId": owner, "from": date_from, "to": date_to,
        })
        events = events or []
        return ok({"count": len(events), "ownerId": owner, "type": owner_type, "events": events})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_calendar_section_list", annotations=READ)
async def b24_calendar_section_list(
    owner_id: OwnerId = None,
    owner_type: OwnerType = "user",
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List calendars (sections) for an owner (calendar.section.get)."""
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        owner = await _resolve_owner(client, owner_id, owner_type)
        sections = await client.call_result("calendar.section.get", {"type": owner_type, "ownerId": owner})
        return ok({"ownerId": owner, "type": owner_type, "sections": sections})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_calendar_event_add", annotations=WRITE)
async def b24_calendar_event_add(
    name: Annotated[str, Field(description="Event title.", min_length=1)],
    date_from: Annotated[str, Field(description="Start, 'YYYY-MM-DD HH:MM:SS'.")],
    date_to: Annotated[str, Field(description="End, 'YYYY-MM-DD HH:MM:SS'.")],
    section_id: Annotated[Optional[int], Field(default=None, description="Target calendar (section) id. If omitted Bitrix uses the owner's default calendar.")] = None,
    extra: Annotated[Optional[dict], Field(default=None, description="Any additional calendar.event.add fields (e.g. {'description':'...','attendees':[1,2],'location':'...'}).")] = None,
    owner_id: OwnerId = None,
    owner_type: OwnerType = "user",
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Create a calendar event (calendar.event.add). Returns the new event id."""
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        owner = await _resolve_owner(client, owner_id, owner_type)
        params: dict = {"type": owner_type, "ownerId": owner, "name": name, "from": date_from, "to": date_to}
        if section_id is not None:
            params["section"] = section_id
        if extra:
            params.update(extra)
        # Bitrix silently drops `attendees` (200 OK, event created, nobody
        # invited) unless `is_meeting` is also set — never leave that as a
        # quiet no-op when the caller clearly wants a meeting.
        if params.get("attendees") and "is_meeting" not in params:
            params["is_meeting"] = "Y"
        return await run_call(ctx, "calendar.event.add", params,
                              webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_calendar_event_update", annotations=WRITE)
async def b24_calendar_event_update(
    id: Annotated[int, Field(description="Event id to update.")],
    fields: Annotated[dict, Field(description="Fields to change, e.g. {'name':'New title','from':'2026-08-01 10:00:00','to':'2026-08-01 11:00:00','description':'...'}.")],
    owner_id: OwnerId = None,
    owner_type: OwnerType = "user",
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Update a calendar event (calendar.event.update)."""
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        owner = await _resolve_owner(client, owner_id, owner_type)
        params: dict = {"type": owner_type, "ownerId": owner, "id": id}
        params.update(fields)
        # Same silent-drop trap as calendar.event.add: attendees without
        # is_meeting is accepted and ignored, not rejected.
        if params.get("attendees") and "is_meeting" not in params:
            params["is_meeting"] = "Y"
        return await run_call(ctx, "calendar.event.update", params,
                              webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_calendar_event_delete", annotations=DESTRUCTIVE)
async def b24_calendar_event_delete(
    id: Annotated[int, Field(description="Event id to delete.")],
    owner_id: OwnerId = None,
    owner_type: OwnerType = "user",
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Delete a calendar event (calendar.event.delete). Irreversible."""
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        owner = await _resolve_owner(client, owner_id, owner_type)
        return await run_call(ctx, "calendar.event.delete", {"type": owner_type, "ownerId": owner, "id": id},
                              webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
