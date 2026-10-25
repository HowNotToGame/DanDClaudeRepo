"""NPCs: every named character in a campaign gets a record, and can think for themselves through a subagent.

One compact JSON file per character in campaigns/<slug>/npcs/<npc>.json:

    name, race, alignment, backstory, relationships   always (relationships may be an empty list)
    statblock   a creature from the rules data for minor NPCs: "commoner|mm" (also commoner_mm, a 5e.tools
                bestiary link, or {"ref": "veteran|mm", "ac": 17, "notes": "…"} with overrides)
    sheet       a character sheet for significant NPCs (tier "major"): a compact stat block whose derived numbers
                (proficiency, saves, skills, to-hit, damage) are computed here, see `compile_sheet`; or
                {"file": "banked/hesby-bramble.json"} for a full PC-style sheet kept elsewhere
    tier        "major" (a significant story NPC: needs a sheet) or "minor"
    lore        the lore entry about them, if any
    role, age, appearance, voice, wants, fears          optional colour
    secrets     what THEY know and keep (their mind sees these)
    dm_only     truths about them they don't know, and mechanics (their mind never sees these)
    status      alive | dead | missing | unknown; the dead and historical need no statblock or sheet
    mind        attitudes toward others, consults (kept by the engine)

Their first-person memories live in npcs/memory.jsonl ({"npc": slug, "text": …}).

Minds: the generic `npc` subagent plays any NPC from `./dm npc brief <npc>` (knowledge-limited like a companion's:
their record minus `dm_only`, their memories, what's tagged `known_by` them, public lore, the scene). Its model is
fixed (NPC_MODEL / NPC_EFFORT), not a table setting. Companions keep their own personas and agents: for a companion,
`npc brief` hands over to the companion brief. The `npc-creator` subagent writes records (`./dm npc import`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from robodm.campaign import Campaign
from robodm.util import (ABILITIES, SKILLS, RoboDMError, ability_mod, append_jsonl, fmt_mod, now_iso,
                         proficiency_bonus, rank_documents, read_json, read_jsonl, slugify, truncate, write_json,
                         write_text)

NPC_MODEL = "haiku"          # every NPC mind: cheap and quick; companions have their own (configurable) agents
NPC_EFFORT = "medium"
TIERS = ("major", "minor")
STATUSES = ("alive", "dead", "missing", "unknown")
ALIGNMENTS = ("lawful good", "neutral good", "chaotic good", "lawful neutral", "neutral", "chaotic neutral",
              "lawful evil", "neutral evil", "chaotic evil", "unaligned", "any")
FIELDS = ("name", "slug", "tier", "status", "race", "alignment", "role", "age", "appearance", "voice", "backstory",
          "wants", "fears", "secrets", "dm_only", "relationships", "statblock", "sheet", "lore", "mind", "created",
          "updated")
_ALIGN_ABBR = {"lawful": "L", "chaotic": "C", "good": "G", "evil": "E", "neutral": "N", "unaligned": "U", "any": "A"}
SIZES = {"tiny": "T", "small": "S", "medium": "M", "large": "L", "huge": "H", "gargantuan": "G"}


# =============================================================================== storage

def npcs_dir(camp: Campaign) -> Path:
    return camp.path("npcs")


def _path(camp: Campaign, slug: str) -> Path:
    return npcs_dir(camp) / f"{slug}.json"


def all_slugs(camp: Campaign) -> list[str]:
    d = npcs_dir(camp)
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


def load(camp: Campaign, slug: str) -> dict:
    rec = read_json(_path(camp, slug))
    if not rec:
        raise RoboDMError(f"No NPC record '{slug}'.")
    rec.setdefault("slug", slug)
    return rec


def all_records(camp: Campaign) -> list[dict]:
    return [load(camp, s) for s in all_slugs(camp)]


def find(camp: Campaign, name: str) -> str:
    """The slug of the NPC called `name` (slug, full name, alias or unique prefix/first name)."""
    slug = slugify(name)
    slugs = all_slugs(camp)
    if slug in slugs:
        return slug
    for s in slugs:
        rec = load(camp, s)
        names = [rec.get("name", "")] + list(rec.get("aliases") or [])
        if any(slugify(n) == slug for n in names):
            return s
    pref = [s for s in slugs if s.startswith(slug + "-") or s.startswith(slug)] or [s for s in slugs if slug in s]
    if len(pref) == 1:
        return pref[0]
    raise RoboDMError(f"No NPC record '{name}'." + (f" Close: {', '.join(pref[:6])}." if pref else "")
                      + " Make one with `./dm npc add` or the npc-creator agent.")


def find_or_none(camp: Campaign, name: str) -> str | None:
    try:
        return find(camp, name)
    except RoboDMError:
        return None


def save(camp: Campaign, rec: dict, overwrite: bool = True) -> Path:
    rec = normalize(rec)
    problems = validate(camp, rec)
    if problems:
        raise RoboDMError(f"NPC record for {rec.get('name', '?')} isn't valid:\n" + "\n".join(f"- {p}" for p in problems))
    p = _path(camp, rec["slug"])
    old = read_json(p)
    if old and not overwrite:
        raise RoboDMError(f"{rec['name']} already has a record (npcs/{rec['slug']}.json). Use --overwrite to replace it.")
    if old:
        rec.setdefault("created", old.get("created"))
        rec["mind"] = {**(old.get("mind") or {}), **(rec.get("mind") or {})}
    rec.setdefault("created", now_iso())
    rec["updated"] = now_iso()
    ordered = {k: rec[k] for k in FIELDS if k in rec and rec[k] not in (None, "")}
    ordered.update({k: v for k, v in rec.items() if k not in ordered and v not in (None, "")})
    write_json(p, ordered)
    return p


def normalize(rec: dict) -> dict:
    rec = dict(rec)
    if not rec.get("name"):
        raise RoboDMError("An NPC record needs a name.")
    rec["slug"] = rec.get("slug") or slugify(rec["name"])
    rec["alignment"] = str(rec.get("alignment") or "").strip().lower()
    rec["status"] = str(rec.get("status") or "alive").lower()
    rec["tier"] = str(rec.get("tier") or ("major" if rec.get("sheet") else "minor")).lower()
    for k in ("secrets", "dm_only"):
        if isinstance(rec.get(k), str):
            rec[k] = [rec[k]] if rec[k].strip() else []
    rels = rec.get("relationships")
    if rels is None:
        rels = []
    out = []
    for r in rels if isinstance(rels, list) else [rels]:
        if isinstance(r, str):                                # "marda-ironside: her trusted captain"
            who, _, how = r.partition(":")
            r = {"who": who.strip(), "how": how.strip()}
        if isinstance(r, dict) and r.get("who"):
            r = dict(r, who=slugify(str(r["who"])))
            out.append(r)
    rec["relationships"] = out
    return rec


def validate(camp: Campaign | None, rec: dict, db=None) -> list[str]:
    """What's missing or wrong. The rules data is checked when it's available."""
    p = []
    for k in ("race", "alignment", "backstory"):
        if not str(rec.get(k) or "").strip():
            p.append(f"{k} is missing")
    if rec.get("alignment") and rec["alignment"] not in ALIGNMENTS:
        p.append(f"alignment '{rec['alignment']}' isn't one of: {', '.join(ALIGNMENTS)}")
    if rec.get("tier") not in TIERS:
        p.append(f"tier must be major or minor, not '{rec.get('tier')}'")
    if rec.get("status") not in STATUSES:
        p.append(f"status must be one of: {', '.join(STATUSES)}")
    if not isinstance(rec.get("relationships"), list):
        p.append("relationships must be a list (it may be empty)")
    living = rec.get("status") not in ("dead",)
    if living and not rec.get("statblock") and not rec.get("sheet"):
        p.append("needs a statblock (a creature from the rules data, e.g. commoner|mm) or a sheet")
    if living and rec.get("tier") == "major" and not rec.get("sheet"):
        p.append("a major (significant story) NPC needs a character sheet")
    if rec.get("sheet"):
        try:
            if isinstance(rec["sheet"], dict) and rec["sheet"].get("file"):
                if camp is not None and not read_json(camp.path(rec["sheet"]["file"])):
                    p.append(f"sheet file {rec['sheet']['file']} not found in the campaign")
            else:
                compile_sheet(rec)
        except (RoboDMError, KeyError, TypeError, ValueError) as e:
            p.append(f"sheet: {e}")
    if rec.get("statblock"):
        ref = statblock_ref(rec["statblock"])
        if not ref:
            p.append("statblock needs a creature name, e.g. commoner|mm")
        else:
            db = db if db is not None else _db()
            if db is not None and getattr(db, "available", False):
                rec_db, _ = db.lookup(ref[0], "monster", ref[1])
                if not rec_db:
                    p.append(f"statblock '{ref[0]}' isn't in the rules data")
    return p


def _db():
    try:
        from robodm.rules import default_db

        return default_db()
    except Exception:
        return None


# =============================================================================== stat blocks and sheets

def statblock_ref(sb: Any) -> tuple[str, str | None] | None:
    """(creature name, source) from "commoner|mm", "commoner_mm", "Commoner (MM)", a bestiary link or {"ref": …}."""
    if isinstance(sb, dict):
        sb = sb.get("ref") or sb.get("name") or ""
    s = str(sb or "").strip()
    if not s:
        return None
    if "#" in s:
        s = s.split("#", 1)[1]
    m = re.match(r"^(.*?)\s*\(([A-Za-z0-9]+)\)\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).upper()
    for sep in ("|", "_"):
        if sep in s:
            name, src = s.rsplit(sep, 1)
            if re.fullmatch(r"[A-Za-z0-9]{2,8}", src):
                return name.replace("%20", " ").strip(), src.upper()
    return s, None


def _parse_type(t: str, race: str) -> Any:
    t = (t or "").strip()
    if not t:
        return {"type": "humanoid", "tags": [race.lower()]} if race else "humanoid"
    m = re.match(r"^(\w+)\s*\((.+)\)$", t)
    if m:
        return {"type": m.group(1).lower(), "tags": [x.strip() for x in m.group(2).split(",")]}
    return t.lower()


def _align_letters(a: str) -> list[str]:
    a = (a or "").lower().strip()
    if a in ("neutral", "true neutral"):
        return ["N"]
    return [_ALIGN_ABBR[w] for w in a.split() if w in _ALIGN_ABBR] or ["N"]


def _level(sheet: dict) -> int:
    return sum(int(n) for n in re.findall(r"(\d+)", str(sheet.get("class") or "")))


def sheet_pb(sheet: dict) -> int:
    lvl = _level(sheet)
    if lvl:
        return proficiency_bonus(min(lvl, 20))
    from robodm.rules.index import cr_to_float

    cr = cr_to_float(sheet.get("cr")) or 0
    return 2 if cr < 5 else 2 + int((cr - 1) // 4)


def _abilities(sheet: dict) -> dict[str, int]:
    src = sheet.get("abilities") or sheet
    return {ab: int(src.get(ab, 10)) for ab in ABILITIES}


def _attack_text(act: dict, ab: dict[str, int], pb: int, spell_ab: str | None) -> str:
    """5etools-tagged attack line from a compact action, so the stat block renderer and the tracker read it."""
    stat = str(act.get("attack", "str")).lower()
    if stat.lstrip("+-").isdigit():
        to_hit, mod = int(stat), 0
    else:
        if stat == "finesse":
            key = "dex" if ab["dex"] >= ab["str"] else "str"
        elif stat == "spell":
            key = spell_ab or "int"
        else:
            key = stat[:3]
            if key not in ABILITIES:
                raise RoboDMError(f"action {act.get('name')}: attack must be str, dex, finesse, spell or a number")
        mod = ability_mod(ab[key])
        to_hit = mod + pb + int(act.get("bonus", 0))
        mod += int(act.get("bonus", 0)) if act.get("magic_damage", True) else 0
    ranged = bool(act.get("range"))
    kind = ("ms" if not ranged else "rs") if stat == "spell" else ("rw" if ranged else "mw")
    where = f"range {act['range']} ft." if ranged else f"reach {int(act.get('reach', 5))} ft."
    parts = []
    for i, d in enumerate([act.get("damage")] + list(act.get("extra_damage") or [])):
        if not d:
            continue
        m = re.match(r"^\s*(\d+d\d+)(?:\s*([+-])\s*(\d+))?\s*([a-z]+)?\s*$", str(d), re.I)
        if not m:
            raise RoboDMError(f"action {act.get('name')}: damage '{d}' should look like '1d8 slashing'")
        dice, sign, flat, dtype = m.group(1), m.group(2), m.group(3), (m.group(4) or "").lower()
        n = (int(flat) * (1 if sign == "+" else -1)) if flat else (mod if i == 0 and act.get("add_mod", True) else 0)
        expr = f"{dice} {'+' if n >= 0 else '-'} {abs(n)}" if n else dice
        cnt, faces = (int(x) for x in dice.split("d"))
        avg = max(1, int(cnt * (faces + 1) / 2) + n)
        parts.append(f"{avg} ({{@damage {expr}}}) {dtype} damage".replace("  ", " "))
    hit = f"{{@atk {kind}}} {{@hit {to_hit}}} to hit, {where}, {act.get('target', 'one target')}."
    if parts:
        hit += " {@h}" + parts[0] + ("" if len(parts) == 1 else " plus " + " plus ".join(parts[1:])) + "."
    if act.get("text"):
        hit += " " + act["text"]
    return hit


def compile_sheet(rec: dict) -> dict:
    """The NPC's compact sheet as a 5etools-shaped creature (what `format_monster` and `combat_profile` read)."""
    sheet = rec.get("sheet")
    if not isinstance(sheet, dict) or sheet.get("file"):
        raise RoboDMError("no compact sheet")
    ab = _abilities(sheet)
    pb = sheet_pb(sheet)
    saves = sheet.get("saves") or []
    skills = sheet.get("skills") or {}
    if isinstance(skills, list):
        skills = {s: 1 for s in skills}
    for s in skills:
        if s.lower() not in SKILLS:
            raise RoboDMError(f"unknown skill '{s}'")
    spell = sheet.get("spellcasting") or {}
    spell_ab = str(spell.get("ability") or "")[:3] or None
    hp = sheet.get("hp")
    if hp is None:
        raise RoboDMError("hp is missing")
    if sheet.get("ac") is None:
        raise RoboDMError("ac is missing")
    speed = sheet.get("speed", 30)
    m: dict[str, Any] = {
        "name": rec.get("name", "?"), "source": "NPC",
        "size": [SIZES.get(str(sheet.get("size", "M")).lower(), str(sheet.get("size", "M"))[:1].upper())],
        "type": _parse_type(sheet.get("type", ""), str(rec.get("race") or "")),
        "alignment": _align_letters(rec.get("alignment", "")),
        "ac": [{"ac": int(sheet["ac"]), "from": [sheet["ac_from"]]} if sheet.get("ac_from") else int(sheet["ac"])],
        "hp": {"average": int(hp), "formula": str(sheet.get("hit_dice") or "")} if sheet.get("hit_dice") else {"special": str(hp), "average": int(hp)},
        "speed": speed if isinstance(speed, dict) else {"walk": int(speed)},
        **ab,
        "cr": str(sheet.get("cr") or ("—" if _level(sheet) else "0")),
    }
    if saves:
        m["save"] = {s[:3].lower(): fmt_mod(ability_mod(ab[s[:3].lower()]) + pb) for s in saves}
    if skills:
        m["skill"] = {s.lower(): fmt_mod(ability_mod(ab[SKILLS[s.lower()]]) + pb * int(v)) for s, v in skills.items()}
    perc = int(skills.get("perception", 0)) if isinstance(skills, dict) else 0
    m["passive"] = 10 + ability_mod(ab["wis"]) + pb * perc
    for key, src in (("senses", "senses"), ("languages", "languages"), ("resist", "resist"), ("immune", "immune"),
                     ("vulnerable", "vulnerable"), ("conditionImmune", "condition_immune")):
        v = sheet.get(src)
        if v:
            m[key] = v if isinstance(v, list) else [v]
    traits = list(sheet.get("traits") or [])
    if sheet.get("class"):
        traits.insert(0, {"name": "Class", "text": f"{sheet['class']}" + (f" · {sheet['background']} background"
                                                                         if sheet.get("background") else "")})
    if traits:
        m["trait"] = [{"name": t["name"], "entries": [t.get("text", "")]} for t in traits]
    for section, key in (("action", "actions"), ("bonus", "bonus"), ("reaction", "reactions")):
        acts = []
        for act in sheet.get(key) or []:
            if "attack" in act:
                acts.append({"name": act["name"], "entries": [_attack_text(act, ab, pb, spell_ab)]})
            else:
                acts.append({"name": act["name"], "entries": [act.get("text", "")]})
        if acts:
            m[section] = acts
    if spell:
        sab = spell_ab or "int"
        dc, atk = 8 + pb + ability_mod(ab[sab]), pb + ability_mod(ab[sab])
        spells: dict[str, Any] = {}
        for lvl, names in (spell.get("spells") or {}).items():
            entry: dict[str, Any] = {"spells": [f"{{@spell {n}}}" for n in names]}
            slots = (spell.get("slots") or {}).get(str(lvl))
            if str(lvl) != "0" and slots:
                entry["slots"] = int(slots)
            spells[str(lvl)] = entry
        m["spellcasting"] = [{"name": "Spellcasting", "type": "spellcasting", "ability": sab, "spells": spells,
                              "headerEntries": [f"{rec.get('name')} is a spellcaster. Their spellcasting ability is "
                                                f"{ {'int': 'Intelligence', 'wis': 'Wisdom', 'cha': 'Charisma'}.get(sab, sab) } "
                                                f"(spell save {{@dc {dc}}}, {{@hit {atk}}} to hit with spell attacks)."]}]
    return m


