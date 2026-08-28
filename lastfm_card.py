#!/usr/bin/env python3
"""
lastfm_card.py — Generate a Discord-"Listening to"-style now-playing card
from a Last.fm scrobble, using iTunes for artwork (since Last.fm artwork
is unreliable), for sharing in Telegram.

Usage:
    python3 lastfm_card.py --user YOUR_LASTFM_USERNAME --api-key YOUR_LASTFM_API_KEY \
        --name "K" [--avatar path/to/avatar.png] [--output card.png]

Get a free Last.fm API key at: https://www.last.fm/api/account/create
"""

import argparse
import io
import os
import re
import subprocess
import sys
import textwrap
import urllib.parse
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

def _find_font(candidates: list[str]) -> str | None:
    """Return the first existing font path from a list of candidates."""
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _find_font_via_fontconfig(family: str, bold: bool) -> str | None:
    """Ask fontconfig to resolve a font family to an actual file on disk.
    This is the most reliable method since font layouts vary a lot
    across distros (e.g. Arch drops DejaVu flat into .../fonts/TTF/,
    Debian nests it under .../fonts/truetype/dejavu/)."""
    style = "bold" if bold else "regular"
    for pattern in (f"{family}:style={style}", family if not bold else f"{family}:bold"):
        try:
            out = subprocess.run(
                ["fc-match", "-f", "%{file}", pattern],
                capture_output=True, text=True, timeout=5,
            )
            path = out.stdout.strip()
            if path and os.path.isfile(path):
                return path
        except Exception:
            continue
    return None


_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/LiberationSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/NotoSans/NotoSans-Bold.ttf",
]
_REGULAR_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/LiberationSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/NotoSans/NotoSans-Regular.ttf",
]

# fontconfig first (handles any distro's actual layout), hardcoded
# paths as a fallback for minimal systems without fontconfig installed.
FONT_BOLD = (
    _find_font_via_fontconfig("DejaVu Sans", bold=True)
    or _find_font_via_fontconfig("sans-serif", bold=True)
    or _find_font(_BOLD_CANDIDATES)
)
FONT_REGULAR = (
    _find_font_via_fontconfig("DejaVu Sans", bold=False)
    or _find_font_via_fontconfig("sans-serif", bold=False)
    or _find_font(_REGULAR_CANDIDATES)
)

if FONT_BOLD is None or FONT_REGULAR is None:
    sys.exit(
        "Could not find a usable TTF font on this system.\n"
        "Install one, e.g.:\n"
        "  Arch:          pacman -S ttf-dejavu\n"
        "  Debian/Ubuntu: apt install fonts-dejavu-core\n"
        "Then re-run this script."
    )

# ---- Card geometry (tweak to taste) ----
CARD_W = 600
CARD_H = 300
PAD = 24
TOP_H = 115              # green header height
ART_SIZE = 150           # album art square size
GREEN_TOP = (29, 185, 84)     # spotify-ish green
GREEN_BOTTOM = (22, 145, 66)  # slightly darker green for bottom band
WHITE = (255, 255, 255)
WHITE_DIM = (235, 245, 238)


EPISODE_RE = re.compile(r"\bep(?:isode)?[^\d]*(\d+)\b", re.IGNORECASE)


def _track_text(value) -> str:
    if isinstance(value, dict):
        return value.get("#text") or value.get("name") or ""
    return str(value or "")


