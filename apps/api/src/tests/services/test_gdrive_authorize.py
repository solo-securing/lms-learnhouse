"""Tests for src/services/integrations/gdrive/authorize.py (consent flow wrapper)."""

import json
import logging
import os
import stat
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from src.services.integrations.gdrive import authorize
from src.services.integrations.gdrive.errors import GDriveConfigError, GDriveTransientError

_SECRETS = {"web": {"client_id": "cid", "client_secret": "GOCSPX-LEAK", "token_uri": "https://oauth2.googleapis.com/token"}}


class _FakeCreds:
    def __init__(self, payload):
        self._payload = payload

    def to_json(self):
        return json.dumps(self._payload)


@pytest.fixture
def fake_flow(monkeypatch):
    """Install a fake ``google_auth_oauthlib.flow.InstalledAppFlow`` module."""
    flow_instance = MagicMock()
    flow_instance.run_local_server.return_value = _FakeCreds(
        {"token": "ya29.new", "refresh_token": "1//REFRESH-LEAK", "scopes": [authorize.DRIVE_FILE_SCOPE], "expiry": "2030-01-01T00:00:00Z"}
    )
    flow_cls = MagicMock()
    flow_cls.from_client_secrets_file.return_value = flow_instance
    module = types.ModuleType("google_auth_oauthlib.flow")
    module.InstalledAppFlow = flow_cls
    pkg = types.ModuleType("google_auth_oauthlib")
    pkg.flow = module
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", pkg)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", module)
    return flow_cls, flow_instance


@pytest.fixture
def paths(tmp_path):
    creds = tmp_path / "client.json"
    creds.write_text(json.dumps(_SECRETS), encoding="utf-8")
    return str(creds), str(tmp_path / "secrets" / "gdrive_token.json")


def _about_ok():
    return patch.object(authorize, "_fetch_account_email", return_value="op@example.com")


