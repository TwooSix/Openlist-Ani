---
name: anime-download
description: >
  MUST USE whenever the user asks to download anything anime-related —
  an RSS link, a magnet link, a torrent URL, or just a description of
  the resource. This skill is the ONLY supported path to
  `oani/create_download`; calling that action without going through
  this skill skips library checks, collection rejection, title
  resolution, and user confirmation. Not for search (use
  `anime-search`); not for subscribe/unsubscribe (use `mikan`
  directly).
---

# anime-download

One linear procedure for every download request. Whatever the user
gives you (an RSS link, a magnet, or just a description), follow the
**same four steps** below in order.

## Hard rules

- **Never call `oani/create_download` outside step 4 of this skill.**
  If you find yourself about to call it from anywhere else — a
  one-shot magnet request, a "just download this" prompt, a follow-up
  in another skill — stop and re-enter step 1 here instead. The
  backend depends on a real upstream title and an explicit user
  confirmation; both come from the steps below.
- **"Download this RSS link" ≠ "subscribe to this RSS".** If the user
  gives you an RSS URL and asks to *download* it ("please download …"), you run
  Workflow 1a below — parse the feed, list entries, let the user pick,
  then go through steps 2–4. Do **NOT** call `oani/add_rss`.
- **Never invent a title.** The `title` you pass to
  `oani/create_download` must be the exact string returned by
  `oani/parse_rss`, `oani/resolve_magnet`, `oani/resolve_torrent`, or
  `mikan/releases`. Do not derive it from the user's prompt, the
  magnet's `xt=` hash, or the anime name plus an episode number. If no
  upstream tool produced a title, ask the user.
- **Never skip step 2 (library lookup) or step 3 (confirmation),**
  even when there is only one candidate.
- **Do not submit downloads inside a single turn with an unresolved
  correction.** If the immediately-preceding user message was a
  correction like the ones above, your next action is step 1 (parse /
  resolve) + step 2 (library query) + step 3 (send confirmation
  message and WAIT). Never follow a correction turn with a
  `create_download` call in the same turn.

---

## Step 1 — Extract `(title, download_url)` candidates

Pick the sub-procedure that matches the user's input. Each
sub-procedure must end with a list of one or more
`(title, download_url)` candidates whose `title` came directly from an
upstream tool.

### 1a. User gave an RSS link

1. Call `oani/parse_rss(url=<rss>)` (optionally pass `limit` for very
   long feeds).
2. Show the entries to the user (table form: index, title,
   episode/quality/language/fansub) and let them pick before
   continuing:
   - "all" / "全部" → use every entry
   - "episode X" / "1080p" / "Simplified Chinese" → filter by
     `episode` / `quality` / `languages` / `fansub`
   - Specific indices → use `entries[i]`
3. Each chosen entry's `title` and `link` become one candidate.

