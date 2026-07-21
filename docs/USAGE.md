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
  active sprint + stages, then `b24_tasks_list` filter `{"GROUP_ID":…, "STAGE_ID":…}`.
- **Create a task and log time:** `b24_task_add` → then `b24_task_elapsed_add`.
- **Download a Disk file:** `b24_disk_file_content` file_id=… (returns base64;
  guarded by `max_size_mb`).
- **Post to a chat:** `b24_im_message_add` dialog_id=`chat123` or a user id.
- **Invite people to a calendar event:** pass `extra: {"attendees": [id, ...]}`
  to `b24_calendar_event_add` (or `fields` on `b24_calendar_event_update`) —
  `is_meeting` is set for you automatically whenever `attendees` is non-empty.

## Known limitations

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
