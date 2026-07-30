"""Live Telegram check: real delivery, and the allowlist actually refusing.

Sends one real message to the configured chat, then attempts the attack the
allowlist exists to stop - an agent redirecting the feed to another chat id.

    python scripts/telegram_live_check.py
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

from bitrix_mcp.tools import telegram as TG  # noqa: E402


def check(label: str, cond: bool) -> bool:
    print(("PASS " if cond else "FAIL ") + label)
    return cond


async def main() -> None:
    r = []

    state = json.loads(await TG.b24_telegram_status())
    print(f"chat_id={state['chat_id']} allowed={state['allowed_users']} "
          f"allowed_ok={state['chat_allowed']}")
    print(f"filter={state['filter']}")
    print(f"backlog={state['backlog']}\n")
    r.append(check("token picked up from .env", state["token_configured"]))
    r.append(check("chat id configured", bool(state["chat_id"])))
    r.append(check("allowlist loaded", len(state["allowed_users"]) > 0))
    r.append(check("current target is allowed", state["chat_allowed"] is True))

    print("\n-- real delivery --")
    sent = json.loads(await TG.b24_telegram_test(
        message="<b>Bitrix24 MCP</b>\nПроверка связи. Пересылка событий настроена."))
    if sent.get("error"):
        print("   telegram said:", sent.get("message"))
    else:
        print(f"   bot @{sent['bot']['username']} -> chat {sent['chat']['id']} "
              f"({sent['chat']['type']})")
    r.append(check("message actually delivered", sent.get("sent") is True))

    print("\n-- the redirect the allowlist exists to stop --")
    hijack = json.loads(await TG.b24_telegram_configure(chat_id="999888777"))
    print("   refusal:", str(hijack.get("message"))[:120])
    r.append(check("redirect to an unlisted chat is refused", bool(hijack.get("error"))))

    after = json.loads(await TG.b24_telegram_status())
    r.append(check("refused redirect left no trace in settings",
                   after["chat_id"] == state["chat_id"]))
    r.append(check("no chat override was stored",
                   "telegram.chat_id" not in after["overrides"]))

    print("\n-- preview without sending --")
    preview = json.loads(await TG.b24_telegram_test(preview_only=True))
    print(f"   would forward {preview['would_forward']} of {preview['of_recent']} recent")
    r.append(check("preview runs without sending", "would_forward" in preview))

    print("\n" + ("ALL CLEAR" if all(r) else "SOMETHING BROKE"))
    sys.exit(0 if all(r) else 1)


if __name__ == "__main__":
    asyncio.run(main())
