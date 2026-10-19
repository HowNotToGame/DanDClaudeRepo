"""Monster stat blocks: readable text plus the numbers the combat tracker needs."""

from __future__ import annotations

import re
from typing import Any

from robodm.rules.index import cr_text, cr_to_float, monster_type
from robodm.rules.render import render_entries, render_spellcasting, strip_tags
from robodm.util import ABILITIES, ability_mod, fmt_mod

SIZES = {"T": "Tiny", "S": "Small", "M": "Medium", "L": "Large", "H": "Huge", "G": "Gargantuan"}
ALIGN = {"L": "lawful", "N": "neutral", "C": "chaotic", "G": "good", "E": "evil", "U": "unaligned", "A": "any alignment"}

CR_XP = {
    "0": 10, "1/8": 25, "1/4": 50, "1/2": 100, "1": 200, "2": 450, "3": 700, "4": 1100, "5": 1800, "6": 2300,
    "7": 2900, "8": 3900, "9": 5000, "10": 5900, "11": 7200, "12": 8400, "13": 10000, "14": 11500, "15": 13000,
    "16": 15000, "17": 18000, "18": 20000, "19": 22000, "20": 25000, "21": 33000, "22": 41000, "23": 50000,
    "24": 62000, "25": 75000, "26": 90000, "27": 105000, "28": 120000, "29": 135000, "30": 155000,
}

DAMAGE_TYPES = ["acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic", "piercing", "poison",
                "psychic", "radiant", "slashing", "thunder"]


def xp_for_cr(cr: Any) -> int:
    return CR_XP.get(cr_text(cr), 0)


def _alignment(m: dict) -> str:
    al = m.get("alignment")
    if not al:
        return ""
    if all(isinstance(a, str) for a in al):
        if set(al) == {"L", "N", "C", "G", "E"} or al == ["A"]:
            return "any alignment"
        if al == ["N"]:
            return "neutral"
        return " ".join(ALIGN.get(a, a) for a in al)
    parts = []
    for a in al:
        if isinstance(a, dict):
            if "special" in a:
                parts.append(a["special"])
            else:
                txt = " ".join(ALIGN.get(x, x) for x in a.get("alignment", []))
                if a.get("chance"):
                    txt += f" ({a['chance']}%)"
                parts.append(txt)
        else:
            parts.append(ALIGN.get(a, a))
    return " or ".join(parts)


def ac_value(m: dict) -> int:
    for a in m.get("ac", []):
        if isinstance(a, int):
            return a
        if isinstance(a, dict) and "ac" in a and not a.get("condition"):
            return int(a["ac"])
    for a in m.get("ac", []):
        if isinstance(a, dict) and "ac" in a:
            return int(a["ac"])
    return 10


def ac_text(m: dict) -> str:
    out = []
    for a in m.get("ac", []):
        if isinstance(a, int):
            out.append(str(a))
        elif isinstance(a, dict):
            if "special" in a:
                out.append(strip_tags(a["special"]))
                continue
            s = str(a.get("ac", "?"))
            if a.get("from"):
                s += f" ({', '.join(strip_tags(f) for f in a['from'])})"
            if a.get("condition"):
                s += f" {strip_tags(a['condition'])}"
            out.append(s)
    return ", ".join(out) or "10"


def hp_info(m: dict) -> tuple[int, str]:
    hp = m.get("hp") or {}
    if "average" in hp:
        return int(hp["average"]), str(hp.get("formula", ""))
    if "special" in hp:
        nums = re.findall(r"\d+", str(hp["special"]))
        return (int(nums[0]) if nums else 1), str(hp["special"])
    return 1, ""


def speed_text(m: dict) -> str:
    sp = m.get("speed") or {}
    if isinstance(sp, int):
        return f"{sp} ft."
    parts = []
    for mode in ("walk", "burrow", "climb", "fly", "swim"):
        if mode not in sp:
            continue
        v = sp[mode]
        cond = ""
        if isinstance(v, dict):
            cond = f" {v.get('condition', '')}".rstrip()
            v = v.get("number", 0)
        txt = f"{v} ft.{cond}" if mode == "walk" else f"{mode} {v} ft.{cond}"
        if mode == "fly" and sp.get("canHover") and "hover" not in txt:
            txt += " (hover)"
        parts.append(txt)
    return ", ".join(parts)


