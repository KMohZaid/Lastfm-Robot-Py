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


def _setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("pyrogram").setLevel(logging.WARNING)


async def main() -> None:
    config = get_config()
    _setup_logging(config.log_level)

    database = Database(config.database_url)
    await database.setup()
    set_db(database)

    from bot.plugins import lastfm  # noqa: F401

    try:
        await client.start()
        await client.set_bot_commands(
            [
                BotCommand("set", "Set your Last.fm username"),
                BotCommand("start", "Show setup help"),
            ]
        )
        await idle()
    finally:
        await client.stop()
        await database.close()


if __name__ == "__main__":
    uvloop.install()
    asyncio.run(main())
