SYSTEM_PROMPT = """Generate TMDB search queries for an anime (animated TV series) name.
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