def creature(camp: Campaign, rec: dict) -> tuple[dict | None, str]:
    """(5etools-shaped creature, where it came from) for the NPC's stat block or compact sheet."""
    if isinstance(rec.get("sheet"), dict) and not rec["sheet"].get("file"):
        return compile_sheet(rec), "sheet"
    if rec.get("statblock"):
        ref = statblock_ref(rec["statblock"])
        db = _db()
        if ref and db is not None and getattr(db, "available", False):
            r, _ = db.lookup(ref[0], "monster", ref[1])
            if r:
                m = dict(db.get(r))
                sb = rec["statblock"] if isinstance(rec["statblock"], dict) else {}
                if sb.get("ac") is not None:
                    m["ac"] = [int(sb["ac"])]
                if sb.get("hp") is not None:
                    m["hp"] = {"average": int(sb["hp"]), "formula": str(sb.get("hit_dice") or "")}
                return m, f"{m.get('name')} ({m.get('source')})"
    return None, ""


def statblock_text(camp: Campaign, rec: dict) -> str:
    from robodm.rules.statblock import format_monster

    if isinstance(rec.get("sheet"), dict) and rec["sheet"].get("file"):
        sheet = read_json(camp.path(rec["sheet"]["file"])) or {}
        if not sheet:
            return f"(sheet file {rec['sheet']['file']} is missing)"
        from robodm.campaign import pc_summary

        return pc_summary(sheet)
    m, where = creature(camp, rec)
    if not m:
        return "(no stat block: the rules data may be missing; `./dm rules status`)"
    text = format_monster(m)
    if where != "sheet":
        sb = rec["statblock"] if isinstance(rec["statblock"], dict) else {}
        text = f"_{rec['name']} uses the **{where}** stat block{(': ' + sb['notes']) if sb.get('notes') else ''}._\n\n" + text
    return text


