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
- **create_download**: Submit a download task by magnet/torrent URL.
- **add_rss**: Add a new RSS feed URL for automatic monitoring.
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
