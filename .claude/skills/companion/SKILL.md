---
name: companion
description: Create and manage RoboDM companions, NPC party members (or important NPCs in a scene) who think for themselves through their own subagents. Use to make a companion from a lore entry, write or adjust their persona, bring them into or out of the party, generate their agent, record table corrections ("Okton wouldn't say that"), or call a party council.
argument-hint: "new <name> | join|leave|here <name> | edit <name> | note <name> \"…\" | council \"question\" | list"
allowed-tools: Bash(./dm:*), Read, Edit, Write, Agent
---

Request: `$ARGUMENTS`

A companion has its own persona (`campaigns/<slug>/companions/<npc>/persona.md`), its own first-person memory
and state (where they are, attitudes, agenda), and its own subagent (`companion-<npc>`). It is consulted only
while it's in the party or present in the scene, and it only knows what that character could know.

## new <name>
1. `./dm companion new "<Name>" --from-lore <lore entry> [--alignment "…"] [--status party]`. This makes a draft
   persona with the lore entry's public text in `## Core` and its DM secrets in `## DM only`.
2. Write the persona from what's recorded: the lore entry, `./dm recall "<name>" "<what they did>"`, the journals.
   Never invent things that contradict canon. Mark open questions as `(ask the player)`. Fill every section:
   - **Drives**: the long-term goal, the goal for this quest, and what they want from the PCs.
   - **Values & lines**: what they'll never do, and what they always do.
   - **Methods**: how they like to solve problems (talk, wait, smash, scheme…) and what they avoid.
   - **Fears & flaws**.
   - **Relationships**: one line each, for the PCs and NPCs that matter.
   - **Secrets**: only what THEY know about themselves.
   - **Knowledge limits**: what they don't know, so they don't act on it.
   - **Combat style**: their tactics, who they protect, when they hold back.
   - **Voice & mannerisms**.

   Leave in **DM only** whatever about them they don't know, plus game mechanics.
3. Seed state: `./dm companion attitude <npc> <pc> <-3..3> --why "…"` for each PC, `./dm companion agenda <npc>
   add "…"` for 1–3 current goals, and `./dm companion remember <npc> "…"` (first person) for the key shared
   memories they'd carry. To tag existing campaign facts as known to them, use
   `./dm memory add "…" --known-by <npc>`.
4. Set `draft: false` in the persona once the player has seen it (if it's the player's companion) or once you're
   happy with it.
5. `./dm companion agent <npc>` writes `.claude/agents/companion-<npc>.md`. New agent files load the next time
   Claude Code starts; until then the generic `companion` agent plays them. Tell the user.

## join / leave / here / away / dead <name>
`./dm companion join <npc>` (travels with the party), `here` (present in this scene only), `leave`/`away`,
`dead`. Only `party` and `present` companions are consulted.

## edit <name>
Edit `persona.md` directly. Keep the section headings. Personas are committed with the campaign, so git keeps
their history.

## note <name> "…"
Table feedback about how the companion is played: `./dm companion note <npc> "…"`. It goes into `## Table notes`,
which the companion is told to follow every time.

## council "question"
`./dm companion council "…"` lists the companions in play. Launch all of their agents **in parallel** (one
message, one Agent call each), then present their positions, and where they disagree, in the fiction.
