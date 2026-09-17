"""One-time OAuth consent for the Google Drive integration (``cli.py gdrive-authorize``).

Runs Google's loopback consent flow with ``google-auth-oauthlib`` — the only
new dependency of the integration, imported lazily so the API process never
loads it — and writes the resulting token (``Credentials.to_json()`` shape)
atomically with mode 0600. Works whether or not the integration flag is on,
so an operator can prepare the token before enabling the feature.

Nothing here prints or logs ``client_secret``, ``refresh_token`` or the
access token.
"""

from __future__ import annotations

import json
import logging

from .client import DriveClient
from .credentials import DRIVE_FILE_SCOPE, load_client_secrets, load_token, save_token_atomic
from .errors import GDriveConfigError, GDriveError

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765


def _redirect_hint(port: int) -> str:
    return (
        f"register http://localhost:{port}/ under 'Authorized redirect URIs' of the OAuth client "
        "(type Web application) in Google Cloud Console, enable the Google Drive API, and publish the "
        "OAuth consent screen (\"In production\") so the refresh token does not expire after 7 days"
    )


def run_authorize(
    *,
    credentials_path: str | None,
    token_path: str | None,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> str:
    """Run the consent flow, persist the token, return the authorized account's email.

    Raises :class:`GDriveConfigError` (with actionable guidance) when the
    client-secrets file is missing/invalid, the paths are unset, the user
    cancels or Google rejects the flow, or the token cannot be written.
    """
    if not token_path:
        raise GDriveConfigError(
            "LEARNHOUSE_GDRIVE_TOKEN_PATH is not set: choose where the token file should be written "
            "(outside the repository and the Docker image)",
            reason="token_missing",
        )
    try:
        secrets = load_client_secrets(credentials_path)  # validates the file up front
    except GDriveConfigError as exc:
        # Actionable for the operator, without echoing anything from the file itself.
        raise GDriveConfigError(
            f"{exc}; check that LEARNHOUSE_GDRIVE_CREDENTIALS_PATH points to the OAuth client JSON "
            f"downloaded from Google Cloud Console, and {_redirect_hint(port)}",
            reason=exc.reason,
        ) from None

    # Lazy: google-auth-oauthlib is a CLI-only dependency.
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]

    try:
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes=[DRIVE_FILE_SCOPE])
    except Exception as exc:  # noqa: BLE001 - library raises several ValueError/KeyError flavours
        raise GDriveConfigError(
            f"Could not load the OAuth client file ({type(exc).__name__}); check that "
            "LEARNHOUSE_GDRIVE_CREDENTIALS_PATH points to the OAuth client JSON downloaded from "
            f"Google Cloud Console, and {_redirect_hint(port)}",
            reason="credentials_invalid",
        ) from None

    try:
        creds = flow.run_local_server(
            port=port,
            open_browser=open_browser,
            access_type="offline",
            prompt="consent",
        )
    except (KeyboardInterrupt, SystemExit):
        raise GDriveConfigError("Authorization cancelled before Google returned a token", reason="token_missing") from None
    except Exception as exc:  # noqa: BLE001 - OAuth/HTTP errors of many shapes; message must stay generic
        raise GDriveConfigError(
            f"Google did not complete the authorization ({type(exc).__name__}); {_redirect_hint(port)}",
            reason="token_missing",
        ) from None

    payload = json.loads(creds.to_json())
    if not payload.get("refresh_token"):
        raise GDriveConfigError(
            "Google returned no refresh_token; revoke the app at https://myaccount.google.com/permissions "
            "and run gdrive-authorize again",
            reason="token_missing",
        )
    # Google omits client_id/secret from to_json for some flows; the refresher needs them.
    payload.setdefault("client_id", secrets.client_id)
    payload.setdefault("client_secret", secrets.client_secret)
    payload.setdefault("token_uri", secrets.token_uri)
    try:
        save_token_atomic(token_path, payload)
    except OSError as exc:
        raise GDriveConfigError(
            f"Could not write the token file ({type(exc).__name__}); check LEARNHOUSE_GDRIVE_TOKEN_PATH is writable",
            reason="token_missing",
        ) from None

    logger.info("Google Drive token written to %s", token_path)
    return _fetch_account_email(token_path)


def _fetch_account_email(token_path: str) -> str:
    """Confirm the token works by asking Drive who we are."""
    import asyncio

    token = load_token(token_path)
    access_token = token.token if token else None
    if not access_token:
        raise GDriveConfigError("The saved token has no access token; run gdrive-authorize again", reason="token_missing")

    async def _about() -> str:
        async def provider() -> str:
            return access_token

        async with DriveClient(provider) as client:
            about = await client.about()
        return str(about.get("emailAddress") or "unknown")

    try:
        return asyncio.run(_about())
    except GDriveError as exc:
        raise GDriveConfigError(
            f"Token saved, but Google Drive could not be reached to confirm the account ({type(exc).__name__})",
            reason="network_error",
        ) from None
