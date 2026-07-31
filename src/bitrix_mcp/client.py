"""Async HTTP client for the Bitrix24 REST API over incoming webhooks.

Design goals (directly informed by the documented bugs of the previous wrapper):

* **Parameters go as a JSON POST body.** Bitrix (PHP) parses nested arrays
  (``filter``, ``select``, ``order``, ``fields``) natively from JSON. The old
  wrapper's "filter is silently ignored" bugs (#6/#8) came from mis-encoded
  query strings — sending JSON avoids that class of bug entirely.
* **Errors are never swallowed.** A Bitrix ``error`` / ``error_description`` (or
  a non-2xx status) always raises :class:`BitrixError`. We never convert an
  access error into an empty "0 results" success (old bug #12).
* **Real pagination.** List helpers use the documented ``start`` / ``next`` /
  ``total`` fields and expose them honestly, with a hard page cap.
* **batch** builds PHP-style ``http_build_query`` command strings, the one place
  bracket encoding is genuinely required.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

import httpx

# ---------------------------------------------------------------------------
# Webhook handling
# ---------------------------------------------------------------------------

_WEBHOOK_RE = re.compile(r"^https?://[^/]+/rest/\d+/[A-Za-z0-9]+/?$")


def normalize_webhook(url: str) -> str:
    """Validate and normalize an incoming-webhook URL.

    Accepts ``https://portal/rest/<user_id>/<token>/`` (trailing slash optional).
    Raises ValueError on an obviously malformed URL so the agent gets an
    actionable message instead of a confusing 404 from Bitrix.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("Empty webhook URL.")
    if not _WEBHOOK_RE.match(url):
        raise ValueError(
            "Malformed webhook URL. Expected 'https://<portal>/rest/<user_id>/<token>/' "
            f"(e.g. https://your-portal.bitrix24.ru/rest/1/xxxxxxxx/). Got: {url!r}"
        )
    return url if url.endswith("/") else url + "/"


# ---------------------------------------------------------------------------
# Read-only guard: classify a REST method as write vs read
# ---------------------------------------------------------------------------

# Final path-segment verbs that mutate portal state. Anything not matching is
# treated as read (so the guard never blocks a harmless read it doesn't know).
_WRITE_VERBS = {
    "add", "update", "delete", "del", "set", "import", "bind", "unbind",
    "attach", "detach", "move", "copy", "rename", "complete", "start",
    "pause", "defer", "renew", "approve", "disapprove", "commit", "upload",
    "register", "finalize", "activate", "deactivate", "markasread",
    "markasunread", "send", "post", "invite", "kick", "join", "leave",
    "create", "save", "prepare", "addfromtext", "setdata", "voximplant",
    "unlike", "like", "follow", "unfollow", "read", "assign", "book",
}


# Prefixes that unambiguously begin a write method's last segment, catching
# compound verbs the exact set misses (uploadfile, addsubfolder, updatestage,
# deletetree, createchild, ...). No read method starts with these.
_WRITE_PREFIXES = ("add", "update", "delete", "upload", "create", "import", "register")


def is_write_method(method: str) -> bool:
    """Heuristic: does this REST method mutate portal data?

    Based on the last dotted segment (e.g. ``crm.deal.add`` -> ``add``): an exact
    verb match, or an unambiguous write prefix (so ``disk.folder.uploadfile`` and
    ``disk.folder.addsubfolder`` are caught too). Deliberately conservative for
    everything else: unknown verbs are treated as *reads* so the read-only guard
    never blocks legitimate reads. Typed write tools pass an explicit override, so
    this heuristic only governs ``b24_call`` / ``b24_batch``.
    """
    if not method:
        return False
    last = method.strip().lower().rstrip("/").split(".")[-1]
    return last in _WRITE_VERBS or last.startswith(_WRITE_PREFIXES)


# ---------------------------------------------------------------------------
# PHP-style query builder (only needed for batch command strings)
# ---------------------------------------------------------------------------

