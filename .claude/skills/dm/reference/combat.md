# Running combat with the tracker

The tracker stores everything in `campaigns/<slug>/combat.json` and keeps PC hit points in sync with their
character sheets. Combatant ids are lowercase names (`goblin-1`, `thorin`); prefixes work (`gob` if unique).
Groups: `enemies` and `party` can be used as targets for saves.

## 1. Set up
```
./dm combat start "Bandits on the road"
./dm combat party                                  # every PC in the campaign
./dm combat add bandit --count 3                   # official stat block (AC, HP, saves, attacks, resistances)
./dm combat add "Bandit Captain" --roll-hp         # roll HP from the formula instead of average
./dm combat add "Hooded Figure" --monster "Cult Fanatic"     # custom name, official stats
./dm combat add "Animated Cart" --hp 20 --ac 12 --init-bonus 0        # homebrew/custom
./dm combat add "Gwen the Guard" --monster guard --side ally
./dm combat init                                   # roll everyone; in players mode PCs are asked
./dm combat init thorin 17                         # record a player's roll
```
Round 1 begins automatically once everyone has initiative. Surprise: tell surprised creatures they can't act on
their first turn (just narrate it and `combat next` past them).

## 1b. The battle map (required before initiative)
```
./dm combat map draw "Old bridge" --text - <<'EOF'      # one character per 5-ft square
TT..~~~~..TT
....~ww~....
#####//#####
............
EOF
./dm combat map new 20x15 "Tavern"                       # or start blank…
./dm combat map set C3:E5 ~                              # …and paint squares (any terrain character)
./dm combat map save bridge | load bridge | list         # reuse layouts (campaigns/<slug>/maps/)
./dm combat map background ~/maps/bridge.png             # lay the grid over an image (squares must line up)
./dm combat place thorin B5 goblin-1 E2                  # tokens (Large creatures fill 2×2 from that square)
./dm combat place party B5 enemies E2                    # a whole side around a square
./dm combat map show [--players]                         # text grid + legend
./dm combat map render [--players] [--html] [--out f]    # SVG/HTML image (default: .cache/battlemap.svg)
```
Fog of war (the players see only what the party has line of sight to; explored squares stay revealed):
```
./dm combat map fog on [--dark]          # dark: vision = darkvision from the sheet, or `combat vision`
./dm combat vision tamsin 40             # carrying a torch (or a darkvision override); -1 = back to the sheet
./dm combat map reveal H2:L6 | all       # what they learn another way (a map, a scout's report)
./dm combat map hide H2:L6 | all  ·  ./dm combat map fog off
```

Terrain: `.` floor · `#` wall · `+` closed door · `/` open door · `~` difficult · `w` water (difficult) ·
`o` low obstacle (difficult, half cover) · `T` tree/pillar (blocks movement, partial cover) · `^` hazard.
Diagonals cost 5 ft (`./dm campaign set grid_diagonals 5-10-5` for the DMG variant).

## 2. On each turn
Every command below passes the **rules gate** first (see SKILL.md, "Mandatory rules checking"). It refuses
actions by the wrong creature, dead or incapacitated creatures, spent reactions, missing spell slots, spells
not on the caster's list, the bonus-action spell rule, unknown conditions or damage types, and wrong spell DCs.
It also asks for a rules-lawyer pre-check the first time a spell, condition or feature appears. Fix the command,
or pass `--ruling "why"` to override a code-gate block on purpose.
```
./dm combat attack goblin-1 thorin --action scimitar           # uses the stat block's to-hit and damage
./dm combat attack goblin-2 mira --action scimitar --reaction  # opportunity attack on Mira's turn (spends the reaction)
./dm combat attack dragon thorin --action tail --legendary     # legendary action (spends 1; --cost N)
./dm combat attack thorin goblin-1 --action longsword          # PC attacks saved with `pc attack add`
./dm combat attack mira goblin-2 --to-hit 5 --damage "1d10 fire" --ranged     # ad-hoc (Fire Bolt)
./dm combat attack thorin goblin-1 --action longsword --result 18 --nat 13 --damage-result 9   # player-rolled
./dm combat move thorin D3 [--path C4,D4,D3] [--dash] [--disengage] [--mode fly]
./dm combat stand thorin        ./dm combat dash mira        ./dm combat disengage mira
./dm combat range mira goblin-2                                # distance, cover, reach
```
On the map the gate also checks: the target is within reach (5 ft, or the stat block's) or range (long range
→ disadvantage; beyond it → blocked), a ranged attacker with an enemy within 5 ft (disadvantage), cover (half
+2 AC, three-quarters +5, total → blocked), and movement (speed, Dash, difficult terrain, walls, hostile
spaces, crawling while prone, speed 0 when grappled/restrained). Moving out of an enemy's reach prints the
opportunity attack to run (`--reaction`); Disengage suppresses it. PC weapons record reach and range with
`./dm pc attack <pc> add "Longbow" --to-hit 5 --damage "1d8+2 piercing" --range 150/600`.
The attack command applies advantage/disadvantage from conditions automatically (blinded, prone, restrained,
invisible, poisoned, paralyzed, unconscious, exhaustion 3+…), crits on natural 20 (doubling dice), auto-crits
melee hits against paralyzed/unconscious targets, and routes damage through resistances, immunities,
vulnerabilities and temporary HP. Pass `--adv`/`--dis` for situational modifiers (flanking, Help, Reckless Attack),
`--ranged` for ranged attacks (prone targets then give disadvantage), `--nonmagical`/`--silvered` when a target
has conditional resistance.

