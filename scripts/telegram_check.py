"""Check Telegram forwarding logic without touching the network.

Filtering is the part that decides whether a user trusts this feature or mutes
it, so it is tested against real captured event shapes - a pull-channel task
move, chat noise, an outgoing-webhook deal event, a polled row.

Delivery itself needs a real bot token and is checked separately with
b24_telegram_test.

    python scripts/telegram_check.py
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

os.environ["BITRIX_EVENT_DB"] = os.path.join(tempfile.mkdtemp(), "tg.sqlite3")
os.environ.pop("BITRIX_TELEGRAM_EVENTS", None)

from bitrix_mcp.events.telegram import (  # noqa: E402
    build_messages, format_event, parse_filter, should_forward,
)
from bitrix_mcp.tools import telegram as TG  # noqa: E402
from bitrix_mcp.tools.events import get_store  # noqa: E402

TASK_MOVE = {
    "id": 1, "event": "tasks/task_update", "source": "pull",
    "entity": "task", "entity_id": "477818", "received_at": 1785195801,
    "payload": {"params": {"TASK_ID": 477818,
                           "BEFORE": {"STAGE": "Готовы разработке"},
                           "AFTER": {"STAGE": "Разработка"}}},
}
CHAT_NOISE = {
    "id": 2, "event": "im/readMessageChatOpponent", "source": "pull",
    "entity": "chat", "entity_id": "14969", "received_at": 1785195802,
    "payload": {"params": {"chatId": 14969}},
}
DEAL_HOOK = {
    "id": 3, "event": "ONCRMDEALUPDATE", "source": "webhook",
    "entity": "deal", "entity_id": "662", "received_at": 1785195803,
    "payload": {"data": {"FIELDS": {"ID": "662"}}},
}
POLLED = {
    "id": 4, "event": "poll/task", "source": "poll",
    "entity": "task", "entity_id": "485747", "received_at": 1785195804,
    "payload": {"params": {"row": {"id": "485747", "title": "Отчет за спринт"}}},
}
ALL = [TASK_MOVE, CHAT_NOISE, DEAL_HOOK, POLLED]


def check(label: str, cond: bool) -> bool:
    print(("PASS " if cond else "FAIL ") + label)
    return cond


async def main() -> None:
    r = []

    print("-- default is silence, not spam --")
    r.append(check("empty filter forwards nothing",
                   not any(should_forward(e, "") for e in ALL)))
    r.append(check("empty filter is not an error", parse_filter("") == ([], [])))

    print("\n-- the user's choices actually take effect --")
    r.append(check("'tasks/*' picks the task move only",
                   [e["id"] for e in ALL if should_forward(e, "tasks/*")] == [1]))
    r.append(check("'ONCRM*' picks the webhook deal event",
                   [e["id"] for e in ALL if should_forward(e, "ONCRM*")] == [3]))
    r.append(check("'entity:task' spans sources (pull + poll)",
                   [e["id"] for e in ALL if should_forward(e, "entity:task")] == [1, 4]))
    r.append(check("'source:poll' selects by origin",
                   [e["id"] for e in ALL if should_forward(e, "source:poll")] == [4]))
    r.append(check("'*' forwards everything",
                   all(should_forward(e, "*") for e in ALL)))
    r.append(check("exclusion wins over inclusion",
                   [e["id"] for e in ALL if should_forward(e, "*,-im/*")] == [1, 3, 4]))
    r.append(check("case does not matter",
                   should_forward(DEAL_HOOK, "oncrmdealupdate")))

    print("\n-- the message a human actually sees --")
    text = format_event(TASK_MOVE, "https://portal.example.ru")
    print("   " + text.replace("\n", "\n   "))
    r.append(check("stage transition spelled out", "→" in text and "Разработка" in text))
    r.append(check("html is escaped",
                   "<script>" not in format_event(
                       {**POLLED, "payload": {"params": {"row": {"title": "<script>x"}}}}, None)))
    r.append(check("batching packs events into few messages",
                   len(build_messages(ALL * 20, None)) < len(ALL * 20)))

    print("\n-- agent-driven configuration --")
    before = json.loads(await TG.b24_telegram_status())
    r.append(check("status reports empty filter by default", before["filter"] == ""))
    r.append(check("status offers presets", "work" in before["presets"]))

    applied = json.loads(await TG.b24_telegram_configure(events="work", chat_id="-1001234567890"))
    print(f"   changed: {applied['changed']}")
    r.append(check("preset expands to a filter", "tasks/*" in applied["filter"]))
    r.append(check("chat id stored", applied["chat_id"] == "-1001234567890"))
    r.append(check("next step points at the missing token",
                   "TOKEN" in str(applied.get("next_step", ""))))

    store = get_store()
    for e in ALL:
        store.put(e["event"], None, e["payload"], source=e["source"],
                  entity=e["entity"], entity_id=e["entity_id"], dedup_on=e["id"])
    preview = json.loads(await TG.b24_telegram_test(preview_only=True))
    print(f"   preview: would forward {preview['would_forward']} of {preview['of_recent']}")
    r.append(check("preview answers 'will I be spammed' without sending",
                   preview["would_forward"] < preview["of_recent"]))

    off = json.loads(await TG.b24_telegram_configure(events="nothing"))
    r.append(check("'nothing' silences without losing chat id",
                   off["filter"] == "" and off["chat_id"] == "-1001234567890"))
    back = json.loads(await TG.b24_telegram_configure(reset=True))
    r.append(check("reset falls back to .env", back["chat_id"] is None))

    no_token = json.loads(await TG.b24_telegram_test())
    r.append(check("sending without a token fails clearly",
                   bool(no_token.get("error")) and "token" in str(no_token.get("message")).lower()))

    print("\n-- forwarding is tracked apart from ack --")
    ids = [e["id"] for e in store.pending_forward(limit=10)]
    r.append(check("all captured events are pending forward", len(ids) == 4))
    store.ack(ids)
    r.append(check("acking does not mark them forwarded",
                   len(store.pending_forward(limit=10)) == 4))
    store.mark_forwarded(ids[:2])
    r.append(check("marking forwarded shrinks the backlog",
                   store.forward_backlog() == 2))

    print("\n" + ("ALL CLEAR" if all(r) else "SOMETHING BROKE"))
    sys.exit(0 if all(r) else 1)


if __name__ == "__main__":
    asyncio.run(main())
