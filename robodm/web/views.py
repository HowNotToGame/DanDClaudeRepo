"""What the web client shows, for players or for the DM.

The player view is spoiler-safe: lore loses its `## DM secrets`, companions show only their public face and a
word for their mood, secret memories, DM notes, reference sources and the DM's running log stay hidden, and
monster HP follows the campaign's `hide_monster_hp`. The DM view shows everything.
"""

from __future__ import annotations

import re

from robodm import audit, battlemap as bm, campaign as campaign_mod, capture, companions, lore, memory, recall, snapshots, world
from robodm.campaign import Campaign
from robodm.combat import Encounter, health_word
from robodm.util import ABILITIES, RoboDMError, ability_mod, read_jsonl, truncate

ATTITUDE_WORDS = {3: "devoted", 2: "warm", 1: "friendly", 0: "neutral", -1: "wary", -2: "cold", -3: "hostile"}
PLAYER_RECALL_KINDS = {"memory", "summary", "transcript", "lore", "quest", "pc", "combat"}


# Memories written from the DM's side ("he does NOT know…", "DM secret", the who-knows-what ledger) aren't marked
# secret but would spoil things in the player view.
_DM_NOTE = re.compile(r"\bDM\b|secret|does\s+not\s+know|doesn['’]t\s+know|do\s+not\s+know|not\s+yet\s+know|unaware|"
                      r"ledger|who[- ]knows[- ]what|never shown|pending the player", re.IGNORECASE)


def strip_dm(text: str) -> str:
    return re.split(r"^##\s+DM (?:secrets|only)\b", text or "", maxsplit=1, flags=re.MULTILINE | re.IGNORECASE)[0].strip()


# =============================================================================== state

def sheet(pc: dict) -> dict:
    hp = pc["hp"]
    return {
        "name": pc["name"], "slug": pc["slug"], "player": pc.get("player", ""), "race": pc.get("race", ""),
        "classes": " / ".join(f"{c['class']} {c['level']}" for c in campaign_mod.pc_classes(pc)),
        "subclass": pc.get("subclass", ""), "level": pc.get("level", 1),
        "hp": hp["current"], "max_hp": hp["max"], "temp_hp": hp.get("temp", 0), "ac": pc.get("ac"),
        "speed": pc.get("speed", 30), "passive": campaign_mod.pc_passive(pc), "prof": campaign_mod.pc_prof(pc),
        "init": campaign_mod.pc_init_bonus(pc), "spell_dc": campaign_mod.pc_spell_dc(pc),
        "abilities": {ab: {"score": pc["abilities"][ab], "mod": ability_mod(pc["abilities"][ab]),
                           "save": campaign_mod.pc_save_bonus(pc, ab)} for ab in ABILITIES},
        "skills": {s: campaign_mod.pc_skill_bonus(pc, s) for s in pc.get("skill_proficiencies", [])},
        "conditions": [c["name"] if isinstance(c, dict) else c for c in pc.get("conditions", [])],
        "death_saves": pc.get("death_saves", {}),
        "slots": [{"label": campaign_mod.slot_label(k, v), "left": v["max"] - v["used"], "max": v["max"]}
                  for k, v in sorted((pc.get("spell_slots") or {}).items())],
        "resources": [{"name": k, "left": v["max"] - v["used"], "max": v["max"], "reset": v.get("reset", "long")}
                      for k, v in (pc.get("resources") or {}).items()],
        "attacks": [{"name": a["name"], "to_hit": a.get("to_hit"), "damage": ", ".join(" ".join(d) for d in a.get("damage", [])),
                     "range": a.get("range"), "reach": a.get("reach")} for a in pc.get("attacks", [])],
        "spells": pc.get("spells", []), "features": pc.get("features", []), "inventory": pc.get("inventory", []),
        "gold": pc.get("gold", 0), "xp": pc.get("xp", 0), "notes": pc.get("notes", ""),
        "equipped": [x for x in ((pc.get("equipped") or {}).get("armor"), (pc.get("equipped") or {}).get("shield")) if x]
                    + list((pc.get("equipped") or {}).get("worn") or []),
        "attuned": pc.get("attuned") or [], "senses": pc.get("senses") or [],
        "hit_dice": f"{pc['hit_dice']['max'] - pc['hit_dice']['used']}/{pc['hit_dice']['max']} d{pc['hit_dice']['die']}",
    }


