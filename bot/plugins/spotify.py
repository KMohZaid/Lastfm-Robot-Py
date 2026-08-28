"""Telegram commands for Spotify authorization and account status."""

from __future__ import annotations

import html

from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.client import client
from bot.database import get_db
from bot.spotify import authorization_url, configured


@client.on_message(filters.command(["spotify_login", "spotify", "login_spotify"]))
async def spotify_login_handler(_, message: Message) -> None:
    if not configured():
        await message.reply_text("Spotify integration is not configured by the bot owner.")
        return
    url = authorization_url(message.from_user.id)
    await message.reply_text(
        "Connect Spotify, then return here:\n\n"
        "<a href=\"%s\">Authorize Spotify</a>" % html.escape(url, quote=True),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Authorize Spotify", url=url)]]
        ),
    )


@client.on_message(filters.command("auth"))
async def auth_handler(_, message: Message) -> None:
    lastfm = await get_db().get_lastfm_user(message.from_user.id)
    spotify = await get_db().get_spotify_token(message.from_user.id)
    lines = ["<b>Your authorised accounts are:</b>"]
    lines.append(
        "Lastfm: "
        + (
            f"<code>{html.escape(lastfm.lastfm_username)}</code>"
            if lastfm
            else "Not configured"
        )
        + " (/set &lt;username&gt; to set/change)"
    )
    if spotify:
        name = spotify.display_name or spotify.spotify_user_id or "connected"
        lines.append(f"Spotify: <code>{html.escape(name)}</code>")
    else:
        lines.append("Spotify: Not authorized (do /spotify_login)")
    await message.reply_text("\n".join(lines))
