"""Campaigns, settings, player characters, sessions and quests.

Layout of a campaign folder:

    campaigns/<slug>/
      campaign.json        name, premise, settings, session counter, last summary
      party/<pc>.json      player characters
      memory.jsonl         durable facts (events, choices, NPCs, items, rulings…)
      quests.json          open / finished quests
      journal/session-NNN.md   running log + summary for each session
      lore/<type>/<entry>.md   world lore (see lore.py)
      context/<block>.md   campaign-specific instruction blocks
      combat.json          the current encounter (if any)
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from robodm import paths
from robodm.util import (
    ABILITIES,
    SKILLS,
    RoboDMError,
    ability_mod,
    fmt_mod,
    normalize_ability,
    normalize_skill,
    now_iso,
    proficiency_bonus,
    read_json,
    slugify,
    today,
    write_json,
    write_text,
)

DEFAULT_SETTINGS: dict[str, Any] = {
    # auto: RoboDM rolls everything. players: players roll their own PC dice and report results.
    "dice_mode": "auto",
    # double_dice (RAW) | max_plus_roll (popular house rule)
    "crit_rule": "double_dice",
    # milestone | xp
    "advancement": "milestone",
    # Books allowed for player options and monsters (5etools source ids). Empty = everything.
    "sources": ["PHB", "MM", "DMG", "XGE", "TCE"],
    # concise | standard | rich — how much narration per beat
    "narration": "standard",
    # Hide monster HP numbers from players (describe as bloodied/near death instead)
    "hide_monster_hp": True,
    # Battle-map diagonals: 5 (PHB: every diagonal costs 5 ft) | 5-10-5 (DMG variant: alternate 5 and 10 ft)
    "grid_diagonals": "5",
    # Companion subagents: key (at decision points; default) | always (every time they act or speak) | off (DM voices them)
    "companion_agents": "key",
    # fast | balanced | thorough: subagent models, post-audit scope and brief size (robodm/costs.py)
    "turn_speed": "balanced",
    # The DM's own model in the web client ("" = Claude Code's default; sonnet, opus, haiku or a full model id)
    "dm_model": "",
}

SETTING_CHOICES = {
    "dice_mode": ["auto", "players"],
    "crit_rule": ["double_dice", "max_plus_roll"],
    "advancement": ["milestone", "xp"],
    "narration": ["concise", "standard", "rich"],
    "grid_diagonals": ["5", "5-10-5"],
    "companion_agents": ["key", "always", "off"],
    "turn_speed": ["fast", "balanced", "thorough"],
}

HIT_DICE = {
    "barbarian": 12, "fighter": 10, "paladin": 10, "ranger": 10, "bard": 8, "cleric": 8, "druid": 8, "monk": 8,
    "rogue": 8, "warlock": 8, "artificer": 8, "sorcerer": 6, "wizard": 6,
}

FULL_CASTERS = {"bard", "cleric", "druid", "sorcerer", "wizard"}
HALF_CASTERS = {"paladin", "ranger"}
THIRD_CASTER_SUBCLASSES = {"eldritch knight", "arcane trickster"}
SPELL_ABILITY = {"bard": "cha", "cleric": "wis", "druid": "wis", "sorcerer": "cha", "wizard": "int", "paladin": "cha",
                 "ranger": "wis", "warlock": "cha", "artificer": "int", "fighter": "int", "rogue": "int"}

# Spell slots per caster level (PHB multiclass table), levels 1-20, slot levels 1-9
FULL_CASTER_SLOTS = [
    [2], [3], [4, 2], [4, 3], [4, 3, 2], [4, 3, 3], [4, 3, 3, 1], [4, 3, 3, 2], [4, 3, 3, 3, 1], [4, 3, 3, 3, 2],
    [4, 3, 3, 3, 2, 1], [4, 3, 3, 3, 2, 1], [4, 3, 3, 3, 2, 1, 1], [4, 3, 3, 3, 2, 1, 1], [4, 3, 3, 3, 2, 1, 1, 1],
    [4, 3, 3, 3, 2, 1, 1, 1], [4, 3, 3, 3, 2, 1, 1, 1, 1], [4, 3, 3, 3, 3, 1, 1, 1, 1], [4, 3, 3, 3, 3, 2, 1, 1, 1],
    [4, 3, 3, 3, 3, 2, 2, 1, 1],
]
# Warlock pact magic: (slots, slot level) by warlock level
PACT_SLOTS = [(1, 1), (2, 1), (2, 2), (2, 2), (2, 3), (2, 3), (2, 4), (2, 4), (2, 5), (2, 5), (3, 5), (3, 5),
              (3, 5), (3, 5), (3, 5), (3, 5), (4, 5), (4, 5), (4, 5), (4, 5)]


class Campaign:
    def __init__(self, slug: str):
        self.slug = slug
        self.dir = paths.campaigns_dir() / slug
        if not (self.dir / "campaign.json").exists():
            raise RoboDMError(f"No campaign '{slug}'. Create one with `./dm campaign new \"Name\"`.")
        self.data = read_json(self.dir / "campaign.json", {})
        # fill in settings added in newer versions
        settings = dict(DEFAULT_SETTINGS)
        settings.update(self.data.get("settings", {}))
        settings.pop("rules_audit", None)  # rules checking is mandatory now (robodm/audit.py)
        self.data["settings"] = settings

    # ------------------------------------------------------------ basics
    @property
    def name(self) -> str:
        return self.data.get("name", self.slug)

    @property
    def settings(self) -> dict:
        return self.data["settings"]

    def save(self) -> None:
        self.data["updated"] = now_iso()
        write_json(self.dir / "campaign.json", self.data)

    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    # ------------------------------------------------------------ sessions
    @property
    def session(self) -> dict:
        return self.data.setdefault("session", {"number": 0, "active": False})

    def journal_path(self, number: int | None = None) -> Path:
        n = number if number is not None else self.session.get("number", 0)
        return self.path("journal", f"session-{n:03d}.md")

    def start_session(self, title: str = "") -> int:
        s = self.session
        if s.get("active"):
            return s["number"]
        s["number"] = s.get("number", 0) + 1
        s["active"] = True
        s["started"] = now_iso()
        header = f"# Session {s['number']}{' — ' + title if title else ''}\n\n*{today()}*\n\n## Log\n"
        jp = self.journal_path()
        if not jp.exists():
            write_text(jp, header)
        self.save()
        return s["number"]

    def log(self, text: str) -> Path:
        if not self.session.get("active"):
            self.start_session()
        jp = self.journal_path()
        content = jp.read_text(encoding="utf-8") if jp.exists() else "## Log\n"
        stamp = now_iso()[11:16]
        line = f"- [{stamp}] {text.strip()}\n"
        if "\n## Summary" in content:
            head, tail = content.split("\n## Summary", 1)
            content = head.rstrip("\n") + "\n" + line + "\n## Summary" + tail
        else:
            content = content.rstrip("\n") + "\n" + line
        write_text(jp, content)
        return jp

    def end_session(self, summary: str) -> int:
        s = self.session
        n = s.get("number", 0)
        if n == 0:
            raise RoboDMError("No session has been started.")
        jp = self.journal_path(n)
        content = jp.read_text(encoding="utf-8") if jp.exists() else f"# Session {n}\n\n## Log\n"
        if "\n## Summary" in content:
            content = content.split("\n## Summary", 1)[0]
        content = content.rstrip("\n") + f"\n\n## Summary\n\n{summary.strip()}\n"
        write_text(jp, content)
        s["active"] = False
        s["ended"] = now_iso()
        self.data["last_summary"] = {"session": n, "text": summary.strip()}
        self.save()
        return n

    def session_summaries(self, limit: int = 3) -> list[tuple[int, str]]:
        out = []
        jdir = self.path("journal")
        if not jdir.exists():
            return out
        for p in sorted(jdir.glob("session-*.md"), reverse=True):
            text = p.read_text(encoding="utf-8")
            if "\n## Summary" in text:
                summ = text.split("\n## Summary", 1)[1].strip()
                m = re.search(r"(\d+)", p.stem)
                out.append((int(m.group(1)) if m else 0, summ))
            if len(out) >= limit:
                break
        return out

    def current_log(self, tail: int = 25) -> list[str]:
        jp = self.journal_path()
        if not jp.exists():
            return []
        text = jp.read_text(encoding="utf-8").split("\n## Summary", 1)[0]
        lines = [ln for ln in text.splitlines() if ln.startswith("- [")]
        return lines[-tail:]

    # ------------------------------------------------------------ party
    def pc_path(self, slug: str) -> Path:
        return self.path("party", f"{slug}.json")

    def party(self) -> list[dict]:
        pdir = self.path("party")
        if not pdir.exists():
            return []
        return [read_json(p) for p in sorted(pdir.glob("*.json"))]

    def find_pc(self, name: str) -> dict | None:
        slug = slugify(name)
        p = self.pc_path(slug)
        if p.exists():
            return read_json(p)
        low = name.lower()
        for pc in self.party():
            if pc["name"].lower() == low or pc["name"].lower().startswith(low) or pc["name"].lower().split()[0] == low:
                return pc
        return None

    def get_pc(self, name: str) -> dict:
        pc = self.find_pc(name)
        if not pc:
            names = ", ".join(p["name"] for p in self.party()) or "none yet"
            raise RoboDMError(f"No character '{name}'. Party: {names}")
        return pc

    def save_pc(self, pc: dict) -> None:
        pc["updated"] = now_iso()
        write_json(self.pc_path(pc["slug"]), pc)

    # ------------------------------------------------------------ quests
    def quests(self) -> list[dict]:
        return read_json(self.path("quests.json"), [])

    def save_quests(self, quests: list[dict]) -> None:
        write_json(self.path("quests.json"), quests)


# ---------------------------------------------------------------- campaign management

def list_campaigns() -> list[dict]:
    root = paths.campaigns_dir()
    if not root.exists():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and (p / "campaign.json").exists():
            d = read_json(p / "campaign.json", {})
            out.append({"slug": p.name, "name": d.get("name", p.name), "session": d.get("session", {}).get("number", 0)})
    return out


def active_slug() -> str | None:
    f = paths.active_campaign_file()
    if f.exists():
        slug = f.read_text(encoding="utf-8").strip()
        if slug and (paths.campaigns_dir() / slug / "campaign.json").exists():
            return slug
    camps = list_campaigns()
    if len(camps) == 1:
        return camps[0]["slug"]
    return None


def set_active(slug: str) -> None:
    if not (paths.campaigns_dir() / slug / "campaign.json").exists():
        raise RoboDMError(f"No campaign '{slug}'.")
    write_text(paths.active_campaign_file(), slug + "\n")


def load(slug: str | None = None) -> Campaign:
    slug = slug or active_slug()
    if not slug:
        camps = list_campaigns()
        if not camps:
            raise RoboDMError("No campaigns yet. Start one with `./dm campaign new \"Campaign Name\"`.")
        raise RoboDMError("Several campaigns exist; pick one with `./dm campaign use <slug>`: "
                          + ", ".join(c["slug"] for c in camps))
    return Campaign(slug)


def create(name: str, premise: str = "", setting: str = "", tone: str = "", slug: str | None = None) -> Campaign:
    slug = slug or slugify(name)
    d = paths.campaigns_dir() / slug
    if (d / "campaign.json").exists():
        raise RoboDMError(f"Campaign '{slug}' already exists.")
    for sub in ("party", "journal", "lore", "context"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "slug": slug,
        "created": now_iso(),
        "premise": premise,
        "setting": setting,
        "tone": tone,
        "settings": dict(DEFAULT_SETTINGS),
        "enabled_blocks": [],
        "session": {"number": 0, "active": False},
        "last_summary": None,
        "dm_notes": "",
    }
    write_json(d / "campaign.json", data)
    write_json(d / "quests.json", [])
    (d / "memory.jsonl").touch()
    for sub in ("party", "journal", "lore", "context"):
        keep = d / sub / ".gitkeep"
        if not any((d / sub).iterdir()):
            keep.touch()
    set_active(slug)
    return Campaign(slug)


def delete(slug: str) -> None:
    d = paths.campaigns_dir() / slug
    if not (d / "campaign.json").exists():
        raise RoboDMError(f"No campaign '{slug}'.")
    shutil.rmtree(d)
    f = paths.active_campaign_file()
    if f.exists() and f.read_text(encoding="utf-8").strip() == slug:
        f.unlink()


def coerce_setting(key: str, value: str) -> Any:
    if key == "rules_audit":
        raise RoboDMError("Rules checking is mandatory (a gate before every rules event and an audit after it); "
                          "it can't be turned off. See `./dm audit status`.")
    if key not in DEFAULT_SETTINGS:
        raise RoboDMError(f"Unknown setting '{key}'. Settings: {', '.join(DEFAULT_SETTINGS)}")
    default = DEFAULT_SETTINGS[key]
    if key == "dm_model":
        v = value.strip()
        if v in ("default", "none", "-"):
            return ""
        if v and v not in ("sonnet", "opus", "haiku") and not v.startswith("claude-"):
            raise RoboDMError("dm_model is sonnet, opus, haiku, a full model id (claude-…), or default.")
        return v
    if key in SETTING_CHOICES and value not in SETTING_CHOICES[key]:
        raise RoboDMError(f"{key} must be one of: {', '.join(SETTING_CHOICES[key])}")
    if isinstance(default, bool):
        return value.lower() in ("1", "true", "yes", "on")
    if isinstance(default, list):
        return [v.strip().upper() for v in value.split(",") if v.strip()]
    if isinstance(default, int):
        return int(value)
    return value


# ---------------------------------------------------------------- characters

def spell_slots_for(cls: str, level: int, subclass: str = "") -> dict:
    """Single-class slots. Half/third casters use the full-caster row at ceil(level/2) / ceil(level/3),
    which reproduces the Paladin, Ranger, Artificer, Eldritch Knight and Arcane Trickster tables exactly
    (multiclass characters: set slots by hand from the PHB multiclass table)."""
    c = cls.lower()
    caster_level = 0
    if c in FULL_CASTERS:
        caster_level = level
    elif c in HALF_CASTERS:
        caster_level = (level + 1) // 2 if level >= 2 else 0
    elif c == "artificer":
        caster_level = (level + 1) // 2
    elif subclass.lower() in THIRD_CASTER_SUBCLASSES:
        caster_level = (level + 2) // 3 if level >= 3 else 0
    if c == "warlock":
        n, lvl = PACT_SLOTS[max(1, min(level, 20)) - 1]
        return {str(lvl): {"max": n, "used": 0, "pact": True}}
    if caster_level <= 0:
        return {}
    row = FULL_CASTER_SLOTS[min(caster_level, 20) - 1]
    return {str(i + 1): {"max": n, "used": 0} for i, n in enumerate(row)}


def parse_classes(text: str) -> list[dict]:
    """'Wizard 3, Cleric 2 (Life Domain)' → [{'class': 'Wizard', 'level': 3, 'subclass': ''}, …]"""
    out = []
    for part in [x.strip() for x in re.split(r"[,/;]", text) if x.strip()]:
        m = re.fullmatch(r"([A-Za-z][A-Za-z' -]*?)\s+(\d{1,2})(?:\s*\(([^)]*)\))?", part)
        if not m:
            raise RoboDMError(f"Can't read '{part}'. Write classes like \"Wizard 3, Cleric 2 (Life Domain)\".")
        out.append({"class": m.group(1).strip().title(), "level": int(m.group(2)), "subclass": (m.group(3) or "").strip()})
    if not out or sum(c["level"] for c in out) > 20:
        raise RoboDMError("Give 1–20 total levels across the classes.")
    return out


def pc_classes(pc: dict) -> list[dict]:
    return pc.get("classes") or [{"class": pc.get("class", ""), "level": pc.get("level", 1), "subclass": pc.get("subclass", "")}]


def multiclass_slots(classes: list[dict]) -> dict:
    """PHB p.164-165: one Spellcasting class uses its own table; several combine into a caster level (full
    levels + half of paladin/ranger + third of Eldritch Knight/Arcane Trickster, rounded down; artificer half
    rounded up) on the multiclass table. Warlock Pact Magic stays separate (key 'pact' when combined)."""
    casters = []
    level = 0
    for c in classes:
        name, lvl, sub = c["class"].lower(), int(c["level"]), c.get("subclass", "").lower()
        if name in FULL_CASTERS:
            casters.append(c)
            level += lvl
        elif name in HALF_CASTERS and lvl >= 2:
            casters.append(c)
            level += lvl // 2
        elif name == "artificer":
            casters.append(c)
            level += (lvl + 1) // 2
        elif sub in THIRD_CASTER_SUBCLASSES and lvl >= 3:
            casters.append(c)
            level += lvl // 3
    warlock = sum(int(c["level"]) for c in classes if c["class"].lower() == "warlock")
    if len(casters) <= 1:
        slots = spell_slots_for(casters[0]["class"], int(casters[0]["level"]), casters[0].get("subclass", "")) if casters else {}
    else:
        row = FULL_CASTER_SLOTS[min(level, 20) - 1] if level > 0 else []
        slots = {str(i + 1): {"max": n, "used": 0} for i, n in enumerate(row)}
    if warlock:
        n, lvl = PACT_SLOTS[min(warlock, 20) - 1]
        if slots:
            slots["pact"] = {"max": n, "used": 0, "pact": True, "level": lvl}
        else:
            slots = {str(lvl): {"max": n, "used": 0, "pact": True}}
    return slots


def pc_slots(pc: dict) -> dict:
    return multiclass_slots(pc_classes(pc))


def slot_label(key: str, slot: dict) -> str:
    return f"pact L{slot.get('level')}" if key == "pact" else f"L{key}{' (pact)' if slot.get('pact') else ''}"


def default_max_hp(cls: str, level: int, con: int) -> int:
    hd = HIT_DICE.get(cls.lower(), 8)
    mod = ability_mod(con)
    return max(1, hd + mod + (level - 1) * (hd // 2 + 1 + mod))


def new_pc(name: str, **kw) -> dict:
    cls = kw.get("cls") or ""
    level = int(kw.get("level") or 1)
    abilities = {ab: int(kw.get(ab) or 10) for ab in ABILITIES}
    subclass = kw.get("subclass") or ""
    hp = kw.get("hp")
    max_hp = int(hp) if hp else default_max_hp(cls, level, abilities["con"])
    saves = [normalize_ability(s) for s in kw.get("saves") or []]
    if not saves and cls.lower() in CLASS_SAVES:
        saves = CLASS_SAVES[cls.lower()]
    skills = []
    for s in kw.get("skills") or []:
        sk = normalize_skill(s)
        if not sk:
            raise RoboDMError(f"Unknown skill '{s}'.")
        skills.append(sk)
    expertise = [normalize_skill(s) for s in kw.get("expertise") or [] if normalize_skill(s)]
    slots = spell_slots_for(cls, level, subclass)
    pc = {
        "name": name,
        "slug": slugify(name),
        "player": kw.get("player") or "",
        "race": kw.get("race") or "",
        "class": cls,
        "subclass": subclass,
        "level": level,
        "background": kw.get("background") or "",
        "abilities": abilities,
        "ac": int(kw.get("ac") or 10 + ability_mod(abilities["dex"])),
        "speed": int(kw.get("speed") or 30),
        "hp": {"max": max_hp, "current": max_hp, "temp": 0},
        "hit_dice": {"die": HIT_DICE.get(cls.lower(), 8), "max": level, "used": 0},
        "save_proficiencies": saves,
        "skill_proficiencies": skills,
        "expertise": expertise,
        "jack_of_all_trades": bool(kw.get("jack_of_all_trades")),
        "initiative_bonus": kw.get("init"),
        "spellcasting_ability": kw.get("spell_ability") or (SPELL_ABILITY.get(cls.lower(), "") if slots else ""),
        "spell_slots": slots,
        "spells": [],
        "attacks": [],
        "resources": {},
        "conditions": [],
        "inventory": [],
        "gold": 0,
        "xp": 0,
        "death_saves": {"success": 0, "fail": 0},
        "features": [],
        "notes": kw.get("notes") or "",
        "created": now_iso(),
    }
    return pc


CLASS_SAVES = {
    "barbarian": ["str", "con"], "bard": ["dex", "cha"], "cleric": ["wis", "cha"], "druid": ["int", "wis"],
    "fighter": ["str", "con"], "monk": ["str", "dex"], "paladin": ["wis", "cha"], "ranger": ["str", "dex"],
    "rogue": ["dex", "int"], "sorcerer": ["con", "cha"], "warlock": ["wis", "cha"], "wizard": ["int", "wis"],
    "artificer": ["con", "int"],
}


def pc_prof(pc: dict) -> int:
    return proficiency_bonus(pc.get("level", 1))


def pc_save_bonus(pc: dict, ab: str) -> int:
    bonus = ability_mod(pc["abilities"].get(ab, 10))
    if ab in pc.get("save_proficiencies", []):
        bonus += pc_prof(pc)
    return bonus + int(pc.get("bonuses", {}).get(f"save_{ab}", 0)) + int(pc.get("bonuses", {}).get("save", 0))


def pc_skill_bonus(pc: dict, skill: str) -> int:
    ab = SKILLS[skill]
    bonus = ability_mod(pc["abilities"].get(ab, 10))
    if skill in pc.get("expertise", []):
        bonus += 2 * pc_prof(pc)
    elif skill in pc.get("skill_proficiencies", []):
        bonus += pc_prof(pc)
    elif pc.get("jack_of_all_trades"):
        bonus += pc_prof(pc) // 2
    return bonus + int(pc.get("bonuses", {}).get(skill, 0))


def pc_init_bonus(pc: dict) -> int:
    if pc.get("initiative_bonus") not in (None, ""):
        return int(pc["initiative_bonus"])
    bonus = ability_mod(pc["abilities"].get("dex", 10))
    if pc.get("jack_of_all_trades"):
        bonus += pc_prof(pc) // 2
    return bonus


def pc_passive(pc: dict, skill: str = "perception") -> int:
    return 10 + pc_skill_bonus(pc, skill)


def pc_spell_dc(pc: dict) -> int | None:
    ab = pc.get("spellcasting_ability")
    if not ab:
        return None
    return 8 + pc_prof(pc) + ability_mod(pc["abilities"].get(ab, 10))


def pc_summary(pc: dict, full: bool = False) -> str:
    hp = pc["hp"]
    temp = f"+{hp['temp']} temp" if hp.get("temp") else ""
    if pc.get("classes") and len(pc["classes"]) > 1:
        who = " ".join(x for x in (pc.get("race"), " / ".join(
            f"{c['class']} {c['level']}" + (f" ({c['subclass']})" if c.get("subclass") else "") for c in pc["classes"])) if x)
    else:
        who = " ".join(x for x in (pc.get("race"), f"{pc.get('class', '')} {pc.get('level', 1)}".strip()) if x)
        if pc.get("subclass"):
            who += f" ({pc['subclass']})"
    status = ""
    if hp["current"] <= 0:
        ds = pc.get("death_saves", {})
        status = f" — DOWN (death saves {ds.get('success', 0)}✓/{ds.get('fail', 0)}✗)"
    conds = f" [{', '.join(c['name'] if isinstance(c, dict) else c for c in pc.get('conditions', []))}]" if pc.get("conditions") else ""
    line = (f"**{pc['name']}**{' (' + pc['player'] + ')' if pc.get('player') else ''} — {who} · "
            f"HP {hp['current']}/{hp['max']}{' ' + temp if temp else ''} · AC {pc.get('ac')} · "
            f"PP {pc_passive(pc)}{status}{conds}")
    if not full:
        return line
    ab = " ".join(f"{a.upper()} {pc['abilities'][a]}({fmt_mod(ability_mod(pc['abilities'][a]))})" for a in ABILITIES)
    saves = ", ".join(f"{a.upper()} {fmt_mod(pc_save_bonus(pc, a))}" for a in ABILITIES)
    profs = ", ".join(f"{s.title()} {fmt_mod(pc_skill_bonus(pc, s))}" for s in pc.get("skill_proficiencies", []))
    lines = [line, ab, f"Prof {fmt_mod(pc_prof(pc))} · Init {fmt_mod(pc_init_bonus(pc))} · Speed {pc.get('speed', 30)} ft."
             f" · Hit dice {pc['hit_dice']['max'] - pc['hit_dice']['used']}/{pc['hit_dice']['max']} d{pc['hit_dice']['die']}",
             f"Saves: {saves}"]
    if profs:
        lines.append(f"Skills: {profs}")
    dc = pc_spell_dc(pc)
    if dc:
        lines.append(f"Spell save DC {dc} · spell attack {fmt_mod(dc - 8)} ({pc['spellcasting_ability'].upper()})")
    if pc.get("spell_slots"):
        lines.append("Slots: " + ", ".join(
            f"{slot_label(k, v)} {v['max'] - v['used']}/{v['max']}" for k, v in sorted(pc["spell_slots"].items())))
    if pc.get("spells"):
        lines.append("Spells: " + ", ".join(pc["spells"]))
    if pc.get("attacks"):
        lines.append("Attacks: " + "; ".join(
            f"{a['name']} {fmt_mod(a.get('to_hit', 0))} ({', '.join(d[0] + ' ' + d[1] for d in a.get('damage', []))})" for a in pc["attacks"]))
    if pc.get("resources"):
        lines.append("Resources: " + ", ".join(
            f"{k} {v['max'] - v['used']}/{v['max']} ({v.get('reset', 'long')})" for k, v in pc["resources"].items()))
    if pc.get("features"):
        lines.append("Features: " + ", ".join(pc["features"]))
    eq = pc.get("equipped") or {}
    worn = [x for x in (eq.get("armor"), eq.get("shield")) if x] + list(eq.get("worn") or [])
    if worn or pc.get("attuned"):
        lines.append("Equipped: " + (", ".join(worn) or "nothing")
                     + (f" · Attuned ({len(pc['attuned'])}/3): {', '.join(pc['attuned'])}" if pc.get("attuned") else ""))
    if pc.get("senses") or pc.get("resist"):
        lines.append(" · ".join(filter(None, [", ".join(pc.get("senses") or []),
                                              ("Resists " + ", ".join(pc["resist"])) if pc.get("resist") else ""])))
    if pc.get("inventory"):
        lines.append("Inventory: " + ", ".join(pc["inventory"]))
    lines.append(f"Gold: {pc.get('gold', 0):g} gp · XP: {pc.get('xp', 0)}")
    if pc.get("notes"):
        lines.append(f"Notes: {pc['notes']}")
    return "\n".join(lines)


def rest(pc: dict, kind: str) -> list[str]:
    """Apply a short or long rest; returns a list of what changed."""
    changes = []
    if kind == "long":
        if pc["hp"]["current"] < pc["hp"]["max"]:
            changes.append(f"HP {pc['hp']['current']}→{pc['hp']['max']}")
        pc["hp"]["current"] = pc["hp"]["max"]
        pc["hp"]["temp"] = 0
        hd = pc["hit_dice"]
        regain = max(1, hd["max"] // 2)
        if hd["used"]:
            new_used = max(0, hd["used"] - regain)
            changes.append(f"hit dice regained {hd['used'] - new_used}")
            hd["used"] = new_used
        for slot in pc.get("spell_slots", {}).values():
            slot["used"] = 0
        if pc.get("spell_slots"):
            changes.append("spell slots restored")
        pc["death_saves"] = {"success": 0, "fail": 0}
        exh = [c for c in pc.get("conditions", []) if (c.get("name") if isinstance(c, dict) else c) == "exhaustion"]
        if exh:
            c = exh[0]
            if isinstance(c, dict) and c.get("level", 1) > 1:
                c["level"] -= 1
                changes.append(f"exhaustion → {c['level']}")
            else:
                pc["conditions"] = [x for x in pc["conditions"] if x is not c]
                changes.append("exhaustion removed")
    else:
        for slot in pc.get("spell_slots", {}).values():
            if slot.get("pact"):
                slot["used"] = 0
                changes.append("pact slots restored")
    for name, res in pc.get("resources", {}).items():
        if res.get("used") and (res.get("reset") == "short" or kind == "long"):
            res["used"] = 0
            changes.append(f"{name} restored")
    return changes
