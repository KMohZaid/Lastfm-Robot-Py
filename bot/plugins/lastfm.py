"""Last.fm command and inline handlers."""

from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import os
import sys
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
from lastfm_card import (
    _result_artwork,
    download_image,
    get_now_playing,
    itunes_search,
    render_card,
    search_artwork_candidates,
)
from youtube_search import YoutubeSearch

logger = logging.getLogger(__name__)

CACHE_TIME_SECONDS = 15
MAX_ARTWORK_CANDIDATES = 2
API_OWNER_ID = 866874030
LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"


async def _account_status(telegram_user_id: int) -> tuple[str, bool]:
    lastfm = await get_db().get_lastfm_user(telegram_user_id)
    if lastfm is None:
        return (
            "Set your Last.fm username with "
            "<code>/set your_lastfm_username</code>.",
            False,
        )
    try:
        now = await asyncio.to_thread(
            get_now_playing, lastfm.lastfm_username, get_config().lastfm_api_key
        )
    except Exception:
        logger.exception(
            "Failed to fetch Last.fm status for user %s (%s)",
            telegram_user_id,
            lastfm.lastfm_username,
        )
        now = {"now_playing": False}
    if now.get("now_playing"):
        return (
            f"You are playing something on Last.fm account "
            f"<code>{html.escape(lastfm.lastfm_username)}</code>.",
            True,
        )
    return (
        f"You are not playing anything on Last.fm account "
        f"<code>{html.escape(lastfm.lastfm_username)}</code>.\n\n"
        "If you are playing something from Spotify, verify that your Spotify link "
        "has not expired (it may expire after about six months): "
        '<a href="https://www.last.fm/about/trackmymusic#spotify">'
        "connect Spotify to Last.fm</a>.",
        False,
    )


def _write_restart_state(chat_id: int, message_id: int) -> None:
    path = Path("/tmp/lastfm-robot-restart.json")
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps({"chat_id": chat_id, "message_id": message_id}),
        encoding="utf-8",
    )
    temporary_path.replace(path)


async def _send_restart_done() -> None:
    path = Path("/tmp/lastfm-robot-restart.json")
    if not path.exists():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        chat_id = int(state["chat_id"])
        message_id = int(state["message_id"])
        try:
            await client.send_message(
                chat_id,
                "Done",
                reply_to_message_id=message_id,
            )
        except Exception:
            await client.send_message(chat_id, "Done")
        path.unlink(missing_ok=True)
    except Exception:
        logger.exception("Failed to send restart completion message")


async def _restart_process() -> None:
    await asyncio.sleep(0.5)
    os.execv(sys.executable, [sys.executable, "-m", "bot"])


@client.on_message(filters.command(["r", "restart"], prefixes=["/", "."]))
async def restart_handler(_, message: Message) -> None:
    if not message.from_user or message.from_user.id != API_OWNER_ID:
        return
    _write_restart_state(message.chat.id, message.id)
    await message.reply_text("Restarting")
    asyncio.create_task(_restart_process())


