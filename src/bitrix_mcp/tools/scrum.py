"""Scrum tools — sprints and kanban columns for Scrum-enabled groups.

Encodes the hard-won correct flow (previous wrapper got this wrong):
* the real kanban columns belong to the *active sprint*, not to the group
  (``task.stages.get`` only returns the 3 default group stages);
* ``tasks.api.scrum.sprint.list`` must be filtered by ``STATUS:"active"`` or it
  may never reach the current sprint due to pagination.

**Reading and writing a task's column are two separate traps, both confirmed
live against a production portal — and the fix is asymmetric:**

* **Write:** ``tasks.task.update`` with ``STAGE_ID`` is accepted with no
  error and the new value reads back correctly, but for a task on an active
  sprint it does **not** relocate the card on the real board (reload the page
  and it's still in the old column). The board-aware write is
  ``tasks.api.scrum.kanban.addTask`` — use ``b24_scrum_task_move`` for that,
  never ``b24_task_update``.
* **Read:** once a task has been moved the *correct* way (``kanban.addTask``,
  or a real drag on the board), its ``tasks.task.STAGE_ID`` goes stale and
  stops tracking the board at all — confirmed by moving a task twice via
  ``kanban.addTask`` and observing ``STAGE_ID`` never change, with no
  replication delay. There is no documented ``tasks.api.scrum.kanban.*``
  method to list which tasks are actually in a given stage (only
  ``addTask``/``deleteTask``/``getStages``/``getFields`` exist). **Filtering
  ``b24_tasks_list`` by ``STAGE_ID`` is therefore not reliable for a sprint
  board** — treat it as approximate at best, accurate only for tasks whose
  stage was last touched via ``tasks.task.add``/``.update`` and never moved
  through the real board afterward.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context

from ..runtime import (
    READ,
    WRITE,
    PersonalWebhook,
    Start,
    FetchAll,
    WebhookUrl,
    err,
    get_client,
    ok,
    run_list,
)
from ..server import mcp


@mcp.tool(name="b24_scrum_sprint_list", annotations=READ)
async def b24_scrum_sprint_list(
    group_id: Annotated[int, Field(description="Scrum group/project id (GROUP_ID).")],
    status: Annotated[Optional[str], Field(default="active", description="Sprint status filter: 'active' (default), 'planned', 'completed', or null for all. Defaulting to 'active' avoids the pagination trap where the current sprint is never reached.")] = "active",
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List sprints of a Scrum group (tasks.api.scrum.sprint.list).

    Returns a pagination envelope of sprint objects (id, name, dateStart,
    dateEnd, status, ...). Use the active sprint's id with
    b24_scrum_kanban_stages to get the real board columns.
    """
    flt: dict = {"GROUP_ID": group_id}
    if status:
        flt["STATUS"] = status
    return await run_list(
        ctx, "tasks.api.scrum.sprint.list", {"filter": flt},
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_scrum_kanban_stages", annotations=READ)
async def b24_scrum_kanban_stages(
    sprint_id: Annotated[int, Field(description="Sprint id whose kanban columns you want.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Get the real kanban columns of a sprint (tasks.api.scrum.kanban.getStages).

    Returns the column definitions (id, name, ...). Feed a column id as STAGE_ID
    into b24_tasks_list (with the same GROUP_ID) to list tasks in that column.
    """
    from ..runtime import run_call
    return await run_call(
        ctx, "tasks.api.scrum.kanban.getStages", {"sprintId": sprint_id},
        webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True,
    )


@mcp.tool(name="b24_scrum_board", annotations=READ)
async def b24_scrum_board(
    group_id: Annotated[int, Field(description="Scrum group/project id (GROUP_ID).")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """One-shot board snapshot: the active sprint plus its kanban columns.

    Convenience that runs the correct two-step flow for you and returns
    {"sprint": {...active sprint...}, "stages": [...columns...]}. Returns a clear
    message if the group has no active sprint.
    """
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        sprints = await client.call_list(
            "tasks.api.scrum.sprint.list", {"filter": {"GROUP_ID": group_id, "STATUS": "active"}}
        )
        items = sprints.get("items") or []
        if not items:
            return ok({"sprint": None, "stages": [], "note": f"No active sprint for group {group_id}."})
        sprint = items[0]
        sprint_id = sprint.get("id") or sprint.get("ID")
        stages = await client.call_result("tasks.api.scrum.kanban.getStages", {"sprintId": sprint_id})
        return ok({"sprint": sprint, "stages": stages})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


@mcp.tool(name="b24_scrum_task_move", annotations=WRITE)
async def b24_scrum_task_move(
    task_id: Annotated[int, Field(description="Task id to move.")],
    sprint_id: Annotated[int, Field(description="Sprint id the task belongs to (from b24_scrum_sprint_list or b24_scrum_board).")],
    stage_id: Annotated[int, Field(description="Target kanban column id (from b24_scrum_kanban_stages or b24_scrum_board).")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Move a task to a column on a Scrum sprint board (tasks.api.scrum.kanban.addTask).

    Use this instead of b24_task_update's STAGE_ID to relocate a task that
    belongs to an active sprint. Confirmed live: tasks.task.update with
    STAGE_ID is accepted with no error and the new value reads back correctly,
    but the card does not actually move on the real board (reload and it's
    still in the old column) — this method is Bitrix's board-aware write and
    is the one that does move it.
    """
    from ..runtime import run_call
    return await run_call(
        ctx, "tasks.api.scrum.kanban.addTask",
        {"sprintId": sprint_id, "taskId": task_id, "stageId": stage_id},
        webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True,
    )
