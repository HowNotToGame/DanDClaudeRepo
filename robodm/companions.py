"""Companions: NPC party members (and important NPCs in a scene) with their own minds, played by their own
subagents.

Each companion has a folder in campaigns/<slug>/companions/<npc>/:
    persona.md     who they are: drives, values, methods, fears, relationships, secrets, combat style, voice
                   (a `## DM only` section is never shown to the companion)
    state.json     where they are (party / present / away / dead), attitudes toward each character, their agenda,
                   whether they want to speak up, when they were last consulted
    memory.jsonl   what they remember, in their own words

The companion's subagent sees only `./dm companion brief <npc>`: its persona minus the DM-only parts, its own
memories, public lore with DM secrets removed, events tagged as known to it (`memory add --known-by`), and the
scene while it's there. It can't read campaign files or change game state; it proposes, the DM rules.

Ideas borrowed (independently implemented) from two orchestration projects: from Paperclip, agents with an
identity, goals that cascade into a concrete agenda, waking only on triggers, and "agents propose, the board
approves"; from Orca, fanning one question out to several agents at once (a party council), agents flagging
that they need attention (interjections), and feedback sent back to the agent (table notes).
"""

from __future__ import annotations

import re
from pathlib import Path

from robodm.campaign import Campaign
from robodm.util import (RoboDMError, append_jsonl, dump_frontmatter, now_iso, parse_frontmatter, rank_documents,
                         read_json, read_jsonl, slugify, truncate, write_json, write_text)

STATUSES = ("party", "present", "away", "dead")
ACTIVE = ("party", "present")
SECTIONS = ["Core", "Drives", "Values & lines", "Methods", "Fears & flaws", "Relationships", "Secrets",
            "Knowledge limits", "Combat style", "Voice & mannerisms", "Table notes", "DM only"]
HIDDEN = {"dm only"}

ALIGNMENT_GUIDE = {
    "lawful good": "Keeps promises and protects the weak through order: honest dealing, oaths, proper channels. "
                   "Bends only when the rules themselves would cause harm, and feels it.",
    "neutral good": "Does the kind thing that works, rules or no rules. Pragmatic compassion; dislikes cruelty more "
                    "than disorder.",
    "chaotic good": "Follows conscience over law. Quick to defy unjust authority, generous, impatient with red tape, "
                    "loyal to people rather than institutions.",
    "lawful neutral": "Holds to a code (a craft, an order, a duty, a debt) above feelings or outcomes. Reliable, "
                      "formal, hard to sway once committed.",
    "neutral": "Weighs each situation on its own terms. Avoids extremes and other people's crusades; self-reliant "
               "and practical.",
    "chaotic neutral": "Guards personal freedom above all. Unpredictable, spontaneous, resents being told what to "
                       "do, honest about wanting what they want.",
    "lawful evil": "Uses rules, contracts and hierarchy to take what they want. Keeps the letter of a bargain; "
                   "patient and calculating.",
    "neutral evil": "Does whatever serves them with no loyalty beyond usefulness. Careful, self-protective, cruel "
                    "when it pays.",
    "chaotic evil": "Acts on appetite and spite. Violent when crossed, scornful of bonds, only held back by fear "
                    "or a stronger will.",
    "unaligned": "Acts on instinct and need rather than ideals.",
}

