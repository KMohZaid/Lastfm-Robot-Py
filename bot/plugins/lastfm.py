"""Last.fm and Spotify command and inline handlers."""

from __future__ import annotations

import asyncio
import io
import tempfile
from pathlib import Path
import urllib.parse

import pyrogram
from PIL import Image
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery, InlineQueryResultArticle, InlineQueryResultCachedPhoto, InlineQueryResultCachedSticker, InputTextMessageContent, Message

from bot.client import client
from bot.config import get_config
from bot.database import get_db
from bot.spotify import SpotifyNowPlaying, currently_playing
from lastfm_card import download_image, get_now_playing, render_card, search_artwork_candidates

CACHE_TIME_SECONDS = 15


@client.on_message(filters.command("start"))
async def start_handler(_, message: Message) -> None:
    await message.reply_text("Set Last.fm with <code>/set your_lastfm_username</code> or connect Spotify with <code>/spotify_login</code>.\n\nThen use this bot inline to share what you are listening to.")


@client.on_message(filters.command("set"))
async def set_handler(_, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text("Usage: <code>/set your_lastfm_username</code>")
        return
    username = parts[1].strip()
    await get_db().set_lastfm_username(message.from_user.id, username)
    await message.reply_text(f"Saved Last.fm username: <code>{username}</code>")


@client.on_inline_query()
async def inline_handler(bot, inline_query: InlineQuery) -> None:
    user_id = inline_query.from_user.id
    avatar_path = await _download_avatar(bot, inline_query)
    try:
        results = []
        try:
            spotify = await currently_playing(user_id)
        except Exception:
            spotify = None
        if spotify:
            results.extend(await _spotify_results(bot, inline_query.id, spotify, inline_query.from_user.first_name or "Someone", avatar_path))
        lastfm = await get_db().get_lastfm_user(user_id)
        if lastfm:
            try:
                payload = await asyncio.to_thread(_lastfm_payload, lastfm.lastfm_username, get_config().lastfm_api_key, inline_query.from_user.first_name or "Someone", avatar_path)
                results.extend(await _upload_lastfm_results(bot, inline_query.id, payload))
            except Exception:
                pass
        if not results:
            results = [InlineQueryResultArticle(id="none", title="No now playing music?? check auth", description="Connect Spotify or set Last.fm first.", input_message_content=InputTextMessageContent("No now playing music?? check auth"))]
        await _safe_answer(inline_query, results, cache_time=CACHE_TIME_SECONDS, is_gallery=True, is_personal=True)
    except pyrogram.errors.QueryIdInvalid:
        pass
    except Exception:
        await _safe_answer(inline_query, [InlineQueryResultArticle(id="failed", title="Could not generate card", input_message_content=InputTextMessageContent("Could not generate a card right now."))], cache_time=CACHE_TIME_SECONDS, is_personal=True)
    finally:
        if avatar_path:
            Path(avatar_path).unlink(missing_ok=True)


async def _safe_answer(inline_query: InlineQuery, results: list, **kwargs) -> bool:
    try:
        await inline_query.answer(results, **kwargs)
        return True
    except pyrogram.errors.QueryIdInvalid:
        return False


def _lastfm_payload(username: str, api_key: str, display_name: str, avatar_path: str | None) -> list[tuple[str, bytes, str]]:
    now = get_now_playing(username, api_key)
    if not now["now_playing"]:
        return []
    candidates = search_artwork_candidates(now["artist"], now["track"], now["album"], now["lastfm_image_url"])
    if not candidates:
        return []
    if get_config().lastfm_show_both_even_when_found_one:
        selected = candidates
    else:
        music = [candidate for candidate in candidates if candidate[0] == "Music"]
        selected = music or candidates[:1]
    payload = []
    for variant, artwork_url in selected:
        artwork_img = download_image(artwork_url) if artwork_url else None
        image = render_card(display_name, now["track"], now["artist"], now["album"], avatar_path, artwork_img)
        payload.append((f"Last.fm {variant}", _png_bytes(image), f"{now['artist']} - {now['track']}"))
    return payload


def _png_bytes(image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def _spotify_results(bot, query_id: str, now: SpotifyNowPlaying, display_name: str, avatar_path: str | None) -> list:
    artwork = await asyncio.to_thread(download_image, now.image_url) if now.image_url else None
    image = await asyncio.to_thread(render_card, display_name, now.name, now.artists, now.album, avatar_path, artwork)
    png = _png_bytes(image)
    return [await _upload_photo(bot, query_id, "Spotify", png, now.url), await _upload_sticker(bot, query_id, "Spotify", png)]


async def _upload_lastfm_results(bot, query_id: str, payload: list[tuple[str, bytes, str]]) -> list:
    return [await _upload_photo(bot, query_id, label, image, None) for label, image, _ in payload]


async def _upload_photo(bot, query_id: str, label: str, image_bytes: bytes, url: str | None):
    photo = io.BytesIO(image_bytes)
    photo.name = f"{label.lower().replace('.', '').replace(' ', '-')}.png"
    sent = await bot.send_photo(chat_id=get_config().storage_chat_id, photo=photo, caption=f"{label}: card")
    return InlineQueryResultCachedPhoto(id=f"{label.lower()}-{query_id}", photo_file_id=sent.photo.file_id, title=f"{label} card", reply_markup=_spotify_button(url) if url else None)


async def _upload_sticker(bot, query_id: str, label: str, image_bytes: bytes):
    sticker = io.BytesIO(_sticker_bytes(image_bytes))
    sticker.name = f"{label.lower()}.webp"
    sent = await bot.send_sticker(chat_id=get_config().storage_chat_id, sticker=sticker)
    return InlineQueryResultCachedSticker(id=f"{label.lower()}-sticker", sticker_file_id=sent.sticker.file_id)


def _sticker_bytes(image_bytes: bytes) -> bytes:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    image.thumbnail((512, 512))
    output = io.BytesIO()
    image.save(output, format="WEBP", lossless=True)
    return output.getvalue()


def _spotify_button(url: str | None) -> InlineKeyboardMarkup | None:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Open on Spotify", url=url)]]) if url else None


async def _download_avatar(bot, inline_query: InlineQuery) -> str | None:
    photo = getattr(inline_query.from_user, "photo", None)
    file_id = (photo.big_file_id or photo.small_file_id) if photo else None
    if not file_id:
        return None
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    tmp.close()
    await bot.download_media(file_id, file_name=tmp.name)
    return tmp.name
