"""Encounter difficulty (DMG 2014, chapter 3) and a simple random encounter builder."""

from __future__ import annotations

import re

from robodm import dice
from robodm.rules.index import RulesDB, cr_to_float
from robodm.rules.statblock import CR_XP
from robodm.util import RoboDMError

# character level -> (easy, medium, hard, deadly) XP threshold per character
THRESHOLDS = {
    1: (25, 50, 75, 100), 2: (50, 100, 150, 200), 3: (75, 150, 225, 400), 4: (125, 250, 375, 500),
    5: (250, 500, 750, 1100), 6: (300, 600, 900, 1400), 7: (350, 750, 1100, 1700), 8: (450, 900, 1400, 2100),
    9: (550, 1100, 1600, 2400), 10: (600, 1200, 1900, 2800), 11: (800, 1600, 2400, 3600),
    12: (1000, 2000, 3000, 4500), 13: (1100, 2200, 3400, 5100), 14: (1250, 2500, 3800, 5700),
    15: (1400, 2800, 4300, 6400), 16: (1600, 3200, 4800, 7200), 17: (2000, 3900, 5900, 8800),
    18: (2100, 4200, 6300, 9500), 19: (2400, 4900, 7300, 10900), 20: (2800, 5700, 8500, 12700),
}
DIFFICULTIES = ["easy", "medium", "hard", "deadly"]
MULTIPLIERS = [0.5, 1, 1.5, 2, 2.5, 3, 4, 5]


def parse_party(spec: str) -> list[int]:
    """'4x3' (four level-3 PCs), '3,3,4,5', or '2x5,1x4'."""
    levels: list[int] = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*[x*@]\s*(\d+)", part)
        if m:
            levels.extend([int(m.group(2))] * int(m.group(1)))
        elif part.isdigit():
            levels.append(int(part))
        else:
            raise RoboDMError(f"Can't read party spec '{spec}'. Use e.g. 4x3 or 3,3,4,5.")
    for lv in levels:
        if lv not in THRESHOLDS:
            raise RoboDMError("Character levels must be 1-20.")
    if not levels:
        raise RoboDMError("Party is empty.")
    return levels


def party_thresholds(levels: list[int]) -> dict[str, int]:
    return {d: sum(THRESHOLDS[lv][i] for lv in levels) for i, d in enumerate(DIFFICULTIES)}


def multiplier(n_monsters: int, party_size: int) -> float:
    if n_monsters <= 0:
        return 1
    if n_monsters == 1:
        idx = 1
    elif n_monsters == 2:
        idx = 2
    elif n_monsters <= 6:
        idx = 3
    elif n_monsters <= 10:
        idx = 4
    elif n_monsters <= 14:
        idx = 5
    else:
        idx = 6
    if party_size < 3:
        idx += 1
    elif party_size >= 6:
        idx -= 1
    return MULTIPLIERS[max(0, min(idx, len(MULTIPLIERS) - 1))]


def rate(levels: list[int], monster_xp: list[int]) -> dict:
    base = sum(monster_xp)
    mult = multiplier(len(monster_xp), len(levels))
    adjusted = int(base * mult)
    th = party_thresholds(levels)
    difficulty = "trivial"
    for d in DIFFICULTIES:
        if adjusted >= th[d]:
            difficulty = d
    return {
        "party": levels,
        "monsters": len(monster_xp),
        "base_xp": base,
        "multiplier": mult,
        "adjusted_xp": adjusted,
        "thresholds": th,
        "difficulty": difficulty,
        "xp_per_pc": base // len(levels),
    }


def parse_monster_list(spec: str) -> list[tuple[str, int]]:
    """'goblin x4, bugbear' -> [('goblin', 4), ('bugbear', 1)]"""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        suffix = re.fullmatch(r"(.+?)\s*[x*×]\s*(\d+)", part)
        prefix = re.fullmatch(r"(\d+)\s*[x*×]?\s+(.+)", part)
        if suffix:
            out.append((suffix.group(1).strip(), int(suffix.group(2))))
        elif prefix:
            out.append((prefix.group(2).strip(), int(prefix.group(1))))
        else:
            out.append((part, 1))
    return out


def rate_named(db: RulesDB | None, levels: list[int], spec: str) -> dict:
    xp_list, lines = [], []
    for name, count in parse_monster_list(spec):
        cr_match = re.fullmatch(r"cr\s*([\d/]+)", name.lower())
        if cr_match:
            cr = cr_match.group(1)
            label = f"CR {cr} creature"
        else:
            if db is None:
                raise RoboDMError("Monster names need local rules data; use 'cr 1/4 x4' style or run `./dm rules fetch`.")
            rec, _ = db.lookup(name, "monster")
            if not rec:
                raise RoboDMError(f"Monster '{name}' not found in local data.")
            e = db.get(rec)
            cr = e.get("cr")
            cr = cr.get("cr") if isinstance(cr, dict) else cr
            label = f"{rec['n']} (CR {cr})"
        xp = CR_XP.get(str(cr))
        if xp is None:
            raise RoboDMError(f"Unknown CR '{cr}'.")
        xp_list.extend([xp] * count)
        lines.append(f"{count}× {label} = {xp * count:,} XP")
    result = rate(levels, xp_list)
    result["lines"] = lines
    return result


def build_random(db: RulesDB, levels: list[int], difficulty: str = "medium", env: str | None = None,
                 mtype: str | None = None, sources: list[str] | None = None, tries: int = 200) -> dict:
    """Pick a monster type and a count that lands in the requested difficulty band."""
    if difficulty not in DIFFICULTIES:
        raise RoboDMError(f"Difficulty must be one of {', '.join(DIFFICULTIES)}.")
    th = party_thresholds(levels)
    lo = th[difficulty]
    hi = th[DIFFICULTIES[DIFFICULTIES.index(difficulty) + 1]] if difficulty != "deadly" else int(th["deadly"] * 1.5)
    srcs = {s.upper() for s in sources} if sources else None
    pool = []
    for r in db.index:
        if r["t"] != "monster" or "cr" not in r:
            continue
        if env and env.lower() not in [x.lower() for x in r.get("env", [])]:
            continue
        if mtype and mtype.lower() not in r.get("mt", "").lower():
            continue
        if srcs and r["s"].upper() not in srcs:
            continue
        crf = cr_to_float(r["cr"])
        if crf is None or crf > max(levels) + 3:
            continue
        pool.append(r)
    if not pool:
        raise RoboDMError("No monsters match those filters.")
    for _ in range(tries):
        r = pool[dice.RNG.randrange(len(pool))]
        xp = CR_XP.get(r["cr"])
        if not xp:
            continue
        for count in range(1, 13):
            result = rate(levels, [xp] * count)
            if lo <= result["adjusted_xp"] < hi:
                result["lines"] = [f"{count}× {r['n']} (CR {r['cr']}, {r['s']}) = {xp * count:,} XP"]
                result["pick"] = {"name": r["n"], "source": r["s"], "count": count, "cr": r["cr"]}
                return result
            if result["adjusted_xp"] >= hi:
                break
    raise RoboDMError("Couldn't build an encounter in that band; loosen the filters.")
