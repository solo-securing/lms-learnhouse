"""Tests for src/services/integrations/gdrive/readiness.py."""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from src.services.integrations.gdrive import readiness
from src.services.integrations.gdrive.errors import (
    GDriveConfigError,
    GDriveNeedsAuthorizationError,
    GDriveNotEnabledError,
)

_SECRETS = {"web": {"client_id": "cid", "client_secret": "sec", "token_uri": "https://oauth2.googleapis.com/token"}}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def _token_payload(*, expired=False, refresh_token="1//r", token="ya29.cached"):
    delta = timedelta(hours=-1) if expired else timedelta(hours=1)
    return {
        "token": token,
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "sec",
        "scopes": ["https://www.googleapis.com/auth/drive.file"],
        "expiry": _iso(datetime.now(timezone.utc) + delta),
    }


class _Env:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.credentials = tmp_path / "client.json"
        self.token = tmp_path / "gdrive_token.json"
        self.http_calls: list[httpx.Request] = []
        self._outcomes: list = []
        self._mtime = 1_000_000_000
        monkeypatch.setenv("LEARNHOUSE_GDRIVE_ENABLED", "true")
        monkeypatch.setenv("LEARNHOUSE_GDRIVE_CREDENTIALS_PATH", str(self.credentials))
        monkeypatch.setenv("LEARNHOUSE_GDRIVE_TOKEN_PATH", str(self.token))
        monkeypatch.setattr(readiness, "_http_client", self._client)
        self.clock = [1000.0]
        monkeypatch.setattr(readiness, "time", SimpleNamespace(monotonic=lambda: self.clock[0]))

    def write_secrets(self, payload=_SECRETS):
        self.credentials.write_text(json.dumps(payload), encoding="utf-8")

    def write_token(self, payload):
        self.token.write_text(json.dumps(payload), encoding="utf-8")
        # Distinct, monotonically increasing mtimes so the cache key changes.
        self._mtime += 1_000_000
        os.utime(self.token, ns=(self._mtime, self._mtime))

    def script(self, *outcomes):
        self._outcomes = list(outcomes)

    def _handler(self, request):
        self.http_calls.append(request)
        if not self._outcomes:
            raise AssertionError("unexpected Google call")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def _client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handler))


@pytest.fixture
def env(tmp_path, monkeypatch):
    readiness.reset_for_tests()
    e = _Env(tmp_path, monkeypatch)
    yield e
    readiness.reset_for_tests()


def _ok_refresh(token="ya29.fresh"):
    return httpx.Response(200, json={"access_token": token, "expires_in": 3600})


# ------------------------------------------------------------- reasons


class TestReasons:
    async def test_disabled(self, env, monkeypatch):
        monkeypatch.setenv("LEARNHOUSE_GDRIVE_ENABLED", "false")
        status = await readiness.get_status()
        assert status == readiness.GDriveStatus(enabled=False, ready=False, reason="disabled")
        assert env.http_calls == []

    async def test_credentials_missing(self, env):
        assert (await readiness.get_status()).reason == "credentials_missing"

    async def test_credentials_invalid(self, env):
        env.write_secrets({"web": {"client_id": "x"}})
        assert (await readiness.get_status()).reason == "credentials_invalid"

    async def test_token_missing_no_file_or_no_refresh_token(self, env):
        env.write_secrets()
        assert (await readiness.get_status()).reason == "token_missing"
        env.write_token(_token_payload(refresh_token=None))
        assert (await readiness.get_status()).reason == "token_missing"
        assert env.http_calls == []

    async def test_ready_without_network_when_token_fresh(self, env):
        env.write_secrets()
        env.write_token(_token_payload())
        status = await readiness.get_status()
        assert status == readiness.GDriveStatus(enabled=True, ready=True, reason=None)
        assert env.http_calls == []

    async def test_ready_after_refresh_and_token_persisted(self, env):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))
        env.script(_ok_refresh("ya29.new"))
        assert (await readiness.get_status()).ready is True
        assert len(env.http_calls) == 1
        assert "grant_type=refresh_token" in env.http_calls[0].content.decode()
        on_disk = json.loads(env.token.read_text())
        assert on_disk["token"] == "ya29.new"
        assert on_disk["refresh_token"] == "1//r"
        assert await readiness.get_access_token() == "ya29.new"

    async def test_invalid_grant_needs_reauthorization(self, env):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))
        env.script(httpx.Response(400, json={"error": "invalid_grant"}))
        assert (await readiness.get_status()).reason == "needs_reauthorization"
        with pytest.raises(GDriveNeedsAuthorizationError):
            await readiness.require_ready()

    async def test_other_refresh_4xx_is_credentials_invalid(self, env):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))
        env.script(httpx.Response(401, json={"error": "invalid_client"}))
        assert (await readiness.get_status()).reason == "credentials_invalid"
        with pytest.raises(GDriveConfigError):
            await readiness.require_ready()

    async def test_network_error_then_recovers_after_ttl(self, env):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))
        env.script(httpx.ConnectError("down"))
        assert (await readiness.get_status()).reason == "network_error"
        with pytest.raises(GDriveNotEnabledError):
            await readiness.require_ready()
        # Still cached inside the TTL: no new call.
        assert (await readiness.get_status()).reason == "network_error"
        assert len(env.http_calls) == 1
        env.clock[0] += readiness.NETWORK_TTL_SECONDS + 1
        env.script(_ok_refresh())
        assert (await readiness.get_status()).ready is True
        assert len(env.http_calls) == 2


