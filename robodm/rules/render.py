"""Turn 5etools entry trees and inline {@tags} into plain readable text."""

from __future__ import annotations

import re
from typing import Any

_TAG_RE = re.compile(r"\{@(\w+)(?:\s([^{}]*?))?\}")

ATK_TYPES = {
    "mw": "Melee Weapon Attack:",
    "rw": "Ranged Weapon Attack:",
    "mw,rw": "Melee or Ranged Weapon Attack:",
    "rw,mw": "Melee or Ranged Weapon Attack:",
    "ms": "Melee Spell Attack:",
    "rs": "Ranged Spell Attack:",
    "ms,rs": "Melee or Ranged Spell Attack:",
    "m": "Melee Attack:",
    "r": "Ranged Attack:",
    "a": "Area Attack:",
    "aw": "Area Weapon Attack:",
}

# Tags whose display text is the third pipe segment (name|source|display).
_REF_TAGS = {
    "spell", "item", "creature", "condition", "disease", "status", "background", "race", "optfeature",
    "class", "classFeature", "subclassFeature", "feat", "reward", "psionic", "object", "boon", "cult",
    "trap", "hazard", "deity", "variantrule", "vehicle", "vehupgrade", "action", "language", "sense",
    "skill", "table", "card", "deck", "legroup", "itemProperty", "itemMastery", "charoption", "recipe",
    "facility", "subclass",
}


def _render_tag(tag: str, text: str | None) -> str:
    text = text or ""
    parts = text.split("|")
    first = parts[0]
    if tag in ("b", "bold", "i", "italic", "u", "underline", "s", "strike", "s2", "strikeDouble", "u2",
               "underlineDouble", "sup", "sub", "kbd", "code", "note", "tip", "style", "font", "help",
               "unit", "highlight", "comic", "comicH1", "comicH2", "comicH3", "comicH4", "comicNote"):
        return first
    if tag == "color":
        return first
    if tag == "h":
        return "Hit: "
    if tag == "m":
        return "Miss: "
    if tag == "hom":
        return "Hit or Miss: "
    if tag == "atk":
        return ATK_TYPES.get(first.strip(), "Attack:")
    if tag == "atkr":
        return ATK_TYPES.get(first.strip(), "Attack Roll:").replace("Attack:", "Attack Roll:")
    if tag == "hit":
        try:
            n = int(first)
            return f"+{n}" if n >= 0 else str(n)
        except ValueError:
            return first
    if tag == "d20":
        if len(parts) > 1 and parts[1]:
            return parts[1]
        try:
            n = int(first)
            return f"+{n}" if n >= 0 else str(n)
        except ValueError:
            return first
    if tag == "dc":
        return f"DC {parts[1] if len(parts) > 1 and parts[1] else first}"
    if tag in ("damage", "dice", "autodice"):
        return parts[1] if len(parts) > 1 and parts[1] else first
    if tag in ("scaledamage", "scaledice"):
        return parts[2] if len(parts) > 2 and parts[2] else first
    if tag == "recharge":
        if not first:
            return "(Recharge 6)"
        return f"(Recharge {first}–6)" if first != "6" else "(Recharge 6)"
    if tag == "chance":
        return parts[1] if len(parts) > 1 and parts[1] else f"{first} percent"
    if tag == "quickref":
        return parts[4] if len(parts) > 4 and parts[4] else first
    if tag == "classFeature":
        return parts[5] if len(parts) > 5 and parts[5] else first
    if tag == "subclassFeature":
        return parts[7] if len(parts) > 7 and parts[7] else first
    if tag in ("filter", "link", "5etools", "book", "adventure", "loader", "area", "homebrew", "footnote", "coinflip"):
        return first
    if tag == "actSave":
        return f"{first.capitalize()} Saving Throw:"
    if tag == "actSaveFail":
        return "Failure:"
    if tag == "actSaveSuccess":
        return "Success:"
    if tag == "actSaveSuccessOrFail":
        return "Failure or Success:"
    if tag == "actTrigger":
        return "Trigger:"
    if tag == "actResponse":
        return "Response:"
    if tag in _REF_TAGS:
        if len(parts) >= 3 and parts[2]:
            return parts[2]
        return first
    return first


