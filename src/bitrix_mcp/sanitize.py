"""Strip credentials out of anything on its way to the model or to disk.

Three real leaks found on a live portal, all of the same class:

1. `crm.documentgenerator.template.list` returns a `downloadMachine` field
   holding the FULL incoming-webhook URL - token included. One innocuous read
   put the credential in the transcript.
2. `b24_test_connection` returned `client.webhook` verbatim, so every
   diagnostic call echoed the token back.
3. httpx logs the request URL at INFO level, so raising the log level sends the
   token to the log file on every single call.

The first two are closed by running all tool output through `sanitize`; the
third by silencing the httpx logger (see `install_log_filter`).

Masking is deliberately shape-preserving: the portal host and user id survive,
only the secret is replaced. A redacted URL is still useful for diagnosis.
"""
from __future__ import annotations

import logging
import re
from typing import Any

MASK = "***REDACTED***"

# https://portal.tld/rest/<user_id>/<token>/... -> keep the shape, kill the token
WEBHOOK_URL = re.compile(r"(https?://[^/\s\"']+/rest/\d+/)([A-Za-z0-9]{8,})(/|\b)")

# ?token=... &auth=... &access_token=... (values may be percent-encoded)
QUERY_SECRET = re.compile(
    r"((?:^|[?&;])(?:token|auth|access_token|refresh_token|application_token)=)"
    r"([^&\s\"']+)",
    re.IGNORECASE,
)

# JSON keys whose value is a credential regardless of context.
SECRET_KEYS = {
    "auth", "token", "access_token", "refresh_token", "application_token",
    "webhook", "webhook_url", "client_secret", "downloadmachine",
    # Push&Pull: channel ids are signed bearer values - anyone holding one can
    # read that user's real-time event stream until it expires.
    "calltoken", "signature",
}

# Keys whose *nested* content is a channel descriptor: {"id": "...", "end": ...}
CHANNEL_KEYS = {"private", "shared"}


def scrub_text(s: str) -> str:
    s = WEBHOOK_URL.sub(lambda m: m.group(1) + MASK + m.group(3), s)
    s = QUERY_SECRET.sub(lambda m: m.group(1) + MASK, s)
    return s


def sanitize(obj: Any, _in_channel: bool = False) -> Any:
    """Recursively redact secrets in any JSON-like structure."""
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, list):
        return [sanitize(v, _in_channel) for v in obj]
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            key = k.lower() if isinstance(k, str) else k
            if key in SECRET_KEYS:
                out[k] = MASK if not isinstance(v, (dict, list)) else sanitize(v, _in_channel)
            elif _in_channel and key == "id":
                # Channel id inside pull.config.get - a bearer value, not a name.
                out[k] = MASK
            else:
                out[k] = sanitize(v, _in_channel or key in CHANNEL_KEYS)
        return out
    return obj


def _scrub_arg(value: Any) -> Any:
    """Redact one logging argument.

    httpx passes the URL as an `httpx.URL` object, not a string, so a
    str-only check silently misses the very leak this exists to stop. Any
    object whose text form looks like a credential is replaced by its scrubbed
    text; everything else is returned untouched so %d/%f formatting still works.
    """
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    try:
        text = str(value)
    except Exception:  # noqa: BLE001
        return value
    scrubbed = scrub_text(text)
    return scrubbed if scrubbed != text else value


class _SecretFilter(logging.Filter):
    """Redact secrets in log records that libraries build from URLs."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = scrub_text(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _scrub_arg(v) for k, v in record.args.items()}
                else:
                    record.args = tuple(_scrub_arg(a) for a in record.args)
        except Exception:  # noqa: BLE001 - logging must never break the caller
            pass
        return True


def install_log_filter(quiet_httpx: bool = True) -> None:
    """Keep credentials out of logs.

    httpx logs the full request URL at INFO, which for a webhook call means the
    token. Two defences, because either alone can be undone by a config change:
    raise httpx to WARNING, and attach a redacting filter in case it is lowered
    again elsewhere.
    """
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.addFilter(_SecretFilter())
        if quiet_httpx:
            logger.setLevel(logging.WARNING)
    logging.getLogger().addFilter(_SecretFilter())