def add_to_combat(camp: Campaign, enc, rec: dict, side: str = "enemy", init: int | None = None) -> list[dict]:
    """Put the NPC in the tracker from their sheet or stat block."""
    from robodm.campaign import pc_init_bonus, pc_save_bonus
    from robodm.rules.statblock import combat_profile

    if isinstance(rec.get("sheet"), dict) and rec["sheet"].get("file"):
        pc = read_json(camp.path(rec["sheet"]["file"])) or {}
        if not pc:
            raise RoboDMError(f"{rec['name']}'s sheet file {rec['sheet']['file']} is missing.")
        c = enc._new(enc._unique_id(rec["name"].split()[0]), rec["name"], kind="npc", side=side, init=init,
                     init_bonus=pc_init_bonus(pc), dex=pc["abilities"].get("dex", 10), ac=pc.get("ac", 10),
                     hp=pc["hp"]["current"], max_hp=pc["hp"]["max"],
                     saves={ab: pc_save_bonus(pc, ab) for ab in ABILITIES}, attacks=pc.get("attacks", []),
                     size=pc.get("size", "M"), speed=pc.get("speed", 30))
        enc.say(f"Added **{rec['name']}** (sheet) — HP {c['hp']}/{c['max_hp']}, AC {c['ac']}, init {fmt_mod(c['init_bonus'])}")
        return [c]
    m, _ = creature(camp, rec)
    if not m:
        raise RoboDMError(f"{rec['name']} has no usable stat block (is the rules data fetched?).")
    profile = combat_profile(m)
    return enc.add_monster(profile, side=side, name=rec["name"], init=init)


