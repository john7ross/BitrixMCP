"""Calendar tools — calendar.event.* / calendar.section.*.

Fixes the old ``calendar_list`` trap: ``calendar.event.get`` requires an explicit
``ownerId``; without it the portal returns zero events even though the docs imply
a default. Here, ``owner_id`` defaults to the acting user, resolved automatically.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.mcpserver import Context

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



# Each event carries ~60 fields, many of them long (DESCRIPTION, ATTENDEE_LIST,
# EXDATE, the DAV/Exchange sync columns). One busy month for one user measured
# 232 events x ~2.6 KB = ~1 MB in a single response - enough to blow an agent's
# context in one call. Bitrix offers no server-side projection for
# calendar.event.get, so the trimming is ours, applied after the fetch.
EVENT_DEFAULT_FIELDS = [
    "ID", "NAME", "DATE_FROM", "DATE_TO", "SECTION_ID", "CAL_TYPE", "OWNER_ID",
    "CREATED_BY", "IS_MEETING", "MEETING_STATUS", "LOCATION", "ACCESSIBILITY", "IMPORTANCE",
]


def _project(event: dict, fields: list[str] | None) -> dict:
    if not isinstance(event, dict):
        return event
    if fields and "*" in fields:
        return event
    keep = fields or EVENT_DEFAULT_FIELDS
    return {k: event.get(k) for k in keep if k in event}


@mcp.tool(name="b24_calendar_event_list", annotations=READ)
async def b24_calendar_event_list(
    date_from: Annotated[str, Field(description="Range start, 'YYYY-MM-DD' (or 'YYYY-MM-DD HH:MM:SS').")],
    date_to: Annotated[str, Field(description="Range end, 'YYYY-MM-DD'.")],
    owner_id: OwnerId = None,
    owner_type: OwnerType = "user",
    select: Annotated[Optional[list], Field(default=None, description="Fields to keep per event. Omit for a compact default set; pass ['*'] for every field (large); or name fields explicitly, e.g. ['ID','NAME','DATE_FROM','DESCRIPTION'].")] = None,
    limit: Annotated[int, Field(default=50, ge=1, le=500, description="Max events to return. The full match count is always reported as 'total'.")] = 50,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List calendar events in a date range (calendar.event.get).

    owner_id defaults to the acting user.

    Bitrix returns every field of every event in the range and offers no
    server-side projection or paging here, so this trims client-side: by
    default each event keeps a compact set of fields and at most ``limit``
    events come back. ``total`` always reports how many matched, and
    ``truncated`` says whether you are seeing all of them — narrow the date
    range or raise ``limit`` rather than assuming the tail is empty. Ask for
    ``select=['*']`` only when you genuinely need the full ~60 fields; a busy
    month measured ~1 MB that way.

    Returns:
        JSON: {"events": [...], "count": <returned>, "total": <matched>,
        "truncated": <bool>, "ownerId": <int>, "type": "user"|"group",
        "fields": "default"|"all"|<list>}.
    """
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        owner = await _resolve_owner(client, owner_id, owner_type)
        events = await client.call_result("calendar.event.get", {
            "type": owner_type, "ownerId": owner, "from": date_from, "to": date_to,
        })
        events = events or []
        total = len(events)
        page = [_project(e, select) for e in events[:limit]]
        return ok({
            "count": len(page),
            "total": total,
            "truncated": total > len(page),
            "ownerId": owner,
            "type": owner_type,
            "fields": "all" if (select and "*" in select) else (select or "default"),
            "events": page,
        })
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
