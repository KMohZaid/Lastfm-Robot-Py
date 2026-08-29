"""Last.fm command and inline handlers."""

from __future__ import annotations

import asyncio
import html
import io
import tempfile
from pathlib import Path

import pyrogram
import requests
from PIL import Image
from pyrogram import filters
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedSticker,
    InputTextMessageContent,
    Message,
)

from bot.client import client
from bot.config import get_config
from bot.database import get_db
from lastfm_card import download_image, get_now_playing, render_card, search_artwork_candidates

CACHE_TIME_SECONDS = 15
MAX_ARTWORK_CANDIDATES = 3
API_OWNER_ID = 866874030
LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"


async def _account_status(telegram_user_id: int) -> str:
    lastfm = await get_db().get_lastfm_user(telegram_user_id)
    if lastfm is None:
        return (
            "Set your Last.fm username with "
            "<code>/set your_lastfm_username</code>."
        )
    try:
        now = await asyncio.to_thread(
            get_now_playing, lastfm.lastfm_username, get_config().lastfm_api_key
        )
    except Exception:
        now = {"now_playing": False}
    if now.get("now_playing"):
        return f"You are playing something on Last.fm account <code>{html.escape(lastfm.lastfm_username)}</code>."
    return (
        f"You are not playing anything on Last.fm account "
        f"<code>{html.escape(lastfm.lastfm_username)}</code>.\n\n"
        "If you are playing something from Spotify, verify that your Spotify link "
        "has not expired (it may expire after about six months): "
        '<a href="https://www.last.fm/about/trackmymusic#spotify">'
        "connect Spotify to Last.fm</a>."
    )


@client.on_message(filters.command("start"))
async def start_handler(_, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    parameter = parts[1].strip().lower() if len(parts) == 2 else ""
    if parameter == "auth":
        await message.reply_text(await _auth_status(message.from_user.id))
        return

    reply_markup = None
    me = await client.get_me()
    if me.username:
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Account status", url=f"https://t.me/{me.username}?start=auth")]]
        )
    await message.reply_text(
        await _account_status(message.from_user.id)
        + "\n\nTo use Spotify, connect it to your Last.fm account here: "
        '<a href="https://www.last.fm/about/trackmymusic#spotify">'
        "Last.fm Spotify connection</a>.",
        reply_markup=reply_markup,
    )


async def _auth_status(telegram_user_id: int) -> str:
    lastfm = await get_db().get_lastfm_user(telegram_user_id)
    if lastfm:
        return (
            "<b>Your authorised account:</b>\n"
            f"Last.fm: <code>{html.escape(lastfm.lastfm_username)}</code>"
        )
    return (
        "<b>Your authorised account:</b>\n"
        "Last.fm: Not configured\n\n"
        "Use <code>/set &lt;username&gt;</code> to configure it."
    )


@client.on_message(filters.command("auth"))
async def auth_handler(_, message: Message) -> None:
    await message.reply_text(await _auth_status(message.from_user.id))