# =============================================================================== what the NPC's mind sees

def memories(camp: Campaign, slug: str) -> list[dict]:
    return [m for m in read_jsonl(npcs_dir(camp) / "memory.jsonl") if m.get("npc") == slug]


def remember(camp: Campaign, slug: str, text: str) -> dict:
    rec = {"npc": slug, "ts": now_iso(), "session": camp.session.get("number", 0), "text": text.strip()}
    append_jsonl(npcs_dir(camp) / "memory.jsonl", rec)
    return rec


def attitude(camp: Campaign, slug: str, who: str, change: str, why: str = "") -> dict:
    rec = load(camp, slug)
    mind = rec.setdefault("mind", {})
    att = mind.setdefault("attitude", {})
    key = slugify(who)
    cur = att.get(key, {"score": 0, "why": ""})
    ch = str(change).strip()
    score = cur["score"] + int(ch) if ch[:1] in "+-" else int(ch)
    cur = {"score": max(-3, min(3, score)), "why": why or cur.get("why", ""), "ts": now_iso()}
    att[key] = cur
    rec["updated"] = now_iso()
    write_json(_path(camp, slug), rec)
    return cur


def is_companion(camp: Campaign, slug: str) -> bool:
    from robodm import companions as co

    return slug in co.all_slugs(camp)


