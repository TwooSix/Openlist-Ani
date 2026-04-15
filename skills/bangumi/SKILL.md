---
name: bangumi
description: >
  Bangumi (bgm.tv) API reference — search, subject detail, calendar,
  user collections, related subjects.
when_to_use: >
  When you need to call a Bangumi API action. For search/discovery
  workflows, use anime-search instead.
---

# bangumi

Bangumi (bgm.tv) API reference.

## Actions

- **search**: Search anime/manga by keyword with filters (type, tag, date, rating).
- **subject_detail**: Get full details for a specific Bangumi subject.
- **calendar**: View the weekly anime airing schedule.
- **related_subjects**: Find sequels, prequels, and related works.
- **reviews**: Fetch community discussion topics and blog reviews for a subject.
- **user_collections**: List the user's collection by status. **Requires token.**
- **update_collection**: Update collection status, rating, comment, or episode progress. **Requires token.**

## Prerequisites

search, subject_detail, calendar, related_subjects, reviews are public APIs — no token needed.

user_collections and update_collection require a Bangumi access token configured in config.toml:
```toml
[bangumi]
access_token = "your_bangumi_token"
```
If missing, these actions return `Error: Bangumi access token not configured`.
Tell the user to set `[bangumi] access_token` in config.toml.

## Data Types

Collection types: 1=wish(想看), 2=done(看过), 3=doing(在看), 4=on_hold(搁置), 5=dropped(抛弃)
Subject types: 1=book, 2=anime, 3=music, 4=game, 6=real
