"""Knowledge layer over the Bitrix24 REST API surface.

Execution was never the gap: `b24_call` reaches every method. What the agent
lacked was knowing *which* method exists and *what parameters it takes* - so it
guessed, and a guessed signature fails in ways that look like a portal problem.

This closes that gap without one tool per method. There are 1930 documented
methods; that many tool descriptions would not fit in any context window and
would turn tool choice into a lottery. Three tools plus the catalog do the job.

The catalog is generated from the official documentation source by
`scripts/build_catalog.py`. It is data, not code: regenerate it rather than
editing it by hand.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parent / "data" / "catalog.json"
DOC_BASE = "https://apidocs.bitrix24.ru/"


class CatalogUnavailable(RuntimeError):
    """The catalog file is missing - the server still works, discovery does not."""


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict]:
    if not CATALOG_PATH.exists():
        raise CatalogUnavailable(
            f"Method catalog not found at {CATALOG_PATH}. "
            f"Run: python scripts/build_catalog.py"
        )
    with CATALOG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def doc_url(relative: str) -> str:
    return DOC_BASE + re.sub(r"\.md$", "", relative)


def _tokens(query: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", query.lower().replace(".", " ")) if t]


def method_search(query: str, scope: str | None = None,
                  include_deprecated: bool = True, limit: int = 25) -> list[dict]:
    """Rank methods by how well their name and doc path match the query."""
    words = _tokens(query)
    prefix = (query or "").strip().lower()
    hits: list[tuple[int, str, dict]] = []
    for name, entry in catalog().items():
        if scope and scope not in entry["scope"]:
            continue
        if not include_deprecated and entry["deprecated"]:
            continue
        lowered = name.lower()
        haystack = lowered + " " + entry["doc"].lower()
        score = sum(3 if word in lowered else (1 if word in haystack else 0)
                    for word in words)
        if prefix and lowered.startswith(prefix):
            score += 5
        if score:
            hits.append((score, name, entry))
    hits.sort(key=lambda item: (-item[0], item[1]))
    return [{
        "method": name,
        "scope": entry["scope"],
        "deprecated": entry["deprecated"],
        "required_params": [p["name"] for p in entry["params"] if p["required"]],
        "all_params": [p["name"] for p in entry["params"]],
        "doc": doc_url(entry["doc"]),
    } for _score, name, entry in hits[:limit]]


def method_schema(method: str) -> dict:
    """Exact parameters for one method, straight from the documentation."""
    entry = catalog().get(method)
    if entry is None:
        near = method_search(method, limit=5)
        return {
            "found": False,
            "method": method,
            "message": "Not in the documented catalog. It may still be callable "
                       "via b24_call - the catalog covers documented methods only.",
            "did_you_mean": [hit["method"] for hit in near],
        }
    return {
        "found": True,
        "method": method,
        "scope": entry["scope"],
        "deprecated": entry["deprecated"],
        "doc": doc_url(entry["doc"]),
        "params": entry["params"],
    }


def scope_gaps(granted: list[str]) -> dict:
    """Turn ACCESS_DENIED / ERROR_METHOD_NOT_FOUND into an actionable checklist.

    On a live portal, ERROR_METHOD_NOT_FOUND does NOT mean the module is absent:
    it is also what you get when the scope was simply not ticked when the webhook
    was created. Comparing granted scopes against the catalog tells the two apart.
    """
    granted_set = {s.strip() for s in granted if s and s.strip()}
    per_scope: dict[str, int] = {}
    for entry in catalog().values():
        for scope in entry["scope"]:
            per_scope[scope] = per_scope.get(scope, 0) + 1
    missing = {s: n for s, n in per_scope.items() if s not in granted_set}
    reachable = sum(n for s, n in per_scope.items() if s in granted_set)
    return {
        "granted_scopes": sorted(granted_set),
        "reachable_methods": reachable,
        "blocked_methods": sum(missing.values()),
        "missing_scopes": dict(sorted(missing.items(), key=lambda kv: -kv[1])),
        "hint": "Methods under a missing scope answer ERROR_METHOD_NOT_FOUND, "
                "which looks identical to 'module not installed'. Re-issue the "
                "webhook with those scopes ticked to tell the two apart.",
    }


def stats() -> dict[str, Any]:
    data = catalog()
    return {
        "methods": len(data),
        "with_parameters": sum(1 for v in data.values() if v["params"]),
        "deprecated": sum(1 for v in data.values() if v["deprecated"]),
        "path": str(CATALOG_PATH),
        "size_bytes": CATALOG_PATH.stat().st_size if CATALOG_PATH.exists() else 0,
    }
