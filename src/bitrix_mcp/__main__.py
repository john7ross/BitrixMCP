"""Entry point. Selects transport from CLI args / environment.

Examples:
    bitrix-mcp                     # stdio (default) - for local agents
    bitrix-mcp --http              # Streamable HTTP on 127.0.0.1:8000/mcp
    bitrix-mcp --http --host 0.0.0.0 --port 5015
    BITRIX_TRANSPORT=http bitrix-mcp

Event feed (all optional, off unless configured):
    BITRIX_PULL_CHANNEL=1          # real-time via an OUTBOUND connection;
                                   # works on both transports, incl. behind VPN
    BITRIX_EVENT_TOKEN=...         # outgoing-webhook receiver (HTTP only)
    BITRIX_HTTP_SSL_CERT/KEY       # serve https (the SDK's own runner cannot)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os

from .config import config
from .sanitize import install_log_filter
from .server import mcp

log = logging.getLogger("bitrix_mcp")


def _open_store():
    """Lazily import the event package so an unconfigured server never touches it."""
    from .events.store import EventStore

    return EventStore(config.event_db, retention_days=config.event_retention_days)


def _mount_receiver(store) -> bool:
    """Attach the outgoing-webhook endpoint. Must run before the app is built."""
    from .events.receiver import register

    register(mcp, store, config.event_path, lambda: config.event_token)
    return True


@contextlib.asynccontextmanager
async def _pull_channel(store):
    """Run the Push&Pull listener alongside the server, if enabled.

    Failure to subscribe must not take the server down: the MCP tools stay
    useful without an event feed, so the error is logged and the server runs on.
    """
    if store is None or not config.pull_channel_enabled:
        yield
        return

    from .client import BitrixClient
    from .events.pullchannel import PullChannel, PullChannelUnavailable

    if not config.default_webhook:
        log.warning("pull channel requested but BITRIX_WEBHOOK_URL is not set - skipping")
        yield
        return

    channel = PullChannel(BitrixClient(config.default_webhook, timeout=config.timeout), store)
    stop = asyncio.Event()

    async def _run() -> None:
        try:
            await channel.run(stop)
        except PullChannelUnavailable as exc:
            log.warning("pull channel unavailable: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("pull channel stopped unexpectedly")

    task = asyncio.create_task(_run(), name="b24-pull-channel")
    log.info("pull channel started")
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        log.info("pull channel stopped")


@contextlib.asynccontextmanager
async def _telegram_forwarder(store):
    """Drain captured events to Telegram in the background, if configured.

    Runs regardless of transport: forwarding is what makes the event store
    useful to a human who is not currently talking to an agent.
    """
    if store is None or not config.telegram_token:
        yield
        return

    from .events.telegram import TelegramForwarder
    from .tools.telegram import settings as telegram_settings

    forwarder = TelegramForwarder(store, telegram_settings(), portal=config.portal_url)
    stop = asyncio.Event()

    async def _run() -> None:
        try:
            await forwarder.run(stop)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("telegram forwarder stopped unexpectedly")

    task = asyncio.create_task(_run(), name="b24-telegram")
    log.info("telegram forwarder started")
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        log.info("telegram forwarder stopped")


async def _serve_stdio(store) -> None:
    async with _pull_channel(store), _telegram_forwarder(store):
        await mcp.run_stdio_async()


async def _serve_http(store) -> None:
    """Serve Streamable HTTP, building uvicorn directly when TLS is requested.

    `mcp.run(transport="streamable-http")` constructs uvicorn.Config with only
    host/port/log_level - there is no way to pass a certificate through it. So
    when TLS is configured the app is taken and served here instead.
    """
    async with _pull_channel(store), _telegram_forwarder(store):
        if not (config.ssl_certfile and config.ssl_keyfile):
            await mcp.run_streamable_http_async()
            return

        import uvicorn

        server = uvicorn.Server(uvicorn.Config(
            mcp.streamable_http_app(),
            host=mcp.settings.host,
            port=mcp.settings.port,
            log_level=mcp.settings.log_level.lower(),
            ssl_certfile=config.ssl_certfile,
            ssl_keyfile=config.ssl_keyfile,
        ))
        log.info("serving https on %s:%s", mcp.settings.host, mcp.settings.port)
        await server.serve()


def main() -> None:
    parser = argparse.ArgumentParser(prog="bitrix-mcp", description="Bitrix24 MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http"],
        default=os.environ.get("BITRIX_TRANSPORT", "stdio"),
        help="Transport to run (default: stdio, or BITRIX_TRANSPORT).",
    )
    parser.add_argument("--http", action="store_true", help="Shortcut for --transport http.")
    parser.add_argument("--host", default=os.environ.get("BITRIX_HTTP_HOST", "127.0.0.1"),
                        help="HTTP bind host (default 127.0.0.1; use 0.0.0.0 to expose on the network).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BITRIX_HTTP_PORT", "8000")),
                        help="HTTP port (default 8000).")
    args = parser.parse_args()

    # Before anything can make a request: httpx logs the full URL at INFO, and
    # for a webhook call that URL contains the token. Install this first so no
    # startup request can beat it to the log file.
    install_log_filter()

    transport = "streamable-http" if (args.http or args.transport in ("http", "streamable-http")) else "stdio"

    # The store is opened only when some event path is actually turned on, so a
    # plain deployment stays exactly as stateless as it was before.
    wants_receiver = bool(config.event_token) and transport == "streamable-http"
    store = _open_store() if (wants_receiver or config.pull_channel_enabled
                              or config.telegram_token) else None

    if wants_receiver:
        _mount_receiver(store)
        log.info("outgoing-webhook receiver mounted at %s", config.event_path)
    elif config.event_token and transport == "stdio":
        log.warning("BITRIX_EVENT_TOKEN is set but the receiver needs --http; "
                    "use BITRIX_PULL_CHANNEL=1 for events over stdio")

    if transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # Stateless JSON responses scale better and avoid the fragile long-lived
        # SSE session issues that plagued the previous deployment.
        mcp.settings.json_response = True
        mcp.settings.stateless_http = True
        asyncio.run(_serve_http(store))
    else:
        asyncio.run(_serve_stdio(store))


if __name__ == "__main__":
    main()
