"""Turn snapshots and `./dm undo`.

Before the first change in each turn (a turn = one player message in Claude Code; `./dm audit turn` elsewhere),
the engine zips the campaign's game state into campaigns/<slug>/.cache/snapshots/turn-NNNN.zip. The last 30 are
kept. `./dm undo` puts one back: HP, sheets, combat and map, memories, quests, lore, companions, the world clock.

What is never rolled back: the transcripts (journal/transcript-*.md, the record of what was said, where the
rewind itself is noted) and the rules audit (audit/: clearances are rules knowledge, not game state; events from
the undone turns stay in the log, marked `undone`, so they don't need auditing). Turn numbers only go forward.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from robodm.campaign import Campaign
from robodm.util import RoboDMError, now_iso, read_json, truncate, write_json

KEEP = 30


def _dir(camp: Campaign) -> Path:
    return camp.path(".cache", "snapshots")


def _index(camp: Campaign) -> dict:
    idx = read_json(_dir(camp) / "index.json") or {}
    idx.setdefault("snapshots", [])
    idx.setdefault("cursor", None)
    return idx


def _save_index(camp: Campaign, idx: dict) -> None:
    write_json(_dir(camp) / "index.json", idx)


def _rel(camp: Campaign, p: Path) -> str:
    return p.relative_to(camp.dir).as_posix()


def _kept(rel: str) -> bool:
    """Files that are never captured or rolled back."""
    return rel.startswith("audit/") or (rel.startswith("journal/transcript-") and rel.endswith(".md"))


def _files(camp: Campaign) -> list[Path]:
    from robodm import bundle

    return [p for p in bundle._campaign_files(camp) if not _kept(_rel(camp, p))]


def _label(camp: Campaign) -> str:
    from robodm import capture

    rows = capture.entries(camp) if camp.session.get("active") else []
    for _, who, text in reversed(rows):
        if who == "Player":
            return truncate(" ".join(text.split()), 120)
    return ""


def take(camp: Campaign, kind: str = "turn", label: str | None = None) -> dict | None:
    """Snapshot the game state. kind "turn": once per turn, before its first change; "redo": the state an undo
    replaced, so `undo --redo` can bring it back."""
    from robodm import audit

    turn = audit.current_turn(camp)
    idx = _index(camp)
    if kind == "turn" and any(s["kind"] == "turn" and s["turn"] == turn for s in idx["snapshots"]):
        return None
    name = f"turn-{turn:04d}.zip" if kind == "turn" else "redo.zip"
    d = _dir(camp)
    d.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(d / name, "w", zipfile.ZIP_DEFLATED) as z:
        for p in _files(camp):
            z.write(p, _rel(camp, p))
    rec = {"file": name, "kind": kind, "turn": turn, "ts": now_iso(),
           "label": _label(camp) if label is None else label, "events": len(audit.events(camp))}
    idx["snapshots"] = [s for s in idx["snapshots"] if s["file"] != name] + [rec]
    turns = sorted((s for s in idx["snapshots"] if s["kind"] == "turn"), key=lambda s: s["turn"])
    for old in turns[:-KEEP]:
        (d / old["file"]).unlink(missing_ok=True)
        idx["snapshots"].remove(old)
    if kind == "turn":
        idx["cursor"] = None          # new changes after an undo: undo counts back from here again
    _save_index(camp, idx)
    return rec


def ensure(camp: Campaign | None) -> None:
    """Called before every state-changing command: the first one in a turn takes the turn's snapshot."""
    if camp is None:
        return
    try:
        take(camp, "turn")
    except OSError:
        pass                          # a snapshot must never stop the game


def pop_notice(camp: Campaign) -> str | None:
    """A rewind the DM's conversation doesn't know about yet (shown once, by the prompt hook)."""
    idx = _index(camp)
    note = idx.pop("notice", None)
    if note:
        _save_index(camp, idx)
    return note


def listing(camp: Campaign) -> list[dict]:
    return sorted((s for s in _index(camp)["snapshots"] if s["kind"] == "turn"), key=lambda s: s["turn"])


def peek(camp: Campaign, steps: int = 1) -> dict | None:
    """The snapshot `undo` would restore."""
    idx = _index(camp)
    cands = [s for s in listing(camp) if idx["cursor"] is None or s["turn"] < idx["cursor"]]
    return cands[-steps] if len(cands) >= steps else None


def undo(camp: Campaign, steps: int = 1, to: int | None = None, redo: bool = False) -> list[str]:
    from robodm import audit, capture

    idx = _index(camp)
    if redo:
        snap = next((s for s in idx["snapshots"] if s["kind"] == "redo"), None)
        if snap is None:
            raise RoboDMError("Nothing to redo: `undo --redo` brings back the state the last undo replaced.")
    elif to is not None:
        snap = next((s for s in listing(camp) if s["turn"] == to), None)
        if snap is None:
            raise RoboDMError(f"No snapshot for turn {to}. Snapshots: "
                              + (", ".join(str(s["turn"]) for s in listing(camp)) or "none yet"))
    else:
        snap = peek(camp, steps)
        if snap is None:
            have = len([s for s in listing(camp) if idx["cursor"] is None or s["turn"] < idx["cursor"]])
            raise RoboDMError(f"Can't go back {steps} turn(s): {have} snapshot(s) available. Snapshots are taken "
                              "before the first change of each turn (`./dm undo --list`).")
    path = _dir(camp) / snap["file"]
    if not path.exists():
        raise RoboDMError(f"The snapshot file {snap['file']} is missing.")
    if not redo:
        take(camp, "redo", label="the state before the last undo")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for p in _files(camp):
            if _rel(camp, p) not in names:
                p.unlink()            # created after the snapshot (a new lore entry, world.json…)
        z.extractall(camp.dir)
    # Rules events from the undone turns: kept in the log, but they no longer need auditing.
    evs = audit.events(camp)
    undone = 0
    if not redo:
        for e in evs[snap.get("events", len(evs)):]:
            if e["status"] in ("pending", "failed", "passed", "fix"):
                e["status"] = "undone"
                e["undone_at_turn"] = audit.current_turn(camp)
                undone += 1
        audit._write_events(camp, evs)
    idx = _index(camp)
    idx["cursor"] = None if redo else snap["turn"]
    idx["notice"] = (f"the campaign was rewound to the start of turn {snap['turn']}"
                     + (f" (“{snap['label']}”)" if snap.get("label") else "")
                     if not redo else "the last undo was reverted (redo)")
    _save_index(camp, idx)
    camp = Campaign(camp.slug)        # reload: campaign.json was restored too
    what = (f"↶ Rewound to the start of turn {snap['turn']}" + (f" (“{snap['label']}”)" if snap.get("label") else "")
            if not redo else "↷ Redo: back to the state before the last undo")
    if camp.session.get("active"):
        camp.log(what)
        capture.record(camp, "RoboDM", what)
    lines = [what + "."]
    if undone:
        lines.append(f"{undone} rules event(s) from the undone turn(s) are marked undone (no audit needed).")
    if not redo:
        lines.append("Changed your mind? `./dm undo --redo` puts it back.")
    return lines
