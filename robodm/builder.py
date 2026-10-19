"""Characters from the rules data: `pc build` (the Character Forge), data-driven `pc levelup`, and equipment
(`pc equip / unequip / attune / unattune`, AC from armor, attacks from weapons, `shop`).

Everything is checked against the local 5etools data and the campaign's `sources`: racial bonuses and choices,
background and class skills, the standard array / point buy / rolled scores, class features by level, subclass
and Ability Score Improvement levels, multiclass prerequisites, armor and weapon proficiency, attunement slots.
Problems go through the rules gate (`--ruling` to allow a house rule), and the result is a normal sheet.
Classes that aren't in the data (homebrew) still level up; they just don't get features added automatically.
"""

from __future__ import annotations

import copy
import re

from robodm import campaign as campaign_mod, dice
from robodm.campaign import Campaign
from robodm.util import ABILITIES, ABILITY_NAMES, SKILLS, RoboDMError, ability_mod, fmt_mod, norm_name, normalize_skill, slugify

STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]
POINT_BUY_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUY_BUDGET = 27
SCORE_METHODS = ("standard", "pointbuy", "roll", "manual")
DAMAGE_CODES = {"S": "slashing", "P": "piercing", "B": "bludgeoning", "N": "necrotic", "R": "radiant", "F": "fire",
                "C": "cold", "L": "lightning", "T": "thunder", "A": "acid", "O": "force", "I": "poison", "Y": "psychic"}
ARMOR_TYPES = {"LA": "light", "MA": "medium", "HA": "heavy", "S": "shield"}
MAX_ATTUNED = 3


# =============================================================================== data access

def _db():
    from robodm.rules import default_db

    db = default_db()
    db.require()
    return db


def _strip(text) -> str:
    from robodm.rules.render import strip_tags

    return " ".join(strip_tags(str(text)).split())


def _ref_name(ref: str) -> str:
    """'chain mail|phb' → 'Chain Mail'; 'Martial Archetype|Fighter||3' → 'Martial Archetype'."""
    return str(ref).split("|")[0].strip()


def _a(noun: str) -> str:
    return f"{'an' if noun[:1].lower() in 'aeiou' else 'a'} {noun}"


def _sources(camp: Campaign | None) -> set[str]:
    return {s.upper() for s in ((camp.settings.get("sources") if camp else None) or [])}


def _source_problem(camp: Campaign | None, what: str, name: str, src: str) -> list[str]:
    srcs = _sources(camp)
    if srcs and src and src.upper() not in srcs:
        return [f"{name} ({what}) is from {src}, which isn't in this campaign's sources ({', '.join(sorted(srcs))})"]
    return []


def find(db, name: str, t: str, camp: Campaign | None = None):
    """(record, entry) for a race/subrace/class/background/item/feat, preferring the campaign's sources."""
    srcs = _sources(camp)
    recs = [r for r in db._filtered(t) if norm_name(r["n"]) == norm_name(name) or
            any(norm_name(a) == norm_name(name) for a in r.get("al", ()))]
    if not recs:
        return None, None
    recs.sort(key=lambda r: (bool(srcs) and r["s"].upper() not in srcs, r["s"].upper() not in ("PHB", "XPHB"),
                             r["s"].upper() == "XPHB"))
    return recs[0], db.get(recs[0])


def race_data(db, name: str, camp: Campaign | None = None) -> dict:
    rec, sub = find(db, name, "subrace", camp)
    base = None
    if rec:
        brec, base = find(db, sub["raceName"], "race", camp)
        if base is None:
            raise RoboDMError(f"Base race {sub['raceName']} for {name} isn't in the data.")
        display = f"{sub['name']} {sub['raceName']}"
        over = sub.get("overwrite") or {}
        base = {k: v for k, v in base.items() if not over.get(k)}     # e.g. a variant replacing the base skills
        parts = [base, sub]
        src = sub.get("source") or base.get("source", "")
    else:
        rec, base = find(db, name, "race", camp)
        if base is None:
            raise RoboDMError(f"No race '{name}' in the rules data. Try `./dm rules list race`.")
        subs = [s for s in (db.load_file("races.json") or {}).get("subrace", [])
                if s.get("raceName") == base["name"] and s.get("raceSource") == base.get("source")]
        plain = next((s for s in subs if not s.get("name")), None)     # the race on its own (e.g. PHB Human's +1s)
        named = [s for s in subs if s.get("name")]
        if named and plain is None:
            names = ", ".join(sorted({f"{s['name']} {base['name']}" for s in named}))
            raise RoboDMError(f"{base['name']} has subraces; pick one: {names}")
        display, parts, src = base["name"], [base] + ([plain] if plain else []), base.get("source", "")
    fixed: dict[str, int] = {}
    choose: list[dict] = []
    skills_fixed, skills_choose, traits, langs = [], [], [], []
    for p in parts:
        ab = (p.get("ability") or [{}])[0]
        for k, v in ab.items():
            if k in ABILITIES:
                fixed[k] = fixed.get(k, 0) + int(v)
            elif k == "choose":
                choose.append({"from": list(v.get("from") or ABILITIES), "count": int(v.get("count", 1)),
                               "amount": int(v.get("amount", 1))})
        for sp in p.get("skillProficiencies") or []:
            for k, v in sp.items():
                if k == "any":
                    skills_choose.append({"from": None, "count": int(v)})
                elif k == "choose":
                    skills_choose.append({"from": list(v.get("from") or []), "count": int(v.get("count", 1))})
                elif v is True:
                    skills_fixed.append(k)
        for lp in p.get("languageProficiencies") or []:
            langs += [k.title() for k, v in lp.items() if v is True]
            if lp.get("anyStandard"):
                langs.append(f"+{lp['anyStandard']} of your choice")
        for e in p.get("entries") or []:
            if isinstance(e, dict) and e.get("name") and e["name"] not in ("Age", "Size", "Alignment", "Languages", "Speed"):
                traits.append(e["name"])
    speed = base.get("speed", 30)
    speed = speed.get("walk", 30) if isinstance(speed, dict) else speed
    for p in parts[1:]:
        if isinstance(p.get("speed"), (int, dict)):
            speed = p["speed"].get("walk", speed) if isinstance(p["speed"], dict) else p["speed"]
    return {"name": display, "source": src, "fixed": fixed, "choose": choose, "speed": int(speed),
            "size": (base.get("size") or ["M"])[0], "darkvision": max(p.get("darkvision", 0) or 0 for p in parts),
            "skills_fixed": skills_fixed, "skills_choose": skills_choose, "traits": traits, "languages": langs,
            "resist": sorted({r for p in parts for r in (p.get("resist") or []) if isinstance(r, str)}),
            "toughness": "Dwarven Toughness" in traits,
            "feats": sum(int(f.get("any", 0)) for p in parts for f in (p.get("feats") or []) if isinstance(f, dict)),
            "weapons": [_ref_name(k) for p in parts for wp in (p.get("weaponProficiencies") or []) for k, v in wp.items() if v is True],
            "armor": [k for p in parts for ap in (p.get("armorProficiencies") or []) for k, v in ap.items() if v is True]}