def _defense_list(items: list, key: str) -> tuple[list[str], list[dict], str]:
    """Return (unconditional types, conditional groups, display text)."""
    plain, cond, shown = [], [], []
    for it in items or []:
        if isinstance(it, str):
            plain.append(it)
            shown.append(it)
        elif isinstance(it, dict):
            if "special" in it:
                shown.append(strip_tags(it["special"]))
                continue
            types = [x for x in it.get(key, []) if isinstance(x, str)]
            note = strip_tags(it.get("note", "") or "")
            pre = strip_tags(it.get("preNote", "") or "")
            nested = [x for x in it.get(key, []) if isinstance(x, dict)]
            if it.get("cond") or note or pre:
                cond.append({"types": types, "note": f"{pre} {note}".strip()})
            else:
                plain.extend(types)
            txt = ", ".join(types)
            if pre:
                txt = f"{pre} {txt}"
            if note:
                txt += f" {note}"
            shown.append(txt)
            for n in nested:
                p2, c2, s2 = _defense_list([n], key)
                plain.extend(p2)
                cond.extend(c2)
                shown.append(s2)
    return plain, cond, "; ".join(s for s in shown if s)


def _condition_immunities(m: dict) -> list[str]:
    out = []
    for c in m.get("conditionImmune", []) or []:
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, dict):
            out.extend(x for x in c.get("conditionImmune", []) if isinstance(x, str))
    return out


def save_bonuses(m: dict) -> dict[str, int]:
    saves = {}
    for ab in ABILITIES:
        saves[ab] = ability_mod(m.get(ab, 10))
    for ab, v in (m.get("save") or {}).items():
        try:
            saves[ab] = int(str(v).replace(" ", ""))
        except ValueError:
            nums = re.findall(r"[+-]?\d+", str(v))
            if nums:
                saves[ab] = int(nums[0])
    return saves


_HIT_RE = re.compile(r"\{@hit (-?\d+)\}")
_DMG_RE = re.compile(r"\{@damage ([^}|]+)[^}]*\}\)?\s*([a-z]+)? ?damage", re.IGNORECASE)
_DC_RE = re.compile(r"\{@dc (\d+)\}\s*(\w+) saving throw", re.IGNORECASE)
_ATK_RE = re.compile(r"\{@atk ([a-z, ]+)\}")
_REACH_RE = re.compile(r"reach (\d+) ?ft", re.IGNORECASE)
_RANGE_RE = re.compile(r"range (\d+)(?:/(\d+))? ?ft", re.IGNORECASE)


def walk_speed(m: dict) -> int:
    sp = m.get("speed") or {}
    if isinstance(sp, int):
        return sp
    v = sp.get("walk", 30 if not sp else 0)
    return int(v.get("number", 0) if isinstance(v, dict) else v or 0)


def speeds(m: dict) -> dict[str, int]:
    sp = m.get("speed") or {}
    if isinstance(sp, int):
        return {"walk": sp}
    out = {}
    for mode in ("walk", "burrow", "climb", "fly", "swim"):
        v = sp.get(mode)
        if v is not None:
            out[mode] = int(v.get("number", 0) if isinstance(v, dict) else v)
    return out or {"walk": 30}