def known_documents(camp: Campaign, slug: str) -> list[dict]:
    from robodm import companions as co, lore

    docs = [{"text": m["text"], "show": f"(you remember, s{m.get('session', 0)}) {m['text']}"} for m in memories(camp, slug)]
    for m in read_jsonl(camp.path("memory.jsonl")):
        if slug in (m.get("known_by") or []) and not m.get("retired"):
            docs.append({"text": m["text"], "show": f"(you know, s{m.get('session', 0)}) {m['text']}"})
    for e in lore.all_entries(camp):
        body = co._strip_secrets(e["body"])
        docs.append({"text": f"{e['meta'].get('name', '')} {e['meta'].get('summary', '')} {body}",
                     "show": f"(lore) {e['meta'].get('name')}: {truncate(str(e['meta'].get('summary') or body), 220)}"})
    return docs


def recall(camp: Campaign, slug: str, query: str, limit: int = 8) -> list[str]:
    return [d["show"] for d in rank_documents(query, known_documents(camp, slug), limit=limit)]


def brief(camp: Campaign, slug: str, situation: str = "", combat: bool = False, record: bool = True) -> str:
    """The NPC's whole world for one consultation (a companion gets its companion brief)."""
    from robodm import audit, capture, companions as co, lore, world
    from robodm.combat import Encounter

    if is_companion(camp, slug):
        return co.brief(camp, slug, situation=situation, combat=combat, record=record)
    rec = load(camp, slug)
    name = rec["name"]
    present = slug in [s for s, _ in world.scene_cast(camp)]
    lines = [f"# You are {name}"]
    if rec.get("status") == "dead":
        lines.append("_(Your record says you are **dead**. Only answer if the DM is deliberately bringing you in: a "
                     "memory, a ghost, a vision.)_")
    elif not present:
        lines.append("_(You're not in the current scene. Answer as yourself, wherever you are, if the DM asks.)_")
    who = " · ".join(x for x in (str(rec.get("race") or "").title(), str(rec.get("alignment") or "").title(),
                                  rec.get("role"), (f"age {rec['age']}" if rec.get("age") else "")) if x)
    lines.append(f"*{who}*" + (f" · voice: {rec['voice']}" if rec.get("voice") else ""))
    for title, key in (("Who you are", "backstory"), ("How you look", "appearance"), ("What you want", "wants"),
                       ("What you fear", "fears")):
        if rec.get(key):
            lines.append(f"\n## {title}\n{rec[key]}")
    if rec.get("secrets"):
        lines.append("\n## Your secrets (yours to keep or reveal)\n" + "\n".join(f"- {s}" for s in rec["secrets"]))
    rels = rec.get("relationships") or []
    att = (rec.get("mind") or {}).get("attitude") or {}
    if rels or att:
        lines.append("\n## The people in your life")
        for r in rels:
            score = att.get(r["who"], {}).get("score")
            lines.append(f"- {r.get('name') or r['who'].replace('-', ' ').title()}: {r.get('how', '')}"
                         + (f" (attitude {score:+d})" if score is not None else ""))
        for k, a in sorted(att.items()):
            if not any(r["who"] == k for r in rels):
                lines.append(f"- {k.replace('-', ' ').title()}: attitude {a['score']:+d}" + (f" ({a['why']})" if a.get("why") else ""))
    guide = co.ALIGNMENT_GUIDE.get(str(rec.get("alignment", "")).lower())
    if guide:
        lines.append(f"\n## How your alignment tends to colour choices\n{guide} (A tendency, not a script.)")
    if rec.get("lore"):
        try:
            e = lore.find(camp, rec["lore"])
            pub = co._strip_secrets(e["body"])
            if pub:
                lines.append(f"\n## What the world says about you (public lore)\n{truncate(pub, 2500)}")
        except RoboDMError:
            pass
    wd = world.load(camp)
    lines.append("\n## Where things stand")
    if world.tracking(wd):
        lines.append(f"- It is {world.fmt_time(wd)}.")
    if not present:
        lines.append("- You're elsewhere, not in the scene the party is in.")
    if present:
        others = [n for s, n in world.scene_cast(camp) if s != slug]
        pcs = [pc["name"] for pc in camp.party()]
        lines.append(f"- You are here{' at ' + wd['scene']['place'] if wd['scene'].get('place') else ''}, with "
                     + ", ".join(pcs + others) + ".")
    mems = memories(camp, slug)
    tagged = [m for m in read_jsonl(camp.path("memory.jsonl")) if slug in (m.get("known_by") or []) and not m.get("retired")]
    if mems or tagged:
        lines.append("\n## What you remember (newest last)")
        lines.extend(f"- {m['text']}" for m in mems[-20:])
        lines.extend(f"- (known to you) {m['text']}" for m in tagged[-12:])
    entries = [e for e in lore.all_entries(camp) if e["meta"].get("summary") and e["slug"] != rec.get("lore")]
    if entries:
        lines.append("\n## The world as people know it (public lore; DM secrets removed)")
        lines.extend(f"- {e['meta'].get('name')}: {truncate(str(e['meta']['summary']), 140)}" for e in entries[:30])
    if present and camp.session.get("active"):
        said = capture.last_exchanges(camp, n=3, width=600)
        if said:
            lines.append("\n## Lately (the last exchanges at the table; the DM's description below wins)")
            lines.extend(f"- {x}" for x in said)
    if situation:
        lines.append(f"\n## Right now (from the DM)\n{situation}")
    enc = Encounter(camp)
    if enc.active and (combat or any(slugify(c["name"]) == slug for c in enc.combatants.values())):
        me = next((c for c in enc.combatants.values() if slugify(c["name"]) == slug), None)
        lines.append("\n## The fight (what you can see)")
        lines.append(enc.status_text(reveal=False))
        if me:
            atks = "; ".join(a["name"] for a in me.get("attacks", [])) or "see your stat block"
            lines.append(f"You are **{me['id']}** in the tracker: HP {me['hp']}/{me['max_hp']}, AC {me['ac']} · actions: {atks}")
    lines.append(f"\n## Answer\nINTENT / DOES / SAYS / HIDES, in that shape. Record what you'll carry from this with "
                 f"`./dm npc remember {slug} \"…\"` (first person, one or two lines), and a changed feeling toward "
                 f"someone with `./dm npc attitude {slug} <who> +1 --why \"…\"`.")
    if record:
        mind = rec.setdefault("mind", {})
        mind["consults"] = int(mind.get("consults", 0)) + 1
        mind["last_brief_turn"] = audit.current_turn(camp)
        write_json(_path(camp, slug), rec)
    return "\n".join(lines)


