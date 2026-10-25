"""`./dm` — the RoboDM command line. Run `./dm <command> --help` for details."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from robodm import __version__, audit, bundle, campaign, combat, context_blocks, dice, hooks, lore, memory, paths, random_tables
from robodm import builder, receipts, snapshots, tactics, world
from robodm.tactics import turn_problems as _turn_problems
from robodm.util import (
    ABILITIES,
    ABILITY_NAMES,
    RoboDMError,
    ability_mod,
    fmt_mod,
    normalize_ability,
    norm_name,
    normalize_skill,
    ordinal,
    parse_csv_list,
    truncate,
)


# =============================================================================== helpers

# Output of the running rules command, kept for its post-audit event (see main()).
_CAPTURE: list[str] | None = None


def out(text: str = "") -> None:
    if _CAPTURE is not None:
        _CAPTURE.extend(str(text).splitlines())
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
        if args.key == "turn_speed":
            from robodm import costs

            changed = costs.apply_speed(c)
            out(f"Subagent models: {', '.join(changed) if changed else 'already set'} · brief {costs.brief_budget(c):,} chars · "
                + ("routine mechanical events auto-pass the post-audit" if costs.speed(c) == "fast" else "every rules event is post-audited")
                + ". (Restart Claude Code to load changed agent files.)")
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
    if a == "build":
        _pc_build(c, args)
        return
    if a == "list":
        party = c.party()
        if not party:
            out("No characters yet.")
        for pc in party:
            out(f"- {campaign.pc_summary(pc)}")
        return
    if a == "rest" and args.name and args.name.lower() in ("all", "party"):
        _rest(c, c.party(), args)
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
            if fl == "level" and len(pc.get("classes") or []) > 1:
                raise RoboDMError(f"{pc['name']} is multiclassed: set `classes \"Wizard 3, Cleric 2\"` instead.")
            pc[fl] = int(raw)
            if fl == "level":
                pc["hit_dice"]["max"] = pc["level"]
                if pc.get("classes"):
                    pc["classes"][0]["level"] = pc["level"]
                pc["spell_slots"] = _merge_slots(pc.get("spell_slots", {}), campaign.pc_slots(pc))
        elif fl == "classes":
            classes = campaign.parse_classes(raw)
            pc["classes"] = classes
            pc["class"], pc["subclass"] = classes[0]["class"], classes[0]["subclass"]
            pc["level"] = sum(c["level"] for c in classes)
            pc["hit_dice"]["max"] = pc["level"]
            pc["spell_slots"] = _merge_slots(pc.get("spell_slots", {}), campaign.pc_slots(pc))
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
                if pc.get("classes"):
                    pc["classes"][0][fl] = raw
                pc["spell_slots"] = _merge_slots(pc.get("spell_slots", {}), campaign.pc_slots(pc))
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
    if a == "cast":
        if not args.value:
            raise RoboDMError("Usage: pc cast <name> \"<spell>\" [--slot N] [--ritual]")
        for line in _cast(c, args, pc["name"], args.value):
            out(line)
        if c.session.get("active"):
            c.log(f"{pc['name']} cast {args.value}.")
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
        _rest(c, [pc], args)
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
                for n in audit.gate(c, [], args, [("feature", res, f"{pc['name']} uses {res}")]):
                    out(n)
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
            rec = {"name": name, "to_hit": int(args.to_hit), "damage": [list(d) for d in combat.parse_damage_spec(args.damage)],
                   "kind": "ranged" if (args.range and not args.reach) else "melee"}
            if args.reach:
                rec["reach"] = int(args.reach)
            if args.range:
                rec["range"] = tactics._parse_range(args.range)
                if args.reach:
                    rec["kind"] = "both"
            attacks.append(rec)
        elif verb == "remove":
            attacks[:] = [x for x in attacks if x["name"].lower() != name.lower()]
        else:
            raise RoboDMError("Usage: pc attack <name> add|remove <attack name> …")
        c.save_pc(pc)
        out("Attacks: " + ("; ".join(f"{x['name']} {fmt_mod(x['to_hit'])} ({', '.join(' '.join(d) for d in x['damage'])})" for x in attacks) or "none"))
        return
    if a in ("equip", "unequip", "attune", "unattune"):
        item = " ".join(x for x in (args.value, args.extra) if x)
        if not item:
            raise RoboDMError(f"Usage: pc {a} <name> \"<item>\"")
        if a == "attune":
            _, problems = builder.attune_problems(pc, item, c)
            audit.gate(c, problems, args)
        fn = {"equip": builder.equip, "unequip": builder.unequip, "attune": builder.attune, "unattune": builder.unattune}[a]
        for line in fn(pc, item, c):
            out(line)
        c.save_pc(pc)
        _sync_combat_from_sheet(c)
        return
    if a == "load":
        for line in _load_report(c, pc):
            out(line)
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
        asi = {"later": 0} if (args.asi or "").strip().lower() == "later" else builder.parse_assign(args.asi)
        new, changes, problems = builder.levelup_plan(c, pc, cls=args.cls or "", subclass=args.subclass or "",
                                                      hp=args.value, asi=asi or None, feat=args.feat or "")
        if args.preview:
            out(f"**Level-up preview for {pc['name']}** (nothing saved):")
            for line in changes:
                out(line)
            for p in problems:
                out(f"⚠️ needs: {p}")
            out("Apply it with the same command without --preview.")
            return
        audit.gate(c, problems, args)
        c.save_pc(new)
        _sync_combat_from_sheet(c)
        out(f"⬆️ {new['name']} is now level {new['level']}!")
        for line in changes:
            out(line)
        c.log(f"{new['name']} reached level {new['level']}.")
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
        if verb == "add" and (args.for_ or args.until_rest):
            w = world.load(c)
            minutes = None if args.until_rest else world.parse_duration(args.for_, world.now(w))
            rec, _ = world.timer_add(c, f"{cond.lower()} on {pc['name']}", who=pc["slug"], minutes=minutes,
                                     until="long-rest" if args.until_rest else None, condition=cond.lower())
            out(f"⌛ {world.fmt_timer(world.load(c), rec)}")
        return
    if a == "delete":
        c.pc_path(pc["slug"]).unlink()
        out(f"Removed {pc['name']} from the party.")
        return
    raise RoboDMError(f"Unknown pc action '{a}'.")


def _pc_build(c: campaign.Campaign, args) -> None:
    """The Character Forge: a level-1 (or higher) character built and checked against the rules data."""
    if not args.name:
        raise RoboDMError("Usage: pc build \"Name\" --race \"Hill Dwarf\" --class Fighter --background Soldier "
                          "--scores standard --assign str=15,dex=12,con=14,int=8,wis=13,cha=10 --skills a,b [--gear …|--gold]")
    slug = campaign.slugify(args.name)
    if c.find_pc(args.name) and c.find_pc(args.name)["slug"] == slug:
        raise RoboDMError(f"{args.name} already exists.")
    method = (args.scores or "standard").lower()
    if method == "roll" and not args.assign:
        prior = audit.rolled_scores(c, slug)
        audit.gate(c, [f"scores were already rolled for {args.name} ({', '.join(map(str, prior))}): assign those "
                       "with --assign"] if prior else [], args)
        rolls = dice.roll_stats("4d6")
        scores = sorted((r["score"] for r in rolls), reverse=True)
        audit.remember_roll(c, slug, scores)
        out(f"🎲 Ability scores for {args.name} (4d6, drop lowest): " + ", ".join(
            f"**{r['score']}** [{' '.join(map(str, r['rolls']))}]" for r in rolls))
        out(f"Now assign them: the same `pc build` with `--scores roll --assign str=…,dex=…,con=…,int=…,wis=…,cha=…`.")
        return
    for need in ("race", "cls", "background"):
        if not getattr(args, need):
            raise RoboDMError(f"pc build needs --{'class' if need == 'cls' else need}.")
    pc, report, problems = builder.build(
        c, args.name, args.race, args.cls, args.background, method, builder.parse_assign(args.assign),
        asi=builder.parse_assign(args.asi), skills=parse_csv_list(args.skills), gear=parse_csv_list(args.gear),
        gold=args.gold, level=args.level or 1, subclass=args.subclass or "", player=args.player or "",
        rolled=audit.rolled_scores(c, slug), feat=args.feat or "")
    if not problems:
        report += builder.equip_all(pc, c)
    if args.preview:
        out(f"**Character Forge preview** (nothing saved):")
        out(campaign.pc_summary(pc, full=True))
        for line in report:
            out(line)
        for p in problems:
            out(f"⚠️ needs: {p}")
        out("Create it with the same command without --preview.")
        return
    audit.gate(c, problems, args)
    if problems:
        report += builder.equip_all(pc, c)
    c.save_pc(pc)
    out(f"🛠 Built {pc['name']}:")
    out(campaign.pc_summary(pc, full=True))
    for line in report:
        out(line)


def _load_report(c: campaign.Campaign, pc: dict) -> list[str]:
    """What the character carries, by weight (items found in the data), against their carrying capacity."""
    import re as _re

    db = rules_db()
    total, unknown = 0.0, []
    for raw in pc.get("inventory") or []:
        m = _re.match(r"^(\d+)\s+(.+)$", raw.strip())
        qty, name = (int(m.group(1)), m.group(2)) if m else (1, raw)
        it, _ = builder.item_entry(db, name, c)
        if it is None and name.endswith("s"):
            it, _ = builder.item_entry(db, name[:-1], c)
        if it and it.get("weight") is not None:
            total += float(it["weight"]) * qty
        else:
            unknown.append(raw)
    st = pc["abilities"]["str"]
    size = {"T": 0.5, "S": 1, "M": 1, "L": 2, "H": 4, "G": 8}.get(pc.get("size", "M"), 1)
    lines = [f"🎒 {pc['name']} carries about {total:g} lb (capacity {st * 15 * size:g} lb; push/drag/lift {st * 30 * size:g} lb)."]
    if c.settings.get("encumbrance") == "variant":
        if total > st * 10 * size:
            lines.append(f"Heavily encumbered (over {st * 10 * size:g} lb): speed −20 ft., disadvantage on STR/DEX/CON checks, "
                         "attacks and saves (PHB p.176).")
        elif total > st * 5 * size:
            lines.append(f"Encumbered (over {st * 5 * size:g} lb): speed −10 ft. (PHB p.176).")
    if total > st * 15 * size:
        lines.append("⚠️ Over carrying capacity.")
    if unknown:
        lines.append(f"(not in the item data, so not weighed: {', '.join(unknown[:12])}{'…' if len(unknown) > 12 else ''})")
    return lines


def cmd_shop(args) -> None:
    """Prices from the item data; buying and selling move gold and inventory."""
    c = get_camp(args)
    name = " ".join(args.item or [])
    if not name:
        raise RoboDMError("Usage: shop \"<item>\" [--pc mira --buy|--sell] [--qty N]")
    it, bonus = builder.item_entry(rules_db(), name, c)
    if it is None:
        raise RoboDMError(f"No item '{name}' in the data (`./dm rules search {name} --type item`).")
    value = it.get("value")
    qty = max(1, args.qty or 1)
    price = (value or 0) / 100 * qty
    head = f"🪙 {it['name']}: " + (f"{value / 100:g} gp" if value is not None else "no list price (DM sets it)") \
        + (f" each · {qty} = {price:g} gp" if qty > 1 and value is not None else "") \
        + (f" · {it['weight']:g} lb" if it.get("weight") is not None else "") \
        + (f" · {it.get('rarity')}" if it.get("rarity") not in (None, "none") else "")
    out(head)
    if not (args.buy or args.sell):
        return
    if not args.pc:
        raise RoboDMError("Say who: --pc <name>.")
    if value is None and args.price is None:
        raise RoboDMError(f"{it['name']} has no list price: give one with --price <gp>.")
    each = args.price if args.price is not None else value / 100
    pc = c.get_pc(args.pc)
    inv = pc.setdefault("inventory", [])
    label = it["name"] if qty == 1 else f"{qty} {it['name']}"
    if args.buy:
        cost = round(each * qty, 2)
        if pc.get("gold", 0) < cost:
            raise RoboDMError(f"{pc['name']} has {pc.get('gold', 0):g} gp; {label} costs {cost:g} gp.")
        pc["gold"] = round(pc.get("gold", 0) - cost, 2)
        inv.append(label)
        out(f"{pc['name']} buys {label} for {cost:g} gp → {pc['gold']:g} gp left.")
    else:
        match = next((x for x in inv if norm_name(x) == norm_name(label) or norm_name(x) == norm_name(it["name"])), None)
        if not match:
            raise RoboDMError(f"{pc['name']} doesn't carry {it['name']}.")
        gain = round(each * qty / (1 if args.price is not None else 2), 2)     # adventurers sell at half price
        inv.remove(match)
        pc["gold"] = round(pc.get("gold", 0) + gain, 2)
        out(f"{pc['name']} sells {label} for {gain:g} gp → {pc['gold']:g} gp.")
    c.save_pc(pc)
    c.log(head.replace("🪙 ", "Shop: "))


def _rest(c: campaign.Campaign, pcs: list[dict], args) -> None:
    """Short (1 hour) or long (8 hours) rest: gated (24-hour rule, at least 1 HP), then the clock moves on."""
    kind = args.value
    if kind not in ("short", "long"):
        raise RoboDMError("Usage: pc rest <name|party> short|long [--no-time]")
    audit.gate(c, world.rest_problems(c, pcs, kind), args)
    for pc in pcs:
        changes = campaign.rest(pc, kind)
        c.save_pc(pc)
        out(f"{pc['name']} — {kind} rest: {', '.join(changes) or 'nothing to restore'}")
    if kind == "long":
        for line in world.long_rest(c, [pc["slug"] for pc in pcs]):
            out(line)
    if len(pcs) > 1:
        if kind == "short":
            out("Short rest: players may spend hit dice (`./dm pc hitdie <name>`).")
        c.log(f"Party took a {kind} rest.")
    if not args.no_time:
        for line in world.advance_if_tracking(c, 8 * 60 if kind == "long" else 60, f"{kind} rest"):
            out(line)


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
    vs = None
    if args.vs is not None:
        try:
            vs = int(args.vs)
        except ValueError:
            raise RoboDMError("--vs is the opponent's total for a contest (roll theirs first), e.g. --vs 14.")
    key = f"{pc['slug']}:{label}"
    problems = []
    if args.dc is None and vs is None and not args.open:
        problems.append("set the DC before the roll: --dc N (5 very easy · 10 easy · 15 medium · 20 hard · 25 very hard · "
                        "30 nearly impossible), --vs <opponent's total> for a contest, or --open for a check with no "
                        "fixed DC (graded results; flagged to the audit)")
    prior = audit.asked(c, key) if args.result is not None else None
    if prior and (prior.get("dc") != args.dc or prior.get("vs") != (str(vs) if vs is not None else None)):
        was = f"DC {prior['dc']}" if prior.get("dc") is not None else (f"vs {prior['vs']}" if prior.get("vs") else "open")
        problems.append(f"when you asked for this roll the target was {was}; it can't change after the roll")
    audit.gate(c, problems, args)
    target = f" vs DC {args.dc}" if args.dc is not None else (f" vs {vs} (contest)" if vs is not None else "")
    if c.settings.get("dice_mode") == "players" and not args.auto and args.result is None:
        audit.remember_ask(c, key, args.dc, str(vs) if vs is not None else None)
        out(f"Ask {pc.get('player') or pc['name']} to roll: d20{fmt_mod(bonus)} for {label}"
            f"{' (advantage)' if args.adv else ''}{' (disadvantage)' if args.dis else ''}{target}. "
            f"Then record it: the same command plus `--result <total>` (or add --auto to roll for them).")
        return
    if args.dc and not 5 <= args.dc <= 30:
        out(f"⚠️ DC {args.dc} is off the PHB scale (5 very easy · 10 easy · 15 medium · 20 hard · 25 very hard · "
            "30 nearly impossible).")
    if args.result is not None:
        total = int(args.result)
        line = f"🎲 {label}: player rolled **{total}**"
        audit.asked(c, key, pop=True)
    else:
        r = dice.roll_d20(bonus, adv=args.adv, dis=args.dis, label=label)
        total = r.total
        line = "🎲 " + r.text()
    ok = None
    if args.dc is not None:
        ok = total >= args.dc
        line += f" vs DC {args.dc} → **{'success' if ok else 'failure'}**"
    elif vs is not None:
        ok = True if total > vs else (False if total < vs else None)
        line += f" vs {vs} (contest) → **{'success' if ok else 'failure' if ok is False else 'tie: nothing changes'}**"
    else:
        line += " (open check: no fixed DC)"
    receipts.note("save" if save else "check", who=pc["name"], what=skill.title() if skill else ABILITY_NAMES[ab],
                  ability=ab.upper() if save else None, total=total, dc=args.dc, vs=vs, ok=ok)
    out(line)


# =============================================================================== rules gate (pre-checks)

def _spell_record(name: str) -> tuple[dict, dict]:
    db = rules_db()
    db.require()
    rec, alts = db.lookup(name, "spell")
    if not rec:
        raise RoboDMError(f"No spell '{name}' in the local data"
                          + (f" (close: {', '.join(r['n'] for r in alts[:5])})" if alts else "")
                          + ". Homebrew? Clear it as a ruling: `./dm ruling \"…\" --key <name>`.")
    return rec, db.get(rec)


def _spell_need(name: str, use: str = "") -> tuple[str, str, str]:
    """The clearance a spell needs, under its canonical name."""
    rec, _ = _spell_record(name)
    return ("spell", rec["n"], use)


def _condition_check(name: str, args, problems: list[str], needs: list) -> None:
    n = name.lower()
    if n in combat.CONDITIONS:
        needs.append(("condition", n, ""))
    elif not getattr(args, "custom", False):
        problems.append(f"'{name}' isn't one of the 15 standard conditions — pass --custom for a spell-specific "
                        "effect (e.g. hexed)")


def _cast(c: campaign.Campaign, args, caster: str, spell_name: str, enc: combat.Encounter | None = None) -> list[str]:
    """Validate a spell before it's cast (slots, class list, casting time, turn, bonus-action rule), then cast it."""
    rec, sp = _spell_record(spell_name)
    name, level = rec["n"], int(sp.get("level", 0))
    t0 = (sp.get("time") or [{}])[0]
    unit, number = (t0.get("unit") or "action").lower(), t0.get("number", 1)
    conc, ritual = bool(rec.get("conc")), bool(getattr(args, "ritual", False))
    problems: list[str] = []
    srcs = {s.upper() for s in c.settings.get("sources") or []}
    if srcs and rec["s"].upper() not in srcs:
        problems.append(f"{name} is from {rec['s']}, which isn't in this campaign's sources ({', '.join(sorted(srcs))})")
    cb = None
    if enc is not None:
        enc.require_active()
        cb = enc.find(caster)
        pc = c.find_pc(cb["pc"]) if cb.get("pc") else None
        who = cb["name"]
    else:
        pc = c.get_pc(caster)
        who = pc["name"]
        live = combat.Encounter(c)
        if live.active and any(x.get("pc") == pc["slug"] for x in live.combatants.values()):
            raise RoboDMError(f"{who} is in combat — use `./dm combat cast {pc['slug']} \"{name}\"`.")
    if ritual and not rec.get("rit"):
        problems.append(f"{name} doesn't have the ritual tag")
    slot_level = None
    if pc:
        known = [norm_name(s) for s in pc.get("spells", [])]
        classes = [x.lower() for x in rec.get("cls", [])]
        mine = [c["class"] for c in campaign.pc_classes(pc)]
        if known and norm_name(name) not in known:
            problems.append(f"{name} isn't among {who}'s spells (`./dm pc set {pc['slug']} spells \"…\"`)")
        elif not known and classes and not any(c.lower() in classes for c in mine):
            problems.append(f"{name} isn't on the {'/'.join(mine) or '?'} spell list ({', '.join(rec['cls'])})")
        if level > 0 and not ritual:
            slots = pc.get("spell_slots") or {}
            slot_level = getattr(args, "slot", None)
            pact_pool = slots.get("pact")
            use_pact = bool(getattr(args, "pact", False))
            if slot_level is None and not use_pact:
                pact = [int(k) for k, v in slots.items() if v.get("pact") and k != "pact"]
                regular = slots.get(str(level))
                if pact_pool and pact_pool["level"] >= level and not (regular and regular["used"] < regular["max"]):
                    use_pact = True
                slot_level = pact[0] if pact and pact[0] >= level else level
            if use_pact:
                if not pact_pool:
                    problems.append(f"{who} has no separate Pact Magic slots")
                slot_level = pact_pool["level"] if pact_pool else level
                slot_key = "pact"
            else:
                slot_key = str(slot_level)
            slot = slots.get(slot_key)
            if slot_level < level:
                problems.append(f"{name} is a level {level} spell — it can't use a level {slot_level} slot")
            elif not slot:
                problems.append(f"{who} has no level {slot_level} spell slots"
                                + (f" (has levels {', '.join(sorted(slots, key=int))})" if slots else ""))
            elif slot["used"] >= slot["max"]:
                free = [k for k, v in sorted(slots.items(), key=lambda kv: int(kv[0]))
                        if int(k) > slot_level and v["used"] < v["max"]]
                problems.append(f"{who} has no level {slot_level} slots left"
                                + (f" (free: level {', '.join(free)})" if free else ""))
    if cb is not None and getattr(args, "target", None):
        problems += tactics.cast_target_problems(c, enc, cb, sp, name, enc.find_many(parse_csv_list(args.target)))
    if cb is not None:
        if unit in ("minute", "hour") or ritual:
            problems.append(f"{name} takes {number} {unit}{'s' if number != 1 else ''}"
                            f"{' plus 10 minutes as a ritual' if ritual else ''} to cast — longer than a combat turn")
        else:
            problems += _turn_problems(enc, cb, "casting", reaction=(unit == "reaction"))
            if unit in ("action", "bonus"):
                done = cb.get("spells_this_turn", [])
                cantrip_action = level == 0 and unit == "action"
                if (unit == "bonus" and any(not (s["level"] == 0 and s["unit"] == "action") for s in done)) or \
                        (any(s["unit"] == "bonus" for s in done) and not cantrip_action):
                    problems.append("bonus-action spell rule: with a bonus-action spell, the only other spell that turn "
                                    "can be a cantrip with a casting time of 1 action (PHB p.202). Already cast: "
                                    + ", ".join(s["name"] for s in done))
    clearance = audit.gate(c, problems, args, needs=[("spell", name, f"{caster} casts {name}")])

    # ---- passed the gate: cast it
    slot = None
    if pc and slot_level is not None:
        slot = (pc.get("spell_slots") or {}).get(slot_key)
        if slot and slot["used"] < slot["max"]:
            slot["used"] += 1
            c.save_pc(pc)
    if cb is not None:
        if unit == "reaction":
            cb["reaction_used"] = True
        elif unit in ("action", "bonus"):
            cb.setdefault("spells_this_turn", []).append({"name": name, "level": level, "unit": unit})
    bits = ["cantrip" if level == 0 else ("ritual, no slot" if ritual else
            (f"{ordinal(slot_level)}-level slot" if slot_level else f"level {level}"))]
    bits.append(f"{number} {'bonus action' if unit == 'bonus' else unit}{'s' if number != 1 else ''}")
    if conc:
        bits.append("concentration")
    head = f"✨ {who} casts **{name}** ({', '.join(bits)})"
    if slot:
        head += f" · {'pact' if slot_key == 'pact' else 'level ' + str(slot_level)} slots {slot['max'] - slot['used']}/{slot['max']} left"
    lines = [head]
    if cb is not None:
        enc.say(head)
        lines = []
        if conc:
            enc.set_concentration(cb["id"], name)
    elif conc:
        lines.append("(concentration — if a fight starts, track it with `./dm combat conc`)")
    lines.extend(clearance)
    minutes = world.spell_minutes(sp)
    if pc and minutes and minutes >= 10 and world.tracking(world.load(c)):
        rec, ended = world.timer_add(c, f"{name} ({who})", who=pc["slug"], minutes=minutes, conc=conc)
        lines.extend(ended)
        lines.append(f"⌛ Timer {rec['id']}: {name} lasts {world.fmt_dur(minutes)} (until {world.fmt_point(rec['ends'])})")
    if pc:
        dc = campaign.pc_spell_dc(pc)
        ref = cb["id"] if cb is not None else pc["slug"]
        if dc and sp.get("savingThrow"):
            lines.append(f"Resolve: `./dm combat save {sp['savingThrow'][0][:3]} <targets> --dc {dc} --spell \"{name}\" "
                         f"--by {ref}`")
        if dc and sp.get("spellAttack"):
            lines.append(f"Resolve: `./dm combat attack {ref} <target> --to-hit {dc - 8} --damage \"…\" --spell \"{name}\""
                         f"{' --ranged' if 'R' in sp['spellAttack'] else ''}`")
    if cb is not None:
        for line in lines:
            enc.say(line, log=False)
        return []
    return lines


