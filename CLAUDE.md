# RoboDM — D&D Dungeon Master for Claude

This repo turns Claude into a Dungeon Master for D&D 5e (2014 rules). It has three layers:

1. **`./dm` CLI** (`robodm/` Python package, standard library only) — dice, combat tracker, characters,
   campaign memory, lore, instruction blocks, and lookups in a local copy of the 5etools 2014 data.
2. **Claude Code skills & agents** (`.claude/`) — `/dm` runs the game; `/campaign`, `/combat`, `/roll`,
   `/remember`, `/recap`, `/lore`, `/blocks`, `/rules`, `/endsession` are shortcuts. The `rules-lawyer` agent
   audits rulings against the data; the `lore-keeper` agent builds and checks world lore.
3. **claude.ai support** (`chat/`) — a skill bundle (`python3 tools/build_chat_skill.py`, or `python` on Windows) and Project instructions.

## When the user wants to play
Use the `dm` skill (`.claude/skills/dm/SKILL.md`) and follow it: load `./dm memory brief` and
`./dm context render`, use `./dm` for every roll and every number, record memories as you go, and end with the
`endsession` skill so the campaign is committed and pushed.

## Battle maps
- `robodm/battlemap.py`: grid geometry (cells, terrain, distance, pathfinding, cover/line of sight with the DMG
  corner method, area templates) and rendering (text grid, SVG/HTML). `robodm/tactics.py`: the `combat map /
  place / move / area / range …` commands and the positional checks the gate runs for attacks, spells and
  initiative. The map lives in `combat.json` (`state["map"]`); tokens are `combatant["pos"]`.

## Companions
- `robodm/companions.py`: personas (`campaigns/<slug>/companions/<npc>/persona.md`, `## DM only` hidden from the
  companion), state (status, attitudes, agenda, interjections), first-person memory, the companion-facing
  `brief()` (knowledge-limited), and the generated subagent files (`.claude/agents/companion*.md`, from
  `PROTOCOL`; regenerate rather than hand-edit). `robodm/gatekeeper.py` limits companion agents to their own
  records and read-only `./dm` commands. The prompt/stop hooks surface interjections and companions voiced
  without their agent.

