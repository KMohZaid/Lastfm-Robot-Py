"""Bot entry point."""

from __future__ import annotations

import asyncio
import logging
import sys

import uvloop
from pyrogram import idle
from pyrogram.types import BotCommand

from bot.client import client
from bot.config import get_config
from bot.database import Database, set_db
from bot.spotify import set_bot_username, start_callback_server

logger = logging.getLogger(__name__)


def _setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger("pyrogram").setLevel(logging.WARNING)


def _registered_handler_count() -> int:
    dispatcher = getattr(client, "dispatcher", None)
    groups = getattr(dispatcher, "groups", None)
    if not isinstance(groups, dict):
        return 0
    return sum(len(handlers) for handlers in groups.values())


async def main() -> None:
    config = get_config()
    _setup_logging(config.log_level)

    logger.info("Connecting to database")
    database = Database(config.database_url)
    await database.setup()
    set_db(database)
    logger.info("Database connected and initialized")

    from bot.plugins import lastfm, spotify  # noqa: F401

    callback_runner = None
    try:
        logger.info("Registered %s handlers", _registered_handler_count())
        await client.start()
        me = await client.get_me()
        set_bot_username(me.username)
        if config.spotify_client_id and config.spotify_client_secret:
            callback_runner = await start_callback_server()
        await client.set_bot_commands(
            [
                BotCommand("set", "Set your Last.fm username"),
                BotCommand("spotify_login", "Connect Spotify"),
                BotCommand("auth", "Show connected accounts"),
                BotCommand("start", "Show setup help"),
            ]
        )
        await idle()
    finally:
        if callback_runner is not None:
            await callback_runner.cleanup()
        await client.stop()
        await database.close()


if __name__ == "__main__":
    uvloop.install()
    asyncio.run(main())