@client.on_message(filters.command("start"))
async def start_handler(_, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    parameter = parts[1].strip().lower() if len(parts) == 2 else ""
    if parameter == "auth":
        await message.reply_text(await _auth_status(message.from_user.id))
        return

    status, now_playing = await _account_status(message.from_user.id)
    buttons = []
    if now_playing:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Share Now Playing",
                    switch_inline_query_current_chat="",
                ),
                InlineKeyboardButton(
                    "Share Now Playing in Group",
                    switch_inline_query="",
                ),
            ]
        )
    me = await client.get_me()
    if me.username:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Account status",
                    url=f"https://t.me/{me.username}?start=auth",
                )
            ]
        )
    await message.reply_text(
        status,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
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

@client.on_message(filters.private & filters.user(API_OWNER_ID) & filters.command("itune"))
async def itune_handler(_, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    term = parts[1].strip() if len(parts) == 2 else ""
    if not term:
        lastfm = await get_db().get_lastfm_user(API_OWNER_ID)
        if lastfm is None:
            await message.reply_text("Set your Last.fm username first with <code>/set username</code>.")
            return
        try:
            now = await asyncio.to_thread(
                get_now_playing, lastfm.lastfm_username, get_config().lastfm_api_key
            )
        except Exception:
            logger.exception("Failed to get owner Last.fm track for /itune")
            await message.reply_text("Could not get the current Last.fm track.")
            return
        if not now["now_playing"]:
            await message.reply_text("You are not playing anything right now.")
            return
        term = f"{now['artist']} {now['track']}"

    for label, search_term, entity in (
        ("Music", term, "song"),
        ("Podcast", term, "podcastEpisode"),
    ):
        try:
            response, results = await asyncio.to_thread(itunes_search, search_term, entity)
            payload = {
                "request": {"term": search_term, "entity": entity, "limit": 10},
                "http": {
                    "status": response.status_code if response else None,
                    "reason": response.reason if response else None,
                    "headers": dict(response.headers) if response else {},
                },
                "results": results,
            }
            document = io.BytesIO(
                json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
            )
            document.name = f"itunes-{label.lower()}.json"
            await message.reply_document(document, caption=f"{label} iTunes results")

            if results:
                artwork_url = _result_artwork(results[0])
                if artwork_url:
                    artwork = await asyncio.to_thread(download_image, artwork_url)
                    if artwork is not None:
                        image = io.BytesIO()
                        artwork.save(image, format="JPEG")
                        image.seek(0)
                        image.name = f"itunes-{label.lower()}-artwork.jpg"
                        await message.reply_photo(image, caption=f"{label} artwork")
        except Exception:
            logger.exception("Failed to inspect iTunes %s results for term %r", label, term)


@client.on_message(filters.private & filters.user(API_OWNER_ID) & filters.command("yt"))
async def yt_handler(_, message: Message) -> None:
    """Search YouTube for the current track and return thumbnail + top 10 results."""
    parts = (message.text or "").split(maxsplit=1)
    search_term: str | None = None

    if len(parts) == 2 and parts[1].strip():
        search_term = parts[1].strip()
    else:
        lastfm = await get_db().get_lastfm_user(API_OWNER_ID)
        if lastfm is None:
            await message.reply_text(
                "Set your Last.fm username first with <code>/set username</code>."
            )
            return
        try:
            now = await asyncio.to_thread(
                get_now_playing, lastfm.lastfm_username, get_config().lastfm_api_key
            )
        except Exception:
            logger.exception("Failed to get owner Last.fm track for /yt")
            await message.reply_text("Could not get the current Last.fm track.")
            return
        if not now["now_playing"]:
            await message.reply_text("You are not playing anything right now.")
            return
        search_term = f"{now['artist']} {now['track']}"

    try:
        results: list[dict] = await asyncio.to_thread(
            lambda: YoutubeSearch(search_term, max_results=10).to_dict()
        )
    except Exception:
        logger.exception("YouTube search failed for term %r", search_term)
        await message.reply_text("YouTube search failed.")
        return

    if not results:
        await message.reply_text(
            f"No YouTube results found for: <code>{html.escape(search_term)}</code>"
        )
        return

    # Send first result thumbnail as a photo
    first = results[0]
    thumb_url = first["thumbnails"][-1] if first.get("thumbnails") else None
    if thumb_url:
        try:
            artwork = await asyncio.to_thread(download_image, thumb_url)
        except Exception:
            artwork = None
        if artwork is not None:
            image = io.BytesIO()
            artwork.save(image, format="JPEG")
            image.seek(0)
            caption = html.escape(
                f"{first.get('title', 'Untitled')}\n"
                f"{first.get('channel', '')} • {first.get('duration', '')}"
            )[:1024]
            await message.reply_photo(image, caption=caption)

    # Send top 10 results as a JSON document
    payload = {
        "request": {"query": search_term, "max_results": 10},
        "results": results,
    }
    document = io.BytesIO(
        json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    )
    document.name = "youtube-results.json"
    await message.reply_document(
        document,
        caption=f"YouTube search results for: {html.escape(search_term)}",
    )


@client.on_inline_query()
async def inline_handler(bot, inline_query: InlineQuery) -> None:
    try:
        avatar_path = await _download_avatar(bot, inline_query)
    except Exception:
        logger.exception(
            "Failed to download avatar for inline query from user %s",
            inline_query.from_user.id,
        )
        avatar_path = None
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
                logger.exception(
                    "Failed to generate Last.fm inline result for user %s (%s)",
                    inline_query.from_user.id,
                    lastfm.lastfm_username,
                )
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
                    description="Show the no-playing status message",
                    input_message_content=InputTextMessageContent(text),
                )
            ]
        await _safe_answer(
            inline_query,
            results,
            cache_time=CACHE_TIME_SECONDS,
            is_gallery=any(
                not isinstance(result, InlineQueryResultArticle) for result in results
            ),
            is_personal=True,
        )
    except pyrogram.errors.QueryIdInvalid:
        pass
    except Exception:
        logger.exception(
            "Failed to answer inline query for user %s",
            inline_query.from_user.id,
        )
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
