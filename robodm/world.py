"""The living world: in-game time and calendar, progress clocks and deadlines, factions, timed effects, the
current scene (who is here to witness things), NPC minds, and the off-screen tick.

Like the rules gate, code decides *what is due* and rolls the dice; the DM decides *what happened* and records
it (`./dm world resolve`). Everything lives in campaigns/<slug>/world.json, committed with the campaign. A
campaign that never ran `./dm time set` works as before: nothing here is required.
"""

from __future__ import annotations

import math
import re

from robodm import dice
from robodm.campaign import Campaign
from robodm.util import RoboDMError, now_iso, read_json, slugify, truncate, write_json

DAY = 1440
WEEK = 7 * DAY

TIME_OF_DAY = ((0, "night"), (5 * 60, "dawn"), (7 * 60, "morning"), (12 * 60, "noon"), (13 * 60, "afternoon"),
               (17 * 60, "evening"), (21 * 60, "night"))
NAMED_TIMES = {"midnight": 0, "dawn": 6 * 60, "sunrise": 6 * 60, "morning": 8 * 60, "noon": 12 * 60, "midday": 12 * 60,
               "afternoon": 15 * 60, "dusk": 18 * 60, "sunset": 18 * 60, "evening": 19 * 60, "night": 22 * 60}
CALENDARS = ("simple", "harptos")

# Harptos (Forgotten Realms): twelve 30-day months, five festival days, Shieldmeet every fourth year.
_HARPTOS = (("Hammer", "Midwinter"), ("Alturiak", None), ("Ches", None), ("Tarsakh", "Greengrass"), ("Mirtul", None),
            ("Kythorn", None), ("Flamerule", "Midsummer"), ("Eleasis", None), ("Eleint", "Highharvestide"),
            ("Marpenoth", None), ("Uktar", "Feast of the Moon"), ("Nightal", None))

REP_WORDS = {-5: "Nemesis", -4: "Hostile", -3: "Hostile", -2: "Unfriendly", -1: "Wary", 0: "Neutral", 1: "Cordial",
             2: "Friendly", 3: "Friendly", 4: "Allied", 5: "Champion"}
PACE = {"slow": -1, "normal": 0, "fast": 1}
# Memory types that describe something happening in a scene: the people present witnessed them.
WITNESSED_TYPES = {"event", "choice", "relationship", "quest", "world"}
MIND_FIELDS = (("feeling", "feeling:"), ("goal", "goal:"), ("opinion", "view of the party:"), ("plan", "plan:"))
TIERS = (("MICRO", 60), ("MINOR", DAY), ("MAJOR", WEEK), ("HEAVY", None))
BEAT_COOLDOWN = 12 * 60   # a resolved beat about the same NPC/agenda doesn't come back for 12 in-game hours


# =============================================================================== state

def path(camp: Campaign):
    return camp.path("world.json")


def load(camp: Campaign) -> dict:
    w = read_json(path(camp)) or {}
    w.setdefault("time", {})
    w.setdefault("scene", {})
    w.setdefault("clocks", [])
    w.setdefault("factions", {})
    w.setdefault("timers", [])
    w.setdefault("last_long_rest", {})
    w.setdefault("offscreen", [])
    return w


def save(camp: Campaign, w: dict) -> None:
    w["updated"] = now_iso()
    write_json(path(camp), w)


def tracking(w: dict) -> bool:
    return isinstance(w.get("time", {}).get("t"), int)


def now(w: dict) -> int:
    if not tracking(w):
        raise RoboDMError("This campaign doesn't track in-game time yet. Start the clock with "
                          "`./dm time set <day> <HH:MM>` (e.g. `./dm time set 1 08:00`; add `--calendar harptos` for "
                          "Forgotten Realms dates).")
    return w["time"]["t"]


def _next_id(items, prefix: str) -> str:
    n = 1 + max((int(i["id"][len(prefix):]) for i in items if re.fullmatch(prefix + r"\d+", str(i.get("id", "")))),
                default=0)
    return f"{prefix}{n}"


# =============================================================================== time & calendar

def _harptos_year(year: int) -> list[str]:
    days = []
    for month, festival in _HARPTOS:
        days.extend(f"{month} {d}" for d in range(1, 31))
        if festival:
            days.append(festival)
            if festival == "Midsummer" and year % 4 == 0:
                days.append("Shieldmeet")
    return days


def harptos_date(start_year: int, start_index: int, day_number: int) -> str:
    """The Harptos date `day_number` days after Day 1 (= start_index of start_year)."""
    year, idx = start_year, start_index + day_number - 1
    while idx >= len(_harptos_year(year)):
        idx -= len(_harptos_year(year))
        year += 1
    while idx < 0:
        year -= 1
        idx += len(_harptos_year(year))
    return f"{_harptos_year(year)[idx]}, {year} DR"


