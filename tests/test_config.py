import pytest

from shared.config import Config, get_config, reset_config


@pytest.fixture(autouse=True)
def clear_config_cache():
    """Reset singleton before each test to prevent stale env var caching."""
    reset_config()
    yield
    reset_config()


def test_defaults():
    cfg = Config()
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-sonnet-4-6"
    assert cfg.server.host == "127.0.0.1"
    assert cfg.chat.history_window == 10


def test_env_override(monkeypatch):
    monkeypatch.setenv("AUTOWIKI_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AUTOWIKI_LLM_MODEL", "gpt-4o")
    cfg = Config()
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "gpt-4o"


def test_get_config_singleton():
    cfg1 = get_config()
    cfg2 = get_config()
    assert cfg1 is cfg2


def test_data_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOWIKI_DATA_DIR", str(tmp_path))
    cfg = Config()
    assert cfg.data_dir == tmp_path


def test_database_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    cfg = Config()
    assert cfg.database_path == tmp_path / "test.db"
