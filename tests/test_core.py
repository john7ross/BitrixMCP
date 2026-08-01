"""Offline unit tests — pure logic, no network to any portal.

Covers the parts most likely to regress: request encoding, the read-only write
classifier, webhook validation/resolution, list normalization, the read-only
guard, and tool-registration parity.
"""

from __future__ import annotations

import asyncio

import pytest

from bitrix_mcp.client import (
    BitrixError,
    _extract_list,
    is_write_method,
    normalize_webhook,
    php_query,
)


# --- php_query (PHP http_build_query semantics, used for batch) --------------

def test_php_query_nested_and_types():
    q = php_query({"filter": {">ID": 1, "STAGE": "x"}, "select": ["ID", "TITLE"], "flag": True})
    # brackets are percent-encoded; PHP parse_str decodes them back
    assert "filter%5B%3EID%5D=1" in q
    assert "filter%5BSTAGE%5D=x" in q
    assert "select%5B0%5D=ID" in q and "select%5B1%5D=TITLE" in q
    assert "flag=1" in q


def test_php_query_empty():
    assert php_query({}) == ""


# --- is_write_method ---------------------------------------------------------

@pytest.mark.parametrize("method", [
    "crm.deal.add", "crm.item.update", "tasks.task.complete", "tasks.task.delete",
    "disk.folder.uploadfile", "disk.folder.addsubfolder", "sonet_group.create",
    "crm.item.productrow.set", "im.chat.add", "bizproc.workflow.start",
    "crm.deal.contact.items.set", "log.blogpost.add", "tasks.api.scrum.kanban.addTask",
])
def test_is_write_true(method):
    assert is_write_method(method) is True


@pytest.mark.parametrize("method", [
    "crm.deal.list", "crm.deal.get", "user.get", "user.current", "tasks.task.list",
    "disk.folder.getchildren", "disk.storage.getlist", "sonet_group.get",
    "tasks.api.scrum.kanban.getStages", "voximplant.statistic.get",
    "crm.status.list", "im.dialog.messages.get", "user.search", "catalog.catalog.list",
])
def test_is_write_false(method):
    assert is_write_method(method) is False


# --- normalize_webhook -------------------------------------------------------

def test_normalize_webhook_adds_slash():
    assert normalize_webhook("https://p.bitrix24.ru/rest/1/abc123") == \
        "https://p.bitrix24.ru/rest/1/abc123/"


def test_normalize_webhook_keeps_slash():
    url = "https://p.bitrix24.ru/rest/1/tok/"
    assert normalize_webhook(url) == url


@pytest.mark.parametrize("bad", ["", "http://bad", "https://p/rest/", "not a url", "https://p/rest/abc/tok/"])
def test_normalize_webhook_rejects(bad):
    with pytest.raises(ValueError):
        normalize_webhook(bad)


# --- _extract_list -----------------------------------------------------------

def test_extract_list_shapes():
    assert _extract_list([1, 2]) == [1, 2]
    assert _extract_list({"tasks": [1]}) == [1]
    assert _extract_list({"items": [2]}) == [2]
    assert _extract_list({"events": [3]}) == [3]
    assert _extract_list({"7": {"ID": 7}}) == [{"ID": 7}]  # dict keyed by id
    assert _extract_list(None) == []
    assert _extract_list("scalar") == ["scalar"]


def test_extract_list_unwraps_named_dict_keyed_by_id():
    """crm.documentgenerator.template.list answers {"templates": {"1": {...}}}.

    Confirmed live: 19 templates arrived as ONE record because the wrapper key
    was never unwrapped, while `total` said 19 - a silent, self-contradicting
    result rather than an error.
    """
    payload = {"templates": {"1": {"id": "1", "name": "Акт"},
                             "2": {"id": "2", "name": "Счёт"}}}
    out = _extract_list(payload)
    assert len(out) == 2, out
    assert {r["name"] for r in out} == {"Акт", "Счёт"}


def test_extract_list_does_not_unwrap_a_single_id_key():
    """The guard above must not eat a genuine one-record dict keyed by id."""
    assert _extract_list({"42": {"ID": 42, "TITLE": "x"}}) == [{"ID": 42, "TITLE": "x"}]


# --- webhook resolution + read-only guard ------------------------------------