def public_sheet(pc: dict) -> dict:
    """Another player's character, as the party sees it across the table."""
    s = sheet(pc)
    s.update({"skills": {}, "slots": [], "resources": [], "attacks": [], "spells": [], "features": [], "inventory": [],
              "equipped": [], "attuned": [], "notes": "", "gold": None, "xp": None, "hit_dice": "", "public": True})
    return s


def combat_view(camp: Campaign, dm: bool) -> dict | None:
    from robodm import tactics

    enc = Encounter(camp)
    if not enc.active:
        return None
    reveal = dm or not camp.settings.get("hide_monster_hp", True)
    cur = (enc.current() or {}).get("id")
    codes = bm.token_codes(enc.combatants)
    m = enc.state.get("map")
    fog = bm.fog_view(m, enc.combatants, tactics.vision_fn(camp)) if m else None
    shown = enc.combatants if (dm or fog is None) else {k: v for k, v in enc.combatants.items() if bm.seen_by_party(v, fog)}
    order = enc.state["order"] + [i for i in enc.combatants if i not in enc.state["order"]]
    rows = []
    for cid in order:
        if cid not in shown:
            continue
        c = enc.combatants[cid]
        show = reveal or c["kind"] == "pc"
        rows.append({
            "id": cid, "name": c["name"], "code": codes.get(cid, "??"), "kind": c["kind"], "side": c["side"],
            "init": c.get("init"), "current": cid == cur, "dead": c.get("status") == "dead",
            "hp": c["hp"] if show else None, "max_hp": c["max_hp"] if show else None, "health": health_word(c),
            "ac": c["ac"] if (dm or c["kind"] == "pc") else None, "temp_hp": c.get("temp_hp", 0) if show else 0,
            "conditions": [enc._cond_label(x) for x in c["conditions"]], "concentration": c.get("concentration"),
            "pos": bm.fmt_cell(*c["pos"]) if c.get("pos") else None, "moved": c.get("moved", 0),
            "speed": enc.speed_now(c), "reaction_used": c.get("reaction_used", False),
        })
    area = enc.state.get("last_area") or {}
    return {"name": enc.state.get("name", ""), "round": enc.state.get("round", 0),
            "current": cur if cur in shown else None, "unseen_turn": bool(cur) and cur not in shown, "order": rows,
            "has_map": bool(enc.state.get("map")), "fog": fog is not None,
            "geometry": bm.svg_geometry(m, shown) if m else None,
            "map_name": (enc.state.get("map") or {}).get("name", ""),
            "area": {"shape": area.get("shape"), "size": area.get("size"), "spell": area.get("spell")} if area else None}


def companion_card(camp: Campaign, slug: str, dm: bool) -> dict:
    meta, sections = companions.load_persona(camp, slug)
    st = companions.state(camp, slug)
    public = ""
    if meta.get("lore"):
        try:
            public = str(lore.find(camp, meta["lore"])["meta"].get("summary") or "")
        except Exception:
            public = ""
    card = {"slug": slug, "name": meta.get("name", slug), "status": st["status"], "alignment": meta.get("alignment", ""),
            "voice": sections.get("Voice & mannerisms", "") if not str(sections.get("Voice & mannerisms", "")).startswith("(fill") else "",
            "public": public or truncate(re.sub(r"\s+", " ", strip_dm(sections.get("Core", ""))), 400),
            # players see how a companion feels about the player characters, not about the rest of the world
            "mood": {who.replace("-", " ").title(): ATTITUDE_WORDS.get(a["score"], str(a["score"]))
                     for who, a in st["attitude"].items() if dm or who in {pc["slug"] for pc in camp.party()}},
            "wants_to_speak": bool(st.get("interject")), "draft": bool(meta.get("draft"))}
    if dm:
        card.update({"persona": (companions._dir(camp, slug) / "persona.md").read_text(encoding="utf-8"),
                     "attitude": st["attitude"], "agenda": st["agenda"], "interject": st.get("interject"),
                     "memories": companions.memories(camp, slug)[-12:], "consults": st.get("consults", 0),
                     "agent": companions.agent_name(camp, slug)})
    return card


