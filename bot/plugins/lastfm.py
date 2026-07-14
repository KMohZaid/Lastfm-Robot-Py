"""Last.fm command and inline handlers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import logging
import tempfile
import time
import urllib.parse
from pathlib import Path

import pyrogram
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
    download_image,
    get_artwork_url,
    get_now_playing,
    render_card,
)

logger = logging.getLogger(__name__)

CACHE_TIME_SECONDS = 15


class NotNowPlayingError(RuntimeError):
    pass


@client.on_message(filters.command("start"))
async def start_handler(_, message: Message) -> None:
    logger.info("Processing /start from user_id=%s", message.from_user.id)
    await message.reply_text(
        "Set your Last.fm username with:\n"
        "<code>/set your_lastfm_username</code>\n\n"
        "Then use this bot inline to share your current or recent track."
    )


@client.on_message(filters.command("set"))
async def set_handler(_, message: Message) -> None:
    logger.info("Processing /set from user_id=%s", message.from_user.id)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text("Usage: <code>/set your_lastfm_username</code>")
        return

    username = parts[1].strip()
    await get_db().set_lastfm_username(message.from_user.id, username)
    logger.info(
        "Saved Last.fm username for user_id=%s username=%s",
        message.from_user.id,
        username,
    )
    await message.reply_text(f"Saved Last.fm username: <code>{username}</code>")


@client.on_inline_query()
async def inline_handler(bot, inline_query: InlineQuery) -> None:
    started = time.monotonic()
    logger.info(
        "Processing inline query id=%s user_id=%s query=%r",
        inline_query.id,
        inline_query.from_user.id,
        inline_query.query,
    )
    saved_user = await get_db().get_lastfm_user(inline_query.from_user.id)
    if saved_user is None:
        logger.info(
            "Inline query rejected because user_id=%s has no Last.fm username",
            inline_query.from_user.id,
        )
        await _safe_answer(
            inline_query,
            [
                InlineQueryResultArticle(
                    id="setup-required",
                    title="Set your Last.fm username first",
                    description="Send /set your_lastfm_username to the bot.",
                    input_message_content=InputTextMessageContent(
                        "Set your Last.fm username in the bot DM first."
                    ),
                )
            ],
            cache_time=CACHE_TIME_SECONDS,
            is_personal=True,
        )
        return

    try:
        cards = await asyncio.to_thread(
            _build_card_payloads,
            lastfm_username=saved_user.lastfm_username,
            lastfm_api_key=get_config().lastfm_api_key,
            display_name=inline_query.from_user.first_name or "Someone",
            avatar_path=await _download_avatar(bot, inline_query),
        )
        logger.info(
            "Generated %s cards for user_id=%s lastfm_username=%s",
            len(cards),
            inline_query.from_user.id,
            saved_user.lastfm_username,
        )
        photo_results = await asyncio.gather(
            *(
                _upload_card_photo_result(
                    bot,
                    inline_query.id,
                    variant,
                    image_bytes,
                    spotify_query,
                )
                for variant, image_bytes, spotify_query in cards
            )
        )
        sticker_results = await asyncio.gather(
            *(
                _upload_card_sticker_result(
                    bot,
                    inline_query.id,
                    variant,
                    image_bytes,
                )
                for variant, image_bytes, _ in cards
            )
        )
        cached_results = [*photo_results, *sticker_results]

        answered = await _safe_answer(
            inline_query,
            cached_results,
            cache_time=CACHE_TIME_SECONDS,
            is_gallery=True,
            is_personal=True,
        )
        if answered:
            logger.info(
                "Answered inline query id=%s with %s results in %.2fs",
                inline_query.id,
                len(cached_results),
                time.monotonic() - started,
            )
    except pyrogram.errors.QueryIdInvalid:
        logger.warning(
            "Inline query id=%s expired before answer after %.2fs",
            inline_query.id,
            time.monotonic() - started,
        )
    except NotNowPlayingError:
        logger.info(
            "Inline query id=%s user_id=%s has no now-playing track",
            inline_query.id,
            inline_query.from_user.id,
        )
        await _safe_answer(
            inline_query,
            [
                InlineQueryResultArticle(
                    id="not-now-playing",
                    title="You are not listening to anything",
                    description="Try after sometime if you just started playing.",
                    input_message_content=InputTextMessageContent(
                        "You are not listening to anything? Try after sometime if you just started playing"
                    ),
                )
            ],
            cache_time=CACHE_TIME_SECONDS,
            is_personal=True,
        )
    except Exception:
        logger.exception("Inline Last.fm card generation failed")
        await _safe_answer(
            inline_query,
            [
                InlineQueryResultArticle(
                    id="generation-failed",
                    title="Could not generate card",
                    description="Check your Last.fm username or try again later.",
                    input_message_content=InputTextMessageContent(
                        "Could not generate a Last.fm card right now."
                    ),
                )
            ],
            cache_time=CACHE_TIME_SECONDS,
            is_personal=True,
        )


async def _safe_answer(
    inline_query: InlineQuery,
    results: list,
    **kwargs,
) -> bool:
    try:
        await inline_query.answer(results, **kwargs)
        return True
    except pyrogram.errors.QueryIdInvalid:
        logger.warning("Inline query id=%s expired before answer", inline_query.id)
        return False


async def _upload_card_photo_result(
    bot,
    inline_query_id: str,
    variant: str,
    image_bytes: bytes,
    spotify_query: str,
) -> InlineQueryResultCachedPhoto:
    photo = io.BytesIO(image_bytes)
    photo.name = f"lastfm-{variant.lower()}.png"
    photo.seek(0)
    sent = await bot.send_photo(
        chat_id=get_config().storage_chat_id,
        photo=photo,
        caption=f"{variant}: {spotify_query}",
    )
    logger.info(
        "Uploaded %s card for inline query id=%s",
        variant.lower(),
        inline_query_id,
    )
    return InlineQueryResultCachedPhoto(
        id=variant.lower(),
        photo_file_id=sent.photo.file_id,
        title=f"{variant} card",
        reply_markup=_spotify_button(spotify_query),
    )


async def _upload_card_sticker_result(
    bot,
    inline_query_id: str,
    variant: str,
    image_bytes: bytes,
) -> InlineQueryResultCachedSticker:
    sticker = io.BytesIO(_render_sticker_webp_bytes(image_bytes))
    sticker.name = f"lastfm-{variant.lower()}.webp"
    sticker.seek(0)
    sent = await bot.send_sticker(
        chat_id=get_config().storage_chat_id,
        sticker=sticker,
    )
    logger.info(
        "Uploaded %s sticker for inline query id=%s",
        variant.lower(),
        inline_query_id,
    )
    return InlineQueryResultCachedSticker(
        id=f"{variant.lower()}-sticker",
        sticker_file_id=sent.sticker.file_id,
    )


async def _download_avatar(bot, inline_query: InlineQuery) -> str | None:
    user = inline_query.from_user
    file_id = None
    if getattr(user, "photo", None) is not None:
        file_id = user.photo.big_file_id or user.photo.small_file_id
    if file_id is None:
        return None

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    tmp.close()
    await bot.download_media(file_id, file_name=tmp.name)
    return tmp.name


def _build_card_payloads(
    *,
    lastfm_username: str,
    lastfm_api_key: str,
    display_name: str,
    avatar_path: str | None,
) -> list[tuple[str, bytes, str]]:
    try:
        now = get_now_playing(lastfm_username, lastfm_api_key)
        if not now["now_playing"]:
            raise NotNowPlayingError
        spotify_query = f"{now['artist']} - {now['track']}"
        common = {
            "display_name": display_name,
            "track": now["track"],
            "artist": now["artist"],
            "album": now["album"],
            "avatar_path": avatar_path,
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _render_variant,
                    variant,
                    entity,
                    now,
                    common,
                    spotify_query,
                )
                for variant, entity in (
                    ("Normal", "song"),
                    ("Podcast", "podcastEpisode"),
                )
            ]
            return [future.result() for future in futures]
    finally:
        if avatar_path is not None:
            Path(avatar_path).unlink(missing_ok=True)


def _render_variant(
    variant: str,
    entity: str,
    now: dict,
    common: dict,
    spotify_query: str,
) -> tuple[str, bytes, str]:
    art_url = get_artwork_url(
        now["artist"],
        now["track"],
        entity,
        now["lastfm_image_url"],
    )
    artwork_img = download_image(art_url) if art_url else None
    return (
        variant,
        _render_png_bytes(render_card(**common, artwork_img=artwork_img)),
        spotify_query,
    )


def _render_png_bytes(image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _render_sticker_webp_bytes(image_bytes: bytes) -> bytes:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    image.thumbnail((512, 512))
    out = io.BytesIO()
    image.save(out, format="WEBP", lossless=True)
    return out.getvalue()


def _spotify_button(query: str) -> InlineKeyboardMarkup:
    encoded_query = urllib.parse.quote(query)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Search on Spotify",
                    url=f"https://open.spotify.com/search/{encoded_query}",
                )
            ]
        ]
    )
