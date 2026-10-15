---
name: campaign
description: Create or switch RoboDM D&D campaigns and run session zero — premise, tone, safety tools, house rules, table settings and player character creation using the 5e 2014 rules. Use when the user wants to start a new campaign, make a character, or change campaign settings.
argument-hint: "new <name> | use <slug> | list | pc (make a character) | settings"
allowed-tools: Bash(./dm:*), Read, Grep, Glob
---

# Campaign & session zero

Request: `$ARGUMENTS`

## Switching / listing
- `./dm campaign list`, `./dm campaign use <slug>`, `./dm campaign show`.

## New campaign (session zero)
Have a short, friendly conversation — one or two questions at a time, offering defaults so it's quick on a phone:

1. **Name, premise, setting.** Offer 3 one-line pitches if they have no idea. Ask whether to use a published
   setting (Forgotten Realms, Eberron…), a world they describe, a document they have, or a world you invent.
2. Create it: `./dm campaign new "Name" --premise "…" --setting "…" --tone "…"`.
3. **Tone & safety.** Suggest a tone block (`tone-heroic`, `tone-grimdark`, `tone-lighthearted`, `tone-horror`)
   and recommend `safety-lines-and-veils`. Ask for lines and veils; save each with
   `./dm memory add "Line: …" --type rule --tags safety --important`.
4. **Table settings.** Explain briefly and set what they choose:
   - dice: do players roll their own dice? → `./dm campaign set dice_mode players` (default `auto`)
   - rules strictness → `./dm campaign set rules_audit off|key|always`, optional `rules-as-written` / `rule-of-cool` blocks
   - allowed books → `./dm campaign set sources PHB,XGE,TCE,MM,DMG`
   - levelling → `milestone-leveling` block or `./dm campaign set advancement xp`
   - presentation → `mobile-friendly`, `theater-of-mind` or `grid-combat`, `new-players`, `solo-play`, `hidden-rolls`
   - house rules → library blocks (`./dm context list`) or custom ones (`/blocks add …`)
   Enable chosen library blocks with `./dm context enable a,b,c`.
5. **World.** For a quick start, create 3–6 starter lore entries (starting town, a local threat, a faction, an NPC
   ally, a rumour). For a deep world or a reference document, hand off to the `lore` skill.
6. **Characters** (below), then `./dm lore index` and start play with the `dm` skill.

## Character creation (5e 2014)
Walk each player through it, looking options up in the local data so everything is legal:

- Race/subrace: `./dm rules list race` → `./dm rules show "Hill Dwarf" --type subrace`
- Class: `./dm rules show fighter --type class`; subclass at the right level
- Background: `./dm rules list background`
- Ability scores: standard array (15,14,13,12,10,8), point buy (27 points, 8–15 before racial bonuses), or
  `./dm roll stats`. Apply racial bonuses.
- Skills: class choices + background + race. Saves come from the class automatically.
- HP: max hit die + CON at level 1, then average per level (the tool computes this if `--hp` is omitted).
- AC from armour (look up with `./dm rules show "chain mail"`), equipment from class + background.
- Spells: `./dm rules list spell --class wizard --level 1`; record with `./dm pc set <pc> spells "a, b, c"`.

Save it:
```
./dm pc add "Name" --player "Sam" --race "Hill Dwarf" --class Cleric --subclass "Life Domain" --level 1 \
  --str 14 --dex 10 --con 15 --int 8 --wis 16 --cha 12 --ac 18 --skills insight,medicine,history,religion \
  --background Acolyte --notes "Seeks her missing brother"
./dm pc attack <pc> add "Warhammer" --to-hit 4 --damage "1d8+2 bludgeoning"
./dm pc resource <pc> add "Channel Divinity" --max 1 --reset short
./dm pc item <pc> add "Holy symbol"   ./dm pc gold <pc> +15
```
If `rules_audit` is not `off`, have the `rules-lawyer` agent check the finished character sheet
(`./dm pc json <pc>`). Save each character's backstory hooks as memories (`--type relationship` or `secret`).
