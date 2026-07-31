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
import math
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


# Words that carry no intent but used to score points anyway. The damage was not
# theoretical: "on" is a substring of "sonet_group", so a preposition earned
# every workgroup method three points.
_STOPWORDS = frozenset("""
a an the this that of to for from with by at in into on over as is are be do does
how i my me we our you your it its and or not all any some new please want need
""".split())

# Bitrix's names are not the words an agent reaches for. Moving a card on a
# sprint board is `kanban.addTask`; a workgroup is a `sonet_group`; call
# statistics live under `voximplant`. Without this map an accurate description
# of the intent cannot reach the method.
_SYNONYMS: dict[str, frozenset[str]] = {
    "board": frozenset({"kanban", "stage"}),
    "column": frozenset({"kanban", "stage"}),
    "move": frozenset({"add", "move", "update", "stage"}),
    "transfer": frozenset({"move", "add"}),
    "create": frozenset({"add", "create"}),
    "make": frozenset({"add", "create"}),
    "remove": frozenset({"delete", "remove"}),
    "erase": frozenset({"delete"}),
    "edit": frozenset({"update"}),
    "change": frozenset({"update"}),
    "modify": frozenset({"update"}),
    "read": frozenset({"get", "list"}),
    "show": frozenset({"get", "list"}),
    "fetch": frozenset({"get", "list"}),
    "list": frozenset({"list", "get"}),
    "find": frozenset({"search", "find", "list"}),
    "search": frozenset({"search", "find"}),
    "send": frozenset({"add", "send"}),
    "post": frozenset({"add", "post"}),
    "employee": frozenset({"user"}),
    "staff": frozenset({"user"}),
    "workgroup": frozenset({"sonet_group", "group", "sonet"}),
    "project": frozenset({"sonet_group", "group", "sonet"}),
    "call": frozenset({"voximplant", "telephony", "call"}),
    "phone": frozenset({"voximplant", "telephony", "call"}),
    "telephony": frozenset({"voximplant", "telephony"}),
    "recording": frozenset({"record", "statistic", "voximplant"}),
    "chat": frozenset({"chat", "im", "dialog"}),
    "message": frozenset({"message", "im", "dialog"}),
    "comment": frozenset({"comment", "commentitem"}),
    "checklist": frozenset({"checklist", "checklistitem"}),
    "time": frozenset({"elapseditem", "elapsed", "time"}),
    "spent": frozenset({"elapseditem", "elapsed"}),
    "log": frozenset({"elapseditem", "add"}),
    "meeting": frozenset({"event", "calendar"}),
    "folder": frozenset({"folder", "disk"}),
    "file": frozenset({"file", "disk"}),
    "product": frozenset({"product", "catalog"}),
    "order": frozenset({"order", "sale"}),
    "pipeline": frozenset({"category", "funnel"}),
}

_EVENT_WORDS = frozenset({"event", "events", "handler", "webhook", "trigger", "notification"})

# A query with no mutating verb is asking to read. Bitrix usually offers both in
# the same family (kanban.addStage vs kanban.getStages), and without this the
# alphabet decides - "sprint board columns" surfaced addStage/deleteStage first.
_WRITE_VERBS = frozenset({
    "add", "create", "make", "new", "update", "edit", "change", "modify", "set",
    "delete", "remove", "erase", "move", "transfer", "send", "post", "start",
    "complete", "finish", "upload", "attach", "assign",
})
_READ_VERBS = frozenset({"get", "list", "search", "find", "fields", "stat", "statistic"})


def _stem(word: str) -> str:
    """Crude singulariser - enough to tie 'tasks'/'task' and 'stages'/'stage'."""
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _segments(name: str) -> set[str]:
    """Split a method name into comparable pieces: dots, underscores, camelCase.

    'tasks.api.scrum.kanban.addTask' -> {task, api, scrum, kanban, add, addtask}
    """
    out: set[str] = set()
    for part in re.split(r"[^A-Za-z0-9]+", name):
        if not part:
            continue
        out.add(_stem(part.lower()))
        for piece in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", part):
            out.add(_stem(piece.lower()))
    return out


@lru_cache(maxsize=1)
def _segment_index() -> tuple[dict[str, set[str]], dict[str, float]]:
    """Per-method segments plus an IDF weight per segment.

    IDF is what makes intent search work. 'department' appears in a handful of
    methods and 'list' in hundreds, so a query naming both must be decided by
    'department'. Scoring every word equally is why "list company departments"
    used to return crm.company.* and never department.get.
    """
    segs: dict[str, set[str]] = {}
    freq: dict[str, int] = {}
    for name in catalog():
        s = _segments(name)
        segs[name] = s
        for token in s:
            freq[token] = freq.get(token, 0) + 1
    total = max(1, len(segs))
    idf = {tok: math.log(total / (1 + n)) + 1.0 for tok, n in freq.items()}
    return segs, idf


def method_search(query: str, scope: str | None = None,
                  include_deprecated: bool = True, limit: int = 25) -> list[dict]:
    """Rank methods by how well they answer the query.

    Matches whole name segments rather than substrings, weights each segment by
    how rare it is across the catalog, bridges the vocabulary gap between how a
    task is described and how Bitrix named the method, and keeps event handlers
    (``on*``) below real methods unless the query is about events.
    """
    raw = _tokens(query)
    stemmed = [_stem(t) for t in raw]
    words = [w for w in stemmed if w not in _STOPWORDS] or stemmed
    if not words:
        return []
    prefix = (query or "").strip().lower()
    wants_events = bool(_EVENT_WORDS & set(raw))
    reading = not (_WRITE_VERBS & set(words))
    segs_by_name, idf = _segment_index()

    ranked: list[tuple[float, int, str, dict]] = []
    for name, entry in catalog().items():
        if scope and scope not in entry["scope"]:
            continue
        if not include_deprecated and entry["deprecated"]:
            continue

        lowered = name.lower()
        segments = segs_by_name.get(name) or _segments(name)
        doc = entry["doc"].lower()

        score = 0.0
        matched = 0
        for word in words:
            weight = idf.get(word, 2.0)
            alt = _SYNONYMS.get(word)
            hit_via_synonym = {_stem(a) for a in alt} & segments if alt else set()
            if word in segments:
                score += weight
                matched += 1
            elif hit_via_synonym:
                # Weight by the rarity of the segment actually found, not of the
                # word the user typed: "recording" appears in no method name, but
                # the "statistic" it resolves to is rare and therefore telling.
                best = max(idf.get(seg, weight) for seg in hit_via_synonym)
                score += max(weight, best) * 0.6
                matched += 1
            elif len(word) >= 4 and word in lowered:
                score += weight * 0.3
                matched += 1
            elif len(word) >= 4 and word in doc:
                score += weight * 0.2
                matched += 1

        if not score:
            continue

        # Answering every word beats answering half of them, even a rarer half.
        score *= 0.4 + 0.6 * (matched / len(words))

        depth = name.count(".")
        if prefix and lowered.startswith(prefix):
            score += 12.0
        if lowered.startswith("on") and not wants_events:
            score -= 4.0
        if entry["deprecated"]:
            score -= 2.0
        if reading and segments & _READ_VERBS:
            score += 1.0
        # Bitrix nests specialised variants deeper (im.message.add vs
        # imbot.v2.Chat.Message.send), so the canonical answer is the shallow one.
        score -= 0.8 * depth

        if score > 0:
            ranked.append((score, depth, name, entry))

    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    hits = [(s, n, e) for s, _depth, n, e in ranked]
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