def parse_attacks(m: dict) -> list[dict]:
    """Pull to-hit, damage dice and damage types from a monster's actions."""
    out = []
    for section in ("action", "bonus", "reaction", "legendary"):
        for act in m.get(section, []) or []:
            if not isinstance(act, dict) or not act.get("name"):
                continue
            raw = " ".join(x for x in _flatten_strings(act.get("entries", [])))
            hit = _HIT_RE.search(raw)
            dmg = []
            for dm in _DMG_RE.finditer(raw):
                dice = dm.group(1).replace(" ", "")
                dtype = (dm.group(2) or "").lower()
                dmg.append([dice, dtype if dtype in DAMAGE_TYPES else ""])
            dc = _DC_RE.search(raw)
            if not hit and not dmg and not dc:
                continue
            rec: dict[str, Any] = {"name": strip_tags(act["name"]), "section": section}
            if hit:
                rec["to_hit"] = int(hit.group(1))
                kind = _ATK_RE.search(raw)
                tags = set(kind.group(1).replace(" ", "").split(",")) if kind else set()
                melee, ranged = bool(tags & {"mw", "ms"}), bool(tags & {"rw", "rs"})
                rec["kind"] = "both" if melee and ranged else "ranged" if ranged else "melee"
                reach = _REACH_RE.search(raw)
                if reach:
                    rec["reach"] = int(reach.group(1))
                rng = _RANGE_RE.search(raw)
                if rng:
                    rec["range"] = [int(rng.group(1)), int(rng.group(2) or rng.group(1))]
            if dmg:
                rec["damage"] = dmg
            if dc:
                rec["save_dc"] = int(dc.group(1))
                rec["save_ability"] = dc.group(2).lower()[:3]
                rec["half_on_save"] = "half as much" in raw
            rech = re.search(r"\{@recharge ?(\d?)\}", act["name"])
            if rech:
                rec["recharge"] = int(rech.group(1) or 6)
            out.append(rec)
    return out


def _flatten_strings(o: Any):
    if isinstance(o, str):
        yield o
    elif isinstance(o, list):
        for x in o:
            yield from _flatten_strings(x)
    elif isinstance(o, dict):
        for k in ("entries", "items", "entry"):
            if k in o:
                yield from _flatten_strings(o[k])


def legendary_count(m: dict) -> int:
    if not m.get("legendary"):
        return 0
    return int(m.get("legendaryActions", 3))


def combat_profile(m: dict) -> dict:
    """Numbers needed to run this monster in the tracker."""
    hp_avg, hp_formula = hp_info(m)
    resist, resist_c, _ = _defense_list(m.get("resist"), "resist")
    immune, immune_c, _ = _defense_list(m.get("immune"), "immune")
    vuln, vuln_c, _ = _defense_list(m.get("vulnerable"), "vulnerable")
    return {
        "name": m.get("name"),
        "source": m.get("source"),
        "ac": ac_value(m),
        "hp": hp_avg,
        "hp_formula": hp_formula,
        "abilities": {ab: m.get(ab, 10) for ab in ABILITIES},
        "init_bonus": ability_mod(m.get("dex", 10)) + (m.get("initiative", {}).get("proficiency", 0) if isinstance(m.get("initiative"), dict) else 0),
        "saves": save_bonuses(m),
        "resist": resist,
        "immune": immune,
        "vulnerable": vuln,
        "cond_defenses": [{"kind": "resist", **c} for c in resist_c] + [{"kind": "immune", **c} for c in immune_c]
        + [{"kind": "vulnerable", **c} for c in vuln_c],
        "condition_immune": _condition_immunities(m),
        "attacks": parse_attacks(m),
        "legendary_actions": legendary_count(m),
        "size": (m.get("size") or ["M"])[0],
        "speed": walk_speed(m),
        "speeds": speeds(m),
        "cr": cr_text(m.get("cr")),
        "xp": xp_for_cr(m.get("cr")),
        "type": monster_type(m),
    }


def _type_line(m: dict) -> str:
    size = "/".join(SIZES.get(s, s) for s in (m.get("size") or ["M"]))
    t = m.get("type")
    tags = ""
    if isinstance(t, dict):
        tag_list = [x if isinstance(x, str) else x.get("tag", "") for x in t.get("tags", [])]
        if tag_list:
            tags = f" ({', '.join(tag_list)})"
        if t.get("swarmSize"):
            size = f"{size} swarm of {SIZES.get(t['swarmSize'], t['swarmSize'])}"
    return f"*{size} {monster_type(m)}{tags}, {_alignment(m)}*"


