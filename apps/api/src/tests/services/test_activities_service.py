"""Tests for src/services/courses/activities/activities.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.db.courses.activities import ActivityCreate, ActivityRead, ActivityTypeEnum, ActivitySubTypeEnum, ActivityUpdate
from src.db.organizations import OrganizationRead
from src.services.courses.activities.activities import (
    _apply_activity_lock,
    _trigger_course_embedding,
    create_activity,
    delete_activity,
    get_activities,
    get_activity,
    get_activityby_id,
    get_editor_bootstrap,
    update_activity,
    EditorBootstrapResponse,
)


class TestCreateActivity:
    @pytest.mark.asyncio
    async def test_raises_404_when_chapter_not_found(
        self, mock_request, db, org, admin_user
    ):
        activity_obj = ActivityCreate(
            name="Test",
            activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            chapter_id=9999,
            course_id=1,
            org_id=org.id,
            content={},
        )
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await create_activity(mock_request, activity_obj, admin_user, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_creates_activity_successfully(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        activity_obj = ActivityCreate(
            name="New Activity",
            activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            chapter_id=chapter.id,
            course_id=course.id,
            org_id=org.id,
            content={"type": "doc", "content": []},
        )
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ):
            result = await create_activity(mock_request, activity_obj, admin_user, db)

        assert isinstance(result, ActivityRead)
        assert result.name == "New Activity"


class TestGetEditorBootstrap:
    @pytest.mark.asyncio
    async def test_raises_404_when_activity_not_found(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.activities.check_ee_activity_paid_access",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "src.services.courses.activities.activities._apply_activity_lock",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await get_editor_bootstrap(mock_request, "nonexistent-uuid", admin_user, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_editor_bootstrap_response(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        fake_org_read = MagicMock(spec=OrganizationRead)

        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.activities.check_ee_activity_paid_access",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "src.services.courses.activities.activities._apply_activity_lock",
            new_callable=AsyncMock,
        ), patch(
            "src.services.orgs.orgs._build_org_read_with_resolved",
            return_value=fake_org_read,
        ):
            result = await get_editor_bootstrap(
                mock_request, activity.activity_uuid, admin_user, db
            )

        assert isinstance(result, EditorBootstrapResponse)
        assert result.activity.name == activity.name
        assert result.course.org_uuid == org.org_uuid

    @pytest.mark.asyncio
    async def test_scrubs_content_when_no_paid_access(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        fake_org_read = MagicMock(spec=OrganizationRead)

        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.activities.check_ee_activity_paid_access",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "src.services.courses.activities.activities._apply_activity_lock",
            new_callable=AsyncMock,
        ), patch(
            "src.services.orgs.orgs._build_org_read_with_resolved",
            return_value=fake_org_read,
        ):
            result = await get_editor_bootstrap(
                mock_request, activity.activity_uuid, admin_user, db
            )

        assert result.activity.content == {"paid_access": False}


class TestGetActivity:
    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_activity(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.activities.check_ee_activity_paid_access",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "src.services.courses.activities.activities._apply_activity_lock",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await get_activity(mock_request, "nonexistent-uuid", admin_user, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_activity_read(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.activities.check_ee_activity_paid_access",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "src.services.courses.activities.activities._apply_activity_lock",
            new_callable=AsyncMock,
        ):
            result = await get_activity(
                mock_request, activity.activity_uuid, admin_user, db
            )

        assert isinstance(result, ActivityRead)
        assert result.name == activity.name


class TestUpdateActivity:
    @pytest.mark.asyncio
    async def test_raises_404_when_activity_not_found(
        self, mock_request, db, admin_user
    ):
        update_obj = ActivityUpdate(name="Updated")
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await update_activity(mock_request, update_obj, "nonexistent-uuid", admin_user, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_updates_activity_name(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        update_obj = ActivityUpdate(name="Renamed Activity")
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.activities.create_activity_version",
            new_callable=AsyncMock,
        ):
            result = await update_activity(
                mock_request, update_obj, activity.activity_uuid, admin_user, db
            )
        assert isinstance(result, ActivityRead)
        assert result.name == "Renamed Activity"

    @pytest.mark.asyncio
    async def test_updates_activity_content_creates_version(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        new_content = {"type": "doc", "content": [{"type": "paragraph"}]}
        update_obj = ActivityUpdate(content=new_content)
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.activities.create_activity_version",
            new_callable=AsyncMock,
        ) as mock_version, patch(
            "src.services.courses.activities.activities._trigger_course_embedding",
            new_callable=AsyncMock,
        ):
            result = await update_activity(
                mock_request, update_obj, activity.activity_uuid, admin_user, db
            )
        assert isinstance(result, ActivityRead)
        mock_version.assert_called_once()


class TestDeleteActivity:
    @pytest.mark.asyncio
    async def test_raises_404_when_activity_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await delete_activity(mock_request, "nonexistent-uuid", admin_user, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_deletes_activity(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.transfer.storage_utils.delete_storage_directory",
            return_value=None,
        ):
            result = await delete_activity(
                mock_request, activity.activity_uuid, admin_user, db
            )
        assert result == {"detail": "Activity deleted"}


class TestGetActivityById:
    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(
        self, mock_request, db, admin_user
    ):
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await get_activityby_id(mock_request, 99999, admin_user, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_activity_by_id(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ):
            result = await get_activityby_id(mock_request, activity.id, admin_user, db)
        assert isinstance(result, ActivityRead)
        assert result.id == activity.id


# ---------------------------------------------------------------------------
# _apply_activity_lock
# ---------------------------------------------------------------------------

_PATCH_IS_ORG_ADMIN = "src.services.courses.activities.activities.is_org_admin"
_PATCH_BATCH_ACCESSIBLE = "src.services.courses.activities.activities.batch_accessible_restricted_uuids"
_PATCH_IS_LOCKED = "src.services.courses.activities.activities.is_locked_for_user"


class TestApplyActivityLock:
    @pytest.mark.asyncio
    async def test_admin_bypasses_lock(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        """Admin path: returns immediately without locking (covers lines 272-275)."""
        activity_read = ActivityRead.model_validate(activity)
        with patch(_PATCH_IS_ORG_ADMIN, new_callable=AsyncMock, return_value=True):
            await _apply_activity_lock(activity_read, activity, course, admin_user, db)
        assert activity_read.is_locked is False

    @pytest.mark.asyncio
    async def test_uses_provided_parent_chapter(
        self, mock_request, db, org, course, chapter, activity, regular_user
    ):
        """parent_chapter provided: no extra query (covers line 281)."""
        activity_read = ActivityRead.model_validate(activity)
        with patch(_PATCH_IS_ORG_ADMIN, new_callable=AsyncMock, return_value=False), \
             patch(_PATCH_BATCH_ACCESSIBLE, new_callable=AsyncMock, return_value=set()), \
             patch(_PATCH_IS_LOCKED, new_callable=AsyncMock, return_value=False):
            await _apply_activity_lock(
                activity_read, activity, course, regular_user, db, parent_chapter=chapter
            )
        assert activity_read.is_locked is False

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_fetches_chapter_when_not_provided(
        self, mock_request, db, org, course, chapter, activity, regular_user
    ):
        """parent_chapter=None: queries DB for chapter (covers line 285)."""
        activity_read = ActivityRead.model_validate(activity)
        with patch(_PATCH_IS_ORG_ADMIN, new_callable=AsyncMock, return_value=False), \
             patch(_PATCH_BATCH_ACCESSIBLE, new_callable=AsyncMock, return_value=set()), \
             patch(_PATCH_IS_LOCKED, new_callable=AsyncMock, return_value=False):
            await _apply_activity_lock(
                activity_read, activity, course, regular_user, db, parent_chapter=None
            )
        assert activity_read.is_locked is False

    @pytest.mark.asyncio
    async def test_locks_activity_when_restricted(
        self, mock_request, db, org, course, chapter, activity, regular_user
    ):
        """Covers lines 295, 305, 315, 326-329 (restricted lock path)."""
        activity_read = ActivityRead.model_validate(activity)
        with patch(_PATCH_IS_ORG_ADMIN, new_callable=AsyncMock, return_value=False), \
             patch(_PATCH_BATCH_ACCESSIBLE, new_callable=AsyncMock, return_value=set()), \
             patch(_PATCH_IS_LOCKED, new_callable=AsyncMock, return_value=True):
            await _apply_activity_lock(
                activity_read, activity, course, regular_user, db
            )
        assert activity_read.is_locked is True


# ---------------------------------------------------------------------------
# _trigger_course_embedding
# ---------------------------------------------------------------------------


class TestTriggerCourseEmbedding:
    @pytest.mark.asyncio
    async def test_runs_embedding_when_course_found(self, db, course):
        """Covers lines 438-439 (lazy imports inside _trigger_course_embedding)."""
        async def fake_get_db():
            yield db

        with patch(
            "src.core.events.database.get_db_session",
            return_value=fake_get_db(),
        ), patch(
            "src.services.ai.rag.embedding_service.embed_course_content",
            new_callable=AsyncMock,
        ) as mock_embed:
            await _trigger_course_embedding(course.id, course.org_id)

        mock_embed.assert_called_once_with(course.id, course.org_id, db)


# ---------------------------------------------------------------------------
# get_activities
# ---------------------------------------------------------------------------


class TestGetActivities:
    @pytest.mark.asyncio
    async def test_raises_404_when_no_published_activities(
        self, mock_request, db, org, course, chapter, admin_user
    ):
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await get_activities(mock_request, chapter.id, admin_user, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_published_activities(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        """Covers line 525 (the select statement in get_activities)."""
        with patch(
            "src.services.courses.activities.activities.check_resource_access",
            new_callable=AsyncMock,
        ):
            result = await get_activities(mock_request, chapter.id, admin_user, db)
        assert isinstance(result, list)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# Google Drive video activities: readiness gate (FR-004) + storage invariant
# (FR-014) on the generic update/delete paths.
# ---------------------------------------------------------------------------

from src.db.courses.activities import Activity  # noqa: E402
from src.db.courses.chapters import ChapterOrder, ChapterUpdateOrder, ActivityOrder  # noqa: E402
from src.services.courses.chapters import reorder_chapters_and_activities  # noqa: E402
from src.services.integrations.gdrive.errors import (  # noqa: E402
    GDriveNeedsAuthorizationError,
    GDriveNotEnabledError,
)

_RBAC = "src.services.courses.activities.activities.check_resource_access"
_READY = "src.services.courses.activities.activities.require_ready"
_STORAGE = "src.services.courses.transfer.storage_utils.delete_storage_directory"
_VERSION = "src.services.courses.activities.activities.create_activity_version"
_EMBED = "src.services.courses.activities.activities._trigger_course_embedding"


def _not_ready(exc=GDriveNotEnabledError):
    return patch(_READY, new_callable=AsyncMock, side_effect=exc("not ready"))


def _ready():
    return patch(_READY, new_callable=AsyncMock)


class TestGDriveReadinessGate:
    @pytest.mark.asyncio
    async def test_update_rejected_409_when_not_ready_and_nothing_changes(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        update = ActivityUpdate(name="Renamed", published=False)
        with patch(_RBAC, new_callable=AsyncMock), _not_ready():
            with pytest.raises(HTTPException) as exc:
                await update_activity(mock_request, update, gdrive_activity.activity_uuid, admin_user, db)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Video : Google Drive storage is not enabled on this instance"
        await db.refresh(gdrive_activity)
        assert gdrive_activity.name == "Drive Video"
        assert gdrive_activity.published is True

    @pytest.mark.asyncio
    async def test_update_needs_reauthorization_detail(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        with patch(_RBAC, new_callable=AsyncMock), _not_ready(GDriveNeedsAuthorizationError):
            with pytest.raises(HTTPException) as exc:
                await update_activity(mock_request, ActivityUpdate(name="x"), gdrive_activity.activity_uuid, admin_user, db)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Video : Google Drive needs re-authorization by the operator"

    @pytest.mark.asyncio
    async def test_delete_rejected_409_when_not_ready(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        with patch(_RBAC, new_callable=AsyncMock), _not_ready(), patch(_STORAGE) as storage:
            with pytest.raises(HTTPException) as exc:
                await delete_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db)
        assert exc.value.status_code == 409
        storage.assert_not_called()
        assert await db.get(Activity, gdrive_activity.id) is not None

    @pytest.mark.asyncio
    async def test_non_drive_activities_ignore_readiness(
        self, mock_request, db, org, course, chapter, activity, hosted_activity, youtube_activity, admin_user
    ):
        # Dynamic page, self-hosted video and YouTube video: none of them consults the Drive gate,
        # so they stay editable and deletable while the integration is off (FR-004).
        for other in (activity, hosted_activity, youtube_activity):
            with patch(_RBAC, new_callable=AsyncMock), _not_ready() as ready, patch(_EMBED, new_callable=AsyncMock):
                result = await update_activity(mock_request, ActivityUpdate(name="Still fine"), other.activity_uuid, admin_user, db)
            assert result.name == "Still fine"
            assert result.activity_sub_type == other.activity_sub_type
            ready.assert_not_awaited()
            with patch(_RBAC, new_callable=AsyncMock), _not_ready() as ready, patch(_STORAGE, return_value=None):
                assert await delete_activity(mock_request, other.activity_uuid, admin_user, db) == {"detail": "Activity deleted"}
            ready.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reorder_is_not_gated(
        self, mock_request, db, org, course, chapter, activity, gdrive_activity, admin_user
    ):
        payload = ChapterUpdateOrder(
            chapter_order_by_ids=[
                ChapterOrder(
                    chapter_id=chapter.id,
                    activities_order_by_ids=[
                        ActivityOrder(activity_id=gdrive_activity.id),
                        ActivityOrder(activity_id=activity.id),
                    ],
                )
            ]
        )
        with patch("src.services.courses.chapters.check_resource_access", new_callable=AsyncMock), _not_ready():
            result = await reorder_chapters_and_activities(mock_request, course.course_uuid, payload, admin_user, db)
        assert result["detail"] == "Chapters and activities reordered successfully"


class TestGDriveStorageInvariant:
    @pytest.mark.asyncio
    async def test_drive_activity_cannot_change_subtype(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        update = ActivityUpdate(activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_HOSTED)
        with patch(_RBAC, new_callable=AsyncMock), _ready():
            with pytest.raises(HTTPException) as exc:
                await update_activity(mock_request, update, gdrive_activity.activity_uuid, admin_user, db)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Video : Storage location of a video activity cannot be changed"
        await db.refresh(gdrive_activity)
        assert gdrive_activity.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE

    @pytest.mark.asyncio
    async def test_drive_activity_cannot_rewrite_drive_content_keys(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        original = dict(gdrive_activity.content)
        for bad in (
            {**original, "gdrive_file_id": "someone-elses-file"},
            {**original, "storage": "server"},
            {k: v for k, v in original.items() if k != "gdrive_folder_id"},
        ):
            with patch(_RBAC, new_callable=AsyncMock), _ready(), patch(_VERSION, new_callable=AsyncMock):
                with pytest.raises(HTTPException) as exc:
                    await update_activity(mock_request, ActivityUpdate(content=bad), gdrive_activity.activity_uuid, admin_user, db)
            assert exc.value.status_code == 409
        await db.refresh(gdrive_activity)
        assert gdrive_activity.content == original

    @pytest.mark.asyncio
    async def test_drive_activity_rejects_non_dict_content(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        """FR-014: ``content: null`` must not slip past the invariant and wipe the Drive ids."""
        original = dict(gdrive_activity.content)
        with patch(_RBAC, new_callable=AsyncMock), _ready(), patch(_VERSION, new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc:
                await update_activity(mock_request, ActivityUpdate(content=None), gdrive_activity.activity_uuid, admin_user, db)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Video : Storage location of a video activity cannot be changed"
        await db.refresh(gdrive_activity)
        assert gdrive_activity.content == original

    @pytest.mark.asyncio
    async def test_drive_activity_update_allowed_when_drive_keys_unchanged(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        same = {**gdrive_activity.content, "extra_note": "ok"}
        update = ActivityUpdate(name="Renamed", content=same, activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE)
        with patch(_RBAC, new_callable=AsyncMock), _ready(), patch(_VERSION, new_callable=AsyncMock), patch(_EMBED, new_callable=AsyncMock):
            result = await update_activity(mock_request, update, gdrive_activity.activity_uuid, admin_user, db)
        assert result.name == "Renamed"
        assert result.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE
        assert result.content["gdrive_file_id"] == "file_abc123"

    @pytest.mark.asyncio
    async def test_hosted_and_youtube_activities_cannot_become_drive(
        self, mock_request, db, org, course, chapter, hosted_activity, youtube_activity, admin_user
    ):
        update = ActivityUpdate(activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE)
        for other, expected in (
            (hosted_activity, ActivitySubTypeEnum.SUBTYPE_VIDEO_HOSTED),
            (youtube_activity, ActivitySubTypeEnum.SUBTYPE_VIDEO_YOUTUBE),
        ):
            with patch(_RBAC, new_callable=AsyncMock), _ready():
                with pytest.raises(HTTPException) as exc:
                    await update_activity(mock_request, update, other.activity_uuid, admin_user, db)
            assert exc.value.status_code == 409
            assert "Storage location" in exc.value.detail
            await db.refresh(other)
            assert other.activity_sub_type == expected

    @pytest.mark.asyncio
    async def test_dynamic_page_cannot_become_drive(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        update = ActivityUpdate(activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE)
        with patch(_RBAC, new_callable=AsyncMock), _ready():
            with pytest.raises(HTTPException) as exc:
                await update_activity(mock_request, update, activity.activity_uuid, admin_user, db)
        assert exc.value.status_code == 409
        await db.refresh(activity)
        assert activity.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE


# ---------------------------------------------------------------------------
# delete_activity: Drive folder cleanup (best effort, reference-aware)
# ---------------------------------------------------------------------------

_DEL_FOLDER = "src.services.courses.activities.activities.delete_folder_best_effort"
_HAS_REF = "src.services.courses.activities.activities.has_other_reference"


class TestDeleteGDriveActivityCleanup:
    @pytest.mark.asyncio
    async def test_deletes_folder_when_ready_and_unreferenced(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user, caplog
    ):
        import logging as _logging

        with patch(_RBAC, new_callable=AsyncMock), _ready(), patch(_STORAGE, return_value=None), patch(
            _DEL_FOLDER, new_callable=AsyncMock, return_value=True
        ) as delete_folder, caplog.at_level(_logging.INFO):
            result = await delete_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db)
        assert result == {"detail": "Activity deleted"}
        delete_folder.assert_awaited_once()
        assert delete_folder.await_args.args[0] == "folder_abc123"
        assert "activity=activity_gdrive" in delete_folder.await_args.kwargs["context"]
        # FR-013: the audit context names the org as well as user/course/activity/folder.
        assert f"org={org.org_uuid}" in delete_folder.await_args.kwargs["context"]
        assert f"user={admin_user.id}" in delete_folder.await_args.kwargs["context"]
        assert await db.get(Activity, gdrive_activity.id) is None
        assert "Drive video activity deleted" in caplog.text
        assert f"org={org.org_uuid}" in caplog.text

    @pytest.mark.asyncio
    async def test_drive_failure_does_not_block_delete(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user, caplog
    ):
        import logging as _logging

        # The real best-effort helper swallows Drive errors and logs WARNING.
        from src.services.integrations.gdrive import service as gdrive_service

        def failing_client_factory():
            raise RuntimeError("drive down")

        with patch(_RBAC, new_callable=AsyncMock), _ready(), patch(_STORAGE, return_value=None), patch.object(
            gdrive_service, "_client", failing_client_factory
        ), caplog.at_level(_logging.WARNING):
            result = await delete_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db)
        assert result == {"detail": "Activity deleted"}
        assert await db.get(Activity, gdrive_activity.id) is None
        assert "folder folder_abc123 was not deleted" in caplog.text
        assert "activity=activity_gdrive" in caplog.text
        assert f"org={org.org_uuid}" in caplog.text  # manual clean-up needs the org too (FR-013)

    @pytest.mark.asyncio
    async def test_shared_folder_is_kept(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user
    ):
        with patch(_RBAC, new_callable=AsyncMock), _ready(), patch(_STORAGE, return_value=None), patch(
            _DEL_FOLDER, new_callable=AsyncMock
        ) as delete_folder, patch(_HAS_REF, new_callable=AsyncMock, return_value=True) as has_ref:
            await delete_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db)
        delete_folder.assert_not_awaited()
        assert has_ref.await_args.kwargs["gdrive_folder_id"] == "folder_abc123"

    @pytest.mark.asyncio
    async def test_budget_exhausted_does_not_block_delete(
        self, mock_request, db, org, course, chapter, gdrive_activity, admin_user, caplog, monkeypatch
    ):
        """FR-014: the best-effort cleanup gives up after its time budget and the activity is still gone."""
        import asyncio
        import logging as _logging

        import httpx

        from src.services.integrations.gdrive import service as gdrive_service
        from src.services.integrations.gdrive.client import DriveClient

        monkeypatch.setattr(gdrive_service, "BEST_EFFORT_BUDGET_SECONDS", 0.01)

        async def hang(_request):
            await asyncio.sleep(1)
            return httpx.Response(204)

        async def token():
            return "t"

        def hanging_client():
            return DriveClient(token, http=httpx.AsyncClient(transport=httpx.MockTransport(hang)))

        with patch(_RBAC, new_callable=AsyncMock), _ready(), patch(_STORAGE, return_value=None), patch.object(
            gdrive_service, "_client", hanging_client
        ), caplog.at_level(_logging.WARNING):
            result = await delete_activity(mock_request, gdrive_activity.activity_uuid, admin_user, db)
        assert result == {"detail": "Activity deleted"}
        assert await db.get(Activity, gdrive_activity.id) is None
        assert "folder folder_abc123 was not deleted within" in caplog.text

    @pytest.mark.asyncio
    async def test_non_drive_delete_never_touches_drive(
        self, mock_request, db, org, course, chapter, activity, admin_user
    ):
        with patch(_RBAC, new_callable=AsyncMock), patch(_STORAGE, return_value=None), patch(
            _DEL_FOLDER, new_callable=AsyncMock
        ) as delete_folder, patch(_HAS_REF, new_callable=AsyncMock) as has_ref:
            await delete_activity(mock_request, activity.activity_uuid, admin_user, db)
        delete_folder.assert_not_awaited()
        has_ref.assert_not_awaited()


# ---------------------------------------------------------------------------
# Generic POST /activities/ must never mint a Drive activity (FR-004 / FR-011)
# ---------------------------------------------------------------------------

from sqlmodel import select as _select  # noqa: E402
from src.db.courses.chapter_activities import ChapterActivity  # noqa: E402

_GDRIVE_CREATE_DETAIL = "Video : Google Drive video activities must be created through POST /activities/video"


async def _row_counts(db):
    activities = len((await db.execute(_select(Activity))).scalars().all())
    links = len((await db.execute(_select(ChapterActivity))).scalars().all())
    return activities, links


class TestGenericCreateRefusesDriveSubtype:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("ready", [True, False], ids=["ready", "not_ready"])
    async def test_drive_subtype_is_refused_before_any_write(
        self, mock_request, db, org, course, chapter, admin_user, ready
    ):
        """The only legitimate way to create a Drive video is POST /activities/video with
        storage=gdrive: the generic endpoint cannot upload anything, so a forged Drive
        activity must be refused whether or not the integration is ready."""
        before = await _row_counts(db)
        forged = ActivityCreate(
            name="Forged Drive video",
            activity_type=ActivityTypeEnum.TYPE_VIDEO,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE,
            chapter_id=chapter.id,
            course_id=course.id,
            org_id=org.id,
            content={"storage": "gdrive", "gdrive_file_id": "forged", "gdrive_folder_id": "forged"},
        )
        gate = _ready() if ready else _not_ready()
        with patch(_RBAC, new_callable=AsyncMock), gate:
            with pytest.raises(HTTPException) as exc:
                await create_activity(mock_request, forged, admin_user, db)
        assert exc.value.status_code == 409
        assert exc.value.detail == _GDRIVE_CREATE_DETAIL
        assert await _row_counts(db) == before

    @pytest.mark.asyncio
    async def test_other_subtypes_still_create(self, mock_request, db, org, course, chapter, admin_user):
        hosted = ActivityCreate(
            name="Hosted via generic endpoint",
            activity_type=ActivityTypeEnum.TYPE_VIDEO,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_HOSTED,
            chapter_id=chapter.id,
            course_id=course.id,
            org_id=org.id,
            content={"filename": "video.mp4"},
        )
        with patch(_RBAC, new_callable=AsyncMock), _not_ready():
            result = await create_activity(mock_request, hosted, admin_user, db)
        assert result.activity_sub_type == ActivitySubTypeEnum.SUBTYPE_VIDEO_HOSTED
