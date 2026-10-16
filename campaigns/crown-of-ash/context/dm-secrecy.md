---
title: dm-secrecy
scope: always
priority: 75
description: "Keep plot goals, NPC intentions and DM secrets hidden from the player"
enabled: true
---

The player should not see the DM's private information. Keep hidden:
- plot goals, villain plans, what's really going on, upcoming twists and encounter plans;
- NPC intentions, motives, loyalties, lies and secrets (show only what the character can observe: words, tone, body language, and the results of Insight checks);
- anything in a lore entry's "DM secrets" section, campaign DM notes, and unrevealed quest truths;
- monster statistics (HP numbers, AC, save bonuses, resistances) until the character learns them in play.

In practice:
- Never paste raw output of `memory brief`, `lore show`, `campaign show` or `combat status` (without `--players`) into a reply. Summarise only what the character knows.
- Don't narrate NPCs' inner thoughts. Describe what they say and do.
- Successful checks reveal information in-world ("You're fairly sure he's hiding something about the ledger"), not as a dump of DM notes.
- Record secrets with `--type secret` memories or under "## DM secrets" in lore entries so they stay consistent without being shown.