PROTOCOL = """\
You are a **companion character** in a D&D 5e (2014) campaign run by RoboDM: a full person with your own
wants, values and ways of doing things, not a sidekick. The Dungeon Master (the main Claude) consults you while
you're in the party or present in a scene: on your turn in combat, when someone talks to you, when the party
makes a plan, or when something touches what you care about.

## Every time
1. Run `{brief}` (add `--combat` during a fight). It is the ONLY source of who you are and what you know.
   Read all of it, including the DM's description of the moment. Run every command exactly as written, from the
   folder you start in: no `cd`, no full paths, nothing piped or chained.
2. **Stay inside your knowledge.** You know your persona, your own secrets, your memories, public lore, and what
   happened in front of you. You don't know DM secrets, other people's thoughts, or what happened where you weren't.
   If you're unsure whether you'd know something, you don't. More of your memories: `{recall}`.
3. **Decide as yourself.** Weigh your drives, values, methods, fears, relationships and agenda against the
   situation. You may agree, push back, argue, refuse, bargain, keep a secret, act on your own agenda, or go along
   reluctantly: whatever this person would really do. Don't be a yes-man, and don't be contrary just for drama.
4. **Never decide for a player character**: not their actions, words or feelings. You can ask, urge, warn, block
   a doorway or grab an arm; they choose.
5. **In combat**, propose concrete actions as `./dm combat …` commands (moves to squares, attacks with your stat
   block's actions, spells) but don't run them: the DM runs them through the rules gate. Fight the way your
   Combat style says, and protect whoever you'd protect.
6. **Remember**: record what you'll carry from this beat, first person, one or two lines: `{remember}`.
   When they change, update your agenda (`{agenda}`) and attitudes (`{attitude}`). To raise something later,
   unprompted: `{interject}`.
7. You can't run anything else. The hooks block dice, combat changes and file access; the DM does all of that.

## Answer (to the DM, not the players), in this shape
INTENT: what you want right now, in one line
DOES: what you do this beat; in combat, the exact `./dm combat …` commands to run
SAYS: your words, in your voice (or "—")
HIDES: anything you're deliberately not saying (DM's eyes only), or "—"
FRICTION: 0–3, how far this goes against the party's plan, and why, in one line
"""


# =============================================================================== storage

def companions_dir(camp: Campaign) -> Path:
    return camp.path("companions")


def _dir(camp: Campaign, slug: str) -> Path:
    return companions_dir(camp) / slug


def mind_dir(camp: Campaign, slug: str) -> Path:
    """Where a mind keeps its state and memories: the companion's folder, else the NPC's (robodm/npcs.py). The
    functions below (state, memories, attitudes, recall, the shared parts of the brief) work for both."""
    d = _dir(camp, slug)
    return d if (d / "persona.md").exists() else camp.path("npcs", slug)


def all_slugs(camp: Campaign) -> list[str]:
    d = companions_dir(camp)
    return sorted(p.name for p in d.iterdir() if (p / "persona.md").exists()) if d.exists() else []


def find(camp: Campaign, name: str) -> str:
    slug = slugify(name)
    slugs = all_slugs(camp)
    if slug in slugs:
        return slug
    pref = [s for s in slugs if s.startswith(slug) or slug in s]
    if len(pref) == 1:
        return pref[0]
    raise RoboDMError(f"No companion '{name}'." + (f" Companions: {', '.join(slugs)}" if slugs else
                                                   " Create one with `./dm companion new \"Name\" --from-lore <entry>`."))


def load_persona(camp: Campaign, slug: str) -> tuple[dict, dict[str, str]]:
    meta, body = parse_frontmatter((_dir(camp, slug) / "persona.md").read_text(encoding="utf-8"))
    return meta, split_sections(body)


def split_sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current, buf = "Core", []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if "\n".join(buf).strip():
                out[current] = "\n".join(buf).strip()
            current, buf = m.group(1), []
        elif not line.startswith("# "):
            buf.append(line)
    if "\n".join(buf).strip():
        out[current] = "\n".join(buf).strip()
    return out


def join_sections(name: str, sections: dict[str, str]) -> str:
    order = SECTIONS + [s for s in sections if s not in SECTIONS]
    parts = [f"# {name}"]
    for s in order:
        if sections.get(s, "").strip():
            parts.append(f"\n## {s}\n{sections[s].strip()}")
    return "\n".join(parts) + "\n"


def save_persona(camp: Campaign, slug: str, meta: dict, sections: dict[str, str]) -> Path:
    p = _dir(camp, slug) / "persona.md"
    write_text(p, dump_frontmatter(meta, join_sections(meta.get("name", slug), sections)))
    return p


def state(camp: Campaign, slug: str) -> dict:
    st = read_json(mind_dir(camp, slug) / "state.json") or {}
    st.setdefault("status", "away")
    st.setdefault("history", [])
    st.setdefault("attitude", {})
    st.setdefault("agenda", [])
    st.setdefault("interject", None)
    st.setdefault("last_brief_turn", -1)
    st.setdefault("consults", 0)
    return st


def save_state(camp: Campaign, slug: str, st: dict) -> None:
    write_json(mind_dir(camp, slug) / "state.json", st)


def memories(camp: Campaign, slug: str) -> list[dict]:
    return read_jsonl(mind_dir(camp, slug) / "memory.jsonl")


# =============================================================================== lifecycle

