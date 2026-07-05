"""Environment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(".env")


@dataclass(frozen=True)
class Config:
    bot_token: str
    api_id: int
    api_hash: str
    lastfm_api_key: str
    database_url: str
    storage_chat_id: int
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        bot_token = _required("BOT_TOKEN")
        api_id = int(_required("API_ID"))
        api_hash = _required("API_HASH")
        lastfm_api_key = _required("LASTFM_API_KEY")
        database_url = _required("DATABASE_URL")
        storage_chat_id = int(_required("STORAGE_CHAT_ID"))
        return cls(
            bot_token=bot_token,
            api_id=api_id,
            api_hash=api_hash,
            lastfm_api_key=lastfm_api_key,
            database_url=database_url,
            storage_chat_id=storage_chat_id,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


def _required(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise ValueError(f"{name} environment variable is required")
    return value


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config