# =============================================================================== the subagents

NPC_PROTOCOL = """\
You play **one named NPC** in a D&D 5e (2014) campaign run by RoboDM: a real person with their own wants, fears,
secrets and voice. The Dungeon Master (the main Claude) consults you when this character has to decide, react or
speak at a moment that matters. The DM's message names the character (their slug, e.g. `halda-barrowe`) and maybe the
campaign (`-c <campaign>`). Substitute them in every command.

## Every time
1. Run `./dm npc brief <npc>` (add `--combat` during a fight). It is the ONLY source of who you are and what you
   know. Run every command exactly as written, from the folder you start in: no `cd`, no full paths, nothing piped
   or chained.
2. **Stay inside your knowledge.** You know your record, your own secrets, your memories, public lore, and what
   happened in front of you. You don't know DM secrets, other people's thoughts, or what happened where you weren't.
   If you're unsure whether you'd know something, you don't. More of your memories: `./dm npc recall <npc> "words"`.
3. **Decide as yourself**, not as a helpful assistant: weigh what you want, fear and owe against the moment. You may
   agree, refuse, bargain, lie, stall or walk away. Keep it short: the DM narrates.
4. **Never decide for a player character**: not their actions, words or feelings.
5. **In combat**, propose concrete actions from your stat block as `./dm combat …` commands; the DM runs them.
6. **Remember** what you'll carry from this beat, first person, one or two lines: `./dm npc remember <npc> "…"`.
   A changed feeling toward someone: `./dm npc attitude <npc> <who> +1 --why "…"`.
7. You can't run anything else. The hooks block dice, combat changes and file access.

## Answer (to the DM, not the players), in this shape
INTENT: what you want right now, in one line
DOES: what you do this beat
SAYS: your words, in your voice (or "—")
HIDES: what you're deliberately not saying (DM's eyes only), or "—"
"""


