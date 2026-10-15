"""Readable text for spells, items, classes, races and every other rules entry."""

from __future__ import annotations

from typing import Any

from robodm.rules.index import SCHOOLS, RulesDB
from robodm.rules.render import render_entries, strip_tags
from robodm.rules.statblock import format_monster
from robodm.util import ABILITY_NAMES, ordinal

DMG_TYPES = {"A": "acid", "B": "bludgeoning", "C": "cold", "F": "fire", "O": "force", "L": "lightning",
             "N": "necrotic", "P": "piercing", "I": "poison", "Y": "psychic", "R": "radiant", "S": "slashing",
             "T": "thunder"}
ITEM_TYPES = {"M": "melee weapon", "R": "ranged weapon", "A": "ammunition", "LA": "light armor", "MA": "medium armor",
              "HA": "heavy armor", "S": "shield", "P": "potion", "RD": "rod", "RG": "ring", "SC": "scroll",
              "WD": "wand", "G": "adventuring gear", "AT": "artisan's tools", "T": "tools", "INS": "instrument",
              "GS": "gaming set", "SCF": "spellcasting focus", "MNT": "mount", "VEH": "vehicle (land)",
              "SHP": "vehicle (water)", "AIR": "vehicle (air)", "TAH": "tack and harness", "TG": "trade good",
              "$": "treasure", "$A": "art object", "$G": "gemstone", "$C": "coins", "FD": "food and drink",
              "OTH": "other", "EXP": "explosive", "AF": "ammunition (firearm)", "SPC": "vehicle (space)",
              "EM": "eldritch machine", "IDG": "illegal drug", "TB": "trade bar", "GV": "generic variant"}
ITEM_PROPS = {"V": "versatile", "F": "finesse", "L": "light", "H": "heavy", "T": "thrown", "2H": "two-handed",
              "R": "reach", "A": "ammunition", "LD": "loading", "S": "special", "AF": "ammunition (firearm)",
              "RLD": "reload", "BF": "burst fire"}


def _src(e: dict) -> str:
    page = f" p.{e['page']}" if e.get("page") else ""
    srd = " · SRD" if e.get("srd") else ""
    return f"[{e.get('source', '?')}{page}{srd}]"


# ---------------------------------------------------------------- spells

def _spell_level(e: dict) -> str:
    school = SCHOOLS.get(e.get("school", ""), e.get("school", ""))
    lvl = e.get("level", 0)
    ritual = " (ritual)" if (e.get("meta") or {}).get("ritual") else ""
    if lvl == 0:
        return f"{school} cantrip{ritual}"
    return f"{ordinal(lvl)}-level {school.lower()}{ritual}"


def _spell_time(e: dict) -> str:
    out = []
    for t in e.get("time", []):
        s = f"{t.get('number', 1)} {t.get('unit', '')}"
        if t.get("number", 1) != 1 and not s.endswith("s"):
            s += "s"
        s = s.replace("bonus", "bonus action")
        if t.get("condition"):
            s += f", {strip_tags(t['condition'])}"
        out.append(s)
    return " or ".join(out)


def _spell_range(e: dict) -> str:
    r = e.get("range") or {}
    rtype = r.get("type")
    d = r.get("distance") or {}
    dtype, amt = d.get("type"), d.get("amount")
    if rtype == "special":
        return "Special"
    if rtype == "point":
        if dtype in ("self", "touch", "sight", "unlimited"):
            return dtype.capitalize()
        return f"{amt} {'foot' if amt == 1 and dtype == 'feet' else dtype}"
    if rtype in ("radius", "sphere", "cone", "line", "cube", "hemisphere", "cylinder", "emanation"):
        unit = "mile" if dtype == "miles" else "foot"
        return f"Self ({amt}-{unit} {rtype})"
    return str(rtype or "?")