# ------------------------------------------------------------- caching


class TestCache:
    async def test_second_call_within_ttl_makes_no_request(self, env):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))
        env.script(_ok_refresh())
        await readiness.get_status()
        await readiness.get_status()
        assert len(env.http_calls) == 1

    async def test_force_bypasses_cache(self, env):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))
        env.script(httpx.ConnectError("down"), _ok_refresh())
        assert (await readiness.get_status()).reason == "network_error"
        assert (await readiness.get_status(force=True)).ready is True
        assert len(env.http_calls) == 2

    async def test_invalidate_forces_recheck(self, env):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))
        env.script(_ok_refresh(), _ok_refresh())
        await readiness.get_status()
        readiness.invalidate()
        # Token in memory is now fresh, so no refresh is needed — but the
        # verdict is recomputed (no request needed either).
        assert (await readiness.get_status()).ready is True
        assert len(env.http_calls) == 1

    async def test_new_token_file_is_picked_up_immediately(self, env):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))
        env.script(httpx.Response(400, json={"error": "invalid_grant"}))
        assert (await readiness.get_status()).reason == "needs_reauthorization"
        # Operator re-runs gdrive-authorize → fresh token, new mtime.
        env.write_token(_token_payload(token="ya29.reauth"))
        assert (await readiness.get_status()).ready is True
        assert len(env.http_calls) == 1
        assert await readiness.get_access_token() == "ya29.reauth"

    async def test_token_file_removed_after_ready(self, env):
        env.write_secrets()
        env.write_token(_token_payload())
        assert (await readiness.get_status()).ready is True
        env.token.unlink()
        assert (await readiness.get_status()).reason == "token_missing"

    async def test_state_accessor_and_reset(self, env):
        st = readiness.state()
        st.root_folder_id = "root123"
        assert readiness.state().root_folder_id == "root123"
        readiness.reset_for_tests()
        assert readiness.state().root_folder_id is None


# ---------------------------------------------------------- access token


class TestAccessToken:
    async def test_raises_when_not_configured(self, env, monkeypatch):
        monkeypatch.setenv("LEARNHOUSE_GDRIVE_ENABLED", "false")
        with pytest.raises(GDriveNotEnabledError):
            await readiness.get_access_token()
        monkeypatch.setenv("LEARNHOUSE_GDRIVE_ENABLED", "true")
        env.write_secrets({"web": {}})
        with pytest.raises(GDriveConfigError):
            await readiness.get_access_token()

    async def test_concurrent_calls_share_one_refresh(self, env):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))

        gate = asyncio.Event()

        async def slow_refresh(request):
            await gate.wait()
            return _ok_refresh("ya29.shared")

        class _AsyncHandlerClient(httpx.AsyncClient):
            pass

        def make_client():
            async def handler(request):
                env.http_calls.append(request)
                return await slow_refresh(request)

            return _AsyncHandlerClient(transport=httpx.MockTransport(handler))

        env.monkeypatch.setattr(readiness, "_http_client", make_client)

        task_a = asyncio.create_task(readiness.get_access_token())
        task_b = asyncio.create_task(readiness.get_access_token())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gate.set()
        tokens = await asyncio.gather(task_a, task_b)
        assert tokens == ["ya29.shared", "ya29.shared"]
        assert len(env.http_calls) == 1

    async def test_refresh_persist_failure_is_tolerated(self, env, caplog):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))
        env.script(_ok_refresh("ya29.mem"))

        def boom(*_a, **_k):
            raise OSError("read-only fs")

        env.monkeypatch.setattr(readiness, "save_token_atomic", boom)
        with caplog.at_level(logging.WARNING):
            assert await readiness.get_access_token() == "ya29.mem"
        assert "could not be written" in caplog.text
        assert "ya29.mem" not in caplog.text