def create(camp: Campaign, name: str, from_lore: str | None = None, alignment: str = "", status: str = "away",
           model: str = "sonnet") -> str:
    from robodm import lore

    slug = slugify(name)
    if (_dir(camp, slug) / "persona.md").exists():
        raise RoboDMError(f"Companion {name} already exists (campaigns/{camp.slug}/companions/{slug}/).")
    sections = {s: "" for s in SECTIONS}
    meta = {"name": name, "alignment": alignment or "neutral", "model": model, "draft": True}
    if from_lore:
        e = lore.find(camp, from_lore)
        meta["lore"] = e["slug"]
        body = split_sections(e["body"])
        public = "\n\n".join(v for k, v in body.items() if k.lower() not in ("dm secrets", "dm only"))
        sections["Core"] = truncate(str(e["meta"].get("summary") or ""), 600) + "\n\n" + truncate(public, 3000)
        if body.get("DM secrets"):
            sections["DM only"] = ("Copied from the lore entry's DM secrets. Move what this character KNOWS about "
                                   "themselves into `## Secrets`; keep the rest here.\n\n" + body["DM secrets"])
    for s in ("Drives", "Values & lines", "Methods", "Fears & flaws", "Relationships", "Secrets", "Knowledge limits",
              "Combat style", "Voice & mannerisms"):
        sections[s] = sections[s] or f"(fill in: {s.lower()})"
    save_persona(camp, slug, meta, sections)
    set_status(camp, slug, status if status in STATUSES else "away")
    return slug


def set_status(camp: Campaign, slug: str, status: str) -> dict:
    if status not in STATUSES:
        raise RoboDMError(f"Status must be one of: {', '.join(STATUSES)}")
    st = state(camp, slug)
    if st["status"] != status or not st["history"]:
        st["history"].append({"status": status, "ts": now_iso(), "session": camp.session.get("number", 0)})
    st["status"] = status
    save_state(camp, slug, st)
    return st


def active(camp: Campaign) -> list[str]:
    return [s for s in all_slugs(camp) if state(camp, s)["status"] in ACTIVE]


def remember(camp: Campaign, slug: str, text: str, about: str = "") -> dict:
    rec = {"ts": now_iso(), "session": camp.session.get("number", 0), "text": text.strip()}
    if about:
        rec["about"] = slugify(about)
    append_jsonl(mind_dir(camp, slug) / "memory.jsonl", rec)
    return rec


def agenda(camp: Campaign, slug: str, verb: str, text: str = "") -> list[dict]:
    st = state(camp, slug)
    items = st["agenda"]
    if verb == "add":
        n = 1 + max((int(i["id"][1:]) for i in items if re.fullmatch(r"a\d+", i.get("id", ""))), default=0)
        items.append({"id": f"a{n}", "text": text.strip(), "status": "open", "ts": now_iso()})
    elif verb in ("done", "drop"):
        target = next((i for i in items if i["id"] == text.strip() or text.strip().lower() in i["text"].lower()), None)
        if not target:
            raise RoboDMError(f"No agenda item '{text}'. Open: " + ", ".join(f"{i['id']} {i['text']}" for i in items if i["status"] == "open"))
        target["status"] = "done" if verb == "done" else "dropped"
        target["closed"] = now_iso()
    elif verb != "list":
        raise RoboDMError("Usage: companion agenda <npc> add \"goal\" | done <id> | drop <id> | list")
    save_state(camp, slug, st)
    return items


def attitude(camp: Campaign, slug: str, who: str, change: str, why: str = "") -> dict:
    st = state(camp, slug)
    key = slugify(who)
    cur = st["attitude"].get(key, {"score": 0, "why": ""})
    ch = change.strip()
    score = cur["score"] + int(ch) if ch[:1] in "+-" else int(ch)
    cur = {"score": max(-3, min(3, score)), "why": why or cur.get("why", ""), "ts": now_iso()}
    st["attitude"][key] = cur
    save_state(camp, slug, st)
    return cur


def interject(camp: Campaign, slug: str, topic: str | None) -> None:
    from robodm import audit

    st = state(camp, slug)
    st["interject"] = {"topic": topic.strip(), "ts": now_iso(), "turn": audit.current_turn(camp)} if topic else None
    save_state(camp, slug, st)


