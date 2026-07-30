"""Event-feed tools: read the local event store, not the portal.

Everything here queries SQLite that the receiver (outgoing webhook) or the pull
channel filled in. No REST call is made, so these tools work regardless of
webhook permissions - and they answer questions REST cannot, such as "what
happened to this task last week", because the portal exposes no change history
for most entities.

The store keeps whole payloads on purpose, so history answers carry the context
that was present at the time rather than ids to re-resolve.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from pydantic import Field

from ..config import config
from ..events.feeds import resolve, rows_of
from ..events.store import EventStore
from ..runtime import READ, WRITE, PersonalWebhook, WebhookUrl, err, get_client, ok
from ..server import mcp

try:
    from mcp.server.fastmcp import Context
except Exception:  # pragma: no cover
    Context = Any  # type: ignore

_RELATIVE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.I)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

_store: EventStore | None = None


def get_store() -> EventStore:
    """Open the store lazily so an unconfigured server never creates a file."""
    global _store
    if _store is None:
        _store = EventStore(config.event_db, retention_days=config.event_retention_days)
    return _store


def parse_when(value: str | float | int | None) -> float | None:
    """Accept a unix time, an ISO timestamp, or a relative age like '7d'/'24h'.

    Returns a unix timestamp, or None when nothing was given. Raises ValueError
    on an unparsable value rather than silently ignoring it - a dropped time
    bound would quietly widen the query instead of narrowing it.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    rel = _RELATIVE.match(text)
    if rel:
        return time.time() - int(rel.group(1)) * _UNIT_SECONDS[rel.group(2).lower()]
    try:
        return float(text)
    except ValueError:
        pass
    iso = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(iso)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _stamp(row: dict) -> dict:
    """Add a human-readable time next to the raw unix one."""
    out = dict(row)
    received = row.get("received_at")
    if received:
        out["received_iso"] = datetime.fromtimestamp(received).isoformat(timespec="seconds")
    return out


@mcp.tool(name="b24_events_poll", annotations=READ)
async def b24_events_poll(
    limit: Annotated[int, Field(default=50, ge=1, le=500, description="Max events to return (1-500).")] = 50,
    event: Annotated[Optional[str], Field(default=None, description="Filter by event name, e.g. 'ONTASKUPDATE' or 'tasks/task_update'. Case-insensitive.")] = None,
    after_id: Annotated[int, Field(default=0, ge=0, description="Return only events with a higher id - use the last id you processed as a cursor.")] = 0,
    include_acked: Annotated[bool, Field(default=False, description="Also return events already marked processed.")] = False,
    ctx: Context | None = None,
) -> str:
    """Read new portal events captured by this server (unprocessed first).

    Events arrive either from the portal's outgoing webhook (documented event
    names like ONTASKUPDATE) or from the Push&Pull channel (names shaped
    'module/command', e.g. 'tasks/task_update'). Both land in the same store.

    Typical loop: poll -> act -> b24_events_ack with the ids you handled.

    Returns:
        JSON: {"events": [{"id","event","source","entity","entity_id","ts",
        "received_at","received_iso","acked","payload"}], "count": <int>,
        "last_id": <int|null>}. Empty list means nothing new.
    """
    try:
        rows = get_store().poll(limit=limit, event=event, after_id=after_id,
                                include_acked=include_acked)
        return ok({
            "events": [_stamp(r) for r in rows],
            "count": len(rows),
            "last_id": rows[-1]["id"] if rows else None,
        })
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_events_ack", annotations=WRITE)
async def b24_events_ack(
    ids: Annotated[list[int], Field(description="Event ids to mark as processed, e.g. [12, 13, 14].")],
    ctx: Context | None = None,
) -> str:
    """Mark events as processed so the next poll does not return them again.

    Acking never deletes anything: acknowledged events stay readable through
    b24_events_history. Re-acking an id is a no-op, so retrying is safe.

    Returns:
        JSON: {"acked": <how many rows changed>, "requested": <int>}.
    """
    try:
        changed = get_store().ack(ids)
        return ok({"acked": changed, "requested": len(ids)})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_events_history", annotations=READ)