def _spell_components(e: dict) -> str:
    c = e.get("components") or {}
    parts = []
    if c.get("v"):
        parts.append("V")
    if c.get("s"):
        parts.append("S")
    m = c.get("m")
    if m:
        if isinstance(m, dict):
            txt = m.get("text", "")
            parts.append(f"M ({txt})")
        elif m is True:
            parts.append("M")
        else:
            parts.append(f"M ({m})")
    if c.get("r"):
        parts.append("R")
    return ", ".join(parts)


def _spell_duration(e: dict) -> str:
    out = []
    for d in e.get("duration", []):
        t = d.get("type")
        if t == "instant":
            out.append("Instantaneous")
        elif t == "timed":
            dur = d.get("duration", {})
            amt, unit = dur.get("amount", 1), dur.get("type", "")
            txt = f"{amt} {unit}{'s' if amt != 1 else ''}"
            if d.get("concentration"):
                txt = f"Concentration, up to {txt}"
            elif dur.get("upTo"):
                txt = f"Up to {txt}"
            out.append(txt)
        elif t == "permanent":
            ends = d.get("ends", [])
            if ends:
                words = {"dispel": "dispelled", "trigger": "triggered", "discharge": "discharged"}
                out.append("Until " + " or ".join(words.get(x, x) for x in ends))
            else:
                out.append("Permanent")
        elif t == "special":
            out.append("Special")
    return " or ".join(out)


def format_spell(e: dict, classes: list[str] | None = None) -> str:
    lines = [f"## {e['name']}  {_src(e)}", f"*{_spell_level(e)}*"]
    lines.append(f"**Casting Time** {_spell_time(e)} · **Range** {_spell_range(e)}")
    lines.append(f"**Components** {_spell_components(e)} · **Duration** {_spell_duration(e)}")
    if classes:
        lines.append(f"**Classes** {', '.join(classes)}")
    lines.append("")
    lines.append(render_entries(e.get("entries", []), 1))
    if e.get("entriesHigherLevel"):
        lines.append("")
        lines.append(render_entries(e["entriesHigherLevel"], 1))
    tags = []
    if e.get("savingThrow"):
        tags.append("save: " + ", ".join(e["savingThrow"]))
    if e.get("spellAttack"):
        tags.append("spell attack: " + ", ".join({"M": "melee", "R": "ranged"}.get(x, x) for x in e["spellAttack"]))
    if e.get("damageInflict"):
        tags.append("damage: " + ", ".join(e["damageInflict"]))
    if e.get("conditionInflict"):
        tags.append("conditions: " + ", ".join(e["conditionInflict"]))
    if tags:
        lines.append("\n_" + " · ".join(tags) + "_")
    return "\n".join(lines)


# ---------------------------------------------------------------- items

def _value(cp: int | None) -> str:
    if not cp:
        return ""
    if cp % 100 == 0:
        return f"{cp // 100:,} gp"
    if cp % 10 == 0:
        return f"{cp // 10} sp"
    return f"{cp} cp"