def background_data(db, name: str, camp: Campaign | None = None) -> dict:
    rec, bg = find(db, name, "background", camp)
    if bg is None:
        raise RoboDMError(f"No background '{name}' in the rules data. Try `./dm rules list background`.")
    fixed, choose, items, gold, feature = [], [], [], 0, ""
    for sp in bg.get("skillProficiencies") or []:
        for k, v in sp.items():
            if v is True:
                fixed.append(k)
            elif k == "choose":
                choose.append({"from": list(v.get("from") or []), "count": int(v.get("count", 1))})
            elif k == "any":
                choose.append({"from": None, "count": int(v)})
    for group in bg.get("startingEquipment") or []:
        opts = group.get("_") or group.get("a") or []          # "_" is fixed; for a/b choices take (a)
        for it in opts:
            if isinstance(it, str):
                items.append(_ref_name(it).title() if it.islower() else _ref_name(it))
            elif isinstance(it, dict):
                if it.get("special"):
                    items.append(_strip(it["special"]))
                elif it.get("item"):
                    items.append(it.get("displayName") or _ref_name(it["item"]).title())
                if it.get("containsValue"):
                    gold += it["containsValue"] / 100
                if it.get("value"):
                    gold += it["value"] / 100
    for e in bg.get("entries") or []:
        if isinstance(e, dict) and str(e.get("name", "")).lower().startswith("feature"):
            feature = e["name"].split(":", 1)[-1].strip()
    tools = [k for tp in bg.get("toolProficiencies") or [] for k, v in tp.items() if v is True]
    return {"name": bg["name"], "source": bg.get("source", ""), "skills_fixed": fixed, "skills_choose": choose,
            "items": items, "gold": gold, "feature": feature, "tools": tools}


def class_data(db, name: str, camp: Campaign | None = None) -> dict | None:
    """The class's mechanics, or None when it isn't in the data (homebrew)."""
    rec, cl = find(db, name, "class", camp)
    if cl is None:
        return None
    sp = cl.get("startingProficiencies") or {}
    skills = next((s["choose"] for s in sp.get("skills") or [] if isinstance(s, dict) and "choose" in s), None)
    any_skills = next((s.get("any") for s in sp.get("skills") or [] if isinstance(s, dict) and "any" in s), None)
    data = db.load_file(rec["f"]) or {}
    subs = [s for s in data.get("subclass", []) if s.get("className") == cl["name"]
            and s.get("classSource") == cl.get("source")]
    srcs = _sources(camp)
    subs = [s for s in subs if not srcs or s.get("source", "").upper() in srcs] or subs
    gain = [int(str(f["classFeature"]).split("|")[3]) for f in cl.get("classFeatures") or []
            if isinstance(f, dict) and f.get("gainSubclassFeature")]
    se = cl.get("startingEquipment") or {}
    return {"name": cl["name"], "source": cl.get("source", ""), "entry": cl, "file": rec["f"],
            "hd": (cl.get("hd") or {}).get("faces", 8),
            "saves": list(cl.get("proficiency") or []),
            "skill_count": int((skills or {}).get("count", any_skills or 0)),
            "skill_from": [s.lower() for s in (skills or {}).get("from", [])] or None,
            "armor": [_strip(a) for a in sp.get("armor") or []], "weapons": [_strip(w) for w in sp.get("weapons") or []],
            "tools": [_strip(t) for t in sp.get("tools") or []],
            "gear": [_strip(g) for g in se.get("default") or []], "gold_dice": _strip(se.get("goldAlternative") or ""),
            "spell_ability": cl.get("spellcastingAbility"), "caster": cl.get("casterProgression"),
            "cantrips": cl.get("cantripProgression"), "known": cl.get("spellsKnownProgression"),
            "prepared": cl.get("preparedSpells"),
            "multiclass": (cl.get("multiclassing") or {}).get("requirements"),
            "multiclass_profs": (cl.get("multiclassing") or {}).get("proficienciesGained") or {},
            "subclass_title": cl.get("subclassTitle") or "Subclass", "subclass_level": min(gain) if gain else None,
            "subclasses": [{"name": s["name"], "short": s.get("shortName", s["name"]), "source": s.get("source", ""),
                            "features": s.get("subclassFeatures") or []} for s in subs]}


def class_features(cd: dict, level: int, subclass: str = "", camp: Campaign | None = None) -> tuple[list[str], bool]:
    """(feature names gained at exactly this class level, whether a subclass feature is due)."""
    srcs = _sources(camp)
    names, sub_due = [], False
    for f in cd["entry"].get("classFeatures") or []:
        ref = f["classFeature"] if isinstance(f, dict) else f
        bits = str(ref).split("|")
        if len(bits) < 4 or int(bits[3] or 0) != level:
            continue
        src = bits[4] if len(bits) > 4 and bits[4] else cd["source"]
        if srcs and src.upper() not in srcs:
            continue
        optional = src.upper() != cd["source"].upper()
        names.append(bits[0] + (f" (optional, {src})" if optional else ""))
        if isinstance(f, dict) and f.get("gainSubclassFeature"):
            sub_due = True
    if sub_due and subclass:
        sc = find_subclass(cd, subclass)
        if sc:
            for ref in sc["features"]:
                bits = str(ref).split("|")
                if len(bits) >= 6 and int(bits[5] or 0) == level:
                    names += [f"{n} ({sc['name']})" for n in _subclass_feature_names(cd, bits, sc)]
    return names, sub_due