async def b24_events_history(
    entity: Annotated[Optional[str], Field(default=None, description="Object kind: 'task', 'chat', 'deal', 'lead', 'contact', 'company', 'item'.")] = None,
    entity_id: Annotated[Optional[str], Field(default=None, description="Id of that object, e.g. '477818'.")] = None,
    event: Annotated[Optional[str], Field(default=None, description="Filter by event name. Case-insensitive.")] = None,
    since: Annotated[Optional[str], Field(default=None, description="Lower time bound: '7d', '24h', '2026-07-01', an ISO timestamp, or a unix time.")] = None,
    until: Annotated[Optional[str], Field(default=None, description="Upper time bound, same formats as 'since'.")] = None,
    limit: Annotated[int, Field(default=100, ge=1, le=500, description="Max events to return (1-500).")] = 100,
    oldest_first: Annotated[bool, Field(default=False, description="Return chronologically instead of newest-first.")] = False,
    ctx: Context | None = None,
) -> str:
    """Search the archive of captured events - "what happened to X, and when".

    Answers questions the REST API cannot: Bitrix exposes no change history for
    most entities, and for Scrum boards it does not expose the task-to-column
    mapping at all. Whatever this server observed is kept here with its full
    payload, including before/after values when the portal sent them.

    Ignores ack state: processed events remain part of the history.

    Args:
        entity/entity_id: narrow to one object, e.g. entity='task',
            entity_id='477818'.
        since/until: time window; '7d' means the last seven days.

    Returns:
        JSON: {"events": [...], "count": <int>, "window": {"since","until"}}.
        An empty list means nothing was captured - which is not proof that
        nothing happened, only that this server was not listening at the time.
    """
    try:
        window_since = parse_when(since)
        window_until = parse_when(until)
        rows = get_store().history(
            entity=entity, entity_id=entity_id, event=event,
            since=window_since, until=window_until,
            limit=limit, newest_first=not oldest_first,
        )
        return ok({
            "events": [_stamp(r) for r in rows],
            "count": len(rows),
            "window": {"since": window_since, "until": window_until},
        })
    except ValueError as exc:
        return err(ValueError(
            f"Could not parse a time bound: {exc}. Use '7d', '24h', "
            f"'2026-07-01', an ISO timestamp, or a unix time."
        ))
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_changes_since", annotations=READ)
async def b24_changes_since(
    feed: Annotated[str, Field(default="tasks", description="What to watch: 'tasks', 'deals', 'leads', 'contacts', 'companies'.")] = "tasks",
    since: Annotated[Optional[str], Field(default=None, description="Lower bound: '7d', '24h', an ISO timestamp, or omit to continue from the stored cursor.")] = None,
    entity_type_id: Annotated[Optional[int], Field(default=None, description="Smart-process type id (1030+). Overrides 'feed'.")] = None,
    limit: Annotated[int, Field(default=50, ge=1, le=200, description="Max rows per call (Bitrix pages by 50).")] = 50,
    advance_cursor: Annotated[bool, Field(default=True, description="Store the newest timestamp so the next call returns only newer rows.")] = True,
    archive: Annotated[bool, Field(default=True, description="Also record the rows in the local event archive, readable via b24_events_history.")] = True,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List what changed since last time - the event path that works anywhere.

    Uses only outbound requests, so it needs no public URL, no firewall rule and
    no push server: this is the fallback when neither the outgoing-webhook
    receiver nor the Push&Pull channel is available.

    A cursor per feed is kept locally, so calling this repeatedly with no
    arguments walks forward through changes. The first call without 'since'
    starts from 24 hours ago rather than the beginning of time.

    Limits worth knowing: this reports the state at poll time, not every
    intermediate change, and it cannot see deletions - a deleted record simply
    stops appearing. For per-change fidelity use the pull channel or the
    outgoing webhook.

    Returns:
        JSON: {"feed", "method", "since", "count", "items": [...],
        "cursor": {"previous","current","advanced"}, "has_more": <bool>,
        "verified": <bool>}. `verified` false means this feed's response shape
        follows the documentation but has not been confirmed against real data.
    """
    try:
        spec = resolve(feed, entity_type_id)
        store = get_store()
        feed_key = f"{feed}:{entity_type_id}" if entity_type_id is not None else spec.entity

        previous = store.cursor_get(feed_key)
        bound = parse_when(since) if since else None
        if bound is not None:
            since_text = datetime.fromtimestamp(bound).isoformat(timespec="seconds")
        elif previous:
            since_text = previous
        else:
            since_text = datetime.fromtimestamp(time.time() - 86400).isoformat(timespec="seconds")

        params: dict[str, Any] = {
            "filter": {f">={spec.date_filter_field}": since_text},
            "order": {spec.date_filter_field: "ASC"},
            "start": 0,
        }
        if spec.select:
            params["select"] = list(spec.select)
        params.update(spec.extra_params)

        client = get_client(ctx, webhook_url, personal_webhook)
        envelope = await client.call(spec.method, params)
        rows = rows_of(spec, envelope.get("result"))[:limit]

        newest = None
        for row in rows:
            stamp = row.get(spec.date_key)
            if stamp and (newest is None or str(stamp) > str(newest)):
                newest = stamp
            if archive:
                store.put(f"poll/{spec.entity}", None,
                          {"params": {"entity": spec.entity, "row": row}},
                          source="poll",
                          entity=spec.entity, entity_id=row.get(spec.id_key),
                          dedup_on=f"{spec.entity}:{row.get(spec.id_key)}:{stamp}")

        advanced = False
        if advance_cursor and newest:
            store.cursor_set(feed_key, str(newest))
            advanced = True

        return ok({
            "feed": feed_key,
            "method": spec.method,
            "since": since_text,
            "count": len(rows),
            "items": rows,
            "cursor": {"previous": previous, "current": newest, "advanced": advanced},
            "has_more": bool(envelope.get("next")),
            "total_matching": envelope.get("total"),
            "verified": spec.verified,
            "note": spec.note,
        })
    except KeyError as exc:
        return err(ValueError(str(exc).strip("'")))
    except ValueError as exc:
        return err(ValueError(
            f"Could not parse 'since': {exc}. Use '7d', '24h', an ISO timestamp, "
            f"or omit it to continue from the stored cursor."
        ))
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_events_stats", annotations=READ)
async def b24_events_stats(
    ctx: Context | None = None,
) -> str:
    """Report what the event feed has captured and whether it is configured.

    Use this first when events are not arriving: it distinguishes "nothing
    happened" from "nothing is listening".

    Returns:
        JSON: {"store": {"path","exists","size_bytes"}, "counts": {...},
        "receiver": {"configured","path"}, "cursors": {...}}.
        `receiver.configured` false means the outgoing-webhook endpoint refuses
        every delivery until BITRIX_EVENT_TOKEN is set.
    """
    try:
        store = get_store()
        path = store.path
        exists = os.path.exists(path)
        return ok({
            "store": {
                "path": path,
                "exists": exists,
                "size_bytes": os.path.getsize(path) if exists else 0,
                "retention_days": store.retention_days,
            },
            "counts": store.stats(),
            "receiver": {
                "configured": bool(config.event_token),
                "path": config.event_path,
            },
            "cursors": store.cursor_list(),
        })
    except Exception as exc:  # noqa: BLE001
        return err(exc)
