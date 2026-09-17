"""Error hierarchy for the Google Drive integration and its HTTP mapping.

Every message is fixed text: it never interpolates tokens, secrets, file paths
or raw Google responses (FR-015). Diagnostic detail belongs in the log line
that accompanies the raise, never in the exception message the API returns.
"""

from fastapi import HTTPException


class GDriveError(Exception):
    """Base class. ``status_code`` / ``detail`` drive :func:`to_http_exception`."""

    status_code: int = 502
    detail: str = "Video : Google Drive is unreachable, please try again"

    def __init__(self, message: str | None = None, *, reason: str | None = None):
        # ``message`` is for logs / CLI output only; ``detail`` is what the API
        # returns and stays fixed per class.
        super().__init__(message or self.detail)
        self.reason = reason


class GDriveNotEnabledError(GDriveError):
    """Flag off, or credential/token missing → operator must enable/authorize."""

    status_code = 409
    detail = "Video : Google Drive storage is not enabled on this instance"


class GDriveNeedsAuthorizationError(GDriveError):
    """Refresh token rejected (``invalid_grant``) → run ``cli.py gdrive-authorize``."""

    status_code = 409
    detail = "Video : Google Drive needs re-authorization by the operator"


class GDriveConfigError(GDriveError):
    """Credential JSON unreadable/invalid, or the consent flow could not complete."""

    status_code = 409
    detail = "Video : Google Drive is not configured correctly"


class GDriveQuotaExceededError(GDriveError):
    """Drive returned 403 ``storageQuotaExceeded``."""

    status_code = 507
    detail = "Video : Google Drive is out of storage space"


class GDrivePermissionError(GDriveError):
    """Sharing/verification failed or Drive refused with a non-quota 403."""

    status_code = 502
    detail = "Video : Could not set sharing on the Google Drive file"


class GDriveTransientError(GDriveError):
    """Network error, 5xx/429 after retries, or a lost upload session."""

    status_code = 502
    detail = "Video : Google Drive is unreachable, please try again"


class GDriveUnauthorizedError(GDriveTransientError):
    """Drive answered 401 mid-operation: the cached access token is stale.

    Callers invalidate the readiness cache so the next request refreshes; the
    HTTP mapping stays the transient 502.
    """


class GDriveNotFoundError(GDrivePermissionError):
    """Drive answered 404 for a file/folder id the app created itself."""


def to_http_exception(err: GDriveError) -> HTTPException:
    """Translate a :class:`GDriveError` into the fixed ``"Video : ..."`` HTTP reply."""
    return HTTPException(status_code=err.status_code, detail=err.detail)
