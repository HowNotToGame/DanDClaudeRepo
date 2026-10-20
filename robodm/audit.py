"""Mandatory rules checking: a gate before every rules event and an audit after it.

Before (pre-check):
  * a code gate checks each rules command against the combat state and the rules data
    (whose turn it is, reactions, incapacitation, spell slots, the bonus-action spell rule…)
    and blocks illegal ones. `--ruling "why"` lets the DM override a code-gate problem; the
    override is logged as a house ruling and flagged to the post-audit.
  * every spell, condition, feature and judgement call must be cleared by the rules-lawyer
    the first time it comes up in a campaign. Clearances are cached in `cleared.json`, and
    their notes are printed every time the item is used.

After (post-audit):
  * every rules command that succeeds is recorded as a pending event. The Stop hook won't let
    the DM finish a reply while events are pending; the rules-lawyer marks them passed or
    failed, and failed ones stay open until the fix is applied (`--fix <id>`) or resolved.
  * backstop: if a reply ended with events still unaudited, the next rules command (and
    `session end`) is refused until they are audited.

Everything lives in campaigns/<slug>/audit/: events.jsonl, cleared.json, state.json.
"""

from __future__ import annotations

import os
import re
from typing import Any

from robodm.campaign import Campaign
from robodm.gatekeeper import pretool as hook_pretool  # noqa: F401
from robodm.util import RoboDMError, now_iso, read_json, read_jsonl, slugify, truncate, write_json, write_text

KINDS = ("spell", "condition", "feature", "ruling")
DAMAGE_TYPES = ("acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic", "piercing", "poison",
                "psychic", "radiant", "slashing", "thunder")
OPEN = ("pending", "failed")
STOP_BLOCKS_PER_TURN = 2

# (command, action) pairs that change game state through the rules. Anything not listed here
# (status, log, show, memory, lore, …) is never gated or recorded.
RULES_COMMANDS = {
    "combat": {"damage", "dmg", "heal", "temp", "condition", "cond", "save", "attack", "conc", "concentrate",
               "concentration", "deathsave", "stabilize", "legendary", "cast", "next", "end"},
    "pc": {"hp", "temp", "check", "save", "hitdie", "rest", "slot", "resource", "levelup", "cast", "build", "equip",
           "attune"},
    "ruling": {None},
}
# Rules commands that are mechanical bookkeeping: gated by the backstop but not recorded as events.
NOT_RECORDED = {("combat", "next")}


class RulesGateError(RoboDMError):
    """A rules command was refused before anything was rolled or changed."""


# =============================================================================== storage

def _dir(camp: Campaign):
    return camp.path("audit")


def _state(camp: Campaign) -> dict:
    st = read_json(_dir(camp) / "state.json") or {}
    st.setdefault("turn", 0)
    st.setdefault("lookups", [])
    st.setdefault("stop_blocks", {})
    return st


def _save_state(camp: Campaign, st: dict) -> None:
    write_json(_dir(camp) / "state.json", st)


def events(camp: Campaign) -> list[dict]:
    return read_jsonl(_dir(camp) / "events.jsonl")


