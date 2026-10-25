"""Claude Code hooks, run as `./dm hook <mode>` with the hook's JSON on stdin.

Going through the `dm` launcher means hooks use the same working Python as every other command (on
Windows, `python3` is often the Microsoft Store placeholder, so hooks can't just call it).

  session-start  sync campaigns from the newest git branch (cloud: apply; desktop: report), fetch the rules
                 data in a fresh cloud container, and after compaction, clear or resume re-inject the brief
                 (with the last transcript exchanges) so the DM carries on mid-scene
  pretool        PreToolUse: only the rules-lawyer records `./dm audit` verdicts; no direct edits to game state
                 (also inspects code-execution MCP tools such as context-mode's ctx_execute)
  prompt         UserPromptSubmit: a new turn; the player's message goes into the session transcript; reminds
                 the DM of names from its last reply that still aren't recorded
  stop           Stop: the DM's reply goes into the transcript; the DM can't finish while rules events are
                 unaudited

ROBODM_CAPTURE=off is for coding sessions in this repo: `prompt` and `stop` do nothing (no transcript entry, no
new audit turn, no audit block), so a campaign left mid-session doesn't record the chat as play. The pretool gate
and session-start still run.

A hook must never wedge the session, so every error is reported on stderr and the action goes through.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys

MODES = ("session-start", "pretool", "prompt", "stop")
_OFF = ("off", "0", "no", "false")


def _env_off(name: str) -> bool:
    return os.environ.get(name, "").lower() in _OFF


def main(mode: str) -> int:
    """`dm hook <mode>`: hook JSON on stdin, the hook's answer on stdout. Always exits 0."""
    stdin = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    text = run(mode, stdin)
    if text:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.flush()
    return 0


def run(mode: str, stdin_text: str) -> str:
    """The hook's stdout for this mode and input ('' when it has nothing to say)."""
    try:
        data = json.loads(stdin_text) if stdin_text.strip() else {}
    except ValueError:
        if mode != "session-start":
            return ""
        data = {}
    if not isinstance(data, dict):
        data = {}
    if mode in ("prompt", "stop") and _env_off("ROBODM_CAPTURE"):
        return ""
    try:
        if mode == "session-start":
            return session_start(data)
        if mode == "pretool":
            from robodm.gatekeeper import pretool

            result = pretool(data)
        elif mode == "prompt":
            result = prompt(_camp(), data)
        elif mode == "stop":
            result = stop(_camp(), data)
        else:
            return ""
    except Exception as e:  # a broken hook must not block the session
        print(f"RoboDM hook error ({mode}): {e}", file=sys.stderr)
        return ""
    return json.dumps(result) if result else ""


def _camp():
    from robodm import campaign
    from robodm.util import RoboDMError

    try:
        return campaign.load()
    except RoboDMError:
        return None


def prompt(camp, data: dict) -> dict | None:
    from robodm import audit, capture

    if camp is None:
        return None
    text = str(data.get("prompt") or "")
    if text.startswith("[RoboDM web client]"):
        from robodm.web.bridge import player_text

        text = player_text(text)
    private = re.match(r"^\[Private from (.+?) — [^\]]*\]\s*", text)
    if private:                      # only that player and the DM see this exchange (the reply is marked in stop())
        text = f"{capture.PRIVATE} [private {private.group(1)}→DM] {text[private.end():]}"
    _set_private_turn(camp, private.group(1) if private else None)
    if text.strip() and not text.lstrip().startswith("/"):
        capture.record(camp, "Player", text)
    result = audit.hook_prompt(camp, data)
    names = capture.pop_unrecorded(camp) if camp.session.get("active") else []
    notes = []
    if names:
        notes.append("RoboDM memory: names from your last reply that aren't recorded anywhere yet: "
                     + ", ".join(names) + ". If they matter, record them (`./dm lore add …` or `./dm memory add …`); "
                     "a named person also needs an NPC record (`./dm npc add …`, or the npc-creator agent).")
    notes.extend(companion_notes(camp))
    notes.extend(npc_notes(camp))
    notes.extend(world_notes(camp))
    from robodm import snapshots

    rewound = snapshots.pop_notice(camp)
    if rewound:
        notes.insert(0, f"RoboDM: {rewound}. Everything after that point didn't happen: HP, positions, memories and "
                        "the clock are back to how they were. Reload `./dm memory brief` (and `./dm combat status`) "
                        "before you narrate, and don't refer to the undone events.")
    if notes:
        ctx = (result or {}).get("hookSpecificOutput", {}).get("additionalContext")
        note = "\n".join(notes)
        result = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                         "additionalContext": f"{ctx}\n{note}" if ctx else note}}
    return result


