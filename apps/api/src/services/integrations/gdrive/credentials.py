"""OAuth client secrets and user-token files for the Google Drive integration.

Two files on disk, both referenced by path from ``GDriveConfig``:

* the **client secrets** JSON downloaded from Google Cloud Console (root key
  ``web`` or ``installed``) — read-only;
* the **token** JSON written by ``cli.py gdrive-authorize`` in the shape of
  ``google.oauth2.credentials.Credentials.to_json()`` — read and rewritten
  atomically (mode 0600) whenever the access token is refreshed.

Nothing in this module logs a token, secret or the contents of either file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .errors import (
    GDriveConfigError,
    GDriveNeedsAuthorizationError,
    GDriveTransientError,
)

logger = logging.getLogger(__name__)

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
_REQUIRED_SECRET_KEYS = ("client_id", "client_secret", "token_uri")


@dataclass(frozen=True)
class ClientSecrets:
    client_id: str
    client_secret: str
    token_uri: str


@dataclass
class TokenData:
    """Parsed token file. ``expiry`` is timezone-aware UTC (or ``None``)."""

    token: str | None
    refresh_token: str | None
    token_uri: str
    client_id: str
    client_secret: str
    scopes: list[str] = field(default_factory=list)
    expiry: datetime | None = None

    def to_json_dict(self) -> dict[str, Any]:
        """Serialise in the ``Credentials.to_json()`` shape (``expiry`` as ISO 8601 ``Z``)."""
        return {
            "token": self.token,
            "refresh_token": self.refresh_token,
            "token_uri": self.token_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scopes": list(self.scopes),
            "expiry": _format_expiry(self.expiry),
        }


def _format_expiry(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def _parse_expiry(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_client_secrets(path: str | None) -> ClientSecrets:
    """Read the OAuth client JSON.

    Raises :class:`GDriveConfigError` with ``reason="credentials_missing"`` when
    the path is unset/unreadable and ``reason="credentials_invalid"`` when the
    JSON lacks a ``web``/``installed`` block or a required key.
    """
    if not path:
        raise GDriveConfigError("Google Drive credentials path is not configured", reason="credentials_missing")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        # OSError → missing/unreadable; ValueError → not JSON at all.
        reason = "credentials_missing" if isinstance(exc, OSError) else "credentials_invalid"
        raise GDriveConfigError(f"Google Drive credentials file could not be read ({reason})", reason=reason) from None

    block = None
    if isinstance(raw, dict):
        block = raw.get("web") or raw.get("installed")
    if not isinstance(block, dict) or any(not block.get(k) for k in _REQUIRED_SECRET_KEYS):
        raise GDriveConfigError(
            "Google Drive credentials JSON must contain a 'web' or 'installed' block "
            "with client_id, client_secret and token_uri",
            reason="credentials_invalid",
        )
    return ClientSecrets(
        client_id=str(block["client_id"]),
        client_secret=str(block["client_secret"]),
        token_uri=str(block["token_uri"]),
    )


def load_token(path: str | None) -> TokenData | None:
    """Read the token file; ``None`` when unset, missing or not valid JSON."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    scopes = raw.get("scopes")
    return TokenData(
        token=raw.get("token") or None,
        refresh_token=raw.get("refresh_token") or None,
        token_uri=str(raw.get("token_uri") or _DEFAULT_TOKEN_URI),
        client_id=str(raw.get("client_id") or ""),
        client_secret=str(raw.get("client_secret") or ""),
        scopes=[str(s) for s in scopes] if isinstance(scopes, list) else [],
        expiry=_parse_expiry(raw.get("expiry")),
    )


def token_mtime_ns(path: str | None) -> int | None:
    """``st_mtime_ns`` of the token file, or ``None`` when it cannot be stat'ed."""
    if not path:
        return None
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def save_token_atomic(path: str, data: dict[str, Any] | TokenData) -> None:
    """Write the token JSON atomically with mode 0600.

    Temp file in the same directory → ``fchmod(0o600)`` → ``fsync`` →
    ``os.replace``; a failure mid-way leaves the previous file untouched and no
    temp file behind.
    """
    payload = data.to_json_dict() if isinstance(data, TokenData) else data
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".gdrive_token.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def is_expired(token: TokenData, skew_seconds: int = 300) -> bool:
    """True when there is no access token or it expires within ``skew_seconds``."""
    if not token.token:
        return True
    if token.expiry is None:
        return True
    return token.expiry <= datetime.now(timezone.utc) + timedelta(seconds=skew_seconds)


async def refresh_access_token(
    secrets: ClientSecrets, token: TokenData, http: httpx.AsyncClient
) -> TokenData:
    """Exchange the refresh token for a new access token (RFC 6749 §6).

    ``invalid_grant`` → :class:`GDriveNeedsAuthorizationError` (token revoked or
    expired — operator must re-run ``gdrive-authorize``). Transport errors and
    5xx → :class:`GDriveTransientError`. Any other 4xx is treated as a config
    problem. Returns a *new* :class:`TokenData`; the caller persists it.
    """
    if not token.refresh_token:
        raise GDriveNeedsAuthorizationError("Google Drive token has no refresh_token", reason="token_missing")
    form = {
        "grant_type": "refresh_token",
        "refresh_token": token.refresh_token,
        "client_id": secrets.client_id,
        "client_secret": secrets.client_secret,
    }
    try:
        response = await http.post(token.token_uri or secrets.token_uri, data=form)
    except httpx.TransportError as exc:
        raise GDriveTransientError(f"Could not reach Google token endpoint: {type(exc).__name__}", reason="network_error") from None

    if response.status_code >= 500:
        raise GDriveTransientError(f"Google token endpoint returned {response.status_code}", reason="network_error")

    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    if response.status_code != 200:
        error_code = str(body.get("error") or "")
        if error_code == "invalid_grant":
            raise GDriveNeedsAuthorizationError("Google rejected the refresh token (invalid_grant)", reason="needs_reauthorization")
        # Do not echo Google's error_description: it can quote request fields.
        raise GDriveConfigError(f"Google token endpoint returned {response.status_code} ({error_code or 'unknown error'})", reason="credentials_invalid")

    access_token = body.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GDriveTransientError("Google token endpoint returned no access_token", reason="network_error")
    try:
        expires_in = int(body.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600
    granted_scopes = body.get("scope")
    scopes = str(granted_scopes).split() if isinstance(granted_scopes, str) and granted_scopes else list(token.scopes)

    return TokenData(
        token=access_token,
        refresh_token=token.refresh_token,
        token_uri=token.token_uri or secrets.token_uri,
        client_id=secrets.client_id,
        client_secret=secrets.client_secret,
        scopes=scopes,
        expiry=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    )
