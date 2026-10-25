---
name: npc
description: Create, check and use RoboDM NPC records and minds. Every named character has race, alignment, backstory, relationships and a stat block (minor) or character sheet (significant, always), and speaks through their own mind (the npc / npc-major agents). Use to make NPCs (one, a batch, or everyone missing), show or fix a record, see an NPC's stat block, or talk to an NPC through their mind.
argument-hint: "new <name>… | check | fill | show <name> | statblock <name> | talk <name> \"…\""
allowed-tools: Bash(./dm:*), Read, Agent
---

Request: `$ARGUMENTS`

Every named character in a RoboDM campaign is a person: a **record** (`./dm npc show <name>`) and a **mind** that
plays them from only what they know (`npc-major` for major NPCs, `npc` for the rest; companions keep their own
agents). Use `./dm npc …` for everything (`.claude/skills/dm/reference/commands.md`, "NPC records and minds").

## new <name>… · fill
- **One minor walk-on:** `./dm npc add "Name" --race … --alignment … --backstory "…" --statblock "Commoner|MM"
  [--role …] [--rel who:how]`. Pick the closest generic stat block (`./dm rules list monster --mtype humanoid`).
- **Anyone significant, or several people:** launch the **`npc-creator`** agent with the names and the campaign.
  It researches canon (memories, journals, lore), writes complete records (major NPCs always with a character
  sheet) through `./dm npc save -`, and reports what it invented.
- **fill:** run `./dm npc check`, then launch `npc-creator` agents **in parallel**, one per batch of about 8–10
  people grouped by who knows whom, until `npc check` reports nobody missing.

## check
`./dm npc check`: who's named but has no record (lore NPCs, companions, people in the scene or in someone's
relationships), and which records have problems. Offer to fill the gaps.

## show <name> · statblock <name>
`./dm npc show <name>` (add `--players` for what the players may see) and `./dm npc statblock <name>` (the stat
block or compiled sheet, with the 5e.tools link for bestiary creatures). To fix a record, save a corrected copy
through `./dm npc save -` (the npc-creator can do it), never by editing the JSON by hand.

## talk <name> "…"
Launch their mind (`./dm npc show <name>` says which agent: `npc-major` or `npc`) with their slug and the moment
from their point of view, and relay their SAYS and DOES. In play, the DM does this every time a named NPC speaks.
