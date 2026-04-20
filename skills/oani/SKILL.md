---
name: oani
description: >
  Openlist-Ani backend API reference — query library, downloads, RSS feeds.
when_to_use: >
  When you need to call an oAni backend API action. For download
  workflows, use anime-download instead.
---

# oani

Openlist-Ani backend API reference.

## Actions

- **query_library**: Execute SELECT queries against the anime resource database.
  Table schema: `resources(id, url, title, anime_name, season, episode, fansub, quality, languages, version, downloaded_at)`
- **list_downloads**: List active/recent download tasks with status.
- **create_download**: Submit a download task by magnet/torrent URL. Requires the
  resource's *real* `title` — never fabricate one (the backend parses it for
  rename).
  **DO NOT call this action directly from a user request.** It is the final
  step of the `anime-download` skill; calling it without going through that
  skill skips library duplicate checks, collection rejection, title
  resolution, and user confirmation. Whenever the user asks to download
  anything (RSS link, magnet link, or just a description), invoke
  `anime-download` instead — it will end up calling `create_download` for you
  at the right time.
- **parse_rss**: Fetch + parse an RSS feed via the backend, returning each
  entry's `title` + `download_url`. Use when the user supplies an RSS URL.
- **resolve_magnet**: Resolve a magnet link to its real `title` (via `dn=` or
  libtorrent metadata) and detect collection releases. Use before calling
  `create_download` whenever a magnet's real title is not already known.
- **resolve_torrent**: Resolve a `.torrent` file URL (http / https) to its
  real `title` and file list by downloading the blob and parsing it with
  libtorrent. Use before calling `create_download` whenever the user
  supplies a `.torrent` URL — do not hand a `.torrent` URL to
  `create_download` without resolving it first.
- **add_rss**: Subscribe to an RSS feed for long-term automatic monitoring —
  the backend will poll it in the background and auto-download NEW entries
  as they appear in the future. **This is NOT how you download the entries
  currently in the feed.** If the user gives you an RSS link and asks to
  "download" it / "Download this RSS" — even if the link is an
  RSS URL — they want the existing entries downloaded now, which is the
  `anime-download` skill (Workflow 1a). Only call `add_rss` when the user
  explicitly asks to "monitor" / "add this rss link" / "auto-download future
  episodes" / "add this feed".
- **list_rss**: Show configured RSS feed URLs and priority settings.

## Prerequisites

All actions require the oAni backend service to be running.
The backend URL is configured in config.toml:
```toml
[backend]
host = "127.0.0.1"
port = 26666
```
If the backend is not running, actions return a connection error.
Tell the user to start the backend service first.
