"""Tests for src/services/courses/activities/video.py."""

from unittest.mock import AsyncMock, MagicMock, patch
from sqlmodel import select

import pytest
from fastapi import HTTPException, UploadFile

from src.services.courses.activities.video import (
    create_external_video_activity,
    create_video_activity,
    ExternalVideo,
)


def _mock_video_file(content_type: str = "video/mp4", filename: str = "test.mp4") -> MagicMock:
    uf = MagicMock(spec=UploadFile)
    uf.content_type = content_type
    uf.filename = filename
    return uf


class TestCreateVideoActivity:
    @pytest.mark.asyncio
    async def test_raises_404_when_chapter_not_found(
        self, mock_request, db, org, admin_user
    ):
        with pytest.raises(HTTPException) as exc:
            await create_video_activity(
                mock_request,
                name="Test Video",
                chapter_id=9999,
                current_user=admin_user,
                db_session=db,
                video_file=None,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_409_when_no_video_file(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        with patch(
            "src.services.courses.activities.video.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await create_video_activity(
                    mock_request,
                    name="Test Video",
                    chapter_id=chapter.id,
                    current_user=admin_user,
                    db_session=db,
                    video_file=None,
                )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_for_invalid_video_content_type(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        with patch(
            "src.services.courses.activities.video.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await create_video_activity(
                    mock_request,
                    name="Test Video",
                    chapter_id=chapter.id,
                    current_user=admin_user,
                    db_session=db,
                    video_file=_mock_video_file(content_type="text/plain"),
                )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_creates_video_activity_successfully(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        with patch(
            "src.services.courses.activities.video.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.video.upload_video",
            new_callable=AsyncMock,
            return_value="video_test.mp4",
        ):
            result = await create_video_activity(
                mock_request,
                name="Test Video",
                chapter_id=chapter.id,
                current_user=admin_user,
                db_session=db,
                video_file=_mock_video_file(),
            )

        assert result.name == "Test Video"


class TestCreateExternalVideoActivity:
    @pytest.mark.asyncio
    async def test_raises_404_when_chapter_not_found(
        self, mock_request, db, org, admin_user
    ):
        data = ExternalVideo(
            name="YT Video", uri="https://youtube.com/watch?v=abc", type="youtube", chapter_id=9999
        )
        with pytest.raises(HTTPException) as exc:
            await create_external_video_activity(mock_request, admin_user, data, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_creates_external_video_activity(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        data = ExternalVideo(
            name="YT Video",
            uri="https://youtube.com/watch?v=abc123",
            type="youtube",
            chapter_id=chapter.id,
        )
        with patch(
            "src.services.courses.activities.video.check_resource_access",
            new_callable=AsyncMock,
        ):
            result = await create_external_video_activity(
                mock_request, admin_user, data, db
            )
        assert result.name == "YT Video"


# ---------------------------------------------------------------------------
# Google Drive storage (storage="gdrive") — create + update branches
# ---------------------------------------------------------------------------

import contextlib  # noqa: E402
import io  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from starlette.datastructures import Headers  # noqa: E402

from src.db.courses.activities import Activity, ActivitySubTypeEnum  # noqa: E402
from src.services.courses.activities.video import update_video_activity  # noqa: E402
from src.services.integrations.gdrive.errors import (  # noqa: E402
    GDriveNotEnabledError,
    GDriveQuotaExceededError,
    GDriveTransientError,
)
from src.services.integrations.gdrive.service import DriveUploadResult  # noqa: E402

_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
_V = "src.services.courses.activities.video"


def _real_upload(data=_MP4, name="lesson-1.mp4", content_type="video/mp4"):
    return UploadFile(file=io.BytesIO(data), filename=name, headers=Headers({"content-type": content_type}))


def _result(file_id="file_new", folder_id="folder_new", mime="video/mp4", size=len(_MP4)):
    return DriveUploadResult(file_id=file_id, folder_id=folder_id, stored_filename="video.mp4", mime_type=mime, size=size)


@contextlib.contextmanager
def _create_ctx(upload_result=None, upload_side_effect=None):
    """Patch the collaborators of create_video_activity; yields the mocks."""
    with contextlib.ExitStack() as stack:
        m = SimpleNamespace()
        m.rbac = stack.enter_context(patch(f"{_V}.check_resource_access", new_callable=AsyncMock))
        m.disk = stack.enter_context(patch(f"{_V}.upload_video", new_callable=AsyncMock))
        m.upload = stack.enter_context(
            patch(f"{_V}.upload_activity_video", new_callable=AsyncMock, return_value=upload_result, side_effect=upload_side_effect)
        )
        m.cleanup = stack.enter_context(patch(f"{_V}.delete_folder_best_effort", new_callable=AsyncMock, return_value=True))
        yield m


@contextlib.contextmanager
def _update_ctx(ready=True, upload_result=None, folder_created=False):
    """Patch the collaborators of update_video_activity; yields the mocks."""
    with contextlib.ExitStack() as stack:
        m = SimpleNamespace()
        m.rbac = stack.enter_context(patch(f"{_V}.check_resource_access", new_callable=AsyncMock))
        m.ready = stack.enter_context(
            patch(f"{_V}.require_ready", new_callable=AsyncMock, side_effect=None if ready else GDriveNotEnabledError("off"))
        )
        m.resolve = stack.enter_context(
            patch(f"{_V}.ensure_activity_folder", new_callable=AsyncMock, return_value=("folder_resolved", folder_created))
        )
        m.upload = stack.enter_context(
            patch(f"{_V}.upload_activity_video", new_callable=AsyncMock, return_value=upload_result or _result(folder_id="folder_resolved"))
        )
        m.delete = stack.enter_context(patch(f"{_V}.delete_file_best_effort", new_callable=AsyncMock, return_value=True))
        m.delete_folder = stack.enter_context(
            patch(f"{_V}.delete_folder_best_effort", new_callable=AsyncMock, return_value=True)
        )
        m.disk = stack.enter_context(patch(f"{_V}.upload_video", new_callable=AsyncMock))
        yield m


async def _create(db, chapter, admin_user, mock_request, **kwargs):
    args = dict(name="Drive lesson", chapter_id=chapter.id, current_user=admin_user, db_session=db, video_file=_real_upload(), storage="gdrive")
    args.update(kwargs)
    return await create_video_activity(mock_request, **args)


class TestCreateGDriveVideoActivity:
    @pytest.mark.asyncio
    async def test_creates_drive_activity_without_touching_server_storage(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        with _create_ctx(_result()) as m:
            result = await _create(db, chapter, admin_user, mock_request, details='{"autoplay": true, "startTime": 5}')
        m.disk.assert_not_awaited()
        kwargs = m.upload.await_args.kwargs
        assert kwargs["org_uuid"] == org.org_uuid and kwargs["course_uuid"] == course.course_uuid
        assert kwargs["activity_uuid"].startswith("activity_") and kwargs["mime_type"] == "video/mp4"
        assert kwargs["size"] == len(_MP4) and kwargs["original_filename"] == "lesson-1.mp4" and kwargs["user_id"] == admin_user.id
        assert result.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE
        assert result.details == {}  # playback settings are ignored for Drive
        assert result.content == {
            "activity_uuid": result.activity_uuid,
            "storage": "gdrive",
            "gdrive_file_id": "file_new",
            "gdrive_folder_id": "folder_new",
            "original_filename": "lesson-1.mp4",
            "mime_type": "video/mp4",
            "size": len(_MP4),
        }
        row = await db.get(Activity, result.id)
        assert row is not None and row.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE

    @pytest.mark.asyncio
    async def test_original_filename_is_truncated_to_255(self, mock_request, db, org, course, chapter, admin_user):
        with _create_ctx(_result()) as m:
            result = await _create(db, chapter, admin_user, mock_request, video_file=_real_upload(name="x" * 300 + ".mp4"))
        assert len(result.content["original_filename"]) == 255
        assert len(m.upload.await_args.kwargs["original_filename"]) == 255

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc,code", [(GDriveQuotaExceededError, 507), (GDriveTransientError, 502), (GDriveNotEnabledError, 409)])
    async def test_drive_errors_map_to_http_and_create_nothing(
        self, mock_request, db, org, course, chapter, admin_user, exc, code
    ):
        with _create_ctx(upload_side_effect=exc("boom")):
            with pytest.raises(HTTPException) as err:
                await _create(db, chapter, admin_user, mock_request)
        assert err.value.status_code == code
        assert err.value.detail.startswith("Video : ")
        assert (await db.execute(select(Activity))).scalars().all() == []

    @pytest.mark.asyncio
    async def test_wrong_format_rejected_before_drive(self, mock_request, db, org, course, chapter, admin_user):
        with _create_ctx(_result()) as m:
            with pytest.raises(HTTPException) as err:
                await _create(db, chapter, admin_user, mock_request, video_file=_real_upload(name="movie.mkv", content_type="video/x-matroska"))
            assert err.value.status_code == 409 and err.value.detail == "Video : Wrong video format"
            # Right content-type but bad bytes → validator, still before Drive.
            with pytest.raises(HTTPException) as err:
                await _create(db, chapter, admin_user, mock_request, video_file=_real_upload(data=b"not a video" * 10))
            assert err.value.status_code == 415
            m.upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_failure_after_upload_removes_drive_folder(self, mock_request, db, org, course, chapter, admin_user):
        real_commit = db.commit
        calls = {"n": 0}

        async def flaky_commit():
            calls["n"] += 1
            if calls["n"] == 2:  # first commit releases the connection; second inserts the Activity
                raise RuntimeError("db down")
            await real_commit()

        with _create_ctx(_result(folder_id="folder_to_remove")) as m, patch.object(db, "commit", side_effect=flaky_commit):
            with pytest.raises(RuntimeError):
                await _create(db, chapter, admin_user, mock_request)
        m.cleanup.assert_awaited_once()
        assert m.cleanup.await_args.args[0] == "folder_to_remove"

    @pytest.mark.asyncio
    async def test_upload_file_is_closed_after_success_and_after_drive_failure(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        """The server-side temp file must be released explicitly, not left to the request teardown (SC-004)."""
        ok = _real_upload()
        with _create_ctx(_result()):
            await _create(db, chapter, admin_user, mock_request, video_file=ok)
        assert ok.file.closed

        failed = _real_upload()
        with _create_ctx(upload_side_effect=GDriveTransientError("boom")):
            with pytest.raises(HTTPException):
                await _create(db, chapter, admin_user, mock_request, video_file=failed)
        assert failed.file.closed

    @pytest.mark.asyncio
    async def test_server_storage_path_unchanged(self, mock_request, db, org, course, chapter, admin_user):
        with _create_ctx(_result()) as m:
            m.disk.return_value = "video_saved.mp4"
            result = await _create(db, chapter, admin_user, mock_request, storage="server")
        m.upload.assert_not_awaited()
        m.disk.assert_awaited_once()
        assert result.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_HOSTED


class TestUpdateGDriveVideoActivity:
    @pytest.mark.asyncio
    async def test_rename_rejected_when_not_ready_even_without_file(self, mock_request, db, org, course, chapter, gdrive_activity, admin_user):
        with _update_ctx(ready=False) as m:
            with pytest.raises(HTTPException) as err:
                await update_video_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db, name="Renamed")
        assert err.value.status_code == 409
        assert err.value.detail == "Video : Google Drive storage is not enabled on this instance"
        await db.refresh(gdrive_activity)
        assert gdrive_activity.name == "Drive Video"
        m.upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rename_only_when_ready_does_not_upload_and_ignores_playback(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        with _update_ctx() as m:
            result = await update_video_activity(
                mock_request, gdrive_activity.activity_uuid, admin_user, db, name="Renamed",
                details='{"autoplay": true, "startTime": 9}',
            )
        assert result.name == "Renamed"
        assert result.details == {}
        assert result.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE
        m.upload.assert_not_awaited()
        m.resolve.assert_not_awaited()
        m.ready.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_replacement_upload_file_is_closed(self, mock_request, db, org, course, chapter, gdrive_activity, admin_user):
        replacement = _real_upload(name="other.mp4")
        with _update_ctx():
            await update_video_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db, name=None, video_file=replacement)
        assert replacement.file.closed

    @pytest.mark.asyncio
    async def test_replace_file_uploads_into_own_folder_and_deletes_old(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        with _update_ctx() as m:
            result = await update_video_activity(
                mock_request, gdrive_activity.activity_uuid, admin_user, db, name=None, video_file=_real_upload(name="other.mp4"),
            )
        m.resolve.assert_awaited_once_with(org.org_uuid, course.course_uuid, gdrive_activity.activity_uuid)
        assert m.upload.await_args.kwargs["folder_id"] == "folder_resolved"
        assert m.upload.await_args.kwargs["original_filename"] == "other.mp4"
        assert result.content["gdrive_file_id"] == "file_new"
        assert result.content["gdrive_folder_id"] == "folder_resolved"
        assert result.content["original_filename"] == "other.mp4"
        assert result.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE
        m.delete.assert_awaited_once()
        assert m.delete.await_args.args[0] == "file_abc123"
        m.disk.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_old_file_kept_when_clone_references_it(self, mock_request, db, org, course, chapter, gdrive_activity, admin_user):
        from datetime import datetime as _dt

        clone = Activity(
            id=50, name="clone", activity_type=gdrive_activity.activity_type,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE, content=dict(gdrive_activity.content),
            org_id=org.id, course_id=course.id, activity_uuid="activity_clone",
            creation_date=str(_dt.now()), update_date=str(_dt.now()),
        )
        db.add(clone)
        await db.commit()
        with _update_ctx() as m:
            await update_video_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db, video_file=_real_upload())
        m.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_replace_wrong_format_and_drive_error(self, mock_request, db, org, course, chapter, gdrive_activity, admin_user):
        with _update_ctx() as m:
            with pytest.raises(HTTPException) as err:
                await update_video_activity(
                    mock_request, gdrive_activity.activity_uuid, admin_user, db,
                    video_file=_real_upload(name="m.mkv", content_type="video/x-matroska"),
                )
            assert err.value.status_code == 409 and err.value.detail == "Video : Wrong video format"
            m.upload.side_effect = GDriveTransientError("down")
            with pytest.raises(HTTPException) as err:
                await update_video_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db, video_file=_real_upload())
            assert err.value.status_code == 502
        await db.refresh(gdrive_activity)
        assert gdrive_activity.content["gdrive_file_id"] == "file_abc123"

    @pytest.mark.asyncio
    async def test_db_failure_after_replacement_removes_new_file(self, mock_request, db, org, course, chapter, gdrive_activity, admin_user):
        real_commit = db.commit
        calls = {"n": 0}

        async def flaky_commit():
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("db down")
            await real_commit()

        with _update_ctx() as m, patch.object(db, "commit", side_effect=flaky_commit):
            with pytest.raises(RuntimeError):
                await update_video_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db, video_file=_real_upload())
        m.delete.assert_awaited_once()
        assert m.delete.await_args.args[0] == "file_new"

    @pytest.mark.asyncio
    async def test_hosted_activity_never_consults_readiness(self, mock_request, db, org, course, chapter, hosted_activity, admin_user):
        with _update_ctx(ready=False) as m:
            result = await update_video_activity(mock_request, hosted_activity.activity_uuid, admin_user, db, name="Hosted renamed")
        assert result.name == "Hosted renamed"
        assert result.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_HOSTED
        m.ready.assert_not_awaited()
        m.upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_video_update_never_changes_storage_location(
        self, mock_request, db, org, course, chapter, hosted_activity, youtube_activity, gdrive_activity, admin_user
    ):
        """PUT /activities/video/{uuid} has no storage switch: every subtype stays what it was (FR-014)."""
        with _update_ctx(ready=True) as m:
            hosted = await update_video_activity(mock_request, hosted_activity.activity_uuid, admin_user, db, name="h")
            youtube = await update_video_activity(mock_request, youtube_activity.activity_uuid, admin_user, db, name="y")
            drive = await update_video_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db, name="d")
        assert hosted.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_HOSTED
        assert youtube.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_YOUTUBE
        assert drive.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE
        assert drive.content["storage"] == "gdrive" and drive.content["gdrive_file_id"] == "file_abc123"
        m.upload.assert_not_awaited()


# ---------------------------------------------------------------------------
# PUT /activities/external_video/{uuid} on a Drive activity (FR-004 / FR-014 / FR-008)
# ---------------------------------------------------------------------------

from src.services.courses.activities.video import update_external_video_activity  # noqa: E402

_STORAGE_CHANGE_DETAIL = "Video : Storage location of a video activity cannot be changed"


class TestExternalVideoUpdateOnDriveActivity:
    @pytest.mark.asyncio
    async def test_drive_activity_is_refused_and_untouched(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        """The YouTube/Vimeo update path must not write a `uri` (a link FR-008 forbids
        storing) or playback `details` onto a Drive activity, ready or not."""
        original = dict(gdrive_activity.content)
        with patch(f"{_V}.check_resource_access", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as err:
                await update_external_video_activity(
                    mock_request, gdrive_activity.activity_uuid, admin_user, db,
                    uri="https://www.youtube.com/watch?v=hijack", name="Hijacked", details='{"autoplay": true}',
                )
        assert err.value.status_code == 409
        assert err.value.detail == _STORAGE_CHANGE_DETAIL
        await db.refresh(gdrive_activity)
        assert gdrive_activity.content == original
        assert "uri" not in gdrive_activity.content
        assert gdrive_activity.name == "Drive Video"
        assert gdrive_activity.details == {}
        assert gdrive_activity.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE

    @pytest.mark.asyncio
    async def test_youtube_activity_keeps_working(
        self, mock_request, db, org, course, chapter, youtube_activity, admin_user
    ):
        with patch(f"{_V}.check_resource_access", new_callable=AsyncMock):
            result = await update_external_video_activity(
                mock_request, youtube_activity.activity_uuid, admin_user, db,
                uri="https://www.youtube.com/watch?v=updated", name="YT renamed",
            )
        assert result.name == "YT renamed"
        assert result.content["uri"].endswith("updated")
        assert result.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_YOUTUBE


# ---------------------------------------------------------------------------
# Audit context (FR-013): user, org, course, activity and Drive ids on every line
# ---------------------------------------------------------------------------

import logging as _logging  # noqa: E402


def _audit_line(caplog, needle):
    return next(r.getMessage() for r in caplog.records if needle in r.getMessage())


class TestGDriveAuditContext:
    @pytest.mark.asyncio
    async def test_rename_audit_line_names_user_org_course_activity(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user, caplog
    ):
        with _update_ctx(), caplog.at_level(_logging.INFO):
            await update_video_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db, name="Renamed")
        line = _audit_line(caplog, "Drive video activity updated")
        for needle in (f"user={admin_user.id}", f"org={org.org_uuid}", f"course={course.course_uuid}", "activity=activity_gdrive"):
            assert needle in line

    @pytest.mark.asyncio
    async def test_replace_audit_line_and_cleanup_context_name_org_and_ids(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user, caplog
    ):
        with _update_ctx() as m, caplog.at_level(_logging.INFO):
            await update_video_activity(
                mock_request, gdrive_activity.activity_uuid, admin_user, db, video_file=_real_upload(name="other.mp4")
            )
        context = m.delete.await_args.kwargs["context"]
        assert f"org={org.org_uuid}" in context
        assert f"user={admin_user.id}" in context
        line = _audit_line(caplog, "Drive video activity replaced")
        for needle in (
            f"user={admin_user.id}", f"org={org.org_uuid}", f"course={course.course_uuid}", "activity=activity_gdrive",
            "old_file=file_abc123", "new_file=file_new", "folder=folder_resolved",
        ):
            assert needle in line


# ---------------------------------------------------------------------------
# Replacement failure must not leave an activity folder this request created
# ---------------------------------------------------------------------------


class TestReplaceFailureCleansUpCreatedFolder:
    @pytest.mark.asyncio
    async def test_folder_created_for_this_activity_is_removed_on_upload_failure(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        """A cloned activity has no folder of its own yet: the replace flow creates one and,
        when the upload then fails, must remove it again (spec Edge Cases)."""
        with _update_ctx(folder_created=True) as m:
            m.upload.side_effect = GDriveTransientError("down")
            with pytest.raises(HTTPException) as err:
                await update_video_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db, video_file=_real_upload())
        assert err.value.status_code == 502
        m.delete_folder.assert_awaited_once()
        assert m.delete_folder.await_args.args[0] == "folder_resolved"
        assert f"org={org.org_uuid}" in m.delete_folder.await_args.kwargs["context"]
        m.delete.assert_not_awaited()
        await db.refresh(gdrive_activity)
        assert gdrive_activity.content["gdrive_file_id"] == "file_abc123"

    @pytest.mark.asyncio
    async def test_pre_existing_folder_is_kept_on_upload_failure(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        with _update_ctx(folder_created=False) as m:
            m.upload.side_effect = GDriveTransientError("down")
            with pytest.raises(HTTPException):
                await update_video_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db, video_file=_real_upload())
        m.delete_folder.assert_not_awaited()
        m.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Remaining branches of the Drive replace/rename path
# ---------------------------------------------------------------------------


class TestUpdateGDriveVideoActivityEdgeBranches:
    @pytest.mark.asyncio
    async def test_missing_organization_yields_404_and_changes_nothing(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        """The Organization row is read before the rename branch so every audit line can
        name the org (FR-013); when that row is gone the request must 404 rather than
        log `org=None` or touch Drive."""
        gdrive_activity.org_id = 999_999
        db.add(gdrive_activity)
        await db.commit()
        with _update_ctx() as m:
            with pytest.raises(HTTPException) as err:
                await update_video_activity(
                    mock_request, gdrive_activity.activity_uuid, admin_user, db, name="Renamed"
                )
        assert err.value.status_code == 404
        assert err.value.detail == "Organization not found"
        m.upload.assert_not_awaited()
        m.resolve.assert_not_awaited()
        await db.refresh(gdrive_activity)
        assert gdrive_activity.name == "Drive Video"

    @pytest.mark.asyncio
    async def test_create_without_organization_yields_404_before_touching_drive(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        """Symmetric to the update path: the create branch needs the org uuid for the Drive
        folder layout (FR-006), so a missing org row must 404 before any upload."""
        from src.db.courses.course_chapters import CourseChapter

        # create_video_activity resolves the org through the course<->chapter link.
        link = (
            await db.execute(select(CourseChapter).where(CourseChapter.chapter_id == chapter.id))
        ).scalars().first()
        link.org_id = 999_999
        db.add(link)
        await db.commit()
        with _create_ctx(_result()) as m:
            with pytest.raises(HTTPException) as err:
                await _create(db, chapter, admin_user, mock_request)
        assert err.value.status_code == 404
        assert err.value.detail == "Organization not found"
        m.upload.assert_not_awaited()
        m.disk.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_replacing_the_file_and_renaming_in_one_request(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        """US4/AC1: `name` and `video_file` may arrive together — both take effect and the
        old Drive file is still cleaned up."""
        with _update_ctx() as m:
            result = await update_video_activity(
                mock_request, gdrive_activity.activity_uuid, admin_user, db,
                name="Lesson 1 (v2)", video_file=_real_upload(name="other.mp4"),
            )
        assert result.name == "Lesson 1 (v2)"
        assert result.content["gdrive_file_id"] == "file_new"
        assert result.content["original_filename"] == "other.mp4"
        assert result.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE
        m.delete.assert_awaited_once()
        assert m.delete.await_args.args[0] == "file_abc123"
        await db.refresh(gdrive_activity)
        assert gdrive_activity.name == "Lesson 1 (v2)"
