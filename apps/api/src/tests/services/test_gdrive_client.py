"""Tests for src/services/integrations/gdrive/client.py (httpx.MockTransport)."""

import io
import json
import logging
import math

import httpx
import pytest

from src.services.integrations.gdrive import client as client_mod
from src.services.integrations.gdrive.client import (
    API_BASE,
    UPLOAD_BASE,
    DriveClient,
    _next_offset_from_range,
)
from src.services.integrations.gdrive.errors import (
    GDriveNotFoundError,
    GDrivePermissionError,
    GDriveQuotaExceededError,
    GDriveTransientError,
    GDriveUnauthorizedError,
)

SESSION_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=abc"


class _Drive:
    """Scripted Drive: pops one outcome per request and records every request."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        outcome = self.outcomes.pop(0)
        if callable(outcome) and not isinstance(outcome, httpx.Response):
            outcome = outcome(request)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def calls(self):
        return [(r.method, str(r.url).split("?")[0]) for r in self.requests]


def _json(status, payload=None, headers=None):
    return httpx.Response(status, json=payload if payload is not None else {}, headers=headers or {})


def _err403(reason):
    return _json(403, {"error": {"code": 403, "errors": [{"reason": reason}], "message": "hidden"}})


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(client_mod, "_sleep", fake_sleep)
    return slept


async def _token():
    return "ya29.secret-token"


def _client(drive):
    return DriveClient(_token, http=httpx.AsyncClient(transport=httpx.MockTransport(drive)))


# ---------------------------------------------------------------- folders


class TestFolders:
    async def test_find_folder_found_and_query_shape(self, no_sleep):
        drive = _Drive([_json(200, {"files": [{"id": "f1", "name": "x", "createdTime": "2026-01-01T00:00:00Z"}]})])
        async with _client(drive) as c:
            assert await c.find_folder("it's", "root") == "f1"
        req = drive.requests[0]
        assert req.method == "GET"
        assert str(req.url).startswith(f"{API_BASE}/files?")
        q = req.url.params["q"]
        assert "name = 'it\\'s'" in q and "'root' in parents" in q
        assert "mimeType = 'application/vnd.google-apps.folder'" in q and "trashed = false" in q
        assert req.headers["Authorization"] == "Bearer ya29.secret-token"

    async def test_find_folder_none_without_create(self, no_sleep):
        drive = _Drive([_json(200, {"files": []})])
        async with _client(drive) as c:
            assert await c.find_folder("nope", "root") is None
        assert drive.calls() == [("GET", f"{API_BASE}/files")]

    async def test_find_folder_picks_earliest_created(self, no_sleep):
        drive = _Drive([
            _json(200, {"files": [
                {"id": "late", "createdTime": "2026-05-01T00:00:00Z"},
                {"id": "early", "createdTime": "2026-01-01T00:00:00Z"},
                {"id": "mid", "createdTime": "2026-03-01T00:00:00Z"},
            ]})
        ])
        async with _client(drive) as c:
            assert await c.find_folder("dup", "root") == "early"

    async def test_ensure_folder_reuses_or_creates(self, no_sleep):
        drive = _Drive([
            _json(200, {"files": [{"id": "existing"}]}),
            _json(200, {"files": []}),
            _json(200, {"id": "created"}),
        ])
        async with _client(drive) as c:
            assert await c.ensure_folder("a", "root") == "existing"
            assert await c.ensure_folder("b", "root") == "created"
        create = drive.requests[2]
        assert create.method == "POST"
        body = json.loads(create.content)
        assert body == {"name": "b", "mimeType": "application/vnd.google-apps.folder", "parents": ["root"]}

    async def test_create_folder_without_id_is_transient(self, no_sleep):
        drive = _Drive([_json(200, {"files": []}), _json(200, {})])
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError):
                await c.ensure_folder("b", "root")


# ----------------------------------------------------------------- upload


def _session_ok(request):
    assert request.method == "POST"
    assert str(request.url).startswith(f"{UPLOAD_BASE}/files")
    assert request.url.params["uploadType"] == "resumable"
    assert request.headers["X-Upload-Content-Type"] == "video/mp4"
    return httpx.Response(200, headers={"Location": SESSION_URL})


class TestResumableUpload:
    async def test_single_chunk(self, no_sleep):
        data = b"a" * 100
        drive = _Drive([_session_ok, _json(200, {"id": "file1"})])
        async with _client(drive) as c:
            file_id = await c.resumable_upload(io.BytesIO(data), len(data), "video.mp4", "video/mp4", "folder")
        assert file_id == "file1"
        start, put = drive.requests
        assert start.headers["X-Upload-Content-Length"] == "100"
        assert json.loads(start.content) == {"name": "video.mp4", "parents": ["folder"], "mimeType": "video/mp4"}
        assert put.method == "PUT" and str(put.url) == SESSION_URL
        assert put.headers["Content-Range"] == "bytes 0-99/100"
        assert put.content == data
        assert "Authorization" not in put.headers  # session URL is pre-authorised

    async def test_session_start_retry_keeps_upload_headers(self, no_sleep):
        """A retried session POST must still declare Content-Type and X-Upload-Content-*."""
        data = b"a" * 100
        drive = _Drive([httpx.Response(503), httpx.Response(200, headers={"Location": SESSION_URL}), _json(200, {"id": "f"})])
        async with _client(drive) as c:
            assert await c.resumable_upload(io.BytesIO(data), len(data), "video.mp4", "video/mp4", "folder") == "f"
        assert len(no_sleep) == 1
        first, retried = drive.requests[0], drive.requests[1]
        for req in (first, retried):
            assert req.headers["X-Upload-Content-Type"] == "video/mp4"
            assert req.headers["X-Upload-Content-Length"] == "100"
            assert req.headers["Content-Type"].startswith("application/json")
            assert req.headers["Authorization"] == "Bearer ya29.secret-token"

    async def test_multi_chunk_with_308(self, no_sleep, monkeypatch):
        monkeypatch.setattr(client_mod, "CHUNK_SIZE", 4)
        data = b"0123456789"
        drive = _Drive([
            _session_ok,
            httpx.Response(308, headers={"Range": "bytes=0-3"}),
            httpx.Response(308, headers={"Range": "bytes=0-7"}),
            _json(201, {"id": "file2"}),
        ])
        async with _client(drive) as c:
            assert await c.resumable_upload(io.BytesIO(data), 10, "video.mp4", "video/mp4", "folder") == "file2"
        puts = drive.requests[1:]
        assert [p.headers["Content-Range"] for p in puts] == ["bytes 0-3/10", "bytes 4-7/10", "bytes 8-9/10"]
        assert [p.content for p in puts] == [b"0123", b"4567", b"89"]

    async def test_308_short_range_seeks_back(self, no_sleep, monkeypatch):
        monkeypatch.setattr(client_mod, "CHUNK_SIZE", 4)
        drive = _Drive([
            _session_ok,
            httpx.Response(308, headers={"Range": "bytes=0-1"}),  # Drive only kept 2 bytes
            httpx.Response(308, headers={"Range": "bytes=0-5"}),
            _json(200, {"id": "file3"}),
        ])
        async with _client(drive) as c:
            assert await c.resumable_upload(io.BytesIO(b"0123456789"), 10, "v.mp4", "video/mp4", "f") == "file3"
        puts = drive.requests[1:]
        assert [p.headers["Content-Range"] for p in puts] == ["bytes 0-3/10", "bytes 2-5/10", "bytes 6-9/10"]

    async def test_308_without_range_restarts_from_zero(self, no_sleep, monkeypatch):
        monkeypatch.setattr(client_mod, "CHUNK_SIZE", 4)
        drive = _Drive([_session_ok, httpx.Response(308), _json(200, {"id": "f"})])
        async with _client(drive) as c:
            await c.resumable_upload(io.BytesIO(b"0123"), 4, "v.mp4", "video/mp4", "f")
        assert [p.headers["Content-Range"] for p in drive.requests[1:]] == ["bytes 0-3/4", "bytes 0-3/4"]

    async def test_5xx_then_status_query_then_success(self, no_sleep, monkeypatch):
        monkeypatch.setattr(client_mod, "CHUNK_SIZE", 4)
        drive = _Drive([
            _session_ok,
            httpx.Response(503),
            httpx.Response(308, headers={"Range": "bytes=0-3"}),  # status query answer
            _json(200, {"id": "f"}),
        ])
        async with _client(drive) as c:
            assert await c.resumable_upload(io.BytesIO(b"01234567"), 8, "v.mp4", "video/mp4", "f") == "f"
        assert len(no_sleep) == 1
        query = drive.requests[2]
        assert query.headers["Content-Range"] == "bytes */8" and query.content == b""
        assert drive.requests[3].headers["Content-Range"] == "bytes 4-7/8"

    async def test_transport_error_then_resume(self, no_sleep):
        drive = _Drive([
            _session_ok,
            httpx.ReadTimeout("slow"),
            httpx.Response(308, headers={"Range": "bytes=0-49"}),
            _json(200, {"id": "f"}),
        ])
        async with _client(drive) as c:
            assert await c.resumable_upload(io.BytesIO(b"x" * 100), 100, "v.mp4", "video/mp4", "f") == "f"
        assert drive.requests[3].headers["Content-Range"] == "bytes 50-99/100"

    async def test_five_retries_with_1_to_16s_backoff_then_success(self, no_sleep):
        # FR-011: up to five retries, waiting 1, 2, 4, 8 and 16 s (+ jitter) between them.
        drive = _Drive([_session_ok] + [httpx.Response(500), httpx.Response(308)] * 5 + [_json(200, {"id": "f"})])
        async with _client(drive) as c:
            assert await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f") == "f"
        assert [math.floor(s) for s in no_sleep] == [1, 2, 4, 8, 16]

    async def test_sixth_consecutive_failure_is_transient(self, no_sleep):
        drive = _Drive([_session_ok] + [httpx.Response(500), httpx.Response(308)] * 5 + [httpx.Response(500)])
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError) as exc:
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")
        assert "after 6 attempts" in str(exc.value)
        assert len(no_sleep) == 5

    async def test_lost_session_404_410(self, no_sleep):
        for code in (404, 410):
            drive = _Drive([_session_ok, httpx.Response(code)])
            async with _client(drive) as c:
                with pytest.raises(GDriveTransientError):
                    await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")

    async def test_lost_session_during_status_query(self, no_sleep):
        drive = _Drive([_session_ok, httpx.Response(502), httpx.Response(404)])
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")

    async def test_status_query_reports_complete(self, no_sleep):
        drive = _Drive([_session_ok, httpx.Response(502), _json(200, {"id": "done-already"})])
        async with _client(drive) as c:
            assert await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f") == "done-already"

    async def test_308_reporting_all_bytes_finalizes(self, no_sleep):
        drive = _Drive([_session_ok, httpx.Response(308, headers={"Range": "bytes=0-9"}), _json(200, {"id": "fin"})])
        async with _client(drive) as c:
            assert await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f") == "fin"
        assert drive.requests[2].headers["Content-Range"] == "bytes */10"

    async def test_finalize_failure_paths(self, no_sleep):
        drive = _Drive([_session_ok, httpx.Response(308, headers={"Range": "bytes=0-9"}), httpx.Response(500)])
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")
        drive = _Drive([_session_ok, httpx.Response(308, headers={"Range": "bytes=0-9"}), httpx.ConnectError("x")])
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")

    async def test_quota_and_permission_403_on_chunk(self, no_sleep):
        drive = _Drive([_session_ok, _err403("storageQuotaExceeded")])
        async with _client(drive) as c:
            with pytest.raises(GDriveQuotaExceededError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")
        drive = _Drive([_session_ok, _err403("insufficientFilePermissions")])
        async with _client(drive) as c:
            with pytest.raises(GDrivePermissionError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")

    async def test_rate_limit_403_and_429_retry_on_chunk(self, no_sleep, caplog):
        drive = _Drive([
            _session_ok,
            _err403("userRateLimitExceeded"),
            httpx.Response(308),
            httpx.Response(429),
            httpx.Response(308),
            _json(200, {"id": "f"}),
        ])
        with caplog.at_level(logging.INFO):
            async with _client(drive) as c:
                assert await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f") == "f"
        assert len(no_sleep) == 2
        # Spec Edge Cases: Google's own reason for a rate limit is logged for the operator.
        assert "userRateLimitExceeded" in caplog.text

    async def test_401_on_chunk_and_on_status_query(self, no_sleep):
        drive = _Drive([_session_ok, httpx.Response(401)])
        async with _client(drive) as c:
            with pytest.raises(GDriveUnauthorizedError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")
        drive = _Drive([_session_ok, httpx.Response(500), httpx.Response(401)])
        async with _client(drive) as c:
            with pytest.raises(GDriveUnauthorizedError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")

    async def test_other_4xx_on_chunk_is_permission(self, no_sleep):
        drive = _Drive([_session_ok, _json(400, {"error": {"errors": [{"reason": "badRequest"}]}})])
        async with _client(drive) as c:
            with pytest.raises(GDrivePermissionError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")

    async def test_status_query_unexpected_4xx_is_transient(self, no_sleep):
        drive = _Drive([_session_ok, httpx.Response(500), httpx.Response(418)])
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")

    async def test_transport_error_during_status_query_is_retried(self, no_sleep):
        # FR-011: a blip while asking Drive "how far did we get?" is one more retry,
        # it must not abort the whole upload.
        drive = _Drive([
            _session_ok,
            httpx.Response(500),  # chunk 0-9 fails
            httpx.ConnectError("x"),  # status query fails → back off, ask again
            httpx.Response(308, headers={"Range": "bytes=0-4"}),  # Drive kept 5 bytes
            _json(200, {"id": "f"}),  # chunk 5-9
        ])
        async with _client(drive) as c:
            assert await c.resumable_upload(io.BytesIO(b"0123456789"), 10, "v.mp4", "video/mp4", "f") == "f"
        assert [math.floor(s) for s in no_sleep] == [1, 2]
        assert drive.requests[-1].headers["Content-Range"] == "bytes 5-9/10"

    async def test_5xx_during_status_query_is_retried(self, no_sleep):
        drive = _Drive([_session_ok, httpx.Response(500), httpx.Response(503), httpx.Response(308), _json(200, {"id": "f"})])
        async with _client(drive) as c:
            assert await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f") == "f"
        assert len(no_sleep) == 2
        assert [q.headers["Content-Range"] for q in drive.requests[2:4]] == ["bytes */10", "bytes */10"]
        assert drive.requests[-1].headers["Content-Range"] == "bytes 0-9/10"

    async def test_status_query_failures_count_toward_retry_budget(self, no_sleep):
        drive = _Drive([_session_ok, httpx.Response(500)] + [httpx.ConnectError("x")] * 5)
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError) as exc:
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")
        assert "after 6 attempts" in str(exc.value)
        assert len(no_sleep) == 5
        assert len(drive.requests) == 7  # session + 1 chunk + 5 status queries

    async def test_transport_failures_exhausted(self, no_sleep):
        drive = _Drive([_session_ok] + [httpx.ConnectError("x"), httpx.Response(308)] * 5 + [httpx.ConnectError("x")])
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")
        assert len(no_sleep) == 5

    async def test_session_without_location_and_empty_file(self, no_sleep):
        drive = _Drive([httpx.Response(200)])
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError):
                await c.resumable_upload(io.BytesIO(b"x" * 10), 10, "v.mp4", "video/mp4", "f")
        async with _client(_Drive([])) as c:
            with pytest.raises(GDrivePermissionError):
                await c.resumable_upload(io.BytesIO(b""), 0, "v.mp4", "video/mp4", "f")

    async def test_short_read_and_missing_id(self, no_sleep):
        drive = _Drive([_session_ok])
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError):
                await c.resumable_upload(io.BytesIO(b"x" * 5), 10, "v.mp4", "video/mp4", "f")
        drive = _Drive([_session_ok, httpx.Response(200, text="not json")])
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError):
                await c.resumable_upload(io.BytesIO(b"x" * 5), 5, "v.mp4", "video/mp4", "f")

    def test_next_offset_from_range(self):
        assert _next_offset_from_range(None) == 0
        assert _next_offset_from_range("bytes=0-1048575") == 1048576
        assert _next_offset_from_range("0-9") == 10
        assert _next_offset_from_range("garbage") == 0


# ---------------------------------------------------- metadata + errors


class TestMetadataAndErrors:
    async def test_share_and_copy_flag(self, no_sleep):
        drive = _Drive([_json(200, {"id": "perm"}), _json(200, {"id": "f", "copyRequiresWriterPermission": True})])
        async with _client(drive) as c:
            await c.share_anyone_reader("f")
            await c.set_copy_requires_writer_permission("f")
        share, patch = drive.requests
        assert share.method == "POST" and str(share.url).startswith(f"{API_BASE}/files/f/permissions")
        assert json.loads(share.content) == {"type": "anyone", "role": "reader"}
        assert patch.method == "PATCH" and json.loads(patch.content) == {"copyRequiresWriterPermission": True}

    async def test_copy_flag_not_applied(self, no_sleep):
        drive = _Drive([_json(200, {"id": "f", "copyRequiresWriterPermission": False})])
        async with _client(drive) as c:
            with pytest.raises(GDrivePermissionError):
                await c.set_copy_requires_writer_permission("f")

    async def test_get_file_and_about(self, no_sleep):
        drive = _Drive([
            _json(200, {"id": "f", "size": "10", "trashed": False}),
            _json(200, {"user": {"emailAddress": "op@example.com"}, "storageQuota": {"limit": "100", "usage": "5"}}),
            _json(200, {}),
        ])
        async with _client(drive) as c:
            assert (await c.get_file("f"))["size"] == "10"
            about = await c.about()
            assert about == {"emailAddress": "op@example.com", "storageQuota": {"limit": "100", "usage": "5"}}
            assert await c.about() == {"emailAddress": None, "storageQuota": {}}
        assert drive.requests[0].url.params["fields"].startswith("id,size,trashed")

    async def test_delete_file_404_idempotent(self, no_sleep):
        drive = _Drive([httpx.Response(204), httpx.Response(404)])
        async with _client(drive) as c:
            await c.delete_file("a")
            await c.delete_file("gone")
        assert [r.method for r in drive.requests] == ["DELETE", "DELETE"]

    async def test_get_file_404_raises_not_found(self, no_sleep):
        async with _client(_Drive([httpx.Response(404)])) as c:
            with pytest.raises(GDriveNotFoundError):
                await c.get_file("gone")

    async def test_403_quota_and_permission(self, no_sleep):
        async with _client(_Drive([_err403("storageQuotaExceeded")])) as c:
            with pytest.raises(GDriveQuotaExceededError):
                await c.get_file("f")
        async with _client(_Drive([_err403("forbidden")])) as c:
            with pytest.raises(GDrivePermissionError):
                await c.get_file("f")
        # details[] shape and status-only shape are parsed too
        async with _client(_Drive([_json(403, {"error": {"details": [{"reason": "storageQuotaExceeded"}]}})])) as c:
            with pytest.raises(GDriveQuotaExceededError):
                await c.get_file("f")
        async with _client(_Drive([_json(403, {"error": {"status": "PERMISSION_DENIED"}})])) as c:
            with pytest.raises(GDrivePermissionError):
                await c.get_file("f")
        async with _client(_Drive([httpx.Response(403, text="<html>")])) as c:
            with pytest.raises(GDrivePermissionError):
                await c.get_file("f")

    async def test_403_rate_limit_retries_then_transient(self, no_sleep, caplog):
        drive = _Drive([_err403("rateLimitExceeded")] * 6)
        async with _client(drive) as c:
            with pytest.raises(GDriveTransientError) as exc:
                await c.get_file("f")
        assert "rateLimitExceeded" in str(exc.value)
        # Five retries (1, 2, 4, 8, 16 s + jitter) → six requests in total (FR-011).
        assert [math.floor(s) for s in no_sleep] == [1, 2, 4, 8, 16]
        assert len(drive.requests) == 6

    async def test_429_and_5xx_and_transport_retry_then_succeed(self, no_sleep):
        drive = _Drive([httpx.Response(429), httpx.Response(502), httpx.ConnectError("x"), _json(200, {"id": "f"})])
        async with _client(drive) as c:
            assert (await c.get_file("f"))["id"] == "f"
        assert len(no_sleep) == 3
        assert all(1 <= s <= 17 for s in no_sleep)

    async def test_401_and_other_4xx(self, no_sleep):
        async with _client(_Drive([httpx.Response(401)])) as c:
            with pytest.raises(GDriveUnauthorizedError):
                await c.get_file("f")
        async with _client(_Drive([_json(400, {"error": {"errors": [{"reason": "badRequest"}]}})])) as c:
            with pytest.raises(GDrivePermissionError):
                await c.get_file("f")

    async def test_non_json_success_body_is_transient(self, no_sleep):
        async with _client(_Drive([httpx.Response(200, text="<html>")])) as c:
            with pytest.raises(GDriveTransientError):
                await c.get_file("f")

    async def test_default_timeout_values_and_applied_to_owned_client(self):
        assert client_mod.DEFAULT_TIMEOUT.connect == 30.0
        assert client_mod.DEFAULT_TIMEOUT.read == 120.0
        assert client_mod.DEFAULT_TIMEOUT.write == 300.0
        assert client_mod.DEFAULT_TIMEOUT.pool == 30.0
        c = DriveClient(_token)
        try:
            assert c._http.timeout == client_mod.DEFAULT_TIMEOUT
        finally:
            await c.aclose()

    async def test_owns_http_client_when_not_injected(self):
        c = DriveClient(_token)
        assert c._owns_http is True
        await c.aclose()

    async def test_no_token_in_logs(self, no_sleep, caplog):
        drive = _Drive([httpx.Response(500), _json(200, {"id": "f"})])
        with caplog.at_level(logging.INFO):
            async with _client(drive) as c:
                await c.get_file("f")
        assert "ya29.secret-token" not in caplog.text
