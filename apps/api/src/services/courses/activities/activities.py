from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from src.db.courses.courses import Course
from src.db.courses.chapters import Chapter
from src.db.courses.activities import ActivityCreate, Activity, ActivityRead, ActivitySubTypeEnum, ActivityUpdate
from src.db.courses.chapter_activities import ChapterActivity
from src.db.organizations import Organization, OrganizationRead
from src.db.organization_config import OrganizationConfig
from src.db.users import AnonymousUser, PublicUser, User
from src.security.auth import resolve_acting_user_id
from fastapi import HTTPException, Request
from pydantic import BaseModel
from uuid import uuid4
from datetime import datetime

import asyncio
import logging

from src.core.ee_hooks import check_ee_activity_paid_access
from src.services.integrations.gdrive.errors import GDriveError, to_http_exception
from src.services.integrations.gdrive.readiness import require_ready
from src.services.integrations.gdrive.service import delete_folder_best_effort, has_other_reference
from src.security.rbac import check_resource_access, AccessAction
from src.services.courses.activities.versioning import create_activity_version
from src.services.courses.locks import (
    batch_accessible_restricted_uuids,
    is_locked_for_user,
    is_org_admin,
)

logger = logging.getLogger(__name__)

# Module-level set to hold strong references to background embedding tasks,
# preventing them from being garbage-collected before they complete.
_embedding_tasks: set = set()


STORAGE_CHANGE_DETAIL = "Video : Storage location of a video activity cannot be changed"
GDRIVE_CREATE_DETAIL = "Video : Google Drive video activities must be created through POST /activities/video"
_GDRIVE_CONTENT_KEYS = ("gdrive_file_id", "gdrive_folder_id", "mime_type", "size", "original_filename")


def _sub_type_value(value) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def is_gdrive_activity(activity: Activity) -> bool:
    return _sub_type_value(activity.activity_sub_type) == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE.value


async def require_gdrive_ready_for(activity: Activity) -> None:
    """FR-004: every write to a Drive video activity needs the integration ready.

    Translates the readiness error into the fixed 409 "Video : ..." reply so
    nothing downstream changes when the operator has the integration off,
    unconfigured or in need of re-authorization.
    """
    if not is_gdrive_activity(activity):
        return
    try:
        await require_ready()
    except GDriveError as err:
        raise to_http_exception(err)


def _enforce_storage_invariant(activity: Activity, update_data: dict) -> None:
    """FR-014: the storage location of a video activity never changes.

    ``ActivityUpdate`` accepts both ``activity_sub_type`` and ``content``, so a
    generic PUT could otherwise flip a Drive activity to hosted (or vice versa)
    or rewrite the Drive ids under it.
    """
    requested_sub = _sub_type_value(update_data.get("activity_sub_type"))
    gdrive = ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE.value
    if is_gdrive_activity(activity):
        if "activity_sub_type" in update_data and requested_sub != gdrive:
            raise HTTPException(status_code=409, detail=STORAGE_CHANGE_DETAIL)
        if "content" in update_data:
            new_content = update_data["content"]
            if not isinstance(new_content, dict):
                # ``content: null`` (or any non-object) would wipe the Drive ids.
                raise HTTPException(status_code=409, detail=STORAGE_CHANGE_DETAIL)
            current = activity.content or {}
            if new_content.get("storage") != "gdrive":
                raise HTTPException(status_code=409, detail=STORAGE_CHANGE_DETAIL)
            for key in _GDRIVE_CONTENT_KEYS:
                if new_content.get(key) != current.get(key):
                    raise HTTPException(status_code=409, detail=STORAGE_CHANGE_DETAIL)
    elif "activity_sub_type" in update_data and requested_sub == gdrive:
        raise HTTPException(status_code=409, detail=STORAGE_CHANGE_DETAIL)


####################################################
# CRUD
####################################################


