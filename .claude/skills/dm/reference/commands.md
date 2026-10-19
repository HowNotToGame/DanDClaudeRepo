# `./dm` command reference

Run from the repo root. Every command accepts `-c <campaign-slug>` before the command name to target a campaign
other than the active one. `./dm <command> --help` shows all flags.

## Dice
```
./dm roll 1d20+5                 # with modifiers
./dm roll 1d20+7 --adv           # advantage (--dis for disadvantage)
./dm roll 2d6+3 1d8 --label "Sneak attack"
./dm roll 2d6+4 --crit           # critical: dice doubled (or max+roll if crit_rule=max_plus_roll)
./dm roll 4d6kh3   4d6dl1   2d20kl1   2d6ro<3   1d6!   1d20min10   d%
./dm roll stats [--method 4d6|3d6|standard]
```

## Campaign
```
./dm campaign new "Name" --premise "…" --setting "…" --tone "…"
./dm campaign list | use <slug> | show
./dm campaign set dice_mode players        # auto | players
./dm campaign set crit_rule max_plus_roll  # double_dice | max_plus_roll
./dm campaign set advancement xp           # milestone | xp
./dm campaign set sources PHB,XGE,TCE,MM,DMG
./dm campaign set grid_diagonals 5-10-5    # 5 (PHB, default) | 5-10-5 (DMG variant)
./dm campaign set narration concise        # concise | standard | rich
./dm campaign set hide_monster_hp false
./dm campaign set premise "…"              # also name / setting / tone
./dm campaign note "Secret: the mayor is the cult leader"   # DM-only notes in the brief
```

## Characters
```
./dm pc build "Name" --race "Hill Dwarf" --class Fighter --background Soldier --scores standard|pointbuy|roll \
    --assign str=15,dex=12,con=14,int=8,wis=13,cha=10 --skills perception,survival [--asi dex=1,wis=1] [--feat …] \
    [--gear "Chain Mail, Shield, Longsword" | --gold] [--level N --subclass …] [--player Sam] [--preview]
./dm pc build "Name" --scores roll       # rolls 4d6-drop-lowest ×6 once for that character, then assign them
./dm pc levelup <pc> [roll|<hp>] [--class Cleric] [--subclass "Life Domain"] [--asi str=2|str=1,con=1|later] [--feat …] [--preview]
./dm pc equip <pc> "Chain Mail"  ·  unequip <pc> "Chain Mail"     # armor/shield → AC; weapons → attacks
./dm pc attune <pc> "Cloak of Protection"  ·  unattune <pc> "…"   # max 3 (gated)
./dm pc load <pc>                                                # carried weight vs capacity (encumbrance)
./dm shop "Potion of Healing" [--pc mira --buy|--sell] [--qty N] [--price 40]
./dm pc add "Name" --race Elf --class Wizard --level 3 --str 8 --dex 14 --con 13 --int 17 --wis 12 --cha 10 \
    --ac 12 --skills arcana,investigation [--hp N] [--saves int,wis] [--expertise …] [--player Sam] [--jack]
./dm pc list | show <pc> | json <pc>
./dm pc set <pc> <field> <value>          # level, ac, max_hp, str…cha, skills, saves, spells, features, notes…
./dm pc check <pc> stealth --dc 15 [--adv|--dis]     # skill or ability check
./dm pc save <pc> dex --dc 14
./dm pc hp <pc> -7            ./dm pc hp <pc> +2d4+2       ./dm pc temp <pc> 5
./dm pc cast <pc> "Detect Magic" [--slot N] [--ritual]   # outside combat: gate + spends the slot
./dm pc set <pc> classes "Wizard 3, Cleric 2 (Life Domain)"     # multiclass: slots from the PHB table
./dm pc levelup <pc> --class Cleric        # level a specific class (or multiclass into a new one)
./dm pc cast <pc> "Hex" --pact             # use Pact Magic slots (multiclass warlocks; slot key "pact")
./dm pc slot <pc> use 2       ./dm pc slot <pc> restore 2
./dm pc resource <pc> add "Second Wind" --max 1 --reset short   |  use "Second Wind"  |  restore …
./dm pc attack <pc> add "Longsword" --to-hit 5 --damage "1d8+3 slashing"
./dm pc item <pc> add "Rope (50 ft)"      ./dm pc gold <pc> +25      ./dm pc xp all 300
./dm pc rest <pc|all> short|long          ./dm pc hitdie <pc> [count]
./dm pc condition <pc> add|remove exhaustion
```

## Combat
See combat.md. Actions: `start, add, party, init, next, status, map, place, move, stand, dash, disengage,
range, area, attack, attacks, cast, save, damage, heal, temp, condition, conc, deathsave, stabilize, legendary,
note, remove, log, end`. Gate flags: `--reaction`, `--legendary`, `--spell "<name>"`, `--target a,b` (cast),
`--custom`, `--ruling "why"`, `--fix <event id>`. Map: `combat map new|draw|set|show|render|save|load|list|background|clear`.

