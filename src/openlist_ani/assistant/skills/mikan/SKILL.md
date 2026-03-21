---
name: "mikan"
description: "Mikan (mikanani.me) platform integration skill. Best for managing resources and subscriptions when you need to know the three-layer relationship: Anime -> Fansub -> Torrent Resource."
usage: "Use this when the user has configured Mikan and wants to manage resource downloads. It is the best choice when you need to understand the relationship between an anime, its subtitle groups (fansubs), and the tracker seeds."
---

# Mikan Skill

## Description

Mikan (mikanani.me) platform integration skill. This skill is optimal for resource management, allowing you to accurately map the three-tier hierarchy: **Anime -> Subtitle Group (Fansub) -> Torrent Resource**. Use this primarily when helping the user set up or manage automated downloads via Mikan.

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

### `mikan_get_bangumi_details`

Get details for a specific anime on Mikan, including available subtitle groups (fansubs) **and each group's latest episode releases with titles and dates**. This is the best way to compare which fansub updates fastest and what episodes are available.

```
run_skill(skill_module="mikan.script.get_details", arguments={"bangumi_id": ID})
```

| Argument | Required | Description |
|----------|----------|-------------|
| bangumi_id | **Yes** | Mikan bangumi ID |

**Output includes**: Each subtitle group's name, ID, and their 5 most recent episode titles with release dates. Use this to answer questions like "which fansub updates fastest" or "what's the latest episode from group X".

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
- mikan_get_bangumi_details: get fansubs, **their latest episodes**, and subgroup IDs for a Bangumi ID → **use this to compare update speed across fansubs**
- mikan_subscribe_bangumi: pass subtitle_group_name, auto-resolves ID
- mikan_unsubscribe_bangumi: unsubscribe
- 搜索流程：1. 先用 `mikan_search_bangumi` 搜索获取大致的 Bangumi ID。2. 进入目标番剧，使用 `mikan_get_bangumi_details` 获取其支持的专门字幕组与其 subgroup IDs，**以及各字幕组的最近更新集数和时间**。3. 使用找到的精确信息再进行订阅或其他操作，不要仅根据标题来猜测第几季，像人类阅览寻找该季的 Bangumi ID 和具体主页信息一样操作。

### ⚠️ 重要：mikan vs oani 的区别
- **需要了解字幕组详情（有哪些字幕组、各组更新了什么、更新速度对比）→ 用 mikan skill 的 `get_details`**
- **需要直接搜索种子下载链接 → 用 oani skill 的 `search_anime`**
- 已经通过 `get_details` 获取到字幕组信息后，不要再用 oani 重复搜索相同内容

## Configuration

Requires `[mikan] username` and `[mikan] password` in config.toml.