def format_item(e: dict) -> str:
    lines = [f"## {e['name']}  {_src(e)}"]
    bits = []
    t = (e.get("type") or "").split("|")[0]
    if e.get("wondrous"):
        bits.append("wondrous item")
    elif t:
        bits.append(ITEM_TYPES.get(t, t))
    if e.get("weaponCategory"):
        bits.append(f"{e['weaponCategory']} weapon")
    rarity = e.get("rarity")
    if rarity and rarity not in ("none", "unknown"):
        bits.append(rarity)
    att = e.get("reqAttune")
    if att:
        bits.append("requires attunement" + (f" {strip_tags(att)}" if isinstance(att, str) else ""))
    lines.append("*" + ", ".join(bits) + "*" if bits else "")
    stats = []
    if e.get("ac") is not None:
        stats.append(f"**AC** {e['ac']}")
    if e.get("dmg1"):
        dmg = f"{e['dmg1']} {DMG_TYPES.get(e.get('dmgType', ''), '')}".strip()
        if e.get("dmg2"):
            dmg += f" (versatile {e['dmg2']})"
        stats.append(f"**Damage** {dmg}")
    if e.get("property"):
        props = [ITEM_PROPS.get(str(p).split("|")[0], str(p)) for p in e["property"]]
        if e.get("range"):
            props = [p + (f" ({e['range']})" if p in ("thrown", "ammunition") else "") for p in props]
        stats.append(f"**Properties** {', '.join(props)}")
    if e.get("strength"):
        stats.append(f"**Str** {e['strength']}")
    if e.get("stealth"):
        stats.append("**Stealth** disadvantage")
    if e.get("weight"):
        stats.append(f"**Weight** {e['weight']} lb.")
    if e.get("value"):
        stats.append(f"**Value** {_value(e['value'])}")
    if e.get("bonusWeapon"):
        stats.append(f"**Weapon bonus** {e['bonusWeapon']}")
    if e.get("bonusAc"):
        stats.append(f"**AC bonus** {e['bonusAc']}")
    if e.get("charges"):
        stats.append(f"**Charges** {e['charges']}")
    if stats:
        lines.append(" · ".join(stats))
    body = render_entries(e.get("entries", []), 1)
    if body:
        lines.append("")
        lines.append(body)
    if e.get("inherits"):
        inh = e["inherits"]
        lines.append(render_entries(inh.get("entries", []), 1))
    return "\n".join(x for x in lines if x is not None)


# ---------------------------------------------------------------- classes

def format_class(e: dict, db: RulesDB | None = None) -> str:
    lines = [f"## {e['name']}  {_src(e)}"]
    hd = e.get("hd") or {}
    if hd:
        lines.append(f"**Hit Die** d{hd.get('faces')} · **Saves** {', '.join(ABILITY_NAMES.get(a, a) for a in e.get('proficiency', []))}")
    sp = e.get("startingProficiencies") or {}
    if sp.get("armor"):
        lines.append("**Armor** " + ", ".join(strip_tags(a) if isinstance(a, str) else a.get("full", "") for a in sp["armor"]))
    if sp.get("weapons"):
        lines.append("**Weapons** " + ", ".join(strip_tags(w) if isinstance(w, str) else str(w) for w in sp["weapons"]))
    for sk in sp.get("skills", []) or []:
        if isinstance(sk, dict) and "choose" in sk:
            ch = sk["choose"]
            lines.append(f"**Skills** choose {ch.get('count', 1)} from {', '.join(ch.get('from', []))}")
        elif isinstance(sk, dict) and "any" in sk:
            lines.append(f"**Skills** any {sk['any']}")
    if e.get("spellcastingAbility"):
        lines.append(f"**Spellcasting** {ABILITY_NAMES.get(e['spellcastingAbility'], e['spellcastingAbility'])}"
                     f" ({e.get('casterProgression', '')} caster)")
    by_level: dict[int, list[str]] = {}
    for f in e.get("classFeatures", []):
        ref = f if isinstance(f, str) else f.get("classFeature", "")
        parts = ref.split("|")
        if len(parts) >= 4:
            src = parts[4] if len(parts) > 4 and parts[4] else ""
            name = parts[0] + (f" ({src})" if src and src != e.get("source") else "")
            try:
                by_level.setdefault(int(parts[3]), []).append(name)
            except ValueError:
                pass
    if by_level:
        lines.append("\n**Features by level**")
        for lvl in sorted(by_level):
            lines.append(f"- {lvl}: {', '.join(by_level[lvl])}")
    if db is not None:
        subs = sorted({(r["n"], r["s"]) for r in db.index if r["t"] == "subclass" and r.get("cl") == e["name"]})
        if subs:
            lines.append(f"\n**{e.get('subclassTitle', 'Subclasses')}** " + ", ".join(f"{n} ({s})" for n, s in subs))
    lines.append("\n_Use `rules show \"<feature name>\" --type classfeature` for a feature's full text._")
    return "\n".join(lines)


