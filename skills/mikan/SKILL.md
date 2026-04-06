---
name: mikan
description: >
  Mikan (mikanani.me) integration — search anime, subscribe/unsubscribe
  to bangumi releases, view available fansub groups and episode releases.
when_to_use: >
  When the user wants to search for anime on Mikan, subscribe or
  unsubscribe to anime releases, browse fansub groups, or find
  specific episode releases for download.
---

# mikan

Provides access to the Mikan (mikanani.me) anime torrent site for:

- **search**: Search anime by keyword, returns bangumi IDs, names, and URLs.
- **subscribe**: Subscribe to a bangumi for automatic RSS updates (requires Mikan account).
- **unsubscribe**: Unsubscribe from a bangumi.
- **subgroups**: List available fansub groups and their IDs for a specific bangumi.
- **episodes**: Fetch episode releases for a specific fansub group, including full titles and magnet links.

Note: subscribe/unsubscribe require Mikan credentials in config.toml:
```toml
[mikan]
username = "your_username"
password = "your_password"
```
