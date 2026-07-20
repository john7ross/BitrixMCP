#!/usr/bin/env python3
"""Live read-only access map for a Bitrix24 portal.

Fires ONE representative read method per domain and prints a single table of
what the given webhook can actually reach — separating "empty but allowed" from
"access denied" from "method not available". Nothing is modified on the portal.

Usage:
    uv run python scripts/smoke.py "https://portal/rest/<id>/<token>/"
    # or rely on BITRIX_WEBHOOK_URL:
    uv run python scripts/smoke.py

Tip: export BITRIX_TIMEOUT=20 to fail slow/blocked calls faster.
"""

from __future__ import annotations

import asyncio
import sys

from bitrix_mcp.client import BitrixClient, BitrixError, _extract_list
from bitrix_mcp.config import config


def _summarize(data: dict) -> str:
    """Short human note about a successful response."""
    result = data.get("result")
    total = data.get("total")
    items = _extract_list(result)
    if total is not None:
        return f"{len(items)} shown / total {total}"
    if isinstance(result, list):
        return f"{len(items)} item(s)"
    if isinstance(result, dict):
        return f"{len(items)} item(s)" if items else "ok (object)"
    return f"ok ({str(result)[:30]})"


def _classify(exc: Exception) -> tuple[str, str]:
    """Map an exception to (STATUS, detail)."""
    if isinstance(exc, BitrixError):
        code = (exc.code or "").upper()
        msg = str(exc)
        if "ACCESS" in code or "access denied" in msg.lower() or code in {"INSUFFICIENT_SCOPE"}:
            return "DENIED", code or "access denied"
        if "METHOD_NOT_FOUND" in code or "NOT_FOUND" in code:
            return "NO METHOD", code
        if code == "TIMEOUT":
            return "TIMEOUT", "request timed out"
        if "READONLY" in code:
            return "OK", "(write blocked by read-only)"
        return "ERROR", (code or msg)[:40]
    return "ERROR", f"{type(exc).__name__}: {exc}"[:40]


async def main() -> int:
    webhook = sys.argv[1] if len(sys.argv) > 1 else config.default_webhook
    if not webhook:
        print("No webhook. Pass it as an argument or set BITRIX_WEBHOOK_URL.")
        return 2

    client = BitrixClient(webhook, timeout=config.timeout)

    # Resolve the acting user id first (needed for calendar/user/task checks).
    uid = None
    try:
        me = await client.call_result("user.current")
        uid = me.get("ID")
    except Exception:  # noqa: BLE001
        pass

    # (domain label, method, params) — every method here is READ-only.
    checks: list[tuple[str, str, dict]] = [
        ("identity",        "user.current",                        {}),
        ("scopes",          "scope",                               {}),
        ("users",           "user.get",                            {"filter": {"ID": uid}} if uid else {}),
        ("user search",     "user.search",                         {"FIND": "a", "ACTIVE": True}),
        ("departments",     "department.get",                      {}),
        ("groups",          "sonet_group.get",                     {}),
        ("tasks",           "tasks.task.list",                     {"select": ["ID", "TITLE"]}),
        ("calendar",        "calendar.event.get",                  {"type": "user", "ownerId": uid, "from": "2026-01-01", "to": "2026-12-31"} if uid else {}),
        ("disk",            "disk.storage.getlist",                {}),
        ("crm: deals",      "crm.deal.list",                       {"select": ["ID"]}),
        ("crm: leads",      "crm.lead.list",                       {"select": ["ID"]}),
        ("crm: contacts",   "crm.contact.list",                    {"select": ["ID"]}),
        ("crm: companies",  "crm.company.list",                    {"select": ["ID"]}),
        ("crm: activities", "crm.activity.list",                   {}),
        ("crm: statuses",   "crm.status.list",                     {}),
        ("crm: currency",   "crm.currency.list",                   {}),
        ("crm: requisites", "crm.requisite.list",                  {}),
        ("crm: products",   "crm.product.list",                    {"select": ["ID"]}),
        ("lists",           "lists.get",                           {"IBLOCK_TYPE_ID": "lists"}),
        ("catalog",         "catalog.catalog.list",                {}),
        ("sale: orders",    "sale.order.list",                     {}),
        ("documents",       "crm.documentgenerator.template.list", {}),
        ("bizproc",         "bizproc.workflow.template.list",      {}),
        ("telephony",       "voximplant.statistic.get",            {}),
        ("im: recent",      "im.recent.get",                       {}),
        ("feed",            "log.blogpost.get",                    {}),
    ]

    rows: list[tuple[str, str, str, str]] = []
    counts = {"OK": 0, "DENIED": 0, "NO METHOD": 0, "TIMEOUT": 0, "ERROR": 0}
    for label, method, params in checks:
        try:
            data = await client.call(method, params)
            status, detail = "OK", _summarize(data)
        except Exception as exc:  # noqa: BLE001
            status, detail = _classify(exc)
        counts[status] = counts.get(status, 0) + 1
        rows.append((label, method, status, detail))

    # Render table (ASCII only, so Windows consoles don't mangle it).
    icon = {"OK": "[+]", "DENIED": "[x]", "NO METHOD": "[-]", "TIMEOUT": "[t]", "ERROR": "[!]"}
    status_cells = {st: f"{icon.get(st, '[?]')} {st}" for st in {r[2] for r in rows}}
    w_dom = max([len(r[0]) for r in rows] + [len("DOMAIN")])
    w_met = max([len(r[1]) for r in rows] + [len("METHOD")])
    w_st = max([len(v) for v in status_cells.values()] + [len("STATUS")])
    print(f"\nBitrix24 access map - {client.webhook}")
    if uid:
        print(f"acting user id: {uid}")
    print()
    print(f"{'DOMAIN'.ljust(w_dom)}  {'METHOD'.ljust(w_met)}  {'STATUS'.ljust(w_st)}  DETAIL")
    print(f"{'-'*w_dom}  {'-'*w_met}  {'-'*w_st}  {'-'*20}")
    for dom, met, st, det in rows:
        print(f"{dom.ljust(w_dom)}  {met.ljust(w_met)}  {status_cells[st].ljust(w_st)}  {det}")

    print("\nsummary: " + "  ".join(f"{k}={v}" for k, v in counts.items() if v))
    print("legend: [+] allowed   [x] no permission   [-] method/module absent   [t] timeout   [!] other error")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