def world_notes(camp) -> list[str]:
    """Off-screen beats the engine rolled that the DM hasn't turned into events yet."""
    from robodm import world

    beats = world.open_beats(world.load(camp))
    if not beats:
        return []
    return [f"RoboDM world: {len(beats)} off-screen beat(s) are waiting ({', '.join(b['id'] for b in beats[:8])}): "
            "`./dm world`. Resolve each in a line (`./dm world resolve <id> \"…\"`) before the party next meets the "
            "people involved."]


def companion_notes(camp) -> list[str]:
    """Companions that want to speak up, or that the DM voiced last turn without asking their agent."""
    from robodm import companions as co

    if camp.settings.get("companion_agents", "key") == "off":
        return []
    out = []
    for slug in co.active(camp):
        st = co.state(camp, slug)
        meta, _ = co.load_persona(camp, slug)
        agent = co.agent_name(camp, slug)
        if st.get("interject"):
            out.append(f"RoboDM companions: {meta.get('name', slug)} wants to speak up about: {st['interject']['topic']}. "
                       f"Consult `{agent}` (slug {slug}) and give them their moment.")
        if st.pop("voiced_unconsulted", False):
            out.append(f"RoboDM companions: your last reply voiced {meta.get('name', slug)} without consulting `{agent}`. "
                       "Their choices and words come from their own agent: consult it at the next decision point.")
            co.save_state(camp, slug, st)
    return out


def companion_stop(camp, reply: str) -> str | None:
    """Flag companions the DM voiced this turn without consulting their agent. In `always` mode, block once."""
    import re

    from robodm import audit, companions as co

    mode = camp.settings.get("companion_agents", "key")
    if mode == "off" or not reply:
        return None
    turn = audit.current_turn(camp)
    missed = []
    for slug in co.active(camp):
        st = co.state(camp, slug)
        if st.get("last_brief_turn", -1) >= turn:
            continue
        meta, _ = co.load_persona(camp, slug)
        first = str(meta.get("name", slug)).split()[0]
        for para in re.split(r"\n\s*\n", reply):
            if re.search(rf"\b{re.escape(first)}\b", para, re.IGNORECASE) and re.search(r'["“”]', para):
                missed.append((slug, meta.get("name", slug), co.agent_name(camp, slug)))
                break
    if not missed:
        return None
    block = None
    for slug, _, _ in missed:
        st = co.state(camp, slug)
        st["voiced_unconsulted"] = True
        if mode == "always" and st.get("stop_block_turn") != turn:
            st["stop_block_turn"] = turn
            block = (f"RoboDM companions (companion_agents=always): you voiced {', '.join(n for _, n, _ in missed)} "
                     "without consulting their agent. Launch " + ", ".join(f"`{a}` (slug {s})" for s, _, a in missed)
                     + " with the moment, then revise what they say and do to match.")
        co.save_state(camp, slug, st)
    return block


def _npc_note_path(camp):
    return camp.path(".cache", "npc-notes.json")


