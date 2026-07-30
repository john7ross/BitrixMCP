"""Forward captured portal events to Telegram.

Events that only pile up in a database are events nobody reads. This ships them
to a chat or channel so a human sees them without asking an agent first.

Two design decisions worth stating, because both are reversals of the obvious:

* **Forwarding is tracked separately from ack.** Ack means "an agent processed
  this"; forwarded means "a human was told". Sharing one flag would let an agent
  run silently swallow the notifications.

* **The filter defaults to sending nothing.** A personal pull channel is chatty
  - 90 seconds of observation produced nine "message read" events and no work
  items. A forwarder that defaults to everything gets muted on day one, which is
  worse than one that asks to be configured. The user chooses what matters;
  `scripts/`-free configuration is available through the b24_telegram_* tools.

Filter syntax (one string, comma-separated, case-insensitive):
    tasks/*                 every pull-channel task event
    ONCRMDEAL*              every outgoing-webhook deal event
    tasks/task_update       one exact event
    entity:task             anything attached to a task, whatever the source
    source:poll             anything the poller found
    *                       everything (you asked for it)
A leading '-' excludes, and exclusions win: `*,-im/*` means everything but chat.
"""
from __future__ import annotations

import asyncio
import fnmatch
import html
import json
import logging
import time
from typing import Any, Iterable

import httpx

from .store import EventStore

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
SETTING_FILTER = "telegram.filter"
SETTING_CHAT = "telegram.chat_id"
SETTING_ENABLED = "telegram.enabled"

MAX_MESSAGE = 3500          # Telegram hard limit is 4096; leave room for markup
BATCH = 10                  # events per message, to stay far from rate limits
IDLE_SLEEP = 5.0
ERROR_SLEEP = 30.0


class TelegramError(RuntimeError):
    pass


def _proxy() -> str | None:
    """Explicit proxy for Telegram only.

    Kept separate from the portal connection on purpose: the portal is usually
    reachable directly while Telegram is not, so routing both through one proxy
    would break the half that already works.
    """
    import os

    for name in ("BITRIX_TELEGRAM_PROXY", "TELEGRAM_PROXY"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


# --------------------------------------------------------------------------
# filtering
# --------------------------------------------------------------------------

def parse_filter(spec: str | None) -> tuple[list[str], list[str]]:
    """Split a filter string into (include, exclude) pattern lists."""
    include: list[str] = []
    exclude: list[str] = []
    for raw in (spec or "").split(","):
        rule = raw.strip().lower()
        if not rule:
            continue
        if rule.startswith("-"):
            exclude.append(rule[1:].strip())
        else:
            include.append(rule)
    return include, exclude


def _matches(rule: str, event: dict) -> bool:
    name = str(event.get("event") or "").lower()
    if rule.startswith("entity:"):
        return str(event.get("entity") or "").lower() == rule[7:]
    if rule.startswith("source:"):
        return str(event.get("source") or "").lower() == rule[7:]
    return fnmatch.fnmatch(name, rule)


def should_forward(event: dict, spec: str | None) -> bool:
    """Exclusions win, and an empty filter forwards nothing."""
    include, exclude = parse_filter(spec)
    if not include:
        return False
    if any(_matches(rule, event) for rule in exclude):
        return False
    return any(_matches(rule, event) for rule in include)


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------

def _esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def format_event(event: dict, portal: str | None = None) -> str:
    """One event as a short HTML line for Telegram.

    Stage transitions get spelled out, because "task changed" is useless and
    "Ready for dev -> In progress" is the whole point.
    """
    name = event.get("event") or "?"
    params = (event.get("payload") or {}).get("params") or {}
    when = time.strftime("%H:%M", time.localtime(event.get("received_at") or time.time()))
    entity, entity_id = event.get("entity"), event.get("entity_id")

    head = f"<b>{_esc(name)}</b> <code>{_esc(when)}</code>"
    lines = [head]

    before = (params.get("BEFORE") or {}) if isinstance(params.get("BEFORE"), dict) else {}
    after = (params.get("AFTER") or {}) if isinstance(params.get("AFTER"), dict) else {}
    if before.get("STAGE") or after.get("STAGE"):
        lines.append(f"{_esc(before.get('STAGE', '?'))} → <b>{_esc(after.get('STAGE', '?'))}</b>")

    row = params.get("row") if isinstance(params.get("row"), dict) else None
    title = (row or {}).get("title") or params.get("title")
    if title:
        lines.append(_esc(str(title)[:200]))

    if entity and entity_id:
        link = _link(portal, entity, entity_id)
        lines.append(f"{_esc(entity)} <code>{_esc(entity_id)}</code>" + (f" — {link}" if link else ""))

    return "\n".join(lines)


def _link(portal: str | None, entity: str, entity_id: str) -> str:
    if not portal:
        return ""
    base = portal.rstrip("/")
    paths = {
        "task": f"{base}/company/personal/user/0/tasks/task/view/{entity_id}/",
        "deal": f"{base}/crm/deal/details/{entity_id}/",
        "lead": f"{base}/crm/lead/details/{entity_id}/",
        "contact": f"{base}/crm/contact/details/{entity_id}/",
        "company": f"{base}/crm/company/details/{entity_id}/",
    }
    url = paths.get(entity)
    return f'<a href="{html.escape(url, quote=True)}">открыть</a>' if url else ""


def build_messages(events: Iterable[dict], portal: str | None = None) -> list[str]:
    """Pack formatted events into as few messages as the size limit allows."""
    out: list[str] = []
    current: list[str] = []
    size = 0
    for event in events:
        block = format_event(event, portal)
        if current and size + len(block) + 2 > MAX_MESSAGE:
            out.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block) + 2
    if current:
        out.append("\n\n".join(current))
    return out


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------