def strip_tags(text: str) -> str:
    """Replace {@tag ...} markup (innermost first, so nesting works)."""
    prev = None
    while prev != text:
        prev = text
        text = _TAG_RE.sub(lambda m: _render_tag(m.group(1), m.group(2)), text)
    return text


def render(entry: Any, depth: int = 0) -> str:
    """Render one entry (string or typed dict) to text."""
    return "\n".join(_render(entry, depth)).strip()


def render_entries(entries: Any, depth: int = 0) -> str:
    if entries is None:
        return ""
    if not isinstance(entries, list):
        entries = [entries]
    blocks = []
    for e in entries:
        txt = render(e, depth)
        if txt:
            blocks.append(txt)
    return "\n\n".join(blocks)


def _named(name: str | None, body: str, depth: int) -> list[str]:
    if not name:
        return [body] if body else []
    name = strip_tags(name)
    if depth <= 0:
        return [f"### {name}", body] if body else [f"### {name}"]
    if "\n" in body:
        return [f"**{name}.**", body]
    return [f"**{name}.** {body}".rstrip()]


def _render(e: Any, depth: int) -> list[str]:
    if e is None:
        return []
    if isinstance(e, str):
        return [strip_tags(e)]
    if isinstance(e, (int, float)):
        return [str(e)]
    if isinstance(e, list):
        return [render_entries(e, depth)]
    if not isinstance(e, dict):
        return [str(e)]

    t = e.get("type", "entries")
    name = e.get("name")

    if t in ("entries", "section", "chapter", "inset", "insetReadaloud", "variant", "variantSub",
             "variantInner", "optfeature", "item", "itemSub", "itemSpell", "actions", "options", "flowBlock",
             "homebrew"):
        if t == "item" and "entry" in e:
            body = render(e["entry"], depth + 1)
        else:
            body = render_entries(e.get("entries", e.get("items", [])), depth + 1)
        if t == "insetReadaloud":
            body = "\n".join("> " + line if line else ">" for line in body.splitlines())
        if t == "variant" and name:
            name = f"Variant: {name}"
        prereq = e.get("prerequisite")
        if prereq and isinstance(prereq, str):
            body = f"*Prerequisite: {strip_tags(prereq)}*\n" + body
        return _named(name, body, depth)

    if t in ("inline", "inlineBlock"):
        return ["".join(render(x, depth) for x in e.get("entries", []))]

    if t == "list":
        lines = []
        for it in e.get("items", []):
            txt = render(it, depth + 1)
            first, *rest = txt.split("\n") if txt else [""]
            lines.append(f"- {first}")
            lines.extend(f"  {r}" for r in rest if r.strip())
        return _named(name, "\n".join(lines), depth) if name else ["\n".join(lines)]

    if t == "table":
        return [_render_table(e)]

    if t == "tableGroup":
        return [_render_table(tbl) for tbl in e.get("tables", [])]

    if t == "quote":
        body = render_entries(e.get("entries", []), depth)
        by = f"\n— {strip_tags(e['by'])}" if e.get("by") else ""
        return ["> " + body.replace("\n", "\n> ") + by]

    if t == "abilityDc":
        attrs = " or ".join(a.upper() for a in e.get("attributes", []))
        return [f"**{strip_tags(name or 'Spell')} save DC** = 8 + proficiency bonus + {attrs} modifier"]

    if t == "abilityAttackMod":
        attrs = " or ".join(a.upper() for a in e.get("attributes", []))
        return [f"**{strip_tags(name or 'Spell')} attack modifier** = proficiency bonus + {attrs} modifier"]

    if t == "abilityGeneric":
        return [strip_tags(e.get("text", ""))]

    if t == "spellcasting":
        return [render_spellcasting(e)]

    if t in ("refClassFeature", "refSubclassFeature", "refOptionalfeature", "refFeat"):
        ref = e.get("classFeature") or e.get("subclassFeature") or e.get("optionalfeature") or e.get("feat") or ""
        return [f"(see feature: {ref.split('|')[0]})"]

    if t in ("image", "gallery", "hr", "pagebreak", "flowchart", "statblockInline"):
        if t == "flowchart":
            return [render_entries(e.get("blocks", []), depth)]
        return []

    if t == "statblock":
        return [f"(stat block: {e.get('name', '?')} — use `rules show \"{e.get('name', '')}\"`)"]

    if t == "link":
        return [strip_tags(e.get("text", ""))]

    if t == "cell":
        return [_cell_text(e)]

    if t in ("dice", "bonus", "bonusSpeed"):
        if t == "dice":
            return [" + ".join(f"{d.get('number', 1)}d{d.get('faces')}" for d in e.get("toRoll", []))]
        v = e.get("value", 0)
        return [f"+{v}" if v >= 0 else str(v)]

    # Unknown type: render whatever text we can find.
    if "entries" in e:
        return _named(name, render_entries(e["entries"], depth + 1), depth)
    if "entry" in e:
        return _named(name, render(e["entry"], depth + 1), depth)
    return []


