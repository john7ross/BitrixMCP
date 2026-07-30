"""Probe: connect to the portal's Push&Pull channel and log whatever arrives.

Answers the question the outgoing webhook cannot: which real-time events can a
LOCAL server actually receive, using nothing but an outbound connection - the
same way the Bitrix24 desktop and mobile apps stay live behind NAT and VPN.

Long polling is used rather than WebSocket on purpose: it needs only `httpx`,
which is already a dependency, so this runs with no extra install.

    python scripts/pull_probe.py            # 120 seconds
    python scripts/pull_probe.py 600        # 10 minutes

Reads BITRIX_WEBHOOK_URL from the environment or .env. Never prints the
webhook, the channel ids or their signatures - those are credentials.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

FRAME = re.compile(r"#!NGINXNMS!#(.*?)#!NGINXNME!#", re.S)

# Windows consoles default to a legacy codepage; redirecting to a file then
# mangles every Cyrillic payload. Force UTF-8 on our own streams.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_webhook() -> str:
    url = os.environ.get("BITRIX_WEBHOOK_URL")
    if not url:
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("BITRIX_WEBHOOK_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not url:
        sys.exit("BITRIX_WEBHOOK_URL not found in environment or .env")
    return url.rstrip("/") + "/"


def get_config(webhook: str) -> dict:
    r = httpx.post(webhook + "pull.config.get.json", json={}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "result" not in data:
        sys.exit(f"pull.config.get failed: {data}")
    return data["result"]


def channel_param(cfg: dict) -> str:
    """Channel ids joined by '/', private first - as the official client does."""
    chans = cfg.get("channels") or {}
    ids = [chans[k]["id"] for k in ("private", "shared") if k in chans and chans[k].get("id")]
    if not ids:
        sys.exit("no channels in pull.config.get result")
    return "/".join(ids)


def show(raw: str) -> int:
    """Print each command from the channel. Returns how many were seen."""
    chunks = FRAME.findall(raw) or ([raw] if raw.strip() else [])
    seen = 0
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            print(f"  [unparsed] {chunk[:400]}")
            seen += 1
            continue
        for cmd in parsed if isinstance(parsed, list) else [parsed]:
            seen += 1
            text = cmd.get("text") if isinstance(cmd, dict) else None
            module = command = None
            if isinstance(text, dict):
                module, command = text.get("module_id"), text.get("command")
            print(f"\n[{time.strftime('%H:%M:%S')}] module={module} command={command}")
            print("  " + json.dumps(cmd, ensure_ascii=False)[:1500])
    return seen


def main() -> None:
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    cfg = get_config(load_webhook())
    server = cfg.get("server") or {}
    endpoint = server.get("long_pooling_secure") or server.get("long_polling")
    if not endpoint:
        sys.exit("push server has no long polling endpoint enabled")

    print(f"push server v{server.get('version')} mode={server.get('mode')} "
          f"enabled={server.get('server_enabled')}")
    print(f"endpoint: {endpoint}")
    print(f"channels: {len(cfg.get('channels') or {})} (ids hidden)")
    print(f"listening {duration}s - go do something in the portal\n", flush=True)

    params = {"CHANNEL_ID": channel_param(cfg), "format": "json"}
    deadline, total, polls = time.time() + duration, 0, 0
    with httpx.Client(timeout=httpx.Timeout(45.0, connect=15.0)) as client:
        while time.time() < deadline:
            polls += 1
            try:
                r = client.get(endpoint, params=params)
            except httpx.TimeoutException:
                continue                      # normal: no events in this window
            except httpx.HTTPError as exc:
                print(f"  transport error: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(3)
                continue
            if r.status_code == 304:
                # Normal long-poll outcome: the connection was held open and
                # nothing arrived in the channel during that window.
                print(f"  [{time.strftime('%H:%M:%S')}] idle window, no events", flush=True)
                continue
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}: {r.text[:300]}", flush=True)
                time.sleep(3)
                continue
            total += show(r.text)
            sys.stdout.flush()

    print(f"\ndone: {total} commands over {polls} polls in {duration}s")


if __name__ == "__main__":
    main()
