"""SQLite-backed queue for Bitrix24 outgoing-webhook events, plus poll cursors.

Deliberately the only stateful component in the server.

Design constraints that drove this:
  * Bitrix retries a delivery whenever the handler does not answer 200 quickly,
    so the write path must be a single fast INSERT and never block.
  * The same event can therefore arrive more than once. `dedup_key` makes the
    insert idempotent instead of piling up duplicates.
  * Readers (MCP tools) and the writer (HTTP handler) live in one process but
    different tasks -> WAL, and a short busy timeout.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key   TEXT    NOT NULL UNIQUE,
    event       TEXT    NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'webhook',
    entity      TEXT,
    entity_id   TEXT,
    ts          INTEGER,
    received_at REAL    NOT NULL,
    acked_at    REAL,
    payload     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_unacked ON events (acked_at, id);
CREATE INDEX IF NOT EXISTS ix_events_name    ON events (event, id);
-- The store doubles as a history archive: "what happened to task 477818 last
-- week" has to be an indexed lookup, not a scan over JSON blobs.
CREATE INDEX IF NOT EXISTS ix_events_entity  ON events (entity, entity_id, id);
CREATE INDEX IF NOT EXISTS ix_events_time    ON events (received_at);

-- Cursors for the polling fallback: one row per feed (e.g. "tasks", "deals").
-- Stores the high-water mark so the next poll only asks for what changed since.
CREATE TABLE IF NOT EXISTS cursors (
    feed       TEXT PRIMARY KEY,
    position   TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Runtime settings the agent may change, overriding the .env baseline.
-- Kept here rather than rewriting .env: a running server must not have its
-- configuration file edited underneath it, and the operator's file stays the
-- record of intent.
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

# Columns added after the first release; SQLite has no "ADD COLUMN IF NOT
# EXISTS", so an existing database is upgraded explicitly.
_MIGRATIONS = (
    ("source", "TEXT NOT NULL DEFAULT 'webhook'"),
    ("entity", "TEXT"),
    ("entity_id", "TEXT"),
    ("forwarded_at", "REAL"),
)


def extract_entity(event: str, payload: dict) -> tuple[str | None, str | None]:
    """Best-effort "which object did this happen to", for history queries.

    Shapes are taken from observed pull-channel traffic and the documented
    outgoing-webhook format. Unknown shapes return (None, None) rather than a
    guess: a wrong entity id is worse than no entity id, because it would make
    a history query silently return someone else's events.
    """
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    fields = {}
    data = payload.get("data")
    if isinstance(data, dict):
        # Real task deliveries carry FIELDS_AFTER / FIELDS_BEFORE rather than
        # FIELDS, which the documentation's deal example shows. Checking only
        # FIELDS stores the event but leaves it unfindable by entity - the
        # archive silently returns nothing for exactly the events people ask
        # about most. Confirmed against a real ONTASKUPDATE body.
        for key in ("FIELDS", "FIELDS_AFTER", "FIELDS_BEFORE"):
            candidate = data.get(key)
            if isinstance(candidate, dict) and candidate.get("ID"):
                fields = candidate
                break

    name = (event or "").lower()

    for key, entity in (("TASK_ID", "task"), ("taskId", "task")):
        if params.get(key):
            return entity, str(params[key])
    if name.startswith("tasks/") and params.get("sourceId"):
        return "task", str(params["sourceId"])
    if params.get("chatId"):
        return "chat", str(params["chatId"])

    if fields.get("ID"):
        for marker, entity in (("task", "task"), ("deal", "deal"), ("lead", "lead"),
                               ("contact", "contact"), ("company", "company"),
                               ("dynamicitem", "item")):
            if marker in name:
                return entity, str(fields["ID"])
        return None, str(fields["ID"])
    return None, None



def dedup_key(event: str, ts: Any, data: Any) -> str:
    """Stable fingerprint of one delivery: same event + same ts + same payload."""
    blob = json.dumps([event, str(ts), data], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class EventStore:
    def __init__(self, path: str, retention_days: int = 14):
        self.path = path
        self.retention_days = retention_days
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(events)")}
            for column, decl in _MIGRATIONS:
                if column not in have:
                    c.execute(f"ALTER TABLE events ADD COLUMN {column} {decl}")

    # ---------------- write path (HTTP handler / pull channel) ----------------

    def put(self, event: str, ts: Any, payload: dict, *,
            source: str = "webhook", dedup_on: Any = None,
            entity: str | None = None, entity_id: Any = None) -> int | None:
        """Insert one event. Returns row id, or None if it was a duplicate.

        `dedup_on` is what makes two deliveries "the same". Defaults to the
        outgoing-webhook payload body; the pull channel passes the message id,
        which the push server already guarantees to be unique.

        `entity` / `entity_id` override the extractor. The poller knows exactly
        what it fetched, so it says so rather than letting a heuristic guess
        from a payload shape that was never designed to be guessed from.
        """
        basis = dedup_on if dedup_on is not None else payload.get("data")
        key = dedup_key(event, ts, basis)
        try:
            ts_int = int(ts) if ts not in (None, "") else None
        except (TypeError, ValueError):
            ts_int = None
        if entity is None and entity_id is None:
            entity, entity_id = extract_entity(event, payload)
        entity_id = str(entity_id) if entity_id is not None else None
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO events"
                " (dedup_key, event, source, entity, entity_id, ts, received_at, payload)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (key, event, source, entity, entity_id, ts_int, time.time(),
                 json.dumps(payload, ensure_ascii=False)),
            )
            return cur.lastrowid if cur.rowcount else None

    # ---------------- read path (MCP tools) ----------------

    def poll(self, *, limit: int = 50, event: str | None = None,
             include_acked: bool = False, after_id: int = 0) -> list[dict]:
        sql = ["SELECT id, event, source, entity, entity_id, ts, received_at, acked_at,"
               " payload FROM events WHERE id > ?"]
        args: list[Any] = [after_id]
        if not include_acked:
            sql.append("AND acked_at IS NULL")
        if event:
            # Webhook events are uppercase (ONTASKUPDATE), pull-channel events
            # are "module/command" in lower case - match either without forcing
            # the caller to know which source an event came from.
            sql.append("AND UPPER(event) = UPPER(?)")
            args.append(event)
        sql.append("ORDER BY id LIMIT ?")
        args.append(max(1, min(limit, 500)))
        with self._conn() as c:
            rows = c.execute(" ".join(sql), args).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        return {"id": r["id"], "event": r["event"], "source": r["source"],
                "entity": r["entity"], "entity_id": r["entity_id"], "ts": r["ts"],
                "received_at": r["received_at"], "acked": r["acked_at"] is not None,
                "payload": json.loads(r["payload"])}

    def history(self, *, entity: str | None = None, entity_id: str | None = None,
                event: str | None = None, since: float | None = None,
                until: float | None = None, limit: int = 100,
                newest_first: bool = True) -> list[dict]:
        """Query the archive - "what happened to task 477818 last week".

        Unlike `poll`, this ignores ack state: acknowledged events stay readable,
        which is the whole point of keeping the full payload rather than ids.
        """
        sql = ["SELECT id, event, source, entity, entity_id, ts, received_at, acked_at,"
               " payload FROM events WHERE 1=1"]
        args: list[Any] = []
        if entity:
            sql.append("AND entity = ?")
            args.append(entity)
        if entity_id is not None:
            sql.append("AND entity_id = ?")
            args.append(str(entity_id))
        if event:
            sql.append("AND UPPER(event) = UPPER(?)")
            args.append(event)
        if since is not None:
            sql.append("AND received_at >= ?")
            args.append(float(since))
        if until is not None:
            sql.append("AND received_at <= ?")
            args.append(float(until))
        sql.append("ORDER BY id DESC" if newest_first else "ORDER BY id ASC")
        sql.append("LIMIT ?")
        args.append(max(1, min(limit, 500)))
        with self._conn() as c:
            rows = c.execute(" ".join(sql), args).fetchall()
        return [self._row(r) for r in rows]

    def ack(self, ids: Iterable[int]) -> int:
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        with self._conn() as c:
            cur = c.execute(
                f"UPDATE events SET acked_at = ? WHERE id IN ({marks}) AND acked_at IS NULL",
                [time.time(), *ids],
            )
            return cur.rowcount

    def stats(self) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
            pending = c.execute("SELECT COUNT(*) n FROM events WHERE acked_at IS NULL").fetchone()["n"]
            by = c.execute(
                "SELECT event, COUNT(*) n FROM events WHERE acked_at IS NULL"
                " GROUP BY event ORDER BY n DESC LIMIT 20").fetchall()
            last = c.execute("SELECT MAX(received_at) m FROM events").fetchone()["m"]
        return {"total": total, "pending": pending, "last_received_at": last,
                "pending_by_event": {r["event"]: r["n"] for r in by}}

    # ---------------- cursors (polling fallback) ----------------

    def cursor_get(self, feed: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT position FROM cursors WHERE feed = ?", (feed,)).fetchone()
        return row["position"] if row else None

    def cursor_set(self, feed: str, position: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO cursors (feed, position, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(feed) DO UPDATE SET position=excluded.position,"
                " updated_at=excluded.updated_at",
                (feed, str(position), time.time()),
            )

    def cursor_list(self) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT feed, position, updated_at FROM cursors ORDER BY feed").fetchall()
        return {r["feed"]: {"position": r["position"], "updated_at": r["updated_at"]} for r in rows}

    # ---------------- settings (agent-adjustable overrides) ----------------

    def setting_get(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def setting_set(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at",
                (key, str(value), time.time()),
            )

    def setting_delete(self, key: str) -> bool:
        """Drop an override so the .env baseline applies again."""
        with self._conn() as c:
            return c.execute("DELETE FROM settings WHERE key = ?", (key,)).rowcount > 0

    def setting_all(self) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ---------------- forwarding (Telegram and friends) ----------------

    def pending_forward(self, limit: int = 20) -> list[dict]:
        """Events not yet forwarded, oldest first.

        Independent of ack: acking is what the *agent* processed, forwarding is
        what a *human* was notified about. Conflating them would mean an agent
        run silently swallows the notifications.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, event, source, entity, entity_id, ts, received_at, acked_at,"
                " payload FROM events WHERE forwarded_at IS NULL ORDER BY id LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [self._row(r) for r in rows]

    def mark_forwarded(self, ids: Iterable[int]) -> int:
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        with self._conn() as c:
            cur = c.execute(
                f"UPDATE events SET forwarded_at = ? WHERE id IN ({marks})"
                f" AND forwarded_at IS NULL",
                [time.time(), *ids],
            )
            return cur.rowcount

    def forward_backlog(self) -> int:
        with self._conn() as c:
            return c.execute(
                "SELECT COUNT(*) n FROM events WHERE forwarded_at IS NULL").fetchone()["n"]

    def purge(self) -> int:
        """Drop events older than the retention window.

        Age only - acknowledged events are NOT expendable here. The store is
        deliberately also a history archive, so "already processed" must not
        mean "safe to delete". Never called automatically; retention is a
        decision the operator makes explicitly.
        """
        cutoff = time.time() - self.retention_days * 86400
        with self._conn() as c:
            cur = c.execute("DELETE FROM events WHERE received_at < ?", (cutoff,))
            return cur.rowcount