async def create_activity(
    request: Request,
    activity_object: ActivityCreate,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
):

    # CHeck if org exists
    statement = select(Chapter).where(Chapter.id == activity_object.chapter_id)
    chapter = (await db_session.execute(statement)).scalars().first()

    if not chapter:
        raise HTTPException(
            status_code=404,
            detail="Chapter not found",
        )

    # RBAC check
    statement = select(Course).where(Course.id == chapter.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.CREATE)

    # A Drive video only exists once its file is on Drive (FR-011); the generic
    # endpoint cannot upload, so it must never mint one — ready or not (FR-004).
    if _sub_type_value(activity_object.activity_sub_type) == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE.value:
        raise HTTPException(status_code=409, detail=GDRIVE_CREATE_DETAIL)

    # Create Activity
    activity = Activity(**activity_object.model_dump())

    activity.activity_uuid = str(f"activity_{uuid4()}")
    activity.creation_date = str(datetime.now())
    activity.update_date = str(datetime.now())
    activity.org_id = chapter.org_id
    activity.course_id = chapter.course_id

    # Flush to get the DB-assigned ID without committing yet
    db_session.add(activity)
    await db_session.flush()

    if activity.id is None:
        raise HTTPException(
            status_code=500,
            detail="Activity creation failed: could not retrieve activity ID",
        )

    # Determine insertion order using MAX to avoid loading all rows
    max_order = (await db_session.execute(
        select(func.max(ChapterActivity.order)).where(
            ChapterActivity.chapter_id == activity_object.chapter_id
        )
    )).scalars().first()
    to_be_used_order = (max_order or 0) + 1

    # Add activity to chapter
    activity_chapter = ChapterActivity(
        chapter_id=activity_object.chapter_id,
        activity_id=activity.id,
        course_id=chapter.course_id,
        org_id=chapter.org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
        order=to_be_used_order,
    )

    # Single atomic commit for both Activity and ChapterActivity
    db_session.add(activity_chapter)
    await db_session.commit()
    await db_session.refresh(activity)

    return ActivityRead.model_validate(activity)


async def get_activity(
    request: Request,
    activity_uuid: str,
    current_user: PublicUser,
    db_session: AsyncSession,
):
    # Optimize by joining Activity with Course and last modified user in a single query
    statement = (
        select(Activity, Course, User)
        .join(Course)
        .outerjoin(User, Activity.last_modified_by_id == User.id)
        .where(Activity.activity_uuid == activity_uuid)
    )
    result = (await db_session.execute(statement)).first()

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Activity not found",
        )

    activity, course, last_modified_user = result

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    # Paid access check (via EE hook with fallback to True if EE not available)
    has_paid_access = await check_ee_activity_paid_access(
        request=request,
        activity_id=activity.id,
        user=current_user,
        db_session=db_session
    )

    activity_read = ActivityRead.model_validate(activity)
    activity_read.content = activity_read.content if has_paid_access else { "paid_access": False }
    # Include last modified user info
    activity_read.last_modified_by_username = last_modified_user.username if last_modified_user else None

    await _apply_activity_lock(activity_read, activity, course, current_user, db_session)

    return activity_read


class EditorBootstrapCourse(BaseModel):
    course_uuid: str
    name: str
    description: str | None = None
    thumbnail_image: str | None = None
    org_uuid: str
    org_id: int


class EditorBootstrapResponse(BaseModel):
    activity: ActivityRead
    course: EditorBootstrapCourse
    org: OrganizationRead