def npc_notes(camp) -> list[str]:
    """People in the scene, or named in the last reply, who have no NPC record yet (each once per session)."""
    from robodm import npcs, world
    from robodm.util import read_json, write_json

    st = read_json(_npc_note_path(camp)) or {}
    sess = camp.session.get("number", 0)
    told = set(st.get("told", [])) if st.get("session") == sess else set()
    names = list(st.get("pending", []))
    names += [n for s, n in world.scene_cast(camp) if not npcs.find_or_none(camp, s) and not npcs.find_or_none(camp, n)]
    fresh = [n for n in dict.fromkeys(names) if n not in told and not npcs.find_or_none(camp, n)]
    write_json(_npc_note_path(camp), {"session": sess, "told": sorted(told | set(fresh)), "pending": []})
    if not fresh:
        return []
    return ["RoboDM NPCs: no NPC record yet for " + ", ".join(fresh) + ". Every named person has a record and a mind: "
            "create it before they speak (`./dm npc add \"Name\" --race … --alignment … --backstory \"…\" "
            "--statblock commoner|MM` for a minor one; launch the npc-creator agent for anyone significant or several "
            "at once)."]


def npc_stop(camp, reply: str) -> str | None:
    """Every named NPC speaks through their own mind. Block once per turn when the reply voices one (their name in
    the narration of a paragraph with quoted speech) whose brief didn't run this turn."""
    import re

    from robodm import audit, companions as co, npcs
    from robodm.util import read_json, write_json

    if not reply:
        return None
    paras = [p for p in re.split(r"\n\s*\n", reply) if re.search(r'["“”]', p)]
    if not paras:
        return None
    # Names inside someone's quoted words are mentions, not speakers: look at the narration around the quotes.
    narration = [re.sub(r'"[^"]*"|“[^”]*”', " ", p) for p in paras]
    turn = audit.current_turn(camp)
    missed = []
    for rec in npcs.all_records(camp):
        if rec["_kind"] != "npc" or rec.get("status", "alive") not in ("alive", "missing"):
            continue
        if not any(re.search(rf"\b{re.escape(k)}\b", n) for k in npcs.name_keys(rec) for n in narration):
            continue
        if co.state(camp, rec["slug"]).get("last_brief_turn", -1) >= turn:
            continue
        missed.append(rec)
    if not missed:
        return None
    path = camp.path(".cache", "npc-stop.json")
    if (read_json(path) or {}).get("turn") == turn:
        return None
    write_json(path, {"turn": turn, "missed": [r["slug"] for r in missed]})
    import os

    fix = (" (Web client: start the revised reply with the line [REVISED]; the players will see only that version.)"
           if os.environ.get("ROBODM_WEB_CLIENT") else "")
    return ("RoboDM NPC minds: your reply voices " + ", ".join(r["name"] for r in missed) + " without consulting their "
            "mind. Every named NPC speaks and decides through their own subagent: launch "
            + ", ".join(f"`{npcs.mind_agent(camp, r)}` (slug {r['slug']})" for r in missed)
            + " (in parallel, in one message) with the moment from their point of view, then rewrite what they say and "
            "do to match their answers." + fix)


def _set_private_turn(camp, who: str | None) -> None:
    from robodm import audit

    st = audit._state(camp)
    if who:
        st["private_turn"] = who
    else:
        st.pop("private_turn", None)
    audit._save_state(camp, st)


def stop(camp, data: dict) -> dict | None:
    from robodm import audit, capture

    if camp is None:
        return None
    reply = str(data.get("last_assistant_message") or "")
    private = audit._state(camp).get("private_turn")
    if private and reply.strip():
        reply = f"{capture.PRIVATE} [private DM→{private}] {reply}"
    if reply.strip() and capture.record(camp, "DM", reply):
        try:
            from robodm.rules import default_db

            db = default_db()
        except Exception:
            db = None
        names = capture.unrecorded_names(camp, reply, db)
        if names:
            capture.remember_unrecorded(camp, names)
        _remember_people_without_records(camp, reply)
    comp = " ".join(x for x in (companion_stop(camp, reply), npc_stop(camp, reply)) if x)
    result = audit.hook_stop(camp, data)
    if comp:
        if result and result.get("decision") == "block":
            result["reason"] += " Also: " + comp
        else:
            result = {"decision": "block", "reason": comp}
    return result


