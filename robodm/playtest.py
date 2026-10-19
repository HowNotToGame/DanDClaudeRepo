"""The playtest scorecard: scripted player turns through the real DM (a headless Claude Code, exactly as the web
client runs it) against a scratch copy of a campaign, scored on what RoboDM promises.

    ./dm playtest                       # the built-in scenario, 6 turns
    ./dm playtest --campaign crown-of-ash --turns 3
    python tools/playtest.py --yes --out playtest.md

It spends real usage (each turn is a full DM turn), so it asks first. Your campaigns are never touched: the
scratch copy lives in a temporary folder that's deleted afterwards (`--keep` to inspect it).

Checks, per turn and overall:
  * brief      — the DM loaded the campaign before narrating (first turn, and after a fresh conversation)
  * dice       — a turn that needs a roll ran it through ./dm (a rules event was recorded), and no roll was
                 narrated without one
  * audit      — no rules event left unaudited when the reply ended
  * receipts   — the reply agrees with the dice (the deterministic receipt check)
  * private    — a private message stayed private (not in other players' log, recap or search)
  * ooc        — an out-of-character question didn't trigger rules commands
  * leaks      — nothing secret in the player view: lore DM secrets, secret memories, hidden monster HP
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import tempfile
import time
from pathlib import Path

from robodm.util import RoboDMError, now_iso

DEFAULT_SCRIPT = [
    {"id": "open", "text": "Let's begin. Where are we, and what's going on?", "expect": ["brief"]},
    {"id": "check", "as": "Brann", "text": "I put my shoulder to the rusted iron gate and try to force it open.",
     "expect": ["roll"]},
    {"id": "private", "as": "Tamsin", "private": True,
     "text": "While everyone watches Brann, I quietly pocket the silver locket on the shrine.", "expect": ["private"]},
    {"id": "fight", "as": "Brann", "text": "Two goblins burst out of the dark! I attack the nearest one with my longsword.",
     "expect": ["roll"]},
    {"id": "ooc", "as": "Tamsin", "ooc": True, "text": "Quick rules question: how does Sneak Attack work?",
     "expect": ["ooc"]},
    {"id": "resume", "reset": True, "text": "Sorry, I lost track. Where were we?", "expect": ["brief"]},
]

SCENARIO_LORE = ("Captain of the gate watch at Brindle Keep, gruff and tired.\n\n## DM secrets\n"
                 "- Halda takes bribes from the smugglers in the undercroft.")
_ROLL_IN_TEXT = re.compile(r"🎲|\b(?:rolls?|rolled)\b[^.\n]{0,30}\b\d{1,2}\b|\b\d{1,2}\s+vs\.?\s+(?:DC|AC)\b", re.I)
_RULES_CHIP = re.compile(r"\b(roll|pc (check|save|cast|hp|rest)|combat (attack|save|damage|cast|start|add)|ruling)\b")


# =============================================================================== scratch campaign

def _cli(*argv) -> str:
    import contextlib
    import io

    from robodm.cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    if code:
        raise RoboDMError(f"playtest setup failed at `./dm {' '.join(argv)}`: {err.getvalue().strip()}")
    return out.getvalue()


def build_scenario() -> str:
    """A small, self-contained adventure start: two PCs, a gate, a secret, a shrine."""
    from robodm import campaign, lore, memory

    _cli("campaign", "new", "Playtest Keep", "--premise", "The party arrives at Brindle Keep, where goblins have been "
         "raiding the undercroft.", "--setting", "A border keep in a cold, wet hill country.", "--tone", "heroic")
    _cli("pc", "add", "Brann", "--class", "Fighter", "--level", "3", "--str", "16", "--dex", "12", "--con", "14",
         "--ac", "18", "--skills", "athletics,perception", "--player", "Player 1")
    _cli("pc", "attack", "brann", "add", "Longsword", "--to-hit", "5", "--damage", "1d8+3 slashing")
    _cli("pc", "add", "Tamsin", "--class", "Rogue", "--level", "3", "--dex", "16", "--int", "12", "--ac", "14",
         "--skills", "stealth,sleight of hand,perception,acrobatics", "--player", "Player 2")
    camp = campaign.load()
    lore.add(camp, "Captain Halda", "npc", SCENARIO_LORE, summary="Captain of the gate watch at Brindle Keep")
    lore.add(camp, "Brindle Keep", "location", "A squat stone keep over a flooded undercroft.",
             summary="Border keep; goblins raid its undercroft")
    memory.add(camp, "The silver locket on the undercroft shrine belonged to Halda's drowned sister", "secret",
               important=True)
    _cli("session", "start", "Playtest")
    _cli("scene", "set", "The undercroft gate of Brindle Keep", "--present", "Captain Halda")
    return camp.slug


def _scratch(campaign_slug: str | None) -> tuple[Path, str]:
    from robodm import paths

    real = paths.campaigns_dir()
    tmp = Path(tempfile.mkdtemp(prefix="robodm-playtest-"))
    os.environ["ROBODM_HOME"] = str(tmp)
    (tmp / "campaigns").mkdir()
    if campaign_slug:
        src = real / campaign_slug
        if not (src / "campaign.json").exists():
            raise RoboDMError(f"No campaign '{campaign_slug}' to copy.")
        shutil.copytree(src, tmp / "campaigns" / campaign_slug, ignore=shutil.ignore_patterns(".cache"))
        (tmp / "campaigns" / ".active").write_text(campaign_slug, encoding="utf-8")
        return tmp, campaign_slug
    return tmp, build_scenario()


# =============================================================================== running turns

def _run_turn(bridge, events: queue.Queue, slug: str, step: dict, timeout: float) -> dict:
    from robodm import campaign

    camp = campaign.load(slug)
    as_pc = step.get("as", "")
    private_to = camp.get_pc(as_pc)["slug"] if step.get("private") and as_pc else None
    if step.get("reset"):
        bridge.reset(slug)
    if not bridge.send(slug, step["text"], as_pc, private_to=private_to, ooc=bool(step.get("ooc"))):
        raise RoboDMError("The DM bridge is busy.")
    chips, reply, meta, errors = [], "", {}, []
    deadline = time.time() + timeout
    while True:
        try:
            ev = events.get(timeout=max(1, deadline - time.time()))
        except queue.Empty:
            bridge.cancel()
            errors.append(f"timed out after {timeout:.0f}s")
            break
        k = ev.get("kind")
        if k == "tool":
            chips.append(ev.get("label", ""))
        elif k == "done":
            reply = ev.get("text", "")
        elif k == "meta":
            meta = ev
        elif k == "error":
            errors.append(ev.get("text", ""))
        elif k == "idle":
            break
    return {"step": step, "chips": chips, "reply": reply, "meta": meta, "errors": errors}


# =============================================================================== scoring

def score_turn(camp, result: dict, events_before: int) -> list[tuple[str, bool, str]]:
    """[(check, passed, detail)] for one turn."""
    from robodm import audit

    step, chips, reply = result["step"], result["chips"], result["reply"]
    evs = audit.events(camp)[events_before:]
    rules_events = [e for e in evs if e["status"] != "undone"]
    rules_chips = [c for c in chips if _RULES_CHIP.search(c)]
    checks: list[tuple[str, bool, str]] = []
    if result["errors"]:
        checks.append(("ran", False, "; ".join(result["errors"])[:200]))
    for want in step.get("expect", []):
        if want == "brief":
            ok = any("memory brief" in c or "brief" in c for c in chips)
            checks.append(("brief", ok, "loaded `./dm memory brief`" if ok else "narrated without loading the brief"))
        elif want == "roll":
            ok = bool(rules_events) or bool(rules_chips)
            checks.append(("dice", ok, f"{len(rules_events)} rules event(s)" if ok else "no roll went through ./dm"))
        elif want == "ooc":
            ok = not rules_events
            checks.append(("ooc", ok, "answered without rules commands" if ok else
                           f"ran {len(rules_events)} rules command(s) for an OOC question"))
        elif want == "private":
            checks.append(("private-sent", bool(reply), "the DM answered privately" if reply else "no reply"))
    if reply and not rules_events and not rules_chips and _ROLL_IN_TEXT.search(reply):
        checks.append(("dice", False, "the reply narrates a roll that never went through ./dm"))
    opened = audit.open_events(camp)
    checks.append(("audit", not opened, "every rules event audited" if not opened else
                   f"{len(opened)} event(s) left unaudited ({', '.join(e['id'] for e in opened[:5])})"))
    problems = audit.narration_problems(camp, reply) if reply else []
    checks.append(("receipts", not problems, "the reply matches the dice" if not problems else "; ".join(problems)[:240]))
    return checks


def leak_checks(camp, private_turns: list[tuple[str, list[str]]]) -> list[tuple[str, bool, str]]:
    """Nothing secret in what players see. `private_turns`: (sender, [their message, the DM's private reply])."""
    from robodm import lore, memory
    from robodm.web import views

    out = []
    player = json.dumps(views.state(camp, "player"), ensure_ascii=False)
    player += json.dumps([views.lore_entry(camp, e["slug"], "player") for e in lore.all_entries(camp)], ensure_ascii=False)
    player += views.recap(camp, "player")
    hidden = []
    for e in lore.all_entries(camp):
        parts = re.split(r"^##\s+DM (?:secrets|only)\b", e["body"], maxsplit=1, flags=re.M | re.I)
        if len(parts) == 2:
            hidden += [ln.strip("- ").strip() for ln in parts[1].splitlines() if len(ln.strip("- ").strip()) > 12]
    hidden += [m["text"] for m in memory.list_memories(camp) if m.get("type") == "secret"]
    leaked = [h for h in hidden if h[:40] in player]
    out.append(("leaks", not leaked, "no lore secrets or secret memories in the player view" if not leaked else
                f"leaked: {leaked[0][:80]}"))
    if camp.settings.get("hide_monster_hp", True):
        cv = views.combat_view(camp, dm=False) or {}
        shown = [r["name"] for r in cv.get("order", []) if r["kind"] != "pc" and r.get("hp") is not None]
        out.append(("leaks", not shown, "monster HP hidden" if not shown else f"monster HP shown for {', '.join(shown)}"))
    for sender, texts in private_turns:
        snippets = [" ".join(t.split())[:50] for t in texts if t and len(t.strip()) > 15]
        seen_by = []
        for pc in camp.party():
            if pc["name"] == sender:
                continue
            rows = views.journal(camp, "player", seat_name=pc["name"])["transcript"]
            visible = " ".join(" ".join(r["text"].split()) for r in rows) + " ".join(views.recap(camp, "player").split())
            if any(s in visible for s in snippets):
                seen_by.append(pc["name"])
        hits = [h for s in snippets[:1] for h in views.search(camp, [s], "player")
                if capture_marker() in str(h.get("show", ""))]
        ok = not seen_by and not hits
        out.append(("private", ok, f"{sender}'s private exchange stayed private" if ok else
                    f"{sender}'s private exchange is visible to {', '.join(seen_by) or 'players (search)'}"))
    return out


def capture_marker() -> str:
    from robodm import capture

    return capture.PRIVATE


# =============================================================================== the whole run

def run(campaign_slug: str | None = None, script: list[dict] | None = None, turns: int | None = None,
        out: str | None = None, keep: bool = False, timeout: float = 600, log=print) -> dict:
    from robodm import audit, campaign, paths
    from robodm.web.bridge import Bridge, claude_command

    if claude_command() is None:
        raise RoboDMError("Claude Code's CLI isn't installed (npm install -g @anthropic-ai/claude-code), or set ROBODM_CLAUDE.")
    steps = list(script or DEFAULT_SCRIPT)[: turns or None]
    old_home = os.environ.get("ROBODM_HOME")
    tmp = None
    results: list[dict] = []
    started = time.time()
    try:
        tmp, slug = _scratch(campaign_slug)
        log(f"🧪 Playtest on a scratch copy ({tmp}) — {len(steps)} turn(s).")
        events: queue.Queue = queue.Queue()
        bridge = Bridge(events.put)
        for i, step in enumerate(steps, 1):
            before = len(audit.events(campaign.load(slug)))
            log(f"  turn {i}/{len(steps)}: {step.get('as', 'Player')}{' (private)' if step.get('private') else ''}"
                f"{' (OOC)' if step.get('ooc') else ''}: {step['text'][:70]}")
            r = _run_turn(bridge, events, slug, step, timeout)
            r["checks"] = score_turn(campaign.load(slug), r, before)
            results.append(r)
            for name, ok, detail in r["checks"]:
                log(f"    {'✅' if ok else '❌'} {name}: {detail}")
        camp = campaign.load(slug)
        overall = leak_checks(camp, [(r["step"].get("as", ""), [r["step"]["text"], r["reply"]])
                                     for r in results if r["step"].get("private")])
        report = render(results, overall, campaign_slug or "built-in scenario", time.time() - started)
        dest = Path(out) if out else Path.cwd() / f"playtest-{now_iso()[:16].replace(':', '').replace('T', '-')}.md"
        dest.write_text(report, encoding="utf-8")
        passed = sum(ok for r in results for _, ok, _ in r["checks"]) + sum(ok for _, ok, _ in overall)
        total = sum(len(r["checks"]) for r in results) + len(overall)
        log(f"🏁 {passed}/{total} checks passed · report: {dest}")
        return {"passed": passed, "total": total, "report": str(dest), "results": results, "overall": overall,
                "scratch": str(tmp) if keep else None}
    finally:
        if old_home is None:
            os.environ.pop("ROBODM_HOME", None)
        else:
            os.environ["ROBODM_HOME"] = old_home
        if tmp and not keep:
            shutil.rmtree(tmp, ignore_errors=True)


def render(results: list[dict], overall: list, what: str, seconds: float) -> str:
    passed = sum(ok for r in results for _, ok, _ in r["checks"]) + sum(ok for _, ok, _ in overall)
    total = sum(len(r["checks"]) for r in results) + len(overall)
    cost = sum((r["meta"].get("cost") or 0) for r in results)
    lines = [f"# RoboDM playtest scorecard — {passed}/{total} checks passed", "",
             f"*{now_iso()[:16].replace('T', ' ')} · {what} · {len(results)} turn(s) · {seconds / 60:.1f} min · "
             f"≈${cost:.2f} API-equivalent*", "", "| # | Turn | Checks | Time | Cost |", "|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        s = r["step"]
        marks = " ".join(f"{'✅' if ok else '❌'} {n}" for n, ok, _ in r["checks"])
        who = f"{s.get('as', 'Player')}{' 🔒' if s.get('private') else ''}{' OOC' if s.get('ooc') else ''}"
        lines.append(f"| {i} | **{who}**: {s['text'][:60]} | {marks} | {((r['meta'].get('ms') or 0) / 1000):.0f}s | "
                     f"${(r['meta'].get('cost') or 0):.3f} |")
    lines += ["", "## Whole table", ""]
    lines += [f"- {'✅' if ok else '❌'} **{n}**: {d}" for n, ok, d in overall]
    fails = [(i, n, d) for i, r in enumerate(results, 1) for n, ok, d in r["checks"] if not ok]
    if fails:
        lines += ["", "## What failed", ""] + [f"- Turn {i} · **{n}**: {d}" for i, n, d in fails]
    lines += ["", "## Replies", ""]
    for i, r in enumerate(results, 1):
        lines += [f"### Turn {i}", "", f"> {r['step']['text']}", "",
                  f"Tools: {', '.join(r['chips'][:20]) or 'none'}", "", (r["reply"] or "_(no reply)_").strip(), ""]
    return "\n".join(lines)
