from typing import Literal, Optional
import json
import logging
import re
from src.db.courses.courses import Course
from src.db.organizations import Organization

from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from src.db.courses.chapters import Chapter
from src.db.courses.activities import (
    Activity,
    ActivityRead,
    ActivitySubTypeEnum,
    ActivityTypeEnum,
)
from src.db.courses.chapter_activities import ChapterActivity
from src.db.courses.course_chapters import CourseChapter
from src.db.users import AnonymousUser, PublicUser
from src.services.courses.activities.uploads.videos import upload_video
from fastapi import HTTPException, status, UploadFile, Request
from uuid import uuid4
from datetime import datetime
from src.security.rbac import check_resource_access, AccessAction
from src.security.file_validation import validate_upload_stream
from src.services.courses.activities.activities import STORAGE_CHANGE_DETAIL, is_gdrive_activity
from src.services.integrations.gdrive.errors import GDriveError, to_http_exception
from src.services.integrations.gdrive.readiness import require_ready
from src.services.integrations.gdrive.service import (
    delete_file_best_effort,
    delete_folder_best_effort,
    has_other_reference,
    ensure_activity_folder,
    upload_activity_video,
)

logger = logging.getLogger(__name__)


async def create_video_activity(
    request: Request,
    name: str,
    chapter_id: int,
    current_user: PublicUser,
    db_session: AsyncSession,
    video_file: UploadFile | None = None,
    details: str = "{}",
    extra_metadata: Optional[dict] = None,
    storage: str = "server",
):
    # get chapter_id
    statement = select(Chapter).where(Chapter.id == chapter_id)
    chapter = (await db_session.execute(statement)).scalars().first()

    # convert details to dict
    details = json.loads(details)

    if not chapter:
        raise HTTPException(
            status_code=404,
            detail="Chapter not found",
        )

    statement = select(CourseChapter).where(CourseChapter.chapter_id == chapter_id)
    coursechapter = (await db_session.execute(statement)).scalars().first()

    if not coursechapter:
        raise HTTPException(
            status_code=404,
            detail="CourseChapter not found",
        )

    # Get course_uuid for RBAC check
    statement = select(Course).where(Course.id == coursechapter.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(
        request, db_session, current_user, course.course_uuid, AccessAction.CREATE
    )

    # Get org_uuid
    statement = select(Organization).where(Organization.id == coursechapter.org_id)
    organization = (await db_session.execute(statement)).scalars().first()

    # generate activity_uuid
    activity_uuid = str(f"activity_{uuid4()}")

    # check if video_file is not None
    if not video_file:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video : No video file provided",
        )

    if video_file.content_type not in ["video/mp4", "video/webm"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Video : Wrong video format"
        )

    if not video_file.filename:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video : No video file provided",
        )

    if storage == "gdrive":
        if not organization:
            raise HTTPException(status_code=404, detail="Organization not found")
        return await _create_gdrive_video_activity(
            name=name,
            chapter=chapter,
            coursechapter=coursechapter,
            course=course,
            organization=organization,
            activity_uuid=activity_uuid,
            current_user=current_user,
            db_session=db_session,
            video_file=video_file,
            extra_metadata=extra_metadata,
        )

    # Upload video first to get safe filename
    saved_filename = None
    if video_file and organization and course:
        saved_filename = await upload_video(
            video_file,
            activity_uuid,
            organization.org_uuid,
            course.course_uuid,
        )

    activity_object = Activity(
        name=name,
        activity_type=ActivityTypeEnum.TYPE_VIDEO,
        activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_HOSTED,
        activity_uuid=activity_uuid,
        org_id=coursechapter.org_id,
        course_id=coursechapter.course_id,
        content={
            "filename": saved_filename or "video",
            "activity_uuid": activity_uuid,
        },
        details=details if isinstance(details, dict) else json.loads(details),
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
        extra_metadata=extra_metadata,
    )

    # create activity
    activity = Activity.model_validate(activity_object)
    db_session.add(activity)
    await db_session.commit()
    await db_session.refresh(activity)

    # Find the last activity order in the chapter
    statement = (
        select(ChapterActivity)
        .where(ChapterActivity.chapter_id == chapter.id)
        .order_by(ChapterActivity.order)  # type: ignore
    )
    chapter_activities = (await db_session.execute(statement)).scalars().all()
    last_order = chapter_activities[-1].order if chapter_activities else 0
    to_be_used_order = last_order + 1

    # update chapter
    chapter_activity_object = ChapterActivity(
        chapter_id=chapter.id,  # type: ignore
        activity_id=activity.id,  # type: ignore
        course_id=coursechapter.course_id,
        org_id=coursechapter.org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
        order=to_be_used_order,
    )

    # Insert ChapterActivity link in DB
    db_session.add(chapter_activity_object)
    await db_session.commit()
    await db_session.refresh(chapter_activity_object)

    # Kick off HLS transcoding (no-op unless LEARNHOUSE_HLS_ENABLED). The MP4 is
    # served as the fallback until HLS is ready.
    try:
        from src.services.utils.hls_jobs import enqueue as enqueue_hls
        enqueue_hls(activity.activity_uuid)
    except Exception:
        logger.exception("Failed to enqueue HLS transcode for %s", activity.activity_uuid)

    return ActivityRead.model_validate(activity)