def agent_text() -> str:
    """The generic `npc` subagent. Its model and effort are fixed: every NPC mind runs on NPC_MODEL."""
    return ("---\nname: npc\ndescription: Plays one named RoboDM NPC (not a companion) with their own mind: wants, "
            "fears, secrets, voice, and only what that character knows. Use when an NPC in the scene has to decide, "
            "react or speak at a moment that matters (a negotiation, a lie, a choice, a reaction to news); tell it the "
            "NPC's slug, the campaign, and the moment from the NPC's point of view. Companions use their own "
            f"companion agents.\ntools: Bash\nmodel: {NPC_MODEL}\neffort: {NPC_EFFORT}\n---\n\n"
            "<!-- generated by `./dm npc agent` · edit robodm/npcs.py, not this file -->\n" + NPC_PROTOCOL)


def write_agent() -> Path:
    from robodm.companions import agents_dir

    p = agents_dir() / "npc.md"
    write_text(p, agent_text())
    return p


# =============================================================================== the cast and what's missing

def named_without_records(camp: Campaign) -> list[tuple[str, str]]:
    """(slug, name) of characters the campaign names (NPC lore entries, companions) who have no NPC record."""
    from robodm import companions as co, lore

    have = set(all_slugs(camp))
    for r in all_records(camp):
        if r.get("lore"):
            have.add(r["lore"])
    out = []
    for e in lore.all_entries(camp):
        if e["meta"].get("type") == "npc" and e["slug"] not in have:
            out.append((e["slug"], str(e["meta"].get("name") or e["slug"])))
    for slug in co.all_slugs(camp):
        if slug not in have and slug not in {x for x, _ in out}:
            meta, _ = co.load_persona(camp, slug)
            out.append((slug, meta.get("name", slug)))
    return out


