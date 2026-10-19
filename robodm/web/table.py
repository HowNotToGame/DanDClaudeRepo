"""Seats and rounds: who plays which character in the web client, and "round mode" (everyone declares, then the
DM resolves the round in one go).

A seat is a player's join link, `/?seat=<pc>&key=<key>`. The key is derived from the table's access token, so
`./dm web --new-token` revokes every seat too. A seat sees its own full sheet and the party's public side, its own
private messages, and the player view only (never the DM view); it can roll, chat as its character, whisper the
DM, declare round actions, level up and equip its own character, and propose moves on the map.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from robodm.campaign import Campaign
from robodm.util import now_iso, read_json, slugify, write_json


def seat_key(token: str, slug: str) -> str:
    digest = hmac.new(token.encode("utf-8"), f"seat:{slug}".encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")[:22]


def check_seat(token: str, slug: str, key: str) -> bool:
    return bool(slug and key) and hmac.compare_digest(seat_key(token, slug), key)


SEAT_COMMANDS = {"levelup", "equip", "unequip", "attune", "unattune", "show", "json", "load"}


def seat_may_run(argv: list[str], slug: str) -> bool:
    """The ./dm commands a seat may run from the web client: dice, and its own character's sheet."""
    if argv[:1] == ["roll"]:
        return True
    return len(argv) >= 3 and argv[0] == "pc" and argv[1] in SEAT_COMMANDS and slugify(argv[2]) == slug


# =============================================================================== round mode

def _path(camp: Campaign):
    return camp.path(".cache", "web-round.json")


def round_state(camp: Campaign) -> dict:
    st = read_json(_path(camp)) or {}
    party = [pc["name"] for pc in camp.party()]
    actions = st.get("actions") or {}
    return {"active": bool(st.get("active")), "actions": actions, "started": st.get("started"),
            "waiting": [n for n in party if n not in actions] if st.get("active") else []}


def start_round(camp: Campaign) -> dict:
    write_json(_path(camp), {"active": True, "actions": {}, "started": now_iso()})
    return round_state(camp)


def stop_round(camp: Campaign) -> None:
    write_json(_path(camp), {"active": False, "actions": {}})


def declare(camp: Campaign, who: str, text: str) -> dict:
    st = read_json(_path(camp)) or {}
    if not st.get("active"):
        from robodm.util import RoboDMError

        raise RoboDMError("No round is being gathered right now.")
    st.setdefault("actions", {})[who] = text.strip()
    write_json(_path(camp), st)
    return round_state(camp)


def compose(camp: Campaign) -> str | None:
    """The round as one message for the DM (and the round resets)."""
    st = round_state(camp)
    if not st["actions"]:
        return None
    lines = ["[Round of declared actions — the players declared these together; resolve the round in a sensible "
             "order (initiative in combat, simultaneous where it fits) and narrate it as one beat.]"]
    lines += [f"- {who}: {text}" for who, text in st["actions"].items()]
    if st["waiting"]:
        lines.append(f"(No action declared: {', '.join(st['waiting'])}.)")
    start_round(camp)
    return "\n".join(lines)