def _subclass_feature_names(cd: dict, bits: list[str], sc: dict) -> list[str]:
    """A subclass's first feature is often just its name introducing the real ones (Champion → Improved
    Critical): follow those references."""
    from robodm.rules import default_db

    data = default_db().load_file(cd["file"]) or {}
    entry = next((f for f in data.get("subclassFeature", []) if f.get("name") == bits[0]
                  and f.get("subclassShortName") == sc["short"] and str(f.get("level")) == bits[5]), None)
    refs = []
    for e in (entry or {}).get("entries", []):
        if isinstance(e, dict) and e.get("type") == "refSubclassFeature":
            refs.append(_ref_name(e.get("subclassFeature", "")))
    return refs or [bits[0]]


def find_subclass(cd: dict, name: str) -> dict | None:
    key = norm_name(name)
    return next((s for s in cd["subclasses"] if norm_name(s["name"]) == key or norm_name(s["short"]) == key), None)


def spell_counts(cd: dict | None, level: int, mod: int) -> dict:
    if not cd or not cd.get("caster") and not cd.get("cantrips"):
        return {}
    out = {}
    if cd.get("cantrips"):
        out["cantrips"] = cd["cantrips"][min(level, 20) - 1]
    if cd.get("known"):
        out["known"] = cd["known"][min(level, 20) - 1]
    elif cd.get("prepared"):
        f = cd["prepared"].replace("<$level$>", str(level))
        f = re.sub(r"<\$\w+_mod\$>", str(mod), f)
        f = f.replace("/ 2", "// 2")
        try:
            out["prepared"] = max(1, int(eval(f, {"__builtins__": {}}, {})))   # e.g. "<$level$> + <$int_mod$>"
        except Exception:
            pass
    return out


# =============================================================================== ability scores

