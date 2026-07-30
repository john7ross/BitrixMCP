# Roadmap & status

**English** · [Русский](ROADMAP.ru.md)

## Status: feature-complete (v0.1.0)

The tool surface is closed for this release. 99 typed tools across 17 domains,
plus the universal `b24_call` / `b24_batch` backbone and a method catalogue from
the official documentation (100% API coverage — both calling and knowing the
signatures), reads and writes, both transports, and three ways of receiving
portal events with a history archive and Telegram forwarding. Offline
verification is green (106 tests); live verification against a real portal was
carried out end to end (see "History") — reads across every domain
plus full write life-cycles (task, workgroup, calendar event, disk file — each
created, verified, and deleted, nothing pre-existing touched).

## History

- **Rebuild from field notes.** Replaced a buggy internal wrapper. The documented
  failures were fixed by design, not patched:
  - filters sent as JSON body (fixes the ignored-filter / full-portal-dump bugs);
  - Bitrix errors surfaced with their code (no more fake "0 results");
  - calendar `ownerId` auto-resolved;
  - Scrum kanban via active-sprint + `tasks.api.scrum.kanban.getStages`;
  - telephony via the correct `voximplant.statistic.get`;
  - stdio / stateless HTTP instead of the fragile `mcp-remote` SSE bridge.
- **Read baseline → full write.** CRUD and side-effecting actions added across
  CRM, tasks, calendar, disk, messaging, lists, catalog, groups, bizproc, with an
  opt-in read-only guard.
- **Breadth for daily use.** Expanded from 8 to 15 typed domains (pipelines,
  statuses, activities, requisites, product rows, deal-contact links, task
  stages/checklists/time/results, workgroups, orders, document generation,
  chats/feed, universal lists).
- **Live-tested against a production portal.** A full read sweep plus write
  create→verify→delete cycles (task with comments/checklist/time/complete;
  workgroup with update; calendar event with attendees; disk file) surfaced and
  fixed three further edge cases beyond the original rebuild — see the bug table
  in the README: `department.get` has no server-side filter at all (Bitrix
  limitation, now filtered client-side), an empty-string Bitrix error code was
  slipping past a truthiness check, and `calendar.event.add`/`.update` silently
  dropped `attendees` without an explicit `is_meeting`. Also surfaced (not a code
  bug): `b24_disk_file_content` needs the server's egress IP allowed by the
  portal's WAF, or file *downloads* 403 even though metadata calls succeed —
  ask the portal admin to allowlist the host running the server.
- **Scrum sprint board move/read, root-caused with the operator live on a real
  board.** Moving a card via `tasks.task.update`'s `STAGE_ID` looked
  successful (200 OK, correct read-back) but silently didn't relocate the card
  — verified by watching the actual board, not just the API response. Added
  `b24_scrum_task_move` (`tasks.api.scrum.kanban.addTask`, Bitrix's
  board-aware write, found via their official REST docs after the wrong
  method was ruled out). Further testing then showed the *read* side is also
  unreliable once a task is moved correctly: `STAGE_ID` on `tasks.task.get`/
  `.list` goes stale and never updates again, with no public method to read a
  sprint board's true task-to-column mapping. **Solved outside REST.** The
  portal's Push & Pull channel broadcasts the move as it happens: the
  `tasks / task_update` event carries `BEFORE.STAGE`, `AFTER.STAGE` and
  `AFTER.STAGE_INFO` with the column id. Verified live on a real board — a card
  was moved and the event captured. The true task-to-column mapping accumulates
  from those transitions; REST still does not expose it, and that remains a
  limitation of REST rather than of this project.

## Out of scope (by design)

- **1:1 typed coverage of the entire API.** The documentation describes 1930
  methods — that many tool descriptions fit in no context window. This limits the
  *mechanism*, not the coverage: requirement [R-1](REQUIREMENTS.md) (100% API
  coverage) is met by the universal tools plus the method catalogue from the
  official documentation, not by the number of wrappers.
- **App infrastructure** (`event.bind`, placements, OAuth app flows) — this
  is a webhook client, not a Bitrix Market application. Confirmed against a live
  portal: `event.get` answers `WRONG_AUTH_TYPE` for an incoming webhook, so this
  is a constraint of the authentication type rather than a project choice.
  **Events themselves are in scope**, by three other routes: the pull channel,
  the outgoing-webhook receiver and the poller — see
  [docs/EVENTS.md](docs/EVENTS.md).
- **Knowledge of any consuming application.** The server is a generic gateway;
  mapping its output into a downstream contract lives in the agent, not here.
- **Sale basket/order writes** — multi-step; use `b24_call` until a clear demand
  justifies a typed flow.

## Possible future work (not committed)

- Typed wrappers for open lines and mail if daily demand appears.
- A cursor helper for very large exports beyond the page cap.
- Optional API-key auth in front of the HTTP transport.
