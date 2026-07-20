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
    "crm.deal.contact.items.set", "log.blogpost.add",
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
    assert len(names) == 87
    assert len(names) == len(set(names)), "duplicate tool names"
    assert all(n.startswith("b24_") for n in names)


def test_every_tool_excludes_context_from_schema():
    from bitrix_mcp.server import mcp
    tools = asyncio.run(mcp.list_tools())
    for t in tools:
        props = t.inputSchema.get("properties", {})
        assert "ctx" not in props, f"{t.name} leaks ctx into its schema"