def _cell_text(c: Any) -> str:
    if isinstance(c, dict):
        if c.get("type") == "cell" and "roll" in c:
            r = c["roll"]
            if "exact" in r:
                return str(r["exact"])
            lo, hi = r.get("min"), r.get("max")
            return f"{lo}–{hi}" if hi is not None else f"{lo}+"
        if "entry" in c:
            return render(c["entry"]).replace("\n", " ")
        return render(c).replace("\n", " ")
    return strip_tags(str(c))


def _render_table(t: dict) -> str:
    lines = []
    if t.get("caption"):
        lines.append(f"**{strip_tags(t['caption'])}**")
    cols = [strip_tags(str(c)) for c in t.get("colLabels", [])]
    if cols:
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
    for row in t.get("rows", []):
        if isinstance(row, dict):
            row = row.get("row", [])
        lines.append("| " + " | ".join(_cell_text(c) for c in row) + " |")
    for foot in t.get("footnotes", []) or []:
        lines.append(render(foot))
    return "\n".join(lines)


_SPELL_FREQ = {
    "will": "At will",
    "daily": "{n}/day",
    "rest": "{n}/rest",
    "weekly": "{n}/week",
    "monthly": "{n}/month",
    "yearly": "{n}/year",
    "charges": "{n} charges",
    "recharge": "Recharge",
    "legendary": "Legendary",
    "restLong": "{n}/long rest",
}


def render_spellcasting(sc: dict) -> str:
    lines = []
    name = strip_tags(sc.get("name", "Spellcasting"))
    header = render_entries(sc.get("headerEntries", []), 1)
    lines.append(f"**{name}.** {header}".strip())
    if sc.get("will"):
        lines.append(f"- At will: {', '.join(strip_tags(s) for s in sc['will'])}")
    for freq in ("daily", "rest", "restLong", "weekly", "monthly", "yearly", "charges"):
        block = sc.get(freq)
        if not block:
            continue
        for key in sorted(block):
            n = key.rstrip("e")
            each = " each" if key.endswith("e") else ""
            label = _SPELL_FREQ[freq].format(n=n) + each
            lines.append(f"- {label}: {', '.join(strip_tags(s) for s in block[key])}")
    spells = sc.get("spells") or {}
    for lvl in sorted(spells, key=lambda x: int(x)):
        info = spells[lvl]
        names = ", ".join(strip_tags(s) for s in info.get("spells", []))
        if lvl == "0":
            lines.append(f"- Cantrips (at will): {names}")
        else:
            slots = info.get("slots")
            lower = info.get("lower")
            lv = f"{lower}–{lvl}" if lower else lvl
            slot_txt = f" ({slots} slot{'s' if slots != 1 else ''})" if slots is not None else ""
            lines.append(f"- Level {lv}{slot_txt}: {names}")
    footer = render_entries(sc.get("footerEntries", []), 1)
    if footer:
        lines.append(footer)
    return "\n".join(lines)