def format_monster(m: dict, lair: dict | None = None) -> str:
    lines = [f"## {m.get('name')}  [{m.get('source', '?')}{' p.' + str(m['page']) if m.get('page') else ''}]", _type_line(m)]
    hp_avg, hp_formula = hp_info(m)
    lines.append(f"**AC** {ac_text(m)} · **HP** {hp_avg}{f' ({hp_formula})' if hp_formula and hp_formula != str(hp_avg) else ''} · **Speed** {speed_text(m)}")
    lines.append(" | ".join(f"{ab.upper()} {m.get(ab, 10)} ({fmt_mod(ability_mod(m.get(ab, 10)))})" for ab in ABILITIES))
    if m.get("save"):
        lines.append("**Saves** " + ", ".join(f"{k.capitalize()} {v}" for k, v in m["save"].items()))
    if m.get("skill"):
        lines.append("**Skills** " + ", ".join(f"{k.title()} {v}" for k, v in m["skill"].items() if isinstance(v, str)))
    for key, label in (("vulnerable", "Vulnerabilities"), ("resist", "Resistances"), ("immune", "Damage Immunities")):
        if m.get(key):
            lines.append(f"**{label}** {_defense_list(m[key], key)[2]}")
    ci = m.get("conditionImmune")
    if ci:
        shown = []
        for c in ci:
            if isinstance(c, str):
                shown.append(c)
            elif isinstance(c, dict):
                shown.append(", ".join(x for x in c.get("conditionImmune", []) if isinstance(x, str)) + (f" {c['note']}" if c.get("note") else ""))
        lines.append(f"**Condition Immunities** {', '.join(shown)}")
    senses = ", ".join(strip_tags(s) for s in (m.get("senses") or []))
    lines.append(f"**Senses** {senses + ', ' if senses else ''}passive Perception {m.get('passive', 10)}")
    langs = ", ".join(strip_tags(x) for x in (m.get("languages") or [])) or "—"
    lines.append(f"**Languages** {langs}")
    cr = m.get("cr")
    cr_extra = ""
    if isinstance(cr, dict) and cr.get("lair"):
        cr_extra = f" ({CR_XP.get(str(cr['lair']), '?')} XP in lair)"
    pb = ""
    crf = cr_to_float(cr)
    if crf is not None:
        pb = f" · PB {fmt_mod(max(2, (int(max(crf, 1)) - 1) // 4 + 2))}"
    lines.append(f"**CR** {cr_text(cr)} ({xp_for_cr(cr):,} XP){cr_extra}{pb}")

    def section(title, items):
        if not items:
            return
        lines.append(f"\n**{title}**" if title else "")
        for it in items:
            if not isinstance(it, dict):
                lines.append(render_entries([it], 1))
                continue
            body = render_entries(it.get("entries", []), 1)
            lines.append(f"- **{strip_tags(it.get('name', ''))}.** {body}".rstrip())

    if m.get("trait"):
        section("Traits", m["trait"])
    for sc in m.get("spellcasting", []) or []:
        lines.append(render_spellcasting(sc))
    section("Actions", m.get("action"))
    section("Bonus Actions", m.get("bonus"))
    section("Reactions", m.get("reaction"))
    if m.get("legendary"):
        header = render_entries(m.get("legendaryHeader") or [], 1) or (
            f"The {m.get('shortName') or m['name'].lower()} can take {legendary_count(m)} legendary actions, choosing from the options below. "
            "Only one legendary action option can be used at a time and only at the end of another creature's turn. "
            "It regains spent legendary actions at the start of its turn."
        )
        lines.append(f"\n**Legendary Actions** — {header}")
        section("", m["legendary"])
    if m.get("mythic"):
        section("Mythic Actions", m["mythic"])
    if lair:
        if lair.get("lairActions"):
            lines.append("\n**Lair Actions**\n" + render_entries(lair["lairActions"], 1))
        if lair.get("regionalEffects"):
            lines.append("\n**Regional Effects**\n" + render_entries(lair["regionalEffects"], 1))
    if m.get("variant"):
        lines.append("\n" + render_entries(m["variant"], 1))
    for note in m.get("_notes", []):
        lines.append(f"\n_(RoboDM note: {note})_")
    return "\n".join(x for x in lines if x is not None)
