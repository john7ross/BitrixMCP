"""Offline unit tests for the event feed - no network, no portal, no Telegram.

Every fixture below is a real payload shape captured from a live portal, not an
invention: the pull-channel task move, chat noise, the documented
outgoing-webhook body. Tests built on imagined shapes pass while production
fails, which is the failure mode this whole subsystem is exposed to.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bitrix_mcp.events.feeds import FEEDS, Feed, resolve, rows_of
from bitrix_mcp.events.phpform import parse_php_form, split_key
from bitrix_mcp.events.receiver import MAX_BODY, handle_delivery
from bitrix_mcp.events.store import EventStore, extract_entity
from bitrix_mcp.events.telegram import (
    build_messages,
    format_event,
    parse_filter,
    should_forward,
)


@pytest.fixture()
def store(tmp_path: Path) -> EventStore:
    return EventStore(str(tmp_path / "events.sqlite3"))


# --- phpform: outgoing webhooks are urlencoded PHP arrays, never JSON --------

def test_split_key_nested():
    assert split_key("data[FIELDS][ID]") == ["data", "FIELDS", "ID"]
    assert split_key("ts") == ["ts"]


def test_parse_documented_webhook_body():
    body = ("event=ONCRMDEALUPDATE&data%5BFIELDS%5D%5BID%5D=662&ts=1484647109"
            "&auth%5Bdomain%5D=portal.tld&auth%5Bapplication_token%5D=tok")
    parsed = parse_php_form(body)
    assert parsed["event"] == "ONCRMDEALUPDATE"
    assert parsed["data"]["FIELDS"]["ID"] == "662"
    assert parsed["auth"]["application_token"] == "tok"


def test_parse_two_sibling_branches():
    """Task events carry FIELDS_BEFORE and FIELDS_AFTER side by side."""
    parsed = parse_php_form(
        "data%5BFIELDS_BEFORE%5D%5BID%5D=7&data%5BFIELDS_AFTER%5D%5BID%5D=7")
    assert parsed["data"]["FIELDS_BEFORE"]["ID"] == "7"
    assert parsed["data"]["FIELDS_AFTER"]["ID"] == "7"


@pytest.mark.parametrize("body,expected", [
    ("", {}),
    ("a%5Bb=1", {"a": {"b": "1"}}),           # missing closing bracket
])
def test_parse_malformed_does_not_raise(body, expected):
    assert parse_php_form(body) == expected


# --- receiver: fail closed, never 500, never store the credential ------------

def _delivery(token="tok", event="ONCRMDEALUPDATE", deal="662"):
    return (f"event={event}&data%5BFIELDS%5D%5BID%5D={deal}&ts=1"
            f"&auth%5Bdomain%5D=portal.tld&auth%5Bapplication_token%5D={token}").encode()


def test_valid_delivery_is_stored(store):
    result = handle_delivery(_delivery(), "tok", store)
    assert (result.status, result.reason) == (200, "stored")


def test_retry_is_deduplicated(store):
    handle_delivery(_delivery(), "tok", store)
    again = handle_delivery(_delivery(), "tok", store)
    assert again.status == 200 and again.reason == "duplicate"
    assert store.stats()["total"] == 1


def test_wrong_token_rejected_and_not_stored(store):
    result = handle_delivery(_delivery(token="wrong"), "tok", store)
    assert result.status == 403
    assert store.stats()["total"] == 0


def test_unconfigured_receiver_refuses_everything(store):
    """No token configured must mean a closed door, not an open one."""
    assert handle_delivery(_delivery(), None, store).status == 503


def test_oversized_body_rejected(store):
    assert handle_delivery(b"x" * (MAX_BODY + 1), "tok", store).status == 413


def test_garbage_never_returns_500(store):
    """A 500 makes Bitrix retry the same broken delivery forever."""
    assert handle_delivery(b"\xff\xfe not a form", "tok", store).status in (200, 400, 403)


def test_application_token_is_not_persisted(store):
    handle_delivery(_delivery(), "tok", store)
    row = store.poll(include_acked=True)[0]
    assert "application_token" not in row["payload"]["auth"]
    assert row["payload"]["auth"]["domain"] == "portal.tld"


# --- store: queue, archive, cursors, settings -------------------------------

def test_ack_hides_from_poll_but_keeps_history(store):
    """Ack means 'the agent processed it', not 'delete it'."""
    store.put("tasks/task_update", "1", {"params": {"TASK_ID": 42}},
              source="pull", dedup_on="mid-1")
    ids = [row["id"] for row in store.poll()]
    assert store.ack(ids) == 1
    assert store.poll() == []
    assert len(store.history(entity="task", entity_id="42")) == 1


def test_ack_is_idempotent(store):
    store.put("x", None, {"data": {}}, dedup_on="a")
    ids = [row["id"] for row in store.poll()]
    assert store.ack(ids) == 1
    assert store.ack(ids) == 0


def test_forwarding_is_tracked_apart_from_ack(store):
    """Otherwise an agent run would silently swallow a human's notifications."""
    store.put("x", None, {"data": {}}, dedup_on="a")
    ids = [row["id"] for row in store.poll()]
    store.ack(ids)
    assert len(store.pending_forward()) == 1
    store.mark_forwarded(ids)
    assert store.forward_backlog() == 0


def test_purge_is_by_age_not_by_ack(store):
    """The store doubles as an archive; 'processed' must not mean 'expendable'."""
    store.retention_days = 365
    store.put("x", None, {"data": {}}, dedup_on="a")
    store.ack([row["id"] for row in store.poll()])
    assert store.purge() == 0
    assert store.stats()["total"] == 1