def table_note(camp: Campaign, slug: str, note: str) -> None:
    meta, sections = load_persona(camp, slug)
    existing = sections.get("Table notes", "")
    sections["Table notes"] = (existing + "\n" if existing else "") + f"- {note.strip()} ({now_iso()[:10]})"
    save_persona(camp, slug, meta, sections)


# =============================================================================== what the companion sees

def _strip_secrets(text: str) -> str:
    return re.split(r"^##\s+DM (?:secrets|only)\b", text, maxsplit=1, flags=re.MULTILINE | re.IGNORECASE)[0].strip()


def known_documents(camp: Campaign, slug: str) -> list[dict]:
    """Everything this companion could know: its memories, events tagged as known to it, public lore."""
    from robodm import lore

    docs = [{"text": m["text"], "show": f"(you remember, s{m.get('session', 0)}) {m['text']}"} for m in memories(camp, slug)]
    for m in read_jsonl(camp.path("memory.jsonl")):
        if slug in (m.get("known_by") or []) and not m.get("retired"):
            docs.append({"text": m["text"], "show": f"(you know, s{m.get('session', 0)}) {m['text']}"})
    for e in lore.all_entries(camp):
        body = _strip_secrets(e["body"])
        docs.append({"text": f"{e['meta'].get('name', '')} {e['meta'].get('summary', '')} {body}",
                     "show": f"(lore) {e['meta'].get('name')}: {truncate(str(e['meta'].get('summary') or body), 220)}"})
    return docs


def recall(camp: Campaign, slug: str, query: str, limit: int = 8) -> list[str]:
    return [d["show"] for d in rank_documents(query, known_documents(camp, slug), limit=limit)]


def brief(camp: Campaign, slug: str, situation: str = "", combat: bool = False, record: bool = True) -> str:
    """The companion's whole world for one consultation."""
    from robodm import lore

    meta, sections = load_persona(camp, slug)
    st = state(camp, slug)
    name = meta.get("name", slug)
    party = [pc["name"] for pc in camp.party()]
    lines = [f"# You are {name}"]
    if st["status"] not in ACTIVE:
        lines.append(f"_(The DM has you as **{st['status']}**: you're not in this scene. Only answer if the DM is "
                     "deliberately bringing you in.)_")
    if meta.get("alignment"):
        lines.append(f"*{meta['alignment'].title()}*" + (f" · voice: {meta['voice']}" if meta.get("voice") else ""))
    for s in SECTIONS + [x for x in sections if x not in SECTIONS]:
        text = sections.get(s, "").strip()
        if not text or s.lower() in HIDDEN or text.startswith("(fill in"):
            continue
        title = {"Secrets": "Your secrets (yours to keep or reveal)", "Table notes":
                 "Table notes (corrections from the players and DM: follow them)"}.get(s, s)
        lines.append(f"\n## {title}\n{text}")
    guide = ALIGNMENT_GUIDE.get(str(meta.get("alignment", "")).lower())
    if guide:
        lines.append(f"\n## How your alignment tends to colour choices\n{guide} (A tendency, not a script: your "
                     "persona comes first.)")
    if meta.get("lore") and not sections.get("Core", "").strip():
        try:
            e = lore.find(camp, meta["lore"])
            lines.append(f"\n## Who you are (from the world's lore)\n{_strip_secrets(e['body'])}")
        except RoboDMError:
            pass
    lines.append("\n## Where things stand")
    where = {"party": f"travelling with the party ({', '.join(party) or 'the player characters'})",
             "present": "present in the current scene (not part of the party)", "away": "away", "dead": "dead"}
    lines.append(f"- You are {where.get(st['status'], st['status'])}.")
    lines.extend(shared_lines(camp, slug, st, st["status"] in ACTIVE, situation, combat, "companion",
                              meta.get("combatant", slug)))
    if record:
        mark_consulted(camp, slug, st)
    return "\n".join(lines)


