---
name: roll
description: Roll dice with standard notation (1d20+5, 4d6kh3, 2d6+3 --adv). Players use /roll for quick rolls.
argument-hint: "<dice> [--adv|--dis|--crit] [--label text]   e.g. 1d20+5, 8d6, stats"
disable-model-invocation: true
allowed-tools: Bash(./dm:*)
---

Roll the dice the player asked for: `$ARGUMENTS`

1. Turn the request into `./dm roll …` arguments. Plain notation passes straight through
   (`1d20+5`, `2d6+3 1d4`, `4d6kh3`, `stats`). Translate words: "with advantage" → `--adv`,
   "disadvantage" → `--dis`, "crit"/"critical" → `--crit`, "three times" → `--times 3`,
   a reason like "for stealth" → `--label Stealth`.
2. If the player names a skill, save or attack of a character in the active campaign (e.g. "/roll stealth for
   Mira"), use `./dm pc check <pc> <skill>` / `./dm pc save <pc> <ability>` instead so their bonuses are applied.
   Add the DC the DM set (`--dc N`); if there isn't one (the player is just rolling), add `--open`.
3. Run it and reply with the result in one line (e.g. `🎲 1d20+5 → 14 + 5 = **19**`). Mention natural 20s/1s.
   If a game is in progress, continue the scene using the result.
