"""Google Drive block of config/config.py (GDriveConfig + env/YAML resolution)."""

import pytest

from config.config import GDriveConfig, _env_bool, get_learnhouse_config


@pytest.fixture
def clear_gdrive_env(monkeypatch):
    # ``get_learnhouse_config()`` calls ``load_dotenv()``, which re-populates any
    # variable that is *absent* from ``os.environ`` from ``apps/api/.env``. On a
    # developer machine with the integration switched on there, ``delenv`` would
    # therefore not clear anything. An empty string is kept by load_dotenv and
    # read by config.py as "unset", so the YAML defaults are what gets tested.
    for var in (
        "LEARNHOUSE_GDRIVE_ENABLED",
        "LEARNHOUSE_GDRIVE_CREDENTIALS_PATH",
        "LEARNHOUSE_GDRIVE_TOKEN_PATH",
        "LEARNHOUSE_GDRIVE_ROOT_FOLDER_NAME",
    ):
        monkeypatch.setenv(var, "")


def test_model_defaults():
    cfg = GDriveConfig()
    assert cfg.enabled is False
    assert cfg.credentials_path is None
    assert cfg.token_path is None
    assert cfg.root_folder_name == "LearnHouse"


def test_config_yaml_defaults_are_off(clear_gdrive_env):
    cfg = get_learnhouse_config().gdrive_config
    assert cfg.enabled is False
    assert cfg.credentials_path is None
    assert cfg.token_path is None
    assert cfg.root_folder_name == "LearnHouse"


def test_env_enabled_wins_over_yaml_false(clear_gdrive_env, monkeypatch):
    monkeypatch.setenv("LEARNHOUSE_GDRIVE_ENABLED", "true")
    assert get_learnhouse_config().gdrive_config.enabled is True


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("TRUE", True), ("yes", True), ("false", False), ("0", False), ("", None)],
)
def test_env_bool_accepts_common_spellings(raw, expected):
    # "" means "unset" → falls back to the YAML value (None here).
    assert _env_bool(raw, None) is expected


def test_env_paths_override_yaml(clear_gdrive_env, monkeypatch):
    monkeypatch.setenv("LEARNHOUSE_GDRIVE_CREDENTIALS_PATH", "/secrets/client.json")
    monkeypatch.setenv("LEARNHOUSE_GDRIVE_TOKEN_PATH", "/secrets/gdrive_token.json")
    monkeypatch.setenv("LEARNHOUSE_GDRIVE_ROOT_FOLDER_NAME", "Campus")
    cfg = get_learnhouse_config().gdrive_config
    assert cfg.credentials_path == "/secrets/client.json"
    assert cfg.token_path == "/secrets/gdrive_token.json"
    assert cfg.root_folder_name == "Campus"


def test_empty_env_strings_fall_back_to_defaults(clear_gdrive_env, monkeypatch):
    monkeypatch.setenv("LEARNHOUSE_GDRIVE_CREDENTIALS_PATH", "")
    monkeypatch.setenv("LEARNHOUSE_GDRIVE_ROOT_FOLDER_NAME", "")
    cfg = get_learnhouse_config().gdrive_config
    assert cfg.credentials_path is None
    assert cfg.root_folder_name == "LearnHouse"
