"""Thin async client for the Google Drive REST v3 API (``httpx``).

Only the handful of calls the integration needs, with the error translation
and retry policy in one place:

* folders: :meth:`DriveClient.find_folder` / :meth:`DriveClient.ensure_folder`
* upload:  :meth:`DriveClient.resumable_upload` (8 MiB chunks, resume on 308,
  backoff + status query on 5xx/429/transport errors)
* sharing: :meth:`DriveClient.share_anyone_reader`,
  :meth:`DriveClient.set_copy_requires_writer_permission`
* misc:    :meth:`DriveClient.get_file`, :meth:`DriveClient.delete_file`,
  :meth:`DriveClient.about`

Google's own SDK is deliberately not used: ``httpx`` is already a dependency,
is async, streams chunks without blocking the event loop and is trivially
mocked with ``httpx.MockTransport``.

No access token is ever logged; only Drive ``reason`` codes and ids are.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, BinaryIO, Callable

import httpx

from .errors import (
    GDriveNotFoundError,
    GDrivePermissionError,
    GDriveQuotaExceededError,
    GDriveTransientError,
    GDriveUnauthorizedError,
)

logger = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB — a multiple of 256 KiB as Drive requires
# FR-011: up to five automatic retries with 1, 2, 4, 8, 16 s (+ jitter) between them.
_MAX_RETRIES = 5
_MAX_ATTEMPTS = _MAX_RETRIES + 1
_BACKOFF_SECONDS = (1, 2, 4, 8, 16)
_RETRYABLE_403_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded"})
_QUOTA_403_REASON = "storageQuotaExceeded"

DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=300.0, pool=30.0)

AccessTokenProvider = Callable[[], Awaitable[str]]


async def _sleep(seconds: float) -> None:  # patched in tests
    await asyncio.sleep(seconds)


def _backoff(attempt: int) -> float:
    base = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
    return base + random.uniform(0, 1)


class _Retry(Exception):
    """Internal: the request failed in a way that warrants another attempt."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


