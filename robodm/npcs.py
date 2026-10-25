"""NPC records and minds: every named character is a person with a record and a mind of their own.

**Records** live beside the minds, one folder per person:
    campaigns/<slug>/npcs/<npc>/record.json     (companions: campaigns/<slug>/companions/<npc>/record.json)

A record holds who they are (race, alignment, role, appearance, voice, backstory, wants, fears, relationships —
possibly none —, the secrets they keep, and `dm_only` truths they don't know) and how they fight: either a
**stat block** from the rules data (`"commoner|MM"`, `commoner_mm`, a 5e.tools bestiary link, or `{ref, ac, hp,
notes}` with overrides) or a **compact character sheet**. Significant (`major`) NPCs always have a sheet.

A compact sheet stores only inputs (class and level or CR, ability scores, AC, HP, proficiencies, actions,
spellcasting); `compile_sheet` derives proficiency, saves, skills, to-hit, damage and the save DC and returns a
5etools-shaped creature, so the stat block renderer and `./dm combat add "<name>"` use it unchanged. A sheet can
also point at a full PC-style sheet file (`{"file": "banked/x.json"}`).

**Minds** reuse the companions' machinery (robodm/companions.py: state, memories, attitudes, recall, the shared
brief): `npcs/<npc>/state.json` and `memory.jsonl`. Every non-companion NPC is played by a generic subagent,
`npc-major` for major NPCs and `npc` for the rest. Their model is fixed (haiku; effort medium / low) and is not a
table setting. Companions keep their own agents.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote

from robodm import companions as co
from robodm.campaign import Campaign
from robodm.util import (ABILITIES, RoboDMError, ability_mod, fmt_mod, now_iso, read_json, slugify, truncate,
                         write_json, write_text)

TIERS = ("major", "minor", "background")
STATUSES = ("alive", "dead", "missing", "historical")
ALIGNMENTS = tuple(co.ALIGNMENT_GUIDE)
NPC_MODEL = "haiku"
AGENTS = {"npc-major": "medium", "npc": "low"}           # agent name → fixed effort (not a table setting)
SKILLS = {"acrobatics": "dex", "animal handling": "wis", "arcana": "int", "athletics": "str", "deception": "cha",
          "history": "int", "insight": "wis", "intimidation": "cha", "investigation": "int", "medicine": "wis",
          "nature": "int", "perception": "wis", "performance": "cha", "persuasion": "cha", "religion": "int",
          "sleight of hand": "dex", "stealth": "dex", "survival": "wis"}
ABILITY_NAMES = {"str": "Strength", "dex": "Dexterity", "con": "Constitution", "int": "Intelligence",
                 "wis": "Wisdom", "cha": "Charisma"}
SIZE_CODES = {"tiny": "T", "small": "S", "medium": "M", "large": "L", "huge": "H", "gargantuan": "G"}
# Words that open a name without being one ("Sister Ilsa", "Old Wenna Hollis", "Captain Marda Ironside").
TITLES = {"sister", "brother", "captain", "lord", "lady", "master", "mistress", "mother", "father", "old", "big",
          "young", "little", "sergeant", "reeve", "factor", "high", "sir", "dame", "the", "warden", "baron", "iron",
          "watch-captain", "lord-marshal", "king", "queen", "prince", "princess", "uncle", "aunt", "grandmother",
          "grandfather", "nan", "granny", "elder", "headman", "steward", "pyrewarden", "brother-captain"}


# =============================================================================== storage

def npcs_dir(camp: Campaign) -> Path:
    return camp.path("npcs")


def is_companion(camp: Campaign, slug: str) -> bool:
    return (co.companions_dir(camp) / slug / "persona.md").exists()


def record_path(camp: Campaign, slug: str) -> Path:
    if is_companion(camp, slug):
        return co.companions_dir(camp) / slug / "record.json"
    return npcs_dir(camp) / slug / "record.json"


def all_records(camp: Campaign) -> list[dict]:
    """Every record (NPCs and companions), each with `_kind` ("npc" | "companion")."""
    out = []
    for root, kind in ((npcs_dir(camp), "npc"), (co.companions_dir(camp), "companion")):
        if not root.exists():
            continue
        for p in sorted(root.glob("*/record.json")):
            rec = read_json(p)
            if isinstance(rec, dict) and rec.get("name"):
                rec.setdefault("slug", p.parent.name)
                rec["_kind"] = kind
                out.append(rec)
    return out


def load(camp: Campaign, slug: str) -> dict | None:
    p = record_path(camp, slug)
    rec = read_json(p) if p.exists() else None
    if rec:
        rec.setdefault("slug", slug)
        rec["_kind"] = "companion" if is_companion(camp, slug) else "npc"
    return rec


def _clean(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def first_name(name: str) -> str:
    words = [w for w in re.split(r"\s+", name.strip()) if w]
    for w in words:
        if w.lower().strip(",.") not in TITLES:
            return w.strip(",.")
    return words[0] if words else name


def name_keys(rec: dict) -> list[str]:
    """How the narration refers to them: full name, aliases, and the first non-title word (3+ letters)."""
    keys = [str(rec.get("name", ""))] + [str(a) for a in rec.get("aliases") or []]
    first = first_name(str(rec.get("name", "")))
    if len(first) >= 3:
        keys.append(first)
    return [k for k in dict.fromkeys(keys) if k]


def find_or_none(camp: Campaign, name: str) -> dict | None:
    if not name or not name.strip():
        return None
    recs = all_records(camp)
    slug = slugify(name)
    low = name.strip().lower()
    for test in (lambda r: r["slug"] == slug,
                 lambda r: str(r.get("name", "")).lower() == low,
                 lambda r: low in [str(a).lower() for a in r.get("aliases") or []],
                 lambda r: r.get("lore") == slug):
        hits = [r for r in recs if test(r)]
        if len(hits) == 1:
            return hits[0]
    hits = [r for r in recs if first_name(str(r.get("name", ""))).lower() == low]
    if len(hits) == 1:
        return hits[0]
    hits = [r for r in recs if r["slug"].startswith(slug)]
    return hits[0] if len(hits) == 1 else None


def find(camp: Campaign, name: str) -> dict:
    rec = find_or_none(camp, name)
    if rec is None:
        raise RoboDMError(f"No NPC record for '{name}'. `./dm npc check` lists who's missing; create one with "
                          "`./dm npc add \"Name\" …` (minor) or the npc-creator agent (significant).")
    return rec


def who_name(camp: Campaign, who: str) -> str | None:
    """The name of a relationship's `who`: another record, or a player character. None if neither exists."""
    rec = find_or_none(camp, who)
    if rec:
        return rec["name"]
    pc = camp.find_pc(who) if who else None
    return pc["name"] if pc and pc["slug"] == slugify(who) else None


