"""Pull-channel client: real-time events over an OUTBOUND connection only.

This is the same mechanism the Bitrix24 mobile and desktop apps use to stay
live behind carrier NAT, corporate firewalls and VPN: the client opens the
connection to the portal's push server and holds it. Nothing ever connects
inbound, so this works where an outgoing webhook cannot - no public URL, no
port forwarding, no firewall rule, no administrator rights.

Verified against a live portal: `pull.config.get` is reachable with a plain
incoming webhook. The official documentation points at
`pull.application.config.get` instead, which requires application context and
fails for a webhook with WRONG_AUTH_TYPE - hence the deliberate choice here.

Long polling rather than WebSocket: it needs only httpx, already a dependency.

Known limits, stated rather than hidden:
  * The channel is personal to the webhook's user - it carries what that user's
    interface would receive, which is NOT the documented outgoing-webhook event
    catalogue. Observed live: tasks/task_update (with stage transitions),
    tasks/itemUpdated, and the im/* family.
  * `module_id` / `command` pairs are an internal UI protocol, not a versioned
    REST contract. They can change between portal versions without notice.
  * Channels expire (12 hours on the observed portal) and are re-fetched.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..client import BitrixClient
from .store import EventStore

log = logging.getLogger(__name__)

# Servers below v4 wrap commands in this envelope; v4 with &format=json does not.
FRAME = re.compile(r"#!NGINXNMS!#(.*?)#!NGINXNME!#", re.S)

REFRESH_MARGIN = 600.0   # re-fetch the channel this long before it expires
ERROR_BACKOFF = 5.0
MAX_BACKOFF = 60.0


class PullChannelUnavailable(RuntimeError):
    """The portal has no usable push server (module off, or long polling off)."""


def parse_commands(raw: str) -> list[dict]:
    """Turn one long-poll response body into a list of command dicts."""
    chunks = FRAME.findall(raw) or ([raw] if raw.strip() else [])
    out: list[dict] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            log.warning("pull: unparsable chunk (%d bytes), skipped", len(chunk))
            continue
        for cmd in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(cmd, dict):
                out.append(cmd)
    return out


def event_name(cmd: dict) -> str:
    """'tasks/task_update' - module and command, matching how the portal names it."""
    text = cmd.get("text")
    if isinstance(text, dict):
        module = text.get("module_id") or "unknown"
        command = text.get("command") or "unknown"
        return f"{module}/{command}"
    return "unknown/unknown"


def flatten(cmd: dict) -> dict:
    """Store the command with its params lifted to the top level.

    `extract_entity` looks for `params`, and a caller reading history wants the
    event body, not the transport envelope - but the envelope is kept too, since
    the whole point of this store is that context is not thrown away.
    """
    text = cmd.get("text") if isinstance(cmd.get("text"), dict) else {}
    return {
        "module_id": text.get("module_id"),
        "command": text.get("command"),
        "params": text.get("params") if isinstance(text.get("params"), dict) else {},
        "extra": text.get("extra"),
        "mid": cmd.get("mid"),
        "channel": cmd.get("channel"),
        "time": cmd.get("time"),
    }


class PullChannel:
    """Holds one long-poll loop against the portal's push server."""

    def __init__(self, client: BitrixClient, store: EventStore, *,
                 timeout: float = 45.0) -> None:
        self.client = client
        self.store = store
        self.timeout = timeout
        self._config: dict[str, Any] | None = None
        self._expires_at: float = 0.0
        self._last_mid: str | None = None

    # -- configuration ------------------------------------------------------

    async def load_config(self) -> dict[str, Any]:
        cfg = await self.client.call_result("pull.config.get", {})
        if not isinstance(cfg, dict):
            raise PullChannelUnavailable("pull.config.get returned no configuration")
        server = cfg.get("server") or {}
        if not server.get("server_enabled"):
            raise PullChannelUnavailable("push server is disabled on this portal")
        if not (server.get("long_pooling_secure") or server.get("long_polling")):
            raise PullChannelUnavailable("push server has no long polling endpoint")
        self._config = cfg
        self._expires_at = self._earliest_expiry(cfg)
        return cfg

    @staticmethod
    def _earliest_expiry(cfg: dict) -> float:
        """Unix time when the first channel dies. 0 when the portal says nothing.

        `end` arrives as an ISO timestamp on v4 portals, but older ones send a
        unix time - accept both rather than silently treating an unparsed value
        as "never expires", which would disable the refresh entirely.
        """
        ends: list[float] = []
        for chan in (cfg.get("channels") or {}).values():
            raw = chan.get("end") if isinstance(chan, dict) else None
            if not raw:
                continue
            try:
                ends.append(float(raw))
                continue
            except (TypeError, ValueError):
                pass
            try:
                parsed = datetime.fromisoformat(str(raw))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                ends.append(parsed.timestamp())
            except ValueError:
                log.warning("pull: unparsable channel expiry %r", raw)
        return min(ends) if ends else 0.0

    @staticmethod
    def channel_param(cfg: dict) -> str:
        chans = cfg.get("channels") or {}
        ids = [chans[k]["id"] for k in ("private", "shared")
               if isinstance(chans.get(k), dict) and chans[k].get("id")]
        if not ids:
            raise PullChannelUnavailable("pull.config.get returned no channel ids")
        return "/".join(ids)

    async def _fresh_config(self) -> dict[str, Any]:
        if self._config is None or (
            self._expires_at and time.time() > self._expires_at - REFRESH_MARGIN
        ):
            log.info("pull: fetching channel configuration")
            return await self.load_config()
        return self._config

    # -- the loop -----------------------------------------------------------

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Poll until `stop` is set. Reconnects on transport errors."""
        stop = stop or asyncio.Event()
        backoff = ERROR_BACKOFF
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15.0)) as http:
            while not stop.is_set():
                try:
                    stored = await self._poll_once(http)
                    backoff = ERROR_BACKOFF
                    if stored:
                        log.debug("pull: stored %d event(s)", stored)
                except PullChannelUnavailable:
                    raise
                except (httpx.TimeoutException, httpx.HTTPError) as exc:
                    log.warning("pull: transport error (%s), retrying in %.0fs",
                                type(exc).__name__, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                except Exception:  # noqa: BLE001 - the loop must outlive one bad event
                    log.exception("pull: unexpected error, retrying in %.0fs", backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)

    async def _poll_once(self, http: httpx.AsyncClient) -> int:
        cfg = await self._fresh_config()
        server = cfg.get("server") or {}
        endpoint = server.get("long_pooling_secure") or server.get("long_polling")
        params: dict[str, Any] = {"CHANNEL_ID": self.channel_param(cfg), "format": "json"}
        if self._last_mid:
            params["mid"] = self._last_mid

        resp = await http.get(endpoint, params=params)
        if resp.status_code == 304:
            return 0                       # held open, nothing arrived - normal
        if resp.status_code == 403:
            # Channel expired or was revoked: drop it and re-fetch next round.
            log.info("pull: channel rejected, refreshing configuration")
            self._config = None
            return 0
        resp.raise_for_status()

        stored = 0
        for cmd in parse_commands(resp.text):
            mid = cmd.get("mid")
            if mid:
                self._last_mid = str(mid)
            name = event_name(cmd)
            if self.store.put(name, cmd.get("time"), flatten(cmd),
                              source="pull", dedup_on=mid or cmd.get("id")) is not None:
                stored += 1
        return stored
