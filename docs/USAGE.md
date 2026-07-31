# Usage guide

**English** · [Русский](USAGE.ru.md)

How to drive the Bitrix24 MCP tools. This is client-agnostic — it assumes only
that your agent or code speaks MCP and can call the server's tools. For wiring
the server into a specific client, see the connection section of the README.

## The two layers

- **Universal backbone** — `b24_call`, `b24_batch`, `b24_test_connection`,
  `b24_list_methods`. `b24_call` invokes *any* REST method, so it covers the
  whole API. Reach for it when no typed tool fits, or to double-check a typed
  tool's behavior.
- **Typed tools** — `b24_<domain>_<action>` (e.g. `b24_crm_list`,
  `b24_task_add`). Ergonomic wrappers with correct pagination, filters, and
  Bitrix quirks handled.

Start a session with `b24_test_connection` to confirm the webhook and see the
acting user, then `b24_list_methods` to see which scopes the webhook has.

## Authentication

Every tool accepts optional `webhook_url` and `personal_webhook`. Resolution
order: `personal_webhook` > `webhook_url` > `X-B24-Webhook` header (HTTP
transport) > `BITRIX_WEBHOOK_URL` env. To act/write as a specific user, pass that
user's `personal_webhook`. Format is always
`https://<portal>/rest/<user_id>/<token>/`.

## Filters

Filter objects mirror Bitrix. Keys may carry operator prefixes:

| Prefix | Meaning | Example |
|---|---|---|
| (none) | equals | `{"STAGE_ID": "NEW"}` |
| `>` `<` `>=` `<=` | comparisons | `{">=DATE_CREATE": "2026-01-01"}` |
| `!` | not equal | `{"!STATUS": "5"}` |
| `%` | substring | `{"%TITLE": "invoice"}` |

Field codes differ by method (CRM uses `UPPER_CASE`; the modern `crm.item.*` and
`catalog.*` use `camelCase`). Use the `*_fields` tools (e.g. `b24_crm_fields`) to
discover exact codes and enum ids before writing.

**Exception:** `b24_department_get` — Bitrix's `department.get` has no
server-side filter at all (confirmed live: any `filter`, including `ID`, was
silently ignored and the whole department tree came back). The tool filters
client-side instead, so `filter`/`ID` still work as documented — it just costs
one extra full fetch under the hood.

## Pagination

List tools return an envelope:

```json
{ "items": [...], "count": 50, "total": 214, "start": 0,
  "next": 50, "has_more": true, "truncated": false }
```

- Page manually by passing `start` = the previous `next`.
- Or pass `fetch_all: true` to walk pages automatically. This is capped by
  `BITRIX_MAX_PAGES` (default 40 pages ≈ 2000 records); if it hits the cap the
  envelope reports `truncated: true` — it never silently stops short. Always add
  a `filter` to keep `fetch_all` bounded.

## Errors

Errors are returned as data, never hidden:

```json
{ "error": true, "code": "ACCESS_DENIED", "method": "crm.deal.list",
  "message": "Access denied." }
```

Common codes: `ACCESS_DENIED` (the acting user lacks portal permission — not a
bug; scope on the webhook is necessary but not sufficient), `ERROR_METHOD_NOT_FOUND`
(module not installed), `B24_READONLY` (write blocked by read-only mode),
`NO_WEBHOOK` (no webhook resolved), `TIMEOUT`.

## Read-only mode

Set `BITRIX_READ_ONLY=1` to refuse every write method before it reaches the
portal (reads still work). Useful for giving a read-only footprint or during
debugging. Unset it to allow writes.

## Batch

`b24_batch` runs up to 50 calls in one request, with back-references:

```json
{ "commands": [
    {"key": "deal", "method": "crm.deal.add", "params": {"fields": {"TITLE": "New"}}},
    {"key": "note", "method": "crm.timeline.comment.add",
     "params": {"fields": {"ENTITY_ID": "$result[deal]", "ENTITY_TYPE": "deal", "COMMENT": "created"}}}
  ], "halt": true }
```

`$result[key]` / `$result[key][field]` inject an earlier command's output.

## Recipes

- **Deals created this month:**
  `b24_crm_list` entity=`deal`, filter `{">=DATE_CREATE":"2026-07-01"}`, select `["ID","TITLE","OPPORTUNITY","STAGE_ID"]`.
- **Resolve a deal stage code to a name:**
  `b24_crm_status_list` entity_id=`DEAL_STAGE` (or `DEAL_STAGE_7` for pipeline 7).
- **Tasks in a Scrum board column:** `b24_scrum_board` group_id=… to get the
  active sprint + stages, then `b24_tasks_list` filter `{"GROUP_ID":…, "STAGE_ID":…}`
  — approximate only, see Known limitations below.
