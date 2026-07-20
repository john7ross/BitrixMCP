"""Scrum tools — sprints and kanban columns for Scrum-enabled groups.

Encodes the hard-won correct flow (previous wrapper got this wrong):
* the real kanban columns belong to the *active sprint*, not to the group
  (``task.stages.get`` only returns the 3 default group stages);
* ``tasks.api.scrum.sprint.list`` must be filtered by ``STATUS:"active"`` or it
  may never reach the current sprint due to pagination.
Once you have the stage ids, list the tasks in a column with
``b24_tasks_list`` filtered by ``{GROUP_ID, STAGE_ID}``.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context

from ..runtime import (
    READ,
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