def format_subclass(e: dict) -> str:
    lines = [f"## {e['name']} ({e.get('className', '')} subclass)  {_src(e)}"]
    feats = []
    for f in e.get("subclassFeatures", []):
        ref = f if isinstance(f, str) else f.get("subclassFeature", "")
        parts = ref.split("|")
        if len(parts) >= 6:
            feats.append(f"{parts[5]}: {parts[0]}")
    if feats:
        lines.append("**Features** " + "; ".join(feats))
    return "\n".join(lines)


# ---------------------------------------------------------------- races, feats, generic

def _ability_bonus(ab: list) -> str:
    out = []
    for a in ab or []:
        if not isinstance(a, dict):
            continue
        for k, v in a.items():
            if k == "choose":
                ch = v
                cnt = ch.get("count", 1)
                amt = ch.get("amount", 1)
                out.append(f"choose {cnt} of {', '.join(x.upper() for x in ch.get('from', []))} +{amt}")
            elif isinstance(v, int):
                out.append(f"{k.upper()} {'+' if v >= 0 else ''}{v}")
    return "; ".join(out)


def _prereq(p: list) -> str:
    out = []
    for req in p or []:
        if not isinstance(req, dict):
            continue
        for k, v in req.items():
            if k == "level":
                out.append(f"level {v.get('level', v) if isinstance(v, dict) else v}")
            elif k == "ability":
                for ab in v:
                    out.append(" or ".join(f"{ABILITY_NAMES.get(a, a)} {n}+" for a, n in ab.items()))
            elif k == "race":
                out.append("race: " + " or ".join(r.get("name", "") for r in v))
            elif k == "spellcasting" or k == "spellcasting2020":
                out.append("ability to cast at least one spell")
            elif k == "proficiency":
                for pr in v:
                    out.append("proficiency with " + ", ".join(f"{a} {b}" for a, b in pr.items()))
            elif k == "other":
                out.append(strip_tags(v))
            elif k == "spell":
                out.append("spell: " + ", ".join(strip_tags(s).split("#")[0] for s in v))
            elif k == "feat":
                out.append("feat: " + ", ".join(strip_tags(s).split("|")[0] for s in v))
            elif k == "pact":
                out.append(f"Pact of the {v}")
            elif k == "patron":
                out.append(f"{v} patron")
    return "; ".join(out)