def php_query(data: dict[str, Any]) -> str:
    """Serialize a nested dict the way PHP's http_build_query does.

    ``{"filter": {">ID": 1}, "select": ["ID", "TITLE"]}`` becomes
    ``filter%5B%3EID%5D=1&select%5B0%5D=ID&select%5B1%5D=TITLE`` — which
    Bitrix's PHP ``parse_str`` decodes back into the correct nested arrays.
    """
    pairs: list[tuple[str, str]] = []

    def _encode(key: str, val: Any) -> None:
        if isinstance(val, dict):
            for k, v in val.items():
                _encode(f"{key}[{k}]", v)
        elif isinstance(val, (list, tuple)):
            for i, v in enumerate(val):
                _encode(f"{key}[{i}]", v)
        elif isinstance(val, bool):
            pairs.append((key, "1" if val else "0"))
        elif val is None:
            pairs.append((key, ""))
        else:
            pairs.append((key, str(val)))

    for k, v in (data or {}).items():
        _encode(k, v)
    return urlencode(pairs)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class BitrixError(Exception):
    """A Bitrix REST call failed. Carries the machine-readable error code so
    agents can branch on it (e.g. ACCESS_DENIED vs ERROR_METHOD_NOT_FOUND)."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        method: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.method = method
        self.status = status

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": True,
            "code": self.code,
            "method": self.method,
            "status": self.status,
            "message": str(self),
        }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class BitrixClient:
    """Thin async wrapper around a single incoming webhook."""

    def __init__(self, webhook: str, *, timeout: float = 60.0) -> None:
        self.webhook = normalize_webhook(webhook)
        self.timeout = timeout

    # -- low level ----------------------------------------------------------

    async def _post(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        url = f"{self.webhook}{method.strip().strip('/')}.json"
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            try:
                resp = await http.post(url, json=params or {})
            except httpx.TimeoutException as exc:
                raise BitrixError(
                    f"Request to '{method}' timed out after {self.timeout}s. "
                    "Narrow the filter or reduce the page size and retry.",
                    code="TIMEOUT",
                    method=method,
                ) from exc
            except httpx.HTTPError as exc:
                raise BitrixError(
                    f"HTTP transport error calling '{method}': {exc}",
                    code="TRANSPORT_ERROR",
                    method=method,
                ) from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise BitrixError(
                f"Non-JSON response (HTTP {resp.status_code}) from '{method}': "
                f"{resp.text[:300]}",
                code="BAD_RESPONSE",
                method=method,
                status=resp.status_code,
            ) from exc

        # Bitrix reports method-level failures via {error, error_description},
        # usually with HTTP 400. Surface both; never swallow into empty success.
        # Note: Bitrix sometimes sends "error": "" (empty string) alongside a
        # real error_description (e.g. plain "Access denied." on a missing
        # scope) — check key presence, not truthiness, or that case falls
        # through to the generic HTTP_xxx branch below and loses its code.
        if isinstance(data, dict) and ("error" in data or "error_description" in data):
            code = data.get("error") or None
            raise BitrixError(
                data.get("error_description") or code or "Unknown Bitrix error",
                code=str(code) if code else None,
                method=method,
                status=resp.status_code,
            )
        if resp.status_code >= 400:
            raise BitrixError(
                f"HTTP {resp.status_code} from '{method}': {str(data)[:300]}",
                code=f"HTTP_{resp.status_code}",
                method=method,
                status=resp.status_code,
            )
        return data

    # -- public API ---------------------------------------------------------

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call any REST method. Returns the full parsed response dict with keys
        such as ``result``, ``total``, ``next``, ``time`` (whichever Bitrix sent)."""
        return await self._post(method, params)

    async def call_result(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Call a method and return just its ``result`` payload."""
        data = await self.call(method, params)
        return data.get("result")

    async def call_list(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        start: int = 0,
        fetch_all: bool = False,
        max_pages: int = 40,
    ) -> dict[str, Any]:
        """Call a *.list-style method with honest pagination.

        Returns an envelope::

            {
              "items": [...],
              "count": <items on this response>,
              "total": <total matching, if Bitrix reported it>,
              "start": <offset used>,
              "next": <offset for the next page, or None>,
              "has_more": <bool>,
              "truncated": <bool: hit the fetch_all page cap>,
            }
        """
        params = dict(params or {})
        params["start"] = start
        data = await self.call(method, params)
        items = _extract_list(data.get("result"))
        total = data.get("total")
        nxt = data.get("next")

        if not fetch_all:
            return {
                "items": items,
                "count": len(items),
                "total": total,
                "start": start,
                "next": nxt,
                "has_more": nxt is not None,
                "truncated": False,
            }

        pages = 1
        truncated = False
        while nxt is not None:
            if pages >= max_pages:
                truncated = True
                break
            page_params = dict(params)
            page_params["start"] = nxt
            page = await self.call(method, page_params)
            items.extend(_extract_list(page.get("result")))
            nxt = page.get("next")
            pages += 1

        return {
            "items": items,
            "count": len(items),
            "total": total,
            "start": start,
            "next": nxt,
            "has_more": nxt is not None,
            "truncated": truncated,
        }

    async def batch(
        self,
        commands: dict[str, tuple[str, dict[str, Any] | None]],
        *,
        halt: bool = False,
    ) -> dict[str, Any]:
        """Run up to 50 commands in one hit.

        ``commands`` maps a caller-chosen key to ``(method, params)``. Command
        strings are built with PHP-style encoding and support Bitrix's
        ``$result[...]`` back-references (pass them literally inside params).
        Returns ``{"result": {...}, "result_error": {...}, "result_next": {...},
        "result_total": {...}}`` keyed the same way.
        """
        if len(commands) > 50:
            raise BitrixError(
                f"batch accepts at most 50 commands, got {len(commands)}.",
                code="BATCH_TOO_LARGE",
                method="batch",
            )
        cmd: dict[str, str] = {}
        for key, (method, params) in commands.items():
            qs = php_query(params or {})
            cmd[key] = f"{method}?{qs}" if qs else method
        data = await self.call("batch", {"halt": 1 if halt else 0, "cmd": cmd})
        result = data.get("result") or {}
        return {
            "result": result.get("result", {}),
            "result_error": result.get("result_error", {}),
            "result_next": result.get("result_next", {}),
            "result_total": result.get("result_total", {}),
        }


def _extract_list(result: Any) -> list[Any]:
    """Normalize a method's ``result`` into a list of records.

    Handles the four shapes Bitrix uses: a bare list (``crm.*.list``), a dict
    with a ``tasks``/``items`` array (``tasks.task.list``), a dict keyed by id
    (some legacy list methods), and a named wrapper key whose value is itself
    keyed by id - ``crm.documentgenerator.template.list`` answers
    ``{"templates": {"1": {...}, "2": {...}}}``. That last shape used to fall
    through to ``list(result.values())`` and yield ONE record containing every
    template, while ``total`` correctly said 19: the caller saw a single
    malformed blob and no way to tell something was wrong."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("tasks", "items", "events"):
            if isinstance(result.get(key), list):
                return result[key]
        # A lone non-numeric key is a wrapper around the real collection; a lone
        # numeric key is a record id and must NOT be unwrapped.
        if len(result) == 1:
            (only_key, only_value), = result.items()
            if not str(only_key).isdigit() and isinstance(only_value, (list, dict)):
                return _extract_list(only_value)
        # dict keyed by id -> values
        return list(result.values())
    return [result]