class TestRunAuthorize:
    def test_happy_path_writes_0600_token_and_returns_email(self, fake_flow, paths, caplog, capsys):
        flow_cls, flow_instance = fake_flow
        creds_path, token_path = paths
        with _about_ok() as about, caplog.at_level(logging.INFO):
            email = authorize.run_authorize(credentials_path=creds_path, token_path=token_path, port=9999, open_browser=False)
        assert email == "op@example.com"
        flow_cls.from_client_secrets_file.assert_called_once_with(creds_path, scopes=[authorize.DRIVE_FILE_SCOPE])
        flow_instance.run_local_server.assert_called_once_with(port=9999, open_browser=False, access_type="offline", prompt="consent")
        about.assert_called_once_with(token_path)
        assert stat.S_IMODE(os.stat(token_path).st_mode) == 0o600
        saved = json.loads(open(token_path, encoding="utf-8").read())
        assert saved["refresh_token"] == "1//REFRESH-LEAK"
        assert saved["client_id"] == "cid" and saved["client_secret"] == "GOCSPX-LEAK"
        # Nothing secret reaches stdout or the log.
        out = capsys.readouterr()
        for secret in ("GOCSPX-LEAK", "1//REFRESH-LEAK", "ya29.new"):
            assert secret not in caplog.text and secret not in out.out and secret not in out.err

    def test_defaults_open_browser_and_port(self, fake_flow, paths):
        _cls, flow_instance = fake_flow
        creds_path, token_path = paths
        with _about_ok():
            authorize.run_authorize(credentials_path=creds_path, token_path=token_path)
        assert flow_instance.run_local_server.call_args.kwargs["port"] == 8765
        assert flow_instance.run_local_server.call_args.kwargs["open_browser"] is True

    def test_missing_credentials_has_guidance(self, fake_flow, tmp_path):
        with pytest.raises(GDriveConfigError) as exc:
            authorize.run_authorize(credentials_path=str(tmp_path / "nope.json"), token_path=str(tmp_path / "t.json"), port=8765)
        assert exc.value.reason == "credentials_missing"
        # Actionable for the operator: which setting to check and how the client must be registered.
        assert "LEARNHOUSE_GDRIVE_CREDENTIALS_PATH" in str(exc.value)
        assert "http://localhost:8765/" in str(exc.value)

    def test_invalid_credentials_json_has_guidance(self, fake_flow, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"web": {"client_id": "only"}}), encoding="utf-8")
        with pytest.raises(GDriveConfigError) as exc:
            authorize.run_authorize(credentials_path=str(bad), token_path=str(tmp_path / "t.json"))
        assert exc.value.reason == "credentials_invalid"
        assert "LEARNHOUSE_GDRIVE_CREDENTIALS_PATH" in str(exc.value)

    def test_missing_token_path(self, fake_flow, paths):
        creds_path, _ = paths
        with pytest.raises(GDriveConfigError) as exc:
            authorize.run_authorize(credentials_path=creds_path, token_path=None)
        assert "LEARNHOUSE_GDRIVE_TOKEN_PATH" in str(exc.value)

    def test_flow_load_failure(self, fake_flow, paths):
        flow_cls, _ = fake_flow
        flow_cls.from_client_secrets_file.side_effect = ValueError("bad file")
        creds_path, token_path = paths
        with pytest.raises(GDriveConfigError) as exc:
            authorize.run_authorize(credentials_path=creds_path, token_path=token_path, port=8765)
        assert exc.value.reason == "credentials_invalid"
        # Same actionable guidance as the load_client_secrets branch (T083).
        assert "LEARNHOUSE_GDRIVE_CREDENTIALS_PATH" in str(exc.value)
        assert "http://localhost:8765/" in str(exc.value)
        assert "bad file" not in str(exc.value)  # never echo the library's message

    def test_consent_failure_mentions_redirect_uri(self, fake_flow, paths):
        _cls, flow_instance = fake_flow
        flow_instance.run_local_server.side_effect = RuntimeError("redirect_uri_mismatch")
        creds_path, token_path = paths
        with pytest.raises(GDriveConfigError) as exc:
            authorize.run_authorize(credentials_path=creds_path, token_path=token_path, port=8765)
        assert "http://localhost:8765/" in str(exc.value)
        assert "In production" in str(exc.value)
        assert not os.path.exists(token_path)

    def test_consent_cancelled(self, fake_flow, paths):
        _cls, flow_instance = fake_flow
        flow_instance.run_local_server.side_effect = KeyboardInterrupt()
        creds_path, token_path = paths
        with pytest.raises(GDriveConfigError) as exc:
            authorize.run_authorize(credentials_path=creds_path, token_path=token_path)
        assert "cancelled" in str(exc.value)

    def test_missing_refresh_token(self, fake_flow, paths):
        _cls, flow_instance = fake_flow
        flow_instance.run_local_server.return_value = _FakeCreds({"token": "ya29.only"})
        creds_path, token_path = paths
        with pytest.raises(GDriveConfigError) as exc:
            authorize.run_authorize(credentials_path=creds_path, token_path=token_path)
        assert "refresh_token" in str(exc.value)
        assert not os.path.exists(token_path)

    def test_token_write_failure(self, fake_flow, paths, monkeypatch):
        creds_path, token_path = paths

        def boom(*_a, **_k):
            raise OSError("read-only")

        monkeypatch.setattr(authorize, "save_token_atomic", boom)
        with pytest.raises(GDriveConfigError) as exc:
            authorize.run_authorize(credentials_path=creds_path, token_path=token_path)
        assert "writable" in str(exc.value)


class TestFetchAccountEmail:
    def test_reads_token_and_calls_about(self, tmp_path):
        token_path = tmp_path / "t.json"
        token_path.write_text(json.dumps({"token": "ya29.x", "refresh_token": "r"}), encoding="utf-8")

        class _FakeClient:
            def __init__(self, provider):
                self.provider = provider

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def about(self):
                assert await self.provider() == "ya29.x"
                return {"emailAddress": "op@example.com", "storageQuota": {}}

        with patch.object(authorize, "DriveClient", _FakeClient):
            assert authorize._fetch_account_email(str(token_path)) == "op@example.com"

    def test_missing_access_token_and_drive_error(self, tmp_path):
        token_path = tmp_path / "t.json"
        token_path.write_text(json.dumps({"refresh_token": "r"}), encoding="utf-8")
        with pytest.raises(GDriveConfigError):
            authorize._fetch_account_email(str(token_path))

        token_path.write_text(json.dumps({"token": "ya29.x", "refresh_token": "r"}), encoding="utf-8")

        class _Boom:
            def __init__(self, provider):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def about(self):
                raise GDriveTransientError("down")

        with patch.object(authorize, "DriveClient", _Boom):
            with pytest.raises(GDriveConfigError) as exc:
                authorize._fetch_account_email(str(token_path))
        assert exc.value.reason == "network_error"
