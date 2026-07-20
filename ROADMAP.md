# Roadmap & status

**English** · [Русский](ROADMAP.ru.md)

## Status: feature-complete (v0.1.0)

The tool surface is closed for this release. 87 typed tools across 15 domains,
plus the universal `b24_call` / `b24_batch` backbone (100% API reach), read and
write, both transports. Offline verification is green; live verification against
a real portal is the one open step and is run by the operator from their own
network (`scripts/smoke.py`), since the build sandbox has no route to the portal.

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