def shared_lines(camp: Campaign, slug: str, st: dict, here: bool, situation: str = "", combat: bool = False,
                 group: str = "companion", combatant: str | None = None) -> list[str]:
    """The parts of a mind's brief that don't depend on who they are (companions and NPCs alike): the time and the
    scene, attitudes and agenda, memories and what's known to them, public lore, what was just said at the table,
    the DM's description of the moment, and the fight as they can see it. `group`: `companion` or `npc` (the
    commands they're told to use)."""
    from robodm import lore, world
    from robodm.combat import Encounter

    lines = []
    wd = world.load(camp)
    if world.tracking(wd):
        lines.append(f"- It is {world.fmt_time(wd)}.")
    if here and (wd["scene"].get("place") or wd["scene"].get("present")):
        others = [n for s, n in world.scene_cast(camp) if s != slug]
        lines.append(f"- Here{' at ' + wd['scene']['place'] if wd['scene'].get('place') else ''}"
                     + (f", with {', '.join(others)}" if others else "") + ".")
    for who, a in sorted(st["attitude"].items()):
        lines.append(f"- Attitude toward {who.replace('-', ' ').title()}: {a['score']:+d}" + (f" ({a['why']})" if a.get("why") else ""))
    open_items = [i for i in st["agenda"] if i["status"] == "open"]
    if open_items:
        lines.append("- Your agenda: " + "; ".join(f"[{i['id']}] {i['text']}" for i in open_items))
    mems = memories(camp, slug)
    tagged = [m for m in read_jsonl(camp.path("memory.jsonl")) if slug in (m.get("known_by") or []) and not m.get("retired")]
    if mems or tagged:
        lines.append("\n## What you remember (newest last)")
        lines.extend(f"- {m['text']}" for m in mems[-25:])
        lines.extend(f"- (known to you) {m['text']}" for m in tagged[-15:])
        if len(mems) > 25:
            lines.append(f"_({len(mems) - 25} older memories: `./dm {group} recall {slug} \"words\"`)_")
    entries = [e for e in lore.all_entries(camp) if e["meta"].get("summary")]
    if entries:
        lines.append("\n## The world as people know it (public lore; DM secrets removed)")
        lines.extend(f"- {e['meta'].get('name')}: {truncate(str(e['meta']['summary']), 160)}" for e in entries[:40])
    if here and camp.session.get("active"):
        # What was said at the table, not the DM's log (that's full of rulings and numbers no character sees).
        from robodm import capture

        said = capture.last_exchanges(camp, n=4, width=700)
        if said:
            lines.append("\n## Lately (the last exchanges at the table; the DM's description below wins)")
            lines.extend(f"- {x}" for x in said)
    if situation:
        lines.append(f"\n## Right now (from the DM)\n{situation}")
    enc = Encounter(camp)
    if (combat or enc.active) and enc.active:
        me = next((c for c in enc.combatants.values() if slugify(c["name"]) == slug or c["id"] == (combatant or slug)), None)
        lines.append("\n## The fight (what you can see)")
        lines.append(enc.status_text(reveal=False))
        if me:
            atks = "; ".join(a["name"] for a in me.get("attacks", [])) or "see your stat block"
            lines.append(f"You are **{me['id']}** in the tracker: HP {me['hp']}/{me['max_hp']}, AC {me['ac']}, speed "
                         f"{me.get('speed', 30)} ft · actions: {atks}")
        if enc.state.get("map"):
            from robodm import battlemap as bm

            lines.append("```\n" + bm.render_text(enc.state["map"], enc.combatants, (enc.current() or {}).get("id"),
                                                 reveal=False) + "\n```")
    lines.append(f"\n## Answer\nUse the INTENT / DOES / SAYS / HIDES / FRICTION shape. Record memories with "
                 f"`./dm {group} remember {slug} \"…\"`.")
    return lines


def mark_consulted(camp: Campaign, slug: str, st: dict) -> None:
    """This mind was consulted this turn (the stop hooks check it before a reply that voices them)."""
    from robodm import audit

    st["last_brief_turn"] = audit.current_turn(camp)
    st["consults"] = int(st.get("consults", 0)) + 1
    st["interject"] = None          # they're being heard now; they can raise it again if it's still open
    st.pop("voiced_unconsulted", None)
    save_state(camp, slug, st)


def dm_summary(camp: Campaign) -> list[str]:
    """One line per active companion for the DM's brief."""
    out = []
    for slug in active(camp):
        meta, _ = load_persona(camp, slug)
        st = state(camp, slug)
        att = ", ".join(f"{k.replace('-', ' ').title()} {v['score']:+d}" for k, v in st["attitude"].items())
        n_open = sum(1 for i in st["agenda"] if i["status"] == "open")
        bits = [st["status"]] + ([att] if att else []) + ([f"{n_open} agenda item(s)"] if n_open else [])
        line = f"- **{meta.get('name', slug)}** ({'; '.join(bits)}) — consult `{agent_name(camp, slug)}`"
        if st.get("interject"):
            line += f" · wants to speak up: {st['interject']['topic']}"
        out.append(line)
    return out


