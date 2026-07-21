# bitrix-mcp

**English** · [Русский](README.ru.md)

Universal, full-featured, portable **MCP server for the Bitrix24 REST API**.
Read **and** write. Not tied to any one application — it's a generic Bitrix24
gateway that any MCP client or agent can mount (Claude Code, Claude Desktop,
Cursor, Windsurf, Cline, or your own Python/Node agent).

- **Language:** Python + FastMCP (official MCP SDK)
- **Transports:** `stdio` (default, most portable/reliable) and **Streamable HTTP**
  (stateless JSON — no fragile long-lived SSE bridge)
- **Coverage:** universal `b24_call` / `b24_batch` reach **100%** of the REST API;
  88 typed tools cover the high-traffic domains with the tricky bits handled.

## Why this exists / what it fixes

Rebuilt from field notes on a previous wrapper. The bugs that motivated it are
fixed *by design*, not patched around:

| Old behavior | Fix here |
|---|---|
| `filter` silently ignored (`groups_list`, `users_list`), full-portal dumps → timeouts | Params sent as **JSON POST body**, so nested `filter`/`select`/`order` are parsed correctly by Bitrix. Real pagination with a page cap. |
| Access errors swallowed into a fake "0 results" (`read_pipelines` etc.) | Errors are **never** swallowed — a Bitrix `error`/`error_description` always surfaces with its `code` (e.g. `ACCESS_DENIED`). |
| `calendar_list` returned 0 without explicit `ownerId` | `owner_id` **auto-resolves** to the acting user. |
| Scrum kanban read from the wrong place | Correct flow baked in: active-sprint filter + `tasks.api.scrum.kanban.getStages` (`b24_scrum_board` does it in one call). |
| Fragile `mcp-remote` SSE session drops / hangs | Prefer **stdio** (no bridge) or **stateless Streamable HTTP**. |
| `department.get` has **no server-side filter at all** (a Bitrix API limitation, undocumented) — any `filter` was silently ignored and the whole department tree (95+ rows) came back regardless | `b24_department_get` filters **client-side** after a full fetch, so `filter`/`ID` genuinely narrow the result instead of quietly dumping everything. |
| Bitrix sometimes reports a failure as `{"error": "", "error_description": "Access denied."}` — an **empty-string** error code — which a naive truthiness check (`if data.get("error")`) misses, losing the code and message to a generic HTTP-status fallback | Checked by **key presence**, not truthiness — `code`/`message` always reflect what Bitrix actually said. |
| `calendar.event.add` / `.update` silently **drop `attendees`** unless `is_meeting` is also set — 200 OK, event created, nobody invited, no error anywhere | `is_meeting` is **auto-set to `'Y'`** whenever `attendees` is non-empty and not already specified. |
| Moving a task on a **Scrum sprint board** via `tasks.task.update`'s `STAGE_ID` is accepted with no error and reads back correctly, but the card **does not actually move** on the real board (confirmed live: reload the page, still in the old column) | New `b24_scrum_task_move` calls the board-aware `tasks.api.scrum.kanban.addTask` instead — the one that actually relocates the card. `b24_task_update`/`b24_tasks_list` now document this trap rather than silently mis-teaching it. |

## Install

```bash
uv sync                       # create venv + install
# or, as a tool on PATH:
uv tool install .             # exposes the `bitrix-mcp` command
```

## Configure

Set the default webhook (see `.env.example`):

```bash
export BITRIX_WEBHOOK_URL="https://your-portal.bitrix24.ru/rest/1/xxxxxxxx/"
# optional:
export BITRIX_READ_ONLY=1     # block all writes
```

The webhook comes from Bitrix: *Profile → Webhooks → inbound webhook*, format
`https://<portal>/rest/<user_id>/<token>/`. The token is a credential — keep it
out of source control (`.env` is gitignored).

**Auth precedence per call:** `personal_webhook` → `webhook_url` →
`X-B24-Webhook` HTTP header → `BITRIX_WEBHOOK_URL`. Pass `personal_webhook` to
act (and write) as a specific user.

## Run

```bash
bitrix-mcp                         # stdio (default)
bitrix-mcp --http                  # Streamable HTTP on 127.0.0.1:8000/mcp
bitrix-mcp --http --host 0.0.0.0 --port 5015   # shared network service
```

## Connect a client

**Claude Code (stdio, recommended):**
```bash
claude mcp add -s user bitrix24 -- uv run --directory C:/Scripts/BitrixMCP bitrix-mcp
```

**Repo-shared `.mcp.json` (stdio):**
```json
{
  "mcpServers": {
    "bitrix24": {
      "command": "uv",
      "args": ["run", "--directory", "C:/Scripts/BitrixMCP", "bitrix-mcp"],
      "env": { "BITRIX_WEBHOOK_URL": "https://your-portal.bitrix24.ru/rest/1/xxxx/" }
    }
  }
}
```

**Claude Code (HTTP):**
```bash
bitrix-mcp --http --port 5015          # then, on the client:
claude mcp add -s user --transport http bitrix24 http://HOST:5015/mcp
```

**Claude Desktop (stdio)** — `%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "bitrix24": {
      "command": "uv",
      "args": ["run", "--directory", "C:/Scripts/BitrixMCP", "bitrix-mcp"],
      "env": { "BITRIX_WEBHOOK_URL": "https://your-portal.bitrix24.ru/rest/1/xxxx/" }
    }
  }
}
```
(For a remote HTTP instance, Desktop still needs the `mcp-remote` bridge; stdio
above avoids it entirely.)

## Tool catalog (88)

