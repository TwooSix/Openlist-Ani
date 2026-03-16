# Mikan Skill

## Description

Mikan (mikanani.me) platform integration skill. Provides actions for searching anime, subscribing to RSS updates for new episodes, and managing subscriptions with subtitle group selection.

## Actions

Run via `run_skill`:

### `mikan_search_bangumi`

Search for anime by keyword on Mikan, returns Mikan IDs.

```
run_skill(skill_module="mikan.script.search", arguments={"keyword": "KEYWORD"})
```

| Argument | Required | Description |
|----------|----------|-------------|
| keyword | **Yes** | Search keyword (anime name) |

### `mikan_subscribe_bangumi`

Subscribe to anime RSS updates with optional subtitle group.

```
run_skill(skill_module="mikan.script.subscribe", arguments={"bangumi_id": ID, ...})
```

| Argument | Required | Description |
|----------|----------|-------------|
| bangumi_id | **Yes** | Mikan bangumi ID |
| subtitle_group_name | No | Subtitle group name (e.g. "ANi"), auto-resolves to ID |
| language | No | 0=all, 1=Simplified Chinese, 2=Traditional Chinese |

### `mikan_unsubscribe_bangumi`

Unsubscribe from anime on Mikan.

```
run_skill(skill_module="mikan.script.unsubscribe", arguments={"bangumi_id": ID})
```

| Argument | Required | Description |
|----------|----------|-------------|
| bangumi_id | **Yes** | Mikan bangumi ID |
| subtitle_group_id | No | Subtitle group ID. Omit to unsubscribe from all. |

## System Prompt Rules

### Mikan (mikanani.me)
- mikan_search_bangumi: find anime by keyword → Mikan ID
- mikan_subscribe_bangumi: pass subtitle_group_name, auto-resolves ID
- mikan_unsubscribe_bangumi: unsubscribe
- Search first to get Mikan ID, then subscribe

## Configuration

Requires `[mikan] username` and `[mikan] password` in config.toml.