# ---------------------------------------------------------------- logs


class TestLogging:
    async def test_logs_once_per_reason_and_on_transition(self, env, caplog):
        env.write_secrets()
        with caplog.at_level(logging.INFO):
            await readiness.get_status()
            await readiness.get_status()
        assert caplog.text.count("no usable token") == 1
        caplog.clear()
        env.write_token(_token_payload(expired=True))
        env.script(httpx.Response(400, json={"error": "invalid_grant"}))
        with caplog.at_level(logging.INFO):
            await readiness.get_status()
            await readiness.get_status()
        assert caplog.text.count("needs re-authorization") == 1
        assert "gdrive-authorize" in caplog.text
        caplog.clear()
        env.write_token(_token_payload())
        with caplog.at_level(logging.INFO):
            await readiness.get_status()
        assert "is ready" in caplog.text

    async def test_generic_not_ready_reason_logged(self, env, caplog):
        env.write_secrets({"web": {"client_id": "x"}})
        with caplog.at_level(logging.WARNING):
            await readiness.get_status()
        assert "reason=credentials_invalid" in caplog.text

    async def test_log_startup_status_disabled_makes_no_request(self, env, monkeypatch, caplog):
        monkeypatch.setenv("LEARNHOUSE_GDRIVE_ENABLED", "false")
        with caplog.at_level(logging.INFO):
            await readiness.log_startup_status()
        assert "disabled" in caplog.text
        assert env.http_calls == []

    async def test_log_startup_status_enabled(self, env, caplog):
        env.write_secrets()
        env.write_token(_token_payload(expired=True))
        env.script(httpx.ConnectError("down"))
        with caplog.at_level(logging.WARNING):
            await readiness.log_startup_status()
        assert "reason=network_error" in caplog.text


# ---------------------------------------------------- status report (CLI)


class TestFormatStatusReport:
    def test_ready_with_account_and_quota(self):
        status = readiness.GDriveStatus(enabled=True, ready=True, reason=None)
        about = {"emailAddress": "op@example.com", "storageQuota": {"usage": "5", "limit": "100"}}
        lines, code = readiness.format_status_report(status, about, "/c.json", "/t.json")
        assert code == 0
        assert lines[:3] == ["enabled: true", "ready: true", "reason: -"]
        assert "account: op@example.com" in lines
        assert "quota: 5/100" in lines
        assert "credentials_path: /c.json" in lines and "token_path: /t.json" in lines

    def test_ready_with_partial_about(self):
        status = readiness.GDriveStatus(enabled=True, ready=True, reason=None)
        lines, _ = readiness.format_status_report(status, {"storageQuota": "?"}, None, None)
        assert "account: -" in lines and "quota: -/-" in lines
        assert "credentials_path: -" in lines

    @pytest.mark.parametrize(
        "reason,needle",
        [
            ("needs_reauthorization", "gdrive-authorize"),
            ("token_missing", "gdrive-authorize"),
            ("credentials_missing", "LEARNHOUSE_GDRIVE_CREDENTIALS_PATH"),
            ("credentials_invalid", "LEARNHOUSE_GDRIVE_CREDENTIALS_PATH"),
            ("disabled", "LEARNHOUSE_GDRIVE_ENABLED"),
            ("network_error", None),
        ],
    )
    def test_not_ready_reasons(self, reason, needle):
        status = readiness.GDriveStatus(enabled=reason != "disabled", ready=False, reason=reason)
        lines, code = readiness.format_status_report(status, {"emailAddress": "x"}, "/c", "/t")
        assert code == 1
        assert f"reason: {reason}" in lines
        assert not any(line.startswith("account:") for line in lines)
        hints = [line for line in lines if line.startswith("hint:")]
        if needle:
            assert hints and needle in hints[0]
            if reason == "needs_reauthorization":
                assert "In production" in hints[0]
        else:
            assert hints == []
