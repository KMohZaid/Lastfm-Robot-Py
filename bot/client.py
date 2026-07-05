"""Kurigram client instance and setup."""

from __future__ import annotations

from pyrogram import Client
from pyrogram.enums import ParseMode

from bot.config import get_config

config = get_config()

client = Client(
    name="lastfm_robot",
    api_id=config.api_id,
    api_hash=config.api_hash,
    bot_token=config.bot_token,
    parse_mode=ParseMode.HTML,
)
