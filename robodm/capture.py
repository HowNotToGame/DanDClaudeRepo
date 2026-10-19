"""Automatic memory capture, so nothing depends on the DM remembering to write it down.

* Transcript: during a play session, the hooks append every player message (UserPromptSubmit) and every DM
  reply (Stop) to journal/transcript-NNN.md. It's committed with the campaign, searched by `./dm recall`,
  and its last exchanges go into the brief, so the DM picks up mid-scene after compaction.
* Unrecorded names: the DM bolds new NPCs and places. Bold names in a reply that appear nowhere in lore,
  memory, the journals, the party or quests are remembered, and the next prompt reminds the DM to record them.
"""

from __future__ import annotations

import re

from robodm.campaign import Campaign
from robodm.util import now_iso, read_json, read_jsonl, truncate, write_json, write_text

MAX_ENTRY = 8000
PRIVATE = "🔒"            # transcript entries between the DM and one player start with "🔒 [private Mira→DM]"
_PRIVATE = re.compile(r"^🔒 \[private (?:(.+?)→DM|DM→(.+?))\]")


def private_to(text: str) -> str | None:
    """The player a private transcript entry belongs to, or None for a public one."""
    m = _PRIVATE.match((text or "").lstrip())
    return (m.group(1) or m.group(2)).strip() if m else None


def visible(text: str, dm: bool = False, name: str | None = None) -> bool:
    """Can this viewer see the entry? The DM sees everything; a player sees public entries and their own."""
    who = private_to(text)
    return dm or who is None or (name is not None and who.lower() == name.lower())
_HEAD = re.compile(r"^### \[([^\]]+)\] (Player|DM)\s*$", re.MULTILINE)
_BOLD = re.compile(r"\*\*([^*\n]{2,60})\*\*")
_SMALL = {"of", "the", "and", "in", "on", "at", "a", "an", "de", "du", "von", "van", "la", "le", "del", "da", "to"}
_NOT_NAMES = {"hit", "miss", "success", "failure", "critical hit", "you", "yes", "no", "dm", "ooc", "note",
              "warning", "previously", "round", "turn", "advantage", "disadvantage", "initiative", "dead", "down",
              "stable", "bloodied", "what do you do", "what do you do?"}


def transcript_path(camp: Campaign, number: int | None = None):
    n = number if number is not None else camp.session.get("number", 0)
    return camp.path("journal", f"transcript-{n:03d}.md")


def entries(camp: Campaign, number: int | None = None) -> list[tuple[str, str, str]]:
    """(time, who, text) for each transcript entry of a session (default: the current one)."""
    p = transcript_path(camp, number)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    heads = list(_HEAD.finditer(text))
    return [(h.group(1), h.group(2), text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)].strip())
            for i, h in enumerate(heads)]


def record(camp: Campaign, who: str, text: str) -> bool:
    """Append one transcript entry if a play session is running. Skips empty text and exact repeats."""
    text = (text or "").strip()
    if not text or not camp.session.get("active"):
        return False
    prev = entries(camp)
    if prev and prev[-1][1] == who and prev[-1][2] == truncate(text, MAX_ENTRY):
        return False
    p = transcript_path(camp)
    head = p.read_text(encoding="utf-8") if p.exists() else f"# Session {camp.session.get('number', 0)} transcript\n"
    write_text(p, head.rstrip("\n") + f"\n\n### [{now_iso()[11:16]}] {who}\n{truncate(text, MAX_ENTRY)}\n")
    return True


def last_exchanges(camp: Campaign, n: int = 4, width: int = 500, public: bool = False) -> list[str]:
    rows = [r for r in entries(camp) if not public or private_to(r[2]) is None]
    return [f"**{who}** [{t}]: {truncate(' '.join(text.split()), width)}" for t, who, text in rows[-n:]]


# =============================================================================== unrecorded names

def _known_text(camp: Campaign) -> str:
    """Everything already on record, lowercased (transcripts excluded: being said isn't being recorded)."""
    from robodm import lore

    parts = [m["text"] for m in read_jsonl(camp.path("memory.jsonl"))]
    for e in lore.all_entries(camp):
        parts.extend([str(e["meta"].get("name", "")), str(e["meta"].get("aliases", "")), str(e["meta"].get("summary", "")),
                      e["body"]])
    jdir = camp.path("journal")
    if jdir.exists():
        parts.extend(p.read_text(encoding="utf-8") for p in jdir.glob("*.md") if not p.name.startswith("transcript-"))
    parts.extend(f"{pc['name']} {pc.get('notes', '')}" for pc in camp.party())
    parts.extend(f"{q['title']} {q.get('notes', '')} {q.get('giver', '')}" for q in camp.quests())
    parts.extend([str(camp.data.get(k, "")) for k in ("name", "premise", "setting", "dm_notes")])
    return "\n".join(parts).lower()


def candidate_names(text: str) -> list[str]:
    """Bold text that looks like a proper name: 1–6 capitalised words (small joining words allowed)."""
    out: list[str] = []
    for m in _BOLD.finditer(text or ""):
        name = m.group(1).strip(" .,:;!?'\"()[]—-")
        words = name.split()
        if not 1 <= len(words) <= 6 or any(ch.isdigit() for ch in name) or name.isupper():
            continue
        if not words[0][:1].isupper() or any(not (w[:1].isupper() or w.lower() in _SMALL) for w in words):
            continue
        if name.lower() in _NOT_NAMES or name in out:
            continue
        out.append(name)
    return out


def unrecorded_names(camp: Campaign, text: str, db=None) -> list[str]:
    names = candidate_names(text)
    if not names:
        return []
    known = _known_text(camp)
    left = [n for n in names if n.lower() not in known]
    if db is not None and getattr(db, "available", False):
        left = [n for n in left if not db.find_exact(n)]   # spells, monsters, items, conditions…
    return left


def _state_path(camp: Campaign):
    return camp.path(".cache", "capture.json")


def remember_unrecorded(camp: Campaign, names: list[str]) -> None:
    st = read_json(_state_path(camp)) or {}
    pending = [n for n in st.get("unrecorded", []) if n not in names] + names
    st["unrecorded"] = pending[-20:]
    write_json(_state_path(camp), st)


def pop_unrecorded(camp: Campaign) -> list[str]:
    """Names still unrecorded since the last reminder (re-checked, so ones recorded since drop out)."""
    st = read_json(_state_path(camp)) or {}
    names = st.get("unrecorded", [])
    if not names:
        return []
    st["unrecorded"] = []
    write_json(_state_path(camp), st)
    known = _known_text(camp)
    return [n for n in names if n.lower() not in known]
