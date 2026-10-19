# 🎲 RoboDM — a Claude Dungeon Master for D&D 5e

RoboDM turns Claude into a Dungeon Master for **Dungeons & Dragons 5e (2014 rules)**, in the spirit of AI DM
apps like fables.gg, built for **Claude Code** first (desktop, terminal, web and the mobile app) with a
**claude.ai chat** version too.

| You asked for | How RoboDM does it |
|---|---|
| **Rules & combat automation** | A combat tracker handles initiative, HP, temp HP, AC, damage resistances/immunities/vulnerabilities, conditions with durations and repeat saves, concentration checks, auto-crits, death saves, legendary actions, recharge abilities and XP. **Every fight is on a battle map**: the DM drafts it from the scene (walls, doors, difficult terrain, water, cover), and the engine checks movement (speed, Dash, difficult terrain, opportunity-attack warnings), reach and range, cover and line of sight, and which squares a sphere, cone, line or cube covers. It shows the map as a text grid anywhere, or renders an SVG/HTML battle map (optionally over your own map image). Multiclass spell slots follow the PHB table. Monsters come straight from official stat blocks. Every roll uses real dice (`./dm roll 2d20kh1+5`). |
| **Long-term memory** | Each campaign is a folder of plain files: session journals, an automatic **play transcript** (every player message and DM reply), a memory log of facts and choices, quests, character sheets and world lore. Claude loads a size-budgeted, priority-tiered **brief** each session and after every compaction: key facts, the current scene's last exchanges, and older memories relevant to what's happening now. `./dm recall` searches everything by meaning: several phrasings, SQLite FTS5 with stemming and trigram matching, typo correction. New bold names that were never recorded trigger a reminder. Everything is committed to git, and `./dm sync` (automatic in cloud sessions) pulls the newest campaign state from whichever branch it was pushed to, so phone and desktop sessions carry over without merging. |
| **5e.tools rules, offline** | `./dm rules fetch` downloads the 5etools 2014 data (≈20 MB, a few seconds) into the repo. Spells, monsters, items, conditions, feats, races, classes, features and core rules are then looked up **locally**. Cloud and mobile sessions fetch it automatically on start. |
| **A second Claude checking the rules** | Mandatory, **before and after every event**. Before: a code gate checks each rules command against the combat state and the data (turn order, reactions, spell slots, spell lists, the bonus-action spell rule, conditions, spell DCs) and blocks illegal ones. The **rules-lawyer** subagent must also clear each spell, condition, feature or judgement call the first time it comes up (then cached). After: every rules event is logged, and the DM can't finish a reply until the rules-lawyer has passed or failed each one and the fixes are applied. Overrides need `--ruling "why"` and are logged as house rulings. |
| **Trust: undo and receipts** | Every turn is snapshotted before its first change, so `./dm undo` (or the DM view's ↶ Rewind button) takes back a misclick or a misunderstood action: HP, positions, memories, lore, companions and the clock all go back, while the transcript keeps what was said. Every rules command also records its receipts (hits, misses, damage, saves, checks, deaths); before a reply is sent, a deterministic check stops the DM if the narration contradicts the dice. And the DC is always set before the roll. |
| **Characters by the book** | The Character Forge (`./dm pc build`, or the web client's "＋ New character" form) builds a sheet from the rules data and refuses anything illegal: racial bonuses and choices, standard array / point buy / scores rolled once per character, class and background skills, saves, HP, proficiencies, features, starting gear. Armor and shields compute AC; weapons become attacks with the right numbers. Level-ups add the class's features from the data, ask for a subclass, ASI or feat when one is due, check multiclass prerequisites, and preview the changes first. Attunement is capped at three, and `./dm shop` buys and sells at list prices. |
| **Speed, cost and proof** | `turn_speed` trades thoroughness for time and usage without giving up the code gate or the audit of judgement calls. The web client shows what each DM turn cost and the session total (API-equivalent, as Claude Code reports it). `./dm playtest` runs scripted turns through the real DM on a scratch copy and scores them: brief loaded, dice through `./dm`, audit clean, narration matching the dice, private messages kept private, no secrets in the player view. |
| **Addable instruction context blocks** | Small Markdown blocks for tone, house rules, safety tools, pacing and presentation. There's a library of 20 ready-made blocks, and you can write your own (`/blocks add …`). They're enabled per campaign, sorted by priority, and can be limited to combat, social or exploration scenes. |
| **World lore, from a reference or from scratch** | The **lore-keeper** subagent turns a document, URL or published setting into structured canon entries, or invents a coherent world. Entries cover NPCs, factions, regions, deities, history and more, and they're consistency-checked and indexed into a world bible. |
| **Companions with their own minds** | NPC party members (and important NPCs in a scene) each get a persona (drives, values, methods, fears, relationships, secrets, combat style, voice), a first-person memory, attitudes, an agenda and **their own subagent**, consulted only while they're in the party or the scene. A companion sees only what that character could know (the hooks enforce it), can disagree, refuse or chase its own agenda, and can't roll dice or change the game: it proposes, the DM rules. Party councils ask every companion in parallel. Inspired by the agent-identity and orchestration ideas in Paperclip and Orca. |
| **A world that moves on its own** | In-game time and a calendar (Harptos for the Realms), timed spells and conditions that expire, rests that take real hours (the 24-hour long-rest rule is gated), countdown and Blades-style progress clocks, faction standing (−5 Nemesis … +5 Champion) and agendas, and NPC minds (feeling, goal, opinion of the party, plan). When time passes, the engine works out what moved off-screen and rolls for it; the DM turns each beat into an event. The current scene's cast automatically witnesses what happens, so companions and NPCs know what they saw and nothing more, and `world gossip` suggests who else would hear. |
| **A real multiplayer table** | Player seats with their own links, private messages to the DM and DM whispers back, out-of-character questions, a "gather a round" mode that waits for everyone, fog of war from real line of sight, and click-to-move with path and opportunity-attack previews. |
| **A table in your browser** | `./dm web` opens a local web client: chat with the DM (a headless Claude Code running all of the above), a live battle map and initiative tracker, party sheets, companions, quests, memory search, lore, rules lookups and a dice tray, all updating as the game changes. The player view is spoiler-safe; a DM view shows everything. `--lan` lets phones and tablets on your Wi-Fi join, served from your PC and its local data. |
| **Claude Code (priority) and Claude chat** | Skills, agents and hooks for Claude Code. For claude.ai, an uploadable skill bundle with save files, or plain Project instructions. |

---

## Quick start: Claude Code

**Desktop or terminal**
```bash
git clone <this repo> && cd DanDClaudeRepo
claude          # then type:  /dm
```
On first run Claude fetches the rules data (`./dm rules fetch`) and walks you through session zero: premise,
tone, safety tools, house rules and characters.

**Web or phone (Claude app → Code)**: start a session on this repo and type `/dm` (or just "let's play D&D").
The SessionStart hook downloads the rules data into the cloud container. Note that cloud sessions start from the
repo's default branch, and `/endsession` commits and pushes the campaign to the session's branch. Merge it (or
start your next session on that branch) so the next session remembers. The environment's network policy must
allow `raw.githubusercontent.com` for the data download.

Requirements: Python 3.9+ (no packages to install).

**Windows.** `./dm` (Git Bash) and `dm.cmd` (cmd and PowerShell, where `.\dm` resolves to it) each test-run their
candidates and use the first Python 3.9+ that works: `python`, then `py -3`, then `python3`. That matters because
`python3` on Windows is often the Microsoft Store placeholder, which is on PATH but only prints "Python was not
found". To force a particular interpreter, set `ROBODM_PYTHON` (e.g. `C:\Python312\python.exe`). The project's
hooks run through `dm` as well, in Git Bash, which is Claude Code's default hook shell on Windows. If Git Bash
isn't installed, install Git for Windows or set `CLAUDE_CODE_GIT_BASH_PATH`.

### Slash commands

| Command | What it does |
|---|---|
| `/dm [campaign or action]` | Start or resume the game. RoboDM loads the brief, recaps, and runs the table |
| `/campaign new <name>` | New campaign and session zero (tone, safety, settings, characters) |
| `/campaign pc` | Guided character creation using the rules data |
| `/combat <what>` | Combat shortcut: "start 4 goblins and a bugbear", "goblin 2 attacks Mira", "next" |
| `/roll 1d20+5` | Quick dice: `4d6kh3`, `--adv`, `stats`, "stealth for Mira" |
| `/remember <fact>` · `/remember ? <question>` | Save to or ask the campaign memory |
| `/recap` | "Previously on…" |
| `/lore <request>` | World-building via the lore-keeper (from scratch, from a file/URL/setting, or `check`) |
| `/blocks list / add / enable / disable` | Manage instruction context blocks |
| `/rules <question>` | Ask the rules-lawyer (answers with citations) |
| `/companion new|join|leave|note|council …` | Make and manage companions (NPCs with their own agents) |
| `/endsession` | Summarise, save memories, update quests, commit and push |

You can also just talk: "I sneak up behind the guard and try to knock him out."

---

## How it works

```
You ──▶ /dm skill (RoboDM persona + rules of play)
          │  every roll / number / fact goes through
          ▼
        ./dm  (Python CLI, stdlib only)
          ├── dice ............ real RNG, full notation
          ├── combat .......... campaigns/<slug>/combat.json  ⇄ party/<pc>.json
          ├── memory/journal .. memory.jsonl, journal/session-NNN.md, quests.json
          ├── lore ............ lore/<type>/<entry>.md + INDEX.md
          ├── world ........... world.json (time, clocks, factions, timers, scene, off-screen beats)
          ├── context blocks .. context/*.md (library) + campaigns/<slug>/context/*.md
          └── rules ........... data/5etools/ (local copy of 5etools 2014 data, indexed)
                                   ▲
          ├── audit ........... campaigns/<slug>/audit/ (rules gate, clearances, post-audit events)
        rules-lawyer subagent ─────┘  pre-checks items, audits every event (only it records verdicts)
        lore-keeper subagent  ──────▶ writes lore entries
Hooks: SessionStart (fetch data; re-inject brief after compaction) · PreToolUse (only the rules-lawyer records
verdicts; no direct edits to game state) · UserPromptSubmit (new turn) · Stop (no reply ends with events unaudited)
```

### The `./dm` CLI

Claude drives it, but you can use it directly too. `./dm <command> --help` explains every flag, and the full
reference is in [`.claude/skills/dm/reference/commands.md`](.claude/skills/dm/reference/commands.md).

```bash
./dm roll 1d20+7 --adv                    # 🎲 1d20+7 → 2d20kh1[~~4~~, 16] + 7 = 23 (advantage)
./dm rules show "hold person"             # full spell text from the local data
./dm rules list monster --cr 2 --env swamp
./dm rules encounter --party 4x3 --monsters "goblin x4, bugbear"     # → MEDIUM, 400 XP
./dm combat start "Ambush" && ./dm combat party && ./dm combat add goblin --count 4 && ./dm combat init
./dm combat save dex goblin-1 goblin-2 --dc 15 --damage 8d6 --type fire --source Fireball
./dm memory search "elder voss"
./dm random npc --culture dwarf
```

### Table settings (`./dm campaign set <key> <value>`)

| Setting | Values | Meaning |
|---|---|---|
| `dice_mode` | `auto` / `players` | RoboDM rolls everything, or players roll their own PC dice and report totals |
| `crit_rule` | `double_dice` / `max_plus_roll` | RAW crits or "brutal crits" house rule |
| `advancement` | `milestone` / `xp` | XP is tracked and split automatically in `xp` mode |
| `sources` | e.g. `PHB,XGE,TCE,MM,DMG` | Books allowed for player options / random encounters |
| `narration` | `concise` / `standard` / `rich` | How much description per beat |
| `hide_monster_hp` | `true` / `false` | Describe monster health instead of showing numbers |
| `grid_diagonals` | `5` / `5-10-5` | Battle-map diagonals: 5 ft each (PHB) or alternating 5/10 (DMG variant) |
| `companion_agents` | `key` / `always` / `off` | Consult companion subagents at decision points, every time they act or speak, or let the DM voice them |
| `turn_speed` | `fast` / `balanced` / `thorough` | Subagent models (haiku / sonnet / opus), post-audit scope (fast: routine mechanical events the engine computed pass automatically once the narration matches the dice; spells, conditions, rulings and overrides are always audited) and brief size (6k / 9k / 14k chars) |
| `dm_model` | `sonnet`, `opus`, `haiku`, a model id, or `default` | The DM's own model in the web client |

### Instruction blocks library (`context/`)

Tone: `tone-heroic`, `tone-grimdark`, `tone-lighthearted`, `tone-horror` · Safety: `safety-lines-and-veils` ·
Table: `new-players`, `solo-play`, `mobile-friendly`, `hidden-rolls`, `npc-voices`, `rule-of-cool`,
`rules-as-written` · Combat: `theater-of-mind`, `grid-combat`, `house-rule-flanking`,
`house-rule-potions-bonus-action`, `house-rule-crits-max-plus-roll` · Campaign: `gritty-realism`,
`milestone-leveling`, `worldbuilding-low-magic`. See [`context/README.md`](context/README.md) to write your own.

---

## The web client (`./dm web`)

```bash
./dm web --open          # http://127.0.0.1:8642/?token=… opens in your browser
./dm web --lan           # also on your network: open the printed link on a phone or tablet
./dm web --new-token     # replace the access token (old links stop working)
```

The page is chat-first: the conversation with the DM fills the middle; 🗺 Scene (the battle map and initiative,
or a recap), 🛡 Party, 🌍 World, 📚 Codex (quests, companions, memory, lore, rules, log) and ⚙ Settings open in
a drawer that sits beside the chat on wide screens and slides over it on phones. During a fight a strip above the
chat shows the round and everyone's health; dice, 🔒 private, OOC, rounds and rewind live by the composer.

- **Chat with the DM.** With the Claude Code engine, each message runs one turn of a headless Claude Code in this repo (`claude -p`, resuming
  the same conversation), so the dm skill, the rules gate and post-audit, the rules-lawyer and the companion
  agents all apply. Replies stream in, with a chip for each roll, combat action, lookup or agent consulted. Pick
  who's speaking under **Playing as**. It needs Claude Code's CLI: `npm install -g @anthropic-ai/claude-code`, run
  `claude` once to sign in, and restart `./dm web` (or point `ROBODM_CLAUDE` at it; `ROBODM_CLAUDE_ARGS` adds
  flags such as `--model`). The headless DM uses the project's permission allow-list; anything that would need a
  permission prompt is refused. Without the CLI the rest of the table still works next to a normal Claude Code
  session, and refreshes as that session changes the campaign.
- **⚙ Settings (host only).** Pick the DM engine (Claude Code, Ollama, KoboldCpp or any OpenAI-compatible server),
  its model (a list from the server, or fable/opus/sonnet/haiku for Claude) and the effort (Claude's `--effort`, or
  how hard a local model works per turn), test the connection, and set the campaign's table settings (turn speed,
  narration, companions, dice, monster HP, crits, diagonals). `./dm engine` does the same from the command line.
- **The table.** The battle map (the same SVG as `./dm combat map show --svg`) and initiative cards, or a recap
  outside combat; full party sheets (click an attack to say it); the world's clocks and factions; the codex. The
  dice tray rolls with advantage or disadvantage, and **→ chat** reports the result to the DM.
- **Several players.** The host's Party tab has a 🔗 link per character (`./dm web` prints them too). A player
  who opens theirs gets a seat: they speak as their character, see their own full sheet and the party's public
  side, and never the DM view. **🔒 private** messages go to the DM alone, and the answer only to them;
  `./dm whisper <pc> "…"` lets the DM tell one player something. **OOC** marks out-of-character questions.
  **⏱ Gather a round** collects everyone's declared action ("waiting for Ilsa, Tamsin") and sends them to the DM
  as one beat once the last player has declared.
- **Fog of war and click-to-move.** With `./dm combat map fog on [--dark]`, the players' map and initiative show
  only what the party can see (line of sight, darkvision, carried light); explored squares stay revealed.
  Clicking a square previews the path, its cost, the movement left and any opportunity attacks; "Move here" asks
  the DM, and the host can also move the token directly.
- **Player view vs DM view.** The player view hides lore `## DM secrets`, companions' personas, agendas, memories
  and feelings about NPCs, secret memories and DM notes, reference sources and (with `hide_monster_hp`) monster
  HP and AC. The DM view (toggle, with a confirmation) shows everything, including the running DM log.
- **Security.** It binds to 127.0.0.1 unless you pass `--lan`. Every request needs the token from the printed link
  (kept in `.cache/web-token`, then a cookie). Requests for any other hostname and cross-site POSTs are refused,
  and the browser can't record rules verdicts, delete campaigns, import or sync. Keep the host link to
  yourself; give players their seat links (a seat can roll, chat, whisper, declare and manage only its own
  character). `./dm web --new-token` revokes every link.

## Local models: Ollama, KoboldCpp, OpenAI-compatible servers

RoboDM can run the DM on a model on your own machine: free, offline, private.

```bash
ollama pull llama3.1:8b                          # https://ollama.com/library/llama3.1
./dm engine set ollama --model llama3.1:8b --effort medium --ctx 12288
./dm engine set kobold --url http://127.0.0.1:5001   # KoboldCpp with a Llama 3.1 Instruct GGUF
./dm engine set openai --url http://127.0.0.1:1234/v1 --model …   # LM Studio, llama.cpp server, vLLM…
./dm engine set claude                           # back to Claude Code
```

(or the ⚙ panel). With a local engine RoboDM runs the DM loop itself, every turn: the condensed DM instructions
plus the campaign brief go to the model, which acts through a `dm` tool that runs `./dm` commands (native tool
calling, or a `<dm>…</dm>` text protocol for servers without it; tool calls written as plain JSON are caught too).
Everything that keeps the game honest still applies: the code gate, first-use pre-checks and the post-audit by a
local rules-lawyer (the same model, answering in JSON), the receipt check, the transcript, private messages and
seats. On top of that the engine catches what small models get wrong: dice or attack results that no command
produced are sent back to be rolled for real, commands pasted into the narration are removed, and lines written
for the players are dropped. A verdict the local rules-lawyer couldn't give is recorded as `unverified`, never as
passed. **Effort** sets the tool rounds per turn (4–24), whether routine engine-computed rolls skip the
rules-lawyer, the temperature and, at xhigh/max, a final self-review pass.

Be realistic about size: an 8B model (llama3.1:8b runs at about 2–3 s per turn on an 8 GB RTX 3070) follows the
basics — scene setting, ability checks, `combat setup` — but fumbles turn order and multi-step combat more than
Claude does. Larger models (14B+, 70B with the memory for it) do much better. For Ollama, set the context window
to what your GPU fits (12k–16k tokens for an 8B model on 8 GB); KoboldCpp's context is set when you launch it.

## The playtest scorecard

```bash
./dm playtest --yes                        # built-in scenario, 6 turns → playtest-<date>.md
./dm playtest --campaign crown-of-ash --turns 3 --yes
python tools/playtest.py --script my-turns.json --out card.md --yes
```

Each turn goes through the real DM exactly as the web client sends it (including a private message, an OOC
question and a fresh conversation that has to reload the campaign from its files), on a scratch copy that's
deleted afterwards. The scorecard lists every check per turn, the time and cost, what failed, and the replies.
It spends real usage, so without `--yes` it only says what it would do.

## Claude chat (claude.ai)

See [`chat/README.md`](chat/README.md). In short: `python3 tools/build_chat_skill.py --with-data` (`python` on
Windows) builds an
uploadable skill (≈2 MB) that runs the same engine in claude.ai's code sandbox. Campaigns persist through
downloadable save files (`dm export` / `dm import`), and those save files also move campaigns between chat and
Claude Code. A no-code Project prompt is in [`chat/PROJECT_INSTRUCTIONS.md`](chat/PROJECT_INSTRUCTIONS.md).

## Using RoboDM with context-mode

[context-mode](https://github.com/mksglu/context-mode) is a Claude Code plugin that keeps big tool output out of
the context window and rebuilds session state after compaction. RoboDM doesn't need it: its own recall index,
transcripts and budgeted brief cover the game's memory, and they also work in claude.ai and mobile sessions.
Those features are an independent stdlib implementation inspired by context-mode's design; context-mode itself is
ELv2-licensed and needs Node 22.5+. The two run fine side by side, though. RoboDM's rules gate also checks
context-mode's code-execution tools (`ctx_execute`, `ctx_batch_execute`), so they can't be used to record
rules-lawyer verdicts or edit game state behind the engine. It's handy for the non-game heavy lifting, such as
fetching a long setting wiki for the lore-keeper. Keep `./dm` itself on the Bash tool so players see the real
dice output.

## Rules data & licensing

RoboDM's code is MIT-licensed (see `LICENSE`). The rules data is **not** part of this repository. `./dm rules
fetch` downloads it from the 5etools 2014 data mirror (`5etools-mirror-3/5etools-2014-src`, falling back to
`2014.5e.tools/data`) onto your own machine or session, and `.gitignore` keeps it out of commits. Dungeons &
Dragons and its content belong to Wizards of the Coast. Please use content from books you own.

## Development

```bash
python3 -m unittest discover -s tests -t .    # offline (fixture data), Python 3.9+; `python` on Windows
```
Code layout: `robodm/` (engine), `robodm/rules/` (5etools fetch, index, renderer, stat blocks, encounter math),
`robodm/audit.py` (rules gate + post-audit), `robodm/hooks.py` (Claude Code hooks, run as `./dm hook <mode>`),
`.claude/` (skills, agents, settings), `context/` (block library), `chat/` + `tools/` (claude.ai bundle), `dm` /
`dm.cmd` (launchers).
