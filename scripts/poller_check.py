"""Live check of the polling fallback against the portal.

Runs b24_changes_since twice to prove the cursor actually advances - the most
likely failure is a cursor that never moves, which would silently re-fetch the
same window forever, and which no amount of "it returned rows" would catch.

    python scripts/poller_check.py
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

_ENV = Path(__file__).resolve().parent.parent / ".env"
if _ENV.exists():
    for _line in _ENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

os.environ["BITRIX_EVENT_DB"] = os.path.join(tempfile.mkdtemp(), "poll.sqlite3")

from bitrix_mcp.tools import events as T  # noqa: E402


def check(label: str, cond: bool) -> bool:
    print(("PASS " if cond else "FAIL ") + label)
    return cond


async def main() -> None:
    results = []

    first = json.loads(await T.b24_changes_since(feed="tasks", since="6h", limit=5))
    if first.get("error"):
        print("call failed:", first)
        sys.exit(1)
    print(f"first call: {first['count']} rows since {first['since']}, "
          f"total matching {first.get('total_matching')}, has_more={first['has_more']}")
    for row in first["items"][:3]:
        print(f"   {row.get('id')}  {row.get('changedDate')}  {str(row.get('title'))[:50]}")

    results.append(check("rows returned", first["count"] > 0))
    results.append(check("cursor advanced", first["cursor"]["advanced"] is True))
    results.append(check("cursor holds a timestamp", bool(first["cursor"]["current"])))
    results.append(check("feed marked verified", first["verified"] is True))

    # Second call with no 'since' must continue from the stored cursor.
    second = json.loads(await T.b24_changes_since(feed="tasks", limit=5))
    print(f"\nsecond call: since={second['since']} count={second['count']}")
    results.append(check("second call resumed from cursor",
                         second["since"] == str(first["cursor"]["current"])))
    results.append(check("cursor never moves backwards",
                         str(second["cursor"]["current"] or second["since"]) >= str(first["cursor"]["current"])))

    # Polled rows must be readable through the same history tool as pushed ones.
    hist = json.loads(await T.b24_events_history(entity="task", limit=5))
    results.append(check("polled rows land in the archive", hist["count"] > 0))
    if hist["count"]:
        sample = hist["events"][0]
        print(f"\narchive sample: {sample['event']} source={sample['source']} "
              f"entity={sample['entity']}/{sample['entity_id']}")
        results.append(check("archived with source=poll",
                             any(e["source"] == "poll" for e in hist["events"])))

    bad = json.loads(await T.b24_changes_since(feed="nonexistent"))
    results.append(check("unknown feed gives a helpful error",
                         bool(bad.get("error")) and "Available" in str(bad.get("message"))))

    print("\n" + ("ALL CLEAR" if all(results) else "SOMETHING BROKE"))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
