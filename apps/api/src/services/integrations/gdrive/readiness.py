"""Readiness of the Google Drive integration, cached per process.

``ready`` means: the flag is on, the client-secrets file parses, the token
file has a ``refresh_token`` and an access token can actually be used. The
local checks (flag, files) run on every call — they are cheap and let a token
freshly written by ``cli.py gdrive-authorize`` take effect immediately (the
cache is keyed on the token file's ``mtime_ns``). The network check (refresh
the access token when it is about to expire) is cached for
:data:`NETWORK_TTL_SECONDS` so opening the create-video dialog never hammers
Google.

Every write to a Drive video activity goes through :func:`require_ready`
first (FR-004); uploads obtain their bearer via :func:`get_access_token`,
which serialises refreshes behind one lock so concurrent uploads share a
single refresh.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from config.config import GDriveConfig, get_learnhouse_config

from .credentials import (
    ClientSecrets,
    TokenData,
    is_expired,
    load_client_secrets,
    load_token,
    refresh_access_token,
    save_token_atomic,
    token_mtime_ns,
)
from .errors import (
    GDriveConfigError,
    GDriveError,
    GDriveNeedsAuthorizationError,
    GDriveNotEnabledError,
    GDriveTransientError,
)

logger = logging.getLogger(__name__)

NETWORK_TTL_SECONDS = 300
TOKEN_SKEW_SECONDS = 300

REAUTH_HINT = (
    "run `cd apps/api && uv run python cli.py gdrive-authorize` to re-authorize; "
    "make sure the OAuth consent screen is published (\"In production\"), otherwise "
    "Google expires the refresh token after 7 days"
)

_NEVER_LOGGED = object()


@dataclass
class GDriveStatus:
    enabled: bool
    ready: bool
    reason: str | None  # see data-model.md §4 for the closed set of values


class _State:
    """Per-process cache. Recreated by :func:`reset_for_tests`."""

    def __init__(self) -> None:
        self.checked_at: float | None = None
        self.network_ok: bool | None = None
        self.network_reason: str | None = None
        self.token_mtime_ns: int | None = None
        self.token: TokenData | None = None
        self.root_folder_id: str | None = None
        self.lock = asyncio.Lock()
        self.logged_reason: Any = _NEVER_LOGGED


_state = _State()


def reset_for_tests() -> None:
    global _state
    _state = _State()


def invalidate() -> None:
    """Drop the cached network verdict (e.g. after Drive answered 401)."""
    _state.checked_at = None
    _state.network_ok = None
    _state.network_reason = None


def state() -> _State:
    """The live cache object (used by the upload service for ``root_folder_id``)."""
    return _state


def _config() -> GDriveConfig:
    return get_learnhouse_config().gdrive_config


def _http_client() -> httpx.AsyncClient:  # patched in tests
    return httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=30.0))


def _load_token_if_changed(cfg: GDriveConfig) -> TokenData | None:
    mtime = token_mtime_ns(cfg.token_path)
    if mtime is None:
        _state.token = None
        _state.token_mtime_ns = None
        return None
    if mtime != _state.token_mtime_ns or _state.token is None:
        _state.token = load_token(cfg.token_path)
        _state.token_mtime_ns = mtime
        # A new token file (re-authorization) must be re-verified right away.
        invalidate()
    return _state.token


def _local_check(cfg: GDriveConfig) -> tuple[GDriveStatus | None, ClientSecrets | None, TokenData | None]:
    """Flag + files. Returns a terminal status, or ``(None, secrets, token)`` when the network step is needed."""
    if not cfg.enabled:
        return GDriveStatus(enabled=False, ready=False, reason="disabled"), None, None
    try:
        secrets = load_client_secrets(cfg.credentials_path)
    except GDriveConfigError as exc:
        return GDriveStatus(enabled=True, ready=False, reason=exc.reason or "credentials_invalid"), None, None
    token = _load_token_if_changed(cfg)
    if token is None or not token.refresh_token:
        return GDriveStatus(enabled=True, ready=False, reason="token_missing"), None, None
    return None, secrets, token


async def _refresh_and_persist(cfg: GDriveConfig, secrets: ClientSecrets, token: TokenData) -> TokenData:
    async with _http_client() as http:
        fresh = await refresh_access_token(secrets, token, http)
    if cfg.token_path:
        try:
            save_token_atomic(cfg.token_path, fresh)
            _state.token_mtime_ns = token_mtime_ns(cfg.token_path)
        except OSError:
            # Keep serving from memory; the next process start will refresh again.
            logger.warning("Google Drive: refreshed access token could not be written to the token file")
    _state.token = fresh
    return fresh


async def _network_check(cfg: GDriveConfig, secrets: ClientSecrets, token: TokenData) -> None:
    """Refresh the access token when needed and record the verdict in the cache."""
    try:
        if is_expired(token, TOKEN_SKEW_SECONDS):
            await _refresh_and_persist(cfg, secrets, token)
        _state.network_ok = True
        _state.network_reason = None
    except GDriveNeedsAuthorizationError:
        _state.network_ok = False
        _state.network_reason = "needs_reauthorization"
    except GDriveTransientError:
        _state.network_ok = False
        _state.network_reason = "network_error"
    except GDriveConfigError:
        _state.network_ok = False
        _state.network_reason = "credentials_invalid"
    _state.checked_at = time.monotonic()


def _cached_verdict(force: bool) -> GDriveStatus | None:
    if force or _state.checked_at is None or _state.network_ok is None:
        return None
    if time.monotonic() - _state.checked_at >= NETWORK_TTL_SECONDS:
        return None
    return GDriveStatus(enabled=True, ready=bool(_state.network_ok), reason=None if _state.network_ok else _state.network_reason)


def _log_transition(status: GDriveStatus) -> None:
    """Log once per distinct ``reason`` so a flapping check does not spam."""
    if _state.logged_reason is not _NEVER_LOGGED and _state.logged_reason == status.reason:
        return
    _state.logged_reason = status.reason
    if status.ready:
        logger.info("Google Drive integration is ready")
    elif status.reason == "disabled":
        logger.info("Google Drive integration is disabled (LEARNHOUSE_GDRIVE_ENABLED=false)")
    elif status.reason == "needs_reauthorization":
        logger.warning("Google Drive integration needs re-authorization: %s", REAUTH_HINT)
    elif status.reason == "token_missing":
        logger.warning(
            "Google Drive integration is enabled but no usable token was found at LEARNHOUSE_GDRIVE_TOKEN_PATH; "
            "run `cd apps/api && uv run python cli.py gdrive-authorize`"
        )
    else:
        logger.warning("Google Drive integration is enabled but not ready (reason=%s)", status.reason)


async def get_status(force: bool = False) -> GDriveStatus:
    """Current readiness. ``force=True`` bypasses the network-check cache."""
    cfg = _config()
    terminal, secrets, token = _local_check(cfg)
    if terminal is not None:
        _log_transition(terminal)
        return terminal
    assert secrets is not None and token is not None

    cached = _cached_verdict(force)
    if cached is None:
        async with _state.lock:
            cached = _cached_verdict(force)
            if cached is None:
                await _network_check(cfg, secrets, _state.token or token)
                cached = _cached_verdict(False)
                assert cached is not None
    _log_transition(cached)
    return cached


def _exception_for(status: GDriveStatus) -> GDriveError:
    if status.reason == "needs_reauthorization":
        return GDriveNeedsAuthorizationError(REAUTH_HINT, reason=status.reason)
    if status.reason == "credentials_invalid":
        return GDriveConfigError("Google Drive credentials JSON is invalid", reason=status.reason)
    return GDriveNotEnabledError(f"Google Drive integration is not ready ({status.reason})", reason=status.reason)


async def require_ready() -> None:
    """Raise the matching :class:`GDriveError` unless the integration is ready (FR-004)."""
    status = await get_status()
    if not status.ready:
        raise _exception_for(status)


async def get_access_token() -> str:
    """A usable bearer token, refreshing under the shared lock when needed."""
    cfg = _config()
    terminal, secrets, token = _local_check(cfg)
    if terminal is not None:
        raise _exception_for(terminal)
    assert secrets is not None and token is not None
    async with _state.lock:
        current = _state.token or token
        if is_expired(current, TOKEN_SKEW_SECONDS):
            current = await _refresh_and_persist(cfg, secrets, current)
            _state.network_ok = True
            _state.network_reason = None
            _state.checked_at = time.monotonic()
        assert current.token  # a non-expired token always carries one
        return current.token


async def log_startup_status() -> None:
    """Startup hook: one log line about readiness; never raises, no network when disabled."""
    try:
        cfg = _config()
        if not cfg.enabled:
            _log_transition(GDriveStatus(enabled=False, ready=False, reason="disabled"))
            return
        await get_status(force=True)
    except Exception as exc:  # pragma: no cover - defensive, must not block boot
        logger.warning("Google Drive readiness check failed at startup: %s", type(exc).__name__)


def format_status_report(
    status: GDriveStatus,
    about: dict[str, Any] | None,
    credentials_path: str | None,
    token_path: str | None,
) -> tuple[list[str], int]:
    """Lines for ``cli.py gdrive-status`` and its exit code (0 ready / 1 otherwise)."""
    lines = [
        f"enabled: {'true' if status.enabled else 'false'}",
        f"ready: {'true' if status.ready else 'false'}",
        f"reason: {status.reason or '-'}",
    ]
    if status.ready and about:
        email = about.get("emailAddress") or "-"
        quota = about.get("storageQuota") or {}
        usage = quota.get("usage") if isinstance(quota, dict) else None
        limit = quota.get("limit") if isinstance(quota, dict) else None
        lines.append(f"account: {email}")
        lines.append(f"quota: {usage if usage is not None else '-'}/{limit if limit is not None else '-'}")
    lines.append(f"credentials_path: {credentials_path or '-'}")
    lines.append(f"token_path: {token_path or '-'}")
    if status.reason == "needs_reauthorization":
        lines.append(f"hint: {REAUTH_HINT}")
    elif status.reason == "token_missing":
        lines.append("hint: run `uv run python cli.py gdrive-authorize` to create the token file")
    elif status.reason in ("credentials_missing", "credentials_invalid"):
        lines.append("hint: check LEARNHOUSE_GDRIVE_CREDENTIALS_PATH points at the OAuth client JSON (web or installed)")
    elif status.reason == "disabled":
        lines.append("hint: set LEARNHOUSE_GDRIVE_ENABLED=true to enable the integration")
    return lines, (0 if status.ready else 1)
