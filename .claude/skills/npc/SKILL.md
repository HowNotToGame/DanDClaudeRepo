---
name: npc
description: Create, check and use RoboDM NPC records. Every named character gets race, alignment, backstory, relationships and a stat block (minor) or character sheet (significant), and a mind the generic npc agent plays. Use to make NPCs (one, a batch, or everyone missing), show or fix a record, see an NPC's stat block, or talk to an NPC through their mind.
argument-hint: "new <name>… | check | fill | show <name> | statblock <name> | talk <name> \"…\""
allowed-tools: Bash(./dm:*), Read, Agent
---

Request: `$ARGUMENTS`

Each record is `campaigns/<slug>/npcs/<npc>.json`. It is made by the **npc-creator** agent, or by `./dm npc add`
for a quick minor NPC, and saved only through `./dm npc import` / `./dm npc add`, which validate it. The **npc**
agent plays the character from `./dm npc brief <npc>`: only what they know, on haiku. Companions keep their own
personas and agents, but also get a record for their identity and sheet.

## new <name>… / fill
- `new`: launch the `npc-creator` agent with the names and the campaign. Say which ones you think are significant
  (they get a character sheet).
- `fill`: run `./dm npc check` first, then launch `npc-creator` with "everyone missing a record" plus that list. For
  a long list, split it into batches of about 10 and launch them in parallel.
- When it reports back, relay the one-line summaries and anything it invented. `./dm npc check` should come back clean.

## check
`./dm npc check`: records with problems, and named characters (NPC lore entries, companions) without a record.

## show / statblock <name>
`./dm npc show <name>` (the whole record, DM-only notes included: don't paste those to players) or
`./dm npc statblock <name>`.

## talk <name> "…"
Launch the `npc` agent with the slug, the campaign and the moment from the NPC's point of view. Relay their answer
in the fiction. HIDES is for the DM only.

## Quick minor NPC, mid-scene
`./dm npc add "Name" --race human --alignment "neutral" --backstory "…" --statblock commoner|mm --role "…"
[--rel "slug: how"]… [--secret "…"] [--lore <entry>]`