def _write_events(camp: Campaign, evs: list[dict]) -> None:
    import json

    write_text(_dir(camp) / "events.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs))


def cleared(camp: Campaign) -> dict:
    return read_json(_dir(camp) / "cleared.json") or {}


def _save_cleared(camp: Campaign, data: dict) -> None:
    write_json(_dir(camp) / "cleared.json", data)


def item_key(kind: str, name: str) -> str:
    kind = kind.lower()
    if kind not in KINDS:
        raise RoboDMError(f"Unknown kind '{kind}'. Kinds: {', '.join(KINDS)}")
    return f"{kind}:{slugify(name)}"


def current_turn(camp: Campaign) -> int:
    return _state(camp)["turn"]


def bump_turn(camp: Campaign) -> int:
    st = _state(camp)
    st["turn"] += 1
    st["stop_blocks"] = {}
    _save_state(camp, st)
    return st["turn"]


def note_lookup(camp: Campaign, name: str) -> None:
    """`rules show` records what was looked up, so a chat-mode `--self` clearance can prove it read the text."""
    st = _state(camp)
    key = slugify(name)
    if key not in st["lookups"]:
        st["lookups"] = (st["lookups"] + [key])[-300:]
        _save_state(camp, st)


def save_last_reply(camp: Campaign, text: str) -> None:
    st = _state(camp)
    st["last_reply"] = truncate(text or "", 4000)
    _save_state(camp, st)


# =============================================================================== pre-check

def require_cleared(camp: Campaign, kind: str, name: str, use: str = "") -> dict:
    """The cached rules-lawyer clearance for an item, or a RulesGateError explaining how to get one."""
    key = item_key(kind, name)
    entry = cleared(camp).get(key)
    if entry and entry.get("verdict") == "CORRECT":
        return entry
    brief = f'./dm audit brief {kind} "{name}"' + (f' --use "{use}"' if use else "")
    if entry and entry.get("verdict") == "INCORRECT":
        raise RulesGateError(
            f"⛔ RULES GATE — the rules-lawyer DENIED {kind} **{name}**: {entry.get('notes') or 'no reason given'}"
            f"{' (' + entry['cite'] + ')' if entry.get('cite') else ''}.\n"
            f"Change the plan, then ask for a new pre-check: `{brief}`.")
    raise RulesGateError(
        f"⛔ RULES GATE — {kind} **{name}** hasn't been cleared by the rules-lawyer in this campaign yet.\n"
        f"Pre-check it: launch the rules-lawyer with the output of `{brief}` and what you intend to do. It records "
        f"`./dm audit clear …`; then re-run this command. (Cleared items stay cleared.)")


def clearance_note(entry: dict, kind: str, name: str) -> str:
    notes = entry.get("notes") or "no special constraints"
    return f"📘 Cleared {kind} {name}: {notes}" + (f" ({entry['cite']})" if entry.get("cite") else "")


def gate(camp: Campaign, problems: list[str], args, needs: list[tuple[str, str, str]] | None = None) -> list[str]:
    """Refuse the command if the code gate found problems (unless the DM passed --ruling), then require a
    rules-lawyer clearance for each (kind, name, intended use) in `needs`. Returns the clearance notes to print.

    Code-gate problems are reported first: they're cheap to fix, and there's no point pre-checking an
    action that can't happen anyway. `--ruling` never skips a clearance."""
    ruling = getattr(args, "ruling", None)
    if problems and not ruling:
        raise RulesGateError(
            "⛔ RULES GATE — blocked before anything was rolled or changed:\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\nFix the command. If the table is deliberately allowing it, re-run with `--ruling \"why\"` "
              "(logged as a house ruling and flagged to the post-audit).")
    notes = [clearance_note(require_cleared(camp, kind, name, use=use), kind, name) for kind, name, use in needs or []]
    if problems:
        _log_override(camp, problems, args)
    return notes


def _log_override(camp: Campaign, problems: list[str], args) -> None:
    ruling = getattr(args, "ruling", None)
    from robodm import memory

    memory.add(camp, f"Ruling (override): {ruling} — allowed despite: {'; '.join(problems)}", "rule", ["override"])
    overridden = getattr(args, "overridden", None)
    if overridden is None:
        overridden = []
        setattr(args, "overridden", overridden)
    overridden.extend(problems)


def clear(camp: Campaign, kind: str, name: str, verdict: str, notes: str = "", cite: str = "",
          self_check: bool = False) -> dict:
    verdict = verdict.upper()
    if verdict not in ("CORRECT", "INCORRECT"):
        raise RoboDMError("--verdict must be CORRECT (cleared) or INCORRECT (denied).")
    key = item_key(kind, name)
    if self_check:
        if kind == "ruling":
            if not cite:
                raise RoboDMError("A self-cleared ruling needs --cite (the rule text you based it on).")
        elif slugify(name) not in _state(camp)["lookups"]:
            raise RoboDMError(f"Look it up first: `dm rules show \"{name}\"`, read the text, then self-clear it.")
    entry = {"kind": kind, "name": name, "verdict": verdict, "notes": notes, "cite": cite,
             "by": "self" if self_check else "rules-lawyer", "ts": now_iso()}
    data = cleared(camp)
    data[key] = entry
    _save_cleared(camp, data)
    if verdict == "CORRECT" and kind == "ruling":
        from robodm import memory

        memory.add(camp, f"Ruling: {name} — {notes}" + (f" ({cite})" if cite else ""), "rule", ["ruling"])
    return entry


def reset_cleared(camp: Campaign, kind: str | None = None, name: str | None = None) -> int:
    data = cleared(camp)
    if kind and name:
        n = 1 if data.pop(item_key(kind, name), None) else 0
    else:
        n = len(data)
        data = {}
    _save_cleared(camp, data)
    return n


# =============================================================================== events & post-audit

def record_event(camp: Campaign, cmd: str, lines: list[str], overridden: list[str] | None = None,
                 fix_for: list[str] | None = None, facts: list[dict] | None = None) -> dict:
    evs = events(camp)
    combat = read_json(camp.path("combat.json")) or {}
    ev: dict[str, Any] = {
        "id": f"e{len(evs) + 1:04d}", "ts": now_iso(), "turn": current_turn(camp),
        "round": combat.get("round") if combat.get("active") else None,
        "cmd": cmd, "out": [truncate(x, 300) for x in lines if x.strip()][:25],
        "status": "fix" if fix_for else "pending",
    }
    if overridden:
        ev["override"] = overridden
    if fix_for:
        ev["fixes"] = fix_for
    if facts:
        ev["facts"] = facts[:40]
    evs.append(ev)
    if fix_for:
        for e in evs:
            if e["id"] in fix_for and e["status"] == "failed":
                e["status"] = "resolved"
                e["resolution"] = f"fixed by {ev['id']}: {cmd}"
    _write_events(camp, evs)
    return ev


def turn_facts(camp: Campaign, turn: int | None = None) -> list[dict]:
    """The mechanical results (receipts) of this turn's rules events."""
    turn = current_turn(camp) if turn is None else turn
    return [f for e in events(camp) if e.get("turn") == turn and e["status"] != "undone" for f in e.get("facts", [])]


def remember_roll(camp: Campaign, slug: str, scores: list[int]) -> None:
    """Rolled ability scores are kept per character (and survive `undo`): no re-rolling for better ones."""
    st = _state(camp)
    st.setdefault("rolled_scores", {})[slug] = scores
    _save_state(camp, st)


def rolled_scores(camp: Campaign, slug: str) -> list[int] | None:
    return (_state(camp).get("rolled_scores") or {}).get(slug)


def remember_ask(camp: Campaign, key: str, dc: int | None, vs: str | None) -> None:
    """Players roll their own dice: the DC is pinned when the DM asks for the roll."""
    st = _state(camp)
    st.setdefault("asked", {})[key] = {"dc": dc, "vs": vs, "turn": st["turn"]}
    _save_state(camp, st)


def asked(camp: Campaign, key: str, pop: bool = False) -> dict | None:
    st = _state(camp)
    rec = (st.get("asked") or {}).get(key)
    if rec and pop:
        st["asked"].pop(key, None)
        _save_state(camp, st)
    return rec


def open_events(camp: Campaign) -> list[dict]:
    return [e for e in events(camp) if e["status"] in OPEN]


def parse_ids(camp: Campaign, spec: list[str]) -> list[str]:
    """'all', 'e0003', 'e3-e7', '3,4' → event ids that exist."""
    evs = events(camp)
    known = [e["id"] for e in evs]
    joined = ",".join(spec)
    if joined.strip().lower() in ("all", "pending"):
        return [e["id"] for e in evs if e["status"] == "pending"]
    ids: list[str] = []
    for part in [p.strip() for p in joined.split(",") if p.strip()]:
        m = re.fullmatch(r"e?(\d+)\s*-\s*e?(\d+)", part, re.IGNORECASE)
        if m:
            ids.extend(f"e{n:04d}" for n in range(int(m.group(1)), int(m.group(2)) + 1))
            continue
        m = re.fullmatch(r"e?(\d+)", part, re.IGNORECASE)
        if not m:
            raise RoboDMError(f"Can't read event id '{part}' (use e12, e12-e15 or all).")
        ids.append(f"e{int(m.group(1)):04d}")
    missing = [i for i in ids if i not in known]
    if missing:
        raise RoboDMError(f"No such event(s): {', '.join(missing)}")
    return ids


def mark(camp: Campaign, ids: list[str], status: str, notes: str = "", fix: str = "", self_check: bool = False,
         by: str | None = None) -> list[dict]:
    evs = events(camp)
    changed = []
    for e in evs:
        if e["id"] not in ids:
            continue
        if status == "resolved" and e["status"] != "failed":
            raise RoboDMError(f"{e['id']} isn't a failed event (status: {e['status']}).")
        e["status"] = status
        e["by"] = by or ("self" if self_check else ("dm" if status == "resolved" else "rules-lawyer"))
        if notes:
            e["verdict_notes"] = notes
        if fix:
            e["fix"] = fix
        if status == "resolved":
            e["resolution"] = notes
        changed.append(e)
    _write_events(camp, evs)
    return changed


def backstop(camp: Campaign, command: str, action: str | None, fix_ids: list[str] | None = None) -> None:
    """Refuse new rules commands while an earlier reply's events are still unaudited."""
    opened = open_events(camp)
    if not opened:
        return
    if command == "session" and action == "end":
        raise RulesGateError(
            f"⛔ RULES GATE — {len(opened)} rules event(s) still unaudited ({_id_span(opened)}). "
            "Run the post-audit (`./dm audit pending` → rules-lawyer) before closing the session.")
    if fix_ids:
        return  # applying the rules-lawyer's fix is always allowed
    turn = current_turn(camp)
    stale = [e for e in opened if e.get("turn", 0) < turn]
    if stale:
        failed = [e for e in stale if e["status"] == "failed"]
        raise RulesGateError(
            f"⛔ RULES GATE — the previous reply left {len(stale)} rules event(s) unaudited or unfixed "
            f"({_id_span(stale)}). Finish the post-audit first: launch the rules-lawyer with `./dm audit pending`"
            + (f"; apply fixes with the printed command plus `--fix <id>`, or `./dm audit resolve <id> --how \"…\"`"
               if failed else "") + ".")


def _id_span(evs: list[dict]) -> str:
    ids = [e["id"] for e in evs]
    return ids[0] if len(ids) == 1 else f"{ids[0]}–{ids[-1]}"


# =============================================================================== packets for the rules-lawyer

def brief(camp: Campaign, db, kind: str, name: str, use: str = "") -> str:
    """Everything the rules-lawyer needs to pre-check an item."""
    from robodm import context_blocks, memory
    from robodm.rules.format import format_record

    item_key(kind, name)  # validates kind
    lines = [f"# Pre-check: {kind} {name}"]
    if use:
        lines.append(f"**DM intends:** {use}")
    prior = cleared(camp).get(item_key(kind, name))
    if prior:
        lines.append(f"**Previous verdict:** {prior['verdict']} — {prior.get('notes', '')}")
    if kind != "ruling":
        t = {"spell": "spell", "condition": "condition"}.get(kind)
        rec, alts = db.lookup(name, t) if db.available else (None, [])
        if rec:
            lines.append("\n## Rules text (local 5etools data)")
            lines.append(format_record(db, rec))
            mentioned = _mentioned_conditions(db, rec)
            todo = [c for c in mentioned if cleared(camp).get(item_key("condition", c), {}).get("verdict") != "CORRECT"]
            if todo:
                lines.append(f"\n_Also uncleared and mentioned here: condition {', '.join(todo)} — clear them in the same pass._")
        else:
            lines.append(f"\n_No entry named '{name}' in the local data"
                         + (f" (close: {', '.join(r['n'] for r in alts[:5])})" if alts else "")
                         + ". If it's homebrew, say so and judge it against the closest official rules._")
    rulings = [h for h in memory.search(camp, name, limit=8, scope="memory") if "Ruling" in h["show"]]
    if rulings:
        lines.append("\n## Earlier table rulings")
        lines.extend(f"- {h['show']}" for h in rulings[:5])
    lines.append("\n## House rules (override RAW when they apply)")
    lines.append(context_blocks.render(camp))
    lines.append(f"\n## Record your verdict\n`./dm audit clear {kind} \"{name}\" --verdict CORRECT|INCORRECT "
                 "--notes \"constraints the DM must follow every time (or why it's denied)\" --cite \"Book p.N\"`")
    return "\n".join(lines)


def _mentioned_conditions(db, rec: dict) -> list[str]:
    import json

    from robodm.combat import CONDITIONS

    try:
        text = json.dumps(db.get(rec)).lower()
    except Exception:
        return []
    return [c for c in CONDITIONS if f"@condition {c}" in text]


def pending_packet(camp: Campaign, log_lines: int = 30) -> str:
    """The post-audit packet: open events, overrides, the DM's last reply and the recent combat log."""
    opened = open_events(camp)
    if not opened:
        return "✅ Nothing to audit — every rules event has been checked."
    lines = [f"# Post-audit: {len(opened)} event(s) ({_id_span(opened)})"]
    for e in opened:
        tag = " ⚠️ FAILED — fix not applied yet" if e["status"] == "failed" else ""
        rnd = f" · round {e['round']}" if e.get("round") else ""
        lines.append(f"\n### {e['id']}{rnd}{tag}\n`{e['cmd']}`")
        if e.get("override"):
            lines.append("**DM OVERRIDE** (code gate blocked it; check the ruling is reasonable): "
                         + "; ".join(e["override"]))
        if e.get("fix"):
            lines.append(f"Fix requested: {e['fix']}")
        lines.extend(f"> {x}" for x in e.get("out", []))
    facts = [f for e in opened for f in e.get("facts", [])]
    if facts:
        from robodm import receipts

        lines.append("\n## Mechanical results (authoritative: the reply must not contradict these)")
        lines.extend(f"- {receipts.fmt(f)}" for f in facts)
    reply = _state(camp).get("last_reply")
    if reply:
        lines.append("\n## The DM's last reply (check rulings narrated here that never went through ./dm)")
        lines.append(reply)
    combat = read_json(camp.path("combat.json")) or {}
    if combat.get("active") and combat.get("log"):
        lines.append(f"\n## Combat log (last {log_lines})")
        lines.extend(combat["log"][-log_lines:])
    ids = ",".join(e["id"] for e in opened if e["status"] == "pending")
    lines.append("\n## Record verdicts\n"
                 f"`./dm audit pass {ids or '<ids>'} --notes \"…\"` for correct events; "
                 "`./dm audit fail <id> --notes \"what's wrong + rule\" --fix \"<exact ./dm command>\"` for each mistake.")
    return "\n".join(lines)


def status_text(camp: Campaign) -> str:
    evs = events(camp)
    counts: dict[str, int] = {}
    for e in evs:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    cl = cleared(camp)
    ok = sum(1 for v in cl.values() if v.get("verdict") == "CORRECT")
    return (f"Rules audit — turn {current_turn(camp)} · events: "
            + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none")
            + f" · cleared items: {ok}" + (f" (+{len(cl) - ok} denied)" if len(cl) > ok else ""))


# =============================================================================== Claude Code hooks
# (PreToolUse lives in robodm/gatekeeper.py so it loads fast; re-exported here as hook_pretool.)

def hook_prompt(camp: Campaign | None, data: dict) -> dict | None:
    """UserPromptSubmit: a new player message starts a new turn."""
    if camp is None:
        return None
    bump_turn(camp)
    opened = open_events(camp)
    if not opened:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
            f"RoboDM rules audit: {len(opened)} event(s) from the previous reply are still unaudited or unfixed "
            f"({_id_span(opened)}). Rules commands are locked until the rules-lawyer audits them "
            "(`./dm audit pending`)."}}


