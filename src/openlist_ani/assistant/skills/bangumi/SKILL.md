# Bangumi Skill

## Description

Bangumi (bangumi.tv) API integration skill. Provides actions for fetching anime calendar, subject details, user collection management, and reviews/discussions.

## Actions

Run via `run_skill`:

### `get_bangumi_calendar`

Fetch weekly anime airing calendar by day-of-week.

```
run_skill(skill_module="bangumi.script.calendar", arguments={"weekday": N})
```

| Argument | Required | Description |
|----------|----------|-------------|
| weekday | No | 1=Mon .. 7=Sun. Omit for full week. |

### `get_bangumi_subject`

Get detailed anime info (summary, rating, tags) by one or more subject IDs. Multiple IDs are fetched concurrently.

```
run_skill(skill_module="bangumi.script.subject", arguments={"subject_ids": [ID, ...]})
```

| Argument | Required | Description |
|----------|----------|-------------|
| subject_ids | **Yes** | List of Bangumi subject IDs |

### `get_bangumi_collection`

Fetch user's anime collection with ratings and comments.

```
run_skill(skill_module="bangumi.script.collection", arguments={"collection_type": N})
```

| Argument | Required | Description |
|----------|----------|-------------|
| collection_type | No | 1=Wish, 2=Done, 3=Doing, 4=OnHold, 5=Dropped |

### `get_bangumi_reviews`

Fetch discussion topics and blog reviews for an anime.

```
run_skill(skill_module="bangumi.script.reviews", arguments={"subject_id": ID})
```

| Argument | Required | Description |
|----------|----------|-------------|
| subject_id | **Yes** | Bangumi subject ID |

### `update_bangumi_collection`

Update collection status or watch progress with safety checks.

```
run_skill(skill_module="bangumi.script.collect", arguments={"subject_id": ID, "collection_type": N, ...})
```

| Argument | Required | Description |
|----------|----------|-------------|
| subject_id | **Yes** | Bangumi subject ID |
| collection_type | **Yes** | 1=Wish, 2=Done, 3=Doing, 4=OnHold, 5=Dropped |
| ep_status | No | Watch progress count (episodes 1..N marked watched) |
| episode_number | No | Single episode number to update |
| episode_numbers | No | List of episode numbers, e.g. [1, 2, 3] |
| episode_collection_type | No | Episode status: 0=Remove, 1=Wish, 2=Done, 3=Dropped (default: 2) |

### `search_bangumi_subjects`

Search Bangumi subjects by keyword with optional filters.

```
run_skill(skill_module="bangumi.script.search", arguments={"keyword": "KEYWORD", ...})
```

| Argument | Required | Description |
|----------|----------|-------------|
| keyword | **Yes** | Search keyword |
| sort | No | Sort order: match (default), heat, rank, score |
| subject_type | No | List of subject type filters (1=Book, 2=Anime, 3=Music, 4=Game, 6=Real) |
| tag | No | Tag filters (AND relation) |
| air_date | No | Air date filters, e.g. [">=2020-07-01", "<2020-10-01"] |
| rating | No | Rating filters, e.g. [">=6", "<8"] |
| rank | No | Rank filters, e.g. [">10", "<=100"] |
| limit | No | Max results per page (default 25) |
| offset | No | Pagination offset (default 0) |

## System Prompt Rules

### Bangumi
- To find subject_id by name: use search_bangumi_subjects or get_bangumi_collection
- update_bangumi_collection: if output shows confirmation/mismatch, relay to user and STOP
- get_bangumi_reviews / get_bangumi_calendar / get_bangumi_subject for info

## Configuration

Requires `[bangumi] access_token` in config.toml (or `BANGUMI_TOKEN` env var).
