"""CLI tests for ``gdrive-authorize`` / ``gdrive-status`` in apps/api/cli.py."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

import cli as cli_module
from src.services.integrations.gdrive.errors import GDriveConfigError
from src.services.integrations.gdrive.readiness import GDriveStatus

runner = CliRunner()


@pytest.fixture
def gdrive_cfg(monkeypatch):
    cfg = SimpleNamespace(
        gdrive_config=SimpleNamespace(
            enabled=True, credentials_path="/secrets/client.json", token_path="/secrets/gdrive_token.json", root_folder_name="LearnHouse"
        )
    )
    monkeypatch.setattr(cli_module, "get_learnhouse_config", lambda: cfg)
    return cfg


class TestGDriveAuthorize:
    def test_success_prints_contract_line(self, gdrive_cfg):
        with patch("src.services.integrations.gdrive.authorize.run_authorize", return_value="op@example.com") as run:
            result = runner.invoke(cli_module.cli, ["gdrive-authorize"])
        assert result.exit_code == 0, result.output
        assert "Google Drive authorized as op@example.com. Token saved to /secrets/gdrive_token.json." in result.output
        assert run.call_args.kwargs == {
            "credentials_path": "/secrets/client.json",
            "token_path": "/secrets/gdrive_token.json",
            "port": 8765,
            "open_browser": True,
        }

    def test_options_are_forwarded(self, gdrive_cfg):
        with patch("src.services.integrations.gdrive.authorize.run_authorize", return_value="x@y") as run:
            result = runner.invoke(cli_module.cli, ["gdrive-authorize", "--port", "9001", "--no-browser"])
        assert result.exit_code == 0, result.output
        assert run.call_args.kwargs["port"] == 9001
        assert run.call_args.kwargs["open_browser"] is False

    def test_config_error_exits_1_on_stderr(self, gdrive_cfg):
        with patch(
            "src.services.integrations.gdrive.authorize.run_authorize",
            side_effect=GDriveConfigError("register http://localhost:8765/ as redirect URI", reason="credentials_invalid"),
        ):
            result = runner.invoke(cli_module.cli, ["gdrive-authorize"])
        assert result.exit_code == 1
        # The failure must land on stderr only (contracts/cli.md); Click ≥ 8.2 keeps the streams apart.
        assert "redirect URI" in result.stderr
        assert "redirect URI" not in result.stdout
        assert "Token saved" not in result.output


class TestGDriveStatus:
    def test_ready_prints_account_quota_and_exits_0(self, gdrive_cfg):
        status = GDriveStatus(enabled=True, ready=True, reason=None)
        about = {"emailAddress": "op@example.com", "storageQuota": {"usage": "10", "limit": "100"}}
        with patch("src.services.integrations.gdrive.readiness.get_status", new_callable=AsyncMock, return_value=status) as gs, patch(
            "src.services.integrations.gdrive.client.DriveClient.about", new_callable=AsyncMock, return_value=about
        ):
            result = runner.invoke(cli_module.cli, ["gdrive-status"])
        assert result.exit_code == 0, result.output
        gs.assert_awaited_once_with(force=True)
        for line in ("enabled: true", "ready: true", "reason: -", "account: op@example.com", "quota: 10/100",
                     "credentials_path: /secrets/client.json", "token_path: /secrets/gdrive_token.json"):
            assert line in result.output

    def test_not_ready_exits_1_without_account(self, gdrive_cfg):
        status = GDriveStatus(enabled=True, ready=False, reason="needs_reauthorization")
        with patch("src.services.integrations.gdrive.readiness.get_status", new_callable=AsyncMock, return_value=status), patch(
            "src.services.integrations.gdrive.client.DriveClient.about", new_callable=AsyncMock
        ) as about:
            result = runner.invoke(cli_module.cli, ["gdrive-status"])
        assert result.exit_code == 1
        about.assert_not_awaited()
        assert "reason: needs_reauthorization" in result.output
        assert "account:" not in result.output
        assert "gdrive-authorize" in result.output
        for secret in ("ya29", "refresh_token", "client_secret"):
            assert secret not in result.output

    def test_ready_but_about_fails_still_reports(self, gdrive_cfg):
        from src.services.integrations.gdrive.errors import GDriveTransientError

        status = GDriveStatus(enabled=True, ready=True, reason=None)
        with patch("src.services.integrations.gdrive.readiness.get_status", new_callable=AsyncMock, return_value=status), patch(
            "src.services.integrations.gdrive.client.DriveClient.about", new_callable=AsyncMock, side_effect=GDriveTransientError("x")
        ):
            result = runner.invoke(cli_module.cli, ["gdrive-status"])
        assert result.exit_code == 0
        assert "ready: true" in result.output
        assert "account:" not in result.output
