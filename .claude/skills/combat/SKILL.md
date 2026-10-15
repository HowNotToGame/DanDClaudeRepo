---
name: combat
description: Shortcut for the RoboDM combat tracker — start an encounter, add monsters, roll initiative, attack, apply damage, show status, next turn, end.
argument-hint: "start <name> | add <monster> [xN] | init | attack <a> <b> | save … | dmg <who> <n> | next | status | end"
disable-model-invocation: true
allowed-tools: Bash(./dm:*), Read
---

The user wants to do this in combat: `$ARGUMENTS`

Translate it into `./dm combat …` commands (read `.claude/skills/dm/reference/combat.md` if you need the
syntax) and run them. Examples:

- "start bandit ambush, 4 bandits and a captain" → `./dm combat start "Bandit ambush"`, `./dm combat party`,
  `./dm combat add bandit --count 4`, `./dm combat add "bandit captain"`, `./dm combat init`
- "goblin 2 attacks Mira" → `./dm combat attack goblin-2 mira --action scimitar` (check `./dm combat attacks goblin-2`)
- "Mira casts fireball on goblins 1-3" → look up the spell if unsure, `./dm pc slot mira use 3`, then
  `./dm combat save dex goblin-1 goblin-2 goblin-3 --dc <Mira's spell DC> --damage 8d6 --type fire --source Fireball`
- "thorin takes 8" → `./dm combat damage thorin 8`
- "next" → `./dm combat next` · "status" → `./dm combat status`
- "end" → `./dm combat end` (add `--award-xp` if the campaign uses XP advancement)

Respect the campaign's dice mode (`./dm memory brief` shows settings): in `players` mode ask for the player's
roll and pass it with `--result`/`--results`. After running the commands, summarise what happened in one or two
lines of narration and show whose turn it is. If a full RoboDM game is running, continue it per the `dm` skill.
