# Architecture

**English** · [Русский](ARCHITECTURE.ru.md)

`bitrix_mcp` is a stateless gateway that exposes the Bitrix24 REST API as MCP
tools. It has one job: translate MCP tool calls into authenticated Bitrix REST
calls and return the results faithfully. It holds no database and no session
state of its own — each tool call resolves a webhook, makes an HTTP request, and
formats the response.

## Technologies

| Concern | Choice | Why |
|---|---|---|
| Language | Python ≥ 3.10 | Compact REST wrapper; broad portability |
| MCP framework | `mcp[cli]` (official SDK, FastMCP) | Decorator tools, schema generation, both transports |
| HTTP | `httpx` (async) | Async client, timeouts, redirects |
| Validation | Pydantic v2 (via FastMCP) | Field constraints + auto JSON schema |
| Packaging | Hatchling + uv | `bitrix-mcp` console entry point; installable wheel/sdist |
| Diagrams | PlantUML (C4 stdlib) | Renders offline |

## Components

See [docs/diagrams/bitrix_mcp_component.png](docs/diagrams/bitrix_mcp_component.png)
(source: [component.puml](docs/diagrams/component.puml)).

- **Entry point** (`__main__.py`) — parses `--transport/--http/--host/--port`
  (and `BITRIX_*` env), then runs FastMCP over **stdio** or **Streamable HTTP**
  (stateless JSON). These are the two shipped front-ends of the same server.
- **Server + registry** (`server.py`) — the `FastMCP("bitrix24_mcp")` instance
  carrying the server instructions; imports the 16 tool modules via `importlib`
  so their `@mcp.tool` decorators register all 87 tools.
- **Tool modules** (`tools/*.py`) — one module per domain (universal, crm, tasks,
  scrum, calendar, disk, users, groups, messaging, lists, catalog, sale,
  documents, bizproc, telephony). Each tool is a thin, typed function that builds
  `(method, params)` and delegates to the runtime.
- **Runtime** (`runtime.py`) — the shared spine: webhook resolution, the
  read-only guard, JSON formatting, the reusable `Annotated` parameter types,
  and the `run_call` / `run_list` helpers every tool uses.
- **Bitrix client** (`client.py`) — the only code that talks HTTP: JSON-body
  POSTs, honest pagination (`start`/`next`/`total`), `php_query` encoding for
  batch command strings, the write classifier, and error surfacing
  (`BitrixError`, never swallowed).
- **Config** (`config.py`) — reads `BITRIX_WEBHOOK_URL`, `BITRIX_READ_ONLY`,
  `BITRIX_TIMEOUT`, `BITRIX_MAX_PAGES` from the environment.

## Logical view — a call's path

See [docs/diagrams/bitrix_mcp_sequence.png](docs/diagrams/bitrix_mcp_sequence.png)
(source: [sequence.puml](docs/diagrams/sequence.puml)).

1. Client sends `tools/call`; FastMCP validates arguments against the tool's
   generated schema and injects `Context`.
2. The tool calls `run_call`/`run_list` with a REST method and params.
3. Runtime resolves the webhook (`personal_webhook` > `webhook_url` >
   `X-B24-Webhook` header > `BITRIX_WEBHOOK_URL`) and applies the read-only guard.
4. `BitrixClient` POSTs a JSON body to `…/rest/<id>/<token>/<method>.json`.
5. On success the response is trimmed/paginated into an envelope; on a Bitrix
   `error` it becomes a structured `{error, code, message}` — the code (e.g.
   `ACCESS_DENIED`) is preserved, never hidden as an empty result.

## Authorization model

Two webhook levels, resolved per call. The default webhook (`BITRIX_WEBHOOK_URL`)
serves the common case; `webhook_url` overrides it for a single call; and
`personal_webhook` acts as a specific user (required to write under that user's
permissions). Over HTTP, an `X-B24-Webhook` request header can carry a per-client
default. The optional `BITRIX_READ_ONLY=1` guard refuses write methods before
they reach the portal (classified by method verb; typed write tools are always
classified correctly).

## Physical / deployment views

- **stdio (portable):** the consuming agent launches `bitrix-mcp` as a
  subprocess. No network service, no bridge — the most reliable option and the
  default. One process per agent.
- **Streamable HTTP (shared):** run `bitrix-mcp --http` as a network service;
  many clients connect to `/mcp`. Stateless JSON responses avoid the fragile
  long-lived SSE sessions that affected the previous deployment. Bind to
  `127.0.0.1` by default; `--host 0.0.0.0` to expose.

The server is stateless, so horizontal scaling is trivial (run more HTTP
instances behind a load balancer); the portal itself is the only stateful
dependency.

## Coverage model

100% of the REST API is reachable through `b24_call` / `b24_batch`. The 87 typed
tools are an ergonomic layer over the high-traffic domains with the Bitrix quirks
handled (JSON-body filters, auto `ownerId`, active-sprint kanban, honest errors).
Long-tail modules (mail, open lines, sale basket writes, app placements) are used
via the universal tools.
