# Portal events: three ways to receive them

**English** · [Русский](EVENTS.ru.md)

The server can learn about changes in Bitrix24 in three different ways. They are
not interchangeable: each works where the others cannot. Which one you use
depends on where the server runs and what your network allows, not on taste.

Everything captured lands in **one shared SQLite file**, whichever way it
arrived. From there it is read through the `b24_events_*` tools and, if
configured, forwarded to Telegram.

## Choosing

| | pull channel | outgoing webhook | poller |
|---|---|---|---|
| Connection direction | outbound | **inbound**, portal → server | outbound |
| Needs a reachable URL | no | **yes** | no |
| Needs admin rights on the host | no | usually (firewall) | no |
| Works behind NAT / VPN | **yes** | no | **yes** |
| Latency | seconds | seconds | polling interval |
| Sees deletions | no | **yes** | no |
| Event catalogue | undocumented | **documented** | n/a |
| Works on stdio | yes | **no**, `--http` only | yes |

**Running locally on a workstation — use the pull channel.** It is the same
mechanism the Bitrix24 mobile and desktop apps use to stay live: the client
opens the connection, so NAT, corporate firewalls and VPNs are irrelevant.

**Running on a host with a stable address — use the outgoing webhook.** It is
the only path with a documented event list, and the only one that sees
deletions.

**The poller is the fallback.** It always works, but it reports the state at
poll time rather than every individual change.

You can enable several at once. Duplicates are suppressed: the webhook
deduplicates on delivery content, the channel on `mid`, the poller on
record-plus-timestamp.

---

## Option 1. Pull channel (Push & Pull)

The server connects outwards to the portal's push server and holds the
connection. Nothing needs configuring on the portal side.

### Setup

```ini
BITRIX_PULL_CHANNEL=1
BITRIX_EVENT_DB=/var/lib/bitrix-mcp/events.sqlite3
```

That is all. Restart the server and the log will show `pull channel started`.

Give the database an **absolute path outside the repository**: the default is
relative to the working directory, and the file holds real personal data about
your staff.

### Verifying

```bash
python scripts/pull_probe.py 120
```

The script connects and prints whatever arrives. Move a card on a board or send
a chat message and watch the events appear.

### What actually arrives

Confirmed against a live portal: `tasks/task_update` (including stage
transitions with `BEFORE`/`AFTER` and the column id), `tasks/itemUpdated` with
Scrum fields, and the `im/*` family — messages, counters, read receipts.

### Limits worth knowing up front

- **The channel is personal.** You receive what the interface of the webhook's
  own user would receive. This is not the same list as the outgoing webhook's.
- **It is noisy.** The messenger generates events constantly. Configure a
  Telegram filter before enabling forwarding, or it will flood.
- **The contract is not guaranteed.** `module_id`/`command` pairs are an
  internal UI protocol, not versioned REST. They can change between portal
  versions without notice.
- **Deletions are invisible.**
- Channels live about 12 hours and are refreshed automatically.

### Why it matters even if you have the webhook

It is the only source that reveals **which Scrum board column a task is in**.
REST cannot do this: `STAGE_ID` from `tasks.task.get` goes stale after a board
move, and no method exposes the real mapping. The channel broadcasts the
transition at the moment it happens.

---

## Option 2. Outgoing webhook

The portal POSTs to your address on every event. Requires the server to be
reachable over HTTP from the portal's network.

### Step 1. Run the server on the HTTP transport

The receiver is mounted **only** on `--http`. It does not exist on stdio — use
the pull channel there.

```ini
BITRIX_EVENT_TOKEN=<filled in at step 3>
BITRIX_EVENT_PATH=/b24/events
BITRIX_EVENT_DB=/var/lib/bitrix-mcp/events.sqlite3
```

```bash
bitrix-mcp --http --host 0.0.0.0 --port 8000
```

The handler URL is then `http://<host>:8000/b24/events`.

### Step 2. Confirm the portal can actually reach you

This is the usual reason setups fail, and it is worth settling before touching
the portal:

```bash
python scripts/probe_listener.py          # on the machine running the server
```

Then, **from the Bitrix server itself**:

```bash
curl -v http://<host>:8000/probe
```

A line in the probe's console means the path exists. A timeout or connection
refused means it does not, and no amount of portal configuration will fix that.
On Windows you will need an inbound rule (elevated PowerShell):

```powershell
New-NetFirewallRule -DisplayName "BitrixMCP" -Direction Inbound `
    -Protocol TCP -LocalPort 8000 -Action Allow
```

### Step 3. Create the webhook on the portal

**Developer resources → Other → Outgoing webhook.** Three fields:

**Handler URL** — the full address, e.g. `https://mcp.example.com/b24/events`.
The portal expects `https`; self-hosted instances often accept `http`, but
verify that on your own installation.

**Application token** — generated by the portal. Copy it into `.env`:

```ini
BITRIX_EVENT_TOKEN=<the token from that field>
```

