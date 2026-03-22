import os
import pytest
from shared.config import Config

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
