"""Functional check of the four event tools against a seeded store.

Seeds one real-shaped pull-channel event (a Scrum stage move, captured live
from this portal) plus one outgoing-webhook event, then exercises poll -> ack
-> history -> stats exactly as an agent would.

    python scripts/events_tools_check.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["BITRIX_EVENT_DB"] = os.path.join(tempfile.mkdtemp(), "tools.sqlite3")
os.environ.setdefault("BITRIX_EVENT_TOKEN", "")

from bitrix_mcp.tools import events as T  # noqa: E402

PULL_TASK_MOVE = {
    "module_id": "tasks",
    "command": "task_update",
    "params": {
        "TASK_ID": 477818,
        "USER_ID": 5432,
        "BEFORE": {"STAGE": "Готовы разработке", "GROUP_ID": 372},
        "AFTER": {"STAGE": "Разработка", "GROUP_ID": 372,
                  "STAGE_INFO": {"id": 17952, "title": "Разработка", "sort": 300}},
    },
    "mid": "17489381460000000248912360",
}
WEBHOOK_TASK = {
    "event": "ONTASKUPDATE",
    "data": {"FIELDS": {"ID": "477818"}},
    "auth": {"domain": "resultforyou.ru"},
}


def show(label, raw):
    print(f"\n=== {label} ===")
    print(json.dumps(json.loads(raw), ensure_ascii=False, indent=1)[:900])


async def main() -> None:
    store = T.get_store()
    store.put("tasks/task_update", "1785195801", PULL_TASK_MOVE,
              source="pull", dedup_on=PULL_TASK_MOVE["mid"])
    store.put("ONTASKUPDATE", "1785195900", WEBHOOK_TASK, source="webhook")
    store.put("im/message", "1785195950",
              {"module_id": "im", "command": "message", "params": {"chatId": 95924}},
              source="pull", dedup_on="mid-im-1")

    polled = json.loads(await T.b24_events_poll(limit=10))
    print(f"poll -> {polled['count']} events, last_id={polled['last_id']}")
    for e in polled["events"]:
        print(f"  #{e['id']} {e['event']:<22} source={e['source']:<7} "
              f"{e['entity']}/{e['entity_id']}  {e.get('received_iso')}")

    # Both sources agree on the same task, which is the point of `entity`.
    show("history: task 477818", await T.b24_events_history(entity="task", entity_id="477818"))

    hist = json.loads(await T.b24_events_history(entity="task", entity_id="477818"))
    move = next((e for e in hist["events"] if e["event"] == "tasks/task_update"), None)
    if move:
        p = move["payload"]["params"]
        print(f"\nstage move recovered: {p['BEFORE']['STAGE']!r} -> {p['AFTER']['STAGE']!r} "
              f"(column id {p['AFTER']['STAGE_INFO']['id']})")
    else:
        print("\nFAIL: stage move not found in history")

    acked = json.loads(await T.b24_events_ack(ids=[e["id"] for e in polled["events"]]))
    print(f"\nack -> {acked}")
    after = json.loads(await T.b24_events_poll(limit=10))
    print(f"poll after ack -> {after['count']} pending")
    still = json.loads(await T.b24_events_history(entity="task", entity_id="477818"))
    print(f"history after ack -> {still['count']} (acked events must survive)")

    window = json.loads(await T.b24_events_history(since="7d"))
    print(f"history since 7d -> {window['count']}")
    bad = json.loads(await T.b24_events_history(since="not-a-date"))
    print(f"bad time bound -> error={bad.get('error')} {str(bad.get('message'))[:60]}")

    show("stats", await T.b24_events_stats())


if __name__ == "__main__":
    asyncio.run(main())
