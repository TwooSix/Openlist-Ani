---
name: anime-search
description: >
  Use when the user wants to search anime, check airing schedule,
  look up details, or find related works. Not for downloads — use
  anime-download.
---

# anime-search

Orchestrates bangumi and mikan API skills for search and discovery.

## Search by Keyword

1. Call **bangumi/search** with the user's keyword.
2. Present results concisely: name, score, rank, date.
3. Detail on a result → **bangumi/subject_detail**.
4. Sequels/related → **bangumi/related_subjects**.

## Weekly Airing Schedule

1. Call **bangumi/calendar**.
2. Format by day of week.

## Check Fansub Availability

1. Call **mikan/search** with the anime name to get the Mikan bangumi ID.
2. Call **mikan/subgroups** with that ID.
3. Present groups with release counts.

## Routing

| User intent | Action |
|---|---|
| Title/name query | bangumi/search (richer metadata) |
| "What's airing" | bangumi/calendar |
| Sequels/prequels | bangumi/related_subjects |
| Fansub availability | mikan/search → mikan/subgroups |
| Ambiguous results | Present top results, let user choose. Do not guess. |