def test_cursor_round_trip_and_upsert(store):
    assert store.cursor_get("tasks") is None
    store.cursor_set("tasks", "2026-07-28T10:00:00")
    store.cursor_set("tasks", "2026-07-28T12:00:00")
    assert store.cursor_get("tasks") == "2026-07-28T12:00:00"


def test_settings_override_and_clear(store):
    store.setting_set("telegram.filter", "tasks/*")
    assert store.setting_get("telegram.filter") == "tasks/*"
    assert store.setting_delete("telegram.filter") is True
    assert store.setting_get("telegram.filter") is None


def test_explicit_entity_beats_extraction(store):
    """The poller knows what it fetched; a heuristic must not overrule it."""
    store.put("poll/task", None, {"params": {"row": {"id": "9"}}},
              source="poll", entity="task", entity_id="9", dedup_on="p1")
    assert store.history(entity="task", entity_id="9")[0]["source"] == "poll"


@pytest.mark.parametrize("event,payload,expected", [
    ("tasks/task_update", {"params": {"TASK_ID": 477818}}, ("task", "477818")),
    ("im/message", {"params": {"chatId": 95924}}, ("chat", "95924")),
    ("ONCRMDEALUPDATE", {"data": {"FIELDS": {"ID": "662"}}}, ("deal", "662")),
    # Real task deliveries use FIELDS_AFTER/FIELDS_BEFORE, not FIELDS. Missing
    # this stores the event but makes it invisible to a history query - caught
    # by the end-to-end receiver check, not by any shape taken from the docs.
    ("ONTASKUPDATE",
     {"data": {"FIELDS_AFTER": {"ID": "477818"}, "FIELDS_BEFORE": {"ID": "477818"}}},
     ("task", "477818")),
    ("ONTASKADD", {"data": {"FIELDS_BEFORE": {"ID": "9"}}}, ("task", "9")),
    ("something/unknown", {"params": {"weird": 1}}, (None, None)),
])
def test_entity_extraction(event, payload, expected):
    """Unknown shapes must yield None, never a guess: a wrong id would make a
    history query silently return someone else's events."""
    assert extract_entity(event, payload) == expected


# --- feeds: the input/output casing trap ------------------------------------

def test_tasks_feed_input_and_output_field_names_differ():
    """tasks.task.list filters on CHANGED_DATE but returns changedDate.
    Getting this wrong leaves the cursor stuck re-reading one window forever."""
    feed = FEEDS["tasks"]
    assert feed.date_filter_field == "CHANGED_DATE"
    assert feed.date_key == "changedDate"


def test_resolve_unknown_feed_lists_the_valid_ones():
    with pytest.raises(KeyError) as excinfo:
        resolve("nope")
    assert "tasks" in str(excinfo.value)


def test_resolve_smart_process_builds_a_feed():
    feed = resolve("anything", entity_type_id=1030)
    assert feed.extra_params["entityTypeId"] == 1030
    assert feed.entity == "item1030"


def test_rows_of_handles_both_response_shapes():
    feed = FEEDS["tasks"]
    assert rows_of(feed, {"tasks": [{"id": "1"}]}) == [{"id": "1"}]
    assert rows_of(Feed("m", "D", None, "id", "d", "e"), [{"id": "2"}]) == [{"id": "2"}]


# --- telegram: the user decides what arrives --------------------------------

TASK_MOVE = {
    "id": 1, "event": "tasks/task_update", "source": "pull",
    "entity": "task", "entity_id": "477818", "received_at": 1785195801,
    "payload": {"params": {"BEFORE": {"STAGE": "Готовы разработке"},
                           "AFTER": {"STAGE": "Разработка"}}},
}
CHAT_NOISE = {
    "id": 2, "event": "im/readMessageChatOpponent", "source": "pull",
    "entity": "chat", "entity_id": "14969", "received_at": 1785195802, "payload": {},
}
DEAL_HOOK = {
    "id": 3, "event": "ONCRMDEALUPDATE", "source": "webhook",
    "entity": "deal", "entity_id": "662", "received_at": 1785195803, "payload": {},
}
POLLED = {
    "id": 4, "event": "poll/task", "source": "poll",
    "entity": "task", "entity_id": "485747", "received_at": 1785195804,
    "payload": {"params": {"row": {"title": "Отчет за спринт"}}},
}
ALL = [TASK_MOVE, CHAT_NOISE, DEAL_HOOK, POLLED]


def test_empty_filter_forwards_nothing():
    """Default silence is deliberate: a forwarder that floods on day one gets
    muted and never trusted again."""
    assert not any(should_forward(event, "") for event in ALL)
    assert parse_filter("") == ([], [])


@pytest.mark.parametrize("spec,expected_ids", [
    ("tasks/*", [1]),
    ("ONCRM*", [3]),
    ("entity:task", [1, 4]),          # spans pull and poll
    ("source:poll", [4]),
    ("*", [1, 2, 3, 4]),
    ("*,-im/*", [1, 3, 4]),           # exclusions win
    ("oncrmdealupdate", [3]),         # case-insensitive
])
def test_filter_selects_what_the_user_asked_for(spec, expected_ids):
    assert [e["id"] for e in ALL if should_forward(e, spec)] == expected_ids


def test_stage_transition_is_spelled_out():
    """'task changed' is useless; the before/after pair is the whole point."""
    text = format_event(TASK_MOVE, "https://portal.example.ru")
    assert "Готовы разработке" in text and "Разработка" in text and "→" in text


def test_html_is_escaped():
    hostile = {**POLLED, "payload": {"params": {"row": {"title": "<script>x</script>"}}}}
    assert "<script>" not in format_event(hostile, None)


def test_messages_are_batched():
    assert len(build_messages(ALL * 20, None)) < len(ALL * 20)
