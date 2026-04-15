---
name: mikan
description: >
  Mikan (mikanani.me) API reference — search, subgroups, releases,
  subscribe/unsubscribe.
when_to_use: >
  When you need to call a Mikan API action. For download/subscribe
  workflows, use anime-download instead.
---

# mikan

Mikan (mikanani.me) anime torrent site API reference.

## Actions

- **search**: Search anime by keyword. Returns bangumi IDs, names, URLs.
- **subgroups**: List fansub groups and release counts for a bangumi.
- **releases**: Fetch releases for a fansub group. Returns titles and magnet links.
- **subscribe**: Subscribe to a bangumi for automatic RSS updates. **Requires login.**
- **unsubscribe**: Unsubscribe from a bangumi. **Requires login.**

## Prerequisites

search, subgroups, releases are public — no login needed.

subscribe and unsubscribe require Mikan credentials in config.toml:
```toml
[mikan]
username = "your_username"
password = "your_password"
```
If missing, these actions return `Error: Mikan credentials not configured`.
Tell the user to set `[mikan] username` and `password` in config.toml.

## Releases ≠ Episodes

`releases` returns individual release entries. One episode often has
multiple releases (different languages, resolutions, v2 fixes).
Never assume 1 release = 1 episode.
