"""Diagnose why Telegram delivery failed - connectivity vs credentials.

Prints the exception class as well as its text: httpx connection errors often
have an empty str(), which is exactly how a network block ends up looking like
"nothing went wrong".

    python scripts/tg_conn_probe.py
"""
from __future__ import annotations

import asyncio
import os
import socket
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

import httpx  # noqa: E402


async def main() -> None:
    host = "api.telegram.org"

    print("1. DNS")
    try:
        addrs = sorted({info[4][0] for info in socket.getaddrinfo(host, 443)})
        print(f"   {host} -> {addrs}")
    except OSError as exc:
        print(f"   FAILED: {type(exc).__name__}: {exc}")
        print("\n   DNS does not resolve - the name is blocked or there is no route.")
        return

    print("\n2. TCP :443")
    try:
        conn = socket.create_connection((host, 443), timeout=10)
        conn.close()
        print("   connected")
    except OSError as exc:
        print(f"   FAILED: {type(exc).__name__}: {exc}")
        print("\n   Name resolves but the port is unreachable - firewall or proxy.")
        return

    print("\n3. HTTPS getMe")
    token = os.environ.get("BITRIX_TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    if not token:
        print("   no token in environment")
        return
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/getMe", json={})
        body = response.json()
        if body.get("ok"):
            me = body["result"]
            print(f"   OK: bot @{me.get('username')} (id {me.get('id')})")
        else:
            print(f"   Telegram refused: {body.get('error_code')} "
                  f"{body.get('description')}")
    except Exception as exc:  # noqa: BLE001
        print(f"   FAILED: {type(exc).__name__}: {str(exc) or '(empty message)'}")
        print(f"   repr: {exc!r}")
        proxies = {k: v for k, v in os.environ.items()
                   if k.lower() in ("http_proxy", "https_proxy", "no_proxy")}
        print(f"   proxy environment: {proxies or 'none set'}")


if __name__ == "__main__":
    asyncio.run(main())
