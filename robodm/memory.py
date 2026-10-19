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
from robodm.util import RoboDMError, append_jsonl, now_iso, read_json, read_jsonl, truncate, write_text

MEMORY_TYPES = ["event", "choice", "npc", "location", "item", "quest", "lore", "rule", "secret", "relationship", "world",
                "rumor", "other"]


def _memory_file(camp: Campaign):
    return camp.path("memory.jsonl")


def all_memories(camp: Campaign) -> list[dict]:
    return read_jsonl(_memory_file(camp))


def add(camp: Campaign, text: str, mtype: str = "event", tags: list[str] | None = None, important: bool = False,
        known_by: list[str] | None = None) -> dict:
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
    if known_by:
        from robodm.util import slugify

        rec["known_by"] = sorted({slugify(k) for k in known_by if k.strip()})
    append_jsonl(_memory_file(camp), rec)
    return rec


def update(camp: Campaign, mem_id: str, text: str | None = None, important: bool | None = None,
           forget: bool = False, known_by: list[str] | None = None) -> dict:
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
            if known_by:
                from robodm.util import slugify

                m["known_by"] = sorted(set(m.get("known_by") or []) | {slugify(k) for k in known_by if k.strip()})
    if not found:
        raise RoboDMError(f"No memory {mem_id}.")
    import json

    write_text(_memory_file(camp), "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in mems))
    return found


def list_memories(camp: Campaign, mtype: str | None = None, recent: int | None = None, tag: str | None = None,
                  include_retired: bool = False, important: bool = False) -> list[dict]:
    mems = [m for m in all_memories(camp) if include_retired or not m.get("retired")]
    if important:
        mems = [m for m in mems if m.get("important")]
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


def search(camp: Campaign, query: str | list[str], limit: int = 12, scope: str = "all") -> list[dict]:
    """Rank everything the campaign knows (memories, journals, transcripts, lore, sources, party, quests).
    Pass several phrasings to search by meaning; see robodm/recall.py."""
    from robodm import recall

    return recall.search(camp, query, limit=limit, scope=scope)


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

BRIEF_BUDGET = 9000  # characters; the brief is reloaded every session and after every compaction


def _section(prio: int, title: str, lines: list[str], keep: str = "head", min_lines: int = 0, more: str = "") -> dict:
    """prio 0 is never trimmed; higher numbers are trimmed first. keep='tail' trims the oldest lines first."""
    return {"prio": prio, "title": title, "lines": [x for x in lines if x is not None], "keep": keep,
            "min": min_lines, "more": more, "cut": 0}


def _size(sections: list[dict]) -> int:
    return sum(len(s["title"]) + 2 + sum(len(x) + 1 for x in s["lines"]) for s in sections if s["lines"])


def _fit(sections: list[dict], budget: int) -> None:
    for prio in sorted({s["prio"] for s in sections if s["prio"] > 0}, reverse=True):
        for sec in [s for s in sections if s["prio"] == prio]:
            while _size(sections) > budget and len(sec["lines"]) > sec["min"]:
                sec["lines"].pop(0 if sec["keep"] == "tail" else -1)
                sec["cut"] += 1
        if _size(sections) <= budget:
            return


def _scene_query(camp: Campaign) -> str:
    """Words describing what's happening now: recent log lines, open quests, who's in the fight."""
    from robodm import capture

    bits = [ln.split("] ", 1)[-1] for ln in camp.current_log(tail=8)] if camp.session.get("active") else []
    bits += [text for _, _, text in capture.entries(camp)[-2:]] if camp.session.get("active") else []
    bits += [q["title"] for q in camp.quests() if q["status"] == "open"]
    combat = read_json(camp.path("combat.json")) or {}
    if combat.get("active"):
        bits += [combat.get("name", "")] + [c["name"] for c in combat.get("combatants", {}).values()]
    return " ".join(bits)[-1500:]


def brief(camp: Campaign, recent: int = 15, budget: int | str | None = "auto", full: bool = False) -> str:
    """The session digest. Sections are tiered by priority and trimmed to `budget` characters, lowest priority
    first; every trimmed section says how to get the rest. `full=True` keeps everything untrimmed."""
    from robodm import audit, capture

    if budget == "auto":                 # sized by the campaign's turn_speed (robodm/costs.py)
        from robodm import costs

        budget = costs.brief_budget(camp)
    w = (lambda text, n: str(text)) if full else (lambda text, n: truncate(str(text), n))
    d = camp.data
    s = camp.settings
    sess = camp.session
    status = f"session {sess.get('number', 0)} in progress" if sess.get("active") else f"next session: {sess.get('number', 0) + 1}"
    from robodm import world

    wd = world.load(camp)
    head = [f"*{status} · campaign folder: campaigns/{camp.slug}*"]
    if world.tracking(wd):
        head.insert(0, f"🕰 **{world.fmt_time(wd)}**")
    for key, label in (("premise", "Premise"), ("setting", "Setting"), ("tone", "Tone")):
        if d.get(key):
            head.append(f"**{label}:** {w(d[key], 400)}")
    head.append("**Table settings:** " + ", ".join(
        f"{k}={','.join(v) if isinstance(v, list) else v}" for k, v in s.items()))
    if d.get("dm_notes"):
        head.append(f"**DM notes (secret):** {w(d['dm_notes'], 800)}")
    sections = [_section(0, f"# 📜 {camp.name}", head)]

    party = camp.party()
    sections.append(_section(0, "## Party", [f"- {pc_summary(pc)}" for pc in party] if party else
                             ["- (no characters yet — run session zero / `./dm pc add`)"]))
    scene_lines, world_lines = world.brief_lines(camp, dm=True)
    if scene_lines:
        sections.append(_section(0, "## Scene", scene_lines, min_lines=len(scene_lines)))
    if world_lines:
        sections.append(_section(1, "## The world moves (clocks, factions, timers)", world_lines, min_lines=4,
                                 more="`./dm clock list` · `./dm faction` · `./dm world`"))
    combat = read_json(camp.path("combat.json"))
    if combat and combat.get("active"):
        sections.append(_section(0, f"## ⚔️ Combat in progress: {combat.get('name') or 'unnamed'} "
                                    f"(round {combat.get('round')})", ["See `./dm combat status`."]))
    if sess.get("active"):
        sections.append(_section(1, "## Last exchanges (transcript)", capture.last_exchanges(camp, n=4, width=600),
                                 keep="tail", min_lines=2, more="`./dm session transcript`"))
        sections.append(_section(1, "## This session so far", [w(x, 240) for x in camp.current_log(tail=30)],
                                 keep="tail", min_lines=6, more="`./dm session show`"))
    quests = [q for q in camp.quests() if q["status"] == "open"]
    if quests:
        sections.append(_section(1, "## Open quests", [f"- {w(fmt_quest(q), 200)}" for q in quests], min_lines=6,
                                 more="`./dm quest list`"))
    mems = list_memories(camp)
    important = [m for m in mems if m.get("important")]
    if important:
        sections.append(_section(1, "## Key facts (★ always remember)",
                                 [f"- {w(fmt_memory(m), 200)}" for m in reversed(important)], min_lines=10,
                                 more="`./dm memory list --important`"))
    summaries = camp.session_summaries(limit=4)
    if summaries:
        lines = [f"**Session {n}:** {w(text, 1400 if i == 0 else 450)}" for i, (n, text) in enumerate(summaries)]
        sections.append(_section(2, "## Previously", list(reversed(lines)), keep="tail", min_lines=1,
                                 more="`./dm session list`"))
    shown = {m["id"] for m in important}
    recent_mems = [m for m in mems[-recent:] if not m.get("important")]
    shown.update(m["id"] for m in recent_mems)
    query = _scene_query(camp)
    relevant = []
    if query.strip() and len(mems) > len(shown):
        for h in search(camp, query, limit=20, scope="memory"):
            m = next((x for x in mems if x["id"] == h["ref"]), None)
            if m and m["id"] not in shown and len(relevant) < 8:
                relevant.append(m)
                shown.add(m["id"])
    if relevant:
        sections.append(_section(2, "## Relevant to the current scene (older memories)",
                                 [f"- {w(fmt_memory(m), 200)}" for m in relevant], min_lines=3, more="`./dm recall \"…\"`"))
    if recent_mems:
        sections.append(_section(3, "## Recent memories", [f"- {w(fmt_memory(m), 200)}" for m in recent_mems],
                                 keep="tail", min_lines=3, more="`./dm memory list --limit 40`"))
    entries = lore.all_entries(camp)
    if entries:
        by_type: dict[str, list[str]] = {}
        for e in entries:
            by_type.setdefault(e["meta"].get("type", "other"), []).append(e["meta"].get("name", e["slug"]))
        sections.append(_section(3, "## World lore index", [f"- **{t}:** {w(', '.join(sorted(by_type[t])), 400)}"
                                                             for t in sorted(by_type)], min_lines=3,
                                 more="`./dm lore list`"))
        npcs = [e for e in entries if e["meta"].get("type") == "npc" and e["meta"].get("summary")]
        if npcs:
            sections.append(_section(4, "## NPCs at a glance", [
                f"- {e['meta']['name']} — {w(e['meta']['summary'], 90)}"
                + (f" [{e['meta']['status']}]" if e["meta"].get("status") and e["meta"]["status"] != "alive" else "")
                for e in npcs[:30]], more="`./dm lore list --type npc`"))
    from robodm import companions

    comp = companions.dm_summary(camp)
    if comp:
        sections.append(_section(1, "## Companions in play (each has its own mind: consult its agent at decision points)",
                                 comp, min_lines=len(comp)))
    blocks = context_blocks.active_blocks(camp)
    if blocks:
        sections.append(_section(1, "## Active instruction blocks", [
            ", ".join(f"{b['name']} ({b['meta'].get('scope', 'always')})" for b in blocks)
            + " — load the text with `./dm context render`."], min_lines=1))
    opened = audit.open_events(camp)
    sections.append(_section(0, "## Rules checking (mandatory)", [
        f"{len(audit.cleared(camp))} item(s) cleared by the rules-lawyer"
        + (f" · ⚠️ {len(opened)} event(s) awaiting the post-audit — `./dm audit pending`" if opened else "")]))

    if not full and budget:
        _fit(sections, budget)
    out = []
    for sec in sections:
        if not sec["lines"] and not sec["cut"]:
            continue
        out.append(("\n" if out else "") + sec["title"])
        out.extend(sec["lines"])
        if sec["cut"]:
            where = "older" if sec["keep"] == "tail" else "more"
            out.append(f"_(+{sec['cut']} {where} not shown — {sec['more'] or 'see the files'})_")
    out.append(f"\n_{len(mems)} memories in total. Search everything by meaning with "
               "`./dm recall \"phrasing 1\" \"phrasing 2\"` (memories, journals, transcripts, lore, sources)._")
    return "\n".join(out)