async def get_editor_bootstrap(
    request: Request,
    activity_uuid: str,
    current_user: PublicUser,
    db_session: AsyncSession,
) -> EditorBootstrapResponse:
    """Single-roundtrip payload for the activity editor.

    Replaces three separate fetches (course meta + activity + org) with one.
    All needed rows are pulled in a single SQL round-trip via joins, including
    OrganizationConfig and the parent Chapter (for lock checks) so the lock
    helper does not need a follow-up query.

    Intentionally not cached — activity content must always reflect the latest
    saved state for collaborators to avoid editing against stale data.
    """
    statement = (
        select(Activity, Course, Organization, OrganizationConfig, User, Chapter)
        .join(Course, Course.id == Activity.course_id)  # type: ignore
        .join(Organization, Organization.id == Course.org_id)  # type: ignore
        .outerjoin(OrganizationConfig, OrganizationConfig.org_id == Organization.id)  # type: ignore
        .outerjoin(User, Activity.last_modified_by_id == User.id)  # type: ignore
        .outerjoin(ChapterActivity, ChapterActivity.activity_id == Activity.id)  # type: ignore
        .outerjoin(Chapter, Chapter.id == ChapterActivity.chapter_id)  # type: ignore
        .where(Activity.activity_uuid == activity_uuid)
    )
    db_result = (await db_session.execute(statement)).first()

    if not db_result:
        raise HTTPException(status_code=404, detail="Activity not found")

    activity, course, org, org_config, last_modified_user, parent_chapter = db_result

    await check_resource_access(
        request, db_session, current_user, course.course_uuid, AccessAction.READ
    )

    has_paid_access = await check_ee_activity_paid_access(
        request=request,
        activity_id=activity.id,
        user=current_user,
        db_session=db_session,
    )

    activity_read = ActivityRead.model_validate(activity)
    activity_read.content = (
        activity_read.content if has_paid_access else {"paid_access": False}
    )
    activity_read.last_modified_by_username = (
        last_modified_user.username if last_modified_user else None
    )
    await _apply_activity_lock(
        activity_read,
        activity,
        course,
        current_user,
        db_session,
        parent_chapter=parent_chapter,
    )

    # Build org with resolved_features (same shape the existing /orgs/uuid/{uuid}
    # endpoint returns, so the frontend doesn't have to reshape anything).
    from src.services.orgs.orgs import _build_org_read_with_resolved
    org_read = _build_org_read_with_resolved(org, org_config)

    return EditorBootstrapResponse(
        activity=activity_read,
        course=EditorBootstrapCourse(
            course_uuid=course.course_uuid,
            name=course.name,
            description=course.description,
            thumbnail_image=course.thumbnail_image,
            org_uuid=org.org_uuid,
            org_id=org.id or 0,
        ),
        org=org_read,
    )


async def _apply_activity_lock(
    activity_read: ActivityRead,
    activity: Activity,
    course: Course,
    current_user,
    db_session: AsyncSession,
    *,
    parent_chapter: Chapter | None = None,
) -> None:
    """Enforce chapter/activity lock_type on a single-activity read.

    Admins/maintainers bypass. A usergroup attached at the course level also
    unlocks every restricted chapter/activity inside that course (same
    inheritance rule as the TOC read). For everyone else, if either the
    activity or its parent chapter is locked, we scrub content/details and set
    ``is_locked=True`` so the client renders a gate instead of an empty page.
    """
    is_anon = isinstance(current_user, AnonymousUser)
    acting_user_id = resolve_acting_user_id(current_user)
    admin = False if is_anon else await is_org_admin(acting_user_id, course.org_id, db_session)
    if admin:
        return

    # Caller may have already fetched the parent chapter (e.g. via the editor
    # bootstrap join); only run the extra query when it wasn't supplied.
    if parent_chapter is not None:
        parent_chapter_row = parent_chapter
    else:
        parent_chapter_row = (await db_session.execute(
            select(Chapter)
            .join(ChapterActivity, ChapterActivity.chapter_id == Chapter.id)  # type: ignore
            .where(ChapterActivity.activity_id == activity.id)
        )).scalars().first()

    check_uuids: list[str] = [course.course_uuid]
    if (activity.lock_type or "public") == "restricted":
        check_uuids.append(activity.activity_uuid)
    if parent_chapter_row and (parent_chapter_row.lock_type or "public") == "restricted":
        check_uuids.append(parent_chapter_row.chapter_uuid)

    accessible: set[str] = set()
    if not is_anon:
        accessible = await batch_accessible_restricted_uuids(
            acting_user_id, check_uuids, db_session
        )

    # Course-level usergroup membership unlocks everything below it.
    if course.course_uuid in accessible:
        return

    chapter_locked = False
    if parent_chapter_row:
        chapter_locked = await is_locked_for_user(
            parent_chapter_row.lock_type,
            parent_chapter_row.chapter_uuid,
            course.org_id,
            current_user,
            db_session,
            accessible_restricted_uuids=accessible,
            is_admin=admin,
        )

    activity_locked = chapter_locked or await is_locked_for_user(
        activity.lock_type,
        activity.activity_uuid,
        course.org_id,
        current_user,
        db_session,
        accessible_restricted_uuids=accessible,
        is_admin=admin,
    )

    if activity_locked:
        activity_read.content = {}
        activity_read.details = None
        activity_read.is_locked = True

