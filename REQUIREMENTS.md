# Requirements

**English** · [Русский](REQUIREMENTS.ru.md)

Hard requirements for this project. Unlike `ROADMAP.md`, this is not a list of
plans — it is what the implementation must satisfy. Changing any entry here
takes a deliberate decision, not quiet drift.

## R-1. 100% coverage of the Bitrix24 REST API

**Status: mandatory. Owner: Sergey Lebedev.**

Every method of the Bitrix24 REST API must be callable through this server. No
method may be out of reach because nobody wrote a tool for it.

### Acceptance criteria

The requirement is met when, for an arbitrary documented method, the agent can:
(1) learn that the method exists, (2) learn its exact parameters, (3) call it.
All three without leaving this server and without guessing a signature.

### How it is achieved

Through **two layers**, not through 1:1 wrappers:

1. **Execution** — `b24_call` and `b24_batch` already reach every method. This
   is the core of the requirement and it is closed.
2. **Knowledge** — a method catalogue generated from the documentation source
   that produces apidocs.bitrix24.ru (`github.com/bitrix24/b24restdocs`): method
   name, scope, parameters with types and requiredness, deprecation flag. Plus
   tools to search for a method and fetch its schema.
3. **Diagnosis** — comparing the scopes actually granted to the webhook against
   the catalogue, so `ACCESS_DENIED` and `ERROR_METHOD_NOT_FOUND` turn into a
   concrete list of permissions to add rather than a dead end.

### Why not one tool per method

The documentation describes 1930 methods. That many tool descriptions fit in no
context window, and choosing among them would be a lottery. Typed tools remain
what they always were — ergonomics for frequent work, not the coverage
mechanism. The "no 1:1 typed coverage" entry in `ROADMAP.md` does not contradict
this requirement: coverage comes from layers 1–3, not from wrapper count.

### What breaks the requirement

- A method that cannot be called because the server does not know it exists and
  cannot supply its schema.
- Silent narrowing: if a typed tool does not support some parameter of a method,
  the agent must still have the `b24_call` route.
- A catalogue that has fallen behind the documentation. It is rebuilt by script
  from the primary source — updating must be reproducible, not manual.

### Verification

- The catalogue holds no fewer methods than the current documentation.
- For every method behind a typed tool, the catalogue knows its schema.
- Scope diagnosis against a real portal yields the list of missing permissions.

## R-2. Deployment-agnostic

**Status: mandatory.**

The server does not adapt itself to one portal, one user or one network. User
permissions, installed modules and whether the portal can be reached from
outside are properties of the environment, not constants.

Consequences:

- A tool is **not removed** because the developer's own portal lacks a
  permission or a module. `ACCESS_DENIED` diagnoses the environment; it is not a
  defect in the code.
- Change detection offers **three paths**, each switchable from `.env`, because
  none of them works everywhere:
  - **pull channel** (Push & Pull) — an outbound connection to the portal's push
    server, the same mechanism the mobile and desktop apps rely on. Real time,
    needs no inbound URL, no firewall rule and no administrator rights; works
    behind NAT and VPN. The right default for a local deployment. Endpoints and
    channels come from `pull.config.get`, which — unlike
    `pull.application.config.get` — is available to a plain webhook. Channels
    live 12 hours and are refreshed.
  - **outgoing-webhook receiver** — for deployments where the server is
    permanently reachable over HTTP. The only path with a documented event
    catalogue.
  - **polling by modification date** — the universal fallback when the push
    server is off. Blind to deletions and to intermediate states.
- The stdio transport keeps no external state when nothing in the event
  subsystem is configured.

## R-3. Honest output

**Status: mandatory.**

- A Bitrix error surfaces with its own code and is never replaced by an empty
  result.
- Credentials never reach the output: webhook URLs and `application_token` are
  stripped before a response goes to the model or into the database.
- A limitation that cannot be fixed is documented as a limitation, not presented
  as resolved.
