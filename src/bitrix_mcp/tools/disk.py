"""Disk tools — storages, folders, files, upload (disk.*)."""

from __future__ import annotations

import base64

import httpx
from typing import Annotated

from pydantic import Field
from mcp.server.mcpserver import Context

from ..config import config
from ..runtime import (
    READ,
    WRITE,
    DESTRUCTIVE,
    Filter,
    PersonalWebhook,
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


@mcp.tool(name="b24_disk_storage_list", annotations=READ)
async def b24_disk_storage_list(
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List available Disk storages (disk.storage.getlist): personal, group, company.

    Returns a pagination envelope of storage objects (ID, NAME, ENTITY_TYPE, ...).
    Use a storage's root folder id with b24_disk_folder_items to browse it.
    """
    return await run_list(
        ctx, "disk.storage.getlist", None,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_disk_folder_items", annotations=READ)
async def b24_disk_folder_items(
    folder_id: Annotated[int, Field(description="Folder id whose children to list.")],
    filter: Filter = None,
    start: Start = 0,
    fetch_all: FetchAll = False,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """List the contents of a Disk folder (disk.folder.getchildren).

    Returns a pagination envelope of files and subfolders (ID, NAME, TYPE,
    DOWNLOAD_URL for files, ...).
    """
    params: dict = {"id": folder_id}
    if filter:
        params["filter"] = filter
    return await run_list(
        ctx, "disk.folder.getchildren", params,
        webhook_url=webhook_url, personal_webhook=personal_webhook,
        start=start, fetch_all=fetch_all,
    )


@mcp.tool(name="b24_disk_file_get", annotations=READ)
async def b24_disk_file_get(
    file_id: Annotated[int, Field(description="Disk file id.")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Get file metadata incl. DOWNLOAD_URL (disk.file.get).

    Note: files attached to task/chat messages may require the acting user to be
    a participant — pass that user's personal_webhook if you get ACCESS_DENIED.
    """
    return await run_call(ctx, "disk.file.get", {"id": file_id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, unwrap=True)


@mcp.tool(name="b24_disk_folder_add", annotations=WRITE)
async def b24_disk_folder_add(
    parent_folder_id: Annotated[int, Field(description="Parent folder id.")],
    name: Annotated[str, Field(description="New subfolder name.", min_length=1)],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Create a subfolder (disk.folder.addsubfolder). Returns the new folder object."""
    return await run_call(ctx, "disk.folder.addsubfolder", {"id": parent_folder_id, "data": {"NAME": name}},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_disk_file_upload", annotations=WRITE)
async def b24_disk_file_upload(
    folder_id: Annotated[int, Field(description="Target folder id.")],
    name: Annotated[str, Field(description="File name including extension, e.g. 'report.pdf'.", min_length=1)],
    content_base64: Annotated[str, Field(description="Base64-encoded file content.", min_length=1)],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Upload a file into a Disk folder (disk.folder.uploadfile).

    Returns the created file object. Content must be base64; the fileContent
    parameter is sent as [name, base64] with a unique name generated on collision.
    """
    params = {
        "id": folder_id,
        "data": {"NAME": name},
        "fileContent": [name, content_base64],
        "generateUniqueName": True,
    }
    return await run_call(ctx, "disk.folder.uploadfile", params,
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_disk_file_delete", annotations=DESTRUCTIVE)
async def b24_disk_file_delete(
    file_id: Annotated[int, Field(description="Disk file id to delete (moves it to the trash).")],
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Delete a Disk file (disk.file.delete). Moves to trash on the portal."""
    return await run_call(ctx, "disk.file.delete", {"id": file_id},
                          webhook_url=webhook_url, personal_webhook=personal_webhook, is_write=True, unwrap=True)


@mcp.tool(name="b24_disk_file_content", annotations=READ)
async def b24_disk_file_content(
    file_id: Annotated[int, Field(description="Disk file id to download.")],
    max_size_mb: Annotated[float, Field(default=10.0, gt=0, le=100, description="Refuse to download files larger than this (default 10 MB) to protect the context window.")] = 10.0,
    webhook_url: WebhookUrl = None,
    personal_webhook: PersonalWebhook = None,
    ctx: Context | None = None,
) -> str:
    """Download a Disk file's content and return it as base64.

    Resolves DOWNLOAD_URL via disk.file.get, then fetches the bytes server-side
    (so the portal WAF sees the request, not your client). Guarded by max_size_mb.

    Returns: {"id","name","size","content_type","base64"} or an error envelope.
    """
    try:
        client = get_client(ctx, webhook_url, personal_webhook)
        meta = await client.call_result("disk.file.get", {"id": file_id})
        if not isinstance(meta, dict):
            return err(ValueError("Unexpected disk.file.get response."))
        url = meta.get("DOWNLOAD_URL")
        name = meta.get("NAME")
        declared = int(meta.get("SIZE") or 0)
        cap = int(max_size_mb * 1024 * 1024)
        if not url:
            return err(ValueError("No DOWNLOAD_URL on this file (check access / personal_webhook)."))
        if declared and declared > cap:
            return err(ValueError(f"File is {declared} bytes, over the {cap}-byte cap. Raise max_size_mb to override."))
        async with httpx.AsyncClient(timeout=config.timeout, follow_redirects=True) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            content = resp.content
        if len(content) > cap:
            return err(ValueError(f"Downloaded {len(content)} bytes, over the {cap}-byte cap."))
        return ok({
            "id": file_id,
            "name": name,
            "size": len(content),
            "content_type": resp.headers.get("content-type"),
            "base64": base64.b64encode(content).decode("ascii"),
        })
    except Exception as exc:  # noqa: BLE001
        return err(exc)