async def get_activityby_id(
    request: Request,
    activity_id: int,
    current_user: PublicUser,
    db_session: AsyncSession,
):
    # Optimize by joining Activity with Course in a single query
    statement = (
        select(Activity, Course)
        .join(Course)
        .where(Activity.id == activity_id)
    )
    result = (await db_session.execute(statement)).first()

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Activity not found",
        )

    activity, course = result

    # RBAC check
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    return ActivityRead.model_validate(activity)


async def update_activity(
    request: Request,
    activity_object: ActivityUpdate,
    activity_uuid: str,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
):
    statement = select(Activity).where(Activity.activity_uuid == activity_uuid)
    activity = (await db_session.execute(statement)).scalars().first()

    if not activity:
        raise HTTPException(
            status_code=404,
            detail="Activity not found",
        )

    # RBAC check
    statement = select(Course).where(Course.id == activity.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.UPDATE)

    # Update only the fields that were explicitly set (not default values)
    # Using model_dump(exclude_unset=True) to get only the fields that were passed in
    update_data = activity_object.model_dump(exclude_unset=True)

    # Drive video activities: readiness gate (FR-004) + storage invariant
    # (FR-014) BEFORE anything is mutated.
    await require_gdrive_ready_for(activity)
    _enforce_storage_invariant(activity, update_data)

    # Create a version snapshot before updating content
    # This preserves the current state for version history.
    # resolve_acting_user_id unwraps APITokenUser → the creating human's id,
    # because current_user.id on a token is 0 (the token id, not a user id)
    # and created_by_id is an FK to user.id — writing 0 triggers a FK
    # violation and the whole update 500s.
    if 'content' in update_data and activity.content:
        user_id = resolve_acting_user_id(current_user)
        await create_activity_version(activity, user_id, db_session)
        # Increment version number
        activity.current_version = (activity.current_version or 1) + 1
        # Track who made the change
        activity.last_modified_by_id = user_id

    if 'content' in update_data and isinstance(update_data['content'], str):
        logger.warning("[Activity Update] Content is STRING not dict for %s", activity_uuid)

    for field, value in update_data.items():
        setattr(activity, field, value)

    # Update the update_date timestamp
    activity.update_date = str(datetime.now())

    # Mark content as modified if it was updated (important for JSON fields)
    if 'content' in update_data:
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(activity, "content")

    db_session.add(activity)
    await db_session.commit()
    await db_session.refresh(activity)

    # Trigger background re-indexing for RAG when content changes
    if 'content' in update_data:
        task = asyncio.create_task(_trigger_course_embedding(activity.course_id, activity.org_id))
        _embedding_tasks.add(task)
        task.add_done_callback(_embedding_tasks.discard)
        task.add_done_callback(
            lambda t: logger.error("Embedding task failed: %s", t.exception()) if t.exception() else None
        )

    activity = ActivityRead.model_validate(activity)

    return activity


