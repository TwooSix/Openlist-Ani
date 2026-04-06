---
name: oani
description: >
  Openlist-Ani backend operations — query anime download library (SQL),
  list/create downloads, manage RSS feed URLs.
when_to_use: >
  When the user asks about their downloaded anime library (what has been
  downloaded, episodes, fansubs, quality), wants to add an RSS feed,
  create a download, or check download status.
---

# oani

Provides access to the Openlist-Ani backend service for:

- **query_library**: Execute SELECT queries against the anime resource database.
  Table schema: `resources(id, url, title, anime_name, season, episode, fansub, quality, languages, version, downloaded_at)`
- **list_downloads**: List all active/recent download tasks with status.
- **create_download**: Submit a new download task by magnet/torrent URL.
- **add_rss**: Add a new RSS feed URL for automatic monitoring.
- **list_rss**: Show currently configured RSS feed URLs and priority settings.
