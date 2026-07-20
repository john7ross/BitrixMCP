"""Task tools — tasks.task.* plus task comments.

For Scrum boards (sprints/kanban columns) see the scrum module — those live
under different methods and need the active sprint id, not task.stages.get.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context

from ..runtime import (
    READ,
    WRITE,
    DESTRUCTIVE,
    Filter,
    Order,
    PersonalWebhook,
    Select,
    Start,
    FetchAll,
    WebhookUrl,
    run_call,
    run_list,
)
from ..server import mcp


@mcp.tool(name="b24_tasks_list", annotations=READ)
async def b24_tasks_list(
    filter: Filter = None,
    select: Select = None,
    order: Order = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List tasks with filtering, sorting, and pagination (tasks.task.list).

    Common filter keys: RESPONSIBLE_ID, CREATED_BY, GROUP_ID (project/group),
    STATUS (1 new,2 pending,3 in progress,4 supposedly done,5 completed,6 deferred),
    STAGE_ID (kanban column), >=CREATED_DATE, etc.
    Common select: ['ID','TITLE','STATUS','RESPONSIBLE_ID','GROUP_ID','CREATED_BY',
    'DEADLINE','STAGE_ID','COMMENTS_COUNT','TAGS'].

    Returns:
        JSON pagination envelope {items, count, total, next, has_more, truncated}.
    """
    params: dict = {}
    if filter:
        params["filter"] = filter
    if select:
        params["select"] = select
    if order:
        params["order"] = order
    return await run_list(
        ctx, "tasks.task.list", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_task_get", annotations=READ)
async def b24_task_get(
    id: Annotated[int, Field(description="Task id.")],
    select: Select = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Fetch a single task by id (tasks.task.get). Returns {"task": {...}}."""
    params: dict = {"taskId": id}
    if select:
        params["select"] = select
    return await run_call(ctx, "tasks.task.get", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_task_add", annotations=WRITE)
async def b24_task_add(
    fields: Annotated[dict, Field(description="Task fields. TITLE and RESPONSIBLE_ID are required, e.g. {'TITLE':'Do X','RESPONSIBLE_ID':1,'DEADLINE':'2026-08-01T18:00:00','GROUP_ID':10}.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Create a task (tasks.task.add). Returns {"task": {...}} with the new id."""
    return await run_call(ctx, "tasks.task.add", {"fields": fields},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_task_update", annotations=WRITE)
async def b24_task_update(
    id: Annotated[int, Field(description="Task id to update.")],
    fields: Annotated[dict, Field(description="Fields to change, e.g. {'STATUS':5} or {'STAGE_ID':123,'DEADLINE':'2026-08-05'}.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Update a task (tasks.task.update). Also used to move kanban stage (STAGE_ID)."""
    return await run_call(ctx, "tasks.task.update", {"taskId": id, "fields": fields},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_task_complete", annotations=WRITE)
async def b24_task_complete(
    id: Annotated[int, Field(description="Task id to mark complete.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Mark a task complete (tasks.task.complete)."""
    return await run_call(ctx, "tasks.task.complete", {"taskId": id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_task_delete", annotations=DESTRUCTIVE)
async def b24_task_delete(
    id: Annotated[int, Field(description="Task id to delete.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Delete a task (tasks.task.delete). Irreversible."""
    return await run_call(ctx, "tasks.task.delete", {"taskId": id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_task_comments_list", annotations=READ)
async def b24_task_comments_list(
    id: Annotated[int, Field(description="Task id.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List a task's comments (task.commentitem.getlist). Returns the comment list."""
    return await run_call(ctx, "task.commentitem.getlist", {"TASKID": id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_task_comment_add", annotations=WRITE)
async def b24_task_comment_add(
    id: Annotated[int, Field(description="Task id to comment on.")],
    text: Annotated[str, Field(description="Comment text.", min_length=1)],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Add a comment to a task (task.commentitem.add). Returns the new comment id."""
    return await run_call(ctx, "task.commentitem.add", {"TASKID": id, "FIELDS": {"POST_MESSAGE": text}},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_task_stages_get", annotations=READ)
async def b24_task_stages_get(
    group_id: Annotated[int, Field(description="Group/project id whose kanban columns you want (entityId).")],
    is_admin: Annotated[bool, Field(default=False, description="True to read the group's shared kanban; false for the personal 'My planner' kanban.")] = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Get kanban stages of a regular (non-Scrum) project/group (task.stages.get).

    For Scrum groups use b24_scrum_kanban_stages instead — their columns belong
    to the active sprint, not the group.
    """
    return await run_call(ctx, "task.stages.get", {"entityId": group_id, "isAdmin": "Y" if is_admin else "N"},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_task_checklist_list", annotations=READ)
async def b24_task_checklist_list(
    id: Annotated[int, Field(description="Task id.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List a task's checklist items (task.checklistitem.getlist)."""
    return await run_call(ctx, "task.checklistitem.getlist", {"TASKID": id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_task_checklist_add", annotations=WRITE)
async def b24_task_checklist_add(
    id: Annotated[int, Field(description="Task id.")],
    title: Annotated[str, Field(description="Checklist item text.", min_length=1)],
    is_complete: Annotated[bool, Field(default=False, description="Mark the item already complete.")] = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Add a checklist item to a task (task.checklistitem.add)."""
    fields = {"TITLE": title, "IS_COMPLETE": "Y" if is_complete else "N"}
    return await run_call(ctx, "task.checklistitem.add", {"TASKID": id, "FIELDS": fields},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_task_elapsed_add", annotations=WRITE)
async def b24_task_elapsed_add(
    id: Annotated[int, Field(description="Task id.")],
    seconds: Annotated[int, Field(description="Time spent, in seconds.", ge=1)],
    comment: Annotated[Optional[str], Field(default=None, description="Optional note for the time entry.")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Log time spent on a task (task.elapseditem.add). Returns the entry id."""
    fields: dict = {"SECONDS": seconds}
    if comment:
        fields["COMMENT_TEXT"] = comment
    return await run_call(ctx, "task.elapseditem.add", {"TASKID": id, "ARFIELDS": fields},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_task_result_list", annotations=READ)
async def b24_task_result_list(
    id: Annotated[int, Field(description="Task id.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List a task's results (tasks.task.result.list) — the marked outcomes of the task."""
    return await run_call(ctx, "tasks.task.result.list", {"taskId": id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)