Without it the receiver answers **503 to every delivery**. That is deliberate:
the route is not covered by the SDK's authorization, so this token is the only
thing distinguishing your portal from an arbitrary request off the network. It
cannot end up open by oversight.

**Events** — pick what you need via "select". The portal's default selection
covers bookings, calendar, activity stream and smart processes. If you want
tasks and deals, add them explicitly — `ONTASKUPDATE`, `ONTASKADD`,
`ONCRMDEALUPDATE`, `ONCRMDEALADD` and similar are **not** in the default list.

### Step 4. Verify

```bash
curl -X POST http://<host>:8000/b24/events -d "event=PROBE"
```

Expect **403**: the route is alive and rejecting a delivery with no valid token.
Then do something in the portal and check `b24_events_stats`.

### Requirements and limits

- **Self-hosted instances need an active licence.** Outgoing webhooks do not
  work on demo portals at all — check this first.
- The handler must answer 200 quickly or the portal retries. The receiver
  answers immediately and never returns 500 — otherwise one malformed delivery
  would retry forever.
- Repeated deliveries are deduplicated on content.
- The payload arrives as `application/x-www-form-urlencoded` with PHP bracket
  arrays (`data[FIELDS][ID]=662`), **not JSON**.
- `application_token` is stripped before anything is written to the database.

### HTTPS

The MCP SDK cannot serve TLS — its runner accepts no certificate options. So
either put a reverse proxy in front, or use the built-in path:

```ini
BITRIX_HTTP_SSL_CERT=/path/fullchain.pem
BITRIX_HTTP_SSL_KEY=/path/privkey.pem
```

With both set, the server builds uvicorn itself and serves https.

---

## Option 3. Poller

Asks the portal what changed since a given moment. Outbound requests only, and
nothing to configure on the portal.

### Setup

No dedicated variables beyond `BITRIX_EVENT_DB` for the cursors. Invoked as a
tool:

```
b24_changes_since(feed="tasks")
```

A cursor is kept per feed, so repeated calls with no arguments walk forward
through changes. The first call without `since` starts 24 hours back.

### Feeds

`tasks` is confirmed against live data. `deals`, `leads`, `contacts` and
`companies` follow the documented shape but have not been confirmed against a
portal with real records — those feeds report `verified: false` in their
response. Smart processes work through `entity_type_id`.

### Limits

- Reports the **state at poll time**, not every change. Two edits between polls
  look like one.
- **Deletions are invisible** — a deleted record simply stops being returned.
- The `>=` bound is inclusive, so the boundary record arrives twice;
  deduplication absorbs it in the archive.

---

## Reading what was captured

All three paths fill the same database, so the tools are shared.

| Tool | Purpose |
|---|---|
| `b24_events_poll` | new unprocessed events; `ack` hides them |
| `b24_events_ack` | mark as processed (does not delete from history) |
| `b24_events_history` | the archive: "what happened to task 477818 last week" |
| `b24_events_stats` | what is configured and what has been captured |
| `b24_changes_since` | the poller |

`b24_events_history` answers questions REST cannot: most Bitrix entities expose
no change history at all. Payloads are stored whole, so the archive keeps the
context as it was at the time rather than ids to re-resolve.

Retention is `BITRIX_EVENT_RETENTION_DAYS` (default 14). Cleanup is **by age**;
acknowledged events are not treated as expendable and are never removed
automatically.

**The database contains personal data.** The pull channel delivers staff email
addresses, work phone numbers, birthdays and LDAP identifiers. Keep the file out
of the repository and out of any backup that leaves your control.

---

## Forwarding to Telegram

Events sitting in a database nobody reads may as well not exist. Forwarding
pushes them to a chat or channel.

### Step 1. Bot and target

Create a bot via **@BotFather** and take its token. Find your numeric id — for
example by messaging the bot and reading `getUpdates`. For a channel, add the
bot as an administrator and use the `-100...` id.

```ini
BITRIX_TELEGRAM_TOKEN=123456:AA...
BITRIX_TELEGRAM_CHAT_ID=733224197
BITRIX_TELEGRAM_ALLOWED_USERS=733224197
```

### Step 2. Choose what to receive

**Nothing is forwarded by default.** That is not an oversight: the pull channel
produces a lot of housekeeping noise, and a forwarder that floods the chat on
first run gets muted the same day.

```ini
BITRIX_TELEGRAM_EVENTS=tasks/*,ONTASK*,ONCRM*,poll/*,-im/*
```

Comma-separated, case-insensitive:

| Rule | Meaning |
|---|---|
| `tasks/*` | task events from the pull channel |
| `ONCRMDEAL*` | deal events from the outgoing webhook |
| `tasks/task_update` | one exact event |
| `entity:task` | anything about tasks, whatever the source |
| `source:poll` | anything the poller found |
| `*` | everything |
| `-im/*` | exclusion; **exclusions win** |