def world_view(camp: Campaign, dm: bool) -> dict:
    """Time, scene, clocks, factions and timers. Players see public clocks, standings and who's here; the DM also
    sees hidden clocks, agendas, NPC minds and the off-screen beats waiting to be resolved."""
    w = world.load(camp)
    on = world.tracking(w)
    t = w["time"].get("t")
    entries = {e["slug"]: e for e in lore.all_entries(camp)}
    cast = []
    for slug, name in world.scene_cast(camp):
        e = entries.get(slug)
        row = {"slug": slug, "name": name, "summary": str((e or {}).get("meta", {}).get("summary") or "")}
        if dm and e:
            row["mind"] = world.fmt_mind(e["meta"])
        cast.append(row)
    clocks = []
    for c in w["clocks"]:
        if c["status"] not in ("running", "filled") or not (dm or c.get("public")):
            continue
        row = {"id": c["id"], "name": c["name"], "segments": c.get("segments"), "filled": c.get("filled", 0),
               "status": c["status"], "public": bool(c.get("public")), "per": world.fmt_dur(c["per"]) if c.get("per") else None,
               "deadline": world.fmt_point(c["deadline"]) if c.get("deadline") is not None else None,
               "left": world.fmt_dur(c["deadline"] - t) if on and c.get("deadline") is not None and c["status"] == "running" else None}
        if dm:
            row.update({"on_fill": c.get("on_fill", ""), "faction": c.get("faction")})
        clocks.append(row)
    factions = []
    for f in world.factions(camp):
        if not dm and not f["rep"]:
            continue
        row = {"slug": f["slug"], "name": f["name"], "rep": f["rep"], "word": f["word"]}
        if dm:
            row.update({"why": f["why"], "pace": f["pace"], "agenda": [i for i in f["agenda"] if i["status"] == "open"]})
        factions.append(row)
    out = {"tracking": on, "time": world.fmt_time(w) if on else None, "word": world.time_word(t) if on else None,
           "day": world.day_of(t) if on else None, "place": w["scene"].get("place", ""), "cast": cast,
           "clocks": clocks, "factions": factions,
           "timers": [{"text": x["text"], "left": world.fmt_timer(w, x).split(" — ", 1)[-1]} for x in w["timers"]] if on else []}
    if dm:
        out["beats"] = world.open_beats(w)
    return out


def state(camp: Campaign, view: str = "player", seat: str | None = None) -> dict:
    """Everything the page shows. `seat`: a player's seat (their own full sheet, the party's public side, their
    private messages); the host (no seat) sees full sheets."""
    from robodm import whispers
    from robodm.web import table

    dm = view == "dm" and seat is None
    s = camp.settings
    sess = camp.session
    return {
        "view": view,
        "campaign": {"name": camp.name, "slug": camp.slug, "premise": camp.data.get("premise", ""),
                     "setting": camp.data.get("setting", ""), "tone": camp.data.get("tone", ""),
                     "session": sess.get("number", 0), "session_active": bool(sess.get("active")),
                     "settings": {k: s.get(k) for k in ("dice_mode", "crit_rule", "advancement", "narration",
                                                         "hide_monster_hp", "grid_diagonals", "companion_agents")}},
        "party": [sheet(pc) if seat is None or pc["slug"] == seat else public_sheet(pc) for pc in camp.party()],
        "combat": combat_view(camp, dm),
        "companions": [companion_card(camp, slug, dm) for slug in companions.all_slugs(camp)],
        "quests": [{"id": q["id"], "title": q["title"], "giver": q.get("giver", ""), "status": q["status"],
                    "notes": q.get("notes", "")} for q in camp.quests() if dm or q["status"] == "open"],
        "audit": {"open": len(audit.open_events(camp)), "cleared": len(audit.cleared(camp))},
        "world": world_view(camp, dm),
        "seat": _seat_info(camp, seat),
        "whispers": whispers.for_pc(camp, seat) if seat else (whispers.all_whispers(camp)[-50:] if dm else []),
        "round": table.round_state(camp),
        "usage": _usage(camp) if seat is None else None,
        "undo": ({k: v for k, v in (snapshots.peek(camp) or {}).items() if k in ("turn", "label", "ts")} or None) if dm else None,
    }


def _usage(camp: Campaign) -> dict:
    from robodm import costs

    return costs.usage_summary(camp)


def _seat_info(camp: Campaign, seat: str | None) -> dict | None:
    if not seat:
        return None
    pc = camp.find_pc(seat)
    enc = Encounter(camp)
    cid = next((k for k, c in enc.combatants.items() if c.get("pc") == seat), None) if enc.active else None
    return {"slug": seat, "name": pc["name"] if pc else seat, "combatant": cid}


# =============================================================================== panels