def check(camp: Campaign) -> tuple[list[str], list[tuple[str, str]]]:
    """(problems in existing records, named characters with no record)."""
    db = _db()
    problems = []
    for r in all_records(camp):
        for p in validate(camp, r, db):
            problems.append(f"{r['name']}: {p}")
    return problems, named_without_records(camp)


def import_records(camp: Campaign, data: Any, overwrite: bool = False) -> list[str]:
    """Save one record, a list of them, or {"npcs": [...]} (from the npc-creator's draft file)."""
    if isinstance(data, dict) and isinstance(data.get("npcs"), list):
        data = data["npcs"]
    recs = data if isinstance(data, list) else [data]
    saved, errors = [], []
    for rec in recs:
        try:
            save(camp, rec, overwrite=overwrite)
            saved.append(rec.get("name", "?"))
        except RoboDMError as e:
            errors.append(str(e))
    if errors:
        raise RoboDMError((f"Saved {len(saved)}: {', '.join(saved)}.\n" if saved else "") + "\n".join(errors))
    return saved


def load_import_file(path: str) -> Any:
    p = Path(path)
    if not p.exists():
        raise RoboDMError(f"No file {path}.")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise RoboDMError(f"{path} isn't valid JSON: {e}")


def by_slug_or_lore(camp: Campaign) -> dict[str, dict]:
    """Records keyed by their own slug and by their lore entry's slug (scene casts use either)."""
    out: dict[str, dict] = {}
    for r in all_records(camp):
        out[r["slug"]] = r
        if r.get("lore"):
            out.setdefault(r["lore"], r)
    return out


def short(rec: dict) -> str:
    stat = "sheet" if rec.get("sheet") else ((statblock_ref(rec.get("statblock")) or ("no stats",))[0])
    return f"{str(rec.get('race') or '?').title()}, {rec.get('alignment') or '?'}, {stat}"


def summary_line(rec: dict) -> str:
    stat = ("sheet" if rec.get("sheet") else
            (statblock_ref(rec.get("statblock")) or ("—",))[0] if rec.get("statblock") else "—")
    bits = [str(rec.get("race") or "?").title(), str(rec.get("alignment") or "?"), rec.get("tier", "minor"), stat]
    if rec.get("status", "alive") != "alive":
        bits.append(rec["status"])
    return f"- **{rec['name']}** ({' · '.join(bits)})" + (f" — {rec['role']}" if rec.get("role") else "")
