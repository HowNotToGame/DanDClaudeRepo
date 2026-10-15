"""Long-term campaign memory: durable facts, full-campaign search, and the session brief.

Memory is plain files inside the campaign folder, so it persists across Claude
sessions, context compaction and devices (commit/push the campaign folder).
The brief is a compact digest the DM loads at the start of every session; the
search finds anything older on demand.
"""

from __future__ import annotations

import re

from robodm import context_blocks, lore
from robodm.campaign import Campaign, pc_summary
from robodm.util import RoboDMError, append_jsonl, now_iso, rank_documents, read_json, read_jsonl, truncate, write_text

MEMORY_TYPES = ["event", "choice", "npc", "location", "item", "quest", "lore", "rule", "secret", "relationship", "other"]


def _memory_file(camp: Campaign):
    return camp.path("memory.jsonl")


def all_memories(camp: Campaign) -> list[dict]:
    return read_jsonl(_memory_file(camp))


def add(camp: Campaign, text: str, mtype: str = "event", tags: list[str] | None = None, important: bool = False) -> dict:
    mtype = mtype.lower()
    if mtype not in MEMORY_TYPES:
        raise RoboDMError(f"Memory type must be one of: {', '.join(MEMORY_TYPES)}")
    existing = all_memories(camp)
    next_id = 1 + max((int(m["id"][1:]) for m in existing if re.fullmatch(r"m\d+", m.get("id", ""))), default=0)
    rec = {
        "id": f"m{next_id:04d}",
        "ts": now_iso(),
        "session": camp.session.get("number", 0),
        "type": mtype,
        "text": text.strip(),
        "tags": [t.strip().lower() for t in tags or [] if t.strip()],
    }
    if important:
        rec["important"] = True
    append_jsonl(_memory_file(camp), rec)
    return rec


