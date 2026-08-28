"""Spotify Authorization Code client and callback server."""

from __future__ import annotations

import base64
import html
import logging
import secrets
import time
import urllib.parse
from dataclasses import dataclass

import aiohttp
from aiohttp import web

from bot.config import get_config
from bot.database import SpotifyToken, get_db

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"


@dataclass(frozen=True)
class SpotifyNowPlaying:
    name: str
    artists: str
    album: str
    image_url: str | None
    url: str | None
    is_episode: bool = False


_pending_states: dict[str, tuple[int, float]] = {}
_STATE_TTL = 600


def configured() -> bool:
    config = get_config()
    return bool(config.spotify_client_id and config.spotify_client_secret)


def authorization_url(telegram_user_id: int) -> str:
    if not configured():
        raise RuntimeError("Spotify OAuth is not configured")
    now = time.time()
    _pending_states.update({state: value for state, value in _pending_states.items() if value[1] > now})
    state = secrets.token_urlsafe(32)
    _pending_states[state] = (telegram_user_id, now + _STATE_TTL)
    config = get_config()
    params = {
        "client_id": config.spotify_client_id,
        "response_type": "code",
        "redirect_uri": config.spotify_redirect_uri,
        "scope": config.spotify_scopes,
        "state": state,
        "show_dialog": "true",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


async def _request_token(data: dict[str, str]) -> dict:
    config = get_config()
    credentials = base64.b64encode(f"{config.spotify_client_id}:{config.spotify_client_secret}".encode()).decode()
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, data=data, headers={"Authorization": f"Basic {credentials}"}) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(payload.get("error_description", "Spotify token request failed"))
            return payload


async def exchange_code(telegram_user_id: int, code: str) -> SpotifyToken:
    config = get_config()
    payload = await _request_token({"grant_type": "authorization_code", "code": code, "redirect_uri": config.spotify_redirect_uri})
    profile = await api_get("/me", payload["access_token"])
    token = SpotifyToken(telegram_user_id, payload["access_token"], payload.get("refresh_token", ""), time.time() + payload.get("expires_in", 3600), payload.get("scope", ""), profile.get("id"), profile.get("display_name"))
    await get_db().set_spotify_token(token)
    return token


async def access_token(telegram_user_id: int) -> str | None:
    token = await get_db().get_spotify_token(telegram_user_id)
    if token is None:
        return None
    if token.expires_at > time.time() + 60:
        return token.access_token
    if not token.refresh_token:
        return None
    payload = await _request_token({"grant_type": "refresh_token", "refresh_token": token.refresh_token})
    refreshed = SpotifyToken(token.telegram_user_id, payload["access_token"], payload.get("refresh_token", token.refresh_token), time.time() + payload.get("expires_in", 3600), payload.get("scope", token.scope), token.spotify_user_id, token.display_name)
    await get_db().set_spotify_token(refreshed)
    return refreshed.access_token


async def api_get(path: str, token: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_URL}{path}", headers={"Authorization": f"Bearer {token}"}) as response:
            if response.status == 204:
                return {}
            payload = await response.json(content_type=None)
            if response.status >= 400:
                error = payload.get("error", {})
                raise RuntimeError(error.get("message", "Spotify API request failed"))
            return payload


async def currently_playing(telegram_user_id: int) -> SpotifyNowPlaying | None:
    token = await access_token(telegram_user_id)
    if not token:
        return None
    data = await api_get("/me/player/currently-playing", token)
    item = data.get("item")
    if not item or not data.get("is_playing"):
        return None
    is_episode = item.get("type") == "episode"
    artists = ", ".join(a.get("name", "") for a in item.get("artists", []))
    if is_episode:
        artists = item.get("show", {}).get("name", "")
    if is_episode:
        images = item.get("images", []) or item.get("show", {}).get("images", [])
        album = item.get("show", {}).get("name", "")
    else:
        images = item.get("album", {}).get("images", [])
        album = item.get("album", {}).get("name", "")
    image_url = max(images, key=lambda image: image.get("width", 0) * image.get("height", 0), default={}).get("url")
    return SpotifyNowPlaying(item.get("name", ""), artists, album, image_url, item.get("external_urls", {}).get("spotify"), is_episode)


async def callback_handler(request: web.Request) -> web.Response:
    state = request.query.get("state", "")
    pending = _pending_states.pop(state, None)
    if pending is None or pending[1] <= time.time():
        return web.Response(status=400, text="Invalid or expired OAuth state.")
    error = request.query.get("error")
    if error:
        return web.Response(text=f"Spotify authorization cancelled: {html.escape(error)}")
    code = request.query.get("code")
    if not code:
        return web.Response(status=400, text="Spotify did not return an authorization code.")
    try:
        await exchange_code(pending[0], code)
    except Exception:
        logger.exception("Spotify OAuth callback failed")
        return web.Response(status=502, text="Spotify authorization failed. You can close this window.")
    return web.Response(text="Spotify connected successfully. You can close this window.")


async def start_callback_server() -> web.AppRunner:
    config = get_config()
    app = web.Application()
    app.router.add_get("/spotify/callback", callback_handler)
    app.router.add_get("/callback", callback_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.spotify_callback_host, config.spotify_callback_port)
    await site.start()
    logger.info("Spotify callback server listening on %s:%s", config.spotify_callback_host, config.spotify_callback_port)
    return runner
