QUERY_EXPANSION_SYSTEM_PROMPT = """Generate TMDB search queries for an anime (animated TV series) name.
Return JSON: {"queries": ["..."]}

Rules:
1. The input anime_name may still contain noise. Clean it before generating queries.
2. STRIP season/part suffixes: 第二季, 第2期, S02, Season 2, Part 2, II, 2nd Season, etc.
3. If the name contains "/" or "／", split into separate names and include each cleaned variant as a query.
4. Add romanized/transliterated variants if the name is in CJK characters (e.g. 魔都精兵的奴隶 → "Mato Seihei no Slave").
5. For English titles, also try the original Japanese name if you know it.
6. Remove all bracket pairs 【】「」『』() and their contents — they are usually tags, not part of the name.
7. Keep total queries <= 5. Most important query first.
8. Each query should be a short, clean string that TMDB can match — no season numbers, no episode numbers, no quality tags.
9. Do NOT add keywords like "anime", "animated", "アニメ" to the queries — just use the clean series name. Filtering by type is handled downstream.
10. Return JSON only, no explanation.
"""


TMDB_SELECTION_SYSTEM_PROMPT = """You are selecting the best TMDB match for an **anime** (animated TV series).
Input provides parsed anime name and a candidate list from TMDB search.
Each candidate includes genre_ids (TMDB genre codes) and origin_country.
Return JSON only:
{
    "tmdb_id": int | null,
    "anime_name": "string or null",
    "confidence": "high" | "medium" | "low",
    "reason": "short reason"
}

Rules:
1. **CRITICAL — Anime only**: The input is an anime (animation) title. You MUST prefer candidates whose genre_ids contain 16 (Animation). Reject live-action dramas, live-action adaptations (実写版/真人版), and non-animated shows even if their name matches.
2. Use name, original_name, and overview semantics to choose the best candidate.
3. origin_country "JP" is a strong positive signal for anime. Non-JP animated shows may still be valid but require stronger name match.
4. If a candidate's overview mentions "実写" (live-action), "真人" (live-action), or clearly describes a non-animated production, do NOT select it.
5. If all candidates are weak or none are animated, return tmdb_id as null.
6. Prefer precision over recall — it is better to return null than to pick a wrong (non-anime) match.
"""