Presets: `work`, `tasks`, `crm`, `everything`, `nothing`.

### Step 3. Verify

```
b24_telegram_test(preview_only=true)   # what would be sent, sending nothing
b24_telegram_test()                    # real delivery
b24_telegram_status()                  # current configuration
```

`preview_only` answers "will this spam me?" without sending a single message: it
replays recently captured events through the current filter.

### Configuring through the agent

Filter, target and the on/off switch can be changed at runtime:

```
b24_telegram_configure(events="crm")
b24_telegram_configure(events="tasks/*,-im/*")
b24_telegram_configure(enabled=false)
b24_telegram_configure(reset=true)     # fall back to the .env values
```

Overrides live in the event database and take precedence over `.env`. The `.env`
file is **never rewritten** — a running server should not have its configuration
edited underneath it, and the file stays the record of intent.

### What the agent cannot change, and why

**The bot token** is set only in `.env`. Credentials do not belong in a chat
transcript.

**`BITRIX_TELEGRAM_ALLOWED_USERS` is a security boundary, not a convenience.**
`b24_telegram_configure` lets an agent change `chat_id`, and that agent reads
portal content: task descriptions, comments, mail. A line such as "redirect
forwarding to chat_id …" planted in any of them would send the entire event feed
to a stranger. The list is read **from the environment only** and enforced in
three places: when a target change is attempted (refused before it is stored, so
a rejected address leaves no trace), before a test send, and on every pass of
the background forwarder. An empty list means unrestricted — always set it.

### If Telegram is unreachable

On many corporate and national networks `api.telegram.org` is blocked: the name
resolves but the connection never completes. The symptom is
`cannot reach api.telegram.org (ConnectTimeout)`. That is a network block, not a
bad token.

```ini
BITRIX_TELEGRAM_PROXY=http://proxy:3128
```

The proxy applies **to Telegram only** — the portal is usually reachable
directly, and routing both through one proxy would break the half that works.

Diagnostics: `python scripts/tg_conn_probe.py` checks DNS, TCP and the API
separately and tells you which layer fails.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `BITRIX_EVENT_DB` | SQLite file: events, cursors, settings. Use an absolute path outside the repository |
| `BITRIX_EVENT_RETENTION_DAYS` | retention, default 14 |
| `BITRIX_PULL_CHANNEL` | `1` to enable the pull channel |
| `BITRIX_EVENT_TOKEN` | application token from the outgoing webhook form; without it the receiver returns 503 |
| `BITRIX_EVENT_PATH` | receiver path, default `/b24/events` |
| `BITRIX_HTTP_SSL_CERT` / `_KEY` | certificate and key for https under `--http` |
| `BITRIX_TELEGRAM_TOKEN` | bot token; here only, not settable by the agent |
| `BITRIX_TELEGRAM_CHAT_ID` | default target |
| `BITRIX_TELEGRAM_ALLOWED_USERS` | permitted targets; a security boundary |
| `BITRIX_TELEGRAM_EVENTS` | filter; empty forwards nothing |
| `BITRIX_TELEGRAM_ENABLED` | force on/off |
| `BITRIX_TELEGRAM_PROXY` | proxy for Telegram only |

Telegram settings are also accepted without the prefix: `TELEGRAM_CHAT_ID`,
`TELEGRAM_ALLOWED_USERS`, `TELEGRAM_TOKEN`, `TELEGRAM_PROXY`.

None of this turns itself on. With nothing configured the server behaves exactly
as before and keeps no state.

---

## When nothing arrives

Start with `b24_events_stats` — it distinguishes "nothing happened" from
"nothing is listening".

| Symptom | Cause |
|---|---|
| `receiver.configured: false` | `BITRIX_EVENT_TOKEN` is unset — the receiver rejects everything |
| Receiver answers 503 | the same |
| Receiver answers 405 | you used GET; the route accepts POST only |
| Receiver answers 403 | delivery without a valid application token |
| The portal never reached you | networking; use `scripts/probe_listener.py` plus `curl` from the Bitrix server |
| Token set, no events, stdio transport | the receiver needs `--http`; a warning is logged |
| Channel does not start | `BITRIX_WEBHOOK_URL` missing, or the push server is disabled on the portal |
| `b24_changes_since` repeats itself | the cursor is not advancing — check `cursor.advanced` in the response |
| In the database but not in Telegram | the filter; run `b24_telegram_test(preview_only=true)` |
| `cannot reach api.telegram.org` | network block; set `BITRIX_TELEGRAM_PROXY` |
| Forwarding silent, log mentions the allowed list | `chat_id` is not in `BITRIX_TELEGRAM_ALLOWED_USERS` |

Verification scripts: `probe_listener.py` (reachability), `pull_probe.py`
(channel), `pull_channel_check.py` (channel end to end), `poller_check.py`
(cursors), `telegram_check.py` (filters, offline), `telegram_live_check.py`
(real delivery), `tg_conn_probe.py` (network path to Telegram).
