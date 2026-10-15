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

## 2. On each turn
```
./dm combat attack goblin-1 thorin --action scimitar           # uses the stat block's to-hit and damage
./dm combat attack thorin goblin-1 --action longsword          # PC attacks saved with `pc attack add`
./dm combat attack mira goblin-2 --to-hit 5 --damage "1d10 fire" --ranged     # ad-hoc (Fire Bolt)
./dm combat attack thorin goblin-1 --action longsword --result 18 --nat 13 --damage-result 9   # player-rolled
```
The attack command applies advantage/disadvantage from conditions automatically (blinded, prone, restrained,
invisible, poisoned, paralyzed, unconscious, exhaustion 3+…), crits on natural 20 (doubling dice), auto-crits
melee hits against paralyzed/unconscious targets, and routes damage through resistances, immunities,
vulnerabilities and temporary HP. Pass `--adv`/`--dis` for situational modifiers (flanking, Help, Reckless Attack),
`--ranged` for ranged attacks (prone targets then give disadvantage), `--nonmagical`/`--silvered` when a target
has conditional resistance.

Area and save effects (one damage roll shared by all targets, half on success by default):
```
./dm combat save dex goblin-1 goblin-2 goblin-3 --dc 15 --damage 8d6 --type fire --source Fireball
./dm combat save con enemies --dc 13 --damage 2d8 --type thunder --source Thunderwave
./dm combat save dex party --action "fire breath" --by dragon        # pulls DC/damage from the stat block, tracks recharge
./dm combat save wis ogre --dc 15 --no-half --damage 3d6 --type psychic
```
Conditions from saves, with repeat saves and concentration links (Hold Person):
```
./dm combat conc mira "Hold Person"
./dm combat save wis bandit-captain --dc 13 --on-fail paralyzed --rounds 10 --repeat end --conc mira --source "Hold Person"
```
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
Then: narrate the aftermath, `./dm session log` anything important (loot, captured foes), and — if rules_audit
isn't `off` — have the rules-lawyer audit the fight (the hook will remind you).
