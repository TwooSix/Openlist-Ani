---
name: "oani"
description: "Openlist-Ani backend API integration skill. Best for direct RSS searches and downloading resources when you only care about mapping a resource title to a download link."
usage: "Use this for searching pure RSS feeds and downloading raw resources. It directly maps a Resource Title to a Download Link, taking the shortest path to downloading without fansub/community organization."
---

# O-Ani Skill

## Description

Openlist-Ani backend API integration skill. Use this skill when you specifically need to search and parse RSS feeds across resources, mapping directly from **Resource Title -> Download Link**. It is the most effective interface when initiating raw downloads where fansub grouping and community metadata are not required.

## Actions

Run via `run_skill`:

### `search_anime_resources`

Search for anime resources on mikan/dmhy/acgrip websites.

```
run_skill(skill_module="oani.script.search_anime", arguments={"anime_name": "NAME", "website": "SITE"})
```

| Argument | Required | Description |
|----------|----------|-------------|
| anime_name | **Yes** | The anime name to search for |
| website | **Yes** | `mikan`, `dmhy`, or `acgrip` |

### `parse_rss`

Parse an RSS feed URL and extract resource information.

```
run_skill(skill_module="oani.script.parse_rss", arguments={"rss_url": "URL"})
```

| Argument | Required | Description |
|----------|----------|-------------|
| rss_url | **Yes** | RSS feed URL to parse |

### `download_resource`

Download a single anime resource via magnet/torrent link.

```
run_skill(skill_module="oani.script.download", arguments={"download_url": "URL", "title": "TITLE"})
```

| Argument | Required | Description |
|----------|----------|-------------|
| download_url | **Yes** | Magnet link or torrent URL |
| title | **Yes** | Resource title for identification |

### `execute_sql_query`

Execute SQL SELECT queries on the download history database.

```
run_skill(skill_module="oani.script.db_query", arguments={"sql": "SELECT * FROM resources"})
```

| Argument | Required | Description |
|----------|----------|-------------|
| sql | **Yes** | SQL SELECT query. Table: `resources` (columns: id, url, title, anime_name, season, episode, fansub, quality, languages, version, downloaded_at). Do NOT add LIMIT/OFFSET. |
| page | No | Page number (default: 1) |
| page_size | No | Results per page (default: 50, max: 200) |

## System Prompt Rules

### Database Schema
Table: `resources`
Columns: id, url, title, anime_name, season, episode, fansub, quality, languages, version, downloaded_at

### Pagination
Do NOT add LIMIT/OFFSET to SQL queries. If has_next_page is true, request next page.

## Configuration

Requires `[openlist]` configuration (url, token) and `[backend]` (host, port) in config.toml.
