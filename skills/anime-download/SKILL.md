---
name: anime-download
description: >
  Use when the user wants to download episodes, manage RSS feeds,
  or check download status/library. Not for search — use anime-search.
  Not for subscribe/unsubscribe — use mikan directly.
---

# anime-download

Orchestrates mikan and oani API skills for episode download and library management.

## Download a Specific Episode

1. **Find the anime.** Call **mikan/search** if you only have a name.
2. **List fansub groups.** Call **mikan/subgroups**. Prefer groups with more releases, or one the user specifies.
3. **Fetch releases.** Call **mikan/releases** with the chosen group_id.
4. **Match the exact episode.** Read each title carefully:
   - Episode number typically appears after `- XX` or in `[XX]`.
   - Do NOT confuse with resolution (1080p), season numbers, or version (v2).
   - Same episode number across releases = different quality/language of the same episode.
5. **Submit download.** Call **oani/create_download** with the correct magnet link and title.

### Episode Matching Rules

- **Never assume 1 release = 1 episode.** Multiple releases often differ only in language/resolution.
- **Never guess.** If you cannot confidently identify the exact episode, tell the user.
- **Never confuse numbers.** Distinguish episode vs resolution vs season vs version.
- **If corrected,** re-read the releases list and find the correct entry.

## Quick Reference

| User intent | Action |
|---|---|
| Download episode | Workflow above |
| Add RSS feed | oani/add_rss |
| Check download tasks | oani/list_downloads |
| Query downloaded library | oani/query_library |
| List RSS feeds | oani/list_rss |