async def _trigger_course_embedding(course_id: int, org_id: int) -> None:
    """Background task to re-index course embeddings after content update."""
    try:
        from src.core.events.database import get_db_session
        from src.services.ai.rag.embedding_service import embed_course_content

        async for session in get_db_session():
            course = (await session.execute(select(Course).where(Course.id == course_id))).scalars().first()
            if not course:
                logger.warning("Skipping embedding for deleted course %d", course_id)
                return
            await embed_course_content(course_id, org_id, session)
    except Exception as e:
        logger.warning("Background embedding update failed (non-critical): %s", e)


async def delete_activity(
    request: Request,
    activity_uuid: str,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
):
    statement = select(Activity).where(Activity.activity_uuid == activity_uuid)
    activity = (await db_session.execute(statement)).scalars().first()

    if not activity:
        raise HTTPException(
            status_code=404,
            detail="Activity not found",
        )

    # RBAC check
    statement = select(Course).where(Course.id == activity.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.DELETE)

    # Drive video activities are only deleted while the integration can also
    # clean up the Drive folder (FR-004): not ready → 409, nothing changes.
    await require_gdrive_ready_for(activity)

    # Clean up content files from storage
    from src.db.organizations import Organization
    org_statement = select(Organization).where(Organization.id == course.org_id)
    org = (await db_session.execute(org_statement)).scalars().first()
    if org:
        from src.services.courses.transfer.storage_utils import delete_storage_directory
        content_path = f"content/orgs/{org.org_uuid}/courses/{course.course_uuid}/activities/{activity_uuid}"
        delete_storage_directory(content_path)

    # Delete activity from chapter
    statement = select(ChapterActivity).where(
        ChapterActivity.activity_id == activity.id
    )
    activity_chapter = (await db_session.execute(statement)).scalars().first()

    if not activity_chapter:
        raise HTTPException(
            status_code=404,
            detail="Activity not found in chapter",
        )

    # Remember what to clean up on Drive before the row is gone. content is
    # copied verbatim by clone_course, so the folder may still be shared.
    gdrive_folder_id = activity.content.get("gdrive_folder_id") if is_gdrive_activity(activity) and activity.content else None
    activity_id = activity.id
    user_id = resolve_acting_user_id(current_user) if not isinstance(current_user, AnonymousUser) else None

    await db_session.delete(activity_chapter)
    await db_session.delete(activity)
    await db_session.commit()

    if gdrive_folder_id:
        # FR-013: name the org too, so a "delete it by hand" WARNING is actionable.
        context = (
            f"user={user_id} org={org.org_uuid if org else None} course={course.course_uuid} "
            f"activity={activity_uuid} folder={gdrive_folder_id}"
        )
        if await has_other_reference(db_session, activity_id, gdrive_folder_id=gdrive_folder_id):
            logger.info("Drive folder kept: still referenced by another activity (%s)", context)
        else:
            # Best effort: a Drive failure here is logged, never blocks the delete.
            await delete_folder_best_effort(gdrive_folder_id, context=context)
        logger.info("Drive video activity deleted (%s) result=ok", context)

    return {"detail": "Activity deleted"}


####################################################
# Misc
####################################################


async def get_activities(
    request: Request,
    coursechapter_id: int,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
) -> list[ActivityRead]:
    # Single query joining Activity, Chapter, and Course to avoid 3 sequential queries
    statement = (
        select(Activity, Chapter, Course)
        .join(ChapterActivity, Activity.id == ChapterActivity.activity_id)
        .join(Chapter, ChapterActivity.chapter_id == Chapter.id)
        .join(Course, Chapter.course_id == Course.id)
        .where(
            ChapterActivity.chapter_id == coursechapter_id,
            Activity.published == True,
        )
    )
    results = (await db_session.execute(statement)).all()

    if not results:
        raise HTTPException(
            status_code=404,
            detail="No published activities found",
        )

    _, chapter, course = results[0]
    await check_resource_access(request, db_session, current_user, course.course_uuid, AccessAction.READ)

    return [ActivityRead.model_validate(activity) for activity, _, _ in results]