def parse_assign(text: str | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in [p.strip() for p in (text or "").split(",") if p.strip()]:
        k, _, v = part.partition("=")
        ab = k.strip().lower()[:3]
        if ab not in ABILITIES or not v.strip().lstrip("+-").isdigit():
            raise RoboDMError(f"Can't read '{part}': use e.g. str=15,dex=14,con=13,int=12,wis=10,cha=8")
        out[ab] = int(v)
    return out


def score_problems(method: str, scores: dict[str, int], rolled: list[int] | None) -> list[str]:
    if set(scores) != set(ABILITIES):
        missing = [a.upper() for a in ABILITIES if a not in scores]
        return [f"assign all six scores (missing: {', '.join(missing)}), e.g. --assign str=15,dex=14,con=13,int=12,wis=10,cha=8"]
    vals = sorted(scores.values(), reverse=True)
    if method == "standard" and vals != STANDARD_ARRAY:
        return [f"the standard array is 15, 14, 13, 12, 10, 8 (each once); got {', '.join(map(str, vals))}"]
    if method == "pointbuy":
        bad = [f"{k.upper()} {v}" for k, v in scores.items() if v not in POINT_BUY_COST]
        if bad:
            return [f"point buy scores run 8–15 before racial bonuses ({', '.join(bad)})"]
        cost = sum(POINT_BUY_COST[v] for v in scores.values())
        if cost > POINT_BUY_BUDGET:
            return [f"point buy: those scores cost {cost} points; the budget is {POINT_BUY_BUDGET}"]
    if method == "roll":
        if not rolled:
            return ["roll the scores first: `./dm pc build \"Name\" --scores roll` (no --assign) rolls 4d6-drop-lowest "
                    "six times and keeps them for this character"]
        if vals != sorted(rolled, reverse=True):
            return [f"assign the scores that were rolled ({', '.join(map(str, sorted(rolled, reverse=True)))}); "
                    f"got {', '.join(map(str, vals))}"]
    if method == "manual":
        bad = [f"{k.upper()} {v}" for k, v in scores.items() if not 3 <= v <= 18]
        if bad:
            return [f"manual scores run 3–18 before racial bonuses ({', '.join(bad)})"]
    return []


def apply_racial(race: dict, scores: dict[str, int], asi: dict[str, int]) -> tuple[dict[str, int], list[str]]:
    out = dict(scores)
    problems = []
    for k, v in race["fixed"].items():
        out[k] += v
    want = sum(c["count"] for c in race["choose"])
    if want:
        allowed = set().union(*[set(c["from"]) for c in race["choose"]])
        amount = race["choose"][0]["amount"]
        picked = {k: v for k, v in asi.items()}
        if len(picked) != want or any(v != amount for v in picked.values()) or not set(picked) <= allowed:
            problems.append(f"{race['name']}: choose {want} different abilit{'y' if want == 1 else 'ies'} for +{amount} "
                            f"from {', '.join(a.upper() for a in sorted(allowed))} with --asi, e.g. --asi "
                            + ",".join(f"{a}={amount}" for a in sorted(allowed)[:want]))
        for k in set(picked) & set(race["fixed"]):
            problems.append(f"{race['name']}: the chosen +{amount} can't go to {k.upper()}, which already gets a racial bonus")
        for k, v in picked.items():
            out[k] += v
    elif asi:
        problems.append(f"{race['name']} has no ability choice to make (its bonuses are fixed); drop --asi")
    over = [f"{k.upper()} {v}" for k, v in out.items() if v > 20]
    if over:
        problems.append(f"no ability score can exceed 20 ({', '.join(over)})")
    return out, problems


# =============================================================================== weapons, armor, AC

def item_entry(db, name: str, camp: Campaign | None = None) -> tuple[dict | None, int]:
    """(5etools item, magic bonus) for an item name; "+1 Longsword" / "Chain Mail +1" resolve to the base item."""
    bonus = 0
    base = name.strip()
    m = re.match(r"^\+(\d)\s+(.+)$", base) or re.match(r"^(.+?)\s+\+(\d)$", base)
    if m:
        if base.startswith("+"):
            bonus, base = int(m.group(1)), m.group(2)
        else:
            base, bonus = m.group(1), int(m.group(2))
    rec, it = find(db, base, "item", camp)
    if it is None and bonus:
        rec, it = find(db, name, "item", camp)
        bonus = 0
    return it, bonus


def proficient_with(pc: dict, it: dict) -> bool:
    profs = pc.get("proficiencies") or {}
    if ARMOR_TYPES.get(it.get("type", "").split("|")[0]):
        kind = ARMOR_TYPES[it["type"].split("|")[0]]
        have = {a.lower() for a in profs.get("armor", [])}
        return kind in have or f"{kind} armor" in have or (kind == "shield" and "shields" in have)
    sing = (lambda w: w.lower().strip()[:-1] if w.lower().strip().endswith("s") else w.lower().strip())
    have = {sing(w) for w in profs.get("weapons", [])}          # the data says "shortswords", "simple weapons"
    cat = (it.get("weaponCategory") or "").lower()
    return bool(cat) and (cat in have or f"{cat} weapon" in have) or sing(it["name"]) in have


def weapon_attack(pc: dict, it: dict, bonus: int = 0, label: str | None = None) -> dict:
    props = {p.split("|")[0] for p in it.get("property") or []}
    ranged = it.get("type", "").split("|")[0] == "R"
    strm, dexm = ability_mod(pc["abilities"]["str"]), ability_mod(pc["abilities"]["dex"])
    mod = dexm if ranged else (max(strm, dexm) if "F" in props else strm)
    prof = campaign_mod.pc_prof(pc) if proficient_with(pc, it) else 0
    dtype = DAMAGE_CODES.get(it.get("dmgType", ""), "")
    total = mod + bonus
    rec = {"name": label or it["name"], "to_hit": mod + prof + bonus,
           "damage": [[f"{it.get('dmg1', '1')}{fmt_mod(total) if total else ''}", dtype]],
           "kind": "ranged" if ranged else ("both" if "T" in props else "melee"), "from_item": label or it["name"]}
    if "R" in props:
        rec["reach"] = 10
    if it.get("range"):
        rec["range"] = [int(x) for x in str(it["range"]).split("/")]
    if it.get("dmg2") and "V" in props:
        rec["versatile"] = f"{it['dmg2']}{fmt_mod(total) if total else ''}"
    if not prof:
        rec["note"] = "not proficient: no proficiency bonus"
    return rec


def compute_ac(pc: dict, db=None, camp: Campaign | None = None) -> tuple[int, str]:
    """AC from the equipped armor and shield, unarmored defense, and attuned items with an AC bonus."""
    db = db or _db()
    eq = pc.get("equipped") or {}
    dexm = ability_mod(pc["abilities"]["dex"])
    classes = [c["class"].lower() for c in campaign_mod.pc_classes(pc)]
    parts = []
    armor, abonus = item_entry(db, eq["armor"], camp) if eq.get("armor") else (None, 0)
    if armor:
        t = armor.get("type", "").split("|")[0]
        base = int(armor.get("ac", 10)) + abonus
        dex = dexm if t == "LA" else (min(dexm, 2) if t == "MA" else 0)
        ac = base + dex
        parts.append(f"{eq['armor']} {base}" + (f" + DEX {fmt_mod(dex)}" if t != "HA" else ""))
    elif "barbarian" in classes:
        ac = 10 + dexm + ability_mod(pc["abilities"]["con"])
        parts.append(f"unarmored defense 10 + DEX {fmt_mod(dexm)} + CON {fmt_mod(ability_mod(pc['abilities']['con']))}")
    elif "monk" in classes and not eq.get("shield"):
        ac = 10 + dexm + ability_mod(pc["abilities"]["wis"])
        parts.append(f"unarmored defense 10 + DEX {fmt_mod(dexm)} + WIS {fmt_mod(ability_mod(pc['abilities']['wis']))}")
    else:
        ac = 10 + dexm
        parts.append(f"10 + DEX {fmt_mod(dexm)}")
    if eq.get("shield"):
        sh, sbonus = item_entry(db, eq["shield"], camp)
        s = int((sh or {}).get("ac", 2)) + sbonus
        ac += s
        parts.append(f"shield +{s}")
    for name in pc.get("attuned") or []:
        it, _ = item_entry(db, name, camp)
        if it and it.get("bonusAc"):
            b = int(str(it["bonusAc"]).replace("+", ""))
            ac += b
            parts.append(f"{name} +{b}")
    if pc.get("ac_bonus"):
        ac += int(pc["ac_bonus"])
        parts.append(f"other +{pc['ac_bonus']}")
    return ac, " + ".join(parts)


# =============================================================================== the Character Forge

def build(camp: Campaign, name: str, race: str, cls: str, background: str, method: str, assign: dict[str, int],
          asi: dict[str, int] | None = None, skills: list[str] | None = None, gear: list[str] | None = None,
          gold: bool = False, level: int = 1, subclass: str = "", player: str = "",
          rolled: list[int] | None = None, feat: str = "") -> tuple[dict, list[str], list[str]]:
    """(the new sheet, report lines, problems). Nothing is saved here."""
    db = _db()
    problems: list[str] = []
    if method not in SCORE_METHODS:
        raise RoboDMError(f"--scores must be one of: {', '.join(SCORE_METHODS)}")
    rd = race_data(db, race, camp)
    bd = background_data(db, background, camp)
    cd = class_data(db, cls, camp)
    if cd is None:
        raise RoboDMError(f"No class '{cls}' in the rules data. For a homebrew class, use `./dm pc add` and set the "
                          "sheet by hand.")
    for what, d in (("race", rd), ("background", bd), ("class", cd)):
        problems += _source_problem(camp, what, d["name"], d["source"])
    problems += score_problems(method, assign, rolled)
    scores, more = apply_racial(rd, assign if not problems else {**{a: 10 for a in ABILITIES}, **assign}, asi or {})
    problems += more
    report = []
    if method == "manual":
        report.append("Scores set manually (flagged to the audit).")
    elif method == "pointbuy":
        spent = sum(POINT_BUY_COST.get(v, 0) for v in assign.values())
        if spent < POINT_BUY_BUDGET:
            report.append(f"Point buy: {spent}/{POINT_BUY_BUDGET} points spent ({POINT_BUY_BUDGET - spent} unspent).")

    # Skills: race and background give some; the class lets you choose; duplicates can be replaced with any skill.
    fixed = rd["skills_fixed"] + bd["skills_fixed"]
    dups = len(fixed) - len(set(fixed))
    free = sum(c["count"] for c in rd["skills_choose"] + bd["skills_choose"]) + dups
    chosen = []
    for s in skills or []:
        sk = normalize_skill(s)
        if not sk:
            problems.append(f"'{s}' isn't a skill")
        else:
            chosen.append(sk)
    in_class = [s for s in chosen if cd["skill_from"] is None or s in cd["skill_from"]]
    need = cd["skill_count"] + free
    already = [s for s in chosen if s in fixed]
    if already:
        problems.append(f"{', '.join(already)} already come from your race or background; choose others")
    if len(set(chosen)) != len(chosen):
        problems.append("a skill is listed twice")
    if len(chosen) != need or len(in_class) < cd["skill_count"]:
        frm = ", ".join(cd["skill_from"]) if cd["skill_from"] else "any skill"
        msg = f"choose {cd['skill_count']} {cd['name']} skill{'s' if cd['skill_count'] != 1 else ''} from: {frm}"
        if free:
            msg += f"; plus {free} more of any skill (race/background choices or duplicates)"
        problems.append(msg + f" — with --skills a,b{',c' if need > 2 else ''} (got {len(chosen)})")

    # Level, HP, features.
    level = max(1, min(20, int(level or 1)))
    con = ability_mod(scores["con"])
    hp = cd["hd"] + con + (level - 1) * (cd["hd"] // 2 + 1 + con) + (level if rd["toughness"] else 0)
    feats: list[str] = []
    for lv in range(1, level + 1):
        names, due = class_features(cd, lv, subclass, camp)
        feats += names
        if due and not subclass:
            opts = ", ".join(s["name"] for s in cd["subclasses"])
            problems.append(f"{cd['name']} {lv} gains {_a(cd['subclass_title'])}: add --subclass (options: {opts})")
    if subclass and not find_subclass(cd, subclass):
        problems.append(f"no {cd['name']} {cd['subclass_title']} '{subclass}' (options: "
                        + ", ".join(s["name"] for s in cd["subclasses"]) + ")")
    if subclass and cd["subclass_level"] and level < cd["subclass_level"]:
        problems.append(f"a {cd['name']} picks {_a(cd['subclass_title'])} at level {cd['subclass_level']}")
    asi_levels = [f for f in feats if f.startswith("Ability Score Improvement")]
    if asi_levels:
        report.append(f"{len(asi_levels)} Ability Score Improvement(s) to apply by hand (`./dm pc set {slugify(name)} str 16`) "
                      "or as feats.")

    sub = find_subclass(cd, subclass) if subclass else None
    pc = campaign_mod.new_pc(name, cls=cd["name"], level=level, subclass=sub["name"] if sub else "", race=rd["name"],
                             background=bd["name"], player=player, hp=hp, speed=rd["speed"], saves=cd["saves"],
                             skills=sorted(set(fixed + chosen)), **scores)
    pc["features"] = feats + [f"{t} (race)" for t in rd["traits"]] + ([f"{bd['feature']} (background)"] if bd["feature"] else [])
    if rd["feats"]:
        fe = find(db, feat, "feat", camp)[1] if feat else None
        if fe is None:
            problems.append(f"{rd['name']} gets a feat: add --feat \"Name\"" + (f" ('{feat}' isn't in the data)" if feat else ""))
        else:
            problems += _source_problem(camp, "feat", fe["name"], fe.get("source", ""))
            pc["features"].append(f"{fe['name']} (feat)")
    elif feat:
        problems.append(f"{rd['name']} doesn't get a feat at level 1; drop --feat")
    pc["proficiencies"] = {"armor": cd["armor"] + rd["armor"], "weapons": cd["weapons"] + rd["weapons"],
                           "tools": cd["tools"] + bd["tools"], "languages": rd["languages"]}
    pc["hit_dice"]["die"] = cd["hd"]
    pc["size"] = rd["size"]
    if rd["darkvision"]:
        pc["senses"] = [f"darkvision {rd['darkvision']} ft."]
    if rd["resist"]:
        pc["resist"] = rd["resist"]
    if cd["spell_ability"]:
        pc["spellcasting_ability"] = cd["spell_ability"]
    pc["build"] = {"method": method, "base_scores": assign, "asi": asi or {}, "sources": [rd["source"], bd["source"], cd["source"]]}
    pc["inventory"] = list(bd["items"])
    pc["gold"] = round(bd["gold"], 2)

    # Starting gear: explicit picks (validated against the item data), or the class's starting gold.
    if gold:
        if not cd["gold_dice"]:
            problems.append(f"{cd['name']} has no starting-gold alternative in the data")
        else:
            expr = cd["gold_dice"].replace("×", "*").replace(" ", "")
            m = re.match(r"^(\d+d\d+)\*?(\d+)?$", expr)
            if m:
                r = dice.roll(m.group(1), label="starting gold")
                g = r.total * int(m.group(2) or 1)
                pc["gold"] = round(pc["gold"] + g, 2)
                report.append(f"Starting gold: {r.text()} × {m.group(2) or 1} = {g} gp")
    elif gear:
        for g in gear:
            it, bonus = item_entry(db, g, camp)
            if it is None:
                problems.append(f"no item '{g}' in the data (`./dm rules search {g} --type item`)")
                continue
            pc["inventory"].append(g.strip() if bonus else it["name"])
        report.append("Gear chosen (flagged to the audit against the class's starting options).")
    else:
        report.append(f"Starting equipment: choose from the {cd['name']} options with --gear \"…, …\" or take --gold "
                      f"({cd['gold_dice'] or 'n/a'} gp):")
        report.extend(f"  - {g}" for g in cd["gear"])
    counts = spell_counts(cd, level, ability_mod(scores.get(cd["spell_ability"] or "int", 10)))
    if counts:
        report.append("Spells: " + ", ".join(f"{v} {k}" for k, v in counts.items())
                      + f" — record them with `./dm pc set {pc['slug']} spells \"A, B, C\"`.")
    if rd["languages"]:
        report.append("Languages: " + ", ".join(rd["languages"]))
    return pc, report, problems


def equip_all(pc: dict, camp: Campaign | None = None) -> list[str]:
    """After a build: wear the first armor and shield in the inventory and turn weapons into attacks."""
    db = _db()
    lines = []
    for name in list(pc.get("inventory") or []):
        it, bonus = item_entry(db, name, camp)
        if not it:
            continue
        t = it.get("type", "").split("|")[0]
        eq = pc.setdefault("equipped", {})
        if t in ("LA", "MA", "HA") and not eq.get("armor"):
            lines += equip(pc, name, camp)
        elif t == "S" and not eq.get("shield"):
            lines += equip(pc, name, camp)
        elif it.get("weapon") or t in ("M", "R"):
            lines += equip(pc, name, camp)
    return lines


# =============================================================================== equipment

def equip(pc: dict, name: str, camp: Campaign | None = None) -> list[str]:
    db = _db()
    it, bonus = item_entry(db, name, camp)
    if it is None:
        raise RoboDMError(f"No item '{name}' in the rules data (`./dm rules search {name} --type item`).")
    label = name.strip() if bonus else it["name"]
    inv = pc.setdefault("inventory", [])
    lines = []
    if not any(norm_name(x) == norm_name(label) for x in inv):
        inv.append(label)
        lines.append(f"(added {label} to the inventory)")
    eq = pc.setdefault("equipped", {})
    t = it.get("type", "").split("|")[0]
    if t in ("LA", "MA", "HA"):
        eq["armor"] = label
        if not proficient_with(pc, it):
            lines.append(f"⚠️ {pc['name']} isn't proficient with {ARMOR_TYPES[t]} armor: disadvantage on any ability check, "
                         "saving throw or attack roll that uses STR or DEX, and no spellcasting (PHB p.144).")
        if it.get("strength") and pc["abilities"]["str"] < int(it["strength"]):
            lines.append(f"⚠️ {label} needs STR {it['strength']}: speed −10 ft. while wearing it (PHB p.144).")
        if it.get("stealth"):
            lines.append(f"{label}: disadvantage on Stealth checks.")
    elif t == "S":
        eq["shield"] = label
        if not proficient_with(pc, it):
            lines.append(f"⚠️ {pc['name']} isn't proficient with shields (same penalties as armor, PHB p.144).")
    elif it.get("weapon") or t in ("M", "R"):
        atk = weapon_attack(pc, it, bonus, label)
        pc["attacks"] = [a for a in pc.get("attacks", []) if a["name"].lower() != atk["name"].lower()] + [atk]
        lines.append(f"⚔️ {atk['name']}: {fmt_mod(atk['to_hit'])} to hit, {atk['damage'][0][0]} {atk['damage'][0][1]}"
                     + (f" (versatile {atk['versatile']})" if atk.get("versatile") else "")
                     + (f", range {'/'.join(map(str, atk['range']))} ft." if atk.get("range") else "")
                     + (f", reach {atk['reach']} ft." if atk.get("reach") else "")
                     + (f" — {atk['note']}" if atk.get("note") else ""))
        return lines
    else:
        eq.setdefault("worn", [])
        if label not in eq["worn"]:
            eq["worn"].append(label)
        if it.get("reqAttune"):
            lines.append(f"{label} requires attunement (a short rest): `./dm pc attune {pc['slug']} \"{label}\"`.")
    ac, how = compute_ac(pc, db, camp)
    old = pc.get("ac")
    pc["ac"] = ac
    lines.append(f"🛡 AC {old} → {ac} ({how})" if old != ac else f"🛡 AC {ac} ({how})")
    return lines


def unequip(pc: dict, name: str, camp: Campaign | None = None) -> list[str]:
    eq = pc.setdefault("equipped", {})
    key = norm_name(name)
    lines = []
    for slot in ("armor", "shield"):
        if eq.get(slot) and norm_name(eq[slot]) == key:
            eq[slot] = None
            lines.append(f"Took off {name}.")
    before = len(pc.get("attacks", []))
    pc["attacks"] = [a for a in pc.get("attacks", []) if norm_name(a.get("from_item") or "") != key]
    if len(pc["attacks"]) < before:
        lines.append(f"Put away {name}.")
    if eq.get("worn"):
        eq["worn"] = [w for w in eq["worn"] if norm_name(w) != key]
    if not lines:
        raise RoboDMError(f"{pc['name']} doesn't have {name} equipped.")
    ac, how = compute_ac(pc, None, camp)
    pc["ac"] = ac
    lines.append(f"🛡 AC {ac} ({how})")
    return lines


def attune_problems(pc: dict, name: str, camp: Campaign | None = None) -> tuple[dict | None, list[str]]:
    it, _ = item_entry(_db(), name, camp)
    if it is None:
        return None, [f"no item '{name}' in the rules data"]
    probs = []
    if not it.get("reqAttune"):
        probs.append(f"{it['name']} doesn't require attunement")
    elif isinstance(it["reqAttune"], str):
        probs.append(f"{it['name']} requires attunement {it['reqAttune']}: check {pc['name']} qualifies")
    if len(pc.get("attuned") or []) >= MAX_ATTUNED:
        probs.append(f"{pc['name']} is already attuned to {MAX_ATTUNED} items ({', '.join(pc['attuned'])}): end one first "
                     "(DMG p.138)")
    return it, probs


def attune(pc: dict, name: str, camp: Campaign | None = None) -> list[str]:
    it, _ = item_entry(_db(), name, camp)
    label = it["name"] if it else name
    pc.setdefault("attuned", [])
    if label not in pc["attuned"]:
        pc["attuned"].append(label)
    inv = pc.setdefault("inventory", [])
    if not any(norm_name(x) == norm_name(label) for x in inv):
        inv.append(label)
    ac, how = compute_ac(pc, None, camp)
    lines = [f"✨ {pc['name']} attunes to {label} ({len(pc['attuned'])}/{MAX_ATTUNED})."]
    if ac != pc.get("ac"):
        lines.append(f"🛡 AC {pc.get('ac')} → {ac} ({how})")
        pc["ac"] = ac
    return lines


def unattune(pc: dict, name: str, camp: Campaign | None = None) -> list[str]:
    key = norm_name(name)
    if not any(norm_name(a) == key for a in pc.get("attuned") or []):
        raise RoboDMError(f"{pc['name']} isn't attuned to {name}.")
    pc["attuned"] = [a for a in pc["attuned"] if norm_name(a) != key]
    ac, how = compute_ac(pc, None, camp)
    pc["ac"] = ac
    return [f"{pc['name']} ends attunement to {name} ({len(pc['attuned'])}/{MAX_ATTUNED}).", f"🛡 AC {ac} ({how})"]


# =============================================================================== level-up

def _meets(scores: dict, req: dict | None) -> bool:
    if not req:
        return True
    if "or" in req:
        return any(_meets(scores, r) for r in req["or"])
    return all(scores.get(k, 0) >= v for k, v in req.items() if k in ABILITIES)


def _req_text(req: dict) -> str:
    if "or" in req:
        return " or ".join(_req_text(r) for r in req["or"])
    return " and ".join(f"{k.upper()} {v}" for k, v in req.items() if k in ABILITIES)


def levelup_plan(camp: Campaign, pc: dict, cls: str = "", subclass: str = "", hp: str | None = None,
                 asi: dict[str, int] | None = None, feat: str = "") -> tuple[dict, list[str], list[str]]:
    """(the sheet after the level-up, change lines, problems). Nothing is saved here."""
    new = copy.deepcopy(pc)
    lines: list[str] = []
    problems: list[str] = []
    classes = campaign_mod.pc_classes(new)
    target = (cls or pc.get("class") or "").strip()
    entry = next((c for c in classes if c["class"].lower() == target.lower()), None)
    try:
        db = _db()
        cd = class_data(db, target, camp)
    except RoboDMError:
        db, cd = None, None
    if entry is None:                                    # multiclassing into a new class
        if cd:
            for c in classes:
                other = class_data(db, c["class"], camp)
                if other and not _meets(pc["abilities"], other["multiclass"]):
                    problems.append(f"multiclassing out of {c['class']} needs {_req_text(other['multiclass'])} (PHB p.163)")
            if not _meets(pc["abilities"], cd["multiclass"]):
                problems.append(f"multiclassing into {cd['name']} needs {_req_text(cd['multiclass'])} (PHB p.163)")
        entry = {"class": cd["name"] if cd else target.title(), "level": 0, "subclass": ""}
        classes.append(entry)
        if cd:
            gained = cd["multiclass_profs"]
            prof = new.setdefault("proficiencies", {"armor": [], "weapons": [], "tools": [], "languages": []})
            prof["armor"] = prof.get("armor", []) + [_strip(a) for a in gained.get("armor") or []]
            prof["weapons"] = prof.get("weapons", []) + [_strip(w) for w in gained.get("weapons") or []]
            if gained:
                lines.append(f"＋ Proficiencies from multiclassing: "
                             + ", ".join(_strip(x) for x in (gained.get("armor") or []) + (gained.get("weapons") or [])))
    entry["level"] += 1
    if len(classes) > 1:
        new["classes"] = classes
    new["level"] = pc["level"] + 1
    new["hit_dice"]["max"] = new["level"]
    lines.append(f"＋ Level {pc['level']} → {new['level']}" + (f" ({entry['class']} {entry['level']})" if len(classes) > 1 or entry["level"] != new["level"] else ""))

    # Hit points.
    con = ability_mod(new["abilities"]["con"])
    die = cd["hd"] if cd else campaign_mod.HIT_DICE.get(entry["class"].lower(), new["hit_dice"]["die"])
    if hp and str(hp).isdigit():
        gain, how = int(hp), "given"
    elif hp == "roll":
        r = dice.roll(f"1d{die}", label="hit points")
        gain, how = max(1, r.total + con), f"rolled {r.total}{fmt_mod(con)}"
    else:
        gain, how = max(1, die // 2 + 1 + con), f"average {die // 2 + 1}{fmt_mod(con)}"
    if any(f.startswith("Dwarven Toughness") for f in new.get("features", [])):
        gain += 1
        how += ", +1 Dwarven Toughness"
    new["hp"]["max"] += gain
    new["hp"]["current"] += gain
    lines.append(f"＋ HP {pc['hp']['max']} → {new['hp']['max']} (+{gain}, {how})")

    if cd is None:
        lines.append(f"({target} isn't in the rules data: add its level-{entry['level']} features by hand.)")
    else:
        if subclass and not entry.get("subclass"):
            sc = find_subclass(cd, subclass)
            if not sc:
                problems.append(f"no {cd['name']} {cd['subclass_title']} '{subclass}' (options: "
                                + ", ".join(s["name"] for s in cd["subclasses"]) + ")")
            else:
                entry["subclass"] = sc["name"]
                if len(classes) == 1:
                    new["subclass"] = sc["name"]
                lines.append(f"＋ {cd['subclass_title']}: {sc['name']}")
        names, due = class_features(cd, entry["level"], entry.get("subclass") or "", camp)
        if due and not entry.get("subclass"):
            problems.append(f"{cd['name']} {entry['level']} gains {_a(cd['subclass_title'])}: add --subclass (options: "
                            + ", ".join(s["name"] for s in cd["subclasses"]) + ")")
        for n in names:
            if n not in new.get("features", []):
                new.setdefault("features", []).append(n)
                lines.append(f"＋ Feature: {n}")
        if any(n.startswith("Ability Score Improvement") for n in names):
            if feat:
                rec, fe = find(db, feat, "feat", camp)
                if fe is None:
                    problems.append(f"no feat '{feat}' in the data")
                else:
                    new["features"].append(f"{fe['name']} (feat)")
                    lines.append(f"＋ Feat: {fe['name']}")
                    problems += _source_problem(camp, "feat", fe["name"], fe.get("source", ""))
            elif asi and asi != {"later": 0}:
                if sum(asi.values()) != 2 or any(v not in (1, 2) for v in asi.values()):
                    problems.append("an Ability Score Improvement is +2 to one score or +1 to two (e.g. --asi str=2 or "
                                    "--asi str=1,con=1)")
                for k, v in asi.items():
                    after = new["abilities"][k] + v
                    if after > 20:
                        problems.append(f"{k.upper()} can't go above 20 ({new['abilities'][k]} + {v})")
                    lines.append(f"＋ {ABILITY_NAMES[k]} {new['abilities'][k]} → {after}")
                    new["abilities"][k] = after
            elif asi != {"later": 0}:
                problems.append("this level has an Ability Score Improvement: add --asi str=2 / --asi str=1,con=1, "
                                "--feat \"Name\", or --asi later to decide afterwards")
            else:
                new["notes"] = (new.get("notes", "") + f" ASI pending ({entry['class']} {entry['level']}).").strip()
                lines.append("＋ Ability Score Improvement: to be decided (noted on the sheet)")
        diff = ability_mod(new["abilities"]["con"]) - ability_mod(pc["abilities"]["con"])
        if diff:                                         # a higher CON modifier raises HP for every level (PHB p.15)
            new["hp"]["max"] += diff * new["level"]
            new["hp"]["current"] += diff * new["level"]
            lines.append(f"＋ HP max {fmt_mod(diff * new['level'])} for the CON modifier change ({new['hp']['max']})")
        before = spell_counts(cd, entry["level"] - 1, ability_mod(pc["abilities"].get(cd["spell_ability"] or "int", 10))) if entry["level"] > 1 else {}
        after = spell_counts(cd, entry["level"], ability_mod(new["abilities"].get(cd["spell_ability"] or "int", 10)))
        for k, v in after.items():
            if v != before.get(k):
                lines.append(f"＋ {k.title()}: {before.get(k, 0)} → {v}" + (" (choose the new ones)" if k != "prepared" else ""))
    old_slots = pc.get("spell_slots") or {}
    new_slots = campaign_mod.pc_slots(new)
    merged = {k: {**v, "used": min((old_slots.get(k) or {}).get("used", 0), v["max"])} for k, v in new_slots.items()}
    new["spell_slots"] = merged
    for k, v in sorted(merged.items()):
        if (old_slots.get(k) or {}).get("max") != v["max"]:
            lines.append(f"＋ {campaign_mod.slot_label(k, v)} slots: {(old_slots.get(k) or {}).get('max', 0)} → {v['max']}")
    if campaign_mod.pc_prof(new) != campaign_mod.pc_prof(pc):
        lines.append(f"＋ Proficiency bonus {fmt_mod(campaign_mod.pc_prof(pc))} → {fmt_mod(campaign_mod.pc_prof(new))}")
    if new.get("equipped") or any(a.get("from_item") for a in new.get("attacks", [])):
        try:
            for a in new.get("attacks", []):
                if a.get("from_item"):
                    it, bonus = item_entry(db or _db(), a["from_item"], camp)
                    if it:
                        a.update(weapon_attack(new, it, bonus, a["from_item"]))
            new["ac"], _ = compute_ac(new, db, camp)
        except RoboDMError:
            pass
    return new, lines, problems


# =============================================================================== options for the web Forge

def forge_options(camp: Campaign | None = None) -> dict:
    """Everything the web client's New-character form offers, limited to the campaign's sources."""
    db = _db()
    srcs = _sources(camp)
    ok = (lambda s: not srcs or str(s).upper() in srcs)
    races_raw = db.load_file("races.json") or {}
    races = set()
    for r in races_raw.get("race", []):
        if not ok(r.get("source")) or "_copy" in r and not r.get("name"):
            continue
        subs = [s for s in races_raw.get("subrace", []) if s.get("raceName") == r["name"]
                and s.get("raceSource") == r.get("source")]
        if not subs or any(not s.get("name") for s in subs):
            races.add(r["name"])
        races.update(f"{s['name']} {r['name']}" for s in subs if s.get("name") and ok(s.get("source"))
                     and not s["name"].startswith("Variant;"))
    classes = []
    for rec in db._filtered("class"):
        if not ok(rec["s"]) or any(c["name"] == rec["n"] for c in classes):
            continue
        cd = class_data(db, rec["n"], camp)
        if cd:
            classes.append({"name": cd["name"], "skill_count": cd["skill_count"], "skill_from": cd["skill_from"],
                            "subclass_title": cd["subclass_title"], "subclass_level": cd["subclass_level"],
                            "subclasses": [s["name"] for s in cd["subclasses"]], "gear": cd["gear"],
                            "gold": cd["gold_dice"]})
    backgrounds = sorted({r["n"] for r in db._filtered("background") if ok(r["s"])})
    return {"races": sorted(races), "classes": sorted(classes, key=lambda c: c["name"]), "backgrounds": backgrounds,
            "standard": STANDARD_ARRAY, "point_cost": POINT_BUY_COST, "budget": POINT_BUY_BUDGET,
            "skills": sorted(SKILLS)}
