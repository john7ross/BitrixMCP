"""Offline unit tests for secret redaction and the method catalog.

The redaction tests use a synthetic token shaped like a real one; the live
credential is never reproduced here. They exist because all three leaks found
on the live portal looked harmless in review and only showed up when a real
token was searched for in real output.
"""

from __future__ import annotations

import logging

import pytest

from bitrix_mcp.catalog import (
    CatalogUnavailable,
    catalog,
    method_schema,
    method_search,
    scope_gaps,
    stats,
)
from bitrix_mcp.sanitize import MASK, install_log_filter, sanitize, scrub_text

FAKE = "0hqXXXXXXXXXXXXX"          # same shape as a real webhook token
PORTAL = "https://portal.example.ru"


# --- sanitize ---------------------------------------------------------------

def test_webhook_url_keeps_shape_loses_secret():
    """A redacted URL must still be diagnosable: host and user id survive."""
    out = scrub_text(f"{PORTAL}/rest/5432/{FAKE}/crm.deal.list/")
    assert FAKE not in out
    assert "/rest/5432/" in out and MASK in out


def test_document_generator_leak_is_closed():
    """crm.documentgenerator.template.list returns the full webhook URL."""
    payload = {"templates": {"1": {"id": "1", "name": "Акт",
               "downloadMachine": f"{PORTAL}/rest/5432/{FAKE}/x/?token=abc"}}}
    out = repr(sanitize(payload))
    assert FAKE not in out and "abc" not in out
    assert "Акт" in out                      # harmless content survives


def test_connection_test_leak_is_closed():
    """b24_test_connection used to echo the resolved webhook verbatim."""
    out = repr(sanitize({"ok": True, "webhook": f"{PORTAL}/rest/5432/{FAKE}/"}))
    assert FAKE not in out


@pytest.mark.parametrize("key", [
    "auth", "token", "access_token", "application_token", "callToken", "signature",
])
def test_secret_keys_are_masked(key):
    assert sanitize({key: "value"})[key] == MASK


def test_pull_channel_ids_are_masked():
    """Channel ids are bearer values - they grant the event stream."""
    config = {"channels": {"private": {"id": "abc.def-signature", "end": "2026-07-29"}}}
    out = sanitize(config)
    assert out["channels"]["private"]["id"] == MASK
    assert out["channels"]["private"]["end"] == "2026-07-29"


def test_ordinary_content_is_untouched():
    plain = {"TITLE": "Договор поставки", "URL": f"{PORTAL}/crm/deal/details/42/"}
    assert sanitize(plain) == plain


def test_log_filter_redacts_non_string_arguments(caplog):
    """httpx logs the URL as an httpx.URL object, not a str - a str-only check
    silently misses the exact leak this filter exists to stop."""

    class UrlLike:
        def __str__(self) -> str:
            return f"{PORTAL}/rest/5432/{FAKE}/pull.config.get"

    install_log_filter()
    logger = logging.getLogger("httpx")
    logger.setLevel(logging.INFO)
    with caplog.at_level(logging.INFO, logger="httpx"):
        logger.info("HTTP Request: %s %s", "POST", UrlLike())
    assert FAKE not in caplog.text


def test_log_filter_leaves_numeric_arguments_alone():
    """Rewriting every argument would break %d formatting."""
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                              "status %d", (200,), None)
    for handler_filter in logging.getLogger("httpx").filters:
        handler_filter.filter(record)
    assert record.getMessage() == "status 200"


# --- catalog: the knowledge half of full API coverage -----------------------

def test_catalog_is_present_and_substantial():
    try:
        data = catalog()
    except CatalogUnavailable:
        pytest.skip("catalog not built; run scripts/build_catalog.py")
    assert len(data) > 1500


@pytest.mark.parametrize("method", [
    "tasks.task.list", "crm.item.list", "catalog.product.list",
    "sale.order.list", "tasks.api.scrum.sprint.list",
])
def test_methods_omitted_by_the_portal_are_still_discoverable(method):
    """These answer when called but are absent from the portal's own `methods`
    listing - which is precisely why the catalog is not built from it."""
    try:
        data = catalog()
    except CatalogUnavailable:
        pytest.skip("catalog not built")
    assert method in data


def test_schema_marks_required_parameters():
    try:
        schema = method_schema("crm.item.list")
    except CatalogUnavailable:
        pytest.skip("catalog not built")
    assert schema["found"] is True
    assert "entityTypeId" in [p["name"] for p in schema["params"] if p["required"]]


def test_unknown_method_suggests_alternatives():
    try:
        schema = method_schema("crm.deal.lst")
    except CatalogUnavailable:
        pytest.skip("catalog not built")
    assert schema["found"] is False
    assert schema["did_you_mean"]


def test_search_finds_by_intent_not_only_by_name():
    try:
        names = [hit["method"] for hit in method_search("call recording attach")]
    except CatalogUnavailable:
        pytest.skip("catalog not built")
    assert any("telephony" in name or "voximplant" in name for name in names)


def test_scope_gaps_separates_reachable_from_blocked():
    try:
        gaps = scope_gaps(["crm", "task"])
    except CatalogUnavailable:
        pytest.skip("catalog not built")
    assert gaps["reachable_methods"] > 0
    assert "telephony" in gaps["missing_scopes"]


def test_stats_reports_the_catalog_file():
    try:
        info = stats()
    except CatalogUnavailable:
        pytest.skip("catalog not built")
    assert info["methods"] > 1500 and info["size_bytes"] > 0
