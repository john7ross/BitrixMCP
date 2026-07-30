"""Verify the three credential leaks are closed - against the live portal.

1. tool output: b24_test_connection used to echo the resolved webhook URL.
2. document generator: crm.documentgenerator.template.list returns
   `downloadMachine` with the full webhook URL inside.
3. logging: httpx logs the request URL at INFO, token included.

Passes only if the real token appears nowhere in tool output or in captured
logs. The token itself is never printed by this script.

    python scripts/leak_check.py
"""
from __future__ import annotations

import asyncio
import io
import logging
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

from bitrix_mcp.sanitize import install_log_filter, sanitize  # noqa: E402
from bitrix_mcp.tools.universal import b24_test_connection    # noqa: E402
from bitrix_mcp.tools.documents import b24_documentgenerator_templates  # noqa: E402


def secret() -> str:
    """The webhook token, taken from the URL - used only for comparison."""
    url = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
    return url.rsplit("/", 1)[-1]


def verdict(label: str, haystack: str, token: str) -> bool:
    leaked = token in haystack
    print(("FAIL " if leaked else "PASS ") + label)
    return not leaked


async def main() -> None:
    token = secret()
    if len(token) < 8:
        sys.exit("could not derive a token from BITRIX_WEBHOOK_URL")
    results = []

    out = await b24_test_connection()
    results.append(verdict("b24_test_connection output", out, token))
    print("   webhook field now reads:",
          next((ln.strip() for ln in out.splitlines() if "rest/" in ln), "(absent)"))

    tpl = await b24_documentgenerator_templates()
    results.append(verdict("documentgenerator template.list output", tpl, token))

    # Logging: force INFO back on, then confirm the filter still redacts.
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    install_log_filter()
    logging.getLogger("httpx").setLevel(logging.INFO)   # simulate a bad config
    await b24_test_connection()
    logging.getLogger().removeHandler(handler)
    results.append(verdict("httpx INFO logs", buf.getvalue(), token))

    # Pull-channel ids are bearer values too.
    fake = {"channels": {"private": {"id": "abc123.def456signature", "end": "2026-07-29"}}}
    results.append(verdict("pull channel id", str(sanitize(fake)), "abc123.def456signature"))

    print("\n" + ("ALL CLEAR" if all(results) else "LEAK STILL PRESENT"))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