def harptos_index(text: str, year: int) -> int:
    days = [d.lower() for d in _harptos_year(year)]
    key = " ".join(text.lower().replace(",", " ").split())
    if key in days:
        return days.index(key)
    m = re.fullmatch(r"(\d+)\s+(\w+)", key)               # "3 Tarsakh"
    if m and f"{m.group(2)} {m.group(1)}" in days:
        return days.index(f"{m.group(2)} {m.group(1)}")
    raise RoboDMError(f"Not a Harptos date: '{text}'. Use e.g. \"Tarsakh 3\" or a festival (Midwinter, Greengrass, "
                      "Midsummer, Shieldmeet, Highharvestide, Feast of the Moon).")


def time_word(t: int) -> str:
    m = t % DAY
    word = "night"
    for start, w in TIME_OF_DAY:
        if m >= start:
            word = w
    return word


def day_of(t: int) -> int:
    return t // DAY + 1


def fmt_clock_time(t: int) -> str:
    return f"{(t % DAY) // 60:02d}:{t % 60:02d}"


def fmt_time(w: dict, t: int | None = None) -> str:
    t = now(w) if t is None else t
    s = f"Day {day_of(t)} · {fmt_clock_time(t)} ({time_word(t)})"
    tm = w["time"]
    if tm.get("calendar") == "harptos":
        s += " · " + harptos_date(tm.get("year", 1492), tm.get("epoch", 0), day_of(t))
    return s


def fmt_point(t: int) -> str:
    return f"day {day_of(t)} {fmt_clock_time(t)}"


def fmt_dur(minutes: int) -> str:
    minutes = int(minutes)
    if minutes <= 0:
        return "0m"
    parts = []
    for unit, size in (("w", WEEK), ("d", DAY), ("h", 60), ("m", 1)):
        if minutes >= size:
            n, minutes = divmod(minutes, size)
            parts.append(f"{n}{unit}")
    return " ".join(parts[:2])


_UNIT = {"w": WEEK, "wk": WEEK, "week": WEEK, "weeks": WEEK, "d": DAY, "day": DAY, "days": DAY, "h": 60, "hr": 60,
         "hrs": 60, "hour": 60, "hours": 60, "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
         "round": 0.1, "rounds": 0.1, "r": 0.1}


def _hhmm(text: str) -> int:
    text = text.strip().lower()
    if text in NAMED_TIMES:
        return NAMED_TIMES[text]
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if not m:
        raise RoboDMError(f"Not a time of day: '{text}'. Use HH:MM (18:30) or dawn/morning/noon/dusk/evening/night/midnight.")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12:
        h += 12
    if m.group(3) == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        raise RoboDMError(f"Not a time of day: '{text}'.")
    return h * 60 + mi


