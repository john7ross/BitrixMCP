"""Messaging tools — chat messages, personal notifications, live feed.

These are user-visible side effects. Descriptions call out who sees what so an
agent (and the read-only guard) treat them appropriately.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import Field
from mcp.server.fastmcp import Context

from ..runtime import (
    READ,
    WRITE,
    PersonalWebhook,
    WebhookUrl,
    run_call,
)
from ..server import mcp


@mcp.tool(name="b24_im_recent", annotations=READ)
async def b24_im_recent(
    skip_chat: Annotated[bool, Field(default=False, description="Skip group chats and show only 1:1 dialogs.")] = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List the acting user's recent chats/dialogs (im.recent.get).

    Returns recent conversations with their DIALOG_ID, last message, and unread
    counters. Use a DIALOG_ID with b24_im_dialog_messages to read history.
    """
    from ..runtime import run_call as _rc
    params: dict = {}
    if skip_chat:
        params["SKIP_OPENLINES"] = "Y"
    return await _rc(ctx, "im.recent.get", params,
                     webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_im_dialog_messages", annotations=READ)
async def b24_im_dialog_messages(
    dialog_id: Annotated[str, Field(description="Dialog id: 'chat123' for a group chat, or a numeric user id (as string) for a 1:1 dialog.")],
    limit: Annotated[int, Field(default=20, ge=1, le=200, description="How many recent messages to return (default 20).")] = 20,
    last_id: Annotated[Optional[int], Field(default=None, description="Return messages older than this message id (for paging back through history).")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Read message history of a chat/dialog (im.dialog.messages.get).

    Returns messages plus the referenced users/files. Page back with last_id.
    """
    from ..runtime import run_call as _rc
    params: dict = {"DIALOG_ID": dialog_id, "LIMIT": limit}
    if last_id is not None:
        params["LAST_ID"] = last_id
    return await _rc(ctx, "im.dialog.messages.get", params,
                     webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_im_user_get", annotations=READ)
async def b24_im_user_get(
    user_id: Annotated[Optional[int], Field(default=None, description="User id to fetch. Omit for the acting user.")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Get a user's IM profile (im.user.get): name, avatar, work status, online."""
    from ..runtime import run_call as _rc
    params: dict = {}
    if user_id is not None:
        params["ID"] = user_id
    return await _rc(ctx, "im.user.get", params,
                     webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_im_chat_create", annotations=WRITE)
async def b24_im_chat_create(
    title: Annotated[str, Field(description="Chat title.", min_length=1)],
    users: Annotated[list, Field(description="User ids to add as members, e.g. [12, 34].")],
    description: Annotated[Optional[str], Field(default=None, description="Optional chat description.")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Create a group chat (im.chat.add). Returns the new chat id (use 'chat<ID>' as DIALOG_ID)."""
    from ..runtime import run_call as _rc
    params: dict = {"TITLE": title, "USERS": users}
    if description:
        params["DESCRIPTION"] = description
    return await _rc(ctx, "im.chat.add", params,
                     webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_im_chat_user_add", annotations=WRITE)
async def b24_im_chat_user_add(
    chat_id: Annotated[int, Field(description="Numeric chat id (without the 'chat' prefix).")],
    users: Annotated[list, Field(description="User ids to add, e.g. [7, 9].")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Add users to a group chat (im.chat.user.add)."""
    from ..runtime import run_call as _rc
    return await _rc(ctx, "im.chat.user.add", {"CHAT_ID": chat_id, "USERS": users},
                     webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_im_message_add", annotations=WRITE)
async def b24_im_message_add(
    dialog_id: Annotated[str, Field(description="Target dialog: a chat id like 'chat123', or a numeric user id (as string) for a private message, e.g. '123'.")],
    message: Annotated[str, Field(description="Message text (BB-code supported).", min_length=1)],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Send an IM message to a chat or user (im.message.add).

    Visible to chat participants / the recipient. Returns the new message id.
    """
    return await run_call(ctx, "im.message.add", {"DIALOG_ID": dialog_id, "MESSAGE": message},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_im_notify_personal", annotations=WRITE)
async def b24_im_notify_personal(
    user_id: Annotated[int, Field(description="Recipient user id.")],
    message: Annotated[str, Field(description="Notification text.", min_length=1)],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Send a personal notification to a user (im.notify.personal.add).

    Appears in the recipient's notification center. Returns the notification id.
    """
    return await run_call(ctx, "im.notify.personal.add", {"USER_ID": user_id, "MESSAGE": message},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_feed_post_add", annotations=WRITE)
async def b24_feed_post_add(
    message: Annotated[str, Field(description="Post body.", min_length=1)],
    title: Annotated[Optional[str], Field(default=None, description="Optional post title.")] = None,
    dest: Annotated[Optional[list], Field(default=None, description="Recipients, e.g. ['UA'] (all employees), ['U12'] (a user), ['DR3'] (a department). Defaults to the author only if omitted.")] = None,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Publish a post to the Live Feed (log.blogpost.add).

    PUBLIC / broadly visible depending on `dest`. Use with care. Returns the
    new post id.
    """
    params: dict = {"POST_MESSAGE": message}
    if title:
        params["POST_TITLE"] = title
    if dest:
        params["DEST"] = dest
    return await run_call(ctx, "log.blogpost.add", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)
