---
name: blocks
description: Manage RoboDM instruction context blocks — add custom instructions, house rules, tone or safety rules, and enable/disable library blocks for the campaign.
argument-hint: "list | add <instruction> | enable <name> | disable <name> | show <name>"
disable-model-invocation: true
allowed-tools: Bash(./dm:*)
---

Request: `$ARGUMENTS`

- **list** → `./dm context list` and summarise enabled vs available blocks in a short list.
- **show X** → `./dm context show X`.
- **enable / disable X** (comma lists work) → `./dm context enable X` / `./dm context disable X`.
  If enabling `house-rule-crits-max-plus-roll`, also run `./dm campaign set crit_rule max_plus_roll`.
- **add …** (or any free-form instruction like "NPCs should speak in rhyme" or "crits on 19-20 for everyone"):
  1. Write it as clear DM directives (bullet points), keeping the user's intent exactly.
  2. Pick a short kebab-case name, a scope (always, combat, exploration, social, downtime, worldbuilding) and a
     priority (50 normal; 60–70 for house rules that override defaults; 100 only for safety).
  3. `./dm context add <name> --scope <scope> --priority <n> --description "<one line>" --text "<directives>"`
     (use `--library` only if the user wants it reusable across all campaigns).
  4. If it changes mechanics the tools compute (crits, dice mode, audit level), also adjust the matching
     `./dm campaign set …` setting, or explain how you'll apply it by hand (e.g. pass `--adv`).
- Afterwards run `./dm context render` and follow the updated instructions from now on. Confirm in one line.