def _remember_people_without_records(camp, reply: str) -> None:
    """Bold names in the reply that are NPCs in the lore but have no record: noted for the next prompt."""
    from robodm import capture, lore, npcs
    from robodm.util import RoboDMError, read_json, write_json

    pending = []
    for name in capture.candidate_names(reply):
        if npcs.find_or_none(camp, name):
            continue
        try:
            e = lore.find(camp, name)
        except RoboDMError:
            continue
        if e["meta"].get("type") == "npc":
            pending.append(str(e["meta"].get("name", name)))
    if pending:
        st = read_json(_npc_note_path(camp)) or {}
        st["pending"] = list(dict.fromkeys(list(st.get("pending", [])) + pending))
        write_json(_npc_note_path(camp), st)


def _captured(argv: list[str]) -> str:
    from robodm.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(argv)
    return buf.getvalue().strip()


def session_start(data: dict) -> str:
    import tempfile
    from pathlib import Path

    from robodm import paths
    from robodm.rules import fetch as fetch_mod

    lines: list[str] = []
    lines.extend(_sync_lines())
    # A fresh cloud clone has no rules data (it's gitignored): fetch it. Never block the session on failure.
    if os.environ.get("CLAUDE_CODE_REMOTE") == "true" and not fetch_mod.is_fetched(paths.data_dir()):
        log = Path(tempfile.gettempdir()) / "robodm-fetch.log"
        try:
            with open(log, "w", encoding="utf-8") as fh, contextlib.redirect_stdout(fh), contextlib.redirect_stderr(fh):
                from robodm.cli import main

                ok = main(["rules", "fetch", "--if-missing"]) == 0
        except Exception:
            ok = False
        if not ok:
            lines.append(f"RoboDM: could not download the 5etools rules data (see {log}). Rules lookups will fail "
                         "until `./dm rules fetch` succeeds; the environment's network policy must allow "
                         "raw.githubusercontent.com.")

    camp = _camp()
    if data.get("source") in ("compact", "clear", "resume") and camp is not None and camp.session.get("active"):
        lines.append(f"RoboDM: a play session is in progress. Context was {data['source']}ed. Here is the campaign "
                     "state; keep running the game with the dm skill.")
        lines.append(_captured(["memory", "brief"]))
        lines.append("")
        lines.append(_captured(["context", "render"]))
        combat = _captured(["combat", "status"])
        if combat and not combat.startswith("No active combat"):
            lines.append("")
            lines.append(combat)
        return "\n".join(lines)
    lines.extend(f"RoboDM: {x}" for x in _captured(["status"]).splitlines()[:4])
    return "\n".join(lines)


def _sync_lines() -> list[str]:
    """Cloud: pull the newest campaign state from any branch. Desktop: only say a newer one exists.
    ROBODM_SYNC=off skips it (e.g. offline)."""
    if _env_off("ROBODM_SYNC"):
        return []
    from robodm import sync

    try:
        if not sync.in_repo():
            return []
        cloud = os.environ.get("CLAUDE_CODE_REMOTE") == "true"
        state = sync.check(fetch=True, fetch_timeout=30 if cloud else 8)
        if cloud:
            return [f"RoboDM sync: {x}" for x in sync.apply(state) if not x.startswith("✅")]
        return [f"RoboDM: newer campaign state for {sync.describe(i)}. Run `./dm sync` before playing to pull it in."
                for i in state["newer"]]
    except Exception as e:
        return [f"RoboDM: campaign sync skipped ({e})."]
