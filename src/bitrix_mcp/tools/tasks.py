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
    err,
    get_client,
    ok,
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

    Caveat for Scrum sprint boards (confirmed live, not just theoretical):
    STAGE_ID here is reliable for a *regular* group kanban, but for a task on
    an active sprint it goes stale the moment the card is moved the correct
    way (b24_scrum_task_move / a real drag on the board) — Bitrix tracks that
    move in a separate structure with no public method to read it back.
    Filtering by STAGE_ID on a sprint board is approximate at best.

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
    """Update a task (tasks.task.update).

    Also used to move kanban stage (STAGE_ID) — but only for a *regular*
    group's kanban (task.stages.get). For a task on an active Scrum sprint
    board, STAGE_ID here is accepted with no error and reads back correctly,
    but does NOT relocate the card on the real board (confirmed live: reload
    the page and it's still in the old column). Use b24_scrum_task_move
    instead when the task's sprintId is set.
    """
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
    limit: Annotated[int, Field(default=50, ge=1, le=200, description="Max comments to return when they live in the task chat.")] = 50,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List a task's comments, from wherever this portal actually keeps them.

    Bitrix stores task comments in one of two places and does not tell you
    which: the legacy forum topic (``task.commentitem.getlist``) or the task's
    chat. On a chat-based portal ``forumTopicId`` is null and the forum method
    answers with an empty list even when comments exist — confirmed live: a
    comment added through b24_task_comment_add returned a real id, was visible
    in the chat, and the forum method still reported nothing. Reading only the
    forum therefore produces a false "no comments", which reads exactly like a
    write that failed.

    So: read the forum first, and when it comes back empty fall back to the
    task chat. ``source`` says which one answered, so an empty result is
    trustworthy rather than ambiguous.

    Returns:
        JSON: {"comments": [...], "count": <int>, "source": "forum"|"chat"|"none",
        "task_id": <int>}. Chat-sourced entries carry "is_system": true for
        Bitrix's own notices (task created, time logged), which have no author.
    """
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        forum = await client.call_result("task.commentitem.getlist", {"TASKID": id})
        if forum:
            return ok({"task_id": id, "source": "forum", "count": len(forum), "comments": forum})

        task = await client.call_result(
            "tasks.task.get", {"taskId": id, "select": ["ID", "CHAT_ID", "FORUM_TOPIC_ID"]}
        )
        # A deleted/inaccessible task answers [] here, not an error (Bitrix's own
        # behaviour) - say so instead of implying the task exists with 0 comments.
        if not isinstance(task, dict) or not task.get("task"):
            return ok({"task_id": id, "source": "none", "count": 0, "comments": [],
                       "note": f"Task {id} returned no data - it may be deleted or not accessible."})

        chat_id = (task.get("task") or {}).get("chatId")
        if not chat_id:
            return ok({"task_id": id, "source": "forum", "count": 0, "comments": [],
                       "note": "No forum comments and this task has no chat."})

        messages = await client.call_result(
            "im.dialog.messages.get", {"DIALOG_ID": f"chat{chat_id}", "LIMIT": limit}
        )
        raw = (messages or {}).get("messages") or []
        comments = [{
            "id": m.get("id"),
            "author_id": m.get("author_id"),
            "is_system": not m.get("author_id"),
            "text": m.get("text"),
            "date": m.get("date"),
        } for m in raw]
        return ok({"task_id": id, "source": "chat", "chat_id": chat_id,
                   "count": len(comments), "comments": comments})
    except Exception as exc:  # noqa: BLE001
        return err(exc)


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
