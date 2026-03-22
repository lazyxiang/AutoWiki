from __future__ import annotations
import os
from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_LLM_")
    provider: Literal["anthropic", "google", "openai", "openai-compatible", "ollama"] = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    base_url: str = ""


class EmbeddingConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_EMBEDDING_")
    provider: Literal["openai", "google", "ollama"] = "openai"
    model: str = "text-embedding-3-small"
    api_key: str = ""


class ServerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_SERVER_")
    host: str = "127.0.0.1"
    port: int = 3001
    auth_token: str = ""


class ChatConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_CHAT_")
    history_window: int = 10


class Config(BaseSettings):
    # Do NOT set env_nested_delimiter here — nested sub-models each read their own
    # env_prefix independently (e.g. LLMConfig reads AUTOWIKI_LLM_*, etc.)
    model_config = SettingsConfigDict(env_prefix="AUTOWIKI_")
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    data_dir: Path = Field(
        default_factory=lambda: Path(os.environ.get("AUTOWIKI_DATA_DIR", Path.home() / ".autowiki"))
    )
    database_path: Path = Field(
        default_factory=lambda: Path(os.environ.get("DATABASE_PATH", Path.home() / ".autowiki" / "autowiki.db"))
    )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config() -> None:
    """Reset the cached config singleton. Use in tests to force re-read of env vars."""
    global _config
    _config = None
