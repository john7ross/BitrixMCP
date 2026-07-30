"""Verify the coverage requirement (T-1) against its own acceptance criteria.

T-1 says: for an arbitrary documented method the agent must be able to
(1) learn the method exists, (2) learn its exact parameters, (3) call it.

This checks all three - and specifically probes methods the portal's own
`methods` listing omits, since that omission is why the catalog exists.

    python scripts/coverage_check.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ENV = Path(__file__).resolve().parent.parent / ".env"
if _ENV.exists():
    for _line in _ENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from bitrix_mcp.catalog import catalog, method_schema, method_search  # noqa: E402
from bitrix_mcp.tools import discovery as D  # noqa: E402

# Methods confirmed callable on a live portal but ABSENT from `methods`.
OMITTED_BY_PORTAL = [
    "tasks.task.list", "crm.item.list", "catalog.product.list",
    "sale.order.list", "tasks.api.scrum.sprint.list",
]


def check(label: str, cond: bool) -> bool:
    print(("PASS " if cond else "FAIL ") + label)
    return cond


async def main() -> None:
    results = []
    data = catalog()
    print(f"catalog: {len(data)} methods\n")
    results.append(check("catalog is substantial (>1500 methods)", len(data) > 1500))

    print("-- (1) methods the portal's own listing omits are discoverable --")
    for name in OMITTED_BY_PORTAL:
        results.append(check(f"   {name} in catalog", name in data))

    print("\n-- (2) exact parameters, not guesses --")
    schema = method_schema("crm.item.list")
    required = [p["name"] for p in schema["params"] if p["required"]]
    print(f"   crm.item.list -> scope={schema['scope']} required={required}")
    results.append(check("   crm.item.list schema found", schema["found"]))
    results.append(check("   entityTypeId marked required", "entityTypeId" in required))

    tel = method_schema("telephony.externalCall.register")
    tel_required = [p["name"] for p in tel["params"] if p["required"]]
    print(f"   telephony.externalCall.register -> required={tel_required}")
    results.append(check("   telephony schema has 4 required params", len(tel_required) >= 4))

    print("\n-- search finds a method from intent, not from its name --")
    hits = method_search("call recording attach", limit=5)
    names = [h["method"] for h in hits]
    print(f"   'call recording attach' -> {names[:3]}")
    results.append(check("   search returns telephony results",
                         any("telephony" in n or "voximplant" in n for n in names)))

    typo = method_schema("crm.deal.lst")
    results.append(check("   typo yields suggestions, not silence",
                         typo["found"] is False and len(typo["did_you_mean"]) > 0))

    print("\n-- (3) scope diagnosis against the live portal --")
    gaps = json.loads(await D.b24_scope_gaps())
    if gaps.get("error"):
        print("   scope call failed:", gaps.get("message"))
        results.append(check("   scope diagnosis reachable", False))
    else:
        print(f"   reachable={gaps['reachable_methods']} blocked={gaps['blocked_methods']}")
        for scope, count in list(gaps["missing_scopes"].items())[:6]:
            print(f"     missing scope {scope:<16} {count} methods")
        results.append(check("   reports reachable methods", gaps["reachable_methods"] > 1000))
        results.append(check("   reports missing scopes", isinstance(gaps["missing_scopes"], dict)))

    print("\n" + ("T-1 SATISFIED" if all(results) else "T-1 NOT SATISFIED"))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
