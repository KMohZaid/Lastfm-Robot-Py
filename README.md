# Last.fm Robot

A Telegram bot that lets users link their Last.fm username and share a now-playing card through inline queries. It renders track artwork using YouTube search results and stores Telegram-to-Last.fm username mappings in PostgreSQL.

## Features

- Set and check a Last.fm username with `/set` and `/auth`.
- Use `/start` for setup and playback status; `/start auth` opens account status.
- Share the current Last.fm track as an inline photo or sticker card.
- Find track artwork from YouTube thumbnails, preferring square images where available.
- Provide owner-only diagnostics for Last.fm recent tracks, iTunes music/podcast search, and YouTube search.
- Run locally with Python or in Docker Compose.

## Requirements

- Python 3.11 or newer (the included Dockerfile uses Python 3.12).
- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- Telegram API ID and hash from [my.telegram.org](https://my.telegram.org).
- A Last.fm API key from [Last.fm API](https://www.last.fm/api).
- A PostgreSQL server and database credentials.
- A Telegram chat ID where the bot can store uploaded inline card media (for example, a private channel the bot can post to).

## Configuration

The bot reads configuration from environment variables. If a `.env` file exists in the working directory, it is loaded automatically. Do not commit `.env` or share its values.

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | Yes | Telegram bot token. |
| `API_ID` | Yes | Telegram application API ID (integer). |
| `API_HASH` | Yes | Telegram application API hash. |
| `LASTFM_API_KEY` | Yes | Last.fm API key. |
| `DATABASE_URL` | Yes | PostgreSQL connection URL, including a database name. |
| `STORAGE_CHAT_ID` | Yes | Chat ID used to store uploaded photo and sticker media for inline results. |
| `LASTFM_SHOW_BOTH_EVEN_WHEN_FOUND_ONE` | No | Optional boolean setting; defaults to `True`. |
| `LOG_LEVEL` | No | Python logging level; defaults to `INFO`. |

Example `.env` (replace the placeholders locally; do not use these values as credentials):

```dotenv
BOT_TOKEN=replace_with_bot_token
API_ID=123456
API_HASH=replace_with_api_hash
LASTFM_API_KEY=replace_with_lastfm_api_key
DATABASE_URL=postgresql://username:password@localhost:5432/lastfm_robot
STORAGE_CHAT_ID=-1001234567890
LOG_LEVEL=INFO
```

The database role must have permission to connect to PostgreSQL and create the configured database if it does not already exist. The bot creates its `users` table on startup. Keep the credentials in a protected environment or secret manager for deployed environments.

## Run locally

From the repository root, create and activate a virtual environment, install the project, provide the configuration above, and start the bot:

```sh
python -m venv .venv
. .venv/bin/activate
pip install .
python -m bot
```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1` instead. The bot connects to PostgreSQL during startup; it will not start without all required configuration values and a reachable database.

## Run with Docker Compose

The included Compose service builds the image and reads settings from `.env`:

```sh
# Create and fill in .env first
docker compose up --build -d
```

View logs or stop the service with:

```sh
docker compose logs -f bot
docker compose down
```

Ensure the bot can access the configured PostgreSQL host from its container network. The included Compose file defines an internal Docker network but does not start a PostgreSQL service; use an existing database or add/configure one for your deployment. The source directory is mounted into `/app` by the Compose configuration.

## Telegram commands

| Command | Who can use it | Description |
| --- | --- | --- |
| `/start` | Everyone | Shows setup guidance and current Last.fm playback status; when playing, offers inline sharing buttons. |
| `/start auth` | Everyone | Shows the configured Last.fm account status. |
| `/set <username>` | Everyone | Saves the sender's Last.fm username. |
| `/auth` | Everyone | Displays the sender's configured Last.fm username or setup instructions. |
| `/api [username]` | Bot owner, private chat | Sends the Last.fm recent-tracks response as XML with HTTP status and response headers. Without a username, uses the owner's configured Last.fm username. |
| `/itune [search term]` | Bot owner, private chat | Searches iTunes Music and Podcast results, sends JSON diagnostics and available artwork; a supplied search term also produces card previews. Without a term, uses the owner's current Last.fm track. |
| `/yt [query]` | Bot owner, private chat | Searches YouTube, sends the first result thumbnail, up to ten results as JSON, and a card preview when a query is supplied. Without a query, searches for the owner's current Last.fm track. |
| `/r` or `/restart` | Bot owner | Restarts the bot process. |

The owner-only commands are restricted to the Telegram account ID configured in the source (`API_OWNER_ID` in `bot/plugins/lastfm.py`); they are not general-purpose user commands.

## Inline sharing

After setting a Last.fm username, invoke the bot inline in any chat (for example, type `@your_bot_username` in a message field). When Last.fm reports a track as currently playing, the bot offers the generated card as a photo and a sticker. `/start` also provides buttons for sharing in the current chat or choosing another chat. If no track is playing, check that Last.fm is receiving listening activity from the connected music service; Spotify scrobbling may need reconnecting if its authorization expired.

The bot uses a configured storage chat to upload media before returning cached Telegram file IDs as inline results. Make sure the bot is a member of that chat and has permission to send photos and stickers.

## Development

Install the project and run the test suite (if tests are present):

```sh
pip install .
python -m pytest
```

Project dependencies and pytest configuration are in `pyproject.toml`.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
