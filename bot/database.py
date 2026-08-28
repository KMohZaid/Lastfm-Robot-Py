"""Small asyncpg database layer for Last.fm and Spotify users."""

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


@dataclass(frozen=True)
class SpotifyToken:
    telegram_user_id: int
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str
    spotify_user_id: str | None = None
    display_name: str | None = None


class Database:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.pool: asyncpg.Pool | None = None

    async def setup(self) -> None:
        await self._ensure_database_exists()
        self.pool = await asyncpg.create_pool(
            self.database_url, min_size=1, max_size=5, command_timeout=60
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
            raise ValueError("DATABASE_URL database name may only contain letters, numbers, underscores, and hyphens")
        postgres_url = urlunparse((parsed.scheme, parsed.netloc, "/postgres", parsed.params, parsed.query, parsed.fragment))
        conn = await asyncpg.connect(postgres_url)
        try:
            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
            if not exists:
                await conn.execute(f'CREATE DATABASE "{db_name}"')
                logger.info("Created database %s", db_name)
        finally:
            await conn.close()

    async def _init_tables(self) -> None:
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id BIGINT PRIMARY KEY,
                    lastfm_username TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS spotify_tokens (
                    telegram_user_id BIGINT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL,
                    scope TEXT NOT NULL DEFAULT '',
                    spotify_user_id TEXT,
                    display_name TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)

    async def set_lastfm_username(self, telegram_user_id: int, lastfm_username: str) -> None:
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (telegram_user_id, lastfm_username) VALUES ($1, $2)
                ON CONFLICT (telegram_user_id) DO UPDATE SET lastfm_username = EXCLUDED.lastfm_username, updated_at = now()
            """, telegram_user_id, lastfm_username)

    async def get_lastfm_user(self, telegram_user_id: int) -> LastfmUser | None:
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT telegram_user_id, lastfm_username FROM users WHERE telegram_user_id = $1", telegram_user_id)
        return LastfmUser(row["telegram_user_id"], row["lastfm_username"]) if row else None

    async def set_spotify_token(self, token: SpotifyToken) -> None:
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO spotify_tokens (telegram_user_id, access_token, refresh_token, expires_at, scope, spotify_user_id, display_name)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (telegram_user_id) DO UPDATE SET access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token,
                    expires_at = EXCLUDED.expires_at, scope = EXCLUDED.scope, spotify_user_id = EXCLUDED.spotify_user_id,
                    display_name = EXCLUDED.display_name, updated_at = now()
            """, token.telegram_user_id, token.access_token, token.refresh_token, token.expires_at, token.scope, token.spotify_user_id, token.display_name)

    async def get_spotify_token(self, telegram_user_id: int) -> SpotifyToken | None:
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM spotify_tokens WHERE telegram_user_id = $1", telegram_user_id)
        if row is None:
            return None
        return SpotifyToken(
            telegram_user_id=row["telegram_user_id"],
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            expires_at=row["expires_at"],
            scope=row["scope"],
            spotify_user_id=row["spotify_user_id"],
            display_name=row["display_name"],
        )


db: Database | None = None


def set_db(database: Database) -> None:
    global db
    db = database


def get_db() -> Database:
    if db is None:
        raise RuntimeError("Database is not initialized")
    return db