async def api_call(token: str, method: str, payload: dict,
                   client: httpx.AsyncClient | None = None) -> dict:
    owned = client is None
    client = client or httpx.AsyncClient(timeout=20.0, proxy=_proxy())
    try:
        try:
            response = await client.post(API.format(token=token, method=method), json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            # Telegram is blocked on many corporate and national networks. The
            # underlying exception often has an empty message, so say plainly
            # what happened rather than surfacing a blank error.
            raise TelegramError(
                f"cannot reach api.telegram.org ({type(exc).__name__}). The name "
                f"resolves but the connection does not complete - this is a "
                f"network block, not a bad token. Set BITRIX_TELEGRAM_PROXY (or "
                f"HTTPS_PROXY) to a reachable proxy, or run the server where "
                f"Telegram is reachable."
            ) from exc
        try:
            body = response.json()
        except json.JSONDecodeError:
            raise TelegramError(f"HTTP {response.status_code}: non-JSON reply") from None
        if not body.get("ok"):
            raise TelegramError(
                f"{body.get('error_code')}: {body.get('description', 'unknown error')}")
        return body.get("result") or {}
    finally:
        if owned:
            await client.aclose()


async def verify(token: str, chat_id: str | None = None) -> dict:
    """Check the bot token, and that it can actually post to the chat."""
    me = await api_call(token, "getMe", {})
    result = {"bot": {"id": me.get("id"), "username": me.get("username")}}
    if chat_id:
        chat = await api_call(token, "getChat", {"chat_id": chat_id})
        result["chat"] = {"id": chat.get("id"), "type": chat.get("type"),
                          "title": chat.get("title") or chat.get("username")}
    return result


async def send(token: str, chat_id: str, text: str,
               client: httpx.AsyncClient | None = None) -> dict:
    return await api_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, client=client)


# --------------------------------------------------------------------------
# background worker
# --------------------------------------------------------------------------

class TelegramForwarder:
    """Drains the store into Telegram, honouring settings that may change live."""

    def __init__(self, store: EventStore, settings, portal: str | None = None) -> None:
        self.store = store
        self.settings = settings          # TelegramSettings-like: token/chat/filter/enabled
        self.portal = portal
        self._warned_target = False

    async def run(self, stop: asyncio.Event) -> None:
        async with httpx.AsyncClient(timeout=20.0, proxy=_proxy()) as client:
            while not stop.is_set():
                try:
                    sent = await self._drain(client)
                except TelegramError as exc:
                    log.warning("telegram: %s", exc)
                    await asyncio.sleep(ERROR_SLEEP)
                    continue
                except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                    log.warning("telegram: transport error %s", type(exc).__name__)
                    await asyncio.sleep(ERROR_SLEEP)
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("telegram: unexpected error")
                    await asyncio.sleep(ERROR_SLEEP)
                    continue
                if not sent:
                    await asyncio.sleep(IDLE_SLEEP)

    async def _drain(self, client: httpx.AsyncClient) -> int:
        if not self.settings.enabled or not self.settings.token or not self.settings.chat_id:
            await asyncio.sleep(IDLE_SLEEP)
            return 0
        # The target may have been changed at runtime; re-check every pass so a
        # redirect cannot outlive the tool call that attempted it.
        if not getattr(self.settings, "chat_allowed", True):
            if not self._warned_target:
                log.error("telegram: configured chat_id is not on the allowed "
                          "list - refusing to forward")
                self._warned_target = True
            await asyncio.sleep(ERROR_SLEEP)
            return 0
        self._warned_target = False
        pending = self.store.pending_forward(limit=BATCH)
        if not pending:
            return 0

        spec = self.settings.event_filter
        wanted = [e for e in pending if should_forward(e, spec)]
        for text in build_messages(wanted, self.portal):
            await send(self.settings.token, self.settings.chat_id, text, client=client)

        # Everything examined is marked, not just what was sent - otherwise
        # filtered-out events would be re-examined forever and block the queue.
        self.store.mark_forwarded([e["id"] for e in pending])
        if wanted:
            log.info("telegram: forwarded %d of %d event(s)", len(wanted), len(pending))
        return len(pending)
