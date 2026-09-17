"""Upload / cleanup orchestration for Google Drive video activities.

Called by the activity services once the request has been validated and the
DB session committed (the Drive round-trips can take a long time and must not
pin a pooled connection). Every operation logs an INFO audit line with the
user id and the org / course / activity uuids plus the Drive ids involved
(FR-013); no line ever contains a token.

Drive layout (data-model.md §6)::

    <root_folder_name>/<org_uuid>/<course_uuid>/<activity_uuid>/video.<ext>
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Collection

from fastapi import UploadFile
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.db.courses.activities import Activity, ActivitySubTypeEnum
from src.security.file_validation import get_safe_filename

from .client import DriveClient
from .errors import (
    GDriveError,
    GDriveNotFoundError,
    GDrivePermissionError,
    GDriveTransientError,
    GDriveUnauthorizedError,
)
from .readiness import get_access_token, invalidate, state

logger = logging.getLogger(__name__)

# One best-effort delete (with the client's transient retries) may not take
# longer than this in total (FR-014).
BEST_EFFORT_BUDGET_SECONDS = 60


@dataclass
class DriveUploadResult:
    file_id: str
    folder_id: str
    stored_filename: str  # "video.mp4" | "video.webm"
    mime_type: str
    size: int


def _client() -> DriveClient:  # patched in tests
    return DriveClient(get_access_token)


def _root_folder_name() -> str:
    return get_learnhouse_config().gdrive_config.root_folder_name


# ------------------------------------------------------------------ folders


async def _ensure_root_folder(client: DriveClient) -> str:
    st = state()
    if st.root_folder_id:
        return st.root_folder_id
    root_id = await client.ensure_folder(_root_folder_name(), "root")
    st.root_folder_id = root_id
    return root_id


async def _resolve_course_folder(client: DriveClient, org_uuid: str, course_uuid: str) -> str:
    root_id = await _ensure_root_folder(client)
    try:
        org_id = await client.ensure_folder(org_uuid, root_id)
    except GDriveNotFoundError:
        # The cached root folder was deleted on Drive: forget it and rebuild once.
        logger.info("Google Drive root folder %s no longer exists; re-creating it", root_id)
        state().root_folder_id = None
        root_id = await _ensure_root_folder(client)
        org_id = await client.ensure_folder(org_uuid, root_id)
    return await client.ensure_folder(course_uuid, org_id)


async def ensure_activity_folder(
    org_uuid: str,
    course_uuid: str,
    activity_uuid: str,
    *,
    client: DriveClient | None = None,
) -> tuple[str, bool]:
    """Folder id for ``<root>/<org>/<course>/<activity>`` plus whether THIS call created it.

    The replace flow uses the flag to remove a folder it created when the
    upload meant to fill it fails (spec Edge Cases); a pre-existing folder
    still holds the current video and must be kept.
    """

    async def _run(c: DriveClient) -> tuple[str, bool]:
        course_folder = await _resolve_course_folder(c, org_uuid, course_uuid)
        found = await c.find_folder(activity_uuid, course_folder)
        if found:
            return found, False
        return await c.create_folder(activity_uuid, course_folder), True

    if client is not None:
        return await _run(client)
    try:
        async with _client() as own:
            return await _run(own)
    except GDriveUnauthorizedError as exc:
        invalidate()
        raise GDriveTransientError("Google Drive rejected the access token") from exc


async def find_course_folder(org_uuid: str, course_uuid: str) -> str | None:
    """Id of ``<root>/<org>/<course>`` looked up by name only (never created).

    Used after a course delete: ``content`` stores no course folder id. Any
    lookup failure (Drive error, folder gone, token rejected) yields ``None``;
    the caller logs and moves on.
    """
    try:
        async with _client() as client:
            root_id = await client.find_folder(_root_folder_name(), "root")
            if not root_id:
                return None
            org_id = await client.find_folder(org_uuid, root_id)
            if not org_id:
                return None
            return await client.find_folder(course_uuid, org_id)
    except GDriveUnauthorizedError:
        invalidate()
        logger.warning("Google Drive rejected the access token while locating the course folder for %s", course_uuid)
        return None
    except GDriveError as exc:
        logger.warning("Could not locate the Drive course folder for %s: %s", course_uuid, type(exc).__name__)
        return None


# ------------------------------------------------------------------- upload


def _verify_uploaded_file(info: dict[str, Any], expected_size: int) -> None:
    problems: list[str] = []
    if info.get("trashed"):
        problems.append("trashed")
    if info.get("copyRequiresWriterPermission") is not True:
        problems.append("copyRequiresWriterPermission=false")
    try:
        actual_size = int(info.get("size", -1))
    except (TypeError, ValueError):
        actual_size = -1
    if actual_size != expected_size:
        problems.append(f"size={actual_size}!={expected_size}")
    permissions = info.get("permissions")
    shared = isinstance(permissions, list) and any(
        isinstance(p, dict) and p.get("type") == "anyone" and p.get("role") == "reader" for p in permissions
    )
    if not shared:
        problems.append("no anyone/reader permission")
    if problems:
        raise GDrivePermissionError("Drive file verification failed: " + ", ".join(problems))


async def _cleanup_after_failure(
    client: DriveClient, file_id: str | None, folder_id: str | None, *, context: str
) -> None:
    """Delete what a failed upload left behind; never raises (logs WARNING instead)."""
    for kind, target in (("file", file_id), ("folder", folder_id)):
        if not target:
            continue
        try:
            async with asyncio.timeout(BEST_EFFORT_BUDGET_SECONDS):
                await client.delete_file(target)
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - cleanup must not mask the original error
            if isinstance(exc, asyncio.CancelledError) and not isinstance(exc, TimeoutError):
                raise
            logger.warning(
                "Google Drive cleanup failed for %s %s after a failed upload (%s): %s",
                kind, target, context, type(exc).__name__,
            )


async def upload_activity_video(
    *,
    org_uuid: str,
    course_uuid: str,
    activity_uuid: str,
    file: UploadFile,
    mime_type: str,
    size: int,
    original_filename: str,
    user_id: int | str | None = None,
    folder_id: str | None = None,
) -> DriveUploadResult:
    """Push ``file`` to Drive, share it read-only, block copies, verify, return the ids.

    With ``folder_id`` unset (create flow) the activity folder is created and
    is the unit of cleanup; when set (replace flow) the file goes into that
    existing folder and only the new file is removed on failure. Any failure
    after the folder exists leaves nothing behind and re-raises the original
    :class:`GDriveError`.
    """
    stored_filename = get_safe_filename(original_filename, "video", content_type=mime_type)
    context = f"user={user_id} org={org_uuid} course={course_uuid} activity={activity_uuid}"
    async with _client() as client:
        created_folder: str | None = None
        target_folder: str | None = None  # stays None if resolving <root>/<org>/<course> fails
        file_id: str | None = None
        try:
            if folder_id is None:
                course_folder = await _resolve_course_folder(client, org_uuid, course_uuid)
                target_folder = await client.ensure_folder(activity_uuid, course_folder)
                created_folder = target_folder
            else:
                target_folder = folder_id

            file.file.seek(0)
            file_id = await client.resumable_upload(file.file, size, stored_filename, mime_type, target_folder)
            await client.share_anyone_reader(file_id)
            await client.set_copy_requires_writer_permission(file_id)
            info = await client.get_file(file_id)
            _verify_uploaded_file(info, size)
        except GDriveUnauthorizedError as exc:
            invalidate()
            logger.warning("Google Drive rejected the access token during upload (%s)", context)
            await _cleanup_after_failure(client, file_id, created_folder, context=context)
            raise GDriveTransientError("Google Drive rejected the access token") from exc
        except GDriveError as exc:
            logger.warning(
                "Google Drive upload failed (%s): %s file=%s folder=%s", context, type(exc).__name__, file_id, target_folder
            )
            await _cleanup_after_failure(client, file_id, created_folder, context=context)
            raise
        except BaseException:
            await _cleanup_after_failure(client, file_id, created_folder, context=context)
            raise

    logger.info(
        "Google Drive video uploaded (%s): file=%s folder=%s name=%s size=%d result=ok",
        context, file_id, target_folder, stored_filename, size,
    )
    return DriveUploadResult(
        file_id=file_id,
        folder_id=target_folder,
        stored_filename=stored_filename,
        mime_type=mime_type,
        size=size,
    )


# ------------------------------------------------------------------ cleanup


async def _delete_best_effort(target_id: str, *, kind: str, context: str) -> bool:
    try:
        async with asyncio.timeout(BEST_EFFORT_BUDGET_SECONDS):
            async with _client() as client:
                await client.delete_file(target_id)
    except TimeoutError:
        logger.warning(
            "Google Drive %s %s was not deleted within %ss (%s); delete it by hand",
            kind, target_id, BEST_EFFORT_BUDGET_SECONDS, context,
        )
        return False
    except GDriveUnauthorizedError:
        invalidate()
        logger.warning("Google Drive %s %s was not deleted: access token rejected (%s); delete it by hand", kind, target_id, context)
        return False
    except GDriveError as exc:
        logger.warning(
            "Google Drive %s %s was not deleted: %s (%s); delete it by hand", kind, target_id, type(exc).__name__, context
        )
        return False
    except Exception as exc:  # noqa: BLE001 - best effort by contract (FR-014)
        logger.warning(
            "Google Drive %s %s was not deleted: unexpected %s (%s); delete it by hand",
            kind, target_id, type(exc).__name__, context,
        )
        return False
    logger.info("Google Drive %s %s deleted (%s) result=ok", kind, target_id, context)
    return True


async def delete_file_best_effort(file_id: str, *, context: str) -> bool:
    """Delete a Drive file; 404 counts as done; failures are logged, never raised."""
    return await _delete_best_effort(file_id, kind="file", context=context)


async def delete_folder_best_effort(folder_id: str, *, context: str) -> bool:
    """Delete a Drive folder and everything in it; failures are logged, never raised."""
    return await _delete_best_effort(folder_id, kind="folder", context=context)


async def has_other_reference(
    db_session: AsyncSession,
    activity_id: int | None = None,
    gdrive_file_id: str | None = None,
    gdrive_folder_id: str | None = None,
    exclude_course_id: int | None = None,
    *,
    gdrive_folder_ids: Collection[str] | None = None,
) -> bool:
    """True when another Drive video activity still points at the same file/folder.

    ``clone_course`` copies ``content`` verbatim, so two activities can share
    one Drive file; deleting it from one side would break the other. Matching
    happens in Python on the JSON ``content`` so the query works on every
    engine the test-suite uses (no ``->>`` operators).
    """
    wanted_folders = set(gdrive_folder_ids or ())
    if gdrive_folder_id:
        wanted_folders.add(gdrive_folder_id)
    if not gdrive_file_id and not wanted_folders:
        return False
    statement = select(Activity).where(Activity.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE)
    if activity_id is not None:
        statement = statement.where(Activity.id != activity_id)
    if exclude_course_id is not None:
        statement = statement.where(Activity.course_id != exclude_course_id)
    rows = (await db_session.execute(statement)).scalars().all()
    for row in rows:
        content = row.content or {}
        if gdrive_file_id and content.get("gdrive_file_id") == gdrive_file_id:
            return True
        if wanted_folders and content.get("gdrive_folder_id") in wanted_folders:
            return True
    return False
