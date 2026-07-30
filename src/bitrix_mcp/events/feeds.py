"""Feed definitions for the polling fallback.

Polling is the event path that works everywhere: outbound requests only, no
inbound URL, no firewall rule, no push server. It is also the weakest one -
it sees the state at poll time, not every intermediate change, and it cannot
see deletions at all, because a deleted record simply stops being returned.

Each feed pins down the four things that are easy to get wrong per entity:
the list method, the date field to filter on, where the rows live in the
response, and what the date field is called *coming back*.

That last point is a real Bitrix trap, verified live on tasks.task.list:
the request takes `CHANGED_DATE` in upper case, and the response returns
`changedDate` in camelCase. Guessing either way silently yields a cursor that
never advances - the poll would re-fetch the same window forever.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Feed:
    method: str
    date_filter_field: str      # what the filter expects (often UPPER_CASE)
    result_key: str | None      # where rows live in `result`; None = result is the list
    id_key: str                 # id field as it comes BACK
    date_key: str               # date field as it comes BACK
    entity: str                 # for the history archive
    select: list[str] = field(default_factory=list)
    extra_params: dict[str, Any] = field(default_factory=dict)
    verified: bool = False      # confirmed against a live portal with real data
    note: str = ""


FEEDS: dict[str, Feed] = {
    "tasks": Feed(
        method="tasks.task.list",
        date_filter_field="CHANGED_DATE",
        result_key="tasks",
        id_key="id",
        date_key="changedDate",
        entity="task",
        select=["ID", "TITLE", "CHANGED_DATE", "STATUS", "RESPONSIBLE_ID", "GROUP_ID"],
        verified=True,
        note="Verified live: 300 tasks changed in one day, ascending, paginates by 50.",
    ),
    "deals": Feed(
        method="crm.item.list",
        date_filter_field="updatedTime",
        result_key="items",
        id_key="id",
        date_key="updatedTime",
        entity="deal",
        extra_params={"entityTypeId": 2},
        select=["id", "title", "updatedTime", "stageId", "assignedById"],
        note="Documented shape; not yet confirmed against a portal with deals.",
    ),
    "leads": Feed(
        method="crm.item.list",
        date_filter_field="updatedTime",
        result_key="items",
        id_key="id",
        date_key="updatedTime",
        entity="lead",
        extra_params={"entityTypeId": 1},
        select=["id", "title", "updatedTime", "statusId", "assignedById"],
        note="Documented shape; not yet confirmed against a portal with leads.",
    ),
    "contacts": Feed(
        method="crm.item.list",
        date_filter_field="updatedTime",
        result_key="items",
        id_key="id",
        date_key="updatedTime",
        entity="contact",
        extra_params={"entityTypeId": 3},
        select=["id", "title", "updatedTime", "assignedById"],
        note="Documented shape; not yet confirmed against a portal with contacts.",
    ),
    "companies": Feed(
        method="crm.item.list",
        date_filter_field="updatedTime",
        result_key="items",
        id_key="id",
        date_key="updatedTime",
        entity="company",
        extra_params={"entityTypeId": 4},
        select=["id", "title", "updatedTime", "assignedById"],
        note="Documented shape; not yet confirmed against a portal with companies.",
    ),
}

# Smart processes share crm.item.list but need the caller's entityTypeId.
SMART_PROCESS = Feed(
    method="crm.item.list",
    date_filter_field="updatedTime",
    result_key="items",
    id_key="id",
    date_key="updatedTime",
    entity="item",
    select=["id", "title", "updatedTime", "stageId", "assignedById"],
    note="Smart process; pass entity_type_id (1030+ on most portals).",
)


def resolve(name: str, entity_type_id: int | None = None) -> Feed:
    """Pick a feed by name, or build a smart-process feed from its type id."""
    if entity_type_id is not None:
        return Feed(
            method=SMART_PROCESS.method,
            date_filter_field=SMART_PROCESS.date_filter_field,
            result_key=SMART_PROCESS.result_key,
            id_key=SMART_PROCESS.id_key,
            date_key=SMART_PROCESS.date_key,
            entity=f"item{entity_type_id}",
            select=list(SMART_PROCESS.select),
            extra_params={"entityTypeId": int(entity_type_id)},
            note=SMART_PROCESS.note,
        )
    key = (name or "").strip().lower()
    if key not in FEEDS:
        raise KeyError(
            f"Unknown feed '{name}'. Available: {', '.join(sorted(FEEDS))}. "
            f"For a smart process pass entity_type_id instead."
        )
    return FEEDS[key]


def rows_of(feed: Feed, result: Any) -> list[dict]:
    """Pull the row list out of a response, tolerating both shapes Bitrix uses."""
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if isinstance(result, dict):
        if feed.result_key and isinstance(result.get(feed.result_key), list):
            return [r for r in result[feed.result_key] if isinstance(r, dict)]
        for value in result.values():
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []
