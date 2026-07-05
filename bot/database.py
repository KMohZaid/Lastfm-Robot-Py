"""Small asyncpg database layer for Last.fm usernames."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse, urlunparse

import asyncpg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LastfmUser:
    telegram_user_id: int
    lastfm_username: str


class Database:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.pool: asyncpg.Pool | None = None

    async def setup(self) -> None:
        await self._ensure_database_exists()
        self.pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=5,
            command_timeout=60,
        )
        await self._init_tables()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()

    async def _ensure_database_exists(self) -> None:
        parsed = urlparse(self.database_url)
        db_name = unquote(parsed.path.lstrip("/"))
        if not db_name:
            raise ValueError("DATABASE_URL must include a database name")
        if re.fullmatch(r"[A-Za-z0-9_-]+", db_name) is None:
            raise ValueError(
                "DATABASE_URL database name may only contain letters, numbers, "
                "underscores, and hyphens"
            )

        postgres_url = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                "/postgres",
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
        conn = await asyncpg.connect(postgres_url)
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                db_name,
            )
            if exists:
                return
            await conn.execute(f'CREATE DATABASE "{db_name}"')
            logger.info("Created database %s", db_name)
        finally:
            await conn.close()

    async def _init_tables(self) -> None:
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id BIGINT PRIMARY KEY,
                    lastfm_username TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    async def set_lastfm_username(
        self,
        telegram_user_id: int,
        lastfm_username: str,
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (telegram_user_id, lastfm_username)
                VALUES ($1, $2)
                ON CONFLICT (telegram_user_id) DO UPDATE
                SET lastfm_username = EXCLUDED.lastfm_username,
                    updated_at = now()
                """,
                telegram_user_id,
                lastfm_username,
            )

    async def get_lastfm_user(self, telegram_user_id: int) -> LastfmUser | None:
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT telegram_user_id, lastfm_username
                FROM users
                WHERE telegram_user_id = $1
                """,
                telegram_user_id,
            )
        if row is None:
            return None
        return LastfmUser(
            telegram_user_id=row["telegram_user_id"],
            lastfm_username=row["lastfm_username"],
        )


db: Database | None = None


def set_db(database: Database) -> None:
    global db
    db = database


def get_db() -> Database:
    if db is None:
        raise RuntimeError("Database is not initialized")
    return db