# =============================================================================== subagent files

def agents_dir() -> Path:
    """Where subagent files live (ROBODM_AGENTS_DIR overrides, e.g. in tests)."""
    import os

    env = os.environ.get("ROBODM_AGENTS_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent / ".claude" / "agents"


def agent_name(camp: Campaign, slug: str) -> str:
    """The subagent to use: companion-<npc> if its file exists for this campaign, else the generic `companion`."""
    for name in (f"companion-{slug}", f"companion-{camp.slug}-{slug}"):
        p = agents_dir() / f"{name}.md"
        if p.exists() and f"campaign: {camp.slug}" in p.read_text(encoding="utf-8"):
            return name
    return "companion"


def protocol(camp_slug: str | None, slug: str | None) -> str:
    c = f"-c {camp_slug} " if camp_slug else ""
    s = slug or "<npc>"
    return PROTOCOL.format(brief=f"./dm {c}companion brief {s}", recall=f'./dm {c}companion recall {s} "words"',
                           remember=f'./dm {c}companion remember {s} "…"',
                           agenda=f'./dm {c}companion agenda {s} add "goal" | done <id>',
                           attitude=f'./dm {c}companion attitude {s} <who> +1 --why "…"',
                           interject=f'./dm {c}companion interject {s} "topic"')


def generic_agent_text(model: str = "sonnet", effort: str = "") -> str:
    """The generic `companion` agent file. `model`/`effort`: the ⚙ choice or the turn speed (`costs.apply_speed`)."""
    return ("---\nname: companion\ndescription: Plays one RoboDM companion or scene NPC with their own mind (wants, "
            "values, methods, secrets, voice). Use when a companion without its own companion-<npc> agent is in the "
            "party or present and has to decide, react or speak; tell it the NPC's slug, the campaign, and the moment. "
            f"Sees only what that character knows.\ntools: Bash\nmodel: {model or 'sonnet'}\n"
            + (f"effort: {effort}\n" if effort else "") + "---\n\n"
            "The DM's message names the character (`<npc>` below: their slug, e.g. `okton`) and maybe the campaign "
            "(`-c <campaign>`). Substitute them in every command.\n\n" + protocol(None, None))


def write_agent(camp: Campaign, slug: str, model: str | None = None, effort: str | None = None) -> Path:
    """Write the companion's subagent file. Without a model or effort: the ⚙ choice, else the turn speed's model
    (`costs.agent_choice`)."""
    from robodm import costs

    meta, sections = load_persona(camp, slug)
    picked_model, picked_effort, _ = costs.agent_choice(camp, "companion")
    model = model or picked_model
    effort = picked_effort if effort is None else effort
    root = agents_dir()
    name = f"companion-{slug}"
    p = root / f"{name}.md"
    if p.exists() and f"campaign: {camp.slug}" not in p.read_text(encoding="utf-8"):
        name = f"companion-{camp.slug}-{slug}"
        p = root / f"{name}.md"
    core = ""
    if meta.get("lore"):
        from robodm import lore

        try:
            core = str(lore.find(camp, meta["lore"])["meta"].get("summary") or "")
        except RoboDMError:
            core = ""
    if not core:
        core = re.sub(r"^You are [^,.]+[,.]\s*", "", re.sub(r"\s+", " ", sections.get("Core", "")).strip())
    desc = (f"{meta.get('name', slug)}: {truncate(core, 220)} RoboDM companion agent for the {camp.name} campaign. "
            f"Use when {meta.get('name', slug)} is in the party or present in the scene and has to decide, react or speak.")
    text = (f"---\nname: {name}\ndescription: {desc.replace(chr(10), ' ')}\ntools: Bash\n"
            f"model: {model or 'sonnet'}\n" + (f"effort: {effort}\n" if effort else "") + "---\n\n"
            f"<!-- generated by `./dm companion agent {slug}` · campaign: {camp.slug} · edit the persona, not this file -->\n"
            f"You are **{meta.get('name', slug)}** (campaign: {camp.slug}). Your persona, memories and knowledge come from "
            f"your brief, every time; this file only tells you how to work.\n\n" + protocol(camp.slug, slug))
    write_text(p, text)
    return p
