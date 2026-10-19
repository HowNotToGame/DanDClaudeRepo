---
title: Grid combat
scope: combat
priority: 50
description: Show the battle map to the players every round (text grid, plus the rendered image where it can be shown).
---
- At the start of each round, show `./dm combat map show --players` in a code block (column letters, row
  numbers, token codes, a legend).
- Where images can be shown (the Claude desktop app, claude.ai), also render it with
  `./dm combat map render --players` and share the file at the start of the fight and when positions change a lot.
- Players may answer with squares ("I move to D4 and attack G2"); run exactly that with `combat move` and
  `combat attack`.
- Mention opportunity attacks, cover and long range when the engine reports them.
