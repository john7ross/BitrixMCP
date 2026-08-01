"""Telegram forwarding tools - let the user decide what reaches their phone.

Settings resolve as: agent override (stored in the event database) > .env
baseline. The .env file stays the operator's record of intent and is never
rewritten underneath a running server; anything changed through these tools
lives in the database and can be cleared to fall back to .env.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from pydantic import Field

from ..config import config
from ..events.telegram import (
    SETTING_CHAT,
    SETTING_ENABLED,
    SETTING_FILTER,
    TelegramError,
    build_messages,
    parse_filter,
    send,
    should_forward,
    verify,
)
from ..runtime import READ, WRITE, err, ok
from ..server import mcp
from .events import get_store

try:
    from mcp.server.mcpserver import Context
except Exception:  # pragma: no cover
    Context = Any  # type: ignore

# The portal's pull channel emits bookkeeping alongside real activity:
# `tasks/user_counter` and `tasks/user_efficiency_counter` fire on almost every
# action and carry nothing a human needs. Confirmed end to end - one task
# created, renamed and commented produced four counter messages against four
# useful ones, which is how a forwarder gets muted on day one. `tasks/*` alone
# catches them, so every task-bearing preset excludes them by name.
NOISE = "-*counter*"

PRESETS = {
    "work": f"tasks/*,ONTASK*,ONCRM*,poll/*,-im/*,{NOISE}",
    "tasks": f"tasks/*,ONTASK*,poll/task,{NOISE}",
    "crm": "ONCRM*,poll/deal,poll/lead,poll/contact,poll/company",
    # Deliberately unfiltered: "everything" must mean everything, counters
    # included, or it cannot be used to find out what the portal actually sends.
    "everything": "*",
    "nothing": "",
}


class _Settings:
    """Effective settings: database override first, .env second."""

    def __init__(self, store) -> None:
        self.store = store

    def _resolve(self, key: str, fallback):
        stored = self.store.setting_get(key)
        return stored if stored is not None else fallback

    @property
    def token(self) -> str | None:
        return config.telegram_token

    @property
    def chat_id(self) -> str | None:
        return self._resolve(SETTING_CHAT, config.telegram_chat_id) or None

    @property
    def allowed_users(self) -> list[str]:
        """Read from the environment only - never from agent-writable settings."""
        return config.telegram_allowed_users

    @property
    def chat_allowed(self) -> bool:
        """Is the current target permitted? Empty allowlist means unrestricted."""
        allowed = self.allowed_users
        if not allowed:
            return True
        return str(self.chat_id or "").lstrip("@") in {a.lstrip("@") for a in allowed}

    def require_allowed_target(self, chat_id: str | None = None) -> None:
        """Raise unless the target is on the allowlist.

        The allowlist exists because b24_telegram_configure can change chat_id,
        and the agent calling it reads portal content. Text planted in a task or
        a comment could ask for the feed to be redirected; this refuses.
        """
        allowed = self.allowed_users
        if not allowed:
            return
        target = str(chat_id if chat_id is not None else self.chat_id or "").lstrip("@")
        if target not in {a.lstrip("@") for a in allowed}:
            raise TelegramError(
                f"chat_id {target or '(empty)'} is not in the allowed list. "
                f"Forwarding targets are fixed in .env "
                f"(BITRIX_TELEGRAM_ALLOWED_USERS) and cannot be changed from a "
                f"conversation. Edit that file if this is a legitimate new target."
            )

    @property
    def event_filter(self) -> str:
        return self._resolve(SETTING_FILTER, config.telegram_filter) or ""

    @property
    def enabled(self) -> bool:
        stored = self.store.setting_get(SETTING_ENABLED)
        if stored is not None:
            return stored.strip().lower() in ("1", "true", "yes", "on")
        return config.telegram_enabled

    def describe(self) -> dict:
        return {
            "enabled": self.enabled,
            "token_configured": bool(self.token),
            "chat_id": self.chat_id,
            "allowed_users": self.allowed_users,
            "chat_allowed": self.chat_allowed,
            "filter": self.event_filter,
            "filter_parsed": dict(zip(("include", "exclude"),
                                      parse_filter(self.event_filter))),
            "overrides": self.store.setting_all(),
        }


def settings():
    return _Settings(get_store())


@mcp.tool(name="b24_telegram_status", annotations=READ)
async def b24_telegram_status(
    ctx: Context | None = None,
) -> str:
    """Show how event forwarding to Telegram is currently configured.

    Start here when nothing arrives in the chat: this separates "not configured"
    from "configured but filtered out" from "nothing happened".

    Returns:
        JSON: {"enabled","token_configured","chat_id","filter","filter_parsed",
        "overrides", "backlog": <events not yet examined>, "presets": {...}}.
        An empty filter forwards nothing - that is the default, on purpose.
    """
    try:
        store = get_store()
        state = settings().describe()
        state["backlog"] = store.forward_backlog()
        state["presets"] = PRESETS
        state["filter_syntax"] = (
            "comma-separated; 'tasks/*', 'ONCRMDEAL*', 'entity:task', "
            "'source:poll', '*' for all; prefix '-' excludes and exclusions win"
        )
        return ok(state)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_telegram_configure", annotations=WRITE)
async def b24_telegram_configure(
    events: Annotated[Optional[str], Field(default=None, description="Which events to forward. A preset name ('work', 'tasks', 'crm', 'everything', 'nothing') or a filter string like 'tasks/*,-im/*'.")] = None,
    chat_id: Annotated[Optional[str], Field(default=None, description="Target chat: numeric user id, channel id (-100...), or @channelname.")] = None,
    enabled: Annotated[Optional[bool], Field(default=None, description="Turn forwarding on or off without losing the rest of the settings.")] = None,
    reset: Annotated[bool, Field(default=False, description="Clear all overrides and fall back to the .env settings.")] = False,
    ctx: Context | None = None,
) -> str:
    """Change what gets forwarded to Telegram, without editing files.

    Use this to help someone set forwarding up: ask what they want to hear
    about, translate it into a filter (or pick a preset), set it, then call
    b24_telegram_test so they see it working.

    The bot token is NOT settable here - a credential belongs in .env
    (BITRIX_TELEGRAM_TOKEN), not in a chat transcript.

    Args:
        events: preset name or filter string. 'nothing' silences forwarding
            while keeping the rest of the configuration.
        chat_id: where to post. Find it by messaging the bot and reading
            getUpdates, or by adding the bot to a channel as administrator.
        reset: drop overrides so the .env values apply again.

    Returns:
        JSON: the resulting configuration, plus "changed": [...] listing what
        this call actually modified.
    """
    try:
        store = get_store()
        changed: list[str] = []

        if reset:
            for key in (SETTING_FILTER, SETTING_CHAT, SETTING_ENABLED):
                if store.setting_delete(key):
                    changed.append(f"cleared {key}")

        if events is not None:
            spec = PRESETS.get(events.strip().lower(), events.strip())
            store.setting_set(SETTING_FILTER, spec)
            changed.append(f"filter = {spec!r}" if spec else "filter = '' (forwards nothing)")

        if chat_id is not None:
            # Refuse before storing: a rejected redirect must leave no trace in
            # the settings for a later call to pick up.
            settings().require_allowed_target(chat_id.strip())
            store.setting_set(SETTING_CHAT, chat_id.strip())
            changed.append(f"chat_id = {chat_id.strip()}")

        if enabled is not None:
            store.setting_set(SETTING_ENABLED, "1" if enabled else "0")
            changed.append(f"enabled = {enabled}")

        state = settings().describe()
        state["changed"] = changed
        if not state["token_configured"]:
            state["next_step"] = ("Set BITRIX_TELEGRAM_TOKEN in .env and restart - "
                                  "the token is not accepted through this tool.")
        elif not state["chat_id"]:
            state["next_step"] = "Set chat_id, then run b24_telegram_test."
        elif not state["filter"]:
            state["next_step"] = ("Filter is empty, so nothing will be forwarded. "
                                  "Pick a preset, e.g. events='work'.")
        else:
            state["next_step"] = "Run b24_telegram_test to confirm delivery."
        return ok(state)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_telegram_test", annotations=WRITE)
async def b24_telegram_test(
    message: Annotated[Optional[str], Field(default=None, description="Custom text. Omitted: sends a short check-in message.")] = None,
    preview_only: Annotated[bool, Field(default=False, description="Do not send; show which recent events the current filter would forward.")] = False,
    ctx: Context | None = None,
) -> str:
    """Verify the bot and chat work, or preview what the filter would forward.

    `preview_only=true` answers "would I get spammed?" without sending anything:
    it replays recent captured events through the current filter and shows the
    messages that would have been produced.

    Returns:
        JSON on success: {"sent": true, "bot": {...}, "chat": {...}}.
        With preview_only: {"would_forward": <int>, "of_recent": <int>,
        "sample": ["..."]}.
        On failure the Telegram error is returned verbatim - 400 usually means a
        wrong chat_id, 403 means the bot was never added to that chat.
    """
    try:
        store = get_store()
        state = settings()

        if preview_only:
            recent = store.history(limit=50)
            wanted = [e for e in recent if should_forward(e, state.event_filter)]
            return ok({
                "would_forward": len(wanted),
                "of_recent": len(recent),
                "filter": state.event_filter,
                "sample": build_messages(wanted[:5], config.portal_url)[:1],
            })

        if not state.token:
            raise TelegramError(
                "No bot token. Set BITRIX_TELEGRAM_TOKEN in .env and restart.")
        if not state.chat_id:
            raise TelegramError(
                "No chat_id. Set one with b24_telegram_configure(chat_id=...).")
        state.require_allowed_target()

        info = await verify(state.token, state.chat_id)
        text = message or (
            "<b>Bitrix24 MCP</b>\nForwarding is configured.\n"
            f"Filter: <code>{state.event_filter or '(empty - nothing will be sent)'}</code>"
        )
        await send(state.token, state.chat_id, text)
        return ok({"sent": True, **info, "filter": state.event_filter})
    except TelegramError as exc:
        return err(exc)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