_MAX_ORIGINAL_FILENAME = 255


def _gdrive_content(activity_uuid: str, upload, original_filename: str) -> dict:
    """``content`` for a Drive video activity (contracts/drive-content.md) — no URL, ever."""
    return {
        "activity_uuid": activity_uuid,
        "storage": "gdrive",
        "gdrive_file_id": upload.file_id,
        "gdrive_folder_id": upload.folder_id,
        "original_filename": original_filename[:_MAX_ORIGINAL_FILENAME],
        "mime_type": upload.mime_type,
        "size": upload.size,
    }


async def _create_gdrive_video_activity(
    *,
    name: str,
    chapter: Chapter,
    coursechapter: CourseChapter,
    course: Course,
    organization: Organization,
    activity_uuid: str,
    current_user: PublicUser,
    db_session: AsyncSession,
    video_file: UploadFile,
    extra_metadata: Optional[dict],
) -> ActivityRead:
    """Google Drive branch of :func:`create_video_activity`.

    Validate the stream (never the whole body in RAM) → release the DB
    connection → push to Drive, share, verify → only then insert the Activity
    with ``SUBTYPE_VIDEO_GDRIVE``. Playback ``details`` are ignored (the Drive
    embed has none). A DB failure after the upload removes the Drive folder.
    """
    try:
        mime_type, size = validate_upload_stream(video_file, ["video"])
        original_filename = (video_file.filename or "video")[:_MAX_ORIGINAL_FILENAME]

        # The Drive upload can take a very long time: do not hold a pooled
        # connection (or an open transaction) across it.
        await db_session.commit()

        try:
            upload = await upload_activity_video(
                org_uuid=organization.org_uuid,
                course_uuid=course.course_uuid,
                activity_uuid=activity_uuid,
                file=video_file,
                mime_type=mime_type,
                size=size,
                original_filename=original_filename,
                user_id=getattr(current_user, "id", None),
            )
        except GDriveError as err:
            raise to_http_exception(err)
    finally:
        # Release the spooled temp file now (SC-004) instead of waiting for the
        # request teardown; UploadFile.close() is idempotent.
        await video_file.close()

    context = (
        f"user={getattr(current_user, 'id', None)} org={organization.org_uuid} "
        f"course={course.course_uuid} activity={activity_uuid}"
    )
    try:
        activity = Activity(
            name=name,
            activity_type=ActivityTypeEnum.TYPE_VIDEO,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE,
            activity_uuid=activity_uuid,
            org_id=coursechapter.org_id,
            course_id=coursechapter.course_id,
            content=_gdrive_content(activity_uuid, upload, original_filename),
            details={},
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
            extra_metadata=extra_metadata,
        )
        db_session.add(activity)
        await db_session.commit()
        await db_session.refresh(activity)

        statement = (
            select(ChapterActivity)
            .where(ChapterActivity.chapter_id == chapter.id)
            .order_by(ChapterActivity.order)  # type: ignore
        )
        chapter_activities = (await db_session.execute(statement)).scalars().all()
        last_order = chapter_activities[-1].order if chapter_activities else 0
        chapter_activity_object = ChapterActivity(
            chapter_id=chapter.id,  # type: ignore
            activity_id=activity.id,  # type: ignore
            course_id=coursechapter.course_id,
            org_id=coursechapter.org_id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
            order=last_order + 1,
        )
        db_session.add(chapter_activity_object)
        await db_session.commit()
    except Exception:
        logger.exception("Drive video activity could not be saved (%s); removing Drive folder %s", context, upload.folder_id)
        try:
            await db_session.rollback()
        except Exception:  # pragma: no cover - rollback of a dead session
            pass
        await delete_folder_best_effort(upload.folder_id, context=context)
        raise

    logger.info(
        "Drive video activity created (%s): file=%s folder=%s result=ok", context, upload.file_id, upload.folder_id
    )
    return ActivityRead.model_validate(activity)