def _error_reason(response: httpx.Response) -> str:
    """Best-effort ``reason`` code from a Drive error body (never the message)."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if not isinstance(error, dict):
        return ""
    for key in ("errors", "details"):
        entries = error.get(key)
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("reason"):
                    return str(entry["reason"])
    status = error.get("status")
    return str(status) if status else ""


def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveClient:
    """Async Drive v3 client. Use as ``async with`` when it owns its ``httpx`` client."""

    def __init__(
        self,
        access_token_provider: AccessTokenProvider,
        http: httpx.AsyncClient | None = None,
    ):
        self._token_provider = access_token_provider
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "DriveClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ core

    async def _auth_headers(self) -> dict[str, str]:
        token = await self._token_provider()
        return {"Authorization": f"Bearer {token}"}

    def _classify(self, response: httpx.Response, *, what: str) -> None:
        """Raise the right ``GDriveError`` for a non-2xx response, or ``_Retry``."""
        status = response.status_code
        if status < 400:
            return
        if status == 401:
            raise GDriveUnauthorizedError(f"Drive rejected the access token during {what}")
        if status == 404:
            raise GDriveNotFoundError(f"Drive returned 404 during {what}")
        if status == 429:
            raise _Retry(f"429 during {what}")
        if status == 403:
            reason = _error_reason(response)
            if reason == _QUOTA_403_REASON:
                raise GDriveQuotaExceededError(f"Drive storage quota exceeded during {what}")
            if reason in _RETRYABLE_403_REASONS:
                raise _Retry(f"403 {reason} during {what}")
            logger.warning("Google Drive refused %s with 403 (reason=%s)", what, reason or "unknown")
            raise GDrivePermissionError(f"Drive returned 403 ({reason or 'unknown'}) during {what}")
        if status >= 500:
            raise _Retry(f"{status} during {what}")
        reason = _error_reason(response)
        logger.warning("Google Drive returned %s for %s (reason=%s)", status, what, reason or "unknown")
        raise GDrivePermissionError(f"Drive returned {status} ({reason or 'unknown'}) during {what}")

    async def _request(self, method: str, url: str, *, what: str, **kwargs: Any) -> httpx.Response:
        """Send with auth + retry (5xx / 429 / retryable 403 / transport errors)."""
        last_why = ""
        # Popped ONCE: a retried attempt must resend the caller's headers
        # (e.g. X-Upload-Content-* on the resumable session POST).
        caller_headers = dict(kwargs.pop("headers", {}) or {})
        for attempt in range(_MAX_ATTEMPTS):
            headers = dict(caller_headers)
            headers.update(await self._auth_headers())
            try:
                response = await self._http.request(method, url, headers=headers, **kwargs)
                self._classify(response, what=what)
                return response
            except httpx.TransportError as exc:
                last_why = f"{type(exc).__name__} during {what}"
            except _Retry as retry:
                last_why = retry.why
            if attempt + 1 < _MAX_ATTEMPTS:
                delay = _backoff(attempt)
                logger.info("Google Drive %s: retrying in %.1fs (%s)", what, delay, last_why)
                await _sleep(delay)
        raise GDriveTransientError(f"Drive request failed after {_MAX_ATTEMPTS} attempts: {last_why}")

    async def _request_json(self, method: str, url: str, *, what: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._request(method, url, what=what, **kwargs)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            body = response.json()
        except ValueError:
            raise GDriveTransientError(f"Drive returned a non-JSON body during {what}") from None
        return body if isinstance(body, dict) else {}

    # --------------------------------------------------------------- folders

    async def find_folder(self, name: str, parent_id: str) -> str | None:
        """Id of the non-trashed folder ``name`` under ``parent_id``, or ``None``.

        Never creates. When several match (a create race), the earliest
        ``createdTime`` wins so every worker converges on the same folder.
        """
        query = (
            f"name = '{_escape_query_value(name)}' and '{_escape_query_value(parent_id)}' in parents "
            f"and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        body = await self._request_json(
            "GET",
            f"{API_BASE}/files",
            what="files.list",
            params={"q": query, "fields": "files(id,name,createdTime)", "pageSize": 20, "spaces": "drive"},
        )
        files = [f for f in body.get("files", []) if isinstance(f, dict) and f.get("id")]
        if not files:
            return None
        files.sort(key=lambda f: str(f.get("createdTime") or ""))
        return str(files[0]["id"])

    async def create_folder(self, name: str, parent_id: str) -> str:
        body = await self._request_json(
            "POST",
            f"{API_BASE}/files",
            what="files.create(folder)",
            params={"fields": "id"},
            json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        )
        folder_id = body.get("id")
        if not folder_id:
            raise GDriveTransientError("Drive did not return an id for the created folder")
        return str(folder_id)

    async def ensure_folder(self, name: str, parent_id: str) -> str:
        """Find-or-create ``name`` under ``parent_id`` (idempotent)."""
        found = await self.find_folder(name, parent_id)
        if found:
            return found
        return await self.create_folder(name, parent_id)

    # ---------------------------------------------------------------- upload

    async def resumable_upload(
        self,
        fileobj: BinaryIO,
        size: int,
        name: str,
        mime_type: str,
        parent_id: str,
    ) -> str:
        """Upload ``fileobj`` (``size`` bytes) as ``name`` into ``parent_id``; returns the file id.

        Resumable protocol: one session-initiating POST, then 8 MiB ``PUT``
        chunks with ``Content-Range``. ``308`` carries the bytes Drive has
        (``Range`` header) and we seek there. Transient failures back off and
        query the session (``Content-Range: bytes */size``) before resuming;
        five retries exhausted (six consecutive failures), or a lost session
        (404/410), raise :class:`GDriveTransientError`.
        """
        if size <= 0:
            raise GDrivePermissionError("Refusing to upload an empty file to Google Drive")

        session_url = await self._start_session(name, mime_type, size, parent_id)

        offset = 0
        failures = 0
        while True:
            fileobj.seek(offset)
            want = min(CHUNK_SIZE, size - offset)
            chunk = fileobj.read(want)
            if len(chunk) != want:
                raise GDriveTransientError("Upload stream ended before the declared size")
            end = offset + len(chunk) - 1
            try:
                response = await self._http.put(
                    session_url,
                    content=chunk,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                    },
                )
            except httpx.TransportError as exc:
                failures += 1
                offset, failures = await self._recover_offset(session_url, size, failures, type(exc).__name__)
                if offset >= size:
                    return await self._finish_from_status(session_url, size)
                continue

            status = response.status_code
            if status == 308:
                failures = 0
                offset = _next_offset_from_range(response.headers.get("Range"))
                if offset >= size:
                    # Everything is there but Drive did not finalize — ask it to.
                    return await self._finish_from_status(session_url, size)
                continue
            if status in (200, 201):
                return _file_id_from_body(response)
            if status in (404, 410):
                raise GDriveTransientError("Google Drive upload session was lost")
            if status == 401:
                raise GDriveUnauthorizedError("Drive rejected the access token during upload")
            reason = _error_reason(response)
            if status == 403:
                if reason == _QUOTA_403_REASON:
                    raise GDriveQuotaExceededError("Drive storage quota exceeded during upload")
                if reason not in _RETRYABLE_403_REASONS:
                    logger.warning("Google Drive refused the upload with 403 (reason=%s)", reason or "unknown")
                    raise GDrivePermissionError(f"Drive returned 403 ({reason or 'unknown'}) during upload")
            elif status != 429 and status < 500:
                logger.warning("Google Drive returned %s during upload (reason=%s)", status, reason or "unknown")
                raise GDrivePermissionError(f"Drive returned {status} during upload")

            # 5xx / 429 / retryable 403 → back off, then ask where we are.
            failures += 1
            why = f"HTTP {status}" + (f" ({reason})" if reason else "")
            offset, failures = await self._recover_offset(session_url, size, failures, why)
            if offset >= size:
                return await self._finish_from_status(session_url, size)

    async def _recover_offset(self, session_url: str, size: int, failures: int, why: str) -> tuple[int, int]:
        """After a failed chunk: back off, then ask Drive how many bytes it has.

        A transient failure of the status query itself is simply one more
        retry against the same FR-011 budget — it never aborts the upload on
        its own. Returns ``(next_offset, failures)``; raises
        :class:`GDriveTransientError` once the budget is spent.
        """
        while True:
            if failures >= _MAX_ATTEMPTS:
                raise GDriveTransientError(f"Upload failed after {failures} attempts ({why})")
            logger.info("Google Drive upload: %s; retrying (attempt %d of %d)", why, failures, _MAX_RETRIES)
            await _sleep(_backoff(failures - 1))
            try:
                return await self._query_session(session_url, size), failures
            except _Retry as retry:
                failures += 1
                why = retry.why

    async def _start_session(self, name: str, mime_type: str, size: int, parent_id: str) -> str:
        response = await self._request(
            "POST",
            f"{UPLOAD_BASE}/files",
            what="files.create(resumable)",
            params={"uploadType": "resumable"},
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(size),
            },
            json={"name": name, "parents": [parent_id], "mimeType": mime_type},
        )
        location = response.headers.get("Location")
        if not location:
            raise GDriveTransientError("Drive did not return an upload session URL")
        return location

    async def _query_session(self, session_url: str, size: int) -> int:
        """Ask Drive how many bytes it has; returns the next offset to send."""
        try:
            response = await self._http.put(
                session_url,
                content=b"",
                headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"},
            )
        except httpx.TransportError as exc:
            raise _Retry(f"{type(exc).__name__} while querying the upload session") from None
        if response.status_code == 308:
            return _next_offset_from_range(response.headers.get("Range"))
        if response.status_code in (200, 201):
            # Already complete; report "all bytes" so the caller finalizes.
            self._completed_body = response  # type: ignore[attr-defined]
            return size
        if response.status_code in (404, 410):
            raise GDriveTransientError("Google Drive upload session was lost")
        if response.status_code == 401:
            raise GDriveUnauthorizedError("Drive rejected the access token while querying the upload session")
        if response.status_code == 429 or response.status_code >= 500:
            raise _Retry(f"HTTP {response.status_code} while querying the upload session")
        raise GDriveTransientError(f"Unexpected HTTP {response.status_code} while querying the upload session")

    async def _finish_from_status(self, session_url: str, size: int) -> str:
        """All bytes are at Drive; obtain the file id (from a completed status reply)."""
        completed = getattr(self, "_completed_body", None)
        if completed is not None:
            self._completed_body = None  # type: ignore[attr-defined]
            return _file_id_from_body(completed)
        try:
            response = await self._http.put(
                session_url,
                content=b"",
                headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"},
            )
        except httpx.TransportError as exc:
            raise GDriveTransientError(f"Could not finalize the upload session ({type(exc).__name__})") from None
        if response.status_code in (200, 201):
            return _file_id_from_body(response)
        raise GDriveTransientError(f"Upload session did not finalize (HTTP {response.status_code})")

    # --------------------------------------------------------------- sharing

    async def share_anyone_reader(self, file_id: str) -> None:
        await self._request_json(
            "POST",
            f"{API_BASE}/files/{file_id}/permissions",
            what="permissions.create",
            params={"fields": "id"},
            json={"type": "anyone", "role": "reader"},
        )

    async def set_copy_requires_writer_permission(self, file_id: str) -> None:
        body = await self._request_json(
            "PATCH",
            f"{API_BASE}/files/{file_id}",
            what="files.update",
            params={"fields": "id,copyRequiresWriterPermission"},
            json={"copyRequiresWriterPermission": True},
        )
        if body.get("copyRequiresWriterPermission") is not True:
            raise GDrivePermissionError("Drive did not apply copyRequiresWriterPermission")

    # ------------------------------------------------------------------ misc

    async def get_file(
        self,
        file_id: str,
        fields: str = "id,size,trashed,copyRequiresWriterPermission,permissions(type,role)",
    ) -> dict[str, Any]:
        return await self._request_json(
            "GET", f"{API_BASE}/files/{file_id}", what="files.get", params={"fields": fields}
        )

    async def delete_file(self, file_id: str) -> None:
        """Permanently delete a file or folder (with its children). 404 counts as done."""
        try:
            await self._request("DELETE", f"{API_BASE}/files/{file_id}", what="files.delete")
        except GDriveNotFoundError:
            return

    async def about(self) -> dict[str, Any]:
        body = await self._request_json(
            "GET", f"{API_BASE}/about", what="about.get", params={"fields": "user(emailAddress),storageQuota(limit,usage)"}
        )
        user = body.get("user") if isinstance(body.get("user"), dict) else {}
        quota = body.get("storageQuota") if isinstance(body.get("storageQuota"), dict) else {}
        return {"emailAddress": user.get("emailAddress"), "storageQuota": quota}


def _next_offset_from_range(range_header: str | None) -> int:
    """``Range: bytes=0-1048575`` → 1048576; absent/malformed → 0."""
    if not range_header:
        return 0
    value = range_header.strip()
    if value.startswith("bytes="):
        value = value[len("bytes="):]
    try:
        _start, end = value.split("-", 1)
        return int(end) + 1
    except ValueError:
        return 0


def _file_id_from_body(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    file_id = body.get("id") if isinstance(body, dict) else None
    if not file_id:
        raise GDriveTransientError("Drive did not return a file id after upload")
    return str(file_id)