def parse_point(text: str, t: int) -> int:
    """An absolute moment: "day 12 06:00", "day 12", "dawn" / "noon" (the next one), "tomorrow noon", "+3d"."""
    s = " ".join(text.lower().split())
    if s.startswith("+"):
        return t + parse_duration(s[1:], t)
    m = re.fullmatch(r"day\s+(\d+)(?:\s+(.+))?", s)
    if m:
        return (int(m.group(1)) - 1) * DAY + (_hhmm(m.group(2)) if m.group(2) else 0)
    m = re.fullmatch(r"tomorrow(?:\s+(.+))?", s)
    if m:
        return (t // DAY + 1) * DAY + (_hhmm(m.group(1)) if m.group(1) else 0)
    target = _hhmm(s)
    point = (t // DAY) * DAY + target
    return point if point > t else point + DAY


def parse_duration(text: str, t: int = 0) -> int:
    """Minutes from "3h", "1d 6h", "45m", "2 weeks", "10 rounds", or "until dawn" / "until day 12 06:00"."""
    s = " ".join(str(text).lower().split())
    if s.startswith("until "):
        point = parse_point(s[6:], t)
        if point <= t:
            raise RoboDMError(f"{fmt_point(point)} has already passed (now: {fmt_point(t)}).")
        return point - t
    total, pos = 0.0, 0
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([a-z]+)", s):
        if s[pos:m.start()].strip(" ,and"):
            break
        unit = _UNIT.get(m.group(2))
        if unit is None:
            raise RoboDMError(f"Unknown time unit '{m.group(2)}' in '{text}'. Use m, h, d, w (or minutes/hours/days/weeks/rounds).")
        total += float(m.group(1)) * unit
        pos = m.end()
    if pos == 0 or s[pos:].strip():
        raise RoboDMError(f"Not a duration: '{text}'. Examples: 3h · 1d 6h · 45m · 2w · until dawn · until day 12 06:00")
    minutes = math.ceil(total)
    if minutes <= 0:
        raise RoboDMError("The duration must be positive.")
    return minutes


def set_time(camp: Campaign, day: int, at: str = "08:00", calendar: str | None = None, date: str | None = None,
             year: int | None = None) -> str:
    w = load(camp)
    if day < 1:
        raise RoboDMError("Days count from 1.")
    tm = w["time"]
    tm["t"] = (day - 1) * DAY + _hhmm(at)
    if calendar:
        if calendar not in CALENDARS:
            raise RoboDMError(f"Calendar must be one of: {', '.join(CALENDARS)}")
        tm["calendar"] = calendar
    tm.setdefault("calendar", "simple")
    if year is not None:
        tm["year"] = year
    if tm["calendar"] == "harptos":
        tm.setdefault("year", 1492)
        if date:
            # Day `day` falls on `date`, so Day 1 is (day - 1) days earlier.
            idx = harptos_index(date, tm["year"]) - (day - 1)
            yr = tm["year"]
            while idx < 0:
                yr -= 1
                idx += len(_harptos_year(yr))
            tm["year"], tm["epoch"] = yr, idx
        tm.setdefault("epoch", 0)
    save(camp, w)
    return fmt_time(w)


# =============================================================================== advancing time

def advance(camp: Campaign, minutes: int, reason: str = "", offscreen: bool = True) -> list[str]:
    """Move the world forward: timers, clocks, deadlines, then the off-screen tick. Returns lines to print."""
    w = load(camp)
    old = now(w)
    new = old + int(minutes)
    w["time"]["t"] = new
    head = f"⏳ +{fmt_dur(minutes)}{f' ({reason})' if reason else ''} → {fmt_time(w)}"
    lines = [head]
    lines += _expire_timers(camp, w, new)
    lines += _tick_clocks(w, old, new)
    if offscreen:
        lines += tick(camp, w, old, new)
    save(camp, w)
    if camp.session.get("active"):
        camp.log(head)
    return lines


def advance_if_tracking(camp: Campaign, minutes: int, reason: str, offscreen: bool = True) -> list[str]:
    return advance(camp, minutes, reason, offscreen) if tracking(load(camp)) else []


# =============================================================================== timers (timed effects)

def timer_add(camp: Campaign, text: str, who: str | None = None, minutes: int | None = None, until: str | None = None,
              conc: bool = False, condition: str | None = None) -> tuple[dict, list[str]]:
    w = load(camp)
    t = now(w) if until != "long-rest" else w["time"].get("t")
    lines = []
    if conc and who:
        for old in [x for x in w["timers"] if x.get("conc") and x.get("who") == who]:
            w["timers"].remove(old)
            lines.append(f"Concentration on {old['text']} ends (a new concentration effect replaced it).")
    rec = {"id": _next_id(w["timers"], "t"), "text": text, "who": who, "conc": conc, "condition": condition,
           "started": t, "ends": "long-rest" if until == "long-rest" else t + int(minutes or 0)}
    w["timers"].append(rec)
    save(camp, w)
    return rec, lines


def fmt_timer(w: dict, x: dict) -> str:
    if x["ends"] == "long-rest" or not tracking(w):
        left = "until a long rest" if x["ends"] == "long-rest" else f"ends {fmt_point(x['ends'])}"
    else:
        left = f"{fmt_dur(x['ends'] - now(w))} left (ends {fmt_point(x['ends'])})"
    return f"[{x['id']}] {x['text']}{' (concentration)' if x.get('conc') else ''} — {left}"


def _remove_condition(camp: Campaign, who: str, condition: str) -> None:
    pc = camp.find_pc(who)
    if pc:
        pc["conditions"] = [c for c in pc.get("conditions", [])
                            if (c.get("name") if isinstance(c, dict) else c) != condition]
        camp.save_pc(pc)


def _expire_timers(camp: Campaign, w: dict, t: int) -> list[str]:
    lines = []
    for x in [x for x in w["timers"] if x["ends"] != "long-rest" and x["ends"] <= t]:
        w["timers"].remove(x)
        lines.append(f"⌛ Expired: {x['text']}")
        if x.get("condition") and x.get("who"):
            _remove_condition(camp, x["who"], x["condition"])
    return lines


def long_rest(camp: Campaign, who: list[str]) -> list[str]:
    """Record a long rest starting now for these PCs and clear their until-long-rest effects."""
    w = load(camp)
    lines = []
    if tracking(w):
        for slug in who:
            w["last_long_rest"][slug] = now(w)
    for x in [x for x in w["timers"] if x["ends"] == "long-rest" and (x.get("who") in who or not x.get("who"))]:
        w["timers"].remove(x)
        lines.append(f"⌛ Ends with the long rest: {x['text']}")
        if x.get("condition") and x.get("who"):
            _remove_condition(camp, x["who"], x["condition"])
    save(camp, w)
    return lines


def rest_problems(camp: Campaign, pcs: list[dict], kind: str) -> list[str]:
    if kind != "long":
        return []
    w = load(camp)
    problems = []
    for pc in pcs:
        if pc["hp"]["current"] <= 0:
            problems.append(f"{pc['name']} is at 0 HP: a character needs at least 1 hit point to benefit from a long "
                            "rest (PHB p.186)")
        last = w["last_long_rest"].get(pc["slug"])
        if tracking(w) and last is not None and now(w) - last < DAY:
            problems.append(f"{pc['name']} began a long rest {fmt_dur(now(w) - last)} ago: no more than one long rest "
                            f"in 24 hours (PHB p.186). Next possible at {fmt_point(last + DAY)}")
    return problems


def spell_minutes(sp: dict) -> int | None:
    """A spell's timed duration in minutes (5etools `duration`), or None."""
    d = (sp.get("duration") or [{}])[0]
    if d.get("type") != "timed":
        return None
    dur = d.get("duration") or {}
    per = {"round": 0.1, "minute": 1, "hour": 60, "day": DAY, "week": WEEK}.get(dur.get("type"))
    return math.ceil(per * dur.get("amount", 1)) if per else None


# =============================================================================== clocks

def clock_add(camp: Campaign, name: str, segments: int | None = None, per: str | None = None,
              deadline: str | None = None, public: bool = False, faction: str | None = None,
              on_fill: str = "") -> dict:
    w = load(camp)
    if not segments and not deadline:
        segments = 6
    rec = {"id": _next_id(w["clocks"], "k"), "name": name.strip(), "segments": segments or None, "filled": 0,
           "per": None, "next_tick": None, "deadline": None, "public": public, "faction": None,
           "on_fill": on_fill.strip(), "status": "running", "created": now_iso(), "history": []}
    if per or deadline:
        t = now(w)
        if per:
            rec["per"] = parse_duration(per, t)
            rec["next_tick"] = t + rec["per"]
        if deadline:
            rec["deadline"] = parse_point(deadline, t)
            if rec["deadline"] <= t:
                raise RoboDMError(f"The deadline {fmt_point(rec['deadline'])} has already passed (now: {fmt_point(t)}).")
    if faction:
        rec["faction"] = _faction_slug(camp, faction)
    w["clocks"].append(rec)
    save(camp, w)
    return rec


def find_clock(w: dict, key: str, open_only: bool = False) -> dict:
    key = key.strip()
    pool = [c for c in w["clocks"] if not open_only or c["status"] in ("running", "filled")]
    hit = next((c for c in pool if c["id"] == key), None)
    if hit:
        return hit
    matches = [c for c in pool if key.lower() in c["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise RoboDMError(f"'{key}' matches several clocks: {', '.join(c['id'] + ' ' + c['name'] for c in matches)}")
    raise RoboDMError(f"No clock '{key}'. Clocks: {', '.join(c['id'] + ' ' + c['name'] for c in w['clocks']) or 'none'}")


def _fill(c: dict, n: int, why: str, t: int | None) -> list[str]:
    if c["status"] != "running" or not c.get("segments"):
        return []
    before = c["filled"]
    c["filled"] = max(0, min(c["segments"], c["filled"] + n))
    c["history"].append({"n": n, "why": why, "t": t, "ts": now_iso()})
    lines = []
    if c["filled"] != before:
        lines.append(f"◔ {c['name']} {'+' if n > 0 else ''}{c['filled'] - before} → {c['filled']}/{c['segments']}"
                     + (f" ({why})" if why else ""))
    if c["filled"] >= c["segments"]:
        c["status"] = "filled"
        lines.append(f"⏰ **Clock filled: {c['name']}**" + (f" — {c['on_fill']}" if c.get("on_fill") else "")
                     + f". Make it happen, then `./dm clock done {c['id']}`.")
    return lines


def clock_tick(camp: Campaign, key: str, n: int = 1, why: str = "") -> list[str]:
    w = load(camp)
    c = find_clock(w, key, open_only=True)
    if not c.get("segments"):
        raise RoboDMError(f"{c['name']} is a deadline, not a segmented clock; it runs down with `./dm time advance`.")
    lines = _fill(c, n, why, w["time"].get("t"))
    save(camp, w)
    return lines or [f"{c['name']}: {c['filled']}/{c['segments']} (no change)"]


def clock_set_status(camp: Campaign, key: str, status: str) -> dict:
    w = load(camp)
    c = find_clock(w, key)
    c["status"] = status
    c["closed"] = now_iso()
    save(camp, w)
    return c


def _tick_clocks(w: dict, old: int, new: int) -> list[str]:
    lines = []
    for c in w["clocks"]:
        if c["status"] != "running":
            continue
        if c.get("per") and c.get("next_tick") is not None and c["next_tick"] <= new:
            n = 0
            while c["next_tick"] <= new:
                n += 1
                c["next_tick"] += c["per"]
            lines += _fill(c, n, f"{fmt_dur(new - old)} passed", new)
        if c.get("deadline") is not None and old < c["deadline"] <= new and c["status"] == "running":
            c["status"] = "filled"
            lines.append(f"⏰ **Deadline passed: {c['name']}** ({fmt_point(c['deadline'])})"
                         + (f" — {c['on_fill']}" if c.get("on_fill") else "")
                         + f". Make it happen, then `./dm clock done {c['id']}`.")
    return lines


def fmt_clock(w: dict, c: dict, dm: bool = True) -> str:
    tag = "" if not dm else (" · public" if c.get("public") else " · hidden from players")
    owner = f" · {c['faction']}" if c.get("faction") and dm else ""
    state = {"filled": " · ⏰ FILLED", "done": " · done", "stopped": " · stopped"}.get(c["status"], "")
    if c.get("segments"):
        bar = "■" * c["filled"] + "□" * (c["segments"] - c["filled"])
        rate = f" · +1 every {fmt_dur(c['per'])}" if c.get("per") else ""
        s = f"[{c['id']}] {c['name']} {bar} {c['filled']}/{c['segments']}{rate}"
    else:
        s = f"[{c['id']}] {c['name']}"
    if c.get("deadline") is not None:
        if tracking(w) and c["status"] == "running":
            s += f" · ⌛ {fmt_dur(c['deadline'] - now(w))} left (by {fmt_point(c['deadline'])})"
        else:
            s += f" · deadline {fmt_point(c['deadline'])}"
    if dm and c.get("on_fill"):
        s += f" → {c['on_fill']}"
    return s + state + owner + tag


# =============================================================================== factions

def _faction_slug(camp: Campaign, name: str) -> str:
    from robodm import lore

    e = lore.find(camp, name)
    if e["meta"].get("type") != "faction":
        raise RoboDMError(f"{e['meta'].get('name', e['slug'])} is a {e['meta'].get('type')} entry, not a faction. "
                          "Use `./dm faction rep \"Name\" … --new` to start a faction entry.")
    return e["slug"]


def _faction_state(w: dict, slug: str) -> dict:
    st = w["factions"].setdefault(slug, {})
    st.setdefault("rep", 0)
    st.setdefault("why", "")
    st.setdefault("pace", "normal")
    st.setdefault("agenda", [])
    return st


def ensure_faction(camp: Campaign, name: str, new: bool = False) -> str:
    from robodm import lore

    if new:
        try:
            return _faction_slug(camp, name)
        except RoboDMError:
            lore.add(camp, name, "faction", "", summary="")
    return _faction_slug(camp, name)


def faction_rep(camp: Campaign, name: str, change: str, why: str = "", new: bool = False) -> tuple[str, dict]:
    slug = ensure_faction(camp, name, new)
    w = load(camp)
    st = _faction_state(w, slug)
    ch = change.strip()
    try:
        score = st["rep"] + int(ch) if ch[:1] in "+-" else int(ch)
    except ValueError:
        raise RoboDMError("Reputation is a number from -5 to +5, or a change like +1 / -2.")
    st["rep"] = max(-5, min(5, score))
    if why:
        st["why"] = why
    st.setdefault("history", []).append({"change": ch, "why": why, "ts": now_iso()})
    save(camp, w)
    return slug, st


def faction_agenda(camp: Campaign, name: str, verb: str, text: str = "", clock: str | None = None) -> tuple[str, list[dict]]:
    slug = ensure_faction(camp, name)
    w = load(camp)
    st = _faction_state(w, slug)
    items = st["agenda"]
    if verb == "add":
        if not text.strip():
            raise RoboDMError("Usage: faction agenda <faction> add \"what they're working toward\" [--clock k2]")
        items.append({"id": _next_id(items, "f"), "text": text.strip(), "status": "open",
                      "clock": find_clock(w, clock)["id"] if clock else None, "ts": now_iso()})
    elif verb in ("done", "drop"):
        target = next((i for i in items if i["id"] == text.strip() or (text.strip() and text.strip().lower() in i["text"].lower())), None)
        if not target:
            raise RoboDMError(f"No agenda item '{text}'. Open: "
                              + ", ".join(f"{i['id']} {i['text']}" for i in items if i["status"] == "open"))
        target["status"] = "done" if verb == "done" else "dropped"
        target["closed"] = now_iso()
    elif verb != "list":
        raise RoboDMError("Usage: faction agenda <faction> add \"goal\" [--clock k2] | done <id> | drop <id> | list")
    save(camp, w)
    return slug, items


def faction_pace(camp: Campaign, name: str, pace: str) -> str:
    if pace not in PACE:
        raise RoboDMError(f"Pace must be one of: {', '.join(PACE)}")
    slug = ensure_faction(camp, name)
    w = load(camp)
    _faction_state(w, slug)["pace"] = pace
    save(camp, w)
    return slug


def factions(camp: Campaign) -> list[dict]:
    """Every faction lore entry with its standing and agenda (state may be empty)."""
    from robodm import lore

    w = load(camp)
    out = []
    for e in lore.all_entries(camp):
        if e["meta"].get("type") != "faction":
            continue
        st = {"rep": 0, "why": "", "pace": "normal", "agenda": [], **w["factions"].get(e["slug"], {})}
        out.append({"slug": e["slug"], "name": e["meta"].get("name", e["slug"]), "summary": e["meta"].get("summary", ""),
                    "status": e["meta"].get("status", ""), "rep": st["rep"], "word": REP_WORDS[st["rep"]],
                    "why": st["why"], "pace": st["pace"], "agenda": st["agenda"]})
    return sorted(out, key=lambda f: f["name"])


# =============================================================================== scene & witnesses

def scene_set(camp: Campaign, place: str | None = None, present: list[str] | None = None,
              add: list[str] | None = None, leave: list[str] | None = None) -> list[str]:
    """Set where the party is and who else is here. Companions follow: named → present, dropped → away."""
    from robodm import companions as co, lore

    w = load(camp)
    sc = w["scene"]
    names: dict[str, str] = dict(sc.get("names") or {})
    cast = list(sc.get("present") or [])
    if present is not None:
        cast = []
    notes = []

    def who(name: str) -> str:
        try:
            slug = co.find(camp, name)
            meta, _ = co.load_persona(camp, slug)
            names[slug] = meta.get("name", slug)
            return slug
        except RoboDMError:
            pass
        from robodm import npcs

        rec = npcs.find_or_none(camp, name)
        if rec:
            names[rec["slug"]] = rec["name"]
            return rec["slug"]
        try:
            e = lore.find(camp, name)
            names[e["slug"]] = e["meta"].get("name", e["slug"])
            return e["slug"]
        except RoboDMError:
            slug = slugify(name)
            names[slug] = name.strip()
            notes.append(f"{name.strip()} isn't in the lore yet (`./dm lore add \"{name.strip()}\" --type npc …` if they matter)")
            return slug

    for n in (present or []) + (add or []):
        if n.strip():
            s = who(n)
            if s not in cast:
                cast.append(s)
    for n in leave or []:
        s = slugify(n) if slugify(n) in cast else who(n)      # whoever is already in the scene by that name first
        cast = [x for x in cast if x != s]
    if place is not None and place.strip():
        if place.strip() != sc.get("place"):
            sc["since"] = w["time"].get("t")
        sc["place"] = place.strip()
    sc["present"] = cast
    sc["names"] = {k: v for k, v in names.items() if k in cast}
    save(camp, w)
    for slug in co.all_slugs(camp):
        status = co.state(camp, slug)["status"]
        if slug in cast and status == "away":
            co.set_status(camp, slug, "present")
        elif slug not in cast and status == "present":
            co.set_status(camp, slug, "away")
    return notes


def scene_cast(camp: Campaign) -> list[tuple[str, str]]:
    """(slug, name) of everyone non-PC who is here: the scene's cast plus companions travelling with the party."""
    from robodm import companions as co

    w = load(camp)
    sc = w["scene"]
    out = [(s, (sc.get("names") or {}).get(s, s)) for s in sc.get("present") or []]
    for slug in co.all_slugs(camp):
        if co.state(camp, slug)["status"] == "party" and slug not in dict(out):
            meta, _ = co.load_persona(camp, slug)
            out.append((slug, meta.get("name", slug)))
    return out


def witnesses(camp: Campaign) -> list[str]:
    return [s for s, _ in scene_cast(camp)]


# =============================================================================== NPC minds

def mind(meta: dict) -> dict:
    return {k: meta.get(f"mind_{k}") for k, _ in MIND_FIELDS if meta.get(f"mind_{k}")}


def fmt_mind(meta: dict) -> str:
    m = mind(meta)
    return "; ".join(f"{label} {m[k]}" for k, label in MIND_FIELDS if k in m)


def set_mind(camp: Campaign, name: str, **fields) -> dict:
    from robodm import lore

    e = lore.find(camp, name)
    lore.update(camp, e["slug"], fields={f"mind_{k}": v for k, v in fields.items() if v is not None})
    return lore.find(camp, e["slug"])


# =============================================================================== the off-screen tick

def tier_for(minutes: int) -> str:
    for name, limit in TIERS:
        if limit is None or minutes < limit:
            return name
    return "HEAVY"


def _cooling(w: dict, ref: str, t: int) -> bool:
    for b in w["offscreen"]:
        if b.get("ref") != ref:
            continue
        if b["status"] == "open":
            return True
        if b.get("resolved_t") is not None and t - b["resolved_t"] < BEAT_COOLDOWN:
            return True
    return False


def _progress_roll(label: str, mod: int = 0) -> tuple[int, str]:
    r = dice.roll("1d6", label=label)
    total = max(1, min(6, r.total + mod))
    if total >= 6:
        return total, "major step"
    if total >= 4:
        return total, "progress"
    return total, "no visible progress"


def tick(camp: Campaign, w: dict, old: int, new: int) -> list[str]:
    """What moved while the party was elsewhere. Code picks what's due and rolls; the DM narrates the result."""
    from robodm import companions as co, lore

    minutes = new - old
    tier = tier_for(minutes)
    if tier == "MICRO":
        return []
    here = set(witnesses(camp))
    beats: list[dict] = []
    quiet: list[str] = []
    rolls = 0 if tier == "MINOR" else (1 if tier == "MAJOR" else min(4, max(1, minutes // WEEK)))

    def due(ref: str, text: str, mod: int = 0, clock: dict | None = None) -> None:
        if _cooling(w, ref, new):
            return
        if not rolls:
            beats.append({"ref": ref, "text": text})
            return
        results = [_progress_roll(f"off-screen: {truncate(text, 40)}", mod) for _ in range(rolls)]
        best = max(results)
        moved = sum(1 for v, _ in results if v >= 4) + sum(1 for v, _ in results if v >= 6)
        rolled = ", ".join(str(v) for v, _ in results)
        if best[0] < 4:
            quiet.append(f"{truncate(text, 70)} (🎲 {rolled}: no visible progress)")
            return
        extra = ""
        if clock is not None and clock["status"] == "running" and clock.get("segments"):
            fill = _fill(clock, moved, f"off-screen ({tier})", new)
            extra = f" · clock {clock['id']} → {clock['filled']}/{clock['segments']}" + (" ⏰ FILLED" if clock["status"] == "filled" else "")
            beats.extend({"ref": f"clock:{clock['id']}", "text": ln, "tier": tier} for ln in fill if ln.startswith("⏰"))
        beats.append({"ref": ref, "text": f"{text} — 🎲 {rolled}: {best[1]}{extra}"
                                          + (" (surface a rumor of it)" if best[0] >= 6 else ""), "roll": best[0]})

    # Factions working on their agendas (over days, not hours).
    for f in factions(camp) if rolls else []:
        for item in f["agenda"]:
            if item["status"] != "open":
                continue
            clock = next((c for c in w["clocks"] if c["id"] == item.get("clock")), None)
            due(f"faction:{f['slug']}:{item['id']}", f"{f['name']}: {item['text']}", PACE.get(f["pace"], 0), clock)
    # NPCs with a plan who aren't here.
    for e in lore.all_entries(camp):
        meta = e["meta"]
        if meta.get("type") != "npc" or not meta.get("mind_plan") or e["slug"] in here or meta.get("status") == "dead":
            continue
        due(f"npc:{e['slug']}", f"{meta.get('name', e['slug'])} (away) is pursuing: {meta['mind_plan']}")
    # Companions who aren't with the party.
    for slug in co.all_slugs(camp):
        st = co.state(camp, slug)
        if st["status"] != "away":
            continue
        meta, _ = co.load_persona(camp, slug)
        for item in st["agenda"]:
            if item["status"] == "open":
                due(f"companion:{slug}:{item['id']}", f"{meta.get('name', slug)} (away): {item['text']}")
    if tier == "HEAVY":
        beats.append({"ref": f"shift:{new}", "text": f"The world shifts ({fmt_dur(minutes)} passed): write 1–2 rumors "
                                                     "or changes the party would notice on their return."})
    if not beats and not quiet:
        return []
    lines = [f"🌍 **Off-screen ({tier}, {fmt_dur(minutes)} passed)**"]
    for b in beats:
        b.update({"id": _next_id(w["offscreen"], "w"), "tier": tier, "t": new, "status": "open", "ts": now_iso()})
        w["offscreen"].append(b)
        lines.append(f"- [{b['id']}] {b['text']}")
    lines.extend(f"- {q}" for q in quiet)
    if beats:
        lines.append("Decide what each of these looks like in the world, then record it in a line: "
                     "`./dm world resolve <id> \"what happened\"` (`--known-by` who would know; `world drop <id>` if nothing "
                     "comes of it).")
    return lines


def open_beats(w: dict) -> list[dict]:
    return [b for b in w["offscreen"] if b["status"] == "open"]


def resolve(camp: Campaign, bid: str, text: str | None, known_by: list[str] | None = None, drop: bool = False) -> dict:
    from robodm import memory

    w = load(camp)
    b = next((x for x in w["offscreen"] if x["id"] == bid.strip()), None)
    if b is None:
        raise RoboDMError(f"No off-screen beat '{bid}'. Open: {', '.join(x['id'] for x in open_beats(w)) or 'none'}")
    if b["status"] != "open":
        raise RoboDMError(f"{bid} is already {b['status']}.")
    if not drop and not (text or "").strip():
        raise RoboDMError("Say what happened: world resolve <id> \"…\" (or `world drop <id>`).")
    b["status"] = "dropped" if drop else "resolved"
    b["resolved_t"] = w["time"].get("t")
    if not drop:
        tags = ["offscreen"] + ([b["ref"].split(":")[1]] if ":" in b.get("ref", "") else [])
        m = memory.add(camp, text.strip(), "world", tags, known_by=known_by)
        b["memory"] = m["id"]
        b["outcome"] = text.strip()
    save(camp, w)
    return b


# =============================================================================== knowledge propagation

def gossip(camp: Campaign, session: int | None = None) -> list[str]:
    """For this session's witnessed memories, who else would plausibly hear: lore neighbours of the witnesses."""
    from robodm import lore, memory

    n = camp.session.get("number", 0) if session is None else session
    entries = {e["slug"]: e for e in lore.all_entries(camp)}
    pcs = {pc["slug"] for pc in camp.party()}
    related: dict[str, set[str]] = {s: set() for s in entries}
    for s, e in entries.items():
        for r in e["meta"].get("related") or []:
            r = slugify(str(r))
            related.setdefault(s, set()).add(r)
            related.setdefault(r, set()).add(s)
    out = []
    for m in memory.list_memories(camp):
        if m.get("session") != n or m.get("type") not in WITNESSED_TYPES | {"rumor"} or not m.get("known_by"):
            continue
        knows = set(m["known_by"])
        reach: dict[str, str] = {}
        for wit in knows:
            for other in related.get(wit, ()):
                e = entries.get(other)
                if not e or other in knows or other in pcs or e["meta"].get("status") == "dead":
                    continue
                if e["meta"].get("type") in ("npc", "faction"):
                    reach.setdefault(other, entries.get(wit, {}).get("meta", {}).get("name", wit))
        if reach:
            who = ", ".join(f"{entries[o]['meta'].get('name', o)} (via {via})" for o, via in sorted(reach.items()))
            out.append(f"- {m['id']} “{truncate(m['text'], 120)}” — known by {', '.join(sorted(knows))}; could reach: {who}")
    if out:
        out.append("For each that would spread: `./dm memory add \"Rumor: …\" --type rumor --known-by a,b` "
                   "(rumors can be wrong or partial). Code only suggests; nothing spreads on its own.")
    return out


# =============================================================================== summaries

def brief_lines(camp: Campaign, dm: bool = True) -> tuple[list[str], list[str]]:
    """(scene lines, world lines) for the brief and the web client."""
    from robodm import lore, npcs

    w = load(camp)
    scene = []
    sc = w["scene"]
    if sc.get("place") or sc.get("present"):
        entries = {e["slug"]: e for e in lore.all_entries(camp)}
        records = {r["slug"]: r for r in npcs.all_records(camp)}
        if sc.get("place"):
            since = f" (since {fmt_point(sc['since'])})" if sc.get("since") is not None else ""
            scene.append(f"**Where:** {sc['place']}{since}")
        for slug, name in scene_cast(camp):
            e = entries.get(slug)
            rec = records.get(slug) or next((r for r in records.values() if r.get("lore") == slug), None)
            bits = [f"- **{name}**"]
            if e and e["meta"].get("summary"):
                bits.append(f"— {truncate(str(e['meta']['summary']), 100)}")
            elif rec and rec.get("role"):
                bits.append(f"— {truncate(str(rec['role']), 100)}")
            if dm and e and mind(e["meta"]):
                bits.append(f"· mind: {fmt_mind(e['meta'])}")
            if dm and npcs.is_companion(camp, slug):
                from robodm import companions as co

                bits.append(f"· speaks through `{co.agent_name(camp, slug)}`" + ("" if rec else " · ⚠ no NPC record yet"))
            elif dm:
                bits.append(f"· speaks through `{npcs.mind_agent(camp, rec)}` (slug {rec['slug']})" if rec else
                            "· ⚠ no NPC record yet")
            scene.append(" ".join(bits))
    world = []
    beats = open_beats(w)
    if dm and beats:                     # first: the brief is trimmed from the bottom
        world.append(f"- 🌍 {len(beats)} off-screen beat(s) to resolve: `./dm world`")
    shown = [c for c in w["clocks"] if c["status"] in ("running", "filled") and (dm or c.get("public"))]
    for c in sorted(shown, key=lambda c: c["status"] != "filled"):
        world.append(f"- {fmt_clock(w, c, dm)}")
    if tracking(w):
        for x in w["timers"]:
            world.append(f"- ⌛ {fmt_timer(w, x)}")
    for f in factions(camp):
        if f["rep"] or (dm and any(i["status"] == "open" for i in f["agenda"])):
            ag = "; ".join(i["text"] for i in f["agenda"] if i["status"] == "open") if dm else ""
            world.append(f"- {f['name']}: {f['word']} ({f['rep']:+d})" + (f" — working on: {truncate(ag, 160)}" if ag else ""))
    return scene, world