class ExternalVideo(BaseModel):
    name: str
    uri: str
    type: Literal["youtube", "vimeo"]
    chapter_id: int
    details: str = "{}"
    extra_metadata: Optional[dict] = None


class ExternalVideoInDB(BaseModel):
    activity_id: int


async def create_external_video_activity(
    request: Request,
    current_user: PublicUser | AnonymousUser,
    data: ExternalVideo,
    db_session: AsyncSession,
):
    # get chapter_id
    statement = select(Chapter).where(Chapter.id == data.chapter_id)
    chapter = (await db_session.execute(statement)).scalars().first()

    if not chapter:
        raise HTTPException(
            status_code=404,
            detail="Chapter not found",
        )

    statement = select(CourseChapter).where(CourseChapter.chapter_id == data.chapter_id)
    coursechapter = (await db_session.execute(statement)).scalars().first()

    if not coursechapter:
        raise HTTPException(
            status_code=404,
            detail="CourseChapter not found",
        )

    # Get course_uuid for RBAC check
    statement = select(Course).where(Course.id == coursechapter.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found",
        )

    # RBAC check
    await check_resource_access(
        request, db_session, current_user, course.course_uuid, AccessAction.CREATE
    )

    # generate activity_uuid
    activity_uuid = str(f"activity_{uuid4()}")

    # convert details to dict
    details = json.loads(data.details)

    activity_object = Activity(
        name=data.name,
        activity_type=ActivityTypeEnum.TYPE_VIDEO,
        activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_YOUTUBE,
        activity_uuid=activity_uuid,
        course_id=coursechapter.course_id,
        org_id=coursechapter.org_id,
        content={
            "uri": data.uri,
            "type": data.type,
            "activity_uuid": activity_uuid,
        },
        details=details,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
        extra_metadata=data.extra_metadata,
    )

    # create activity
    activity = Activity.model_validate(activity_object)
    db_session.add(activity)
    await db_session.commit()
    await db_session.refresh(activity)

    # Find the last activity order in the chapter
    statement = (
        select(ChapterActivity)
        .where(ChapterActivity.chapter_id == coursechapter.chapter_id)
        .order_by(ChapterActivity.order)  # type: ignore
    )
    chapter_activities = (await db_session.execute(statement)).scalars().all()
    last_order = chapter_activities[-1].order if chapter_activities else 0
    to_be_used_order = last_order + 1

    # update chapter
    chapter_activity_object = ChapterActivity(
        chapter_id=coursechapter.chapter_id,  # type: ignore
        activity_id=activity.id,  # type: ignore
        course_id=coursechapter.course_id,
        org_id=coursechapter.org_id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
        order=to_be_used_order,
    )

    # Insert ChapterActivity link in DB
    db_session.add(chapter_activity_object)
    await db_session.commit()

    return ActivityRead.model_validate(activity)


