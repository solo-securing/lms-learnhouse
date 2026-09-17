"""Tests for src/services/integrations/gdrive/service.py against a scripted fake Drive."""

import asyncio
import io
import json
import logging
import re
from datetime import datetime

import httpx
import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.services.integrations.gdrive import client as client_mod
from src.services.integrations.gdrive import readiness
from src.services.integrations.gdrive import service
from src.services.integrations.gdrive.client import DriveClient
from src.services.integrations.gdrive.errors import (
    GDrivePermissionError,
    GDriveQuotaExceededError,
    GDriveTransientError,
)

_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
_NAME_RE = re.compile(r"name = '((?:\\.|[^'])*)'")
_PARENT_RE = re.compile(r"'([^']+)' in parents")


class FakeDrive:
    """Just enough of Drive v3 for the service: folders, one-shot uploads, sharing, delete."""

    def __init__(self):
        self.folders: dict[str, tuple[str, str]] = {}  # id -> (name, parent)
        self.files: dict[str, dict] = {}
        self.permissions: dict[str, list] = {}
        self.copy_flag: dict[str, bool] = {}
        self.deleted: list[str] = []
        self.requests: list[httpx.Request] = []
        self.script: list = []  # (predicate, outcome) consumed once each
        self.verify_override = None
        self._n = 0

    # -- scripting -------------------------------------------------------
    def inject(self, method: str, needle: str, outcome, times: int = 1):
        for _ in range(times):
            self.script.append((lambda r, m=method, n=needle: r.method == m and n in str(r.url), outcome))

    def calls(self):
        return [(r.method, r.url.path) for r in self.requests]

    def folder_by_name(self, name):
        for fid, (fname, _parent) in self.folders.items():
            if fname == name:
                return fid
        return None

    # -- handler ---------------------------------------------------------
    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for i, (pred, outcome) in enumerate(self.script):
            if pred(request):
                self.script.pop(i)
                if callable(outcome) and not isinstance(outcome, httpx.Response):
                    outcome = await outcome(request) if asyncio.iscoroutinefunction(outcome) else outcome(request)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        return self._route(request)

    def _new_id(self, prefix):
        self._n += 1
        return f"{prefix}{self._n}"

    def _route(self, request: httpx.Request) -> httpx.Response:
        method, path, url = request.method, request.url.path, str(request.url)
        if path == "/drive/v3/files" and method == "GET":
            q = request.url.params["q"]
            name = _NAME_RE.search(q).group(1).replace("\\'", "'")
            parent = _PARENT_RE.search(q).group(1)
            if parent != "root" and parent not in self.folders:
                return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})
            files = [
                {"id": fid, "name": n, "createdTime": "2026-01-01T00:00:00Z"}
                for fid, (n, p) in self.folders.items()
                if n == name and p == parent
            ]
            return httpx.Response(200, json={"files": files})
        if path == "/drive/v3/files" and method == "POST":
            body = json.loads(request.content)
            fid = self._new_id("folder")
            self.folders[fid] = (body["name"], body["parents"][0])
            return httpx.Response(200, json={"id": fid})
        if path == "/upload/drive/v3/files" and method == "POST":
            body = json.loads(request.content)
            sid = self._new_id("session")
            self.files[sid] = {"pending": body, "mime": request.headers["X-Upload-Content-Type"]}
            return httpx.Response(200, headers={"Location": f"https://upload.test/{sid}"})
        if url.startswith("https://upload.test/") and method == "PUT":
            sid = url.rsplit("/", 1)[1]
            pending = self.files.pop(sid)
            fid = self._new_id("file")
            self.files[fid] = {
                "name": pending["pending"]["name"],
                "parent": pending["pending"]["parents"][0],
                "mimeType": pending["pending"]["mimeType"],
                "size": len(request.content),
            }
            return httpx.Response(200, json={"id": fid})
        m = re.fullmatch(r"/drive/v3/files/([^/]+)/permissions", path)
        if m and method == "POST":
            self.permissions.setdefault(m.group(1), []).append(json.loads(request.content))
            return httpx.Response(200, json={"id": "perm"})
        m = re.fullmatch(r"/drive/v3/files/([^/]+)", path)
        if m and method == "PATCH":
            self.copy_flag[m.group(1)] = json.loads(request.content).get("copyRequiresWriterPermission") is True
            return httpx.Response(200, json={"id": m.group(1), "copyRequiresWriterPermission": self.copy_flag[m.group(1)]})
        if m and method == "GET":
            fid = m.group(1)
            if fid not in self.files:
                return httpx.Response(404)
            info = {
                "id": fid,
                "size": str(self.files[fid]["size"]),
                "trashed": False,
                "copyRequiresWriterPermission": self.copy_flag.get(fid, False),
                "permissions": self.permissions.get(fid, []),
            }
            if self.verify_override:
                info.update(self.verify_override)
            return httpx.Response(200, json=info)
        if m and method == "DELETE":
            fid = m.group(1)
            if fid in self.files:
                del self.files[fid]
            elif fid in self.folders:
                del self.folders[fid]
            else:
                return httpx.Response(404)
            self.deleted.append(fid)
            return httpx.Response(204)
        if path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "op@example.com"}, "storageQuota": {"limit": "1", "usage": "0"}})
        raise AssertionError(f"unrouted {method} {url}")