def test_resolve_webhook_precedence(monkeypatch):
    from bitrix_mcp import runtime
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://env.bitrix24.ru/rest/1/env/")
    # personal beats explicit beats env
    assert "personal" in runtime.resolve_webhook(None, "https://x/rest/1/wh/", "https://p/rest/1/personal/")
    assert runtime.resolve_webhook(None, "https://x.bitrix24.ru/rest/1/wh/", None).endswith("/wh/")
    assert "env" in runtime.resolve_webhook(None, None, None)


def test_resolve_webhook_missing(monkeypatch):
    from bitrix_mcp import runtime
    monkeypatch.delenv("BITRIX_WEBHOOK_URL", raising=False)
    with pytest.raises(BitrixError) as ei:
        runtime.resolve_webhook(None, None, None)
    assert ei.value.code == "NO_WEBHOOK"


def test_guard_blocks_write_when_readonly(monkeypatch):
    from bitrix_mcp import runtime
    monkeypatch.setenv("BITRIX_READ_ONLY", "1")
    with pytest.raises(BitrixError) as ei:
        runtime.guard_write("crm.deal.add")
    assert ei.value.code == "B24_READONLY"
    # reads always pass
    runtime.guard_write("crm.deal.list")


def test_guard_allows_write_when_not_readonly(monkeypatch):
    from bitrix_mcp import runtime
    monkeypatch.delenv("BITRIX_READ_ONLY", raising=False)
    runtime.guard_write("crm.deal.add")  # no raise


# --- tool registration parity ------------------------------------------------

def test_all_tools_register_uniquely():
    from bitrix_mcp.server import mcp
    tools = asyncio.run(mcp.list_tools())
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), "duplicate tool names"
    assert all(n.startswith("b24_") for n in names)
    # A floor rather than an exact count: adding a tool should not fail the
    # suite, but silently losing a whole module must.
    assert len(names) >= 99, f"tool count dropped to {len(names)}"


def test_every_domain_still_has_tools():
    """Registration is import-driven, so a broken import loses a whole domain
    quietly - the server still starts, just without those tools."""
    from bitrix_mcp.server import mcp
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    for expected in ("b24_call", "b24_method_search", "b24_crm_list",
                     "b24_tasks_list", "b24_events_poll", "b24_changes_since",
                     "b24_telegram_status"):
        assert expected in names, f"{expected} is not registered"


def test_scrum_move_removes_from_the_board_before_placing(monkeypatch):
    """A sprint card is moved by deleteTask -> addTask, in that order.

    kanban.addTask alone only PLACES a card that is off the board; called on a
    card already in a column it returns true and does nothing. Confirmed by
    watching a real board: a card sat in the old column while the API reported
    success. Order matters, so assert the order, not just the calls.
    """
    import json as _json

    from bitrix_mcp.tools import scrum

    calls: list[tuple[str, dict]] = []

    class FakeClient:
        async def call_result(self, method, params=None):
            calls.append((method, params or {}))
            return True

    monkeypatch.setattr(scrum, "get_client", lambda *a, **k: FakeClient())
    monkeypatch.delenv("BITRIX_READ_ONLY", raising=False)

    out = _json.loads(asyncio.run(
        scrum.b24_scrum_task_move(task_id=42, sprint_id=794, stage_id=17951)
    ))

    assert [m for m, _ in calls] == [
        "tasks.api.scrum.kanban.deleteTask",
        "tasks.api.scrum.kanban.addTask",
    ], calls
    assert calls[0][1] == {"sprintId": 794, "taskId": 42}
    assert calls[1][1] == {"sprintId": 794, "taskId": 42, "stageId": 17951}
    assert out["moved"] is True
    # The caller must be told not to trust STAGE_ID for verification.
    assert "b24_task_get" in out["note"]


def test_server_reports_its_own_version_not_the_sdk_version():
    """`serverInfo.version` must be this package's, read from the installed
    metadata and never hardcoded here.

    Under the 1.x FastMCP there was no `version` parameter at all, so clients
    saw the SDK's own version (1.28.1) and could not tell which build they were
    talking to. MCPServer takes it directly.
    """
    from importlib.metadata import version

    from bitrix_mcp.server import mcp

    expected = version("bitrix-mcp")
    assert mcp.version == expected
    assert version("mcp") != expected  # the SDK's version must not leak back in


def test_every_tool_excludes_context_from_schema():
    from bitrix_mcp.server import mcp
    tools = asyncio.run(mcp.list_tools())
    for t in tools:
        props = t.input_schema.get("properties", {})
        assert "ctx" not in props, f"{t.name} leaks ctx into its schema"