## Companions (NPCs with their own minds and subagents)
```
./dm companion new "Okton" --from-lore okton [--alignment "lawful neutral"] [--status party]
./dm companion list | show <npc>
./dm companion join|here|leave|away|dead <npc>        # party / present in the scene / not around
./dm companion brief <npc> [--combat] [--situation "…"]    # what the companion's agent reads (their knowledge only)
./dm companion agent <npc>|all [--model sonnet|haiku|opus]  # writes .claude/agents/companion-<npc>.md
./dm companion council "Do we raid the crypt tonight?"      # who to consult in parallel
./dm companion note <npc> "Okton never swears"              # table correction, shown in every brief
./dm companion remember|recall|agenda|attitude|interject <npc> …   # used by the companion's own agent
./dm memory add "…" --known-by okton,halda                  # a campaign fact these characters know
./dm campaign set companion_agents key|always|off
```

## Players: whispers, fog
```
./dm whisper tamsin "You notice fresh scratches on the crypt door."   # only Tamsin's seat sees it
./dm whisper list [pc]
./dm combat map fog on [--dark]  ·  fog off  ·  map reveal|hide <squares|all>  ·  combat vision <who> <ft>
```

## The web client's DM engine
```
./dm engine                                         # which engine, model, effort; is the server up
./dm engine set claude [--model opus] [--effort high]
./dm engine set ollama --model llama3.1:8b [--effort medium] [--ctx 12288] [--tools native|text] [--url …]
./dm engine set kobold [--url http://127.0.0.1:5001]  ·  engine set openai --url … --model … [--api-key …]
./dm engine models                                  # what the server has
./dm combat setup "Ambush" --monsters "goblin x2" [--map 12x8]   # start + party + monsters + map + places + initiative
```

## Speed, cost, playtests
```
./dm campaign set turn_speed fast|balanced|thorough   # subagent models, post-audit scope, brief size
./dm campaign set dm_model sonnet|opus|haiku|default  # the web client's DM model
./dm audit autopass                                   # fast mode: pass this turn's routine mechanical events
./dm playtest [--campaign slug] [--turns N] [--out card.md] --yes    # scored scripted turns (spends usage)
```

## Undo (turn snapshots)
```
./dm undo                    # back to the start of the last turn that changed anything
./dm undo 2  ·  ./dm undo --to 41  ·  ./dm undo --list  ·  ./dm undo --redo
```

## Receipts and the DC rule
```
./dm audit receipts [--reply "your reply text" | --file reply.md]   # this turn's results; checks a reply against them
./dm pc check mira stealth --dc 15            # the DC is required before the roll…
./dm pc check mira athletics --vs 14          # …or a contest against the opponent's total
./dm pc check mira history --open             # …or an open check (no fixed DC; flagged to the audit)
./dm pc save mira dex --dc 13 --result 17     # players dice mode: record the player's total
```

## Time, clocks, factions, scene (the living world)
```
./dm time                                       # Day 11 · 18:20 (evening) · clocks, factions, timers
./dm time set 11 18:00 [--calendar harptos --date "Tarsakh 3" --year 1492]
./dm time advance 3h --reason "road to Velun"   # 1d 6h · 45m · 2w · until dawn · until day 12 06:00
./dm pc rest party long [--no-time]             # 8 h (short = 1 h); 24-hour rule and 1+ HP are gated
./dm pc condition mira add poisoned --for 2h    # or --until-rest
./dm clock add "Lorard storms the gates" --deadline "day 13 dawn" --public --on-fill "…"
./dm clock add "The Choir's ritual" --segments 6 --per 1d [--faction choir]
./dm clock tick k2 [N] --why "…"  ·  clock list [--all]  ·  clock done|stop k2
./dm faction [list]  ·  faction show <f>  ·  faction rep <f> +1 --why "…" [--new]
./dm faction agenda <f> add "…" [--clock k3] | done f1 | drop f1  ·  faction pace <f> fast|normal|slow
./dm scene set "Pyre Square" --present halda,okton  ·  scene add|leave <names>  ·  scene  ·  scene clear
./dm lore mind halda --feeling "…" --goal "…" --opinion "…" --plan "…"
./dm world                                      # off-screen beats waiting to be resolved
./dm world resolve w3 "what happened" [--known-by a,b]  ·  world drop w3
./dm world gossip                               # who else would plausibly hear of this session's events
./dm memory add "…"                             # events/choices default to --known-by <everyone in the scene>
./dm memory edit m0042 --known-by halda         # add people who now know
```