def format_generic(e: dict, t: str) -> str:
    lines = [f"## {e.get('name')}  {_src(e)}" + (f"  ({t})" if t else "")]
    if e.get("prerequisite"):
        lines.append(f"*Prerequisite: {_prereq(e['prerequisite'])}*")
    if t == "subrace" and e.get("raceName"):
        lines[0] = f"## {e['raceName']} ({e.get('name')})  {_src(e)}  (subrace — also has all {e['raceName']} traits)"
    if t in ("race", "subrace"):
        bits = []
        if e.get("ability"):
            bits.append(f"**Ability** {_ability_bonus(e['ability'])}")
        if e.get("size"):
            bits.append(f"**Size** {'/'.join(e['size'])}")
        if e.get("speed"):
            sp = e["speed"]
            bits.append(f"**Speed** {sp if isinstance(sp, int) else ', '.join(f'{k} {v}' for k, v in sp.items())}")
        if e.get("darkvision"):
            bits.append(f"**Darkvision** {e['darkvision']} ft.")
        if bits:
            lines.append(" · ".join(bits))
    if t == "feat" and e.get("ability"):
        lines.append(f"**Ability** {_ability_bonus(e['ability'])}")
    if t == "optionalfeature" and e.get("featureType"):
        lines.append(f"_feature type: {', '.join(e['featureType'])}_")
    if t in ("classfeature", "subclassfeature"):
        who = e.get("className", "")
        if e.get("subclassShortName"):
            who += f" ({e['subclassShortName']})"
        lines.append(f"*{who} feature, level {e.get('level', '?')}*")
    if t == "action" and e.get("time"):
        lines.append("*Time: " + ", ".join(f"{x.get('number', 1)} {x.get('unit')}" if isinstance(x, dict) else str(x) for x in e["time"]) + "*")
    if t == "skill" and e.get("ability"):
        lines.append(f"*Ability: {ABILITY_NAMES.get(e['ability'], e['ability'])}*")
    if t == "deity":
        bits = [f"{k}: {strip_tags(str(e[k])) if not isinstance(e[k], list) else ', '.join(map(str, e[k]))}"
                for k in ("pantheon", "alignment", "domains", "symbol", "title") if e.get(k)]
        if bits:
            lines.append("*" + " · ".join(bits) + "*")
    if t == "legendarygroup":
        if e.get("lairActions"):
            lines.append("**Lair Actions**\n" + render_entries(e["lairActions"], 1))
        if e.get("regionalEffects"):
            lines.append("**Regional Effects**\n" + render_entries(e["regionalEffects"], 1))
        return "\n".join(lines)
    body = render_entries(e.get("entries", e.get("entry", [])), 1)
    if body:
        lines.append("")
        lines.append(body)
    if t == "itemgroup" and e.get("items"):
        lines.append("Items: " + ", ".join(i.split("|")[0] for i in e["items"]))
    return "\n".join(lines)


def format_record(db: RulesDB, rec: dict) -> str:
    e = db.get(rec)
    t = rec["t"]
    if t == "spell":
        return format_spell(e, rec.get("cls"))
    if t == "monster":
        lair = None
        lg = e.get("legendaryGroup")
        if isinstance(lg, dict):
            lrec = db.find_exact(lg.get("name", ""), "legendarygroup", lg.get("source"))
            if lrec:
                lair = db.get(lrec)
        return format_monster(e, lair)
    if t == "item":
        return format_item(e)
    if t == "class":
        return format_class(e, db)
    if t == "subclass":
        return format_subclass(e)
    if t in ("quickref", "book"):
        title = f"## {e.get('name')}  [{rec['s']} {'quick reference' if t == 'quickref' else 'book'}]"
        if rec.get("parent"):
            title += f" — part of '{rec['parent']}'"
        return title + "\n\n" + render_entries(e.get("entries", []), 1)
    return format_generic(e, t)


def summary_line(rec: dict) -> str:
    extra = ""
    t = rec["t"]
    if t == "spell":
        lvl = rec.get("lvl", 0)
        extra = f"{'cantrip' if lvl == 0 else ordinal(lvl) + ' level'} {SCHOOLS.get(rec.get('sch', ''), '').lower()}"
        if rec.get("conc"):
            extra += ", conc."
        if rec.get("cls"):
            extra += f" — {', '.join(rec['cls'][:6])}"
    elif t == "monster":
        extra = f"CR {rec.get('cr', '?')} {rec.get('mt', '')}".strip()
    elif t == "item":
        extra = ", ".join(x for x in (ITEM_TYPES.get(rec.get("it", ""), rec.get("it", "")), rec.get("rar")) if x and x != "none")
    elif t in ("classfeature", "subclassfeature"):
        extra = f"{rec.get('cl', '')}{' (' + rec['sc'] + ')' if rec.get('sc') else ''} lvl {rec.get('lv', '?')}"
    elif t == "subclass":
        extra = rec.get("cl", "")
    elif rec.get("parent"):
        extra = f"in {rec['parent']}"
    srd = " SRD" if rec.get("srd") else ""
    return f"{rec['n']} [{t} · {rec['s']}{srd}]" + (f" — {extra}" if extra else "")


def as_text(value: Any) -> str:
    return render_entries(value)