def _first_track(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value if isinstance(value, dict) else None


def extract_episode_number(text: str) -> str | None:
    match = EPISODE_RE.search(text or "")
    return match.group(1) if match else None


def _itunes_search_results(term: str, entity: str, limit: int = 10) -> list[dict]:
    try:
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": term, "entity": entity, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except (requests.RequestException, ValueError):
        return []


def search_artwork_candidates(
    artist: str, track: str, album: str, lastfm_fallback: str | None
) -> list[tuple[str, str | None]]:
    """Return conservative music matches, or podcast matches when needed."""
    number = extract_episode_number(" ".join((track, album)))
    candidates: list[tuple[str, str | None]] = []
    music_terms = [
        f"{album} {artist}" if album else f"{artist} {track}",
        f"{artist} {track}",
        f"{track} {album} {artist}" if album else f"{track} {artist}",
    ]
    for term in music_terms:
        for result in _itunes_search_results(term, "song"):
            name = str(result.get("trackName", "")).casefold()
            result_artist = str(result.get("artistName", "")).casefold()
            if not name or track.casefold() not in name and name not in track.casefold():
                continue
            if artist and artist.casefold() not in result_artist and result_artist not in artist.casefold():
                continue
            candidate = ("Music", _result_artwork(result) or lastfm_fallback)
            if candidate not in candidates:
                candidates.append(candidate)
        if candidates:
            break

    episode_terms = []
    if number:
        episode_terms.extend(
            (
                f"{album} episode {number}" if album else f"{artist} episode {number}",
                f"{album} episode {number} {track}" if album else f"{artist} episode {number} {track}",
            )
        )
    episode_terms.append(f"{track} {album} {artist}" if album else f"{artist} {track}")
    for term in episode_terms:
        for result in _itunes_search_results(term, "podcastEpisode"):
            name = str(result.get("trackName", ""))
            collection = str(result.get("collectionName", ""))
            haystack = f"{name} {collection}".casefold()
            if number and number not in haystack:
                continue
            if track and track.casefold() not in haystack and not number:
                continue
            candidate = ("Podcast", _result_artwork(result) or lastfm_fallback)
            if candidate not in candidates:
                candidates.append(candidate)
        if any(kind == "Podcast" for kind, _ in candidates):
            break
    return candidates


def search_artwork(
    artist: str, track: str, album: str, lastfm_fallback: str | None
) -> tuple[str, str | None] | None:
    """Choose the best music or podcast artwork match."""
    candidates = search_artwork_candidates(artist, track, album, lastfm_fallback)
    return candidates[0] if candidates else None


def _result_artwork(result: dict) -> str | None:
    art_url = result.get("artworkUrl600") or result.get("artworkUrl100") or result.get("artworkUrl60") or result.get("artworkUrl30")
    return _upsize_artwork(art_url) if art_url else None


def get_now_playing(username: str, api_key: str) -> dict:
    """Fetch the current/most recent scrobble from Last.fm."""
    resp = requests.get(
        "https://ws.audioscrobbler.com/2.0/",
        params={"method": "user.getrecenttracks", "user": username, "api_key": api_key, "format": "json", "limit": 1},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    track = _first_track(data.get("recenttracks", {}).get("track"))
    if track is None:
        raise RuntimeError("No recent tracks found for this Last.fm user.")
    images = track.get("image", [])
    if isinstance(images, dict):
        images = [images]
    image_url = next((image.get("#text") for image in reversed(images) if image.get("#text")), None)
    return {
        "track": _track_text(track.get("name")) or "Unknown Track",
        "artist": _track_text(track.get("artist")) or "Unknown Artist",
        "album": _track_text(track.get("album")),
        "now_playing": track.get("@attr", {}).get("nowplaying") == "true",
        "lastfm_image_url": image_url,
    }


def _upsize_artwork(url: str, size: int = 632) -> str:
    """iTunes artwork URLs end in e.g. '100x100bb.jpg' or '600x600bb.jpg' --
    swap that for a larger size regardless of which one we got."""
    return ARTWORK_SIZE_RE.sub(f"{size}x{size}bb", url)


def _itunes_search(term: str, entity: str) -> dict | None:
    url = "https://itunes.apple.com/search"
    params = {"term": term, "entity": entity, "limit": 1}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0] if results else None
    except requests.RequestException:
        return None


def get_artwork_url(artist: str, track: str, entity: str, lastfm_fallback: str | None) -> str | None:
    """Look up high-res artwork via the iTunes Search API for a specific
    entity type ('song' or 'podcastEpisode'). If iTunes has no match,
    falls back to Last.fm's own image URL for this track (which is
    sometimes a generic placeholder, but better than nothing).
    """
    term = f"{artist} {track}"
    result = _itunes_search(term, entity)

    if result is not None:
        # Different entities expose different artwork fields/sizes; take
        # whichever is present and upscale it.
        art_url = (
            result.get("artworkUrl600")
            or result.get("artworkUrl100")
            or result.get("artworkUrl60")
            or result.get("artworkUrl30")
        )
        if art_url:
            return _upsize_artwork(art_url)

    return lastfm_fallback


def download_image(url: str) -> Image.Image | None:
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception:
        return None


def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size[0], size[1]], radius=radius, fill=255)
    return mask


def fit_font(draw, text, font_path, max_width, start_size, min_size=18):
    size = start_size
    while size > min_size:
        font = ImageFont.truetype(font_path, size)
        w = draw.textlength(text, font=font)
        if w <= max_width:
            return font
        size -= 2
    return ImageFont.truetype(font_path, min_size)


