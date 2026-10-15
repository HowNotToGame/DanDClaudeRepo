# Instruction context blocks

Small Markdown files that change how RoboDM runs your table: tone, house rules,
safety tools, pacing, presentation. Mix and match per campaign.

- Blocks in **this folder** are a shared library. They're **off** until a campaign enables them:
  `./dm context enable tone-grimdark,safety-lines-and-veils`
- Blocks in **`campaigns/<slug>/context/`** belong to one campaign and are **on** by default.
  Add one with `./dm context add my-rule --text "…" --scope combat` (or `/blocks` in Claude Code).
- See what's active: `./dm context list` · load the text: `./dm context render [--scope combat]`

## Format

```markdown
---
title: Flanking
scope: combat          # always | combat | exploration | social | downtime | worldbuilding
priority: 60           # higher wins when two blocks disagree (default 50)
description: One line shown in `context list`
---
Instructions for the DM, written as plain directives.
```

House rules in blocks override rules-as-written; the rules-lawyer agent is told to respect them.