async def update_video_activity(
    request: Request,
    activity_uuid: str,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
    name: Optional[str] = None,
    video_file: UploadFile | None = None,
    details: Optional[str] = None,
) -> ActivityRead:
    statement = select(Activity).where(Activity.activity_uuid == activity_uuid)
    activity = (await db_session.execute(statement)).scalars().first()

    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    statement = select(Course).where(Course.id == activity.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    await check_resource_access(
        request, db_session, current_user, course.course_uuid, AccessAction.UPDATE
    )

    if activity.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE:
        return await _update_gdrive_video_activity(
            activity=activity,
            course=course,
            current_user=current_user,
            db_session=db_session,
            name=name,
            video_file=video_file,
        )

    if name is not None:
        activity.name = name

    if details:
        activity.details = json.loads(details)

    if video_file and video_file.filename:
        if video_file.content_type not in ["video/mp4", "video/webm"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Video : Wrong video format",
            )

        statement = select(Organization).where(Organization.id == activity.org_id)
        organization = (await db_session.execute(statement)).scalars().first()

        if organization and course:
            saved_filename = await upload_video(
                video_file,
                activity_uuid,
                organization.org_uuid,
                course.course_uuid,
            )
            new_content = dict(activity.content) if activity.content else {}
            new_content["filename"] = saved_filename
            activity.content = new_content
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(activity, "content")

    activity.update_date = str(datetime.now())
    db_session.add(activity)
    await db_session.commit()
    await db_session.refresh(activity)

    return ActivityRead.model_validate(activity)


async def _update_gdrive_video_activity(
    *,
    activity: Activity,
    course: Course,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
    name: Optional[str],
    video_file: UploadFile | None,
) -> ActivityRead:
    """Google Drive branch of :func:`update_video_activity`.

    Only ``name`` and ``video_file`` have an effect; playback settings are
    ignored (the Drive embed has none) and the storage location never changes.
    Every update — even a bare rename — needs the integration ready (FR-004).
    A replacement file goes into the folder resolved from THIS activity's own
    uuid path (a cloned activity sharing the old folder keeps its file), and
    the previous file is removed after the commit unless another activity
    still references it.
    """
    try:
        await require_ready()
    except GDriveError as err:
        raise to_http_exception(err)

    user_id = getattr(current_user, "id", None)
    statement = select(Organization).where(Organization.id == activity.org_id)
    organization = (await db_session.execute(statement)).scalars().first()
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")
    # FR-013: every audit line (rename, replace, best-effort cleanup) names the
    # acting user and the org / course / activity.
    context = (
        f"user={user_id} org={organization.org_uuid} course={course.course_uuid} "
        f"activity={activity.activity_uuid}"
    )

    if not (video_file and video_file.filename):
        if name is not None:
            activity.name = name
        activity.update_date = str(datetime.now())
        db_session.add(activity)
        await db_session.commit()
        await db_session.refresh(activity)
        logger.info("Drive video activity updated (%s): fields=name result=ok", context)
        return ActivityRead.model_validate(activity)

    try:
        if video_file.content_type not in ["video/mp4", "video/webm"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Video : Wrong video format")
        mime_type, size = validate_upload_stream(video_file, ["video"])
        original_filename = (video_file.filename or "video")[:_MAX_ORIGINAL_FILENAME]

        old_content = dict(activity.content or {})
        old_file_id = old_content.get("gdrive_file_id")
        activity_uuid = activity.activity_uuid
        org_uuid = organization.org_uuid
        course_uuid = course.course_uuid

        # Nothing is mutated yet; release the connection for the long upload.
        await db_session.commit()

        created_folder: str | None = None
        try:
            folder_id, created = await ensure_activity_folder(org_uuid, course_uuid, activity_uuid)
            if created:
                created_folder = folder_id
            upload = await upload_activity_video(
                org_uuid=org_uuid,
                course_uuid=course_uuid,
                activity_uuid=activity_uuid,
                file=video_file,
                mime_type=mime_type,
                size=size,
                original_filename=original_filename,
                user_id=user_id,
                folder_id=folder_id,
            )
        except GDriveError as err:
            if created_folder:
                # A cloned activity had no folder of its own yet: this request
                # created one and nothing landed in it, so do not leave it behind.
                await delete_folder_best_effort(created_folder, context=context)
            raise to_http_exception(err)
    finally:
        # Release the spooled temp file as soon as Drive has it (SC-004).
        await video_file.close()

    try:
        from sqlalchemy.orm.attributes import flag_modified

        if name is not None:
            activity.name = name
        activity.content = _gdrive_content(activity_uuid, upload, original_filename)
        flag_modified(activity, "content")
        activity.update_date = str(datetime.now())
        db_session.add(activity)
        await db_session.commit()
        await db_session.refresh(activity)
    except Exception:
        logger.exception("Drive video replacement could not be saved (%s); removing new file %s", context, upload.file_id)
        try:
            await db_session.rollback()
        except Exception:  # pragma: no cover - rollback of a dead session
            pass
        await delete_file_best_effort(upload.file_id, context=context)
        raise

    if old_file_id and old_file_id != upload.file_id:
        if await has_other_reference(db_session, activity.id, gdrive_file_id=old_file_id):
            logger.info("Drive file %s kept: still referenced by another activity (%s)", old_file_id, context)
        else:
            await delete_file_best_effort(old_file_id, context=context)

    logger.info(
        "Drive video activity replaced (%s): old_file=%s new_file=%s folder=%s result=ok",
        context, old_file_id, upload.file_id, upload.folder_id,
    )
    return ActivityRead.model_validate(activity)


# --------------------------------------------------------------------------
# AI closed captions
# --------------------------------------------------------------------------

_CAPTION_LANG_RE = re.compile(r"^[A-Za-z0-9-]{2,20}$")
MAX_CAPTION_LANGUAGES = 15


class CaptionLanguageIn(BaseModel):
    code: str
    label: Optional[str] = None


class CaptionsConfigIn(BaseModel):
    enabled: bool = True
    # "auto" = let the model detect the spoken language, or a specific code.
    source_language: str = "auto"
    languages: list[CaptionLanguageIn] = []


async def configure_captions(
    request: Request,
    activity_uuid: str,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
    config: CaptionsConfigIn,
) -> dict:
    """Persist AI-caption settings on a hosted-video activity and (when enabled)
    enqueue generation. Instructor must have UPDATE rights on the course.

    Languages may be any of the platform's available languages OR custom
    additions — any well-formed BCP-47-ish code is accepted (the model can
    translate to it). Returns the stored `captions` metadata block.
    """
    activity = (
        await db_session.execute(select(Activity).where(Activity.activity_uuid == activity_uuid))
    ).scalars().first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    if activity.activity_sub_type != ActivitySubTypeEnum.SUBTYPE_VIDEO_HOSTED:
        raise HTTPException(status_code=400, detail="Captions are only available for hosted videos")

    course = (
        await db_session.execute(select(Course).where(Course.id == activity.course_id))
    ).scalars().first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    await check_resource_access(
        request, db_session, current_user, course.course_uuid, AccessAction.UPDATE
    )

    # Validate + dedupe target languages.
    seen: set[str] = set()
    langs: list[dict] = []
    for lang in config.languages:
        code = (lang.code or "").strip()
        if not _CAPTION_LANG_RE.match(code):
            raise HTTPException(status_code=400, detail=f"Invalid language code: {lang.code!r}")
        if code in seen:
            continue
        seen.add(code)
        langs.append({"code": code, "label": (lang.label or code).strip(), "status": "queued"})
    if len(langs) > MAX_CAPTION_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"At most {MAX_CAPTION_LANGUAGES} languages")
    src = (config.source_language or "auto").strip()
    if src != "auto" and not _CAPTION_LANG_RE.match(src):
        raise HTTPException(status_code=400, detail="Invalid source language code")
    if config.enabled and not langs:
        raise HTTPException(status_code=400, detail="Choose at least one caption language")

    # Feature + credit pre-check for immediate feedback (the job re-checks + meters).
    if config.enabled:
        from src.security.features_utils.usage import (
            check_feature_enabled,
            get_ai_credits_summary,
        )
        await check_feature_enabled("ai", activity.org_id, db_session)
        summary = await get_ai_credits_summary(activity.org_id, db_session)
        remaining = summary.get("remaining_credits")
        if isinstance(remaining, (int, float)) and remaining != -1 and remaining <= 0:
            raise HTTPException(status_code=402, detail="No AI credits remaining")

    from sqlalchemy.orm.attributes import flag_modified

    meta = dict(activity.extra_metadata or {})
    captions = dict(meta.get("captions") or {})
    captions.update({
        "enabled": config.enabled,
        "auto_generate": True,
        "source_language": src,
        "languages": langs,
        "status": "queued" if (config.enabled and langs) else "idle",
        "error": None,
        "updated_at": str(datetime.now()),
    })
    meta["captions"] = captions
    activity.extra_metadata = meta
    flag_modified(activity, "extra_metadata")
    db_session.add(activity)
    await db_session.commit()

    if config.enabled and langs:
        try:
            from src.services.utils.caption_jobs import enqueue as enqueue_captions
            enqueue_captions(activity_uuid)
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to enqueue captions for %s", activity_uuid
            )

    return captions


async def update_external_video_activity(
    request: Request,
    activity_uuid: str,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
    uri: Optional[str] = None,
    name: Optional[str] = None,
    details: Optional[str] = None,
) -> ActivityRead:
    statement = select(Activity).where(Activity.activity_uuid == activity_uuid)
    activity = (await db_session.execute(statement)).scalars().first()

    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    statement = select(Course).where(Course.id == activity.course_id)
    course = (await db_session.execute(statement)).scalars().first()

    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    await check_resource_access(
        request, db_session, current_user, course.course_uuid, AccessAction.UPDATE
    )

    # A Drive video never gets a `uri` (FR-008) or playback details, and its
    # storage location never changes (FR-014): refuse before touching anything.
    if is_gdrive_activity(activity):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=STORAGE_CHANGE_DETAIL)

    if name is not None:
        activity.name = name

    if uri:
        content = dict(activity.content) if activity.content else {}
        content["uri"] = uri
        activity.content = content
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(activity, "content")

    if details:
        activity.details = json.loads(details)

    activity.update_date = str(datetime.now())
    db_session.add(activity)
    await db_session.commit()
    await db_session.refresh(activity)

    return ActivityRead.model_validate(activity)


## 🔒 RBAC Utils ##