def wrap_to_width(draw, text, font, max_width):
    """Wrap text to fit max_width, returns list of lines (max 2)."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > 2:
        lines = lines[:2]
        while draw.textlength(lines[1] + "…", font=font) > max_width and len(lines[1]) > 1:
            lines[1] = lines[1][:-1]
        lines[1] = lines[1].rstrip() + "…"
    return lines


def build_avatar(avatar_path: str | None, display_name: str, size: int) -> Image.Image:
    if avatar_path:
        try:
            img = Image.open(avatar_path).convert("RGB")
            img = ImageOps.fit(img, (size, size), Image.LANCZOS, centering=(0.5, 0.5))
        except Exception:
            img = None
    else:
        img = None

    if img is None:
        # Fallback: colored square with the first letter of the display name
        img = Image.new("RGB", (size, size), (255, 255, 255))
        d = ImageDraw.Draw(img)
        letter = (display_name or "?").strip()[0].upper()
        font = ImageFont.truetype(FONT_BOLD, int(size * 0.5))
        bbox = d.textbbox((0, 0), letter, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(
            ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
            letter,
            font=font,
            fill=GREEN_TOP,
        )

    mask = rounded_mask((size, size), int(size * 0.18))
    out = Image.new("RGBA", (size, size))
    out.paste(img, (0, 0), mask)
    return out


def render_card(display_name: str, track: str, artist: str, album: str,
                 avatar_path: str | None, artwork_img: Image.Image | None) -> Image.Image:
    card = Image.new("RGB", (CARD_W, CARD_H), GREEN_BOTTOM)
    draw = ImageDraw.Draw(card)

    # --- Top header band ---
    draw.rectangle([0, 0, CARD_W, TOP_H], fill=GREEN_TOP)

    avatar_d = 80
    avatar = build_avatar(avatar_path, display_name, avatar_d)
    avatar_x, avatar_y = PAD, (TOP_H - avatar_d) // 2
    card.paste(avatar, (avatar_x, avatar_y), avatar)

    text_x = avatar_x + avatar_d + 20
    name_font = ImageFont.truetype(FONT_BOLD, 32)
    sub_font = ImageFont.truetype(FONT_BOLD, 30)

    name_bbox = draw.textbbox((0, 0), display_name, font=name_font)
    name_h = name_bbox[3] - name_bbox[1]
    sub_bbox = draw.textbbox((0, 0), "is now listening to", font=sub_font)
    sub_h = sub_bbox[3] - sub_bbox[1]
    gap = 8
    block_h = name_h + gap + sub_h
    top_y = (TOP_H - block_h) // 2

    draw.text((text_x, top_y - name_bbox[1]), display_name, font=name_font, fill=WHITE)
    draw.text(
        (text_x, top_y + name_h + gap - sub_bbox[1]),
        "is now listening to",
        font=sub_font,
        fill=WHITE,
    )

    # --- Bottom info band ---
    art_x, art_y = PAD, TOP_H + (CARD_H - TOP_H - ART_SIZE) // 2
    if artwork_img is not None:
        art = ImageOps.fit(artwork_img, (ART_SIZE, ART_SIZE), Image.LANCZOS)
    else:
        art = Image.new("RGB", (ART_SIZE, ART_SIZE), (60, 60, 60))
    mask = rounded_mask((ART_SIZE, ART_SIZE), 9)
    card.paste(art, (art_x, art_y), mask)

    info_x = art_x + ART_SIZE + 20
    info_w = CARD_W - info_x - PAD

    track_font = fit_font(draw, track, FONT_BOLD, info_w, 26, 14)
    artist_font = ImageFont.truetype(FONT_REGULAR, 21)
    album_font = ImageFont.truetype(FONT_REGULAR, 15)

    track_lines = wrap_to_width(draw, track, track_font, info_w)
    artist_text = f"by {artist}"
    artist_lines = wrap_to_width(draw, artist_text, artist_font, info_w)
    album_text = f"on {album}" if album else ""
    album_lines = wrap_to_width(draw, album_text, album_font, info_w) if album_text else []

    cy = art_y + 5
    for line in track_lines:
        draw.text((info_x, cy), line, font=track_font, fill=WHITE)
        cy += track_font.size + 4
    cy += 3
    for line in artist_lines:
        draw.text((info_x, cy), line, font=artist_font, fill=WHITE_DIM)
        cy += artist_font.size + 3
    cy += 2
    for line in album_lines:
        draw.text((info_x, cy), line, font=album_font, fill=WHITE_DIM)
        cy += album_font.size + 3

    return card


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--user", required=True, help="Last.fm username")
    p.add_argument("--api-key", required=True, help="Last.fm API key")
    p.add_argument("--name", required=True, help="Display name shown at top (e.g. 'K')")
    p.add_argument("--avatar", default=None, help="Path to an avatar image (optional)")
    p.add_argument("--output", default="card.png",
                    help="Output PNG path for the song version. The podcast "
                         "version is saved alongside it with a 'podcast-' "
                         "prefix (e.g. card.png -> podcast-card.png)")
    args = p.parse_args()

    output_path = Path(args.output)
    podcast_output_path = output_path.with_name(f"podcast-{output_path.name}")

    now = get_now_playing(args.user, args.api_key)

    # We can't reliably tell from Last.fm's data alone whether this
    # scrobble is a song or a podcast episode, so resolve artwork both
    # ways and produce both cards -- whichever looks right, use it.
    song_art_url = get_artwork_url(now["artist"], now["track"], "song", now["lastfm_image_url"])
    podcast_art_url = get_artwork_url(now["artist"], now["track"], "podcastEpisode", now["lastfm_image_url"])

    song_img = download_image(song_art_url) if song_art_url else None
    podcast_img = download_image(podcast_art_url) if podcast_art_url else None

    common = dict(
        display_name=args.name,
        track=now["track"],
        artist=now["artist"],
        album=now["album"],
        avatar_path=args.avatar,
    )

    render_card(**common, artwork_img=song_img).save(output_path)
    print(f"Saved: {output_path}")

    render_card(**common, artwork_img=podcast_img).save(podcast_output_path)
    print(f"Saved: {podcast_output_path}")

    if not now["now_playing"]:
        print("Note: this was the most recent scrobble, not a currently-playing track.")


if __name__ == "__main__":
    main()
