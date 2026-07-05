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

    from bot.plugins import lastfm  # noqa: F401

    try:
        logger.info("Registered %s handlers", _registered_handler_count())
        logger.info("Starting bot")
        await client.start()
        logger.info("Bot started")
        await client.set_bot_commands(
            [
                BotCommand("set", "Set your Last.fm username"),
                BotCommand("start", "Show setup help"),
            ]
        )
        logger.info("Registered %s bot commands", 2)
        await idle()
    finally:
        logger.info("Stopping bot")
        await client.stop()
        await database.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    uvloop.install()
    asyncio.run(main())