def mind_agent(camp: Campaign, rec: dict) -> str:
    """The subagent that plays this person."""
    if rec.get("_kind") == "companion" or is_companion(camp, rec["slug"]):
        return co.agent_name(camp, rec["slug"])
    return "npc-major" if rec.get("tier") == "major" else "npc"


# =============================================================================== stat blocks and links

def statblock_ref(sb) -> tuple[str, str | None, dict]:
    """(name, source or None, overrides) from any of the accepted spellings."""
    over: dict = {}
    if isinstance(sb, dict):
        over = {k: v for k, v in sb.items() if k not in ("ref", "name")}
        if sb.get("name") and not sb.get("ref"):
            sb = sb["name"]
        else:
            if sb.get("name"):
                over["name"] = sb["name"]
            sb = sb.get("ref", "")
    s = unquote(str(sb or "")).strip()
    if "#" in s:
        s = s.rsplit("#", 1)[1]
    m = re.fullmatch(r"(.+?)\s*\(([A-Za-z0-9]+)\)", s)
    if m:
        return m.group(1).strip(), m.group(2).upper(), over
    if "|" in s:
        name, src = s.split("|", 1)
        return name.strip(), (src.strip().upper() or None), over
    m = re.fullmatch(r"(.+)_([a-z0-9]+)", s)
    if m and " " not in m.group(2):
        return m.group(1).strip(), m.group(2).upper(), over
    return s, None, over


def statblock_url(name: str, source: str) -> str:
    """The 5e.tools bestiary page for a creature, e.g. http://localhost:5050/bestiary.html#commoner_mm."""
    base = os.environ.get("ROBODM_LOCAL_5ETOOLS", "http://localhost:5050").rstrip("/")
    return f"{base}/bestiary.html#{quote(name.lower(), safe=chr(39) + '()!*~')}_{source.lower()}"


def _db():
    try:
        from robodm.rules import default_db

        db = default_db()
        return db if getattr(db, "available", True) else None
    except Exception:
        return None


def _lookup_monster(db, name: str, source: str | None):
    if db is None:
        return None
    rec = db.find_exact(name, "monster", source) if source else db.find_exact(name, "monster")
    return rec


# =============================================================================== compact sheets

def total_level(sheet: dict) -> int:
    return sum(int(n) for n in re.findall(r"(\d+)", str(sheet.get("class") or "")))


