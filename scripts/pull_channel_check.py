"""End-to-end check of the pull-channel path: connect, receive, store, query.

Runs the real PullChannel against the real portal for a few seconds, then reads
back what landed in SQLite - including the history query, which is the reason
full payloads are kept rather than ids.

    python scripts/pull_channel_check.py [seconds]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# The entry point loads .env; a standalone script does not, so do it here
# BEFORE importing config - config reads the environment at property access.
_ENV = Path(__file__).resolve().parent.parent / ".env"
if _ENV.exists():
    for _line in _ENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from bitrix_mcp.client import BitrixClient          # noqa: E402
from bitrix_mcp.config import config                # noqa: E402
from bitrix_mcp.events.pullchannel import PullChannel  # noqa: E402
from bitrix_mcp.events.store import EventStore      # noqa: E402


def webhook() -> str:
    for attr in ("default_webhook", "webhook", "webhook_url", "bitrix_webhook"):
        val = getattr(config, attr, None)
        if isinstance(val, str) and val.strip():
            return val
    val = os.environ.get("BITRIX_WEBHOOK_URL")
    if val:
        return val
    sys.exit(f"no webhook found; config exposes: "
             f"{[a for a in dir(config) if not a.startswith('_')]}")


async def main() -> None:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    db = os.path.join(tempfile.mkdtemp(), "check.sqlite3")
    store = EventStore(db)
    channel = PullChannel(BitrixClient(webhook()), store)

    cfg = await channel.load_config()
    server = cfg.get("server") or {}
    print(f"push server v{server.get('version')} enabled={server.get('server_enabled')}")
    expiry = channel._expires_at
    if expiry:
        print(f"channel expires in {(expiry - time.time()) / 3600:.1f} h")
    print(f"listening {seconds}s - do something in the portal\n", flush=True)

    stop = asyncio.Event()
    task = asyncio.create_task(channel.run(stop))
    await asyncio.sleep(seconds)
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    rows = store.poll(limit=100, include_acked=True)
    print(f"\nstored {len(rows)} event(s)")
    for r in rows:
        print(f"  #{r['id']:<3} {r['event']:<24} source={r['source']:<7} "
              f"entity={r['entity']}/{r['entity_id']}")

    tasks = [r for r in rows if r["entity"] == "task"]
    if tasks:
        tid = tasks[0]["entity_id"]
        hist = store.history(entity="task", entity_id=tid)
        print(f"\nhistory for task {tid}: {len(hist)} event(s)")
        for h in hist:
            params = h["payload"].get("params") or {}
            before = (params.get("BEFORE") or {}).get("STAGE")
            after = (params.get("AFTER") or {}).get("STAGE")
            if before or after:
                print(f"  {h['event']}: {before!r} -> {after!r}")
            else:
                print(f"  {h['event']}: {json.dumps(params, ensure_ascii=False)[:120]}")
    else:
        print("\nno task events captured - move a card to exercise the history path")

    print(f"\nstats: {store.stats()}")


if __name__ == "__main__":
    asyncio.run(main())
