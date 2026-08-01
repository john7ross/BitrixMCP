# Roadmap & status

**English** · [Русский](ROADMAP.ru.md)

## Status: feature-complete (v0.1.0)

The tool surface is closed for this release. 99 typed tools across 17 domains,
plus the universal `b24_call` / `b24_batch` backbone and a method catalogue from
the official documentation (100% API coverage — both calling and knowing the
signatures), reads and writes, both transports, and three ways of receiving
portal events with a history archive and Telegram forwarding. Offline
verification is green (132 tests); live verification against a real portal was
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
  limitation of REST rather than of this project. **Superseded in part by the
  closeout audit below:** `kanban.addTask` alone does *not* relocate a card that
  is already on the board — the working move is `deleteTask` then `addTask`.
- **Release closeout audit (v0.1.0).** The whole surface was re-verified against
  the production portal, one domain at a time, and the sweep found twelve defects
  that the previous "feature-complete" claim had shipped past. All are fixed —
  and the SDK version pin that one of them forced was then removed by porting to
  2.x rather than carried:
  - `b24_task_comments_list` returned `[]` for a task that demonstrably had
    comments. This portal keeps them in the task **chat**, not the legacy forum
    topic (`forumTopicId` is null), and the tool read only the forum — a write
    that succeeded looked like a write that failed. It now reads the forum,
    falls back to the chat, and reports which source answered.
  - `b24_calendar_event_list` returned every field of every event with no way to
    narrow it: one month for one user measured **232 events × 60 fields ≈ 1 MB**,
    enough to exhaust an agent's context in a single call. Added `select` and
    `limit` with a compact default (~16 KB for the same range), an honest `total`
    and a `truncated` flag; `select=['*']` still returns everything on request.
  - `b24_method_search` scored substrings with every word weighted equally, so
    "on" matched inside `sonet_group` and common words drowned rare ones. Of 15
    realistic intent queries only 4 reached the right method — "search users by
    name" never returned `user.search` at all. Replaced with segment matching,
    IDF weighting, a synonym map for Bitrix's naming (a board move is
    `kanban.addTask`, a workgroup is a `sonet_group`), and a demotion for event
    handlers. Now 15/15, locked in by tests.
  - `_extract_list` did not recognise a named wrapper key holding a dict keyed by
    id, so `crm.documentgenerator.template.list` delivered **19 templates as one
    malformed record** while `total` said 19 — a self-contradicting result that
    raised no error.
  - The server reported the **MCP SDK's version** (1.28.1) as its own in
    `serverInfo`, because FastMCP does not forward one. It now reports the
    package version, read from installed metadata rather than hardcoded twice.
  - `scripts/events_tools_check.py` printed `FAIL` and exited **0**, so a broken
    run looked green. It now asserts and returns a real exit code — proven in
    both directions by forcing a failure.
  - `pydantic`, `starlette` and `uvicorn` were imported directly but declared
    nowhere, inherited by luck from `mcp[cli]`. Now declared.
  - Ten of the fifteen scripts, including every verification script, were
    documented nowhere. README (both languages) now tables them.
  - Documentation drift: the test count said 106, and the Russian docs claimed
    1901 catalogue methods where the English said 1930. Both corrected to the
    measured values.
  - **`b24_scrum_task_move` did not move anything, and the docs said it did.**
    Reported by the operator: a card moved for him but not for colleagues,
    while the change log showed the move to everyone. Re-tested on the live
    board: `kanban.addTask` only *places* a card that is off the board — called
    on one already in a column it returns `true` and does nothing, which is why
    nobody's board changed. `tasks.task.update` with `STAGE_ID` is worse: it
    writes the field and a visible history entry while the card stays put,
    which is exactly the half-moved state that was observed. The move that
    works is `kanban.deleteTask` then `kanban.addTask`, confirmed by watching
    the board. Note that `STAGE_ID` cannot verify any of this — it read `0`
    while the card was visibly in the target column. Also learned along the
    way: `tasks.api.scrum.task.update` with `entityId` genuinely moves a task
    between backlog and sprint, and that entity has no column field at all.
  - **Telegram forwarding was verified end to end for the first time** —
    portal → pull channel → store → filter → chat, with no public URL involved
    (the pull channel is outbound-only). It worked, and it exposed a usability
    defect the offline filter tests could not: the portal fires
    `tasks/user_counter` and `tasks/user_efficiency_counter` on nearly every
    action, `tasks/*` catches them, and one task created + renamed + commented
    produced four counter messages against four useful ones. The `work` and
    `tasks` presets now exclude `*counter*`; `everything` deliberately does not.
  - **Ported to the MCP SDK 2.x.** Rather than carry a pin, the server moved to
    `mcp.server.mcpserver.MCPServer`. The change is smaller than the rename
    suggests — one import line in each of 20 modules, five lines in `server.py`
    and the transport wiring in `__main__.py`, where `mcp.settings` no longer
    exists and host/port/stateless are passed to `run_streamable_http_async()`
    instead of mutated globally. Two of those edits are improvements: `version`
    is now a real constructor parameter, so the private `_mcp_server.version`
    stamp is gone, and the entry point no longer mutates SDK global state.
    Re-verified after the move: 132 tests twice, all five offline check
    scripts, stdio and HTTP transports, the receiver fail-closed, the pull
    channel and Telegram forwarder against live portal events, and a clean-venv
    install of the rebuilt wheel calling the production portal.
  - **The built wheel could not be installed.** `mcp[cli]>=1.2.0` carried no
    upper bound, so a clean install resolved **mcp 2.0.0**, which replaced
    `mcp.server.fastmcp` with `mcp.server.mcpserver` — the package failed at
    `import`, before any tool ran. Development never saw it because `uv.lock`
    pins 1.28.1; only installing the wheel into a throwaway venv exposed it.
    Bounded to `<2` and re-verified by installing the rebuilt wheel into a clean
    environment and running it against the live portal from outside the repo.

  Not defects, confirmed and recorded so the next audit does not re-flag them:
  CRM answers `ACCESS_DENIED` for this webhook's user (a portal permission, and
  the empty-error-code fix correctly preserves the message); `tasks.task.get` on
  a deleted task returns `[]` rather than an error, which is Bitrix's own
  behaviour passed through faithfully; telephony answers
  `ERROR_METHOD_NOT_FOUND` because the scope is genuinely not granted, exactly
  as `b24_scope_gaps` predicts.

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