## Rules checking (mandatory: a gate before every rules event, an audit after it)
```
./dm ruling "Grappling with a shield hand is fine; the shield still gives AC" --key grapple-shield \
    [--items "feature:Shield Master"]          # judgement calls: pre-checked once, then reusable by --key
./dm audit brief spell "Hold Person" [--use "Mira targets the ogre"]   # pre-check packet for the rules-lawyer
./dm audit pending                             # post-audit packet: open events + your last reply + combat log
./dm audit status | log [--n 30] | cleared     # overview, recent events, everything cleared so far
./dm audit resolve e0016 --how "…"             # a failed event fixed without a ./dm command
<any fixing command> --fix e0016               # apply the rules-lawyer's fix (resolves e0016)
# rules-lawyer only (the DM is blocked from these in Claude Code):
./dm audit clear <spell|condition|feature|ruling> "<name>" --verdict CORRECT|INCORRECT --notes "…" --cite "PHB p.N"
./dm audit pass <ids|all> [--notes …]   ·   ./dm audit fail <id> --notes "…" --fix "<exact ./dm command>"
# claude.ai only (no subagents there): add --self to clear/pass/fail after looking the rule up yourself
```

## Session journal
```
./dm session start ["Title"]      # opens journal/session-NNN.md
./dm session log "beat"           # timestamped line in this session's log
./dm session end --summary "…"    # writes the summary (use --summary - to pipe text)
./dm session show [N] | list
./dm session transcript [N] [--n 40]   # the automatic play transcript (Claude Code hooks)
```

## Web client
```
./dm web [--open] [--port 8642]   # local table in the browser: DM chat, map, initiative, sheets, dice, lore…
./dm web --lan                    # also serve on the network (phones/tablets); the printed link carries the token
./dm web --new-token              # revoke old links
```

## Sync (play on several devices)
```
./dm sync --check      # is there a newer campaign state on another branch of origin?
./dm sync              # restore each such campaign folder from it (staged; /endsession commits it)
./dm sync --force      # …even over uncommitted local campaign changes
```

## Memory & quests
```
./dm memory add "fact" --type event|choice|npc|location|item|quest|lore|rule|secret|relationship|other \
    [--tags a,b] [--important]
./dm recall "phrasing 1" "phrasing 2" […] [--scope memory|journal|transcript|lore|party|quests] [--limit N]
./dm recall --stats                          # what's indexed (FTS5: stemmed words + trigrams)
./dm memory search "words" [--or "another phrasing"]   # same engine, one or more phrasings
./dm memory list [--type choice] [--limit 20] [--tags voss] [--important]
./dm memory edit m0012 --new-text "…" [--important|--not-important]
./dm memory forget m0012
./dm memory brief [--full] [--budget 9000]   # size-budgeted digest; --full keeps everything
./dm quest add "Title" --giver "NPC" --notes "…" | list [--all] | update q001 --notes "…" | done q001 | fail q001
```

## Lore
```
./dm lore add "Name" --type npc --summary "one line" --tags a,b --status alive --related other-entry \
    [--aliases "Nick"] [--draft] --text "body"      # or --text - with a heredoc, or --file path
./dm lore list [--type faction] | show <name> | search <words> | types
./dm lore update <name> --append "new development" [--status dead] [--summary …] [--type …]
./dm lore index                       # regenerate lore/INDEX.md (the world bible)
./dm lore source add path/to/file.md [--title …]   |   ./dm lore source add --text - --title "…"
./dm lore source list
```
Types: world, region, location, settlement, faction, npc, deity, history, culture, creature, item, language,
plane, event, other.

## Instruction context blocks
```
./dm context list                      # [x] enabled, library vs campaign blocks
./dm context show <name>
./dm context enable tone-grimdark,safety-lines-and-veils
./dm context disable <name>
./dm context add "no-critical-fumbles" --scope combat --priority 60 --text "…"   [--library]
./dm context remove <name>             # campaign blocks only
./dm context render [--scope combat|exploration|social|downtime|worldbuilding]
```

## Rules (local 5etools 2014 data)
```
./dm rules fetch [--sources PHB,MM,XGE] [--books PHB,DMG,XGE,TCE|none] [--mirror local|github|site] [--if-missing]
./dm rules status
./dm rules show "name" [--type spell|monster|item|condition|feat|race|class|classfeature|subclassfeature|
                               optionalfeature|background|action|rule|quickref|book|deity|…] [--source PHB]
./dm rules search "words" [--type …] [--text]      # --text searches inside entry text
./dm rules list spell --class wizard --level 3
./dm rules list monster --cr 1/2 --mtype undead --env swamp
./dm rules list item --rarity rare
./dm rules encounter --party 4x3 --monsters "goblin x4, bugbear"     # DMG difficulty + XP
```

## Random tables
```
./dm random name --culture dwarf -n 5      # human elf dwarf halfling gnome orc tiefling dragonborn half-elf half-orc
./dm random npc [--culture …]              # name, job, trait, motivation, secret
./dm random tavern | weather --climate cold | hook
./dm random encounter --difficulty hard --env forest [--party 4x3]
./dm random item --rarity uncommon -n 3
./dm random pick "left door, right door, the chimney"
```

## Saving / moving campaigns
```
./dm export [--out file.json]      # whole campaign → one JSON save file
./dm import file.json [--as new-slug] [--overwrite]
./dm status
```
