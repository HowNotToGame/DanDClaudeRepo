---
name: lore
description: Build or expand world lore for the RoboDM campaign — invent a setting from scratch, or ingest a reference (published setting, book excerpt, notes, URL) into consistent canon entries for regions, factions, NPCs, deities, history and more.
argument-hint: "<what to create>  |  from <file or URL or setting name>  |  check (consistency review)"
context: fork
agent: lore-keeper
---

Build world lore for the active RoboDM campaign. Request: `$ARGUMENTS`

1. Read the current state: `./dm memory brief`, `./dm lore list`, and `./dm context render --scope worldbuilding`
   (worldbuilding blocks set the style). Read `campaigns/<slug>/lore/INDEX.md` if it exists.
2. Decide the mode:
   - **From a reference** (file path, pasted text, URL, or a named published setting): store raw material first
     with `./dm lore source add <file>` or `./dm lore source add --text - --title "…"` (for URLs, fetch the page and
     store the relevant text). Then extract entities into entries, faithfully — keep the reference's names and
     facts; mark gaps you fill yourself as `--draft`.
   - **From scratch**: generate a coherent set (e.g. for a region: geography, 1–2 settlements, 2–3 factions with
     conflicting goals, 3–5 NPCs with wants/fears/secrets, a bit of history, a few rumours and adventure hooks).
     Use `./dm random name --culture …` for names.
   - **check**: review all entries for contradictions, duplicates and dangling `related` links; fix or report them.
3. Write every entry with `./dm lore add` (one per entity; summary = one line; tags; related slugs; status for
   NPCs). Use `--text -` with a heredoc for long bodies. Update existing entries with `./dm lore update` instead
   of duplicating. Secrets the players shouldn't know go in a `## DM secrets` section of the body.
4. Never contradict existing canon or the session journals. If the request conflicts, say so and propose options.
5. Finish with `./dm lore index` and return a short summary: what was created/changed (names + one-liners), any
   open questions for the DM, and 2–3 adventure hooks the new lore suggests.
