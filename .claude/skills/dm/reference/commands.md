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
./dm campaign set rules_audit always       # off | key | always
./dm campaign set crit_rule max_plus_roll  # double_dice | max_plus_roll
./dm campaign set advancement xp           # milestone | xp
./dm campaign set sources PHB,XGE,TCE,MM,DMG
./dm campaign set narration concise        # concise | standard | rich
./dm campaign set hide_monster_hp false
./dm campaign set premise "…"              # also name / setting / tone
./dm campaign note "Secret: the mayor is the cult leader"   # DM-only notes in the brief
```

## Characters
```
./dm pc add "Name" --race Elf --class Wizard --level 3 --str 8 --dex 14 --con 13 --int 17 --wis 12 --cha 10 \
    --ac 12 --skills arcana,investigation [--hp N] [--saves int,wis] [--expertise …] [--player Sam] [--jack]
./dm pc list | show <pc> | json <pc>
./dm pc set <pc> <field> <value>          # level, ac, max_hp, str…cha, skills, saves, spells, features, notes…
./dm pc check <pc> stealth --dc 15 [--adv|--dis]     # skill or ability check
./dm pc save <pc> dex --dc 14
./dm pc hp <pc> -7            ./dm pc hp <pc> +2d4+2       ./dm pc temp <pc> 5
./dm pc slot <pc> use 2       ./dm pc slot <pc> restore 2
./dm pc resource <pc> add "Second Wind" --max 1 --reset short   |  use "Second Wind"  |  restore …
./dm pc attack <pc> add "Longsword" --to-hit 5 --damage "1d8+3 slashing"
./dm pc item <pc> add "Rope (50 ft)"      ./dm pc gold <pc> +25      ./dm pc xp all 300
./dm pc rest <pc|all> short|long          ./dm pc hitdie <pc> [count]
./dm pc levelup <pc> [roll|<hp>]          # default: average HP
./dm pc condition <pc> add|remove exhaustion
```

## Combat
See combat.md. Actions: `start, add, party, init, next, status, attack, attacks, save, damage, heal, temp,
condition, conc, deathsave, stabilize, legendary, note, remove, log, end`.

## Session journal
```
./dm session start ["Title"]      # opens journal/session-NNN.md
./dm session log "beat"           # timestamped line in this session's log
./dm session end --summary "…"    # writes the summary (use --summary - to pipe text)
./dm session show [N] | list
```

## Memory & quests
```
./dm memory add "fact" --type event|choice|npc|location|item|quest|lore|rule|secret|relationship|other \
    [--tags a,b] [--important]
./dm memory search "words"   [--scope memory|journal|lore|party|quests]
./dm memory list [--type choice] [--limit 20] [--tags voss]
./dm memory edit m0012 --new-text "…" [--important|--not-important]
./dm memory forget m0012
./dm memory brief
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
./dm rules fetch [--sources PHB,MM,XGE] [--books PHB,DMG,XGE,TCE|none] [--mirror github|site] [--if-missing]
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
