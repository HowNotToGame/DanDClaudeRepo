---
title: Theater of the mind
scope: combat
priority: 50
description: Narrate positions instead of showing the grid (the engine still tracks the battle map for the rules).
---
- The engine still keeps every fight on a battle map (that's how reach, range, cover and areas are checked),
  but don't show the grid unless a player asks. Describe positions in the fiction: "the bridge", "the
  riverbank", "the treeline", "engaged", "near" (within 30 ft), "far".
- Keep the map honest: when you describe someone moving, move them with `./dm combat move`, and when a
  player says where they go, translate it into a square yourself.
- At the start of each round, restate where everyone is in one line of prose.
- Before an area spell, use `./dm combat area …` to see who's caught, then tell the players in words.
