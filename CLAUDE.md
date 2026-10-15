# RoboDM — D&D Dungeon Master for Claude

This repo turns Claude into a Dungeon Master for D&D 5e (2014 rules). It has three layers:

1. **`./dm` CLI** (`robodm/` Python package, standard library only) — dice, combat tracker, characters,
   campaign memory, lore, instruction blocks, and lookups in a local copy of the 5etools 2014 data.
2. **Claude Code skills & agents** (`.claude/`) — `/dm` runs the game; `/campaign`, `/combat`, `/roll`,
   `/remember`, `/recap`, `/lore`, `/blocks`, `/rules`, `/endsession` are shortcuts. The `rules-lawyer` agent
   audits rulings against the data; the `lore-keeper` agent builds and checks world lore.
3. **claude.ai support** (`chat/`) — a skill bundle (`python3 tools/build_chat_skill.py`) and Project instructions.

## When the user wants to play
Use the `dm` skill (`.claude/skills/dm/SKILL.md`) and follow it: load `./dm memory brief` and
`./dm context render`, use `./dm` for every roll and every number, record memories as you go, and end with the
`endsession` skill so the campaign is committed and pushed.

## Data & persistence
- `data/5etools/` holds the rules data. It is **gitignored** — fetch with `./dm rules fetch` (the SessionStart
  hook does this automatically in cloud sessions). Never commit it.
- `campaigns/<slug>/` holds each campaign (JSON + Markdown). This **is** committed: it's the long-term memory.
  `campaigns/.active` names the active campaign.
- `context/` is the shared library of instruction blocks (Markdown with frontmatter).

## Developing
- Tests: `python3 -m unittest discover -s tests -t .` (fixture data in `tests/helpers.py`; no network needed).
- Keep the package dependency-free and Python 3.9+ compatible (`from __future__ import annotations` in every module).
- User-facing errors raise `robodm.util.RoboDMError` (printed without a traceback).
- CLI output is read by Claude and often relayed to players on phones: keep it compact Markdown.
- Hooks: `.claude/hooks/session-start.sh` (data fetch + re-injecting the brief after compaction) and
  `.claude/hooks/rules-audit.py` (reminds the DM to run the rules-lawyer after combat).