@client.on_message(filters.command("set"))
async def set_handler(_, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text("Usage: <code>/set your_lastfm_username</code>")
        return
    username = parts[1].strip()
    await get_db().set_lastfm_username(message.from_user.id, username)
    await message.reply_text(f"Saved Last.fm username: <code>{html.escape(username)}</code>")


@client.on_message(filters.private & filters.user(API_OWNER_ID) & filters.command("api"))
async def api_handler(_, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        username = parts[1].strip()
    else:
        lastfm = await get_db().get_lastfm_user(API_OWNER_ID)
        if lastfm is None:
            await message.reply_text("Set your Last.fm username first with <code>/set username</code>.")
            return
        username = lastfm.lastfm_username

    response = await asyncio.to_thread(
        requests.get,
        LASTFM_API_URL,
        params={
            "method": "user.getrecenttracks",
            "user": username,
            "api_key": get_config().lastfm_api_key,
        },
        timeout=20,
    )
    headers = "\n".join(f"{key}: {value}" for key, value in response.headers.items())
    caption = html.escape(f"HTTP {response.status_code} {response.reason}\n{headers}")[:1024]
    document = io.BytesIO(response.content)
    document.name = f"{username}-recent-tracks.xml"
    await message.reply_document(document, caption=caption)


@client.on_inline_query()
async def inline_handler(bot, inline_query: InlineQuery) -> None:
    avatar_path = await _download_avatar(bot, inline_query)
    try:
        results = []
        lastfm = await get_db().get_lastfm_user(inline_query.from_user.id)
        if lastfm:
            try:
                payload = await asyncio.to_thread(
                    _lastfm_payload,
                    lastfm.lastfm_username,
                    get_config().lastfm_api_key,
                    inline_query.from_user.first_name or "Someone",
                    avatar_path,
                )
                results.extend(await _upload_lastfm_results(bot, inline_query.id, payload))
            except Exception:
                pass
        if not results:
            text = (
                "Set your Last.fm username first."
                if lastfm is None
                else "You are not playing anything... Check Spotify connection?"
            )
            results = [
                InlineQueryResultArticle(
                    id="none",
                    title=text,
                    description=text,
                    input_message_content=InputTextMessageContent(text),
                )
            ]
        await _safe_answer(
            inline_query,
            results,
            cache_time=CACHE_TIME_SECONDS,
            is_gallery=True,
            is_personal=True,
        )
    except pyrogram.errors.QueryIdInvalid:
        pass
    except Exception:
        await _safe_answer(
            inline_query,
            [
                InlineQueryResultArticle(
                    id="failed",
                    title="Could not generate card",
                    input_message_content=InputTextMessageContent(
                        "Could not generate a card right now."
                    ),
                )
            ],
            cache_time=CACHE_TIME_SECONDS,
            is_personal=True,
        )
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
    candidates = search_artwork_candidates(
        now["artist"], now["track"], now["album"], now["lastfm_image_url"]
    )
    selected = candidates[:MAX_ARTWORK_CANDIDATES]
    payload = []
    for variant, artwork_url in selected:
        artwork_img = download_image(artwork_url) if artwork_url else None
        image = render_card(
            display_name,
            now["track"],
            now["artist"],
            now["album"],
            avatar_path,
            artwork_img,
        )
        payload.append((variant, _png_bytes(image), f"{now['artist']} - {now['track']}"))
    return payload


def _png_bytes(image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def _upload_lastfm_results(
    bot, query_id: str, payload: list[tuple[str, bytes, str]]
) -> list:
    results = []
    for label, image, _ in payload:
        results.append(await _upload_photo(bot, query_id, label, image))
        results.append(await _upload_sticker(bot, query_id, label, image))
    return results


async def _upload_photo(bot, query_id: str, label: str, image_bytes: bytes):
    photo = io.BytesIO(image_bytes)
    photo.name = f"{label.lower()}-{query_id}.png"
    sent = await bot.send_photo(
        chat_id=get_config().storage_chat_id,
        photo=photo,
        caption=f"{label}: card",
    )
    return InlineQueryResultCachedPhoto(
        id=f"{label.lower()}-photo-{query_id}",
        photo_file_id=sent.photo.file_id,
        title=f"{label} card",
    )


async def _upload_sticker(bot, query_id: str, label: str, image_bytes: bytes):
    sticker = io.BytesIO(_sticker_bytes(image_bytes))
    sticker.name = f"{label.lower()}-{query_id}.webp"
    sent = await bot.send_sticker(
        chat_id=get_config().storage_chat_id,
        sticker=sticker,
    )
    return InlineQueryResultCachedSticker(
        id=f"{label.lower()}-sticker-{query_id}",
        sticker_file_id=sent.sticker.file_id,
    )


def _sticker_bytes(image_bytes: bytes) -> bytes:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    image.thumbnail((512, 512))
    output = io.BytesIO()
    image.save(output, format="WEBP", lossless=True)
    return output.getvalue()


async def _download_avatar(bot, inline_query: InlineQuery) -> str | None:
    photo = getattr(inline_query.from_user, "photo", None)
    file_id = (photo.big_file_id or photo.small_file_id) if photo else None
    if not file_id:
        return None
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    tmp.close()
    await bot.download_media(file_id, file_name=tmp.name)
    return tmp.name