**Universal** — `b24_call`, `b24_batch`, `b24_test_connection`, `b24_list_methods`
**CRM** — `b24_crm_list`, `b24_crm_get`, `b24_crm_fields`, `b24_crm_add`, `b24_crm_update`, `b24_crm_delete`, `b24_crm_timeline_comment_add`, `b24_crm_timeline_comment_list`, `b24_crm_category_list` (pipelines), `b24_crm_status_list` (stages/dictionaries), `b24_crm_activity_list`, `b24_crm_activity_add`, `b24_crm_activity_delete`, `b24_crm_productrows_get`, `b24_crm_productrows_set`, `b24_crm_currency_list`, `b24_crm_requisite_list`, `b24_crm_deal_contacts_get`, `b24_crm_deal_contacts_set` (classic entities *and* SPA via `entity_type_id`)
**Tasks** — `b24_tasks_list`, `b24_task_get`, `b24_task_add`, `b24_task_update`, `b24_task_complete`, `b24_task_delete`, `b24_task_comments_list`, `b24_task_comment_add`, `b24_task_stages_get`, `b24_task_checklist_list`, `b24_task_checklist_add`, `b24_task_elapsed_add`, `b24_task_result_list`
**Scrum** — `b24_scrum_sprint_list`, `b24_scrum_kanban_stages`, `b24_scrum_board`, `b24_scrum_task_move`
**Calendar** — `b24_calendar_event_list`, `b24_calendar_section_list`, `b24_calendar_event_add`, `b24_calendar_event_update`, `b24_calendar_event_delete`
**Disk** — `b24_disk_storage_list`, `b24_disk_folder_items`, `b24_disk_file_get`, `b24_disk_file_content` (server-side download → base64), `b24_disk_folder_add`, `b24_disk_file_upload`, `b24_disk_file_delete`
**Users/structure** — `b24_user_get`, `b24_user_search`, `b24_user_current`, `b24_department_get`
**Groups (workgroups)** — `b24_group_list`, `b24_group_users`, `b24_group_create`, `b24_group_update`, `b24_group_delete`
**Messaging** — `b24_im_recent`, `b24_im_dialog_messages`, `b24_im_message_add`, `b24_im_notify_personal`, `b24_im_user_get`, `b24_im_chat_create`, `b24_im_chat_user_add`, `b24_feed_post_add`
**Lists (universal lists)** — `b24_lists_get`, `b24_lists_element_list`, `b24_lists_element_add`, `b24_lists_element_update`, `b24_lists_element_delete`
**Catalog / products** — `b24_catalog_list`, `b24_catalog_section_list`, `b24_catalog_product_list`, `b24_catalog_product_get`, `b24_catalog_product_add`, `b24_catalog_product_update`, `b24_crm_product_list`
**Sale (orders)** — `b24_sale_order_list`, `b24_sale_order_get`
**Documents** — `b24_documentgenerator_templates`, `b24_documentgenerator_add`
**Bizproc** — `b24_bizproc_template_list`, `b24_bizproc_start`
**Telephony** — `b24_telephony_statistics`

Anything still not typed here is reachable through `b24_call` (e.g. mail,
open-lines, sale basket writes, admin/app-placement methods).

## Retrospective-app integration

This server has **no knowledge** of any downstream app. An agent connects to both
this server and your app's MCP, reads Bitrix here, and relays into the app's
contract. Field names from `b24_tasks_list` / `b24_calendar_event_list` map
directly onto `PushSprintTask` / `PushCalendarEvent`, so the mapping is trivial —
but that translation lives in the agent, not here.

## Documentation

- [docs/USAGE.md](docs/USAGE.md) — how to drive the tools: auth, filters, pagination, batch, errors, recipes.
- [ARCHITECTURE.md](ARCHITECTURE.md) — components, call flow, deployment; diagrams in [docs/diagrams/](docs/diagrams/).
- [ROADMAP.md](ROADMAP.md) — status, history, and out-of-scope boundaries.

## Development

```bash
uv sync                 # install runtime + dev deps
uv run pytest -q        # offline unit tests (no portal needed)
uv run python scripts/smoke.py "<webhook>"   # live read-only access map (run from a network with portal access)
```

Diagrams are regenerated with `java -jar plantuml.jar -tpng docs/diagrams/*.puml`.

## Notes on limits

- `fetch_all=true` is capped by `BITRIX_MAX_PAGES` (default 40 pages ≈ 2000 records)
  and reports `truncated: true` when it hits the cap — it never silently stops short.
- The read-only guard classifies writes by method verb; typed write tools are
  always classified correctly. `b24_call`/`b24_batch` use the heuristic.

## Support author

<p align="center">
  <img src="donate-qr.png" alt="Donate QR" width="200"/>
</p>

BTC: bc1q3frrup5neh7nhfg944etu2agd4j9u0vg3jyee6

ETH(Arbitrum): 0x43B349d8Cea83215D707EBa3bc35e9917f746b0a

TRX: THSzvy49KNeqRjXsGkurh2A5G4avV4RgN4

XRP: rLWZjS3DMupC4ZdXCX3BVYn4dEtC3iNhgy

SOL: 3xwfybxJ6Tz5t6pjBBkL5yYQCZo6wfbv932UNA4ThdP8

ADA: addr1q926ys75jp5wn2pv32a3t8r8pdhr7w02v0t9j4a8pmg0ruww5rlkctu4lnz2hfcwa5qfn3zhsd0s23r22uqwzx9gu6cq5c4e76

TON: UQC4qlAOD9Nly4K_66GJ_yCsSM3x2sB0vZ2GrBQbc--gZUui

DOGE: DTjNYmbtymzcjUiV4MsZY8MP4dM7MJ6qLC

XMR: 44qRqM6YtnxXUhkgCFqDDrKMPjWriu69FLBoop8Kwp7e1VQsBUJoVQ8JYQjfMV5C6uidTUgSSyoJ65mq8aYG2esZ1rrqfwt
