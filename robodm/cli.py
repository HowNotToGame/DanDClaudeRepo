"""`./dm` — the RoboDM command line. Run `./dm <command> --help` for details."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from robodm import __version__, bundle, campaign, combat, context_blocks, dice, lore, memory, paths, random_tables
from robodm.util import (
    ABILITIES,
    ABILITY_NAMES,
    RoboDMError,
    ability_mod,
    fmt_mod,
    normalize_ability,
    normalize_skill,
    parse_csv_list,
    truncate,
)


# =============================================================================== helpers

def out(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:  # e.g. legacy Windows consoles
        print(text.encode("ascii", "replace").decode("ascii"))


def read_text(args, attr: str = "text") -> str:
    val = getattr(args, attr, None)
    file = getattr(args, "file", None)
    if file:
        return Path(file).expanduser().read_text(encoding="utf-8")
    if val == "-":
        return sys.stdin.read()
    return val or ""


def get_camp(args) -> campaign.Campaign:
    return campaign.load(getattr(args, "campaign", None))


def maybe_camp(args) -> campaign.Campaign | None:
    try:
        return get_camp(args)
    except RoboDMError:
        return None


def rules_db():
    from robodm.rules import default_db

    return default_db()


def roll_or_int(value: str) -> tuple[int, str]:
    """Accept '12' or a dice expression like '2d6+3'."""
    v = value.strip()
    if v.lstrip("+-").isdigit():
        return int(v), ""
    r = dice.roll(v)
    return r.total, r.text()


# =============================================================================== roll

def cmd_roll(args) -> None:
    if args.expr and args.expr[0].lower() == "stats":
        method = args.method or "4d6"
        results = dice.roll_stats(method)
        out(f"Ability scores ({'standard array' if method == 'standard' else method + (' drop lowest' if method == '4d6' else '')}):")
        for r in results:
            rolls = f" [{', '.join(r['rolls'])}]" if r["rolls"] else ""
            out(f"- **{r['score']}** ({fmt_mod(r['mod'])}){rolls}")
        out(f"Total {sum(r['score'] for r in results)}, modifiers sum {fmt_mod(sum(r['mod'] for r in results))}")
        return
    if not args.expr:
        raise RoboDMError("Give a dice expression, e.g. `./dm roll 1d20+5`.")
    camp = maybe_camp(args)
    crit_rule = camp.settings.get("crit_rule", "double_dice") if camp else "double_dice"
    exprs = args.expr
    if len(exprs) > 1 and all(e.startswith(("+", "-")) or e.isdigit() for e in exprs[1:]):
        exprs = ["".join(exprs)]  # tolerate "1d20 + 5"
    results = []
    for _ in range(max(1, args.times)):
        for e in exprs:
            r = dice.roll(e, adv=args.adv, dis=args.dis, crit=args.crit, crit_rule=crit_rule, label=args.label or "")
            results.append(r)
            if not args.json:
                out("🎲 " + r.text())
    if args.json:
        out(json.dumps([r.to_dict() for r in results], indent=1))
    elif len(results) > 1:
        out(f"Sum of all: {sum(r.total for r in results)}")


# =============================================================================== campaign

def cmd_campaign(args) -> None:
    a = args.action
    if a == "new":
        c = campaign.create(args.name, premise=args.premise or "", setting=args.setting or "", tone=args.tone or "",
                            slug=args.slug)
        out(f"Created campaign **{c.name}** at campaigns/{c.slug}/ and made it active.")
        out("Next: session zero — characters (`./dm pc add`), tone & safety blocks (`./dm context list`), world lore (`/lore`).")
    elif a == "list":
        active = campaign.active_slug()
        camps = campaign.list_campaigns()
        if not camps:
            out("No campaigns yet. `./dm campaign new \"Name\"`")
        for c in camps:
            out(f"{'▶' if c['slug'] == active else ' '} {c['slug']} — {c['name']} (sessions: {c['session']})")
    elif a == "use":
        campaign.set_active(args.name)
        out(f"Active campaign: {args.name}")
    elif a == "show":
        c = get_camp(args)
        out(json.dumps({k: v for k, v in c.data.items()}, indent=2, ensure_ascii=False))
    elif a == "set":
        c = get_camp(args)
        if args.key in ("name", "premise", "setting", "tone"):
            c.data[args.key] = args.value
        else:
            c.settings[args.key] = campaign.coerce_setting(args.key, args.value)
        c.save()
        out(f"{args.key} = {c.data.get(args.key, c.settings.get(args.key))}")
    elif a == "note":
        c = get_camp(args)
        c.data["dm_notes"] = (c.data.get("dm_notes", "") + "\n" + args.name).strip() if not args.replace else args.name
        c.save()
        out("DM notes updated (secret; shown only in the brief).")
    elif a == "delete":
        if not args.yes:
            raise RoboDMError("This deletes the whole campaign folder. Re-run with --yes to confirm.")
        campaign.delete(args.name)
        out(f"Deleted campaign {args.name}.")


# =============================================================================== pc

def cmd_pc(args) -> None:
    c = get_camp(args)
    a = args.action
    if a == "add":
        if c.find_pc(args.name) and campaign.slugify(args.name) == c.find_pc(args.name)["slug"]:
            raise RoboDMError(f"{args.name} already exists.")
        pc = campaign.new_pc(
            args.name, player=args.player, race=args.race, cls=args.cls or "", subclass=args.subclass, level=args.level,
            background=args.background, ac=args.ac, hp=args.hp, speed=args.speed, init=args.init,
            saves=parse_csv_list(args.saves), skills=parse_csv_list(args.skills), expertise=parse_csv_list(args.expertise),
            spell_ability=args.spell_ability, notes=args.notes, jack_of_all_trades=args.jack,
            **{ab: getattr(args, ab) for ab in ABILITIES},
        )
        c.save_pc(pc)
        out(f"Added {pc['name']}.")
        out(campaign.pc_summary(pc, full=True))
        return
    if a == "list":
        party = c.party()
        if not party:
            out("No characters yet.")
        for pc in party:
            out(f"- {campaign.pc_summary(pc)}")
        return
    if a == "rest" and args.name.lower() in ("all", "party"):
        for pc in c.party():
            changes = campaign.rest(pc, args.value)
            c.save_pc(pc)
            out(f"{pc['name']} — {args.value} rest: {', '.join(changes) or 'nothing to restore'}")
        if args.value == "short":
            out("Short rest: players may spend hit dice (`./dm pc hitdie <name>`).")
        c.log(f"Party took a {args.value} rest.")
        return
    if a == "xp" and args.name.lower() in ("all", "party"):
        amount = int(args.value)
        for pc in c.party():
            pc["xp"] = pc.get("xp", 0) + amount
            c.save_pc(pc)
            out(f"{pc['name']}: {pc['xp']} XP")
        return

    pc = c.get_pc(args.name)
    if a == "show":
        out(campaign.pc_summary(pc, full=True))
        return
    if a == "json":
        out(json.dumps(pc, indent=2, ensure_ascii=False))
        return
    if a == "set":
        field, raw = args.value, args.extra
        if raw is None:
            raise RoboDMError("Usage: pc set <name> <field> <value>  (fields: level, ac, speed, race, class, subclass, "
                              "player, notes, str..cha, max_hp, init, spell_ability, gold, xp, saves, skills, expertise, "
                              "spells, features, background)")
        fl = field.lower()
        if fl in ABILITIES:
            pc["abilities"][fl] = int(raw)
        elif fl in ("level", "ac", "speed", "gold", "xp"):
            pc[fl] = int(raw)
            if fl == "level":
                pc["hit_dice"]["max"] = pc["level"]
                pc["spell_slots"] = campaign.spell_slots_for(pc.get("class", ""), pc["level"], pc.get("subclass", ""))
        elif fl in ("max_hp", "hp_max", "maxhp"):
            pc["hp"]["max"] = int(raw)
            pc["hp"]["current"] = min(pc["hp"]["current"], pc["hp"]["max"])
        elif fl in ("init", "initiative"):
            pc["initiative_bonus"] = None if raw.lower() in ("auto", "none") else int(raw)
        elif fl in ("saves", "save_proficiencies"):
            pc["save_proficiencies"] = [normalize_ability(s) for s in parse_csv_list(raw)]
        elif fl in ("skills", "skill_proficiencies", "expertise"):
            vals = []
            for s in parse_csv_list(raw):
                sk = normalize_skill(s)
                if not sk:
                    raise RoboDMError(f"Unknown skill '{s}'.")
                vals.append(sk)
            pc["expertise" if fl == "expertise" else "skill_proficiencies"] = vals
        elif fl in ("spells", "features", "inventory", "resist", "immune", "vulnerable"):
            pc[fl] = parse_csv_list(raw)
        elif fl in ("spell_ability", "spellcasting_ability"):
            pc["spellcasting_ability"] = normalize_ability(raw)
        elif fl in ("race", "class", "subclass", "player", "notes", "background", "name"):
            pc[fl] = raw
            if fl in ("class", "subclass"):
                pc["spell_slots"] = campaign.spell_slots_for(pc.get("class", ""), pc["level"], pc.get("subclass", ""))
        elif fl in ("jack", "jack_of_all_trades"):
            pc["jack_of_all_trades"] = raw.lower() in ("1", "true", "yes")
        elif fl.startswith("bonus."):
            pc.setdefault("bonuses", {})[fl[6:]] = int(raw)
        else:
            raise RoboDMError(f"Unknown field '{field}'.")
        c.save_pc(pc)
        out(campaign.pc_summary(pc, full=True))
        return
    if a == "hp":
        delta_txt = args.value
        amount, rolled = roll_or_int(delta_txt.lstrip("+") if delta_txt.startswith("+") else delta_txt)
        hp = pc["hp"]
        if delta_txt.startswith("-") or args.extra == "damage":
            dmg = abs(amount)
            absorbed = min(hp.get("temp", 0), dmg)
            hp["temp"] -= absorbed
            hp["current"] = max(0, hp["current"] - (dmg - absorbed))
            out(f"{pc['name']} takes {dmg}{' (' + str(absorbed) + ' to temp)' if absorbed else ''} → HP {hp['current']}/{hp['max']}")
            if hp["current"] == 0:
                out("⚠️ At 0 HP — unconscious and dying (use the combat tracker for death saves).")
        else:
            before = hp["current"]
            hp["current"] = min(hp["max"], hp["current"] + abs(amount))
            if before == 0 and hp["current"] > 0:
                pc["death_saves"] = {"success": 0, "fail": 0}
            out(f"{pc['name']} heals {hp['current'] - before}{' (' + rolled + ')' if rolled else ''} → HP {hp['current']}/{hp['max']}")
        c.save_pc(pc)
        _sync_combat_from_sheet(c)
        return
    if a == "temp":
        amount = int(args.value)
        pc["hp"]["temp"] = max(pc["hp"].get("temp", 0), amount)
        c.save_pc(pc)
        _sync_combat_from_sheet(c)
        out(f"{pc['name']} temp HP: {pc['hp']['temp']} (temp HP doesn't stack)")
        return
    if a in ("check", "save"):
        _pc_check(c, pc, args, save=(a == "save"))
        return
    if a == "hitdie":
        hd = pc["hit_dice"]
        n = int(args.value or 1)
        if hd["max"] - hd["used"] < n:
            raise RoboDMError(f"{pc['name']} has only {hd['max'] - hd['used']} hit dice left.")
        con = ability_mod(pc["abilities"]["con"])
        total = 0
        for _ in range(n):
            r = dice.roll(f"1d{hd['die']}{fmt_mod(con) if con else ''}", label="hit die")
            total += max(0, r.total)
            out("🎲 " + r.text())
        hd["used"] += n
        before = pc["hp"]["current"]
        pc["hp"]["current"] = min(pc["hp"]["max"], before + total)
        c.save_pc(pc)
        out(f"{pc['name']} recovers {pc['hp']['current'] - before} HP → {pc['hp']['current']}/{pc['hp']['max']} "
            f"({hd['max'] - hd['used']} hit dice left)")
        return
    if a == "rest":
        changes = campaign.rest(pc, args.value)
        c.save_pc(pc)
        out(f"{pc['name']} — {args.value} rest: {', '.join(changes) or 'nothing to restore'}")
        return
    if a == "slot":
        verb, level = args.value, args.extra
        if verb not in ("use", "restore") or not level:
            raise RoboDMError("Usage: pc slot <name> use|restore <level>")
        slot = pc.get("spell_slots", {}).get(str(level))
        if not slot:
            raise RoboDMError(f"{pc['name']} has no level {level} slots. Slots: {pc.get('spell_slots') or 'none'}")
        if verb == "use":
            if slot["used"] >= slot["max"]:
                higher = [k for k, v in sorted(pc["spell_slots"].items()) if int(k) > int(level) and v["used"] < v["max"]]
                raise RoboDMError(f"No level {level} slots left." + (f" Higher slots available: {', '.join(higher)}" if higher else ""))
            slot["used"] += 1
        else:
            slot["used"] = max(0, slot["used"] - 1)
        c.save_pc(pc)
        out(f"{pc['name']} level {level} slots: {slot['max'] - slot['used']}/{slot['max']} left")
        return
    if a == "resource":
        verb, res = args.value, args.extra
        resources = pc.setdefault("resources", {})
        if verb == "add":
            resources[res] = {"max": int(args.max or 1), "used": 0, "reset": args.reset or "long"}
        elif verb in ("use", "restore"):
            if res not in resources:
                raise RoboDMError(f"No resource '{res}'. Known: {', '.join(resources) or 'none'}")
            r = resources[res]
            if verb == "use":
                if r["used"] >= r["max"]:
                    raise RoboDMError(f"{res} is spent (resets on a {r.get('reset', 'long')} rest).")
                r["used"] += int(args.n or 1)
            else:
                r["used"] = 0
        elif verb == "remove":
            resources.pop(res, None)
        else:
            raise RoboDMError("Usage: pc resource <name> add|use|restore|remove <resource> [--max N --reset short|long]")
        c.save_pc(pc)
        out("Resources: " + (", ".join(f"{k} {v['max'] - v['used']}/{v['max']} ({v.get('reset')})" for k, v in resources.items()) or "none"))
        return
    if a == "attack":
        verb, name = args.value, args.extra
        attacks = pc.setdefault("attacks", [])
        if verb == "add":
            if args.to_hit is None or not args.damage:
                raise RoboDMError("Usage: pc attack <name> add \"Longsword\" --to-hit 5 --damage \"1d8+3 slashing\"")
            attacks[:] = [x for x in attacks if x["name"].lower() != name.lower()]
            attacks.append({"name": name, "to_hit": int(args.to_hit),
                            "damage": [list(d) for d in combat.parse_damage_spec(args.damage)]})
        elif verb == "remove":
            attacks[:] = [x for x in attacks if x["name"].lower() != name.lower()]
        else:
            raise RoboDMError("Usage: pc attack <name> add|remove <attack name> …")
        c.save_pc(pc)
        out("Attacks: " + ("; ".join(f"{x['name']} {fmt_mod(x['to_hit'])} ({', '.join(' '.join(d) for d in x['damage'])})" for x in attacks) or "none"))
        return
    if a == "item":
        verb, item = args.value, args.extra
        inv = pc.setdefault("inventory", [])
        if verb == "add":
            inv.append(item)
        elif verb == "remove":
            match = next((x for x in inv if x.lower() == item.lower()), None) or next((x for x in inv if item.lower() in x.lower()), None)
            if not match:
                raise RoboDMError(f"{pc['name']} doesn't carry '{item}'.")
            inv.remove(match)
        else:
            raise RoboDMError("Usage: pc item <name> add|remove <item>")
        c.save_pc(pc)
        out(f"{pc['name']} inventory: {', '.join(inv) or 'empty'}")
        return
    if a == "gold":
        pc["gold"] = pc.get("gold", 0) + int(args.value)
        c.save_pc(pc)
        out(f"{pc['name']} gold: {pc['gold']} gp")
        return
    if a == "xp":
        pc["xp"] = pc.get("xp", 0) + int(args.value)
        c.save_pc(pc)
        out(f"{pc['name']}: {pc['xp']} XP")
        return
    if a == "levelup":
        pc["level"] += 1
        pc["hit_dice"]["max"] = pc["level"]
        con = ability_mod(pc["abilities"]["con"])
        hd = pc["hit_dice"]["die"]
        if args.value and args.value.isdigit():
            gain = int(args.value)
            how = "given"
        elif args.value == "roll":
            r = dice.roll(f"1d{hd}")
            gain = max(1, r.total + con)
            how = f"rolled {r.total}{fmt_mod(con)}"
        else:
            gain = max(1, hd // 2 + 1 + con)
            how = f"average {hd // 2 + 1}{fmt_mod(con)}"
        pc["hp"]["max"] += gain
        pc["hp"]["current"] += gain
        pc["spell_slots"] = _merge_slots(pc.get("spell_slots", {}), campaign.spell_slots_for(pc.get("class", ""), pc["level"], pc.get("subclass", "")))
        c.save_pc(pc)
        out(f"⬆️ {pc['name']} is now level {pc['level']}! +{gain} max HP ({how}).")
        out(campaign.pc_summary(pc, full=True))
        out("Check new class features with `./dm rules list classfeature --class <class> --level <level>` and record choices.")
        c.log(f"{pc['name']} reached level {pc['level']}.")
        return
    if a == "condition":
        verb, cond = args.value, args.extra
        conds = pc.setdefault("conditions", [])
        if verb == "add":
            conds.append({"name": cond.lower(), "persistent": True})
        elif verb == "remove":
            pc["conditions"] = [x for x in conds if (x.get("name") if isinstance(x, dict) else x) != cond.lower()]
        c.save_pc(pc)
        out(f"{pc['name']} conditions: {', '.join((x.get('name') if isinstance(x, dict) else x) for x in pc['conditions']) or 'none'}")
        return
    if a == "delete":
        c.pc_path(pc["slug"]).unlink()
        out(f"Removed {pc['name']} from the party.")
        return
    raise RoboDMError(f"Unknown pc action '{a}'.")


def _merge_slots(old: dict, new: dict) -> dict:
    for k, v in new.items():
        if k in old:
            v["used"] = min(old[k].get("used", 0), v["max"])
    return new


def _sync_combat_from_sheet(c: campaign.Campaign) -> None:
    enc = combat.Encounter(c)
    if enc.active:
        enc.save()


def _pc_check(c: campaign.Campaign, pc: dict, args, save: bool) -> None:
    what = args.value
    if not what:
        raise RoboDMError("Name a skill or ability, e.g. `pc check mira stealth` or `pc save mira dex`.")
    skill = None if save else normalize_skill(what)
    if skill:
        bonus = campaign.pc_skill_bonus(pc, skill)
        label = f"{pc['name']} {skill.title()} check"
    else:
        ab = normalize_ability(what)
        if save:
            bonus = campaign.pc_save_bonus(pc, ab)
            label = f"{pc['name']} {ABILITY_NAMES[ab]} save"
        else:
            bonus = ability_mod(pc["abilities"][ab])
            if pc.get("jack_of_all_trades"):
                bonus += campaign.pc_prof(pc) // 2
            label = f"{pc['name']} {ABILITY_NAMES[ab]} check"
    if c.settings.get("dice_mode") == "players" and not args.auto:
        out(f"Ask {pc.get('player') or pc['name']} to roll: d20{fmt_mod(bonus)} for {label}"
            f"{' (advantage)' if args.adv else ''}{' (disadvantage)' if args.dis else ''}"
            f"{' vs DC ' + str(args.dc) if args.dc else ''}. (Add --auto to roll for them.)")
        return
    r = dice.roll_d20(bonus, adv=args.adv, dis=args.dis, label=label)
    line = "🎲 " + r.text()
    if args.dc:
        line += f" vs DC {args.dc} → **{'success' if r.total >= args.dc else 'failure'}**"
    out(line)


# =============================================================================== combat

def cmd_combat(args) -> None:
    c = get_camp(args)
    enc = combat.Encounter(c)
    a = args.action
    show_status = False
    if a == "start":
        enc.start(" ".join(args.args) if args.args else "")
    elif a == "add":
        if not args.args:
            raise RoboDMError("Usage: combat add <name> [--monster NAME] [--count N] [--hp N --ac N --init-bonus N]")
        enc.require_active()
        name = " ".join(args.args)
        pc = c.find_pc(name) if not args.monster and args.hp is None else None
        if pc:
            enc.add_pc(pc, init=args.init)
        elif args.hp is not None and not args.monster:
            enc.add_custom(name, hp=args.hp, ac=args.ac or 10, init_bonus=args.init_bonus or 0, side=args.side,
                           count=args.count, init=args.init)
        else:
            from robodm.rules.statblock import combat_profile

            db = rules_db()
            rec, alts = db.lookup(args.monster or name, "monster", args.source)
            if not rec:
                hint = ", ".join(r["n"] for r in alts[:5])
                raise RoboDMError(f"No monster '{args.monster or name}' in local data{' (close: ' + hint + ')' if hint else ''}. "
                                  "For a custom creature give --hp and --ac.")
            profile = combat_profile(db.get(rec))
            if args.hp is not None:
                profile["hp"] = args.hp
            if args.ac is not None:
                profile["ac"] = args.ac
            if args.init_bonus is not None:
                profile["init_bonus"] = args.init_bonus
            enc.add_monster(profile, count=args.count, roll_hp=args.roll_hp, side=args.side,
                            name=name if args.monster else None, init=args.init)
    elif a == "party":
        enc.require_active()
        party = c.party()
        if not party:
            raise RoboDMError("No PCs in this campaign yet.")
        for pc in party:
            if any(x.get("pc") == pc["slug"] for x in enc.combatants.values()):
                continue
            enc.add_pc(pc)
    elif a == "init":
        enc.require_active()
        if args.args and len(args.args) >= 2:
            enc.set_init(" ".join(args.args[:-1]), int(args.args[-1]))
        else:
            enc.roll_initiative(reroll=args.reroll)
        show_status = True
    elif a == "next":
        enc.next_turn()
        show_status = args.verbose
    elif a == "status":
        out(enc.status_text(reveal=not args.players))
        return
    elif a in ("damage", "dmg", "heal"):
        if len(args.args) < 2:
            raise RoboDMError(f"Usage: combat {a} <target> <amount or dice>")
        target, amount_txt = " ".join(args.args[:-1]), args.args[-1]
        amount, rolled = roll_or_int(amount_txt)
        if rolled:
            enc.say(f"🎲 {rolled}", log=False)
        if a == "heal":
            enc.heal(target, amount, source=args.source or "")
        else:
            enc.damage(target, amount, dtype=(args.type or "").lower(), nonmagical=args.nonmagical, crit=args.crit,
                       source=args.source or "", nonlethal=args.nonlethal, silvered=args.silvered,
                       adamantine=args.adamantine)
    elif a == "temp":
        enc.set_temp_hp(" ".join(args.args[:-1]), int(args.args[-1]))
    elif a in ("condition", "cond"):
        if len(args.args) < 3 or args.args[1] not in ("add", "remove"):
            raise RoboDMError("Usage: combat condition <target> add|remove <condition> [--rounds N --save wis:13 --conc caster]")
        target, verb, cond = args.args[0], args.args[1], " ".join(args.args[2:])
        if verb == "add":
            enc.add_condition(target, cond, rounds=args.rounds, tick=args.tick, save=args.save, source=args.source or "",
                              conc=args.conc, level=args.level or 1)
        else:
            enc.remove_condition(target, cond)
    elif a == "save":
        if len(args.args) < 2:
            raise RoboDMError("Usage: combat save <ability> <target> [target…] --dc N [--damage 8d6 --type fire]")
        ability, targets = args.args[0], args.args[1:]
        dc, damage, dtype, half = args.dc, args.damage, (args.type or "").lower(), not args.no_half
        if args.action_name:
            if not args.by:
                raise RoboDMError("--action needs --by <creature using it>.")
            user = enc.find(args.by)
            atk = next((x for x in user.get("attacks", []) if x["name"].lower().startswith(args.action_name.lower())), None)
            if not atk or "save_dc" not in atk:
                raise RoboDMError(f"{user['name']} has no saving-throw action '{args.action_name}'.")
            if atk.get("recharge"):
                if atk["name"] in user.get("recharge_spent", []):
                    raise RoboDMError(f"{atk['name']} hasn't recharged yet.")
                user.setdefault("recharge_spent", []).append(atk["name"])
            dc = dc or atk["save_dc"]
            if not damage and atk.get("damage"):
                damage = "+".join(d[0] for d in atk["damage"])
                dtype = dtype or atk["damage"][0][1]
            half = half and atk.get("half_on_save", True)
            enc.say(f"{user['name']} uses **{atk['name']}** (DC {dc} {atk['save_ability'].upper()})")
            ability = ability if ability != "-" else atk["save_ability"]
        if dc is None:
            raise RoboDMError("Give --dc.")
        results = {}
        for pair in parse_csv_list(args.results):
            k, _, v = pair.partition("=")
            results[k.strip()] = int(v)
        if args.result is not None and len(targets) == 1:
            results[targets[0]] = args.result
        enc.saving_throw(targets, ability, int(dc), damage=damage, dtype=dtype, half=half, on_fail=args.on_fail,
                         rounds=args.rounds, repeat=args.repeat, conc=args.conc, source=args.source or "",
                         adv=args.adv, dis=args.dis, results=results, ends=args.ends, concentration=args.concentration,
                         nonmagical=args.nonmagical)
    elif a == "attack":
        if len(args.args) != 2:
            raise RoboDMError("Usage: combat attack <attacker> <target> [--action NAME | --to-hit N --damage \"1d8+3 slashing\"]")
        dmg = combat.parse_damage_spec(args.damage) if args.damage else None
        enc.attack(args.args[0], args.args[1], action=args.action_name, to_hit=args.to_hit, damage=dmg, adv=args.adv,
                   dis=args.dis, ranged=args.ranged, nonmagical=args.nonmagical, result=args.result, nat=args.nat,
                   damage_result=args.damage_result, nonlethal=args.nonlethal, silvered=args.silvered)
    elif a in ("conc", "concentrate", "concentration"):
        if not args.args:
            raise RoboDMError("Usage: combat conc <caster> <spell>   |   combat conc <caster> --end")
        enc.set_concentration(args.args[0], None if args.end else " ".join(args.args[1:]) or None)
    elif a == "deathsave":
        enc.death_save(" ".join(args.args), result=args.result)
    elif a == "stabilize":
        enc.stabilize(" ".join(args.args))
    elif a == "legendary":
        enc.use_legendary(args.args[0], cost=args.cost or 1, action=" ".join(args.args[1:]))
    elif a == "remove":
        enc.remove(" ".join(args.args))
    elif a == "note":
        if len(args.args) < 1:
            raise RoboDMError("Usage: combat note <target> \"text\"   (e.g. position \"C4, behind the cart\"; empty text clears)")
        cb = enc.find(args.args[0])
        cb["notes"] = " ".join(args.args[1:])
        enc.say(f"{cb['name']} note: {cb['notes'] or '(cleared)'}", log=False)
    elif a == "log":
        for line in enc.state.get("log", [])[-(args.n or 20):]:
            out(line)
        return
    elif a == "end":
        enc.end(award_xp=args.award_xp)
        for m in enc.messages:
            out(m)
        return
    elif a == "attacks":
        cb = enc.find(" ".join(args.args))
        for atk in cb.get("attacks", []):
            bits = []
            if "to_hit" in atk:
                bits.append(f"{fmt_mod(atk['to_hit'])} to hit")
            if atk.get("damage"):
                bits.append(", ".join(f"{d[0]} {d[1]}".strip() for d in atk["damage"]))
            if atk.get("save_dc"):
                bits.append(f"DC {atk['save_dc']} {atk['save_ability'].upper()} save" + (" (half on success)" if atk.get("half_on_save") else ""))
            if atk.get("recharge"):
                bits.append(f"recharge {atk['recharge']}–6")
            out(f"- {atk['name']}: {'; '.join(bits)}")
        return
    else:
        raise RoboDMError(f"Unknown combat action '{a}'.")
    enc.save()
    for m in enc.messages:
        out(m)
    if show_status:
        out("")
        out(enc.status_text())


# =============================================================================== session / memory / quests

def cmd_session(args) -> None:
    c = get_camp(args)
    a = args.action
    if a == "start":
        was_active = c.session.get("active")
        n = c.start_session(args.text or "")
        out(f"Session {n} {'already in progress' if was_active else 'started'} — log: campaigns/{c.slug}/journal/session-{n:03d}.md")
    elif a == "log":
        if not args.text:
            raise RoboDMError("Usage: session log \"what happened\"")
        c.log(args.text)
        out("Logged.")
    elif a == "end":
        summary = read_text(args, "summary")
        if not summary:
            raise RoboDMError("Give a summary: session end --summary \"…\" (or --summary - to read stdin)")
        n = c.end_session(summary)
        out(f"Session {n} closed and summarised. Remember to commit & push campaigns/{c.slug}/ so it persists.")
    elif a == "show":
        n = int(args.text) if args.text else c.session.get("number", 0)
        p = c.journal_path(n)
        out(p.read_text(encoding="utf-8") if p.exists() else f"No journal for session {n}.")
    elif a == "list":
        for n, s in reversed(c.session_summaries(limit=999)):
            out(f"- Session {n}: {truncate(s, 200)}")


def cmd_memory(args) -> None:
    c = get_camp(args)
    a = args.action
    if a == "add":
        text = read_text(args)
        if not text:
            raise RoboDMError("Usage: memory add \"fact\" [--type choice] [--tags a,b] [--important]")
        m = memory.add(c, text, args.type or "event", parse_csv_list(args.tags), important=args.important)
        out(f"Remembered {memory.fmt_memory(m)}")
    elif a == "search":
        hits = memory.search(c, args.text or "", limit=args.limit or 12, scope=args.scope or "all")
        if not hits:
            out("Nothing found.")
        for h in hits:
            out(f"- ({h['kind']}) {h['show']}")
    elif a == "list":
        mems = memory.list_memories(c, mtype=args.type, recent=args.limit, tag=args.tags)
        for m in mems:
            out(f"- {memory.fmt_memory(m)}")
        if not mems:
            out("No memories yet.")
    elif a == "edit":
        imp = True if args.important else (False if args.not_important else None)
        m = memory.update(c, args.text, text=args.new_text, important=imp)
        out(f"Updated {memory.fmt_memory(m)}")
    elif a == "forget":
        m = memory.update(c, args.text, forget=True)
        out(f"Retired {m['id']} (kept in the file, hidden from brief/search).")
    elif a == "brief":
        out(memory.brief(c, recent=args.limit or 15))


def cmd_quest(args) -> None:
    c = get_camp(args)
    a = args.action
    if a == "add":
        q = memory.quest_add(c, args.text, notes=args.notes or "", giver=args.giver or "")
        out(f"New quest {memory.fmt_quest(q)}")
        c.log(f"New quest: {q['title']}")
    elif a == "list":
        qs = c.quests() if args.all else [q for q in c.quests() if q["status"] == "open"]
        for q in qs:
            out(f"- {memory.fmt_quest(q)}")
        if not qs:
            out("No quests.")
    elif a in ("update", "done", "fail", "abandon", "reopen"):
        status = {"done": "done", "fail": "failed", "abandon": "abandoned", "reopen": "open"}.get(a)
        q = memory.quest_update(c, args.text, status=status, note=args.notes)
        out(f"Quest {memory.fmt_quest(q)}")
        if status:
            c.log(f"Quest {status}: {q['title']}")


# =============================================================================== lore

def cmd_lore(args) -> None:
    c = get_camp(args)
    a = args.action
    if a == "add":
        text = read_text(args)
        p = lore.add(c, args.name, args.type or "other", text, summary=args.summary or "", tags=args.tags or "",
                     status=args.status or "", related=args.related or "", aliases=args.aliases or "",
                     canon=not args.draft, overwrite=args.overwrite)
        out(f"Saved lore: {p.relative_to(c.dir).as_posix()}")
    elif a == "list":
        entries = lore.all_entries(c)
        if args.type:
            entries = [e for e in entries if e["meta"].get("type") == args.type]
        if not entries:
            out("No lore yet.")
        for e in sorted(entries, key=lambda x: (str(x["meta"].get("type")), str(x["meta"].get("name")))):
            out(f"- {lore.fmt_entry(e)}")
    elif a == "show":
        out(lore.fmt_entry(lore.find(c, args.name), full=True))
    elif a == "search":
        hits = lore.search(c, args.name)
        if not hits:
            out("Nothing found.")
        for h in hits:
            out(f"- **{h['name']}** ({h['type']}) — {truncate(h['summary'] or h['text'], 150)}")
    elif a == "update":
        fields = {"summary": args.summary, "status": args.status, "tags": args.tags, "type": args.type,
                  "related": args.related, "aliases": args.aliases}
        if args.canon:
            fields["canon"] = "true"
        if args.draft:
            fields["canon"] = "false"
        body = read_text(args) if (args.text or args.file) else None
        p = lore.update(c, args.name, append=args.append or "", fields=fields, body=body)
        out(f"Updated {p.relative_to(c.dir).as_posix()}")
    elif a == "index":
        p = lore.build_index(c)
        out(f"Wrote {p.relative_to(c.dir).as_posix()}")
        out(p.read_text(encoding="utf-8"))
    elif a == "source":
        sub = args.name
        if sub == "add":
            p = lore.add_source(c, file=args.file, text=read_text(args) if args.text else None, title=args.title or "")
            out(f"Stored reference: {p.relative_to(c.dir).as_posix()} — now build entries from it (lore-keeper agent or /lore).")
        else:
            for p in lore.list_sources(c):
                out(f"- {p.relative_to(c.dir).as_posix()} ({p.stat().st_size // 1024} KB)")
    elif a == "types":
        out(", ".join(lore.LORE_TYPES))


# =============================================================================== context blocks

def cmd_context(args) -> None:
    camp = maybe_camp(args)
    a = args.action
    if a == "list":
        blocks = context_blocks.all_blocks(camp)
        if not blocks:
            out("No context blocks found.")
        for b in sorted(blocks, key=lambda x: (not x["enabled"], x["origin"], x["name"])):
            m = b["meta"]
            out(f"{'[x]' if b['enabled'] else '[ ]'} {b['name']} ({b['origin']}, {m.get('scope', 'always')}, p{m.get('priority', 50)})"
                f" — {m.get('description') or m.get('title', '')}")
    elif a == "show":
        b = context_blocks.find(camp, args.name)
        out(f"# {b['meta'].get('title', b['name'])}  [{b['origin']} | {'enabled' if b['enabled'] else 'disabled'}]\n")
        out(b["body"])
    elif a in ("enable", "disable"):
        if camp is None:
            raise RoboDMError("No active campaign.")
        for name in parse_csv_list(args.name):
            b = context_blocks.set_enabled(camp, name, a == "enable")
            out(f"{'Enabled' if b['enabled'] else 'Disabled'} {b['name']}.")
    elif a == "add":
        text = read_text(args)
        if not text:
            raise RoboDMError("Give the block text with --text \"…\", --text - (stdin) or --file path.")
        p = context_blocks.add(camp, args.name, text, title=args.title or "", scope=args.scope or "always",
                               priority=args.priority or 50, description=args.description or "", to_library=args.library)
        out(f"Saved block {p.name} ({'shared library, disabled until enabled per campaign' if args.library else 'campaign, enabled'}).")
    elif a == "remove":
        if camp is None:
            raise RoboDMError("No active campaign.")
        p = context_blocks.remove(camp, args.name)
        out(f"Deleted {p.name}.")
    elif a == "render":
        out(context_blocks.render(camp, scope=args.scope))


# =============================================================================== rules

def cmd_rules(args) -> None:
    from robodm.rules import fetch as fetch_mod
    from robodm.rules.format import format_record, summary_line
    from robodm.rules.index import normalize_type

    a = args.action
    data = paths.data_dir()
    if a == "fetch":
        if args.if_missing and fetch_mod.is_fetched(data):
            out(f"5etools data already present in {data}.")
            return
        books = fetch_mod.DEFAULT_BOOKS if args.books is None else \
            [b for b in parse_csv_list(args.books) if b.lower() != "none"]
        fetch_mod.fetch(data, mirror=args.mirror or "auto", sources=parse_csv_list(args.sources) or None,
                        books=books or None, log=out)
        db = rules_db()
        db._index = None
        out(f"Indexed {len(db.index):,} entries.")
        return
    if a == "status":
        meta = json.loads((data / "_meta.json").read_text()) if (data / "_meta.json").exists() else None
        if not meta:
            out(f"No local data in {data}. Run `./dm rules fetch`.")
            return
        db = rules_db()
        counts: dict[str, int] = {}
        for r in db.index:
            counts[r["t"]] = counts.get(r["t"], 0) + 1
        out(f"5etools data: {data}\nFetched {meta['fetched_at']} from {meta['mirror']} ({meta['bytes'] / 1e6:.1f} MB, sources: {meta['sources']})")
        out("Entries: " + ", ".join(f"{k} {v:,}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))
        return
    if a == "encounter":
        from robodm.rules import encounter as enc_mod

        levels = enc_mod.parse_party(args.party or _party_spec(args))
        db = rules_db() if rules_db().available else None
        res = enc_mod.rate_named(db, levels, args.monsters or "")
        _print_encounter(res)
        return
    db = rules_db()
    if a == "list" and not args.type and args.query:
        args.type = args.query[0]
    t = normalize_type(args.type) if getattr(args, "type", None) else None
    if a == "search":
        q = " ".join(args.query)
        hits = db.fulltext(q, t, limit=args.limit or 15) if args.text else db.search(q, t, limit=args.limit or 15, source=args.source)
        if not hits:
            out(f"No matches for '{q}'. Try --text for a full-text search, or a different --type.")
        for h in hits:
            out(f"- {summary_line(h)}" + (f"\n    {h['snippet']}" if h.get("snippet") else ""))
        return
    if a == "show":
        q = " ".join(args.query)
        rec, alts = db.lookup(q, t, args.source)
        if not rec:
            out(f"No exact entry for '{q}'.")
            if alts:
                out("Did you mean: " + "; ".join(summary_line(r) for r in alts[:8]))
            return
        from robodm.util import norm_name

        if norm_name(q) not in [norm_name(x) for x in [rec["n"], *rec.get("al", [])]]:
            out(f"_(closest match for '{q}')_")
        out(format_record(db, rec))
        if alts:
            out("\n_Other matches: " + "; ".join(summary_line(r) for r in alts[:6]) + "_")
        return
    if a == "list":
        if not t:
            raise RoboDMError("Usage: rules list <type> [filters]  e.g. rules list spell --class wizard --level 3")
        rows = [r for r in db.index if r["t"] == t]
        if args.level is not None:
            key = "lvl" if t == "spell" else "lv"
            rows = [r for r in rows if r.get(key) == args.level]
        if args.cls:
            rows = [r for r in rows if args.cls.lower() in [x.lower() for x in r.get("cls", [])] or (r.get("cl") or "").lower() == args.cls.lower()]
        if args.school:
            rows = [r for r in rows if r.get("sch", "").lower() == args.school[0].lower() or
                    (args.school.lower() == "evocation" and r.get("sch") == "V")]
        if args.cr:
            rows = [r for r in rows if r.get("cr") == args.cr]
        if args.mtype:
            rows = [r for r in rows if args.mtype.lower() in r.get("mt", "").lower()]
        if args.env:
            rows = [r for r in rows if args.env.lower() in [e.lower() for e in r.get("env", [])]]
        if args.source:
            rows = [r for r in rows if r["s"].upper() == args.source.upper()]
        if args.rarity:
            rows = [r for r in rows if (r.get("rar") or "").lower() == args.rarity.lower()]
        rows.sort(key=lambda r: (r.get("lvl", r.get("lv", 0)) or 0, r["n"]))
        for r in rows[: args.limit or 60]:
            out(f"- {summary_line(r)}")
        out(f"({len(rows)} match{'es' if len(rows) != 1 else ''}{', showing ' + str(args.limit or 60) if len(rows) > (args.limit or 60) else ''})")
        return
    raise RoboDMError(f"Unknown rules action '{a}'.")


def _party_spec(args) -> str:
    camp = maybe_camp(args)
    if camp and camp.party():
        return ",".join(str(pc.get("level", 1)) for pc in camp.party())
    raise RoboDMError("Give --party (e.g. 4x3), or add PCs to the campaign.")


def _print_encounter(res: dict) -> None:
    for line in res.get("lines", []):
        out(f"- {line}")
    th = res["thresholds"]
    out(f"Party levels {res['party']} → thresholds: easy {th['easy']:,} · medium {th['medium']:,} · hard {th['hard']:,} · deadly {th['deadly']:,}")
    out(f"Base XP {res['base_xp']:,} × {res['multiplier']} ({res['monsters']} monsters) = adjusted {res['adjusted_xp']:,} → **{res['difficulty'].upper()}**")
    out(f"XP award: {res['base_xp']:,} total ({res['xp_per_pc']:,} per PC)")


# =============================================================================== random

def cmd_random(args) -> None:
    a = args.action
    n = args.count or 1
    if a == "name":
        for _ in range(n):
            out(random_tables.name(args.culture or "human", with_family=not args.first_only))
    elif a == "npc":
        for _ in range(n):
            p = random_tables.npc(args.culture)
            out(f"**{p['name']}** ({p['race']} {p['occupation']}) — {p['trait']}; wants to {p['motivation']}; secret: {p['secret']}")
    elif a == "tavern":
        for _ in range(n):
            t = random_tables.tavern()
            out(f"**{t['name']}** — keeper {t['keeper']}; {t['quirk']}")
    elif a == "weather":
        for _ in range(n):
            out(random_tables.weather(args.climate or "temperate"))
    elif a == "hook":
        for _ in range(n):
            out(f"- {random_tables.hook()}")
    elif a == "pick":
        options = parse_csv_list(args.options)
        if not options:
            raise RoboDMError("Usage: random pick \"a, b, c\"")
        out(random_tables.pick_from(options))
    elif a == "encounter":
        from robodm.rules import encounter as enc_mod

        camp = maybe_camp(args)
        levels = enc_mod.parse_party(args.party or _party_spec(args))
        srcs = camp.settings.get("sources") if camp else None
        res = enc_mod.build_random(rules_db(), levels, difficulty=args.difficulty or "medium", env=args.env,
                                   mtype=args.mtype, sources=srcs or None)
        _print_encounter(res)
        p = res["pick"]
        out(f"Add it: `./dm combat add \"{p['name']}\" --count {p['count']}`")
    elif a == "item":
        db = rules_db()
        rarity = (args.rarity or "uncommon").lower()
        pool = [r for r in db.index if r["t"] == "item" and (r.get("rar") or "").lower() == rarity]
        camp = maybe_camp(args)
        srcs = {s.upper() for s in (camp.settings.get("sources") or [])} if camp else set()
        if srcs:
            pool = [r for r in pool if r["s"].upper() in srcs] or pool
        if not pool:
            raise RoboDMError(f"No {rarity} items in local data.")
        for _ in range(n):
            r = pool[dice.RNG.randrange(len(pool))]
            out(f"- {r['n']} ({r['s']}, {rarity}) — `./dm rules show \"{r['n']}\" --type item`")


# =============================================================================== misc

def cmd_export(args) -> None:
    c = get_camp(args)
    if args.markdown:
        p = bundle.export_markdown(c, Path(args.out) if args.out else None)
        out(f"Wrote readable digest of {c.name} → {p} (for a Claude Project's knowledge; not re-importable)")
        return
    p = bundle.export(c, Path(args.out) if args.out else None)
    out(f"Exported {c.name} → {p}")


def cmd_import(args) -> None:
    c = bundle.import_bundle(Path(args.file), slug=args.as_slug, overwrite=args.overwrite)
    out(f"Imported {c.name} as campaigns/{c.slug}/ (now active).")


def cmd_status(args) -> None:
    from robodm.rules import fetch as fetch_mod

    out(f"RoboDM {__version__}")
    data = paths.data_dir()
    out(f"Rules data: {'ready (' + str(data) + ')' if fetch_mod.is_fetched(data) else 'NOT FETCHED — run ./dm rules fetch'}")
    camps = campaign.list_campaigns()
    active = campaign.active_slug()
    out(f"Campaigns: {', '.join(c['slug'] for c in camps) or 'none'}; active: {active or 'none'}")
    if active:
        c = campaign.load(active)
        s = c.session
        out(f"Session: {s.get('number', 0)} ({'in progress' if s.get('active') else 'not running'}) · party: "
            f"{', '.join(pc['name'] for pc in c.party()) or 'none'}")
        enc = combat.Encounter(c)
        if enc.active:
            out(enc.status_text())


# =============================================================================== parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dm", description="RoboDM — D&D 5e (2014) toolkit for Claude.")
    p.add_argument("-c", "--campaign", help="campaign slug (default: the active campaign)")
    p.add_argument("--version", action="version", version=f"RoboDM {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("roll", help="roll dice: 1d20+5, 2d6+3, 4d6kh3, stats")
    r.add_argument("expr", nargs="*")
    r.add_argument("--adv", action="store_true")
    r.add_argument("--dis", action="store_true")
    r.add_argument("--crit", action="store_true", help="critical hit: double the dice")
    r.add_argument("--label", "-l")
    r.add_argument("--times", "-n", type=int, default=1)
    r.add_argument("--method", choices=["4d6", "3d6", "standard"], help="for `roll stats`")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_roll)

    cp = sub.add_parser("campaign", help="create / switch / configure campaigns")
    cp.add_argument("action", choices=["new", "list", "use", "show", "set", "note", "delete"])
    cp.add_argument("name", nargs="?", help="campaign name, slug, or note text")
    cp.add_argument("key", nargs="?")
    cp.add_argument("value", nargs="?")
    cp.add_argument("--premise")
    cp.add_argument("--setting")
    cp.add_argument("--tone")
    cp.add_argument("--slug")
    cp.add_argument("--replace", action="store_true")
    cp.add_argument("--yes", action="store_true")
    cp.set_defaults(func=_campaign_dispatch)

    pc = sub.add_parser("pc", help="player characters: add, show, check, save, hp, slots, rest…")
    pc.add_argument("action", choices=["add", "list", "show", "json", "set", "hp", "temp", "check", "save", "hitdie",
                                       "rest", "slot", "resource", "attack", "item", "gold", "xp", "levelup",
                                       "condition", "delete"])
    pc.add_argument("name", nargs="?")
    pc.add_argument("value", nargs="?")
    pc.add_argument("extra", nargs="?")
    pc.add_argument("--player")
    pc.add_argument("--race")
    pc.add_argument("--class", dest="cls")
    pc.add_argument("--subclass")
    pc.add_argument("--level", type=int, default=1)
    pc.add_argument("--background")
    for ab in ABILITIES:
        pc.add_argument(f"--{ab}", type=int)
    pc.add_argument("--ac", type=int)
    pc.add_argument("--hp", type=int)
    pc.add_argument("--speed", type=int)
    pc.add_argument("--init", type=int)
    pc.add_argument("--saves", help="comma list, default from class")
    pc.add_argument("--skills", help="comma list of proficient skills")
    pc.add_argument("--expertise")
    pc.add_argument("--jack", action="store_true", help="Jack of All Trades")
    pc.add_argument("--spell-ability")
    pc.add_argument("--notes")
    pc.add_argument("--adv", action="store_true")
    pc.add_argument("--dis", action="store_true")
    pc.add_argument("--dc", type=int)
    pc.add_argument("--auto", action="store_true", help="roll even in players dice mode")
    pc.add_argument("--max", type=int)
    pc.add_argument("--reset", choices=["short", "long"])
    pc.add_argument("--n", type=int)
    pc.add_argument("--to-hit", type=int)
    pc.add_argument("--damage")
    pc.set_defaults(func=cmd_pc)

    cb = sub.add_parser("combat", help="initiative, HP, damage, conditions, saves, attacks")
    cb.add_argument("action", choices=["start", "add", "party", "init", "next", "status", "damage", "dmg", "heal", "temp",
                                       "condition", "cond", "save", "attack", "attacks", "conc", "concentrate",
                                       "concentration", "deathsave", "stabilize", "legendary", "remove", "note", "log",
                                       "end"])
    cb.add_argument("args", nargs="*")
    cb.add_argument("--monster", help="5etools monster to pull stats from")
    cb.add_argument("--source", help="sourcebook for --monster, or damage source text")
    cb.add_argument("--count", type=int, default=1)
    cb.add_argument("--hp", type=int)
    cb.add_argument("--ac", type=int)
    cb.add_argument("--init-bonus", type=int)
    cb.add_argument("--init", type=int)
    cb.add_argument("--side", default="enemy", choices=["enemy", "party", "ally", "neutral"])
    cb.add_argument("--roll-hp", action="store_true")
    cb.add_argument("--reroll", action="store_true")
    cb.add_argument("--players", action="store_true", help="status safe to show players (no monster HP)")
    cb.add_argument("--verbose", "-v", action="store_true")
    cb.add_argument("--type", help="damage type")
    cb.add_argument("--nonmagical", action="store_true")
    cb.add_argument("--silvered", action="store_true")
    cb.add_argument("--adamantine", action="store_true")
    cb.add_argument("--crit", action="store_true")
    cb.add_argument("--nonlethal", action="store_true")
    cb.add_argument("--rounds", type=int)
    cb.add_argument("--tick", choices=["start", "end"], default="end")
    cb.add_argument("--save", help="repeat save to end a condition, e.g. wis:13")
    cb.add_argument("--conc", help="condition ends when this caster's concentration ends")
    cb.add_argument("--level", type=int)
    cb.add_argument("--dc", type=int)
    cb.add_argument("--damage")
    cb.add_argument("--no-half", action="store_true")
    cb.add_argument("--on-fail")
    cb.add_argument("--repeat", choices=["start", "end"])
    cb.add_argument("--ends", help="condition removed on a successful save")
    cb.add_argument("--concentration", action="store_true", help="this save is a concentration check")
    cb.add_argument("--results", help="player-rolled totals: mira=14,thorin=9")
    cb.add_argument("--result", type=int, help="player-rolled total (attack, save or death save)")
    cb.add_argument("--nat", type=int, help="natural d20 for a player-rolled attack")
    cb.add_argument("--damage-result", type=int)
    cb.add_argument("--action", dest="action_name", help="named attack/action from the stat block or PC sheet")
    cb.add_argument("--by", help="creature using a saving-throw action")
    cb.add_argument("--to-hit", type=int)
    cb.add_argument("--adv", action="store_true")
    cb.add_argument("--dis", action="store_true")
    cb.add_argument("--ranged", action="store_true")
    cb.add_argument("--end", action="store_true")
    cb.add_argument("--cost", type=int)
    cb.add_argument("--n", type=int)
    cb.add_argument("--award-xp", action="store_true")
    cb.set_defaults(func=cmd_combat)

    se = sub.add_parser("session", help="start / log / end a play session")
    se.add_argument("action", choices=["start", "log", "end", "show", "list"])
    se.add_argument("text", nargs="?")
    se.add_argument("--summary")
    se.add_argument("--file")
    se.set_defaults(func=cmd_session)

    me = sub.add_parser("memory", help="long-term memory: add, search, list, brief")
    me.add_argument("action", choices=["add", "search", "list", "edit", "forget", "brief"])
    me.add_argument("text", nargs="?", help="fact text, search query or memory id")
    me.add_argument("--type", choices=memory.MEMORY_TYPES)
    me.add_argument("--tags")
    me.add_argument("--important", action="store_true")
    me.add_argument("--not-important", action="store_true")
    me.add_argument("--new-text")
    me.add_argument("--limit", type=int)
    me.add_argument("--scope", choices=["all", "memory", "journal", "lore", "party", "quests"])
    me.add_argument("--file")
    me.set_defaults(func=cmd_memory)

    qu = sub.add_parser("quest", help="track quests")
    qu.add_argument("action", choices=["add", "list", "update", "done", "fail", "abandon", "reopen"])
    qu.add_argument("text", nargs="?", help="title or quest id")
    qu.add_argument("--notes")
    qu.add_argument("--giver")
    qu.add_argument("--all", action="store_true")
    qu.set_defaults(func=cmd_quest)

    lo = sub.add_parser("lore", help="world lore entries and reference sources")
    lo.add_argument("action", choices=["add", "list", "show", "search", "update", "index", "source", "types"])
    lo.add_argument("name", nargs="?")
    lo.add_argument("--type", choices=lore.LORE_TYPES)
    lo.add_argument("--text")
    lo.add_argument("--file")
    lo.add_argument("--summary")
    lo.add_argument("--tags")
    lo.add_argument("--status")
    lo.add_argument("--related")
    lo.add_argument("--aliases")
    lo.add_argument("--append")
    lo.add_argument("--title")
    lo.add_argument("--draft", action="store_true")
    lo.add_argument("--canon", action="store_true")
    lo.add_argument("--overwrite", action="store_true")
    lo.set_defaults(func=cmd_lore)

    cx = sub.add_parser("context", help="instruction context blocks (house rules, tone, safety…)")
    cx.add_argument("action", choices=["list", "show", "enable", "disable", "add", "remove", "render"])
    cx.add_argument("name", nargs="?")
    cx.add_argument("--text")
    cx.add_argument("--file")
    cx.add_argument("--title")
    cx.add_argument("--scope", choices=context_blocks.SCOPES)
    cx.add_argument("--priority", type=int)
    cx.add_argument("--description")
    cx.add_argument("--library", action="store_true", help="save to the shared library instead of the campaign")
    cx.set_defaults(func=cmd_context)

    ru = sub.add_parser("rules", help="local 5etools rules data: fetch, search, show, list, encounter")
    ru.add_argument("action", choices=["fetch", "status", "search", "show", "list", "encounter"])
    ru.add_argument("query", nargs="*")
    ru.add_argument("--type", help="spell, monster, item, condition, feat, race, class, classfeature, rule, quickref, book…")
    ru.add_argument("--source", help="sourcebook id, e.g. PHB, MM, XGE")
    ru.add_argument("--text", action="store_true", help="full-text search inside entries")
    ru.add_argument("--limit", type=int)
    ru.add_argument("--level", type=int)
    ru.add_argument("--class", dest="cls")
    ru.add_argument("--school")
    ru.add_argument("--cr")
    ru.add_argument("--mtype", help="monster type, e.g. undead")
    ru.add_argument("--env", help="monster environment, e.g. forest")
    ru.add_argument("--rarity")
    ru.add_argument("--party", help="e.g. 4x3 or 3,3,4,5 (default: current party)")
    ru.add_argument("--monsters", help="e.g. \"goblin x4, bugbear\" or \"cr 1/2 x3\"")
    ru.add_argument("--mirror", choices=["auto", "github", "site"])
    ru.add_argument("--sources", help="fetch only these books' spells/monsters, e.g. PHB,MM,XGE")
    ru.add_argument("--books", help="full rulebook text to fetch (default PHB,DMG,XGE,TCE; 'none' to skip)")
    ru.add_argument("--if-missing", action="store_true")
    ru.set_defaults(func=_rules_dispatch)

    ra = sub.add_parser("random", help="random names, NPCs, taverns, weather, hooks, encounters, loot")
    ra.add_argument("action", choices=["name", "npc", "tavern", "weather", "hook", "pick", "encounter", "item"])
    ra.add_argument("options", nargs="?", help="for pick: comma-separated options")
    ra.add_argument("--culture", help="human, elf, dwarf, halfling, gnome, orc, tiefling, dragonborn, half-elf, half-orc")
    ra.add_argument("--first-only", action="store_true")
    ra.add_argument("--count", "-n", type=int)
    ra.add_argument("--climate", choices=["temperate", "cold", "hot"])
    ra.add_argument("--party")
    ra.add_argument("--difficulty", choices=["easy", "medium", "hard", "deadly"])
    ra.add_argument("--env")
    ra.add_argument("--mtype")
    ra.add_argument("--rarity", choices=["common", "uncommon", "rare", "very rare", "legendary", "artifact"])
    ra.set_defaults(func=cmd_random)

    ex = sub.add_parser("export", help="save the campaign to one JSON file (for claude.ai or backups)")
    ex.add_argument("--out")
    ex.add_argument("--markdown", action="store_true", help="readable digest instead of a re-importable save")
    ex.set_defaults(func=cmd_export)

    im = sub.add_parser("import", help="load a campaign save file")
    im.add_argument("file")
    im.add_argument("--as", dest="as_slug")
    im.add_argument("--overwrite", action="store_true")
    im.set_defaults(func=cmd_import)

    st = sub.add_parser("status", help="quick overview of data, campaign, session and combat")
    st.set_defaults(func=cmd_status)
    return p


def _campaign_dispatch(args) -> None:
    if args.action == "set" and args.name and args.key is not None and args.value is None:
        args.key, args.value = args.name, args.key  # `campaign set dice_mode players`
    elif args.action == "set" and args.name and args.key and args.value:
        args.campaign = args.campaign or args.name
    cmd_campaign(args)


def _rules_dispatch(args) -> None:
    cmd_rules(args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except RoboDMError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