def update(camp: Campaign, mem_id: str, text: str | None = None, important: bool | None = None,
           forget: bool = False) -> dict:
    mems = all_memories(camp)
    found = None
    for m in mems:
        if m["id"] == mem_id:
            found = m
            if text:
                m["text"] = text
                m["edited"] = now_iso()
            if important is not None:
                m["important"] = important
            if forget:
                m["retired"] = True
    if not found:
        raise RoboDMError(f"No memory {mem_id}.")
    import json

    write_text(_memory_file(camp), "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in mems))
    return found


def list_memories(camp: Campaign, mtype: str | None = None, recent: int | None = None, tag: str | None = None,
                  include_retired: bool = False) -> list[dict]:
    mems = [m for m in all_memories(camp) if include_retired or not m.get("retired")]
    if mtype:
        mems = [m for m in mems if m["type"] == mtype]
    if tag:
        mems = [m for m in mems if tag.lower() in m.get("tags", [])]
    if recent:
        mems = mems[-recent:]
    return mems


def fmt_memory(m: dict) -> str:
    star = "★ " if m.get("important") else ""
    tags = f" #{' #'.join(m['tags'])}" if m.get("tags") else ""
    return f"{m['id']} [s{m.get('session', 0)} {m['type']}] {star}{m['text']}{tags}"


def search(camp: Campaign, query: str, limit: int = 12, scope: str = "all") -> list[dict]:
    """Rank everything the campaign knows: memories, journals, lore, party sheets, quests."""
    docs: list[dict] = []
    if scope in ("all", "memory"):
        for m in all_memories(camp):
            if m.get("retired"):
                continue
            docs.append({"kind": "memory", "ref": m["id"], "text": f"{m['text']} {' '.join(m.get('tags', []))} {m['type']}",
                         "show": fmt_memory(m)})
    if scope in ("all", "journal"):
        jdir = camp.path("journal")
        if jdir.exists():
            for p in sorted(jdir.glob("*.md")):
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if len(line) > 12 and not line.startswith("#"):
                        docs.append({"kind": "journal", "ref": p.stem, "text": line, "show": f"{p.stem}: {truncate(line, 220)}"})
    if scope in ("all", "lore"):
        for e in lore.all_entries(camp):
            body = e["body"]
            docs.append({"kind": "lore", "ref": e["slug"],
                         "text": f"{e['meta'].get('name', '')} {e['meta'].get('name', '')} {e['meta'].get('summary', '')} "
                                 f"{' '.join(map(str, e['meta'].get('tags', []) or []))} {body}",
                         "show": f"lore/{e['meta'].get('type', '?')}/{e['slug']}: {e['meta'].get('name')} — "
                                 f"{truncate(e['meta'].get('summary') or body, 180)}"})
    if scope in ("all", "party"):
        for pc in camp.party():
            docs.append({"kind": "pc", "ref": pc["slug"],
                         "text": f"{pc['name']} {pc.get('race', '')} {pc.get('class', '')} {pc.get('notes', '')} "
                                 f"{' '.join(pc.get('inventory', []))} {' '.join(pc.get('features', []))}",
                         "show": f"PC {pc['name']}: {truncate(pc.get('notes', '') or pc.get('class', ''), 160)}"})
    if scope in ("all", "quests"):
        for q in camp.quests():
            docs.append({"kind": "quest", "ref": q["id"], "text": f"{q['title']} {q.get('notes', '')}",
                         "show": f"quest {q['id']} [{q['status']}] {q['title']} — {truncate(q.get('notes', ''), 140)}"})
    return rank_documents(query, docs, limit=limit)


# ---------------------------------------------------------------- quests

def quest_add(camp: Campaign, title: str, notes: str = "", giver: str = "") -> dict:
    qs = camp.quests()
    next_id = 1 + max((int(q["id"][1:]) for q in qs if re.fullmatch(r"q\d+", q.get("id", ""))), default=0)
    q = {"id": f"q{next_id:03d}", "title": title, "status": "open", "notes": notes, "giver": giver,
         "opened_session": camp.session.get("number", 0), "updates": []}
    qs.append(q)
    camp.save_quests(qs)
    return q


def quest_find(qs: list[dict], key: str) -> dict:
    for q in qs:
        if q["id"] == key:
            return q
    low = key.lower()
    matches = [q for q in qs if low in q["title"].lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RoboDMError(f"No quest '{key}'.")
    raise RoboDMError(f"'{key}' matches several quests: {', '.join(q['id'] for q in matches)}")


def quest_update(camp: Campaign, key: str, status: str | None = None, note: str | None = None) -> dict:
    qs = camp.quests()
    q = quest_find(qs, key)
    if status:
        if status not in ("open", "done", "failed", "abandoned"):
            raise RoboDMError("Status must be open, done, failed or abandoned.")
        q["status"] = status
        if status != "open":
            q["closed_session"] = camp.session.get("number", 0)
    if note:
        q.setdefault("updates", []).append({"session": camp.session.get("number", 0), "note": note})
    camp.save_quests(qs)
    return q


def fmt_quest(q: dict) -> str:
    upd = q.get("updates") or []
    last = f" — latest: {upd[-1]['note']}" if upd else ""
    giver = f" (from {q['giver']})" if q.get("giver") else ""
    return f"{q['id']} [{q['status']}] **{q['title']}**{giver}{': ' + q['notes'] if q.get('notes') else ''}{last}"


# ---------------------------------------------------------------- brief

def brief(camp: Campaign, recent: int = 15) -> str:
    d = camp.data
    s = camp.settings
    sess = camp.session
    lines = [f"# 📜 {camp.name}"]
    status = f"session {sess.get('number', 0)} in progress" if sess.get("active") else f"next session: {sess.get('number', 0) + 1}"
    lines.append(f"*{status} · campaign folder: campaigns/{camp.slug}*")
    for key, label in (("premise", "Premise"), ("setting", "Setting"), ("tone", "Tone")):
        if d.get(key):
            lines.append(f"**{label}:** {d[key]}")
    lines.append("**Table settings:** " + ", ".join(
        f"{k}={','.join(v) if isinstance(v, list) else v}" for k, v in s.items()))
    if d.get("dm_notes"):
        lines.append(f"**DM notes (secret):** {d['dm_notes']}")

    party = camp.party()
    lines.append("\n## Party")
    if party:
        lines.extend(f"- {pc_summary(pc)}" for pc in party)
    else:
        lines.append("- (no characters yet — run session zero / `./dm pc add`)")

    summaries = camp.session_summaries(limit=2)
    if summaries:
        lines.append("\n## Previously")
        for n, text in reversed(summaries):
            lines.append(f"**Session {n}:** {text}")
    log = camp.current_log(tail=20) if sess.get("active") else []
    if log:
        lines.append("\n## This session so far")
        lines.extend(log)

    quests = [q for q in camp.quests() if q["status"] == "open"]
    if quests:
        lines.append("\n## Open quests")
        lines.extend(f"- {fmt_quest(q)}" for q in quests)

    mems = list_memories(camp)
    important = [m for m in mems if m.get("important")]
    recent_mems = [m for m in mems[-recent:] if not m.get("important")]
    if important:
        lines.append("\n## Key facts (★ always remember)")
        lines.extend(f"- {fmt_memory(m)}" for m in important[-25:])
    if recent_mems:
        lines.append("\n## Recent memories")
        lines.extend(f"- {fmt_memory(m)}" for m in recent_mems)
    if len(mems) > recent + len(important):
        lines.append(f"_({len(mems)} memories total — search older ones with `./dm memory search <words>`)_")

    entries = lore.all_entries(camp)
    if entries:
        lines.append("\n## World lore index")
        by_type: dict[str, list[str]] = {}
        for e in entries:
            by_type.setdefault(e["meta"].get("type", "other"), []).append(e["meta"].get("name", e["slug"]))
        for t in sorted(by_type):
            lines.append(f"- **{t}:** {', '.join(sorted(by_type[t]))}")
        npcs = [e for e in entries if e["meta"].get("type") == "npc" and e["meta"].get("summary")]
        if npcs:
            lines.append("\n**NPCs at a glance:** " + "; ".join(
                f"{e['meta']['name']} — {truncate(str(e['meta']['summary']), 90)}"
                + (f" [{e['meta']['status']}]" if e["meta"].get("status") and e["meta"]["status"] != "alive" else "")
                for e in npcs[:30]))

    blocks = context_blocks.active_blocks(camp)
    if blocks:
        lines.append("\n## Active instruction blocks")
        lines.append(", ".join(f"{b['name']} ({b['meta'].get('scope', 'always')})" for b in blocks)
                     + " — load the text with `./dm context render`.")
    combat = read_json(camp.path("combat.json"))
    if combat and combat.get("active"):
        lines.append(f"\n## ⚔️ Combat in progress: {combat.get('name') or 'unnamed'} (round {combat.get('round')})"
                     " — see `./dm combat status`.")
    return "\n".join(lines)
