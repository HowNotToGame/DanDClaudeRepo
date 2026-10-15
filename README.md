# 🎲 RoboDM — a Claude Dungeon Master for D&D 5e

RoboDM turns Claude into a Dungeon Master for **Dungeons & Dragons 5e (2014 rules)**, in the spirit of AI DM
apps like fables.gg, built for **Claude Code** first (desktop, terminal, web and the mobile app) with a
**claude.ai chat** version too.

| You asked for | How RoboDM does it |
|---|---|
| **Rules & combat automation** | A combat tracker handles initiative, HP, temp HP, AC, damage resistances/immunities/vulnerabilities, conditions with durations and repeat saves, concentration checks, auto-crits, death saves, legendary actions, recharge abilities and XP. Monsters come straight from official stat blocks. Every roll uses real dice (`./dm roll 2d20kh1+5`). |
| **Long-term memory** | Each campaign is a folder of plain files: session journals, a memory log of facts and choices, quests, character sheets and world lore. Claude loads a compact **brief** at the start of each session, searches older material on demand, and gets the brief back automatically after context compaction. Everything is committed to git, so it carries across sessions and devices. |
| **5e.tools rules, offline** | `./dm rules fetch` downloads the 5etools 2014 data (≈20 MB, a few seconds) into the repo. Spells, monsters, items, conditions, feats, races, classes, features and core rules are then looked up **locally**. Cloud and mobile sessions fetch it automatically on start. |
| **A second Claude checking the rules** | The **rules-lawyer** subagent audits rulings against that data and returns CORRECT / INCORRECT / UNCLEAR with citations and the exact fix. How often it runs is up to you (`off`, `key` moments, or `always`). A hook reminds the DM to have it audit every finished fight. |
| **Addable instruction context blocks** | Small Markdown blocks for tone, house rules, safety tools, pacing and presentation. There's a library of 20 ready-made blocks, and you can write your own (`/blocks add …`). They're enabled per campaign, sorted by priority, and can be limited to combat, social or exploration scenes. |
| **World lore, from a reference or from scratch** | The **lore-keeper** subagent turns a document, URL or published setting into structured canon entries, or invents a coherent world. Entries cover NPCs, factions, regions, deities, history and more, and they're consistency-checked and indexed into a world bible. |
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
          ├── context blocks .. context/*.md (library) + campaigns/<slug>/context/*.md
          └── rules ........... data/5etools/ (local copy of 5etools 2014 data, indexed)
                                   ▲
        rules-lawyer subagent ─────┘  audits rulings (read-only)
        lore-keeper subagent  ──────▶ writes lore entries
Hooks: SessionStart (fetch data; re-inject brief after compaction) · PostToolUse (audit reminder after combat)
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
| `rules_audit` | `off` / `key` / `always` | How often the rules-lawyer checks rulings |
| `crit_rule` | `double_dice` / `max_plus_roll` | RAW crits or "brutal crits" house rule |
| `advancement` | `milestone` / `xp` | XP is tracked and split automatically in `xp` mode |
| `sources` | e.g. `PHB,XGE,TCE,MM,DMG` | Books allowed for player options / random encounters |
| `narration` | `concise` / `standard` / `rich` | How much description per beat |
| `hide_monster_hp` | `true` / `false` | Describe monster health instead of showing numbers |

### Instruction blocks library (`context/`)

Tone: `tone-heroic`, `tone-grimdark`, `tone-lighthearted`, `tone-horror` · Safety: `safety-lines-and-veils` ·
Table: `new-players`, `solo-play`, `mobile-friendly`, `hidden-rolls`, `npc-voices`, `rule-of-cool`,
`rules-as-written` · Combat: `theater-of-mind`, `grid-combat`, `house-rule-flanking`,
`house-rule-potions-bonus-action`, `house-rule-crits-max-plus-roll` · Campaign: `gritty-realism`,
`milestone-leveling`, `worldbuilding-low-magic`. See [`context/README.md`](context/README.md) to write your own.

---

## Claude chat (claude.ai)

See [`chat/README.md`](chat/README.md). In short: `python3 tools/build_chat_skill.py --with-data` builds an
uploadable skill (≈2 MB) that runs the same engine in claude.ai's code sandbox. Campaigns persist through
downloadable save files (`dm export` / `dm import`), and those save files also move campaigns between chat and
Claude Code. A no-code Project prompt is in [`chat/PROJECT_INSTRUCTIONS.md`](chat/PROJECT_INSTRUCTIONS.md).

## Rules data & licensing

RoboDM's code is MIT-licensed (see `LICENSE`). The rules data is **not** part of this repository. `./dm rules
fetch` downloads it from the 5etools 2014 data mirror (`5etools-mirror-3/5etools-2014-src`, falling back to
`2014.5e.tools/data`) onto your own machine or session, and `.gitignore` keeps it out of commits. Dungeons &
Dragons and its content belong to Wizards of the Coast. Please use content from books you own.

## Development

```bash
python3 -m unittest discover -s tests -t .    # 73 tests, offline (fixture data), Python 3.9+
```
Code layout: `robodm/` (engine), `robodm/rules/` (5etools fetch, index, renderer, stat blocks, encounter math),
`.claude/` (skills, agents, hooks, settings), `context/` (block library), `chat/` + `tools/` (claude.ai bundle).