async def _token():
    return "ya29.secret-token"


@pytest.fixture
def drive(monkeypatch):
    readiness.reset_for_tests()
    fake = FakeDrive()
    monkeypatch.setattr(
        service, "_client", lambda: DriveClient(_token, http=httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    )

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(client_mod, "_sleep", no_sleep)
    monkeypatch.setenv("LEARNHOUSE_GDRIVE_ROOT_FOLDER_NAME", "LearnHouse")
    yield fake
    readiness.reset_for_tests()


def _upload_file(data=_MP4, name="lesson-1.mp4"):
    return UploadFile(file=io.BytesIO(data), filename=name, headers=Headers({"content-type": "video/mp4"}))


async def _upload(**overrides):
    kwargs = dict(
        org_uuid="org_a", course_uuid="course_b", activity_uuid="activity_c",
        file=_upload_file(), mime_type="video/mp4", size=len(_MP4), original_filename="lesson-1.mp4", user_id=7,
    )
    kwargs.update(overrides)
    return await service.upload_activity_video(**kwargs)


# ------------------------------------------------------------------ upload


class TestUploadActivityVideo:
    async def test_happy_path_order_and_layout(self, drive, caplog):
        with caplog.at_level(logging.INFO):
            result = await _upload()
        # Folder chain root → org → course → activity, each looked up before creation.
        assert drive.folders[drive.folder_by_name("LearnHouse")] == ("LearnHouse", "root")
        assert drive.folders[drive.folder_by_name("org_a")][1] == drive.folder_by_name("LearnHouse")
        assert drive.folders[drive.folder_by_name("course_b")][1] == drive.folder_by_name("org_a")
        assert drive.folders[drive.folder_by_name("activity_c")][1] == drive.folder_by_name("course_b")
        assert result.folder_id == drive.folder_by_name("activity_c")
        assert result.stored_filename == "video.mp4" and result.mime_type == "video/mp4" and result.size == len(_MP4)
        assert drive.files[result.file_id]["name"] == "video.mp4"
        assert drive.files[result.file_id]["parent"] == result.folder_id
        assert drive.permissions[result.file_id] == [{"type": "anyone", "role": "reader"}]
        assert drive.copy_flag[result.file_id] is True
        paths = [p for _m, p in drive.calls()]
        assert paths.index("/upload/drive/v3/files") < paths.index(f"/drive/v3/files/{result.file_id}/permissions")
        assert paths[-1] == f"/drive/v3/files/{result.file_id}"  # verification GET last
        assert readiness.state().root_folder_id == drive.folder_by_name("LearnHouse")
        assert "ya29.secret-token" not in caplog.text
        assert result.file_id in caplog.text and "user=7" in caplog.text and "activity_c" in caplog.text

    async def test_root_folder_is_cached_between_uploads(self, drive):
        await _upload()
        n_before = len(drive.requests)
        await _upload(activity_uuid="activity_d")
        lists = [r for r in drive.requests[n_before:] if r.method == "GET" and r.url.path == "/drive/v3/files"]
        # org / course / activity lookups only — no root lookup, no root create.
        assert [_NAME_RE.search(r.url.params["q"]).group(1) for r in lists] == ["org_a", "course_b", "activity_d"]
        assert len([f for f in drive.folders.values() if f[0] == "LearnHouse"]) == 1

    async def test_stale_root_cache_is_rebuilt_once(self, drive):
        readiness.state().root_folder_id = "stale-root"
        result = await _upload()
        assert readiness.state().root_folder_id == drive.folder_by_name("LearnHouse")
        assert drive.folders[result.folder_id][0] == "activity_c"

    @pytest.mark.parametrize(
        "override",
        [{"copyRequiresWriterPermission": False}, {"size": "1"}, {"trashed": True}, {"permissions": []}],
    )
    async def test_verification_failure_cleans_up_file_then_folder(self, drive, override):
        drive.verify_override = override
        with pytest.raises(GDrivePermissionError):
            await _upload()
        assert len(drive.deleted) == 2
        assert drive.deleted[0].startswith("file") and drive.deleted[1].startswith("folder")
        assert drive.folder_by_name("activity_c") is None
        assert not [f for f in drive.files.values() if f.get("name") == "video.mp4"]

    async def test_transient_upload_failure_removes_folder(self, drive):
        drive.inject("PUT", "upload.test", httpx.Response(503), times=6)  # 1 attempt + 5 retries (FR-011)
        with pytest.raises(GDriveTransientError):
            await _upload()
        assert drive.deleted == [drive.deleted[0]] and drive.deleted[0].startswith("folder")
        assert drive.folder_by_name("activity_c") is None

    async def test_drive_error_while_resolving_course_folder_surfaces_unchanged(self, drive, caplog):
        """A failure BEFORE the activity folder exists must raise the original GDriveError (not mask it)."""
        drive.inject("GET", "/drive/v3/files", httpx.Response(503), times=6)  # root lookup: 1 attempt + 5 retries
        with caplog.at_level(logging.WARNING):
            with pytest.raises(GDriveTransientError):
                await _upload()
        assert drive.deleted == []  # nothing was created, so nothing to clean up
        assert drive.folder_by_name("activity_c") is None
        assert "upload failed" in caplog.text and "ya29.secret-token" not in caplog.text

    async def test_quota_exceeded_at_session_start(self, drive):
        drive.inject("POST", "/upload/drive/v3/files", httpx.Response(403, json={"error": {"errors": [{"reason": "storageQuotaExceeded"}]}}))
        with pytest.raises(GDriveQuotaExceededError):
            await _upload()
        assert drive.folder_by_name("activity_c") is None

    async def test_cleanup_failure_keeps_original_error_and_logs_ids(self, drive, caplog):
        drive.verify_override = {"trashed": True}
        # Two best-effort deletes (file, then folder) × 6 attempts each must all fail.
        drive.inject("DELETE", "/drive/v3/files/", httpx.Response(500), times=12)
        with caplog.at_level(logging.WARNING):
            with pytest.raises(GDrivePermissionError):
                await _upload()
        assert "cleanup failed for file file" in caplog.text
        assert "cleanup failed for folder folder" in caplog.text
        assert "ya29.secret-token" not in caplog.text

    async def test_401_invalidates_readiness_and_is_transient(self, drive):
        readiness.state().checked_at = 123.0
        readiness.state().network_ok = True
        drive.inject("PUT", "upload.test", httpx.Response(401))
        with pytest.raises(GDriveTransientError):
            await _upload()
        assert readiness.state().checked_at is None
        assert drive.folder_by_name("activity_c") is None

    async def test_replace_flow_uploads_into_given_folder_and_keeps_it_on_failure(self, drive):
        first = await _upload()
        result = await _upload(folder_id=first.folder_id, original_filename="other.webm", mime_type="video/webm")
        assert result.folder_id == first.folder_id
        assert result.stored_filename == "video.webm"
        assert drive.files[result.file_id]["parent"] == first.folder_id
        drive.verify_override = {"trashed": True}
        with pytest.raises(GDrivePermissionError):
            await _upload(folder_id=first.folder_id)
        assert first.folder_id in drive.folders  # only the new file is removed
        assert all(d.startswith("file") for d in drive.deleted)

    async def test_unexpected_exception_still_cleans_up(self, drive, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("bad verify")

        monkeypatch.setattr(service, "_verify_uploaded_file", boom)
        with pytest.raises(RuntimeError):
            await _upload()
        assert drive.folder_by_name("activity_c") is None


class TestEnsureActivityFolder:
    async def test_reports_whether_the_activity_folder_was_created(self, drive):
        fid, created = await service.ensure_activity_folder("org_a", "course_b", "activity_c")
        assert created is True
        assert fid == drive.folder_by_name("activity_c")
        again, created_again = await service.ensure_activity_folder("org_a", "course_b", "activity_c")
        assert again == fid
        assert created_again is False

    async def test_each_activity_gets_its_own_folder_under_the_same_course(self, drive):
        """A clone resolves to its own <activity_uuid> folder, next to the original's."""
        first, _ = await service.ensure_activity_folder("org_a", "course_b", "activity_c")
        other, other_created = await service.ensure_activity_folder("org_a", "course_b", "activity_clone")
        assert other_created is True
        assert other != first
        assert drive.folders[other][1] == drive.folders[first][1]

    async def test_shared_client_and_401(self, drive):
        async with service._client() as client:
            fid, created = await service.ensure_activity_folder("org_a", "course_b", "activity_c", client=client)
        assert created is True and fid == drive.folder_by_name("activity_c")
        readiness.state().checked_at = 5.0
        drive.inject("GET", "/drive/v3/files", httpx.Response(401))
        with pytest.raises(GDriveTransientError):
            await service.ensure_activity_folder("org_a", "course_b", "activity_d")
        assert readiness.state().checked_at is None


# --------------------------------------------------------------- best effort


class TestBestEffortDeletes:
    async def test_success_and_404_are_quiet(self, drive, caplog):
        result = await _upload()
        with caplog.at_level(logging.INFO):
            assert await service.delete_file_best_effort(result.file_id, context="t") is True
            assert await service.delete_file_best_effort("never-existed", context="t") is True
            assert await service.delete_folder_best_effort(result.folder_id, context="t") is True
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []
        assert result.file_id not in drive.files and result.folder_id not in drive.folders

    async def test_5xx_retries_then_warns_without_raising(self, drive, caplog):
        drive.inject("DELETE", "/drive/v3/files/x1", httpx.Response(502), times=6)
        with caplog.at_level(logging.WARNING):
            assert await service.delete_folder_best_effort("x1", context="activity=a1") is False
        assert "folder x1 was not deleted" in caplog.text and "activity=a1" in caplog.text
        assert "delete it by hand" in caplog.text
        # One deletion attempt with the FR-011 retry policy: 1 + 5 retries = 6 requests.
        assert len([r for r in drive.requests if r.method == "DELETE"]) == 6

    async def test_budget_exhausted_warns(self, drive, caplog, monkeypatch):
        monkeypatch.setattr(service, "BEST_EFFORT_BUDGET_SECONDS", 0.01)

        async def hang(_request):
            await asyncio.sleep(1)
            return httpx.Response(204)

        drive.inject("DELETE", "/drive/v3/files/slow", hang)
        with caplog.at_level(logging.WARNING):
            assert await service.delete_file_best_effort("slow", context="c") is False
        assert "was not deleted within" in caplog.text

    async def test_401_and_unexpected_errors(self, drive, caplog, monkeypatch):
        readiness.state().checked_at = 1.0
        drive.inject("DELETE", "/drive/v3/files/u1", httpx.Response(401))
        with caplog.at_level(logging.WARNING):
            assert await service.delete_file_best_effort("u1", context="c") is False
        assert readiness.state().checked_at is None
        assert "access token rejected" in caplog.text

        def broken_client():
            raise RuntimeError("no client")

        monkeypatch.setattr(service, "_client", broken_client)
        with caplog.at_level(logging.WARNING):
            assert await service.delete_file_best_effort("u2", context="c") is False
        assert "unexpected RuntimeError" in caplog.text


# ------------------------------------------------------------- references


async def _add_drive_activity(db, org, course, *, id, file_id, folder_id, course_id=None):
    a = Activity(
        id=id,
        name=f"a{id}",
        activity_type=ActivityTypeEnum.TYPE_VIDEO,
        activity_sub_type=ActivitySubTypeEnum.SUBTYPE_VIDEO_GDRIVE,
        content={"storage": "gdrive", "gdrive_file_id": file_id, "gdrive_folder_id": folder_id},
        org_id=org.id,
        course_id=course_id or course.id,
        activity_uuid=f"activity_{id}",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(a)
    await db.commit()
    return a


class TestHasOtherReference:
    async def test_zero_one_two_references(self, db, org, course):
        me = await _add_drive_activity(db, org, course, id=10, file_id="f1", folder_id="d1")
        assert await service.has_other_reference(db, me.id, gdrive_file_id="f1") is False
        assert await service.has_other_reference(db, me.id, gdrive_folder_id="d1") is False
        await _add_drive_activity(db, org, course, id=11, file_id="f1", folder_id="d1")
        assert await service.has_other_reference(db, me.id, gdrive_file_id="f1") is True
        assert await service.has_other_reference(db, me.id, gdrive_folder_id="d1") is True
        await _add_drive_activity(db, org, course, id=12, file_id="f1", folder_id="d1")
        assert await service.has_other_reference(db, me.id, gdrive_file_id="f1") is True
        assert await service.has_other_reference(db, me.id, gdrive_file_id="other") is False

    async def test_exclude_course_and_folder_list(self, db, org, course):
        other_course = course.__class__(
            id=99, name="Clone", description="", public=True, published=True, open_to_contributors=False,
            org_id=org.id, course_uuid="course_clone", creation_date="x", update_date="x",
        )
        db.add(other_course)
        await db.commit()
        await _add_drive_activity(db, org, course, id=20, file_id="f2", folder_id="d2")
        # Only same-course references → excluded → no "other" reference.
        assert await service.has_other_reference(db, None, gdrive_folder_ids=["d2"], exclude_course_id=course.id) is False
        await _add_drive_activity(db, org, course, id=21, file_id="f2", folder_id="d2", course_id=99)
        assert await service.has_other_reference(db, None, gdrive_folder_ids=["zzz", "d2"], exclude_course_id=course.id) is True
        assert await service.has_other_reference(db, None) is False


class TestFindCourseFolder:
    async def test_three_level_lookup_never_creates(self, drive):
        await service.ensure_activity_folder("org_a", "course_b", "activity_c")
        creates_before = len([r for r in drive.requests if r.method == "POST"])
        found = await service.find_course_folder("org_a", "course_b")
        assert found == drive.folder_by_name("course_b")
        assert len([r for r in drive.requests if r.method == "POST"]) == creates_before
        lists = [r for r in drive.requests[-3:]]
        assert [r.method for r in lists] == ["GET"] * 3

    async def test_none_when_any_level_is_missing(self, drive):
        assert await service.find_course_folder("org_a", "course_b") is None  # no root at all
        await service.ensure_activity_folder("org_a", "course_b", "activity_c")
        assert await service.find_course_folder("org_zzz", "course_b") is None
        assert await service.find_course_folder("org_a", "course_zzz") is None

    async def test_errors_yield_none(self, drive, caplog):
        readiness.state().checked_at = 3.0
        drive.inject("GET", "/drive/v3/files", httpx.Response(401))
        with caplog.at_level(logging.WARNING):
            assert await service.find_course_folder("org_a", "course_b") is None
        assert readiness.state().checked_at is None
        drive.inject("GET", "/drive/v3/files", httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}]}}))
        with caplog.at_level(logging.WARNING):
            assert await service.find_course_folder("org_a", "course_b") is None
        assert "Could not locate the Drive course folder" in caplog.text
