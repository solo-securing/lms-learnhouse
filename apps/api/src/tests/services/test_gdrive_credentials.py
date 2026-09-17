"""Tests for src/services/integrations/gdrive/credentials.py."""

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.services.integrations.gdrive import credentials as creds
from src.services.integrations.gdrive.errors import (
    GDriveConfigError,
    GDriveNeedsAuthorizationError,
    GDriveTransientError,
)

_SECRET_BLOCK = {
    "client_id": "cid.apps.googleusercontent.com",
    "client_secret": "GOCSPX-super-secret",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _token(**overrides) -> creds.TokenData:
    base = dict(
        token="ya29.access",
        refresh_token="1//refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id=_SECRET_BLOCK["client_id"],
        client_secret=_SECRET_BLOCK["client_secret"],
        scopes=[creds.DRIVE_FILE_SCOPE],
        expiry=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    base.update(overrides)
    return creds.TokenData(**base)


class TestLoadClientSecrets:
    def test_missing_path_and_missing_file(self, tmp_path):
        with pytest.raises(GDriveConfigError) as exc:
            creds.load_client_secrets(None)
        assert exc.value.reason == "credentials_missing"
        with pytest.raises(GDriveConfigError) as exc:
            creds.load_client_secrets(str(tmp_path / "nope.json"))
        assert exc.value.reason == "credentials_missing"

    def test_invalid_json_and_missing_keys(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(GDriveConfigError) as exc:
            creds.load_client_secrets(str(bad))
        assert exc.value.reason == "credentials_invalid"

        for payload in ({}, {"web": {"client_id": "x"}}, {"installed": {"client_id": "x", "client_secret": ""}}, ["list"]):
            with pytest.raises(GDriveConfigError) as exc:
                creds.load_client_secrets(_write(tmp_path / "c.json", payload))
            assert exc.value.reason == "credentials_invalid"

    @pytest.mark.parametrize("root", ["web", "installed"])
    def test_parses_web_and_installed(self, tmp_path, root):
        path = _write(tmp_path / "c.json", {root: _SECRET_BLOCK})
        secrets = creds.load_client_secrets(path)
        assert secrets.client_id == _SECRET_BLOCK["client_id"]
        assert secrets.client_secret == _SECRET_BLOCK["client_secret"]
        assert secrets.token_uri == _SECRET_BLOCK["token_uri"]

    def test_error_message_never_contains_secret(self, tmp_path):
        path = _write(tmp_path / "c.json", {"web": {"client_id": "x", "client_secret": "LEAK", "token_uri": ""}})
        with pytest.raises(GDriveConfigError) as exc:
            creds.load_client_secrets(path)
        assert "LEAK" not in str(exc.value)


class TestLoadToken:
    def test_none_when_unset_missing_or_garbage(self, tmp_path):
        assert creds.load_token(None) is None
        assert creds.load_token(str(tmp_path / "nope.json")) is None
        bad = tmp_path / "bad.json"
        bad.write_text("nope", encoding="utf-8")
        assert creds.load_token(str(bad)) is None
        assert creds.load_token(_write(tmp_path / "list.json", [1])) is None

    def test_parses_credentials_to_json_shape(self, tmp_path):
        path = _write(
            tmp_path / "t.json",
            {
                "token": "ya29.x",
                "refresh_token": "1//r",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "cid",
                "client_secret": "sec",
                "scopes": [creds.DRIVE_FILE_SCOPE],
                "expiry": "2030-01-02T03:04:05.123456Z",
            },
        )
        token = creds.load_token(path)
        assert token is not None
        assert token.token == "ya29.x"
        assert token.refresh_token == "1//r"
        assert token.scopes == [creds.DRIVE_FILE_SCOPE]
        assert token.expiry == datetime(2030, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)

    def test_tolerates_missing_fields_and_bad_expiry(self, tmp_path):
        token = creds.load_token(_write(tmp_path / "t.json", {"refresh_token": "1//r", "expiry": "garbage", "scopes": "notalist"}))
        assert token is not None
        assert token.token is None
        assert token.expiry is None
        assert token.scopes == []
        assert token.token_uri == "https://oauth2.googleapis.com/token"

    def test_token_mtime_ns(self, tmp_path):
        assert creds.token_mtime_ns(None) is None
        assert creds.token_mtime_ns(str(tmp_path / "nope.json")) is None
        path = _write(tmp_path / "t.json", {})
        assert isinstance(creds.token_mtime_ns(path), int)


class TestSaveTokenAtomic:
    def test_writes_0600_and_round_trips(self, tmp_path):
        path = tmp_path / "sub" / "gdrive_token.json"
        token = _token()
        creds.save_token_atomic(str(path), token)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        loaded = creds.load_token(str(path))
        assert loaded is not None
        assert loaded.token == token.token
        assert loaded.refresh_token == token.refresh_token
        assert loaded.expiry == token.expiry.replace(microsecond=token.expiry.microsecond)
        # Only the final file is left behind — no temp file.
        assert sorted(os.listdir(path.parent)) == ["gdrive_token.json"]

    def test_accepts_plain_dict(self, tmp_path):
        path = tmp_path / "t.json"
        creds.save_token_atomic(str(path), {"token": "a", "refresh_token": "b"})
        assert json.loads(path.read_text())["refresh_token"] == "b"

    def test_failed_write_leaves_previous_file_and_no_temp(self, tmp_path, monkeypatch):
        path = tmp_path / "t.json"
        path.write_text('{"token": "old"}', encoding="utf-8")

        def boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(creds.json, "dump", boom)
        with pytest.raises(OSError):
            creds.save_token_atomic(str(path), {"token": "new"})
        assert json.loads(path.read_text())["token"] == "old"
        assert sorted(os.listdir(tmp_path)) == ["t.json"]


class TestIsExpired:
    def test_expired_when_no_token_or_no_expiry(self):
        assert creds.is_expired(_token(token=None)) is True
        assert creds.is_expired(_token(expiry=None)) is True

    def test_skew_window(self):
        soon = datetime.now(timezone.utc) + timedelta(seconds=120)
        assert creds.is_expired(_token(expiry=soon), skew_seconds=300) is True
        assert creds.is_expired(_token(expiry=soon), skew_seconds=60) is False
        later = datetime.now(timezone.utc) + timedelta(hours=1)
        assert creds.is_expired(_token(expiry=later)) is False


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestRefreshAccessToken:
    async def test_success_updates_token_and_expiry(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"access_token": "ya29.new", "expires_in": 1800, "scope": creds.DRIVE_FILE_SCOPE})

        secrets = creds.ClientSecrets(**_SECRET_BLOCK)
        before = datetime.now(timezone.utc)
        async with _client(handler) as http:
            new = await creds.refresh_access_token(secrets, _token(token="old", expiry=None), http)
        assert seen["url"] == _SECRET_BLOCK["token_uri"]
        assert "grant_type=refresh_token" in seen["body"]
        assert "refresh_token=1%2F%2Frefresh" in seen["body"]
        assert new.token == "ya29.new"
        assert new.refresh_token == "1//refresh"
        assert new.scopes == [creds.DRIVE_FILE_SCOPE]
        assert before + timedelta(seconds=1700) < new.expiry <= before + timedelta(seconds=1900)

    async def test_invalid_grant_needs_reauthorization(self):
        def handler(request):
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})

        async with _client(handler) as http:
            with pytest.raises(GDriveNeedsAuthorizationError) as exc:
                await creds.refresh_access_token(creds.ClientSecrets(**_SECRET_BLOCK), _token(), http)
        assert exc.value.reason == "needs_reauthorization"

    async def test_other_4xx_is_config_error_without_echoing_description(self):
        def handler(request):
            return httpx.Response(401, json={"error": "invalid_client", "error_description": "secret=LEAK"})

        async with _client(handler) as http:
            with pytest.raises(GDriveConfigError) as exc:
                await creds.refresh_access_token(creds.ClientSecrets(**_SECRET_BLOCK), _token(), http)
        assert "LEAK" not in str(exc.value)

    async def test_5xx_and_transport_errors_are_transient(self):
        def five(request):
            return httpx.Response(503, text="oops")

        async with _client(five) as http:
            with pytest.raises(GDriveTransientError):
                await creds.refresh_access_token(creds.ClientSecrets(**_SECRET_BLOCK), _token(), http)

        def raise_connect(request):
            raise httpx.ConnectError("no route")

        async with _client(raise_connect) as http:
            with pytest.raises(GDriveTransientError) as exc:
                await creds.refresh_access_token(creds.ClientSecrets(**_SECRET_BLOCK), _token(), http)
        assert exc.value.reason == "network_error"

    async def test_missing_access_token_or_non_json_body(self):
        def no_token(request):
            return httpx.Response(200, json={"expires_in": 10})

        async with _client(no_token) as http:
            with pytest.raises(GDriveTransientError):
                await creds.refresh_access_token(creds.ClientSecrets(**_SECRET_BLOCK), _token(), http)

        def not_json(request):
            return httpx.Response(400, text="<html>")

        async with _client(not_json) as http:
            with pytest.raises(GDriveConfigError):
                await creds.refresh_access_token(creds.ClientSecrets(**_SECRET_BLOCK), _token(), http)

    async def test_bad_expires_in_falls_back_to_one_hour(self):
        def handler(request):
            return httpx.Response(200, json={"access_token": "ya29.new", "expires_in": "soon"})

        async with _client(handler) as http:
            new = await creds.refresh_access_token(creds.ClientSecrets(**_SECRET_BLOCK), _token(), http)
        assert new.expiry > datetime.now(timezone.utc) + timedelta(minutes=55)

    async def test_no_refresh_token(self):
        async with _client(lambda r: httpx.Response(200)) as http:
            with pytest.raises(GDriveNeedsAuthorizationError) as exc:
                await creds.refresh_access_token(creds.ClientSecrets(**_SECRET_BLOCK), _token(refresh_token=None), http)
        assert exc.value.reason == "token_missing"