def autopass(camp: Campaign) -> list[str]:
    """turn_speed fast: this turn's routine mechanical events (no override) pass without the rules-lawyer. Only
    called once the receipt check found the narration agrees with the dice."""
    from robodm import costs

    if costs.speed(camp) != "fast":
        return []
    evs = events(camp)
    turn = current_turn(camp)
    passed = []
    for e in evs:
        if e.get("turn") == turn and costs.autopassable(e):
            e["status"] = "passed"
            e["by"] = "engine (turn_speed fast: computed by the engine, receipts agreed)"
            passed.append(e["id"])
    if passed:
        _write_events(camp, evs)
    return passed


def narration_problems(camp: Campaign, reply: str) -> list[str]:
    """Where the reply contradicts this turn's dice (receipts), deterministically."""
    from robodm import receipts

    combat = read_json(camp.path("combat.json")) or {}
    creatures = {c["name"]: c for c in (combat.get("combatants") or {}).values()} if combat.get("active") else {}
    return receipts.check(reply, turn_facts(camp), creatures, bool(camp.settings.get("hide_monster_hp", True)),
                          receipts.HIDDEN_CHECKS if _hidden_rolls(camp) else ())


def _hidden_rolls(camp: Campaign) -> bool:
    from robodm import context_blocks

    try:
        return context_blocks.is_enabled(camp, context_blocks.find(camp, "hidden-rolls"))
    except RoboDMError:
        return False


