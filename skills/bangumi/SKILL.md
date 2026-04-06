---
name: bangumi
description: >
  Bangumi (bgm.tv) API integration — search anime, view subject details,
  weekly airing calendar, manage user collections (watching/watched/wish),
  rate and track episode progress.
when_to_use: >
  When the user asks about anime information on Bangumi, wants to check
  the weekly airing schedule, search for anime, view their watch list,
  update collection status (mark as watching/watched/dropped), rate anime,
  or track episode progress.
---

# bangumi

Provides access to the Bangumi (bgm.tv) API for anime tracking:

- **search**: Search anime/manga by keyword with filters (type, tag, date, rating).
- **subject_detail**: Get full details for a specific Bangumi subject (anime/manga).
- **calendar**: View the weekly anime airing schedule.
- **user_collections**: List the user's anime collection by status (wish/doing/done/on_hold/dropped).
- **update_collection**: Update collection status, rating, comment, or episode progress for an anime.
- **related_subjects**: Find sequels, prequels, and related works for an anime.

Collection types: 1=wish(想看), 2=done(看过), 3=doing(在看), 4=on_hold(搁置), 5=dropped(抛弃)
Subject types: 1=book, 2=anime, 3=music, 4=game, 6=real