def recap(camp: Campaign, view: str) -> str:
    if view == "dm":
        return memory.brief(camp)
    lines = [f"# {camp.name}"]
    for key in ("premise", "setting"):
        if camp.data.get(key):
            lines.append(f"**{key.title()}:** {camp.data[key]}")
    summaries = camp.session_summaries(limit=3)
    if summaries:
        lines.append("\n## Previously")
        lines.extend(f"**Session {n}:** {t}" for n, t in reversed(summaries))
    quests = [q for q in camp.quests() if q["status"] == "open"]
    if quests:
        lines.append("\n## Open quests")
        lines.extend(f"- **{q['title']}**" + (f" (from {q['giver']})" if q.get("giver") else "") for q in quests)
    said = capture.last_exchanges(camp, n=6, width=600, public=True)
    if said:
        lines.append("\n## Last at the table")
        lines.extend(f"- {x}" for x in said)
    return "\n".join(lines)


def lore_list(camp: Campaign, view: str) -> list[dict]:
    return [{"slug": e["slug"], "name": e["meta"].get("name", e["slug"]), "type": e["meta"].get("type", "other"),
             "summary": e["meta"].get("summary", ""), "status": e["meta"].get("status", "")}
            for e in sorted(lore.all_entries(camp), key=lambda x: (str(x["meta"].get("type")), str(x["meta"].get("name"))))]


def lore_entry(camp: Campaign, slug: str, view: str) -> dict:
    e = lore.find(camp, slug)
    return {"slug": e["slug"], "name": e["meta"].get("name"), "type": e["meta"].get("type"),
            "summary": e["meta"].get("summary", ""), "body": e["body"] if view == "dm" else strip_dm(e["body"]),
            "related": e["meta"].get("related", []) or []}


def people_list(camp: Campaign, view: str) -> list[dict]:
    """NPC records for the People tab. Players see who the party could know of: no `hidden` records, no stats."""
    from robodm import npcs

    dm = view == "dm"
    rows = []
    for r in npcs.all_records(camp):
        if not dm and r.get("hidden"):
            continue
        rows.append({"slug": r["slug"], "name": r["name"], "race": r.get("race", ""), "role": r.get("role", ""),
                     "tier": r.get("tier", "minor") if dm else "", "status": r.get("status", "alive"),
                     "companion": r["_kind"] == "companion",
                     "mind": npcs.mind_agent(camp, r) if dm else ""})
    order = {"major": 0, "minor": 1, "background": 2, "": 1}
    return sorted(rows, key=lambda x: (0 if x["companion"] else 1, order.get(x["tier"], 1), x["name"]))


def person(camp: Campaign, slug: str, view: str) -> dict:
    """One NPC record: the player view drops alignment, backstory, secrets, DM-only notes, hidden bonds and stats."""
    from robodm import npcs

    dm = view == "dm"
    rec = npcs.find(camp, slug)
    if not dm and rec.get("hidden"):
        raise RoboDMError("No such person.")
    out = {"slug": rec["slug"], "name": rec["name"], "text": npcs.show(camp, rec, dm=dm, stats=dm)}
    if dm:
        out["statblock"] = npcs.statblock_text(camp, rec)
    return out


def search(camp: Campaign, queries: list[str], view: str) -> list[dict]:
    hits = recall.search(camp, queries, limit=30)
    if view == "dm":
        return hits[:15]
    secret_ids = {m["id"] for m in read_jsonl(camp.path("memory.jsonl"))
                  if m.get("type") == "secret" or _DM_NOTE.search(m.get("text", ""))}
    out = []
    for h in hits:
        if h["kind"] not in PLAYER_RECALL_KINDS or (h["kind"] == "memory" and h["ref"] in secret_ids):
            continue
        if capture.PRIVATE in str(h.get("show", "")) + str(h.get("snippet", "")):
            continue                     # a private exchange between the DM and one player
        if h["kind"] == "lore":
            h = dict(h, snippet="")      # a snippet could come from the DM secrets section
        out.append(h)
    return out[:15]


def journal(camp: Campaign, view: str, number: int | None = None, seat_name: str | None = None) -> dict:
    n = number if number is not None else camp.session.get("number", 0)
    rows = [{"time": t, "who": who, "text": text, "private": capture.private_to(text) is not None}
            for t, who, text in capture.entries(camp, n) if capture.visible(text, view == "dm", seat_name)]
    out = {"session": n, "transcript": rows}
    if view == "dm":
        p = camp.journal_path(n)
        out["log"] = p.read_text(encoding="utf-8") if p.exists() else ""
    return out
