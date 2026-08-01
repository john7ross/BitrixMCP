"""Scrum tools — sprints and kanban columns for Scrum-enabled groups.

Encodes the hard-won correct flow (previous wrapper got this wrong):
* the real kanban columns belong to the *active sprint*, not to the group
  (``task.stages.get`` only returns the 3 default group stages);
* ``tasks.api.scrum.sprint.list`` must be filtered by ``STATUS:"active"`` or it
  may never reach the current sprint due to pagination.

**Reading and writing a task's column are two separate traps, both established
by watching a production board rather than by trusting API responses — every
call involved reports success regardless of what happened:**

* **Write:** no single call moves a card. ``tasks.task.update`` with
  ``STAGE_ID`` changes the field and writes a history entry everyone can see,
  yet the card stays put. ``kanban.addTask`` only *places* a card that is off
  the board; for one already in a column it returns ``true`` and does nothing.
  ``task.stages.movetask`` returns ``false`` (it governs the plain group
  kanban). What works is ``kanban.deleteTask`` then ``kanban.addTask`` — see
  ``b24_scrum_task_move``, which does exactly that and is lossless.
* **Read:** ``tasks.task.STAGE_ID`` cannot be trusted on a sprint board — it
  read ``0`` while the card was visibly in the target column, and tasks carry
  stage ids left over from previous sprints. There is no documented
  ``tasks.api.scrum.kanban.*`` method listing which tasks are in a stage, and
  ``tasks.api.scrum.task.get`` exposes the sprint (``entityId``), story points
  and sort order but **no column at all**. **Filtering ``b24_tasks_list`` by
  ``STAGE_ID`` is therefore not reliable for a sprint board.** The pull
  channel is the dependable read: ``tasks / task_update`` carries
  ``BEFORE.STAGE``, ``AFTER.STAGE`` and ``AFTER.STAGE_INFO``.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.mcpserver import Context

from ..runtime import (
    READ,
    WRITE,
    PersonalWebhook,
    Start,
    FetchAll,
    WebhookUrl,
    err,
    get_client,
    guard_write,
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
    """Move a task to a column on a Scrum sprint board.

    Bitrix has no single "move" call, and the obvious candidates each fail in a
    way that looks like success — all of the following was established by
    watching a real board, not by reading API responses:

    * ``tasks.api.scrum.kanban.addTask`` only *places* a card that is not
      currently on the board. For a card already sitting in a column it returns
      ``true`` and does nothing at all — no field change, no history entry, no
      movement.
    * ``tasks.task.update`` with ``STAGE_ID`` does change the field and does
      write a history entry that everyone can see, but the card does not move.
      That is the worst outcome: colleagues see "stage changed" in the log while
      the board still shows the old column.
    * ``task.stages.movetask`` answers ``false`` — it governs the plain group
      kanban, not sprint boards.

    What works is taking the card off the board and putting it back in the
    target column: ``kanban.deleteTask`` then ``kanban.addTask``. This tool does
    that, in that order.

    ``deleteTask`` does **not** delete the task and does not produce a second
    card. It removes only the column placement; the task, its sprint membership
    and everything on it survive, so ``addTask`` puts the same card back.
    Measured before and after a move: identical id, GUID, creation date, story
    points, checklist, logged time and tags — the only difference was one added
    history row. Moving the same card repeatedly is safe.

    Do not verify the result with ``b24_task_get``: ``STAGE_ID`` is unreliable
    for sprint boards — it read ``0`` while the card was visibly sitting in the
    target column. The board is the source of truth; the pull channel's
    ``tasks / task_update`` event carries ``AFTER.STAGE_INFO`` and is the only
    reliable read.

    Returns:
        JSON: {"moved": true, "task_id", "sprint_id", "stage_id",
        "steps": {"removed_from_board": ..., "added_to_stage": ...},
        "note": "..."}.
    """
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        guard_write("tasks.api.scrum.kanban.deleteTask", is_write=True)
        removed = await client.call_result(
            "tasks.api.scrum.kanban.deleteTask", {"sprintId": sprint_id, "taskId": task_id}
        )
        added = await client.call_result(
            "tasks.api.scrum.kanban.addTask",
            {"sprintId": sprint_id, "taskId": task_id, "stageId": stage_id},
        )
        return ok({
            "moved": bool(added),
            "task_id": task_id,
            "sprint_id": sprint_id,
            "stage_id": stage_id,
            "steps": {"removed_from_board": removed, "added_to_stage": added},
            "note": ("Verify on the board, not via b24_task_get - STAGE_ID is "
                     "unreliable for sprint boards and may still read 0 or the "
                     "previous column."),
        })
    except Exception as exc:  # noqa: BLE001
        return err(exc)