If the user's *very first* message was an RSS link with an implied
"download all" (e.g. "download this
feed"), you may skip the pick step and treat every entry as a
candidate — but **you still must run step 2 and step 3 before
calling `create_download`.** "All entries" is not a substitute for the
confirmation message.

### 1b. User gave a magnet link

1. Call `oani/resolve_magnet(magnet=<magnet>)`.
2. If `success: false` (e.g. metadata fetch timed out and `dn=` is
   missing), **ask the user for the resource title.** Do not invent
   one.
3. The resolved title plus the original magnet form a single
   candidate.
4. Trust the resolver's `is_collection` flag — if true, refuse this
   candidate per *Collection rejection* below.

### 1b′. User gave a .torrent file URL

A `.torrent` URL looks like `http(s)://…/*.torrent` (e.g. Mikan's
`/Download/…/<hash>.torrent`).  It is NOT a magnet — do not feed it to
`resolve_magnet`.

1. Call `oani/resolve_torrent(url=<torrent-url>)`.
2. If `success: false` (download or parse failed), **ask the user for
   the resource title.** Do not invent one.
3. The resolved title plus the original .torrent URL form a single
   candidate.
4. Trust the resolver's `is_collection` flag — if true, refuse this
   candidate per *Collection rejection* below.

### 1c. User gave only a description (anime / episode / fansub / …)

1. `mikan/search` with the anime name.
2. `mikan/subgroups` — if the user named a fansub group, pick that
   one; otherwise prefer the group with the most releases.
3. `mikan/releases` with the chosen `group_id`.
4. Match the exact episode (rules below). If you cannot confidently
   identify it, ask the user; if the user corrects you, re-read the
   list and find the right entry.
5. The matched release's `title` and download URL form one candidate.

**Episode matching rules (1c).** Episode number typically appears
after `- XX` or in `[XX]`. Don't confuse it with resolution (1080p),
season numbers, or version (v2). Same episode number across releases
= different quality / language of the same episode — pick the one
matching the user's preference. Never assume one release equals one
episode.

### Collection rejection (applies to 1a / 1b / 1c)

For magnet candidates, trust `oani/resolve_magnet`'s `is_collection`
field — do not re-derive it from title keywords. For RSS / Mikan
entries (which lack the flag), only refuse on unambiguous markers:
`合集`, `全集`, `Complete`, `Batch`, `BD BOX`, `S\d+ Complete`, or
zero-padded ranges like `01-12`. Naked numbers such as `Season 2 - 14`
are NOT collections.

When refusing, quote the resolver's `collection_reason` verbatim (or
the matched marker for RSS / Mikan) and tell the user:

> OpenList-Ani does not currently support downloading collection
> resources (matched: `<reason>`). Please supply a single-episode link
> or pick a non-collection source.

Drop that candidate (or stop, if it was the only one).

---

## Step 2 — Library duplicate lookup

The user often does not know they have already downloaded a resource.
Always check the library before asking for confirmation, **even when
there is only one candidate** — single-item flows still benefit from
catching prior downloads under a different magnet / fansub edit.

Build a single `oani/query_library` call covering every candidate:

```
SELECT title, anime_name, season, episode, downloaded_at
FROM resources
WHERE title IN ('<candidate1.title>', '<candidate2.title>', ...)
```

If titles vary across releases (different fansub edits), fall back to
a per-`(anime_name, season, episode)` query. The point is to surface
duplicates, not to be exhaustive.

Tag each candidate with a **library status**:

- `NEW` — no row matched.
- `DOWNLOADED <iso-date>` — exact title match in the library.
- `EPISODE EXISTS (<existing fansub/quality>)` — same season+episode
  is present from another release.

---

## Step 3 — Confirmation message

Send **one** message that lists every candidate with its library
status, and ask for explicit approval. Use this shape (single-item
case is the same, just one row):

> **About to download**
>
> | # | Title | Source | Library status |
> |---|---|---|---|
> | 0 | `<title>` | `<download_url>` | `<NEW / DOWNLOADED … / EPISODE EXISTS …>` |
> | 1 | `…`     | `…`              | `…` |
>
> Items which already in the library will not download.
> Confirm download? (yes / no / "skip duplicates" / Only download xxx)

Then **wait** for an explicit reply. Treat anything other than an
affirmative response (`yes` / `proceed` /
…) as a refusal and abort. Honour selection responses (`skip
duplicates`, index lists like `0,2`, `new only`) by carrying only the
matching subset into step 4.

---

## Step 4 — Create download tasks

For each candidate the user approved, call
`oani/create_download(download_url=<url>, title=<title>)`. Submit in
sequence; report which succeeded and which failed.

Never call `create_download` outside step 4, and never with a title
that did not come from step 1.

---

## Quick reference

| User intent | Action |
|---|---|
| Download anything (RSS / magnet / description) | Steps 1 → 4 above |
| Add RSS feed for monitoring | `oani/add_rss` |
| Check download tasks | `oani/list_downloads` |
| Query downloaded library | `oani/query_library` |
| List RSS feeds | `oani/list_rss` |
