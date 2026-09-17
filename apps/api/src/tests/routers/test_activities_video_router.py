"""Router tests for the video endpoints in src/routers/courses/activities/activities.py
(``storage`` form field + Google Drive readiness gate)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.events.database import get_db_session
from src.db.courses.activities import ActivityRead, ActivitySubTypeEnum, ActivityTypeEnum
from src.routers.courses.activities.activities import router as activities_router
from src.security.auth import get_current_user
from src.services.integrations.gdrive.errors import (
    GDriveConfigError,
    GDriveNeedsAuthorizationError,
    GDriveNotEnabledError,
)

_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
_CREATE = "src.routers.courses.activities.activities.create_video_activity"
_READY = "src.routers.courses.activities.activities.require_ready"


@pytest.fixture
def app(db, admin_user):
    app = FastAPI()
    app.include_router(activities_router, prefix="/api/v1/activities")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: admin_user
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _drive_activity_read(**overrides) -> ActivityRead:
    data = dict(
        id=2,
        org_id=1,
        course_id=1,
        name="Drive Video",
        activity_type=ActivityTypeEnum.TYPE_VIDEO,
        activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE,
        content={
            "activity_uuid": "activity_gdrive",
            "storage": "gdrive",
            "gdrive_file_id": "file_abc123",
            "gdrive_folder_id": "folder_abc123",
            "original_filename": "lesson-1.mp4",
            "mime_type": "video/mp4",
            "size": len(_MP4),
        },
        details={},
        published=False,
        activity_uuid="activity_gdrive",
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    data.update(overrides)
    return ActivityRead(**data)


def _form(storage=None):
    data = {"name": "Lesson", "chapter_id": "1"}
    if storage is not None:
        data["storage"] = storage
    return data


_FILES = {"video_file": ("lesson-1.mp4", _MP4, "video/mp4")}


class TestCreateVideoStorageField:
    async def test_bogus_storage_is_422_and_service_not_called(self, client):
        with patch(_CREATE, new_callable=AsyncMock) as create, patch(_READY, new_callable=AsyncMock) as ready:
            response = await client.post("/api/v1/activities/video", data=_form("bogus"), files=_FILES)
        assert response.status_code == 422
        create.assert_not_awaited()
        ready.assert_not_awaited()

    @pytest.mark.parametrize(
        "exc,detail",
        [
            (GDriveNotEnabledError, "Video : Google Drive storage is not enabled on this instance"),
            (GDriveNeedsAuthorizationError, "Video : Google Drive needs re-authorization by the operator"),
            (GDriveConfigError, "Video : Google Drive is not configured correctly"),
        ],
    )
    async def test_gdrive_not_ready_is_409_before_upload(self, client, exc, detail):
        with patch(_CREATE, new_callable=AsyncMock) as create, patch(
            _READY, new_callable=AsyncMock, side_effect=exc("nope")
        ):
            response = await client.post("/api/v1/activities/video", data=_form("gdrive"), files=_FILES)
        assert response.status_code == 409
        assert response.json()["detail"] == detail
        create.assert_not_awaited()

    async def test_default_storage_is_server_and_readiness_not_consulted(self, client):
        with patch(_CREATE, new_callable=AsyncMock, return_value=_drive_activity_read(
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_HOSTED, content={"filename": "v.mp4"}
        )) as create, patch(_READY, new_callable=AsyncMock) as ready:
            response = await client.post("/api/v1/activities/video", data=_form(), files=_FILES)
        assert response.status_code == 200
        ready.assert_not_awaited()
        assert create.await_args.kwargs["storage"] == "server"

    async def test_gdrive_when_ready_passes_storage_and_returns_drive_shape(self, client):
        with patch(_CREATE, new_callable=AsyncMock, return_value=_drive_activity_read()) as create, patch(
            _READY, new_callable=AsyncMock
        ) as ready:
            response = await client.post("/api/v1/activities/video", data=_form("GDrive "), files=_FILES)
        assert response.status_code == 200
        ready.assert_awaited_once()
        assert create.await_args.kwargs["storage"] == "gdrive"
        body = response.json()
        assert body["activity_sub_type"] == "SUBTYPE_VIDEO_GDRIVE"
        assert body["details"] == {}
        assert set(body["content"]) == {
            "activity_uuid", "storage", "gdrive_file_id", "gdrive_folder_id", "original_filename", "mime_type", "size",
        }
        for forbidden in ("uri", "filename", "webViewLink"):
            assert forbidden not in body["content"]


# ---------------------------------------------------------------------------
# Through the real service: validator status codes + PUT for Drive activities
# ---------------------------------------------------------------------------

_V = "src.services.courses.activities.video"


class TestCreateVideoThroughService:
    async def test_413_when_over_five_gib(self, client, org, course, chapter):
        with patch(_READY, new_callable=AsyncMock), patch(f"{_V}.check_resource_access", new_callable=AsyncMock), patch(
            f"{_V}.upload_activity_video", new_callable=AsyncMock
        ) as upload, patch("src.security.file_validation._stream_length", return_value=5 * 1024 * 1024 * 1024 + 1):
            response = await client.post("/api/v1/activities/video", data=_form("gdrive"), files=_FILES)
        assert response.status_code == 413
        upload.assert_not_awaited()

    async def test_415_when_magic_bytes_wrong(self, client, org, course, chapter):
        bad = {"video_file": ("lesson.mp4", b"this is not an mp4 at all" * 4, "video/mp4")}
        with patch(_READY, new_callable=AsyncMock), patch(f"{_V}.check_resource_access", new_callable=AsyncMock), patch(
            f"{_V}.upload_activity_video", new_callable=AsyncMock
        ) as upload:
            response = await client.post("/api/v1/activities/video", data=_form("gdrive"), files=bad)
        assert response.status_code == 415
        upload.assert_not_awaited()

    async def test_409_wrong_format_before_drive(self, client, org, course, chapter):
        mkv = {"video_file": ("movie.mkv", _MP4, "video/x-matroska")}
        with patch(_READY, new_callable=AsyncMock), patch(f"{_V}.check_resource_access", new_callable=AsyncMock), patch(
            f"{_V}.upload_activity_video", new_callable=AsyncMock
        ) as upload:
            response = await client.post("/api/v1/activities/video", data=_form("gdrive"), files=mkv)
        assert response.status_code == 409
        assert response.json()["detail"] == "Video : Wrong video format"
        upload.assert_not_awaited()


class TestUpdateDriveVideoThroughService:
    async def test_put_rename_rejected_409_when_not_ready(self, client, org, course, chapter, gdrive_activity, db):
        with patch(f"{_V}.check_resource_access", new_callable=AsyncMock), patch(
            f"{_V}.require_ready", new_callable=AsyncMock, side_effect=GDriveNeedsAuthorizationError("x")
        ):
            response = await client.put(f"/api/v1/activities/video/{gdrive_activity.activity_uuid}", data={"name": "Renamed"})
        assert response.status_code == 409
        assert response.json()["detail"] == "Video : Google Drive needs re-authorization by the operator"
        await db.refresh(gdrive_activity)
        assert gdrive_activity.name == "Drive Video"

    async def test_put_rename_when_ready_ignores_playback_fields(self, client, org, course, chapter, gdrive_activity):
        with patch(f"{_V}.check_resource_access", new_callable=AsyncMock), patch(
            f"{_V}.require_ready", new_callable=AsyncMock
        ), patch(f"{_V}.upload_activity_video", new_callable=AsyncMock) as upload:
            response = await client.put(
                f"/api/v1/activities/video/{gdrive_activity.activity_uuid}",
                data={"name": "Renamed", "autoplay": "true", "start_time": "12"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Renamed" and body["details"] == {}
        assert body["activity_sub_type"] == "SUBTYPE_VIDEO_GDRIVE"
        upload.assert_not_awaited()
