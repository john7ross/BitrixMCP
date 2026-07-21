# Roadmap & status

**English** · [Русский](ROADMAP.ru.md)

## Status: feature-complete (v0.1.0)

The tool surface is closed for this release. 87 typed tools across 15 domains,
plus the universal `b24_call` / `b24_batch` backbone (100% API reach), read and
write, both transports. Offline verification is green; live verification against
a real portal has been run end to end (see History) — reads across every domain
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

## Out of scope (by design)

- **1:1 typed coverage of the entire API.** Hundreds of methods would bloat tool
  selection; the universal tools cover the long tail intentionally.
- **App/event infrastructure** (`event.bind`, placements, OAuth app flows) — this
  is a webhook client, not a Bitrix Market app.
- **Knowledge of any consuming application.** The server is a generic gateway;
  mapping its output into a downstream contract lives in the agent, not here.
- **Sale basket/order writes** — multi-step; use `b24_call` until a clear demand
  justifies a typed flow.

## Possible future work (not committed)

- Typed wrappers for open lines and mail if daily demand appears.
- A cursor helper for very large exports beyond the page cap.
- Optional API-key auth in front of the HTTP transport.
