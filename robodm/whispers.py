"""Private messages between the DM and one player: `./dm whisper mira "You notice the coin is fake."`

They're kept in campaigns/<slug>/whispers.jsonl and shown only to that player's seat in the web client (and to
the host). In the transcript they're marked 🔒 so every player-facing view leaves them out.
"""

from __future__ import annotations

from robodm import capture
from robodm.campaign import Campaign
from robodm.util import RoboDMError, append_jsonl, now_iso, read_jsonl


def _path(camp: Campaign):
    return camp.path("whispers.jsonl")


def all_whispers(camp: Campaign) -> list[dict]:
    return read_jsonl(_path(camp))


def add(camp: Campaign, to: str, text: str, frm: str = "dm") -> dict:
    """A whisper from the DM to a PC (frm="dm") or from a PC to the DM (frm=<pc slug>)."""
    text = (text or "").strip()
    if not text:
        raise RoboDMError("Say what to whisper: whisper <pc> \"…\"")
    pc = camp.get_pc(to if frm == "dm" else frm)
    rows = all_whispers(camp)
    rec = {"id": f"w{len(rows) + 1:04d}", "ts": now_iso(), "session": camp.session.get("number", 0),
           "from": frm, "to": pc["slug"] if frm == "dm" else "dm", "pc": pc["slug"], "name": pc["name"], "text": text}
    append_jsonl(_path(camp), rec)
    label = f"DM→{pc['name']}" if frm == "dm" else f"{pc['name']}→DM"
    capture.record(camp, "DM" if frm == "dm" else "Player", f"{capture.PRIVATE} [private {label}] {text}")
    return rec


def for_pc(camp: Campaign, slug: str) -> list[dict]:
    return [w for w in all_whispers(camp) if w.get("pc") == slug]