- **Move a task on a Scrum sprint board:** `b24_scrum_task_move` task_id=…
  sprint_id=… stage_id=… — **not** `b24_task_update` with `STAGE_ID` (accepted,
  read back correctly, but doesn't move the card — see Known limitations).
- **Create a task and log time:** `b24_task_add` → then `b24_task_elapsed_add`.
- **Download a Disk file:** `b24_disk_file_content` file_id=… (returns base64;
  guarded by `max_size_mb`).
- **Post to a chat:** `b24_im_message_add` dialog_id=`chat123` or a user id.
- **Invite people to a calendar event:** pass `extra: {"attendees": [id, ...]}`
  to `b24_calendar_event_add` (or `fields` on `b24_calendar_event_update`) —
  `is_meeting` is set for you automatically whenever `attendees` is non-empty.

## Known limitations

- **Scrum sprint boards: moving a card takes two calls, and `STAGE_ID` cannot
  confirm it.** Established by watching a production board, because every call
  involved reports success regardless of what happened:

  | Call | What it really does |
  |---|---|
  | `tasks.task.update` + `STAGE_ID` | Changes the field and writes a history entry everyone can see — **the card does not move**. The worst case: colleagues read "stage changed" in the log while the board still shows the old column. |
  | `kanban.addTask` | **Places** a card that is *not* on the board. For one already in a column it returns `true` and does nothing at all. |
  | `kanban.deleteTask` | Takes the card off the board (the task stays in the sprint). |
  | `task.stages.movetask` | Returns `false` — it governs the plain group kanban, not sprints. |

  So a move is `deleteTask` then `addTask`, which is what `b24_scrum_task_move`
  does. Despite the name, `deleteTask` removes only the *column placement* — it
  does not delete the task and does not leave a duplicate card. Compared before
  and after a real move: same id, same GUID, same creation date, story points,
  checklist, logged time and tags all intact, one extra history row; the board
  showed exactly one card. Repeated moves are safe.
  Do **not** verify with `b24_task_get`: `STAGE_ID` read `0` while the
  card was visibly sitting in the target column. There is no documented
  `tasks.api.scrum.kanban.*` method that lists which tasks are in a stage, so
  `b24_tasks_list` filtered by `STAGE_ID` on a sprint board is approximate at
  best. The pull channel is the reliable read: its `tasks / task_update` event
  carries `BEFORE.STAGE`, `AFTER.STAGE` and `AFTER.STAGE_INFO` with the column
  id — see [EVENTS.md](EVENTS.md).
- **Backlog ↔ sprint is a different, working mechanism.**
  `tasks.api.scrum.task.update` with `entityId` moves a task between the
  group's backlog and a sprint, and it genuinely works — `entityId` on
  `tasks.api.scrum.task.get` reflects it immediately. That entity carries the
  sprint id, story points and sort order, but **no column**: the card's column
  is simply not part of the REST model.
- **Task comments live in one of two places, and Bitrix will not say which.**
  Older portals keep them in a forum topic (`task.commentitem.*`); portals where
  a task has a chat keep them there instead, leaving `forumTopicId` null. Reading
  only the forum on such a portal returns `[]` for a task that visibly has
  comments — a successful `b24_task_comment_add` then looks like a failure, and
  the natural reaction is to post it again. `b24_task_comments_list` reads the
  forum first, falls back to the chat, and reports `source` so an empty answer
  means "nothing there", not "looked in the wrong place". Chat-sourced entries
  include Bitrix's own notices (task created, time logged) flagged `is_system`.
- **Calendar events come back whole, so ask for less.** `calendar.event.get` has
  no server-side projection and no paging: it returns every field of every event
  in the range. One month for one user measured 232 events × 60 fields ≈ 1 MB.
  `b24_calendar_event_list` therefore trims client-side — a compact field set and
  `limit=50` by default — while always reporting the true `total` and a
  `truncated` flag. Widen deliberately (`select=['*']`, a higher `limit`) rather
  than assuming the tail is empty.
- **Disk downloads need the server's IP allowed by the portal.**
  `b24_disk_file_content` resolves `DOWNLOAD_URL` and fetches it server-side, but
  some portals' WAF returns `403 Forbidden` (HTML) to that fetch when it comes
  from an IP outside the portal's trusted network — even though the file was
  just uploaded by the same webhook and `b24_disk_file_get` metadata works fine.
  This is a portal-side network/WAF policy, not a bug in the client: ask the
  portal admin to allowlist the host running `bitrix-mcp`, or run the server
  from a network the portal already trusts.

## SPA / Smart Processes

For Smart Process items, pass `entity_type_id` to the CRM tools — they switch to
the modern `crm.item.*` API automatically (e.g. `b24_crm_list` entity_type_id=1030).

## Finding the right method

The Bitrix API has 1930 documented methods. Do not guess a signature — ask:

```
b24_method_search("call recording attach")   → telephony.externalCall.attachRecord
b24_method_schema("crm.item.list")           → parameters, types, what is required
b24_scope_gaps()                             → what this webhook cannot reach
```

The catalogue is built from the official documentation and includes methods that
the portal's own `methods` listing omits (`tasks.task.list`, `crm.item.list`,
`catalog.product.list`, `sale.order.list` and others) — which is exactly why it
is not built from the portal's response.

`b24_scope_gaps` is what to reach for when a call fails with
`ERROR_METHOD_NOT_FOUND`: on a live portal that usually means **a scope was not
granted**, not that the module is missing. Re-issue the webhook with the right
boxes ticked.

Refresh the catalogue with `python scripts/build_catalog.py`.

## Portal events

The server can learn about changes in the portal and keep their history. Three
ways to receive them — pull channel, outgoing-webhook receiver, poller — chosen
by where the server runs and what the network allows.

```
b24_events_poll()                             new events
b24_events_ack(ids=[...])                     mark as processed
b24_events_history(entity="task",
                   entity_id="477818",
                   since="7d")                the archive for one object
b24_changes_since(feed="tasks")               the poller
b24_events_stats()                            what is configured and captured
```

`b24_events_history` answers what REST cannot: most Bitrix entities expose no
change history, and a Scrum board offers no way to read a task's current column.
Here it exists, assembled from captured events.

Telegram forwarding is configured through `b24_telegram_status`,
`b24_telegram_configure` and `b24_telegram_test`. Nothing is forwarded by
default — the user picks the filter.

Step-by-step setup for each path, every environment variable and a
troubleshooting table: [EVENTS.md](EVENTS.md).