# The web client holds the reply until the turn ends, so a correction can replace the draft instead of trailing it.
REVISED = "[REVISED]"
WEB_REVISE = (f" (Web client: the players haven't seen your reply yet. To fix it, write the whole corrected reply "
              f"starting with the line {REVISED}; they will see only that version. Don't add notes about the fix.)")


def hook_stop(camp: Campaign | None, data: dict) -> dict | None:
    """Stop: the DM can't finish a reply that contradicts the dice, or with rules events unaudited."""
    if camp is None:
        return None
    reply = str(data.get("last_assistant_message") or "")
    st = _state(camp)
    key = str(st["turn"])
    if reply and not st.setdefault("narration_checked", {}).get(key):
        problems = narration_problems(camp, reply)
        st["narration_checked"] = {key: True}          # once per turn: a false alarm costs one extra line
        _save_state(camp, st)
        if not problems:
            autopass(camp)
        if problems:
            return {"decision": "block", "reason":
                    "RoboDM receipt check: your reply may not match the dice this turn:\n"
                    + "\n".join(f"- {p}" for p in problems)
                    + ("\nIf it really doesn't match, or breaks a table rule, fix it." + WEB_REVISE
                       + " If it's a false alarm, just finish." if os.environ.get("ROBODM_WEB_CLIENT") else
                       "\nIf it really doesn't, send the players a one-line correction (e.g. \"Correction: the "
                       "goblin's blade glances off; it missed.\"). If it's a false alarm, just finish.")}
    opened = open_events(camp)
    if not opened:
        return None
    if data.get("last_assistant_message"):
        save_last_reply(camp, data["last_assistant_message"])
    st = _state(camp)
    key = str(st["turn"])
    blocks = int(st["stop_blocks"].get(key, 0))
    if blocks >= STOP_BLOCKS_PER_TURN:
        return {"systemMessage": f"RoboDM: {len(opened)} rules event(s) still unaudited ({_id_span(opened)}); "
                                 "rules commands stay locked until the post-audit is done."}
    st["stop_blocks"][key] = blocks + 1
    _save_state(camp, st)
    failed = [e for e in opened if e["status"] == "failed"]
    reason = (f"RoboDM rules audit required before you finish: {len(opened)} event(s) ({_id_span(opened)}). "
              "Launch the rules-lawyer subagent with the output of `./dm audit pending` (it includes your reply). "
              "It records `./dm audit pass|fail`. For each failed event, run the fix it gives with `--fix <id>` "
              "added (or `./dm audit resolve <id> --how \"…\"`), then tell the players in one line.")
    if failed:
        reason += f" Unfixed failures: {', '.join(e['id'] for e in failed)}."
    if os.environ.get("ROBODM_WEB_CLIENT"):
        reason += (" (Web client: the players see nothing until you finish, and your reply so far is already kept. "
                   "Don't write a progress note: run the rules-lawyer in the foreground and wait for its verdict. "
                   "If nothing changes for the players, end with no further text. If the reply must change, write the "
                   f"whole corrected reply starting with the line {REVISED}; they will see only that version.)")
    return {"decision": "block", "reason": reason}