## DM engines (the web client)
- `robodm/engines/`: the engine config per machine (`<home>/.cache/engine.json`: claude | ollama | kobold | openai,
  model, effort, url, num_ctx, tool_mode), `llm.py` (Ollama /api/chat and OpenAI-compatible streaming clients,
  neutral message shape), `local.py` (`LocalDM`: the DM loop for local models: hooks.prompt → model + `dm` tool →
  pre-checks → receipt rewrite → invented-dice guard → local post-audit → hooks.stop), `prompts.py` (condensed
  prompts for small models). `web/bridge.py` dispatches to Claude Code or `LocalDM`; `robodm/runner.py` runs ./dm
  in-process for both (catching argparse's SystemExit). The web ⚙ panel uses `/api/settings`.

## Speed, cost, playtests
- `robodm/costs.py`: `turn_speed` (agent models via `apply_speed`, `MECHANICAL` events auto-passed in fast mode
  by `audit.autopass` from the Stop hook after the receipt check, brief budget via `memory.brief(budget="auto")`),
  and the web client's usage log (`record_usage` from `bridge.handle`, `usage_summary` for the host).
- `robodm/playtest.py` (`./dm playtest`, `tools/playtest.py`): scripted turns through the real bridge on a
  scratch ROBODM_HOME, scored by `score_turn` and `leak_checks`, written as a Markdown scorecard. Tests drive it
  with a fake Claude Code (`ROBODM_CLAUDE`).

## The table (seats, privacy, fog)
- `robodm/web/table.py`: seat keys (HMAC of the web token), what a seat may run, round mode. `server.py` resolves
  each request to the host (token) or a seat; seats get the player view, their own sheet, their own private
  messages; SSE events with an `audience` reach only those seats (and the host). Private exchanges are marked
  `🔒 [private X→DM]` in the transcript (`capture.private_to/visible`) and filtered from every player-facing view.
- `robodm/whispers.py`: `./dm whisper`. Fog of war: `battlemap.visible_cells/update_fog/fog_view` (sampled
  sight lines; walls and closed doors block, creatures don't), `tactics.vision_fn/refresh_fog/path_preview`.

## Characters
- `robodm/builder.py`: `pc build` (race/subrace incl. unnamed and overwriting subraces, background, class data,
  scores, skills, gear), `levelup_plan` (features by class level incl. subclass refs, ASI/feat, subclass,
  multiclass prerequisites and proficiencies, CON retroactive HP, slots), equipment (`compute_ac`, `weapon_attack`,
  attunement), `forge_options` for the web form. Problems go through `audit.gate`; homebrew classes fall back to
  plain HP-and-slots level-ups.

## Trust: undo, receipts, DC-first
- `robodm/snapshots.py`: before the first state-changing command of each turn (`cli._mutates`), the campaign is
  zipped to `.cache/snapshots/turn-NNNN.zip` (30 kept); `./dm undo` restores one. Transcripts and `audit/` are
  never rolled back; events from undone turns get status `undone`; the next prompt tells the DM.
- `robodm/receipts.py`: facts noted by the engine while a rules command runs (`receipts.note`, stored on the audit
  event) and `check()`, the conservative narration-vs-dice comparison the Stop hook runs once per turn.
- `pc check/save` need `--dc`, `--vs` or `--open` before the roll; in players mode the DC is pinned when asked.

## Living world
- `robodm/world.py` + `campaigns/<slug>/world.json`: in-game time (minutes since Day 1; `simple` or `harptos`
  calendar), timers for timed spells/conditions, clocks (segments, `per`, deadlines), factions (rep −5…+5,
  agendas, pace; keyed to faction lore entries), the scene (place + cast → default `known_by` for witnessed memory
  types), NPC minds (flat `mind_*` lore frontmatter), and `tick()`: tiers by elapsed time, d6 progress rolls,
  open beats the DM resolves with `world resolve`. Code decides what's due; the DM decides what happened.

## Web client
- `robodm/web/`: `./dm web` (`server.py`: ThreadingHTTPServer, token + Host/Origin checks, JSON API, SSE events
  driven by a campaign-file watcher), `views.py` (what each API returns; everything player-facing goes through
  the player/DM filtering there), `bridge.py` (the DM chat: `claude -p --output-format stream-json --resume`, the
  prompt on stdin, turned into chat events) and `static/` (plain HTML/CSS/JS, no build step). Messages that start
  with `[RoboDM web client]` come from it: write the fiction, don't paste tool output or text maps.

## Memory
- `robodm/recall.py`: FTS5 index (stemmed words + trigrams, RRF fusion, typo correction) over memories,
  journals, transcripts, combat log, lore, lore sources, PCs and quests. The cache lives in
  `campaigns/<slug>/.cache/recall.db` (gitignored) and is rebuilt when any source file changes. Plain BM25 is the
  fallback without FTS5.
- `robodm/memory.py brief()`: priority-tiered and trimmed to `BRIEF_BUDGET`; every trimmed section points to the
  command for the rest.
- `robodm/capture.py`: the transcript (`journal/transcript-NNN.md`, written by the prompt/stop hooks) and the
  unrecorded-names reminder. `robodm/sync.py`: `./dm sync` across branches (automatic in cloud sessions).
- `bundle.autosave()`: `ROBODM_AUTOSAVE=<dir>` keeps a current save file there (claude.ai).

## Data & persistence
- `data/5etools/` holds the rules data. It is **gitignored** — fetch with `./dm rules fetch` (the SessionStart
  hook does this automatically in cloud sessions). Never commit it.
- `campaigns/<slug>/` holds each campaign (JSON + Markdown). This **is** committed: it's the long-term memory.
  `campaigns/.active` names the active campaign.
- `context/` is the shared library of instruction blocks (Markdown with frontmatter).

## Developing
- Tests: `python3 -m unittest discover -s tests -t .` (`python` on Windows; fixture data in `tests/helpers.py`;
  no network needed).
- Launchers: `dm` (sh) and `dm.cmd` (Windows cmd/PowerShell) test-run candidate interpreters, because `python3`
  is often the Microsoft Store placeholder on Windows. `ROBODM_PYTHON` overrides. Keep `dm` LF and `dm.cmd`
  CRLF (`.gitattributes`). Never call `python3` directly from hooks or scripts; go through `dm`.
- Keep the package dependency-free and Python 3.9+ compatible (`from __future__ import annotations` in every module).
- User-facing errors raise `robodm.util.RoboDMError` (printed without a traceback).
- CLI output is read by Claude and often relayed to players on phones: keep it compact Markdown.
- Hooks: every hook in `.claude/settings.json` is `"$CLAUDE_PROJECT_DIR"/dm hook <mode>` (`robodm/hooks.py`).
  `session-start` fetches the data in cloud sessions and re-injects the brief after compaction. `pretool`,
  `prompt` and `stop` do mandatory rules checking: only the rules-lawyer records verdicts, no direct edits to
  game state, a new turn per prompt, and no reply ends with rules events unaudited. The gate and audit logic
  live in `robodm/audit.py`; `main()` in `robodm/cli.py` records every rules command as an event.