def prof_bonus(sheet: dict) -> int:
    lv = total_level(sheet)
    if lv:
        return 2 + (max(lv, 1) - 1) // 4
    from robodm.rules.index import cr_to_float

    cr = cr_to_float(sheet.get("cr"))
    return max(2, (int(max(cr or 0, 1)) - 1) // 4 + 2)


def _mods(sheet: dict) -> dict[str, int]:
    ab = sheet.get("abilities") or {}
    return {a: ability_mod(int(ab.get(a, 10))) for a in ABILITIES}


def _dmg_split(expr: str) -> tuple[str, str]:
    m = re.match(r"\s*([0-9d+\-\s]+?)\s+([a-z]+)\s*$", str(expr or ""), re.IGNORECASE)
    if m:
        return m.group(1).replace(" ", ""), m.group(2).lower()
    return str(expr or "").replace(" ", ""), ""


def _with_mod(dice: str, mod: int) -> str:
    return dice if not mod else f"{dice}{'+' if mod > 0 else '-'}{abs(mod)}"


def _avg(expr: str) -> int:
    from robodm import dice

    try:
        return int(dice.average(expr))
    except Exception:
        return 0


def _attack_ability(act: dict, mods: dict, sheet: dict) -> str:
    a = str(act.get("attack", "str")).lower()
    if a == "finesse":
        return "str" if mods["str"] >= mods["dex"] else "dex"
    if a == "spell":
        return str((sheet.get("spellcasting") or {}).get("ability", "int")).lower()[:3]
    return a[:3]


def _compile_action(act: dict, sheet: dict, mods: dict, pb: int) -> dict:
    name = str(act.get("name", "Action"))
    parts = []
    if act.get("attack") is not None:
        fixed = isinstance(act["attack"], int) or str(act["attack"]).lstrip("+-").isdigit()
        ab = None if fixed else _attack_ability(act, mods, sheet)
        magic = int(act.get("magic", 0))           # a +N weapon: to-hit and damage; `bonus`: to-hit only
        to_hit = int(act["attack"]) if fixed else mods.get(ab, 0) + pb + magic + int(act.get("bonus", 0))
        spell = str(act.get("attack")).lower() == "spell"
        ranged = bool(act.get("range")) and not act.get("reach")
        tag = ("rs" if ranged else "ms") if spell else ("rw" if ranged else "mw")
        if act.get("reach") and act.get("range"):
            tag = "mw,rw"
        reach = f"reach {act['reach']} ft." if act.get("reach") else ""
        rng = f"range {act['range']} ft." if act.get("range") else ""
        where = " or ".join(x for x in (reach, rng) if x) or "reach 5 ft."
        txt = f"{{@atk {tag}}} {{@hit {to_hit}}} to hit, {where}, {act.get('target', 'one target')}."
        if act.get("damage"):
            dice, dtype = _dmg_split(act["damage"])
            add = 0 if fixed or act.get("add_mod") is False else mods.get(ab, 0)
            add += (0 if fixed else magic) + int(act.get("damage_bonus", 0))
            expr = _with_mod(dice, add)
            txt += f" {{@h}}{_avg(expr)} ({{@damage {expr}}}) {dtype} damage".rstrip()
            for extra in act.get("extra_damage") or []:
                d2, t2 = _dmg_split(extra)
                txt += f" plus {_avg(d2)} ({{@damage {d2}}}) {t2} damage"
            txt += "."
        parts.append(txt)
    sv = act.get("save")
    if isinstance(sv, dict):
        ab = str(sv.get("ability", "dex")).lower()[:3]
        dc = sv.get("dc", "spell")
        if dc == "spell":
            sab = str((sheet.get("spellcasting") or {}).get("ability", "int")).lower()[:3]
            dc = 8 + pb + mods.get(sab, 0)
        txt = f"{sv.get('who', 'Each creature in the area')} must make a {{@dc {int(dc)}}} {ABILITY_NAMES.get(ab, ab)} saving throw"
        if sv.get("damage"):
            dice, dtype = _dmg_split(sv["damage"])
            txt += f", taking {_avg(dice)} ({{@damage {dice}}}) {dtype} damage on a failed save"
            txt += ", or half as much damage on a successful one." if sv.get("half", True) else "."
        else:
            txt += f". {sv.get('effect', '')}".rstrip()
        parts.append(txt)
    if act.get("text"):
        parts.append(str(act["text"]))
    return {"name": name, "entries": [" ".join(parts) or "—"]}


def compile_sheet(rec: dict) -> dict:
    """A 5etools-shaped creature from a compact sheet (inputs only; the numbers are derived here)."""
    sheet = rec.get("sheet") or {}
    if sheet.get("file"):
        raise RoboDMError("This sheet is a file; use `sheet_file`.")
    mods = _mods(sheet)
    pb = prof_bonus(sheet)
    ab = sheet.get("abilities") or {}
    m: dict = {"name": rec.get("name", "NPC"), "source": "NPC",
               "size": [SIZE_CODES.get(str(sheet.get("size", "medium")).lower(), "M")],
               "type": str(sheet.get("type") or f"humanoid ({rec.get('race', 'any race')})"),
               "alignment": [{"special": rec.get("alignment", "")}] if rec.get("alignment") else [],
               "ac": [{"ac": int(sheet.get("ac", 10)), "from": [sheet["ac_from"]]}] if sheet.get("ac_from")
               else [int(sheet.get("ac", 10))],
               "hp": {"average": int(sheet.get("hp", 1)), "formula": str(sheet.get("hit_dice", ""))},
               "speed": sheet.get("speed") if isinstance(sheet.get("speed"), dict) else {"walk": int(sheet.get("speed", 30))}}
    for a in ABILITIES:
        m[a] = int(ab.get(a, 10))
    saves = [str(s).lower()[:3] for s in sheet.get("saves") or []]
    if saves:
        m["save"] = {a: fmt_mod(mods[a] + pb) for a in ABILITIES if a in saves}
    skills = {}
    for sk, mult in (sheet.get("skills") or {}).items():
        key = str(sk).lower()
        abl = SKILLS.get(key)
        if abl:
            skills[key] = fmt_mod(mods[abl] + pb * int(mult or 1))
    if skills:
        m["skill"] = skills
    perc = int(str(skills.get("perception", fmt_mod(mods["wis"]))).replace("+", ""))
    m["passive"] = 10 + perc
    m["senses"] = list(sheet.get("senses") or [])
    m["languages"] = list(sheet.get("languages") or [])
    for key, field in (("resist", "resist"), ("immune", "immune"), ("vulnerable", "vulnerable"),
                       ("condition_immune", "conditionImmune")):
        if sheet.get(key):
            m[field] = list(sheet[key])
    if sheet.get("cr") is not None:
        m["cr"] = str(sheet["cr"])
    m["trait"] = [{"name": t.get("name", "Trait"), "entries": [t.get("text", "")]} for t in sheet.get("traits") or []]
    for key, field in (("actions", "action"), ("bonus_actions", "bonus"), ("reactions", "reaction")):
        acts = [_compile_action(a, sheet, mods, pb) for a in sheet.get(key) or [] if isinstance(a, dict)]
        if acts:
            m[field] = acts
    sc = sheet.get("spellcasting")
    if isinstance(sc, dict) and sc.get("spells"):
        sab = str(sc.get("ability", "int")).lower()[:3]
        dc, hit = 8 + pb + mods.get(sab, 0), pb + mods.get(sab, 0)
        spells = {}
        for lvl, names in (sc.get("spells") or {}).items():
            entry = {"spells": [f"{{@spell {n}}}" for n in names]}
            if str(lvl) != "0":
                entry["slots"] = int((sc.get("slots") or {}).get(str(lvl), 0))
            spells[str(lvl)] = entry
        m["spellcasting"] = [{"name": "Spellcasting", "headerEntries": [
            f"Their spellcasting ability is {ABILITY_NAMES.get(sab, sab)} (spell save {{@dc {dc}}}, {{@hit {hit}}} to hit "
            f"with spell attacks). They have the following spells prepared:"], "spells": spells}]
    m["_notes"] = [f"NPC character sheet: {sheet.get('class') or 'CR ' + str(sheet.get('cr', '?'))} · proficiency "
                   f"{fmt_mod(pb)}" + (f" · {sheet['background']}" if sheet.get("background") else "")]
    return m


def sheet_file(camp: Campaign, rec: dict) -> dict | None:
    f = (rec.get("sheet") or {}).get("file")
    if not f:
        return None
    p = camp.path(*Path(f).parts)
    data = read_json(p) if p.exists() else None
    if not isinstance(data, dict):
        raise RoboDMError(f"{rec.get('name')}: sheet file {f} is missing or unreadable.")
    return data


def creature(camp: Campaign, rec: dict, db=None) -> dict | None:
    """The 5etools-shaped creature this NPC fights as (stat block with overrides, or the compiled sheet)."""
    if rec.get("sheet") and not rec["sheet"].get("file"):
        return compile_sheet(rec)
    if rec.get("statblock"):
        name, src, over = statblock_ref(rec["statblock"])
        db = db or _db()
        hit = _lookup_monster(db, name, src)
        if not hit:
            return None
        m = dict(db.get(hit))
        if over.get("ac") is not None:
            m["ac"] = [int(over["ac"])]
        if over.get("hp") is not None:
            m["hp"] = {"average": int(over["hp"]), "formula": str(over["hp"])}
        return m
    return None


def statblock_text(camp: Campaign, rec: dict, db=None) -> str:
    from robodm import campaign as cmod
    from robodm.rules.statblock import format_monster

    lines = []
    if rec.get("sheet") and rec["sheet"].get("file"):
        pc = sheet_file(camp, rec)
        lines.append(f"Character sheet ({rec['sheet']['file']}):")
        lines.append(cmod.pc_summary(pc, full=True))
        return "\n".join(lines)
    m = creature(camp, rec, db)
    if m is None:
        return f"_(no stat block found for {rec.get('statblock') or 'this record'})_"
    text = format_monster(m)
    if rec.get("sheet"):
        sheet = rec["sheet"]
        text = re.sub(r"(?m)^\*\*CR\*\* \? \(0 XP\)$", f"**Level** {sheet.get('class') or '—'} · **PB** "
                      f"{fmt_mod(prof_bonus(sheet))}", text)
    else:
        name, src, over = statblock_ref(rec["statblock"])
        source = src or m.get("source", "MM")
        lines.append(f"Stat block: {m.get('name')} ({source}) — {statblock_url(str(m.get('name')), str(source))}")
        if over:
            lines.append("Overrides: " + ", ".join(f"{k} {v}" for k, v in over.items()))
    lines.append(text)
    return "\n".join(lines)


def add_to_combat(camp: Campaign, enc, rec: dict, side: str = "enemy", init: int | None = None, db=None) -> list[dict]:
    """Put this NPC in the tracker, named as themselves, from their sheet or stat block."""
    from robodm import campaign as cmod
    from robodm.rules.statblock import combat_profile

    if rec.get("sheet") and rec["sheet"].get("file"):
        pc = sheet_file(camp, rec)
        cid = enc._unique_id(str(rec["name"]).split()[0])
        c = enc._new(cid, rec["name"], kind="npc", side=side, init=init, init_bonus=cmod.pc_init_bonus(pc),
                     dex=pc["abilities"].get("dex", 10), ac=pc.get("ac", 10), hp=pc["hp"]["max"], max_hp=pc["hp"]["max"],
                     saves={ab: cmod.pc_save_bonus(pc, ab) for ab in ABILITIES}, attacks=pc.get("attacks", []),
                     size=pc.get("size", "M"), speed=pc.get("speed", 30), resist=pc.get("resist", []),
                     immune=pc.get("immune", []), vulnerable=pc.get("vulnerable", []),
                     ref={"name": rec["name"], "source": "NPC", "cr": None})
        enc.say(f"Added **{rec['name']}** (NPC sheet) — HP {c['hp']}/{c['max_hp']}, AC {c['ac']}, "
                f"init {fmt_mod(c['init_bonus'])}")
        return [c]
    m = creature(camp, rec, db)
    if m is None:
        raise RoboDMError(f"{rec.get('name')} has no usable stat block or sheet (`./dm npc show {rec['slug']}`).")
    profile = dict(combat_profile(m), name=rec["name"])      # tracked as themselves, not "Commoner 3"
    return enc.add_monster(profile, side=side, init=init)


# =============================================================================== validation

def validate(camp: Campaign, rec: dict, db=None) -> tuple[list[str], list[str]]:
    """(problems that block saving, warnings)."""
    problems, warnings = [], []
    db = db if db is not None else _db()
    for k in ("name", "race", "alignment", "backstory"):
        if not str(rec.get(k) or "").strip():
            problems.append(f"missing {k}")
    if rec.get("alignment") and str(rec["alignment"]).lower() not in ALIGNMENTS:
        problems.append(f"alignment '{rec['alignment']}' must be one of: {', '.join(ALIGNMENTS)}")
    if rec.get("tier", "minor") not in TIERS:
        problems.append(f"tier must be one of: {', '.join(TIERS)}")
    if rec.get("status", "alive") not in STATUSES:
        problems.append(f"status must be one of: {', '.join(STATUSES)}")
    rels = rec.get("relationships")
    if not isinstance(rels, list):
        problems.append("relationships must be a list (empty if they have none)")
    else:
        for r in rels:
            if not isinstance(r, dict) or not r.get("who") or not r.get("how"):
                problems.append(f"relationship {r!r} needs who and how")
            elif not who_name(camp, str(r["who"])) and slugify(str(r["who"])) != slugify(str(rec.get("name", ""))):
                warnings.append(f"{r['who']} has no record yet")
    for k in ("secrets", "dm_only", "aliases"):
        if rec.get(k) is not None and not isinstance(rec[k], list):
            problems.append(f"{k} must be a list")
    sheet, sb = rec.get("sheet"), rec.get("statblock")
    if not sheet and not sb:
        problems.append("needs a stat block (`\"statblock\": \"commoner|MM\"`) or a character sheet")
    if rec.get("tier") == "major" and not sheet:
        problems.append("major NPCs always have a character sheet")
    if sheet and sb:
        warnings.append("has both a sheet and a stat block: the sheet is used")
    if sb and not sheet:
        name, src, _ = statblock_ref(sb)
        if db is not None and not _lookup_monster(db, name, src):
            problems.append(f"no stat block '{name}'{' (' + src + ')' if src else ''} in the rules data")
    if isinstance(sheet, dict):
        problems.extend(_sheet_problems(camp, rec, sheet, db))
    elif sheet is not None:
        problems.append("sheet must be an object")
    if db is not None and rec.get("race") and not rec.get("homebrew"):
        race = re.sub(r"\s*\(.*\)$", "", str(rec["race"])).strip()
        if not db.find_exact(race, "race") and not db.find_exact(race, "monster"):
            warnings.append(f"race '{rec['race']}' isn't in the rules data (fine for homebrew; set \"homebrew\": true)")
    return problems, warnings


def _sheet_problems(camp: Campaign, rec: dict, sheet: dict, db) -> list[str]:
    from robodm import dice

    out = []
    if sheet.get("file"):
        try:
            sheet_file(camp, rec)
        except RoboDMError as e:
            out.append(str(e))
        return out
    ab = sheet.get("abilities") or {}
    if set(ab) != set(ABILITIES) or not all(isinstance(v, int) and 1 <= v <= 30 for v in ab.values()):
        out.append("sheet.abilities needs str, dex, con, int, wis, cha (1–30)")
    for k in ("ac", "hp"):
        if not isinstance(sheet.get(k), int) or sheet[k] < 1:
            out.append(f"sheet.{k} must be a positive number")
    if not total_level(sheet) and sheet.get("cr") is None:
        out.append('sheet needs a "class" with levels (e.g. "Fighter 6") or a "cr"')
    if db is not None and sheet.get("class") and not (rec.get("homebrew") or sheet.get("homebrew")):
        for cls in re.findall(r"([A-Za-z][A-Za-z' -]*?)\s+\d+", str(sheet["class"])):
            if not db.find_exact(cls.strip(), "class"):
                out.append(f"class '{cls.strip()}' isn't in the rules data (set \"homebrew\": true if intended)")
    for s in sheet.get("saves") or []:
        if str(s).lower()[:3] not in ABILITIES:
            out.append(f"save '{s}' isn't an ability")
    for sk in sheet.get("skills") or {}:
        if str(sk).lower() not in SKILLS:
            out.append(f"skill '{sk}' isn't a 5e skill")
    for key in ("actions", "bonus_actions", "reactions"):
        for act in sheet.get(key) or []:
            if not isinstance(act, dict) or not act.get("name"):
                out.append(f"{key}: every entry needs a name")
                continue
            if act.get("damage"):
                d, _ = _dmg_split(act["damage"])
                try:
                    dice.parse(d)
                except Exception:
                    out.append(f"{act['name']}: damage '{act['damage']}' isn't a dice expression like \"1d8 slashing\"")
            a = act.get("attack")
            if a is not None and not (isinstance(a, int) or str(a).lstrip("+-").isdigit()) and \
                    str(a).lower() not in ("str", "dex", "con", "int", "wis", "cha", "finesse", "spell"):
                out.append(f"{act['name']}: attack must be str, dex, finesse, spell or a number")
    return out


# =============================================================================== saving

def save(camp: Campaign, rec: dict, force: bool = False) -> tuple[Path | None, list[str], list[str]]:
    rec = _clean(rec)
    if not rec.get("name"):
        return None, ["missing name"], []
    rec["slug"] = slugify(rec.get("slug") or rec["name"])
    rec.setdefault("tier", "minor")
    rec.setdefault("status", "alive")
    rec.setdefault("relationships", [])
    if not rec.get("lore"):                       # their lore entry, when there is one by that name
        from robodm import lore

        try:
            e = lore.find(camp, rec["slug"])
            if e["meta"].get("type") == "npc":
                rec["lore"] = e["slug"]
        except RoboDMError:
            pass
    problems, warnings = validate(camp, rec)
    if problems and not force:
        return None, problems, warnings
    p = record_path(camp, rec["slug"])
    old = read_json(p) if p.exists() else None
    rec["created"] = (old or {}).get("created") or now_iso()
    rec["updated"] = now_iso()
    write_json(p, rec)
    return p, problems, warnings


def save_text(camp: Campaign, text: str, force: bool = False) -> list[str]:
    """`./dm npc save -`: one record or a list, as JSON. Returns report lines."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise RoboDMError(f"Not valid JSON: {e}")
    recs = data if isinstance(data, list) else [data]
    lines = []
    for rec in recs:
        if not isinstance(rec, dict):
            lines.append(f"✘ not a record: {truncate(str(rec), 60)}")
            continue
        p, problems, warnings = save(camp, rec, force)
        if p is None:
            lines.append(f"✘ {rec.get('name', '?')} not saved: " + "; ".join(problems))
        else:
            saved = load(camp, slugify(rec.get("slug") or rec["name"]))
            lines.append(f"✔ {saved['name']} ({saved.get('tier')}, {stats_line(camp, saved)})"
                         + (" · ⚠ saved despite: " + "; ".join(problems) if problems else ""))
        lines.extend(f"   ⚠ {w}" for w in warnings)
    return lines


def stats_line(camp: Campaign, rec: dict) -> str:
    sheet = rec.get("sheet")
    if sheet and sheet.get("file"):
        try:
            pc = sheet_file(camp, rec)
            return f"sheet {sheet['file']}: {pc.get('class')} {pc.get('level')}, AC {pc.get('ac')}, HP {pc['hp']['max']}"
        except RoboDMError as e:
            return str(e)
    if sheet:
        try:
            from robodm.rules.statblock import combat_profile

            prof = combat_profile(compile_sheet(rec))
            atk = ", ".join(f"{a['name']} {fmt_mod(a['to_hit'])}" for a in prof["attacks"] if "to_hit" in a)[:80]
            return (f"sheet: {sheet.get('class') or 'CR ' + str(sheet.get('cr'))}, AC {prof['ac']}, HP {prof['hp']}"
                    + (f", {atk}" if atk else ""))
        except Exception as e:  # a half-written sheet still shows up in check
            return f"sheet (can't compile: {e})"
    if rec.get("statblock"):
        name, src, over = statblock_ref(rec["statblock"])
        return f"stat block {name}{' (' + src + ')' if src else ''}" + (f" with {', '.join(over)}" if over else "")
    return "no stats"


def add_quick(camp: Campaign, name: str, **kw) -> tuple[Path | None, list[str], list[str]]:
    """`./dm npc add`: a minor NPC from flags."""
    rec = {"name": name.strip(), "tier": kw.get("tier") or "minor", "status": kw.get("status") or "alive"}
    for k in ("race", "alignment", "role", "age", "appearance", "voice", "backstory", "wants", "fears", "lore"):
        if kw.get(k):
            rec[k] = kw[k]
    if kw.get("statblock"):
        rec["statblock"] = kw["statblock"]
    rels = []
    for r in kw.get("rel") or []:
        who, _, how = str(r).partition(":")
        if who.strip() and how.strip():
            rels.append({"who": slugify(who), "how": how.strip()})
    rec["relationships"] = rels
    if not rec.get("lore"):
        from robodm import lore

        try:
            rec["lore"] = lore.find(camp, name)["slug"]
        except RoboDMError:
            pass
    return save(camp, rec, force=bool(kw.get("force")))


def template(major: bool = False) -> dict:
    base = {"name": "Full Name", "aliases": [], "tier": "major" if major else "minor", "status": "alive",
            "race": "human", "alignment": "lawful neutral", "role": "what they do and where",
            "age": "40s", "appearance": "one or two telling details", "voice": "how they talk; a verbal habit",
            "backstory": "3–6 sentences: where they come from and what has happened to them (canon first)",
            "wants": "…", "fears": "…",
            "relationships": [{"who": "other-npc-slug", "how": "old friend; owes them a debt", "attitude": 1,
                               "hidden": False}],
            "secrets": ["things they know and keep"], "dm_only": ["truths about them they don't know; invented details"],
            "lore": "lore-entry-slug or null"}
    if major:
        base["sheet"] = {
            "class": "Fighter 6", "background": "Soldier", "size": "medium", "type": "humanoid (human)",
            "ac": 18, "ac_from": "plate", "hp": 52, "hit_dice": "6d10+12", "speed": 30,
            "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 13, "cha": 11},
            "saves": ["str", "con"], "skills": {"athletics": 1, "perception": 1}, "senses": [],
            "languages": ["Common"], "traits": [{"name": "Second Wind", "text": "Once per short rest, regain 1d10+6 HP."}],
            "actions": [{"name": "Multiattack", "text": "Two longsword attacks."},
                        {"name": "Longsword", "attack": "str", "reach": 5, "damage": "1d8 slashing"},
                        {"name": "Heavy Crossbow", "attack": "dex", "range": "100/400", "damage": "1d10 piercing"}],
            "reactions": [], "gear": ["plate", "longsword", "heavy crossbow"]}
    else:
        base["statblock"] = "Commoner|MM"
    return base


# =============================================================================== showing

def show(camp: Campaign, rec: dict, dm: bool = True, stats: bool = True) -> str:
    st = co.state(camp, rec["slug"])
    lines = [f"# {rec['name']}  ({rec.get('tier', 'minor')} · {rec.get('status', 'alive')})"]
    ident = [rec.get("race", "?")] + ([rec["alignment"]] if dm and rec.get("alignment") else []) + \
        [x for x in (rec.get("role"), rec.get("age")) if x]
    lines.append(" · ".join(str(x) for x in ident))
    for k, label in (("appearance", "Looks"), ("voice", "Voice")):
        if rec.get(k):
            lines.append(f"**{label}:** {rec[k]}")
    if dm:
        lines.append(f"\n{rec.get('backstory', '')}")
        for k, label in (("wants", "Wants"), ("fears", "Fears")):
            if rec.get(k):
                lines.append(f"**{label}:** {rec[k]}")
    rels = [r for r in rec.get("relationships") or [] if dm or not r.get("hidden")]
    if rels:
        lines.append("\n**Relationships**")
        for r in rels:
            who = who_name(camp, str(r["who"])) or str(r["who"])
            att = f" ({int(r['attitude']):+d})" if dm and r.get("attitude") is not None else ""
            lines.append(f"- {who}: {r['how']}{att}" + (" _(secret)_" if r.get("hidden") else ""))
    if dm:
        if rec.get("secrets"):
            lines.append("\n**Secrets they keep**\n" + "\n".join(f"- {s}" for s in rec["secrets"]))
        if rec.get("dm_only"):
            lines.append("\n**DM only**\n" + "\n".join(f"- {s}" for s in rec["dm_only"]))
        mind = f"\n**Mind:** `{mind_agent(camp, rec)}` · consulted {st.get('consults', 0)}×"
        if st["attitude"]:
            mind += " · " + ", ".join(f"{k.replace('-', ' ').title()} {v['score']:+d}" for k, v in st["attitude"].items())
        lines.append(mind)
        if rec.get("lore"):
            lines.append(f"**Lore:** `./dm lore show {rec['lore']}`")
    if stats:
        lines.append(f"\n**Stats:** {stats_line(camp, rec)}")
        if rec.get("statblock") and not rec.get("sheet"):
            name, src, _ = statblock_ref(rec["statblock"])
            m = creature(camp, rec)
            if m:
                lines.append(statblock_url(str(m.get("name")), str(src or m.get("source"))))
    return "\n".join(lines)


def listing(camp: Campaign, tier: str | None = None) -> list[str]:
    out = []
    for rec in sorted(all_records(camp), key=lambda r: (TIERS.index(r.get("tier", "minor")) if r.get("tier") in TIERS else 9,
                                                        r["name"])):
        if tier and rec.get("tier") != tier:
            continue
        mark = "🧭" if rec["_kind"] == "companion" else {"major": "★", "minor": "·", "background": "·"}.get(rec.get("tier"), "·")
        out.append(f"{mark} **{rec['name']}** ({rec.get('tier')}, {rec.get('status', 'alive')}) — {rec.get('race')} · "
                   f"{truncate(str(rec.get('role') or ''), 60)} · {stats_line(camp, rec)}")
    return out


def check(camp: Campaign) -> dict:
    """Who is missing a record, and which records have problems."""
    from robodm import lore, world

    recs = all_records(camp)
    have = {r["slug"] for r in recs} | {r.get("lore") for r in recs if r.get("lore")}
    names = {str(r.get("name", "")).lower() for r in recs}
    missing: dict[str, str] = {}
    for e in lore.all_entries(camp):
        if e["meta"].get("type") == "npc" and e["slug"] not in have and str(e["meta"].get("name", "")).lower() not in names:
            missing[e["slug"]] = f"{e['meta'].get('name')} (lore entry)"
    for slug in co.all_slugs(camp):
        if slug not in have:
            meta, _ = co.load_persona(camp, slug)
            missing[slug] = f"{meta.get('name', slug)} (companion)"
    for slug, name in world.scene_cast(camp):
        if slug not in have and str(name).lower() not in names:
            missing.setdefault(slug, f"{name} (in the scene)")
    for r in recs:
        for rel in r.get("relationships") or []:
            if isinstance(rel, dict) and rel.get("who") and not who_name(camp, str(rel["who"])):
                missing.setdefault(slugify(str(rel["who"])), f"{rel['who']} (named in {r['name']}'s relationships)")
    db = _db()
    invalid = {}
    for r in recs:
        problems, _ = validate(camp, r, db)
        if problems:
            invalid[r["slug"]] = problems
    return {"records": len(recs), "missing": missing, "invalid": invalid}


# =============================================================================== the mind

def brief(camp: Campaign, slug: str, situation: str = "", combat: bool = False, record: bool = True) -> str:
    """Everything this NPC's mind sees for one consultation (never their `dm_only`)."""
    from robodm import lore, world

    rec = find(camp, slug)
    if rec["_kind"] == "companion":
        return co.brief(camp, rec["slug"], situation=situation, combat=combat, record=record)
    slug = rec["slug"]
    st = co.state(camp, slug)
    name = rec["name"]
    lines = [f"# You are {name}"]
    if rec.get("status") in ("dead", "historical"):
        lines.append(f"_(You are {rec['status']}. The DM is calling on you as you were (a memory, a vision, a "
                     "story told): answer only as asked.)_")
    ident = [x for x in (rec.get("alignment", "").title() if rec.get("alignment") else "", rec.get("race"),
                         rec.get("role"), rec.get("age")) if x]
    lines.append("*" + " · ".join(str(x) for x in ident) + "*" + (f" · voice: {rec['voice']}" if rec.get("voice") else ""))
    lines.append(f"\n## Who you are\n{rec.get('backstory', '')}")
    if rec.get("appearance"):
        lines.append(f"- You look: {rec['appearance']}")
    for k, label in (("wants", "You want"), ("fears", "You fear")):
        if rec.get(k):
            lines.append(f"- {label}: {rec[k]}")
    if rec.get("secrets"):
        lines.append("\n## Your secrets (yours to keep or reveal)\n" + "\n".join(f"- {s}" for s in rec["secrets"]))
    rels = rec.get("relationships") or []
    if rels:
        lines.append("\n## The people in your life")
        for r in rels:
            who = who_name(camp, str(r["who"])) or str(r["who"]).replace("-", " ").title()
            lines.append(f"- {who}: {r['how']}" + (" (nobody else knows)" if r.get("hidden") else ""))
    guide = co.ALIGNMENT_GUIDE.get(str(rec.get("alignment", "")).lower())
    if guide:
        lines.append(f"\n## How your alignment tends to colour choices\n{guide} (A tendency, not a script: who you "
                     "are comes first.)")
    if rec.get("lore"):
        try:
            e = lore.find(camp, rec["lore"])
            pub = co._strip_secrets(e["body"])
            if pub:
                lines.append(f"\n## What the world says about you (public lore)\n{truncate(pub, 1500)}")
        except RoboDMError:
            pass
    lines.append(f"\n## What you can do\n{stats_line(camp, rec)}")
    here = slug in dict(world.scene_cast(camp))
    lines.append("\n## Where things stand")
    lines.append("- You are in the current scene." if here else "- You are not in the current scene (the DM is bringing "
                 "you in, or asking what you'd do from where you are).")
    lines.extend(co.shared_lines(camp, slug, st, here, situation, combat, "npc", slug))
    if record:
        co.mark_consulted(camp, slug, st)
    return "\n".join(lines)


def remember(camp: Campaign, slug: str, text: str, about: str = "") -> dict:
    rec = find(camp, slug)
    return co.remember(camp, rec["slug"], text, about)


def attitude(camp: Campaign, slug: str, who: str, change: str, why: str = "") -> dict:
    rec = find(camp, slug)
    return co.attitude(camp, rec["slug"], who, change, why)


def recall(camp: Campaign, slug: str, query: str, limit: int = 8) -> list[str]:
    rec = find(camp, slug)
    return co.recall(camp, rec["slug"], query, limit)


# =============================================================================== the generic NPC agents

NPC_PROTOCOL = """\
You are **one named non-player character** in a D&D 5e (2014) campaign run by RoboDM: whoever the DM names in its
message (their slug, `<npc>` below, e.g. `halda-barrowe`; maybe a campaign, `-c <campaign>`: add it to every
command). You are a full person with your own wants, fears, loyalties and ways of talking, not a prop. The Dungeon
Master (the main Claude) consults you **every time you speak or decide something**, so answer for this moment only.

## Every time
1. Run `./dm npc brief <npc>` (add `--combat` during a fight). It is the ONLY source of who you are and what you
   know. Read all of it, including the DM's description of the moment. Run every command exactly as written, from
   the folder you start in: no `cd`, no full paths, nothing piped or chained.
2. **Stay inside your knowledge.** You know your record, your own secrets, your memories, public lore, and what
   happened in front of you. You don't know DM secrets, other people's thoughts, or what happened where you weren't.
   If you're unsure whether you'd know something, you don't. More of your memories: `./dm npc recall <npc> "words"`.
3. **Decide as yourself.** Weigh what you want, fear and owe against the moment. You may help, refuse, lie,
   bargain, stall, threaten or tell the truth: whatever this person would really do. Don't be helpful just because
   the party asks, and don't be hostile just for drama.
4. **Never decide for a player character**: not their actions, words or feelings.
5. **In combat**, propose concrete `./dm combat …` commands with your own actions, but don't run them: the DM runs
   them through the rules gate.
6. **Remember** what you'll carry from this beat, first person, a line: `./dm npc remember <npc> "…"`. When how you
   feel about someone changes: `./dm npc attitude <npc> <who> +1 --why "…"`.
7. You can't run anything else. The hooks block dice, game changes and file access; the DM does all of that.

## Answer (to the DM, not the players), in this shape
INTENT: what you want right now, in one line
DOES: what you do this beat; in combat, the exact `./dm combat …` commands to run
SAYS: your words, in your voice (or "—"); what you'd really say now, usually a line or a few
HIDES: anything you're deliberately not saying (DM's eyes only), or "—"
FRICTION: 0–3, how far this goes against what the player characters want, and why, in one line
"""


def agent_text(name: str) -> str:
    effort = AGENTS[name]
    who = "a **major** NPC (a significant story character)" if name == "npc-major" else \
        "a minor or background NPC"
    desc = (f"Plays {who.replace('**', '')} in RoboDM with their own mind (wants, fears, secrets, voice), limited to what "
            f"they know. Use every time that NPC speaks or decides; tell it their slug and the moment from their point "
            "of view. " + ("Minor and background NPCs use the `npc` agent." if name == "npc-major" else
                           "Major NPCs use `npc-major`; companions use their own agents."))
    return (f"---\nname: {name}\ndescription: {desc}\ntools: Bash\nmodel: {NPC_MODEL}\neffort: {effort}\n---\n\n"
            f"<!-- generated by `./dm npc agents` · edit robodm/npcs.py, not this file -->\n"
            f"You play {who}.\n\n" + NPC_PROTOCOL)


def write_agents() -> list[Path]:
    root = co.agents_dir()
    out = []
    for name in AGENTS:
        p = root / f"{name}.md"
        write_text(p, agent_text(name))
        out.append(p)
    return out
