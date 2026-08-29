# Implementation summary

## Last.fm-only bot

- Stores each Telegram user's Last.fm username in PostgreSQL.
- `/set` configures the username, `/auth` reports the configured account, and `/start` provides setup and status guidance.
- `/start auth` is a deep-link equivalent of `/auth`; the regular `/start` response includes an account-status button.
- Inline queries fetch Last.fm now-playing data and generate up to three artwork variants. Each variant produces both a cached photo and sticker, for up to six results.
- Artwork labels are separated into `Music` and `Podcast` candidates. Last.fm artwork matching uses iTunes conservatively, preferring music and matching podcast episode numbers when available.
- When no activity is available, inline results say: `You are not playing anything... Check Spotify connection?`
- Users are directed to connect Spotify to Last.fm at https://www.last.fm/about/trackmymusic#spotify. The status message notes that this connection may expire after about six months.

## Owner API export

The private, owner-only `/api` command fetches `user.getrecenttracks` from Last.fm. It uses the configured owner username when no argument is supplied, or the supplied username when `/api {username}` is used. The request omits `format=json`, so Last.fm returns XML; the response is sent as an XML document with HTTP status and response headers in the caption.

## Configuration

Required environment variables are `BOT_TOKEN`, `API_ID`, `API_HASH`, `LASTFM_API_KEY`, `DATABASE_URL`, and `STORAGE_CHAT_ID`. Optional variables are `LASTFM_SHOW_BOTH_EVEN_WHEN_FOUND_ONE` and `LOG_LEVEL`.