Spells: cast first, then resolve. `cast` checks the slot, the spell list, casting time (action, bonus action,
reaction; minute-long spells can't be cast in a round), whose turn it is and the bonus-action spell rule. It
then spends the slot, sets concentration, and prints the exact save/attack command with the caster's DC.
```
./dm combat cast mira "Fireball" --slot 3          # --slot defaults to the spell's level (pact slots for warlocks)
./dm combat cast mira "Shield"                     # reaction spell: allowed on others' turns, spends the reaction
./dm combat cast cult-fanatic "Hold Person"        # monsters: no slot tracking (count uses from the stat block)
```

Areas on the map: the engine picks the targets (squares whose centre lies inside) and the cover each has
from the point of origin; DEX saves against the area get that bonus automatically.
```
./dm combat area sphere 20 --at E2 --by mira --spell Fireball [--through-corners]   # range + clear line checked
./dm combat area cone --from mira --toward E4 --spell "Burning Hands"   # self-areas: shape/size from the data
./dm combat area line 60 --from mira --toward H2 [--width 10]
./dm combat area cube 10 --at C3                                       # --at = the cube's top-left square
./dm combat save dex area --dc 15 --damage 8d6 --type fire --spell Fireball --by mira
```
Area and save effects (one damage roll shared by all targets, half on success by default). Add
`--spell "<name>" --by <caster>` when a save comes from a spell: the gate checks the DC against the caster's sheet.
```
./dm combat save dex goblin-1 goblin-2 goblin-3 --dc 15 --damage 8d6 --type fire --spell Fireball --by mira
./dm combat save con enemies --dc 13 --damage 2d8 --type thunder --source Thunderwave
./dm combat save dex party --action "fire breath" --by dragon        # pulls DC/damage from the stat block, tracks recharge
./dm combat save wis ogre --dc 15 --no-half --damage 3d6 --type psychic
```
Conditions from saves, with repeat saves and concentration links (Hold Person):
```
./dm combat cast mira "Hold Person"                 # sets concentration automatically (or: combat conc mira "Hold Person")
./dm combat save wis bandit-captain --dc 13 --on-fail paralyzed --rounds 10 --repeat end --conc mira --spell "Hold Person" --by mira
```
Non-standard effects from spells (hexed, marked, bane) need `--custom` on `--on-fail` or `condition add`.
When Mira's concentration breaks (damage, incapacitation, new concentration spell), the paralysis ends by itself.
Concentration checks are rolled automatically when a concentrating creature takes damage (DC 10 or half damage).

Manual tools:
```
./dm combat damage goblin-2 7 --type piercing      # number or dice: ./dm combat damage ogre 2d6+4 --type slashing
./dm combat heal thorin 2d4+2 --source "Potion of Healing"
./dm combat temp thorin 8                          # temp HP (doesn't stack; higher value kept)
./dm combat condition goblin-1 add prone
./dm combat condition goblin-1 add frightened --rounds 10 --save wis:13 --source "Cause Fear"
./dm combat condition orc add poisoned --rounds 1 --tick start   # "until the start of its next turn"
./dm combat condition orc remove prone
./dm combat legendary dragon --cost 2 "Wing Attack"
./dm combat note goblin-3 "on the roof, half cover"   # positions & reminders (shown in status)
./dm combat attacks dragon                           # list parsed attacks / save actions of a creature
./dm combat remove goblin-4                          # fled / left the fight
```

## 3. Advancing
```
./dm combat next          # ends the current turn and starts the next one
./dm combat status        # full table for you; --players hides monster HP numbers
./dm combat log --n 30    # recent events (useful for the rules-lawyer audit)
```
`next` handles: end-of-turn repeat saves (rolled for monsters; prompted for PCs in players mode), condition
durations, start-of-turn recharge rolls, legendary action refresh, skipping dead monsters, and death saving
throws for dying PCs (nat 20 = back up with 1 HP, nat 1 = two failures, 3 failures = dead, 3 successes = stable).

## 4. Dying and death
- A PC at 0 HP is unconscious and dying. Damage while dying = one failed death save (two on a crit); damage of at
  least their max HP kills outright. Healing of any amount brings them back (still prone).
- `./dm combat deathsave mira --result 12` records a player's death save; `./dm combat stabilize mira` (Spare the
  Dying, Medicine DC 10).
- Monsters die at 0 HP unless you use `--nonlethal` (melee knockout).

## 5. Ending
```
./dm combat end            # summary goes into the session log and journal/combat-log.md
./dm combat end --award-xp # also splits XP among PCs (for advancement=xp)
```
Then narrate the aftermath and `./dm session log` anything important (loot, captured foes). The post-audit runs
after every reply as usual: launch the rules-lawyer with `./dm audit pending` when the Stop hook asks.
