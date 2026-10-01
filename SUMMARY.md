# Implementation summary

## Last.fm-only bot

- Stores each Telegram user's Last.fm username in PostgreSQL.
- `/set` configures the username, `/auth` reports the configured account, and `/start` provides setup and status guidance.
- `/start auth` is a deep-link equivalent of `/auth`; when playback is active, `/start` also offers `Share Now Playing` buttons for the current chat or another group/chat.
- Inline queries fetch Last.fm now-playing data, use the first YouTube result thumbnail as artwork, and return exactly one cached photo and one cached sticker entry.
- YouTube artwork prefers a square thumbnail from the first few results; if none is available, landscape thumbnails use an adaptive 16:9 artwork layout without cropping or large letterbox bars. Card typography is enlarged, and no progress bar is shown.
- Explicit `/yt` and `/itune` searches also send a rendered card demo from the first result, alongside their diagnostic artwork and JSON responses. iTunes and podcast artwork searches are not used for inline previews.
- When no activity is available, the inline result text is: `You are not playing anything... Check Spotify connection?`
- Users are directed to connect Spotify to Last.fm at https://www.last.fm/about/trackmymusic#spotify. The status message notes that this connection may expire after about six months.
- Owner-only `/r` and `/restart` restart the process and report completion after startup. Unauthorized users are ignored silently.

## Owner API export

- The private, owner-only `/api` command fetches `user.getrecenttracks` from Last.fm. It uses the configured owner username when no argument is supplied, or the supplied username when `/api {username}` is used.
- The request omits `format=json`, so Last.fm returns XML; the response is sent as an XML document with HTTP status and response headers in the caption.

## Owner iTunes diagnostic

- The hidden owner-only `/itune [search term]` command searches both Music (`song`) and Podcast (`podcastEpisode`) entities.
- It sends separate indented JSON response documents and, when available, the first result artwork from each category as a photo.

## Owner YouTube search

- The hidden owner-only `/yt [query]` command searches YouTube using `youtube_search`.
- When no query is supplied, it searches for the owner's currently playing Last.fm track (artist + title).
- It sends the first result's thumbnail as a photo and a JSON document with the top 10 results.

## Configuration

Required environment variables are `BOT_TOKEN`, `API_ID`, `API_HASH`, `LASTFM_API_KEY`, `DATABASE_URL`, and `STORAGE_CHAT_ID`. Optional variables are `LASTFM_SHOW_BOTH_EVEN_WHEN_FOUND_ONE` and `LOG_LEVEL`.