# =============================================================================== combat

def _combat_setup(c: campaign.Campaign, enc: combat.Encounter, args) -> None:
    """One step from "a fight starts" to round 1: start, the party, the monsters, a plain map, places, initiative.
    Built from the ordinary commands, so every step keeps its checks."""
    import re as _re

    from robodm import runner

    if enc.active:
        raise RoboDMError(f"A fight is already running ({enc.state.get('name') or 'unnamed'}): `combat status`, "
                          "`combat add` for newcomers, or `combat end` first.")
    if not args.monsters:
        raise RoboDMError("Usage: combat setup \"Ambush\" --monsters \"goblin x2, wolf\" [--map 12x8]")
    name = " ".join(args.args) or "Fight"
    w, h = 12, 8
    if args.map:
        m = _re.fullmatch(r"(\d+)\s*x\s*(\d+)", args.map.strip().lower())
        if not m:
            raise RoboDMError("--map is WIDTHxHEIGHT in squares, e.g. 12x8")
        w, h = int(m.group(1)), int(m.group(2))
    steps = [["combat", "start", name], ["combat", "party"]]
    for part in [p.strip() for p in args.monsters.split(",") if p.strip()]:
        m = _re.fullmatch(r"(?:(\d+)\s*[x×]?\s+)?(.+?)(?:\s*[x×]\s*(\d+))?", part)
        count = int(m.group(1) or m.group(3) or 1)
        steps.append(["combat", "add", m.group(2), "--count", str(count)])
    mid = max(1, h // 2)
    steps += [["combat", "map", "new", f"{w}x{h}", name],
              ["combat", "place", "party", f"B{mid}", "enemies", f"{bm_col(w - 2)}{mid}"], ["combat", "init"]]
    for argv in steps:
        code, o, e = runner.run_cli(argv)
        if code:
            raise RoboDMError(f"combat setup stopped at `./dm {' '.join(argv)}`: {(e or o).strip()}")
    enc = combat.Encounter(c)
    out(f"⚔️ {name}: {len(enc.combatants)} combatants on a {w}×{h} map (party on the left, enemies on the right). "
        "Redraw it to match the scene with `combat map draw` if you like.")
    out(enc.status_text(reveal=True))


def bm_col(x: int) -> str:
    from robodm import battlemap

    return battlemap.col_name(x)


def cmd_combat(args) -> None:
    c = get_camp(args)
    enc = combat.Encounter(c)
    a = args.action
    show_status = False
    if a == "setup":
        _combat_setup(c, enc, args)
        return
    if a == "start":
        enc.start(" ".join(args.args) if args.args else "")
        enc.say("Next: draft the battle map from the scene (`./dm combat map draw --text -`, one character per 5-ft "
                "square; or `combat map load <saved>`), add combatants, `combat place` them, then `combat init`.", log=False)
    elif a == "map":
        changed = tactics.map_load_or_list(c, enc, args, out)
        if changed is None:
            changed = tactics.map_command(c, enc, args, out)
        if changed:
            enc.save()
        return
    elif a == "place":
        tactics.place(c, enc, args, out)
    elif a == "move":
        tactics.move(c, enc, args, out)
    elif a == "vision":
        if len(args.args) < 2 or not args.args[1].lstrip("-").isdigit():
            raise RoboDMError("Usage: combat vision <who> <feet>   (how far they see in darkness: darkvision or a carried "
                              "light, e.g. 40 for a torch; -1 to go back to the sheet's darkvision)")
        cb = enc.find(args.args[0])
        ft = int(args.args[1])
        if ft < 0:
            cb.pop("vision", None)
        else:
            cb["vision"] = ft
        tactics.refresh_fog(c, enc)
        enc.say(f"👁 {cb['name']} sees {ft} ft in darkness." if ft >= 0 else f"👁 {cb['name']}: vision from the sheet.", log=False)
    elif a == "stand":
        tactics.stand(c, enc, args)
    elif a in ("dash", "disengage"):
        tactics.flag(c, enc, args, "Dash" if a == "dash" else "Disengage")
    elif a == "area":
        tactics.area(c, enc, args, out)
    elif a == "range":
        for line in tactics.range_report(c, enc, args):
            out(line)
        return
    elif a == "add":
        if not args.args:
            raise RoboDMError("Usage: combat add <name> [--monster NAME] [--count N] [--hp N --ac N --init-bonus N]")
        enc.require_active()
        name = " ".join(args.args)
        pc = c.find_pc(name) if not args.monster and args.hp is None else None
        from robodm import npcs

        person = npcs.find_or_none(c, name) if not pc and not args.monster and args.hp is None else None
        if pc:
            enc.add_pc(pc, init=args.init)
        elif person:                     # a named NPC fights as their own sheet or stat block, never a fuzzy match
            for _ in range(max(1, args.count or 1)):
                npcs.add_to_combat(c, enc, person, side=args.side, init=args.init)
        elif args.hp is not None and not args.monster:
            enc.add_custom(name, hp=args.hp, ac=args.ac or 10, init_bonus=args.init_bonus or 0, side=args.side,
                           count=args.count, init=args.init, size=(args.size or "M").upper(), speed=args.speed or 30)
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
        audit.gate(c, tactics.init_problems(enc), args)
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
        tgt = enc.find(target)
        problems = []
        if tgt["status"] == "dead":
            problems.append(f"{tgt['name']} is dead" + (" — hit points can't bring it back (it needs a spell like "
                                                          "Revivify)" if a == "heal" else ""))
        if a != "heal" and args.type and args.type.lower() not in audit.DAMAGE_TYPES:
            problems.append(f"'{args.type}' isn't a damage type ({', '.join(audit.DAMAGE_TYPES)})")
        audit.gate(c, problems, args)
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
            problems, needs = [], []
            _condition_check(cond, args, problems, needs)
            if enc.find(target)["status"] == "dead":
                problems.append(f"{enc.find(target)['name']} is dead")
            for n in audit.gate(c, problems, args, needs):
                enc.say(n, log=False)
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
            audit.gate(c, _turn_problems(enc, user, f"using {atk['name']}", reaction=args.reaction,
                                         legendary=args.legendary), args)
            if args.legendary:
                enc.use_legendary(user["id"], cost=args.cost or 1, action=atk["name"])
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
        needs = []
        problems = [f"{t['name']} is dead" for t in enc.find_many(targets) if t["status"] == "dead"]
        if args.spell:
            needs.append(_spell_need(args.spell))
            caster = enc.find(args.by) if args.by else None
            pc = c.find_pc(caster["pc"]) if caster and caster.get("pc") else None
            expected = campaign.pc_spell_dc(pc) if pc else None
            if expected and int(dc) != expected:
                problems.append(f"{pc['name']}'s spell save DC is {expected} (8 + proficiency + "
                                f"{(pc.get('spellcasting_ability') or '?').upper()} mod), not {dc}")
        if args.on_fail:
            _condition_check(args.on_fail, args, problems, needs)
        for n in audit.gate(c, problems, args, needs):
            enc.say(n, log=False)
        bonus = None
        if "area" in [t.lower() for t in targets] and ability.lower().startswith("dex"):
            bonus = (enc.state.get("last_area") or {}).get("cover")
        enc.saving_throw(targets, ability, int(dc), damage=damage, dtype=dtype, half=half, on_fail=args.on_fail,
                         rounds=args.rounds, repeat=args.repeat, conc=args.conc, source=args.source or "",
                         adv=args.adv, dis=args.dis, results=results, ends=args.ends, concentration=args.concentration,
                         nonmagical=args.nonmagical, bonus=bonus)
    elif a == "attack":
        if len(args.args) != 2:
            raise RoboDMError("Usage: combat attack <attacker> <target> [--action NAME | --to-hit N --damage \"1d8+3 slashing\"]")
        dmg = combat.parse_damage_spec(args.damage) if args.damage else None
        att, tgt = enc.find(args.args[0]), enc.find(args.args[1])
        problems = _turn_problems(enc, att, "attacking", reaction=args.reaction, legendary=args.legendary)
        if att["id"] == tgt["id"]:
            problems.append("a creature can't attack itself")
        if tgt["status"] == "dead":
            problems.append(f"{tgt['name']} is already dead")
        pos = tactics.attack_checks(c, enc, att, tgt, args)
        problems += pos["problems"]
        for n in audit.gate(c, problems, args, [_spell_need(args.spell)] if args.spell else []):
            enc.say(n, log=False)
        if pos["distance"] is not None:
            enc.say(f"📏 {pos['distance']} ft{' · ranged' if pos['ranged'] else ''}"
                    + (f" · {'half' if pos['cover'] == 2 else 'three-quarters'} cover" if pos["cover"] in (2, 5) else ""), log=False)
        if args.reaction:
            att["reaction_used"] = True
        if args.legendary:
            enc.use_legendary(att["id"], cost=args.cost or 1, action=args.action_name or "attack")
        enc.attack(args.args[0], args.args[1], action=args.action_name, to_hit=args.to_hit, damage=dmg, adv=args.adv,
                   dis=args.dis, ranged=pos["ranged"], nonmagical=args.nonmagical, result=args.result, nat=args.nat,
                   damage_result=args.damage_result, nonlethal=args.nonlethal, silvered=args.silvered,
                   within_5ft=pos["within_5ft"], cover=pos["cover"], extra_dis=pos["dis"])
    elif a in ("conc", "concentrate", "concentration"):
        if not args.args:
            raise RoboDMError("Usage: combat conc <caster> <spell>   |   combat conc <caster> --end")
        spell = None if args.end else " ".join(args.args[1:]) or None
        if spell:
            db = rules_db()
            rec = db.lookup(spell, "spell")[0] if db.available else None
            problems = []
            if rec is None:
                problems.append(f"no spell named '{spell}' in the local data")
            elif not rec.get("conc"):
                problems.append(f"{rec['n']} isn't a concentration spell")
            for n in audit.gate(c, problems, args, [("spell", rec["n"], "")] if rec else []):
                enc.say(n, log=False)
            spell = rec["n"] if rec else spell
        enc.set_concentration(args.args[0], spell)
    elif a == "cast":
        if len(args.args) < 2:
            raise RoboDMError("Usage: combat cast <caster> \"<spell>\" [--slot N] [--ritual] [--ruling \"why\"]")
        _cast(c, args, args.args[0], " ".join(args.args[1:]), enc=enc)
    elif a == "deathsave":
        enc.death_save(" ".join(args.args), result=args.result)
    elif a == "stabilize":
        enc.stabilize(" ".join(args.args))
    elif a == "legendary":
        audit.gate(c, _turn_problems(enc, enc.find(args.args[0]), "a legendary action", legendary=True), args)
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
        rounds = enc.state.get("round", 0) or 0
        enc.end(award_xp=args.award_xp)
        for m in enc.messages:
            out(m)
        for line in world.advance_if_tracking(c, max(1, (rounds * 6 + 59) // 60), f"combat, {rounds} round(s)",
                                              offscreen=False):
            out(line)
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
        beats = world.open_beats(world.load(c))
        if beats and not args.force:
            raise RoboDMError(f"{len(beats)} off-screen beat(s) still open ({', '.join(b['id'] for b in beats)}): "
                              "resolve them first (`./dm world`), or `session end --force`.")
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
    elif a == "transcript":
        from robodm import capture

        n = int(args.text) if args.text else c.session.get("number", 0)
        rows = capture.entries(c, n)
        if not rows:
            out(f"No transcript for session {n} (it's captured automatically by the Claude Code hooks while a "
                "session is running).")
        for t, who, text in rows[-(args.n or 40):]:
            out(f"### [{t}] {who}\n{text}\n")


def cmd_memory(args) -> None:
    c = get_camp(args)
    a = args.action
    if a == "add":
        text = read_text(args)
        if not text:
            raise RoboDMError("Usage: memory add \"fact\" [--type choice] [--tags a,b] [--important]")
        mtype = args.type or "event"
        known = parse_csv_list(args.known_by)
        seen = ""
        if args.known_by is None and mtype in world.WITNESSED_TYPES:
            cast = world.scene_cast(c)
            known = [slug for slug, _ in cast]
            if cast:
                seen = (f"👁 witnessed by {', '.join(n for _, n in cast)} (from the scene; `--known-by none` if it was "
                        "private, or name who knew)")
        elif args.known_by and args.known_by.strip().lower() == "none":
            known = []
        m = memory.add(c, text, mtype, parse_csv_list(args.tags), important=args.important, known_by=known)
        out(f"Remembered {memory.fmt_memory(m)}")
        if seen:
            out(seen)
    elif a == "search":
        _print_hits(memory.search(c, [args.text or ""] + (args.or_ or []), limit=args.limit or 12,
                                  scope=args.scope or "all"))
    elif a == "list":
        mems = memory.list_memories(c, mtype=args.type, recent=args.limit, tag=args.tags, important=args.important)
        for m in mems:
            out(f"- {memory.fmt_memory(m)}")
        if not mems:
            out("No memories yet.")
    elif a == "edit":
        imp = True if args.important else (False if args.not_important else None)
        m = memory.update(c, args.text, text=args.new_text, important=imp, known_by=parse_csv_list(args.known_by))
        out(f"Updated {memory.fmt_memory(m)}")
    elif a == "forget":
        m = memory.update(c, args.text, forget=True)
        out(f"Retired {m['id']} (kept in the file, hidden from brief/search).")
    elif a == "brief":
        out(memory.brief(c, recent=args.limit or 15, budget=args.budget or "auto", full=args.full))


def _print_hits(hits: list[dict]) -> None:
    if not hits:
        out("Nothing found. Try other phrasings: names, synonyms, what it was about (`./dm recall \"a\" \"b\"`).")
    for h in hits:
        out(f"- ({h['kind']}) {h['show']}")
        if h.get("snippet") and h["snippet"] not in h["show"]:
            out(f"    … {h['snippet']}")


def cmd_recall(args) -> None:
    from robodm import recall

    c = get_camp(args)
    if args.stats:
        out(recall.stats(c))
        return
    if not args.queries:
        raise RoboDMError("Usage: recall \"phrasing 1\" [\"phrasing 2\" …] [--scope memory|journal|transcript|lore|party|quests]")
    _print_hits(recall.search(c, args.queries, limit=args.limit or 12, scope=args.scope or "all"))


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
    elif a == "mind":
        fields = {"feeling": args.feeling, "goal": args.goal, "opinion": args.opinion, "plan": args.plan}
        if not args.name or not any(fields.values()):
            raise RoboDMError("Usage: lore mind <npc> [--feeling …] [--goal …] [--opinion \"of the party\"] [--plan …]")
        e = world.set_mind(c, args.name, **fields)
        out(f"🧠 {e['meta'].get('name')}: {world.fmt_mind(e['meta'])}")


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
        meta = json.loads((data / "_meta.json").read_text(encoding="utf-8")) if (data / "_meta.json").exists() else None
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

        if norm_name(q) not in [norm_name(x) for x in [rec["n"], *rec.get("al", [])]]:
            out(f"_(closest match for '{q}')_")
        out(format_record(db, rec))
        camp = maybe_camp(args)
        if camp is not None:
            audit.note_lookup(camp, rec["n"])
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


# =============================================================================== rulings & audit

def cmd_ruling(args) -> None:
    c = get_camp(args)
    if not args.text:
        raise RoboDMError("Usage: ruling \"what you're ruling\" [--key short-name] [--items \"feature:Divine Smite,spell:Shield\"]")
    key = args.key or args.text
    needs = [("ruling", key, args.text)]
    for item in parse_csv_list(args.items):
        kind, _, name = item.partition(":")
        if not name:
            raise RoboDMError(f"Items look like kind:name (kinds: {', '.join(audit.KINDS)}), not '{item}'.")
        needs.append((kind.strip().lower(), name.strip(), ""))
    notes = audit.gate(c, [], args, needs)
    out(f"⚖️ Ruling: {args.text}")
    for n in notes:
        out(n)
    if c.session.get("active"):
        c.log(f"Ruling: {args.text}")


def costs_speed(c) -> str:
    from robodm import costs

    return costs.speed(c)


def cmd_audit(args) -> None:
    c = get_camp(args)
    a, rest = args.action, args.args

    def kind_name() -> tuple[str, str]:
        if len(rest) < 2:
            raise RoboDMError(f"Usage: audit {a} <{'|'.join(audit.KINDS)}> \"<name>\"")
        return rest[0].lower(), " ".join(rest[1:])

    if a == "brief":
        kind, name = kind_name()
        out(audit.brief(c, rules_db(), kind, name, use=args.use or ""))
    elif a == "autopass":
        passed = audit.autopass(c)
        out(f"Auto-passed {len(passed)} routine event(s): {', '.join(passed)}" if passed else
            ("Nothing to auto-pass." if costs_speed(c) == "fast" else "Auto-pass only applies with `turn_speed fast`."))
    elif a == "receipts":
        facts = audit.turn_facts(c)
        out(f"Turn {audit.current_turn(c)} — mechanical results:")
        for f in facts:
            out(f"- {receipts.fmt(f)}")
        if not facts:
            out("- (no rules results this turn)")
        reply = read_text(args, "reply") if (args.reply or args.file) else ""
        if reply:
            problems = audit.narration_problems(c, reply)
            out("")
            if problems:
                out("Your reply may not match the dice:")
                for p in problems:
                    out(f"- {p}")
            else:
                out("✅ The reply agrees with the dice.")
    elif a == "clear":
        kind, name = kind_name()
        if not args.verdict:
            raise RoboDMError("Give --verdict CORRECT (cleared) or INCORRECT (denied), plus --notes and --cite.")
        if kind in ("spell", "condition") and rules_db().available:
            rec = rules_db().lookup(name, kind)[0]
            name = rec["n"] if rec else name
        e = audit.clear(c, kind, name, args.verdict, notes=args.notes or "", cite=args.cite or "", self_check=args.self)
        out(("✅ Cleared " if e["verdict"] == "CORRECT" else "🚫 Denied ") + f"{kind} {name}"
            + (f": {e['notes']}" if e["notes"] else "") + (f" ({e['cite']})" if e["cite"] else ""))
    elif a == "pending":
        out(audit.pending_packet(c, log_lines=args.n or 30))
    elif a in ("pass", "fail", "resolve"):
        if not rest:
            raise RoboDMError(f"Usage: audit {a} <event ids | all>")
        ids = audit.parse_ids(c, rest)
        if a == "fail":
            if len(ids) != 1:
                raise RoboDMError("Fail one event at a time, each with its own --notes and --fix.")
            if not args.notes or not args.fix:
                raise RoboDMError("audit fail needs --notes \"what's wrong + the rule\" and --fix \"<exact ./dm command>\".")
        if a == "resolve" and not args.how:
            raise RoboDMError("audit resolve needs --how \"what was done to fix it\".")
        status = {"pass": "passed", "fail": "failed", "resolve": "resolved"}[a]
        changed = audit.mark(c, ids, status, notes=args.how if a == "resolve" else (args.notes or ""),
                             fix=args.fix or "", self_check=args.self)
        if a == "fail":
            out(f"❌ {ids[0]} failed: {args.notes}\nFix: `{args.fix} --fix {ids[0]}`")
        else:
            out(f"{'✅' if a == 'pass' else '🔧'} {status.title()}: {', '.join(e['id'] for e in changed) or 'nothing'}")
        left = audit.open_events(c)
        out(f"Still open: {', '.join(e['id'] + (' (failed)' if e['status'] == 'failed' else '') for e in left)}"
            if left else "Post-audit complete — nothing open.")
    elif a == "turn":
        out(f"Turn {audit.bump_turn(c)}.")
    elif a == "status":
        out(audit.status_text(c))
        for e in audit.open_events(c):
            out(f"- {e['id']} [{e['status']}] {truncate(e['cmd'], 120)}")
    elif a == "log":
        for e in audit.events(c)[-(args.n or 20):]:
            out(f"- {e['id']} [{e['status']}] {truncate(e['cmd'], 110)}"
                + (f" — {truncate(e['verdict_notes'], 80)}" if e.get("verdict_notes") else ""))
    elif a == "cleared":
        items = audit.cleared(c)
        if not items:
            out("Nothing cleared yet.")
        for e in sorted(items.values(), key=lambda x: (x["kind"], x["name"])):
            out(f"- {'✅' if e['verdict'] == 'CORRECT' else '🚫'} {e['kind']} {e['name']} — {truncate(e.get('notes', ''), 120)}"
                + (f" ({e['cite']})" if e.get("cite") else "") + f" [{e.get('by', '?')}]")
    elif a == "reset-cleared":
        if rest:
            kind, name = kind_name()
            out(f"Removed {audit.reset_cleared(c, kind, name)} clearance(s).")
        elif not args.yes:
            raise RoboDMError("This forgets every rules-lawyer clearance. Re-run with --yes, or name one: "
                              "audit reset-cleared spell \"Hold Person\".")
        else:
            out(f"Removed {audit.reset_cleared(c)} clearance(s).")


# =============================================================================== misc

# =============================================================================== the living world

def cmd_time(args) -> None:
    c = get_camp(args)
    a = args.action or "show"
    if a == "show":
        w = world.load(c)
        if not world.tracking(w):
            out("In-game time isn't tracked yet. Start the clock: `./dm time set <day> <HH:MM>` "
                "(e.g. `./dm time set 1 08:00`; `--calendar harptos --date \"Tarsakh 3\" --year 1492` for Realms dates).")
            return
        out(f"🕰 {world.fmt_time(w)}")
        for line in world.brief_lines(c)[1]:
            out(line)
    elif a == "set":
        if not args.args:
            raise RoboDMError("Usage: time set <day> [HH:MM|dawn|noon…] [--calendar harptos --date \"Tarsakh 3\" --year 1492]")
        try:
            day = int(args.args[0])
        except ValueError:
            raise RoboDMError("time set: the first value is the day number (Day 1 = the campaign's first day).")
        at = " ".join(args.args[1:]) or "08:00"
        out(f"🕰 {world.set_time(c, day, at, args.calendar, args.date, args.year)}")
    elif a == "advance":
        if not args.args:
            raise RoboDMError("Usage: time advance <3h | 1d 6h | until dawn | until day 12 06:00> [--reason \"…\"]")
        enc = combat.Encounter(c)
        problems = []
        if enc.active:
            problems.append(f"combat is running ({enc.state.get('name') or 'unnamed'}, round {enc.state.get('round', 0)}): "
                            "combat time passes in 6-second rounds (`./dm combat next`); `./dm combat end` adds them to the clock")
        audit.gate(c, problems, args)
        w = world.load(c)
        for line in world.advance(c, world.parse_duration(" ".join(args.args), world.now(w)), args.reason or ""):
            out(line)


def cmd_clock(args) -> None:
    c = get_camp(args)
    a = args.action or "list"
    w = world.load(c)
    if a == "add":
        if not args.name:
            raise RoboDMError("Usage: clock add \"what's coming\" [--segments 6] [--per 1d] [--deadline \"day 12 noon\"] "
                              "[--public] [--faction …] [--on-fill \"what happens\"]")
        rec = world.clock_add(c, args.name, args.segments, args.per, args.deadline, args.public, args.faction,
                              args.on_fill or "")
        out(f"Clock {world.fmt_clock(world.load(c), rec)}")
    elif a == "tick":
        if not args.name:
            raise RoboDMError("Usage: clock tick <id|name> [N] [--why \"…\"]")
        try:
            n = int(args.n) if args.n else 1
        except ValueError:
            raise RoboDMError("clock tick: N must be a number (negative to unwind).")
        for line in world.clock_tick(c, args.name, n, args.why or ""):
            out(line)
    elif a in ("done", "stop"):
        if not args.name:
            raise RoboDMError(f"Usage: clock {a} <id|name>")
        rec = world.clock_set_status(c, args.name, "done" if a == "done" else "stopped")
        out(f"{rec['name']}: {rec['status']}.")
    else:
        shown = [x for x in w["clocks"] if args.all or x["status"] in ("running", "filled")]
        for x in shown:
            out(f"- {world.fmt_clock(w, x)}")
        if not shown:
            out("No clocks. Every deadline, threat or rival plan deserves one: `./dm clock add \"…\" --segments 6`.")


def cmd_faction(args) -> None:
    c = get_camp(args)
    a = args.action or "list"
    rest = list(args.args or [])
    if a == "list":
        fs = world.factions(c)
        if not fs:
            out("No factions in the lore yet (`./dm lore add \"Name\" --type faction …` or `faction rep \"Name\" 0 --new`).")
        for f in fs:
            ag = [i for i in f["agenda"] if i["status"] == "open"]
            out(f"- **{f['name']}** — {f['word']} ({f['rep']:+d}){' · pace ' + f['pace'] if f['pace'] != 'normal' else ''}"
                + (f" · agenda: {'; '.join(i['id'] + ' ' + i['text'] for i in ag)}" if ag else ""))
        return
    if not rest:
        raise RoboDMError(f"Usage: faction {a} <faction> …")
    name = rest.pop(0)
    if a == "show":
        f = next((x for x in world.factions(c) if x["slug"] == world.ensure_faction(c, name)), None)
        out(f"**{f['name']}** — {f['word']} ({f['rep']:+d}){': ' + f['why'] if f['why'] else ''} · pace {f['pace']}")
        if f["summary"]:
            out(f"_{f['summary']}_")
        for i in f["agenda"]:
            out(f"- [{i['id']}] {i['text']} ({i['status']}){' · clock ' + i['clock'] if i.get('clock') else ''}")
    elif a == "rep":
        if not rest:
            raise RoboDMError("Usage: faction rep <faction> <+1|-2|3> [--why \"…\"] [--new]")
        slug, st = world.faction_rep(c, name, rest[0], args.why or "", args.new)
        out(f"{slug}: {world.REP_WORDS[st['rep']]} ({st['rep']:+d})" + (f" — {st['why']}" if st.get("why") else ""))
    elif a == "agenda":
        verb = rest.pop(0) if rest else "list"
        slug, items = world.faction_agenda(c, name, verb, " ".join(rest), args.clock)
        for i in items:
            if i["status"] == "open" or verb == "list":
                out(f"- [{i['id']}] {i['text']} ({i['status']}){' · clock ' + i['clock'] if i.get('clock') else ''}")
    elif a == "pace":
        if not rest:
            raise RoboDMError("Usage: faction pace <faction> fast|normal|slow")
        out(f"{world.faction_pace(c, name, rest[0])}: pace {rest[0]}")


def cmd_scene(args) -> None:
    c = get_camp(args)
    a = args.action or "show"
    if a == "set":
        notes = world.scene_set(c, place=args.text, present=parse_csv_list(args.present) if args.present is not None else None)
    elif a in ("add", "leave"):
        names = parse_csv_list(args.text) + parse_csv_list(args.present)
        if not names:
            raise RoboDMError(f"Usage: scene {a} name1,name2")
        notes = world.scene_set(c, add=names if a == "add" else None, leave=names if a == "leave" else None)
    elif a == "clear":
        notes = world.scene_set(c, place="", present=[])
        w = world.load(c)
        w["scene"] = {}
        world.save(c, w)
    else:
        notes = []
    scene, _ = world.brief_lines(c)
    for line in scene or ["No scene set: `./dm scene set \"Place\" --present npc1,npc2`."]:
        out(line)
    for n in notes:
        out(f"Note: {n}")


def cmd_world(args) -> None:
    c = get_camp(args)
    a = args.action or "show"
    if a == "show":
        w = world.load(c)
        beats = world.open_beats(w)
        if world.tracking(w):
            out(f"🕰 {world.fmt_time(w)}")
        for b in beats:
            out(f"- [{b['id']}] ({b['tier']}) {b['text']}")
        if beats:
            out("Resolve each: `./dm world resolve <id> \"what happened\"` [--known-by who] · or `./dm world drop <id>`.")
        else:
            out("No off-screen beats waiting.")
        for line in world.brief_lines(c)[1]:
            if "off-screen beat" not in line:
                out(line)
    elif a in ("resolve", "drop"):
        if not args.args:
            raise RoboDMError(f"Usage: world {a} <id>" + (" \"what happened\"" if a == "resolve" else ""))
        b = world.resolve(c, args.args[0], " ".join(args.args[1:]), parse_csv_list(args.known_by), drop=(a == "drop"))
        out(f"{b['id']}: {b['status']}" + (f" → memory {b['memory']}" if b.get("memory") else ""))
    elif a == "gossip":
        lines = world.gossip(c, args.session)
        for line in lines or ["Nothing this session that others would plausibly hear about (or no witnesses recorded)."]:
            out(line)


def _agent_text(cfg: dict, camp, engine: str, role: str) -> str:
    from robodm import costs, engines

    if engine == "claude":
        model, effort, chosen = costs.agent_choice(camp, role)
        return costs.describe(model, effort, chosen)
    a = engines.agent(cfg, role, engine)
    return (a["model"] or "the DM's model") + f" · effort {a['effort'] or 'the DM’s'}"


def cmd_engine(args) -> None:
    """Who plays the DM behind the web client (Claude Code, Ollama, KoboldCpp, an OpenAI-compatible server)."""
    from robodm import engines

    a = args.action or "show"
    if a == "set":
        if not args.name or args.name not in engines.ENGINES:
            raise RoboDMError(f"Usage: engine set <{'|'.join(engines.ENGINES)}> [--model …] [--effort …] [--url …] [--ctx N] [--tools native|text]")
        sub = {k: v for k, v in (("model", args.model), ("url", args.url), ("num_ctx", args.ctx), ("tool_mode", args.tools),
                                 ("api_key", args.api_key)) if v is not None}
        patch = {"engine": args.name, args.name: sub} if sub else {"engine": args.name}
        if args.effort is not None:
            patch["effort"] = "" if args.effort == "default" else args.effort
        engines.update(patch)
    camp = maybe_camp(args)
    if a == "agent":
        if args.name not in engines.AGENT_ROLES:
            raise RoboDMError(f"Usage: engine agent <{'|'.join(engines.AGENT_ROLES)}> [--model … | default] "
                              "[--effort low…max | default] [--for claude|ollama|kobold|openai]")
        eng = args.for_engine or engines.load()["engine"]
        choice = {k: v for k, v in (("model", args.model), ("effort", args.effort)) if v is not None}
        if choice:
            engines.update({"agents": {eng: {args.name: choice}}})
        if eng == "claude":
            from robodm import costs

            changed = costs.apply_speed(camp)
            if changed:
                out("Agent files: " + "; ".join(changed) + ". (Claude Code loads changed agent files when it starts; "
                    "the web client's DM starts fresh every turn.)")
    cfg = engines.load()
    e = cfg["engine"]
    sub = cfg[e]
    ok, detail = engines.reachable(cfg, fresh=True)
    out(f"🧠 DM engine: {engines.ENGINES[e]} · model {sub.get('model') or 'default'} · effort {cfg.get('effort') or ('medium' if e != 'claude' else 'default')}"
        + (f" · {sub['url']} · context {sub.get('num_ctx')} · tools {sub.get('tool_mode')}" if e != "claude" else ""))
    out("🤖 Subagents: " + " · ".join(f"{label.lower()} {_agent_text(cfg, camp, e, role)}"
                                      for role, label in engines.AGENT_ROLES.items()
                                      if e == "claude" or role in engines.LOCAL_ROLES))
    if e != "claude":
        out(("✔ reachable: " + detail) if ok else ("✘ " + engines.help_text(cfg) + f" ({detail})"))
    if a == "models" or (a == "set" and e != "claude" and ok):
        try:
            out("Models: " + (", ".join(engines.list_models(cfg)) or "none"))
        except Exception as ex:
            out(f"Models: couldn't list them ({ex})")


def cmd_playtest(args) -> None:
    """Scripted turns through the real DM on a scratch copy, scored (robodm/playtest.py). Spends real usage."""
    from robodm import playtest

    script = json.loads(Path(args.script).read_text(encoding="utf-8")) if args.script else None
    n = len((script or playtest.DEFAULT_SCRIPT)[: args.turns or None])
    if not args.yes:
        out(f"🧪 The playtest runs {n} real DM turn(s) through Claude Code (your usage; typically a few minutes and "
            "about as much as playing those turns yourself) on a scratch copy of "
            + (f"`{args.campaign}`" if args.campaign else "a built-in scenario")
            + ". Your campaigns aren't touched. Re-run with --yes to start.")
        return
    res = playtest.run(args.campaign, script, args.turns, args.out, args.keep, args.timeout or 600, log=out)
    if res.get("scratch"):
        out(f"Scratch copy kept at {res['scratch']}")


def cmd_whisper(args) -> None:
    """A private message to one player (their seat in the web client; 🔒 in the transcript)."""
    from robodm import whispers

    c = get_camp(args)
    if not args.who or args.who == "list":
        rows = whispers.all_whispers(c)
        if args.text:
            pc = c.get_pc(args.text)
            rows = [w for w in rows if w["pc"] == pc["slug"]]
        for w in rows[-30:]:
            out(f"- [{w['ts'][5:16].replace('T', ' ')}] {'DM → ' + w['name'] if w['from'] == 'dm' else w['name'] + ' → DM'}: {w['text']}")
        if not rows:
            out("No whispers yet.")
        return
    rec = whispers.add(c, args.who, read_text(args))
    out(f"🔒 Whispered to {rec['name']} (only their seat sees it).")


def cmd_undo(args) -> None:
    c = get_camp(args)
    if args.list:
        snaps = snapshots.listing(c)
        nxt = snapshots.peek(c)
        for s in snaps:
            mark = "  ← `./dm undo` goes here" if nxt and s["file"] == nxt["file"] else ""
            out(f"- turn {s['turn']} · {s['ts'][5:16].replace('T', ' ')}" + (f" · “{s['label']}”" if s.get("label") else "") + mark)
        if not snaps:
            out("No snapshots yet: one is taken before the first change of each turn.")
        return
    for line in snapshots.undo(c, steps=args.steps or 1, to=args.to, redo=args.redo):
        out(line)


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


def cmd_npc(args) -> None:
    """Named NPCs: records (race, alignment, backstory, relationships, stat block or sheet) and their minds."""
    from robodm import npcs

    a = args.action
    rest = list(args.args or [])
    if a == "agents":
        for p in npcs.write_agents():
            out(f"Wrote .claude/agents/{p.name} ({npcs.NPC_MODEL}, effort {npcs.AGENTS[p.stem]}).")
        return
    if a == "template":
        out("```json\n" + json.dumps(npcs.template(args.major), indent=2, ensure_ascii=False) + "\n```")
        return
    c = get_camp(args)
    if a == "list":
        rows = npcs.listing(c, args.tier)
        out("\n".join(rows) if rows else "No NPC records yet. `./dm npc check` lists who needs one.")
    elif a == "check":
        res = npcs.check(c)
        out(f"🧑 {res['records']} NPC record(s).")
        if res["missing"]:
            out(f"**Missing a record ({len(res['missing'])}):** " + "; ".join(res["missing"].values()))
        if res["invalid"]:
            out(f"**Records with problems ({len(res['invalid'])}):**")
            for slug, problems in res["invalid"].items():
                out(f"- {slug}: {'; '.join(problems)}")
        if not res["missing"] and not res["invalid"]:
            out("✔ Everyone named has a complete record.")
    elif a == "show":
        if not rest:
            raise RoboDMError("Usage: npc show <name> [--players]")
        out(npcs.show(c, npcs.find(c, " ".join(rest)), dm=not args.players))
    elif a == "statblock":
        if not rest:
            raise RoboDMError("Usage: npc statblock <name>")
        out(npcs.statblock_text(c, npcs.find(c, " ".join(rest))))
    elif a == "add":
        if not rest:
            raise RoboDMError('Usage: npc add "Name" --race … --alignment … --backstory "…" --statblock commoner|MM '
                              '[--role …] [--rel who:how] (significant NPCs: the npc-creator agent, with a sheet)')
        path, problems, warnings = npcs.add_quick(c, " ".join(rest), race=args.race, alignment=args.alignment,
                                                  role=args.role, backstory=args.backstory, statblock=args.statblock,
                                                  tier=args.tier, status=args.status, voice=args.voice,
                                                  appearance=args.appearance, lore=args.lore, rel=args.rel,
                                                  force=args.force)
        if path is None:
            raise RoboDMError("Not saved: " + "; ".join(problems))
        rec = npcs.find(c, " ".join(rest))
        out(f"✔ {rec['name']} ({rec.get('tier')}, {npcs.stats_line(c, rec)}) · mind: `{npcs.mind_agent(c, rec)}`")
        for w in warnings:
            out(f"   ⚠ {w}")
    elif a == "save":
        text = read_text(args, "file") if args.file else (sys.stdin.read() if (rest and rest[0] == "-") or not rest else
                                                          " ".join(rest))
        if not text.strip():
            raise RoboDMError("Usage: ./dm npc save - <<'EOF'  {record JSON, or a list}  EOF   (`./dm npc template`)")
        for line in npcs.save_text(c, text, force=args.force):
            out(line)
    elif a in ("brief", "recall", "remember", "attitude"):
        if not rest:
            raise RoboDMError(f"Usage: npc {a} <npc> …")
        slug = rest.pop(0)
        if a == "brief":
            out(npcs.brief(c, slug, situation=args.situation or " ".join(rest), combat=args.combat))
        elif a == "recall":
            hits = npcs.recall(c, slug, " ".join(rest), limit=args.limit or 8)
            out("\n".join(f"- {h}" for h in hits) if hits else "Nothing you know matches that.")
        elif a == "remember":
            if not rest:
                raise RoboDMError('Usage: npc remember <npc> "what you\'ll carry from this, first person"')
            npcs.remember(c, slug, " ".join(rest), args.about or "")
            out("Remembered.")
        else:
            if len(rest) < 2:
                raise RoboDMError('Usage: npc attitude <npc> <who> +1|-1|N [--why "…"]')
            res = npcs.attitude(c, slug, rest[0], rest[1], args.why or "")
            out(f"Attitude toward {rest[0]}: {res['score']:+d}")


def cmd_companion(args) -> None:
    from robodm import companions as co

    c = get_camp(args)
    a, rest = args.action, list(args.args)

    def who() -> str:
        if not rest:
            raise RoboDMError(f"Usage: companion {a} <npc> …")
        return co.find(c, rest.pop(0))

    if a == "new":
        if not rest:
            raise RoboDMError("Usage: companion new \"Name\" [--from-lore <entry>] [--alignment \"lawful neutral\"] "
                              "[--status party|present|away]")
        slug = co.create(c, " ".join(rest), from_lore=args.from_lore, alignment=args.alignment or "",
                         status=args.status or "away", model=args.model or "sonnet")
        out(f"Created companion **{' '.join(rest)}** at campaigns/{c.slug}/companions/{slug}/persona.md (draft). Fill in "
            "the sections (drives, values, methods, fears, relationships, secrets, combat style, voice), then "
            f"`./dm companion agent {slug}` for its own subagent and `./dm companion join {slug}` when it travels with the party.")
    elif a == "list":
        slugs = co.all_slugs(c)
        if not slugs:
            out("No companions yet: `./dm companion new \"Name\" --from-lore <entry>`.")
        for slug in slugs:
            meta, _ = co.load_persona(c, slug)
            st = co.state(c, slug)
            out(f"- {meta.get('name', slug)} [{st['status']}]{' (draft)' if meta.get('draft') else ''} — agent "
                f"`{co.agent_name(c, slug)}` · {len(co.memories(c, slug))} memories · consulted {st.get('consults', 0)}×")
    elif a == "show":
        slug = who()
        out((co._dir(c, slug) / "persona.md").read_text(encoding="utf-8"))
        out(json.dumps(co.state(c, slug), indent=1, ensure_ascii=False))
    elif a == "brief":
        out(co.brief(c, who(), situation=args.situation or "", combat=args.combat))
    elif a in ("join", "leave", "here", "away", "dead"):
        slug = who()
        status = {"join": "party", "leave": "away", "here": "present", "away": "away", "dead": "dead"}[a]
        co.set_status(c, slug, status)
        meta, _ = co.load_persona(c, slug)
        out(f"{meta.get('name', slug)} is now **{status}**." + (
            f" Consult `{co.agent_name(c, slug)}` at decision points." if status in co.ACTIVE else ""))
        if c.session.get("active"):
            c.log(f"{meta.get('name', slug)} {'joins the party' if status == 'party' else 'is ' + status}.")
    elif a == "remember":
        slug = who()
        rec = co.remember(c, slug, " ".join(rest), about=args.about or "")
        out(f"🧠 {slug} remembers: {rec['text']}")
    elif a == "recall":
        slug = who()
        hits = co.recall(c, slug, " ".join(rest), limit=args.limit or 8)
        out("\n".join(f"- {h}" for h in hits) or "Nothing you know matches that.")
    elif a == "agenda":
        slug = who()
        verb = rest.pop(0) if rest else "list"
        items = co.agenda(c, slug, verb, " ".join(rest))
        out("Agenda: " + ("; ".join(f"[{i['id']}] {i['text']}" for i in items if i["status"] == "open") or "nothing open"))
    elif a == "attitude":
        slug = who()
        if len(rest) < 2:
            raise RoboDMError("Usage: companion attitude <npc> <who> +1|-1|<-3..3> [--why \"…\"]")
        res = co.attitude(c, slug, rest[0], rest[1], why=args.why or "")
        out(f"{slug} → {rest[0]}: {res['score']:+d}" + (f" ({res['why']})" if res.get("why") else ""))
    elif a == "interject":
        slug = who()
        co.interject(c, slug, None if args.clear else " ".join(rest))
        out(f"{slug} {'has nothing to raise' if args.clear else 'wants to speak up: ' + ' '.join(rest)}.")
    elif a == "note":
        slug = who()
        co.table_note(c, slug, " ".join(rest))
        out(f"Noted for {slug}: shown in every brief from now on.")
    elif a == "agent":
        if args.generic:
            p = co.agents_dir() / "companion.md"
            from robodm.util import write_text

            from robodm import costs

            model, effort, _ = costs.agent_choice(c, "companion")
            write_text(p, co.generic_agent_text(model, effort))
            out(f"Wrote {p.name}.")
            return
        slugs = co.all_slugs(c) if (rest and rest[0] == "all") else [who()]
        for slug in slugs:
            p = co.write_agent(c, slug, model=args.model)
            out(f"Wrote .claude/agents/{p.name} (subagent `{p.stem}`). New agent files load when Claude Code starts; "
                "until then the generic `companion` agent plays this character.")
    elif a == "council":
        question = " ".join(rest)
        act = co.active(c)
        if not act:
            out("No companions are in the party or the scene.")
            return
        out(f"Party council: launch these subagents **in parallel** (one message, one Agent call each), then weigh their "
            f"answers and present the disagreements in the fiction:")
        for slug in act:
            meta, _ = co.load_persona(c, slug)
            out(f"- `{co.agent_name(c, slug)}`: \"You are {meta.get('name', slug)} (slug {slug}, campaign {c.slug}). The party "
                f"is deciding: {question}. Run your brief with --situation describing this, then answer.\"")


def cmd_web(args) -> None:
    from robodm.web import server

    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    server.serve(host=host, port=args.port, open_browser=args.open, renew=args.new_token, log=out)


def cmd_sync(args) -> None:
    from robodm import sync

    state = sync.check(fetch=not args.no_fetch)
    if state.get("error") == "not a git checkout":
        raise RoboDMError("Sync needs a git checkout of the RoboDM repo.")
    if args.check:
        if state.get("error"):
            out(f"⚠️ Couldn't fetch origin ({state['error']}).")
        for item in state["newer"]:
            out(f"⤵ Newer: {sync.describe(item)}")
        if not state["newer"]:
            out("✅ Campaigns are up to date with every branch on origin.")
        return
    for line in sync.apply(state, force=args.force):
        out(line)


def cmd_hook(args) -> None:
    text = hooks.run(args.mode, sys.stdin.read())
    if text:
        out(text)


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
        from robodm import costs

        u = costs.usage_summary(c)
        if u["turns"]:
            out(f"Web DM this session: {u['turns']} turn(s), ≈${u['cost']:.2f} API-equivalent · turn speed {u['speed']}")
        w = world.load(c)
        if world.tracking(w):
            out(f"Time: {world.fmt_time(w)}" + (f" · {len(world.open_beats(w))} off-screen beat(s) open" if world.open_beats(w) else ""))
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
    pc.add_argument("action", choices=["add", "build", "list", "show", "json", "set", "hp", "temp", "check", "save",
                                       "hitdie", "rest", "slot", "resource", "attack", "item", "gold", "xp", "levelup",
                                       "condition", "cast", "delete", "equip", "unequip", "attune", "unattune", "load"])
    pc.add_argument("name", nargs="?")
    pc.add_argument("value", nargs="?")
    pc.add_argument("extra", nargs="?")
    pc.add_argument("--player")
    pc.add_argument("--race")
    pc.add_argument("--class", dest="cls")
    pc.add_argument("--subclass")
    pc.add_argument("--level", type=int, default=None)
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
    pc.add_argument("--slot", type=int, help="spell slot level for `pc cast` (default: the spell's level)")
    pc.add_argument("--pact", action="store_true", help="cast with a Pact Magic slot (multiclass warlocks)")
    pc.add_argument("--reach", type=int, help="pc attack add: melee reach in feet (default 5)")
    pc.add_argument("--range", help="pc attack add: ranged weapon range, e.g. 80/320 (thrown weapons: with --reach)")
    pc.add_argument("--ritual", action="store_true", help="cast as a ritual (no slot, +10 minutes)")
    pc.add_argument("--no-time", action="store_true", help="rest: don't move the in-game clock (you already advanced it)")
    pc.add_argument("--result", type=int, help="check/save: the total the player rolled (players dice mode)")
    pc.add_argument("--vs", help="check: the opponent's total, for a contest")
    pc.add_argument("--open", action="store_true", help="check: no fixed DC (graded results; flagged to the audit)")
    pc.add_argument("--scores", choices=list(builder.SCORE_METHODS), help="build: standard (array), pointbuy, roll, manual")
    pc.add_argument("--assign", help="build: the six scores before racial bonuses, e.g. str=15,dex=14,con=13,int=12,wis=10,cha=8")
    pc.add_argument("--asi", help="build: racial choices (half-elf: dex=1,wis=1) · levelup: str=2 / str=1,con=1 / later")
    pc.add_argument("--feat", help="build (variant human) / levelup (instead of an ASI): the feat")
    pc.add_argument("--gear", help="build: starting equipment picks, comma-separated (validated against the item data)")
    pc.add_argument("--gold", action="store_true", help="build: take the class's starting gold instead of gear")
    pc.add_argument("--preview", action="store_true", help="build / levelup: show the result without saving")
    pc.add_argument("--for", dest="for_", help="condition add: how long it lasts, e.g. 8h, 1d, until dawn")
    pc.add_argument("--until-rest", action="store_true", help="condition add: lasts until a long rest")
    _add_gate_args(pc)
    pc.set_defaults(func=cmd_pc)

    cb = sub.add_parser("combat", help="initiative, HP, damage, conditions, saves, attacks")
    cb.add_argument("action", choices=["start", "add", "party", "init", "next", "status", "damage", "dmg", "heal", "temp",
                                       "condition", "cond", "save", "attack", "attacks", "conc", "concentrate",
                                       "concentration", "deathsave", "stabilize", "legendary", "remove", "note", "log",
                                       "cast", "end", "map", "place", "move", "stand", "dash", "disengage", "area",
                                       "range", "vision", "setup"])
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
    cb.add_argument("--dark", action="store_true", help="map fog: the area is dark (vision limited to darkvision/light)")
    cb.add_argument("--monsters", help="setup: who the party fights, e.g. \"goblin x2, wolf\"")
    cb.add_argument("--map", help="setup: map size in squares (default 12x8)")
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
    cb.add_argument("--reaction", action="store_true", help="act on someone else's turn using the reaction")
    cb.add_argument("--legendary", action="store_true", help="a legendary action (spends --cost, default 1)")
    cb.add_argument("--spell", help="the spell this attack/save resolves (must be cleared)")
    cb.add_argument("--custom", action="store_true", help="a non-standard condition (spell-specific effect)")
    cb.add_argument("--slot", type=int, help="spell slot level for `combat cast` (default: the spell's level)")
    cb.add_argument("--ritual", action="store_true")
    cb.add_argument("--pact", action="store_true", help="cast with a Pact Magic slot (multiclass warlocks)")
    cb.add_argument("--target", help="cast: creatures the spell targets (range and a clear path are checked)")
    cb.add_argument("--reach", type=int, help="attack: melee reach in feet when the stat block doesn't say")
    cb.add_argument("--range", help="attack: normal/long range, e.g. 80/320")
    cb.add_argument("--text", help="map draw: the map rows (- for stdin)")
    cb.add_argument("--file", help="map draw: read the map rows from a file")
    cb.add_argument("--out", help="map render: where to write the SVG (or .html)")
    cb.add_argument("--html", action="store_true", help="map render: an HTML page instead of a bare SVG")
    cb.add_argument("--clear", action="store_true", help="map background: remove it")
    cb.add_argument("--path", help="move: the squares to move through, e.g. C4,D5,E5")
    cb.add_argument("--dash", action="store_true", help="move: this creature took the Dash action")
    cb.add_argument("--disengage", action="store_true", help="move: this creature took the Disengage action")
    cb.add_argument("--mode", choices=["walk", "fly", "swim", "climb", "burrow"], help="move: movement type")
    cb.add_argument("--at", help="area: the point of origin (a square)")
    cb.add_argument("--from", dest="from_", help="area: a cone/line/self-area starting at this creature")
    cb.add_argument("--toward", help="area: direction (a square or creature)")
    cb.add_argument("--width", type=int, help="area: line width in feet (default 5)")
    cb.add_argument("--corner", action="store_true", help="area: origin at the top-left corner of --at, not its centre")
    cb.add_argument("--through-corners", action="store_true",
                    help="area: the effect spreads around corners (e.g. Fireball); don't exclude total cover")
    cb.add_argument("--size", help="add: size of a custom creature (T S M L H G)")
    cb.add_argument("--speed", type=int, help="add: walking speed of a custom creature")
    _add_gate_args(cb)
    cb.set_defaults(func=cmd_combat)

    rl = sub.add_parser("ruling", help="a judgement call (grapple, improvised action, feature use): pre-checked by the rules-lawyer")
    rl.add_argument("text", nargs="?")
    rl.add_argument("--key", help="short reusable name for this ruling, e.g. grapple-with-shield")
    rl.add_argument("--items", help="rules items it relies on: \"feature:Divine Smite,spell:Shield\"")
    _add_gate_args(rl)
    rl.set_defaults(func=cmd_ruling)

    au = sub.add_parser("audit", help="mandatory rules checking: pre-check briefs, verdicts, the post-audit")
    au.add_argument("action", choices=["brief", "clear", "pending", "pass", "fail", "resolve", "turn", "status", "log",
                                       "cleared", "reset-cleared", "receipts", "autopass"])
    au.add_argument("args", nargs="*")
    au.add_argument("--verdict", type=str.upper, choices=["CORRECT", "INCORRECT"])
    au.add_argument("--notes")
    au.add_argument("--cite")
    au.add_argument("--fix", help="for `audit fail`: the exact ./dm command that corrects the mistake")
    au.add_argument("--how", help="for `audit resolve`: what was done")
    au.add_argument("--use", help="for `audit brief`: what the DM intends to do")
    au.add_argument("--self", action="store_true", help="claude.ai only: a self-check (no subagents there)")
    au.add_argument("--n", type=int)
    au.add_argument("--yes", action="store_true")
    au.add_argument("--reply", help="receipts: your reply text to check against the dice (- reads stdin)")
    au.add_argument("--file", help="receipts: read the reply from a file")
    au.set_defaults(func=cmd_audit)

    se = sub.add_parser("session", help="start / log / end a play session")
    se.add_argument("action", choices=["start", "log", "end", "show", "list", "transcript"])
    se.add_argument("text", nargs="?")
    se.add_argument("--n", type=int, help="transcript: how many recent entries (default 40)")
    se.add_argument("--summary")
    se.add_argument("--force", action="store_true", help="end: even with off-screen beats unresolved")
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
    me.add_argument("--scope", choices=["all", "memory", "journal", "transcript", "lore", "party", "quests", "companions"])
    me.add_argument("--or", dest="or_", action="append", help="search: another phrasing (repeatable)")
    me.add_argument("--known-by", help="add/edit: companions/NPCs who know this (shows in their briefs), e.g. okton,halda; "
                                       "events default to who's in the scene; `none` for private")
    me.add_argument("--full", action="store_true", help="brief: don't trim to the size budget")
    me.add_argument("--budget", type=int, help="brief: size budget in characters (default 9000)")
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
    lo.add_argument("action", choices=["add", "list", "show", "search", "update", "index", "source", "types", "mind"])
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
    lo.add_argument("--feeling", help="mind: how the NPC feels right now")
    lo.add_argument("--goal", help="mind: what they want")
    lo.add_argument("--opinion", help="mind: what they think of the party")
    lo.add_argument("--plan", help="mind: what they're doing about it (it advances off-screen)")
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
    ru.add_argument("--mirror", choices=["auto", "local", "github", "site"])
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

    tm = sub.add_parser("time", help="in-game time: show, set, advance (timers, clocks and the off-screen world follow)")
    tm.add_argument("action", nargs="?", choices=["show", "set", "advance"])
    tm.add_argument("args", nargs="*", help="set: <day> [HH:MM] · advance: 3h | 1d 6h | until dawn | until day 12 06:00")
    tm.add_argument("--calendar", choices=list(world.CALENDARS))
    tm.add_argument("--date", help="set (harptos): the date of that day, e.g. \"Tarsakh 3\"")
    tm.add_argument("--year", type=int, help="set (harptos): the year, e.g. 1492")
    tm.add_argument("--reason", help="advance: why time passes (travel, downtime…)")
    _add_gate_args(tm)
    tm.set_defaults(func=cmd_time)

    ck = sub.add_parser("clock", help="progress clocks and deadlines: add, tick, list, done, stop")
    ck.add_argument("action", nargs="?", choices=["add", "tick", "list", "done", "stop"])
    ck.add_argument("name", nargs="?", help="clock name, or its id (k1)")
    ck.add_argument("n", nargs="?", help="tick: how many segments (default 1; negative unwinds)")
    ck.add_argument("--segments", type=int, help="add: how many segments (default 6; none for a pure deadline)")
    ck.add_argument("--per", help="add: fills one segment every … of in-game time, e.g. 1d")
    ck.add_argument("--deadline", help="add: a moment, e.g. \"day 12 noon\", \"+3d\", \"tomorrow dawn\"")
    ck.add_argument("--public", action="store_true", help="add: the players know about it (shown in the player view)")
    ck.add_argument("--faction", help="add: the faction behind it")
    ck.add_argument("--on-fill", help="add: what happens when it fills")
    ck.add_argument("--why", help="tick: what caused it")
    ck.add_argument("--all", action="store_true", help="list: include done and stopped clocks")
    ck.set_defaults(func=cmd_clock)

    fa = sub.add_parser("faction", help="factions: standing with the party (-5…+5), agendas, pace")
    fa.add_argument("action", nargs="?", choices=["list", "show", "rep", "agenda", "pace"])
    fa.add_argument("args", nargs="*", help="<faction> then: rep +1 · agenda add \"…\" | done f1 · pace fast")
    fa.add_argument("--why")
    fa.add_argument("--clock", help="agenda add: the clock that measures it (k2)")
    fa.add_argument("--new", action="store_true", help="rep: create a faction lore entry if there isn't one")
    fa.set_defaults(func=cmd_faction)

    sc = sub.add_parser("scene", help="where the party is and who else is here (they witness what happens)")
    sc.add_argument("action", nargs="?", choices=["show", "set", "add", "leave", "clear"])
    sc.add_argument("text", nargs="?", help="set: the place · add/leave: names, comma-separated")
    sc.add_argument("--present", help="set: everyone else here, comma-separated (replaces the cast)")
    sc.set_defaults(func=cmd_scene)

    wo = sub.add_parser("world", help="the off-screen world: pending beats, resolve them, gossip")
    wo.add_argument("action", nargs="?", choices=["show", "resolve", "drop", "gossip"])
    wo.add_argument("args", nargs="*", help="resolve: <id> \"what happened\" · drop: <id>")
    wo.add_argument("--known-by", help="resolve: who knows what happened")
    wo.add_argument("--session", type=int, help="gossip: which session (default: the current one)")
    wo.set_defaults(func=cmd_world)

    sh = sub.add_parser("shop", help="prices from the item data; --buy / --sell move gold and inventory")
    sh.add_argument("item", nargs="*")
    sh.add_argument("--pc")
    sh.add_argument("--buy", action="store_true")
    sh.add_argument("--sell", action="store_true")
    sh.add_argument("--qty", type=int)
    sh.add_argument("--price", type=float, help="a negotiated price in gp (each)")
    sh.set_defaults(func=cmd_shop)

    en = sub.add_parser("engine", help="who plays the DM in the web client: claude, ollama, kobold, openai")
    en.add_argument("action", nargs="?", choices=["show", "set", "models", "agent"])
    en.add_argument("name", nargs="?", help="set: claude | ollama | kobold | openai · agent: rules-lawyer | companion | lore-keeper")
    en.add_argument("--model", help="the model (agent: `default` = turn speed for Claude, the DM's model locally)")
    en.add_argument("--for", dest="for_engine", choices=["claude", "ollama", "kobold", "openai"],
                    help="agent: which engine's subagent (default: the current engine)")
    en.add_argument("--effort", choices=["default", "low", "medium", "high", "xhigh", "max"])
    en.add_argument("--url", help="the local server's address")
    en.add_argument("--ctx", type=int, help="context window in tokens (local engines)")
    en.add_argument("--tools", choices=["native", "text"], help="tool calling: the server's API or the <dm> text protocol")
    en.add_argument("--api-key", help="openai engine: the server's API key")
    en.set_defaults(func=cmd_engine)

    pt = sub.add_parser("playtest", help="scripted turns through the real DM on a scratch copy, scored (spends usage)")
    pt.add_argument("--campaign", help="copy this campaign (default: a built-in scenario)")
    pt.add_argument("--turns", type=int, help="only the first N turns of the script")
    pt.add_argument("--script", help="a JSON list of turns: {text, as, private, ooc, reset, expect: [brief|roll|ooc|private]}")
    pt.add_argument("--out", help="where to write the Markdown scorecard (default: ./playtest-<date>.md)")
    pt.add_argument("--keep", action="store_true", help="keep the scratch copy to inspect")
    pt.add_argument("--timeout", type=float, help="seconds per turn (default 600)")
    pt.add_argument("--yes", action="store_true", help="really run it")
    pt.set_defaults(func=cmd_playtest)

    wh = sub.add_parser("whisper", help="a private message to one player: whisper <pc> \"…\" · whisper list [pc]")
    wh.add_argument("who", nargs="?")
    wh.add_argument("text", nargs="?")
    wh.add_argument("--file")
    wh.set_defaults(func=cmd_whisper)

    ud = sub.add_parser("undo", help="rewind the campaign to the start of a turn (snapshots taken automatically)")
    ud.add_argument("steps", nargs="?", type=int, help="how many turns back (default 1)")
    ud.add_argument("--to", type=int, help="the turn number to go back to (see --list)")
    ud.add_argument("--list", action="store_true", help="show the snapshots")
    ud.add_argument("--redo", action="store_true", help="bring back the state the last undo replaced")
    ud.set_defaults(func=cmd_undo)

    st = sub.add_parser("status", help="quick overview of data, campaign, session and combat")
    st.set_defaults(func=cmd_status)

    npc = sub.add_parser("npc", help="named NPCs: records (race, alignment, backstory, relationships, stat block or "
                                     "sheet) and their minds")
    npc.add_argument("action", choices=["list", "check", "show", "statblock", "template", "add", "save", "brief",
                                        "recall", "remember", "attitude", "agents"])
    npc.add_argument("args", nargs="*")
    npc.add_argument("--tier", choices=["major", "minor", "background"])
    npc.add_argument("--status", choices=["alive", "dead", "missing", "historical"])
    npc.add_argument("--race")
    npc.add_argument("--alignment")
    npc.add_argument("--role")
    npc.add_argument("--backstory")
    npc.add_argument("--voice")
    npc.add_argument("--appearance")
    npc.add_argument("--lore", help="add: the lore entry this person is (default: a lore entry with the same name)")
    npc.add_argument("--statblock", help='add: e.g. "commoner|MM", commoner_mm or a 5e.tools bestiary link')
    npc.add_argument("--rel", action="append", help='add: a relationship, "who:how" (repeatable)')
    npc.add_argument("--major", action="store_true", help="template: a major NPC (with a character sheet)")
    npc.add_argument("--players", action="store_true", help="show: what the players may see")
    npc.add_argument("--situation", help="brief: what's happening right now, from their point of view")
    npc.add_argument("--combat", action="store_true", help="brief: include the fight as they'd see it")
    npc.add_argument("--about", help="remember: who or what the memory is about")
    npc.add_argument("--why")
    npc.add_argument("--file", help="save: read the JSON from a file instead of stdin")
    npc.add_argument("--force", action="store_true", help="save/add: keep a record despite problems")
    npc.add_argument("--limit", type=int)
    npc.set_defaults(func=cmd_npc)

    co = sub.add_parser("companion", help="NPC companions with their own minds and subagents: new, join, brief, council…")
    co.add_argument("action", choices=["new", "list", "show", "brief", "join", "leave", "here", "away", "dead",
                                       "remember", "recall", "agenda", "attitude", "interject", "note", "agent", "council"])
    co.add_argument("args", nargs="*")
    co.add_argument("--from-lore", help="new: build the draft persona from this lore entry")
    co.add_argument("--alignment")
    co.add_argument("--status", choices=["party", "present", "away", "dead"])
    co.add_argument("--model", choices=["sonnet", "opus", "haiku", "inherit"], help="agent: the companion subagent's model")
    co.add_argument("--generic", action="store_true", help="agent: (re)write the generic companion agent")
    co.add_argument("--situation", help="brief: what's happening right now, from the companion's point of view")
    co.add_argument("--combat", action="store_true", help="brief: include the fight (status and map, as they'd see it)")
    co.add_argument("--about", help="remember: who or what the memory is about")
    co.add_argument("--why")
    co.add_argument("--clear", action="store_true", help="interject: nothing to raise any more")
    co.add_argument("--limit", type=int)
    co.set_defaults(func=cmd_companion)

    rc = sub.add_parser("recall", help="search everything the campaign knows, by meaning: give 2–4 phrasings")
    rc.add_argument("queries", nargs="*")
    rc.add_argument("--scope", choices=["all", "memory", "journal", "transcript", "lore", "party", "quests", "companions"])
    rc.add_argument("--limit", type=int)
    rc.add_argument("--stats", action="store_true", help="what's indexed")
    rc.set_defaults(func=cmd_recall)

    wb = sub.add_parser("web", help="the local web client: live map, party, companions, dice and a chat with the DM")
    wb.add_argument("--port", type=int, default=8642)
    wb.add_argument("--lan", action="store_true", help="also serve on your network (phones, tablets, other PCs)")
    wb.add_argument("--host", help="bind address (default 127.0.0.1, or 0.0.0.0 with --lan)")
    wb.add_argument("--open", action="store_true", help="open it in your browser")
    wb.add_argument("--new-token", action="store_true", help="replace the access token (old links stop working)")
    wb.set_defaults(func=cmd_web)

    sy = sub.add_parser("sync", help="pull campaigns from whichever git branch has the newest state")
    sy.add_argument("--check", action="store_true", help="only report, change nothing")
    sy.add_argument("--force", action="store_true", help="replace uncommitted local campaign changes")
    sy.add_argument("--no-fetch", action="store_true")
    sy.set_defaults(func=cmd_sync)

    hk = sub.add_parser("hook", help="Claude Code hooks (hook JSON on stdin): " + ", ".join(hooks.MODES))
    hk.add_argument("mode", choices=hooks.MODES)
    hk.set_defaults(func=cmd_hook)
    return p


def _add_gate_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ruling", help="override a rules-gate block: the reason (logged as a house ruling, flagged to the audit)")
    p.add_argument("--fix", help="this command applies the rules-lawyer's fix for a failed event, e.g. e0014")


def _campaign_dispatch(args) -> None:
    if args.action == "set" and args.name and args.key is not None and args.value is None:
        args.key, args.value = args.name, args.key  # `campaign set dice_mode players`
    elif args.action == "set" and args.name and args.key and args.value:
        args.campaign = args.campaign or args.name
    cmd_campaign(args)


def _rules_dispatch(args) -> None:
    cmd_rules(args)


# Commands (or command actions) that only read: no turn snapshot before them.
_READ_ONLY = {"status", "recall", "rules", "roll", "export", "web", "hook", "sync", "undo", "audit", "random", "import",
              "campaign", "playtest", "engine"}
_READ_ACTIONS = {"pc": {"list", "show", "json"}, "combat": {"status", "log", "attacks", "range"},
                 "session": {"show", "list", "transcript"}, "memory": {"search", "list", "brief"}, "quest": {"list"},
                 "lore": {"list", "show", "search", "types", "index", "source"}, "context": {"list", "show", "render"},
                 "companion": {"list", "show", "brief", "recall", "council"}, "time": {None, "show"},
                 "npc": {"list", "check", "show", "statblock", "template", "brief", "recall", "agents"},
                 "clock": {None, "list"}, "faction": {None, "list", "show"}, "scene": {None, "show"},
                 "world": {None, "show", "gossip"}}


def _mutates(args) -> bool:
    if args.command in _READ_ONLY:
        return False
    return getattr(args, "action", None) not in _READ_ACTIONS.get(args.command, set())


def _is_rules_command(args) -> bool:
    action = getattr(args, "action", None)
    return action in audit.RULES_COMMANDS.get(args.command, ()) or (args.command == "session" and action == "end")


def main(argv: list[str] | None = None) -> int:
    global _CAPTURE
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    gated = _is_rules_command(args)
    _CAPTURE = [] if gated else None
    if gated:
        receipts.start()
    try:
        if _mutates(args):
            try:
                snapshots.ensure(maybe_camp(args))
            except RoboDMError:
                pass
        camp = maybe_camp(args) if gated else None
        fix_ids = None
        if camp is not None:
            if getattr(args, "fix", None):
                fix_ids = audit.parse_ids(camp, [args.fix])
                not_failed = [e["id"] for e in audit.events(camp) if e["id"] in fix_ids and e["status"] != "failed"]
                if not_failed:
                    raise RoboDMError(f"--fix is for events the rules-lawyer failed; {', '.join(not_failed)} isn't one.")
            audit.backstop(camp, args.command, getattr(args, "action", None), fix_ids)
        args.func(args)
        if os.environ.get("ROBODM_AUTOSAVE") and args.command not in ("hook", "import"):
            bundle.autosave(maybe_camp(args), Path(os.environ["ROBODM_AUTOSAVE"]))
        if camp is not None and args.command != "session" and \
                (args.command, getattr(args, "action", None)) not in audit.NOT_RECORDED:
            audit.record_event(campaign.load(camp.slug), "./dm " + " ".join(shlex.quote(x) for x in argv),
                               _CAPTURE or [], overridden=getattr(args, "overridden", None), fix_for=fix_ids,
                               facts=receipts.stop())
    except RoboDMError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        _CAPTURE = None
        receipts.stop()
    return 0
