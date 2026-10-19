"""Battle maps: a 5-ft grid with terrain, token positions, movement, reach and range, cover, line of sight and
areas of effect. Everything the rules gate needs to judge positioning, so the DM never eyeballs it.

Cells are named like a chessboard: column letters left to right (A…Z, AA…), rows numbered from the top (1…).
A map is drawn as text, one character per square:

    .  open floor                 #  wall (blocks movement and sight)
    ~  difficult terrain          +  closed door (as a wall)      /  open door
    w  water (difficult)          o  low obstacle: crates, rubble, low wall (difficult; half cover)
    T  tree or pillar (impassable; partial cover)                 ^  hazard (open; marked)

Rules used (PHB ch. 9 "Playing on a Grid", DMG ch. 8 "Cover" and "Areas of Effect", PHB p.191 movement):
  * distance counts squares; diagonals cost 5 ft (or alternate 5/10 with the campaign's `grid_diagonals`).
  * difficult terrain and other creatures' spaces cost double; hostile spaces can't be crossed unless two
    sizes apart; you can't end your move in an occupied space; crawling while prone costs double.
  * cover: from a corner of the attacker's space, trace lines to the four corners of one target square. Walls
    blocking 1–2 lines give half cover (+2), 3 lines three-quarters (+5), 4 total. Creatures, trees and low
    obstacles in the way give half cover at most. The attacker picks the corner and square that suit it best.
  * areas: a square is affected when its centre lies inside the area (≈ "covers at least half the square").
    A cone is as wide as it is long; a line is 5 ft wide unless stated.
"""

from __future__ import annotations

import base64
import heapq
import html
import math
import re
from pathlib import Path
from typing import Any

from robodm.util import RoboDMError

TERRAIN: dict[str, dict[str, Any]] = {
    ".": {"name": "open", "cost": 1, "block": None},
    "#": {"name": "wall", "cost": None, "block": "solid"},
    "+": {"name": "closed door", "cost": None, "block": "solid"},
    "/": {"name": "open door", "cost": 1, "block": None},
    "~": {"name": "difficult terrain", "cost": 2, "block": None},
    "w": {"name": "water", "cost": 2, "block": None},
    "o": {"name": "low obstacle", "cost": 2, "block": "partial"},
    "T": {"name": "tree/pillar", "cost": None, "block": "partial"},
    "^": {"name": "hazard", "cost": 1, "block": None},
}
SIZE_SQUARES = {"T": 1, "S": 1, "M": 1, "L": 2, "H": 3, "G": 4}
SIZE_ORDER = "TSMLHG"
COVER_NAMES = {0: "none", 2: "half cover (+2 AC/DEX)", 5: "three-quarters cover (+5 AC/DEX)", 99: "total cover"}
MAX_SIDE = 60


# =============================================================================== cells

def col_name(x: int) -> str:
    s = ""
    x += 1
    while x:
        x, r = divmod(x - 1, 26)
        s = chr(65 + r) + s
    return s


def fmt_cell(x: int, y: int) -> str:
    return f"{col_name(x)}{y + 1}"


def parse_cell(text: str, m: dict | None = None) -> tuple[int, int]:
    mt = re.fullmatch(r"\s*([A-Za-z]{1,2})\s*(\d{1,3})\s*", text or "")
    if not mt:
        raise RoboDMError(f"'{text}' isn't a square. Use a column letter and row number, e.g. C4.")
    letters, row = mt.group(1).upper(), int(mt.group(2))
    x = 0
    for ch in letters:
        x = x * 26 + (ord(ch) - 64)
    x, y = x - 1, row - 1
    if m is not None and not in_bounds(m, x, y):
        raise RoboDMError(f"{text.upper()} is off the map (it's {col_name(0)}1–{fmt_cell(m['w'] - 1, m['h'] - 1)}).")
    return x, y


def parse_cells(text: str, m: dict) -> list[tuple[int, int]]:
    """'C4', 'C3:E5' (rectangle) or a comma list of either."""
    out: list[tuple[int, int]] = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        if ":" in part:
            a, b = part.split(":", 1)
            (x0, y0), (x1, y1) = parse_cell(a, m), parse_cell(b, m)
            out.extend((x, y) for y in range(min(y0, y1), max(y0, y1) + 1) for x in range(min(x0, x1), max(x0, x1) + 1))
        else:
            out.append(parse_cell(part, m))
    return out


# =============================================================================== maps

def new_map(w: int, h: int, name: str = "") -> dict:
    if not (1 <= w <= MAX_SIDE and 1 <= h <= MAX_SIDE):
        raise RoboDMError(f"Maps are 1–{MAX_SIDE} squares a side.")
    return {"name": name, "w": w, "h": h, "rows": ["." * w for _ in range(h)]}


def from_text(text: str, name: str = "") -> dict:
    rows = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith(";"):
            continue
        cells = line.replace(" ", "") if " " in line.strip() and all(len(t) == 1 for t in line.split()) else line
        rows.append(cells)
    if not rows:
        raise RoboDMError("The map text is empty. Draw one character per square, e.g. `#..~~..#`.")
    bad = sorted({ch for r in rows for ch in r if ch not in TERRAIN})
    if bad:
        raise RoboDMError(f"Unknown map characters: {' '.join(bad)}. Use: " +
                          ", ".join(f"{k} {v['name']}" for k, v in TERRAIN.items()))
    w = max(len(r) for r in rows)
    m = new_map(w, len(rows), name)
    m["rows"] = [r.ljust(w, ".") for r in rows]
    return m


def to_text(m: dict) -> str:
    return "\n".join(m["rows"])


def in_bounds(m: dict, x: int, y: int) -> bool:
    return 0 <= x < m["w"] and 0 <= y < m["h"]


def terrain(m: dict, x: int, y: int) -> str:
    return m["rows"][y][x] if in_bounds(m, x, y) else "#"


def set_terrain(m: dict, cells: list[tuple[int, int]], ch: str) -> None:
    if ch not in TERRAIN:
        raise RoboDMError(f"Unknown terrain '{ch}'. Use: " + ", ".join(f"{k} {v['name']}" for k, v in TERRAIN.items()))
    rows = [list(r) for r in m["rows"]]
    for x, y in cells:
        rows[y][x] = ch
    m["rows"] = ["".join(r) for r in rows]


# =============================================================================== creatures on the map

def size_squares(size: Any) -> int:
    if isinstance(size, int):
        return max(1, size)
    return SIZE_SQUARES.get(str(size or "M")[:1].upper(), 1)


def footprint(c: dict, pos: tuple[int, int] | None = None) -> list[tuple[int, int]]:
    p = pos if pos is not None else c.get("pos")
    if not p:
        return []
    s = size_squares(c.get("size", 1))
    return [(p[0] + i, p[1] + j) for j in range(s) for i in range(s)]


def _box(cells: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    xs, ys = [c[0] for c in cells], [c[1] for c in cells]
    return min(xs), min(ys), max(xs), max(ys)


def grid_feet(dx: int, dy: int, diagonals: str = "5") -> int:
    lo, hi = sorted((abs(dx), abs(dy)))
    return 5 * (hi + lo // 2) if diagonals == "5-10-5" else 5 * hi


def distance(a: list[tuple[int, int]], b: list[tuple[int, int]], diagonals: str = "5") -> int:
    """Feet between two spaces (closest squares), counting the target's square but not the starting one."""
    ax0, ay0, ax1, ay1 = _box(a)
    bx0, by0, bx1, by1 = _box(b)
    dx = max(bx0 - ax1, ax0 - bx1, 0)
    dy = max(by0 - ay1, ay0 - by1, 0)
    return grid_feet(dx, dy, diagonals)


def occupants(combatants: dict, exclude: set[str] = frozenset()) -> dict[tuple[int, int], dict]:
    out = {}
    for c in combatants.values():
        if c["id"] in exclude or c.get("status") == "dead" or not c.get("pos"):
            continue
        for cell in footprint(c):
            out[cell] = c
    return out


def hostile(a: dict, b: dict) -> bool:
    party = {"party", "ally"}
    return (a["side"] in party) != (b["side"] in party) and "neutral" not in (a["side"], b["side"])


def check_space(m: dict, combatants: dict, c: dict, pos: tuple[int, int]) -> str | None:
    """Why `c` can't stand at pos, or None."""
    occ = occupants(combatants, exclude={c["id"]})
    for x, y in footprint(c, pos):
        if not in_bounds(m, x, y):
            return f"{fmt_cell(*pos)} puts {c['name']} partly off the map"
        if TERRAIN[terrain(m, x, y)]["cost"] is None:
            return f"{fmt_cell(x, y)} is {TERRAIN[terrain(m, x, y)]['name']}"
        if (x, y) in occ:
            return f"{fmt_cell(x, y)} is occupied by {occ[(x, y)]['name']}"
    return None


def free_cells_near(m: dict, combatants: dict, c: dict, anchor: tuple[int, int], taken: set | None = None) -> tuple[int, int]:
    """The nearest square to anchor where c fits (for placing a group around a point)."""
    taken = taken or set()
    best = None
    for y in range(m["h"]):
        for x in range(m["w"]):
            if check_space(m, combatants, c, (x, y)) or any(cell in taken for cell in footprint(c, (x, y))):
                continue
            d = (max(abs(x - anchor[0]), abs(y - anchor[1])), abs(x - anchor[0]) + abs(y - anchor[1]))
            if best is None or d < best[0]:
                best = (d, (x, y))
    if not best:
        raise RoboDMError(f"No room on the map for {c['name']}.")
    return best[1]


# =============================================================================== movement

_STEPS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def find_path(m: dict, combatants: dict, c: dict, dest: tuple[int, int], diagonals: str = "5",
              prone: bool = False) -> tuple[int, list[tuple[int, int]]] | None:
    """Cheapest legal path for c to dest: (feet, [positions incl. start]) or None. Dijkstra over squares,
    with the diagonal parity in the state for the 5/10/5 rule."""
    start = tuple(c["pos"])
    size = size_squares(c.get("size", 1))
    occ = occupants(combatants, exclude={c["id"]})

    def passable(pos) -> tuple[bool, bool]:
        """(can enter, is difficult)"""
        difficult = False
        for x, y in footprint(c, pos):
            if not in_bounds(m, x, y):
                return False, False
            t = TERRAIN[terrain(m, x, y)]
            if t["cost"] is None:
                return False, False
            if t["cost"] > 1:
                difficult = True
            o = occ.get((x, y))
            if o is not None:
                if hostile(c, o) and abs(size - size_squares(o.get("size", 1))) < 2:
                    return False, False
                difficult = True
        return True, difficult

    if check_space(m, combatants, c, dest):
        return None
    frontier = [(0, 0, start)]
    best = {(start, 0): 0}
    came: dict = {}
    while frontier:
        cost, parity, pos = heapq.heappop(frontier)
        if pos == dest:
            path = [pos]
            key = (pos, parity)
            while key in came:
                key = came[key]
                path.append(key[0])
            return cost, list(reversed(path))
        if cost > best.get((pos, parity), 10 ** 9):
            continue
        for dx, dy in _STEPS:
            nxt = (pos[0] + dx, pos[1] + dy)
            ok, difficult = passable(nxt)
            if not ok:
                continue
            diag = dx != 0 and dy != 0
            if diag and not (passable((pos[0] + dx, pos[1]))[0] and passable((pos[0], pos[1] + dy))[0]):
                continue  # no squeezing diagonally past a wall corner or a hostile creature
            step = 10 if (diag and diagonals == "5-10-5" and parity) else 5
            new_parity = (parity ^ 1) if (diag and diagonals == "5-10-5") else parity
            step *= 2 if difficult else 1
            step += 5 if prone else 0
            ncost = cost + step
            key = (nxt, new_parity)
            if ncost < best.get(key, 10 ** 9):
                best[key] = ncost
                came[key] = (pos, parity)
                heapq.heappush(frontier, (ncost, new_parity, nxt))
    return None


def path_cost(m: dict, combatants: dict, c: dict, path: list[tuple[int, int]], diagonals: str = "5",
              prone: bool = False) -> tuple[int | None, str]:
    """Cost of an explicit path (each step to a neighbouring square), or (None, why it's illegal)."""
    occ = occupants(combatants, exclude={c["id"]})
    size = size_squares(c.get("size", 1))
    total, parity = 0, 0
    for a, b in zip(path, path[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if max(abs(dx), abs(dy)) != 1:
            return None, f"{fmt_cell(*a)} → {fmt_cell(*b)} isn't a single step"
        difficult = False
        for x, y in footprint(c, b):
            if not in_bounds(m, x, y) or TERRAIN[terrain(m, x, y)]["cost"] is None:
                return None, f"{fmt_cell(x, y)} is {TERRAIN[terrain(m, x, y)]['name'] if in_bounds(m, x, y) else 'off the map'}"
            difficult |= TERRAIN[terrain(m, x, y)]["cost"] > 1
            o = occ.get((x, y))
            if o is not None:
                if hostile(c, o) and abs(size - size_squares(o.get("size", 1))) < 2:
                    return None, f"{fmt_cell(x, y)} is {o['name']}'s space (hostile)"
                difficult = True
        diag = dx != 0 and dy != 0
        step = 10 if (diag and diagonals == "5-10-5" and parity) else 5
        if diag and diagonals == "5-10-5":
            parity ^= 1
        total += step * (2 if difficult else 1) + (5 if prone else 0)
    return total, ""


def opportunity_attacks(m: dict, combatants: dict, c: dict, path: list[tuple[int, int]],
                        diagonals: str = "5") -> list[tuple[dict, tuple[int, int]]]:
    """Hostile creatures whose reach the mover leaves along the path: (creature, square where it happens)."""
    from robodm.combat import NO_ACTIONS

    out = []
    for h in combatants.values():
        if h["id"] == c["id"] or not h.get("pos") or h.get("status") == "dead" or not hostile(c, h):
            continue
        if h.get("reaction_used") or h["hp"] <= 0 or any(x["name"] in NO_ACTIONS for x in h["conditions"]):
            continue
        reach = melee_reach(h)
        for a, b in zip(path, path[1:]):
            if distance(footprint(c, a), footprint(h), diagonals) <= reach < distance(footprint(c, b), footprint(h), diagonals):
                out.append((h, a))
                break
    return out


def melee_reach(c: dict) -> int:
    reaches = [a.get("reach", 5) for a in c.get("attacks", []) if a.get("kind", "melee") in ("melee", "both")]
    return max(reaches or [5])


# =============================================================================== cover & line of sight

def _seg_crosses_cell(p, q, cx: int, cy: int, eps: float = 1e-6) -> bool:
    """Does segment p→q pass through the interior of the cell (cx, cy)? (Liang–Barsky clipping)"""
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x0 - (cx + eps)), (dx, (cx + 1 - eps) - x0), (-dy, y0 - (cy + eps)), (dy, (cy + 1 - eps) - y0)):
        if abs(pp) < 1e-12:
            if qq < 0:
                return False
            continue
        r = qq / pp
        if pp < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return False
    return t1 - t0 > 1e-9


def _line_block(m: dict, p, q, skip: set, creature_cells: set) -> str | None:
    """'solid', 'partial' or None for the worst obstacle the line crosses."""
    x_lo, x_hi = int(math.floor(min(p[0], q[0]))), int(math.ceil(max(p[0], q[0])))
    y_lo, y_hi = int(math.floor(min(p[1], q[1]))), int(math.ceil(max(p[1], q[1])))
    def block_at(x: int, y: int) -> str | None:
        if (x, y) in skip or not in_bounds(m, x, y):
            return None
        return TERRAIN[terrain(m, x, y)]["block"] or ("partial" if (x, y) in creature_cells else None)

    def both(a: str | None, b: str | None) -> str | None:
        return None if not (a and b) else "solid" if a == b == "solid" else "partial"

    worst = None
    for y in range(y_lo, y_hi + 1):
        for x in range(x_lo, x_hi + 1):
            block = block_at(x, y)
            if block and _seg_crosses_cell(p, q, x, y):
                if block == "solid":
                    return "solid"
                worst = "partial"
    # A line running exactly along the seam between two blocking squares, or through the corner where two
    # blocking squares meet diagonally, touches neither interior but can't see through: it's blocked too.
    (x0, y0), (x1, y1) = p, q
    seams = []
    if abs(y0 - y1) < 1e-9 and abs(y0 - round(y0)) < 1e-9:
        y = int(round(y0))
        seams += [both(block_at(cx, y - 1), block_at(cx, y)) for cx in range(int(math.floor(min(x0, x1))), int(math.ceil(max(x0, x1))))]
    elif abs(x0 - x1) < 1e-9 and abs(x0 - round(x0)) < 1e-9:
        x = int(round(x0))
        seams += [both(block_at(x - 1, cy), block_at(x, cy)) for cy in range(int(math.floor(min(y0, y1))), int(math.ceil(max(y0, y1))))]
    else:
        for vx in range(int(math.ceil(min(x0, x1))), int(math.floor(max(x0, x1))) + 1):
            t = (vx - x0) / (x1 - x0) if x1 != x0 else None
            if t is None or not 1e-9 < t < 1 - 1e-9:
                continue
            vy = y0 + t * (y1 - y0)
            if abs(vy - round(vy)) < 1e-9:
                vy = int(round(vy))
                seams += [both(block_at(vx - 1, vy - 1), block_at(vx, vy)), both(block_at(vx, vy - 1), block_at(vx - 1, vy))]
    if "solid" in seams:
        return "solid"
    return worst or ("partial" if "partial" in seams else None)


def _corners(cells: list[tuple[int, int]]) -> list[tuple[float, float]]:
    pts = set()
    for x, y in cells:
        pts.update({(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)})
    return sorted(pts)


def cover(m: dict, combatants: dict, target: dict, attacker: dict | None = None,
          origin: tuple[float, float] | None = None) -> int:
    """0, 2 (half), 5 (three-quarters) or 99 (total) for target against an attacker or an area's origin point."""
    tcells = footprint(target)
    exclude = {target["id"]} | ({attacker["id"]} if attacker else set())
    creature_cells = set(occupants(combatants, exclude=exclude))
    skip = set(tcells) | (set(footprint(attacker)) if attacker else set())
    starts = _corners(footprint(attacker)) if attacker else [origin]
    best = 99
    for p in starts:
        for sq in tcells:
            solid = partial = 0
            for corner in _corners([sq]):
                b = _line_block(m, p, corner, skip, creature_cells)
                solid += b == "solid"
                partial += b == "partial"
            level = 99 if solid == 4 else 5 if solid == 3 else 2 if (solid or partial) else 0
            best = min(best, level)
            if best == 0:
                return 0
    return best


# =============================================================================== fog of war

def fog_on(m: dict | None) -> bool:
    return bool(m and (m.get("fog") or {}).get("on"))


def _sight_blocked(m: dict, a: tuple[float, float], b: tuple[float, float], skip: set) -> bool:
    """Is the straight line a→b cut by a wall or closed door? Sampled densely; creatures and low cover don't block
    sight. A line squeezing between two diagonal walls counts as blocked."""
    dist = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(2, int(dist * 6))
    prev = None
    for i in range(1, n):
        t = i / n
        x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        cell = (int(math.floor(x)), int(math.floor(y)))
        if cell in skip or not in_bounds(m, *cell):
            prev = cell
            continue
        if TERRAIN[terrain(m, *cell)]["block"] == "solid":
            return True
        if prev and prev != cell and prev[0] != cell[0] and prev[1] != cell[1]:   # crossed a corner diagonally
            c1, c2 = (prev[0], cell[1]), (cell[0], prev[1])
            if all(in_bounds(m, *c) and TERRAIN[terrain(m, *c)]["block"] == "solid" for c in (c1, c2)):
                return True
        prev = cell
    return False


def visible_cells(m: dict, combatants: dict, radius_ft=None) -> set[tuple[int, int]]:
    """Squares the party can see right now: line of sight from any party-side creature, limited in darkness to
    `radius_ft(creature)` (darkvision or a carried light; 0 = only its own square)."""
    fog = m.get("fog") or {}
    dark = fog.get("light") == "dark"
    seen: set[tuple[int, int]] = set()
    for c in combatants.values():
        if c.get("side") not in ("party", "ally") or not c.get("pos") or c.get("status") == "dead":
            continue
        own = footprint(c)
        seen.update(own)
        if c.get("status") == "down" or any(x["name"] == "unconscious" for x in c.get("conditions", [])):
            continue
        reach = (radius_ft(c) if radius_ft else c.get("vision", 0)) if dark else None
        if reach is not None and reach <= 0:
            continue
        cells = reach // 5 if reach is not None else max(m["w"], m["h"])
        x0, y0, x1, y1 = _box(own)
        eyes = [(x + .5, y + .5) for x, y in own]
        for y in range(max(0, y0 - cells), min(m["h"], y1 + cells + 1)):
            for x in range(max(0, x0 - cells), min(m["w"], x1 + cells + 1)):
                if (x, y) in seen:
                    continue
                if reach is not None and distance(own, [(x, y)]) > reach:
                    continue
                targets = [(x + .5, y + .5), (x + .15, y + .15), (x + .85, y + .15), (x + .15, y + .85), (x + .85, y + .85)]
                if TERRAIN[terrain(m, x, y)]["block"] == "solid":
                    # a wall is seen when its face is: aim at the edge nearest the viewer
                    ex, ey = eyes[0]
                    targets = [(min(max(ex, x + .02), x + .98), min(max(ey, y + .02), y + .98))] + targets
                if any(not _sight_blocked(m, e, t, set(own) | {(x, y)}) for e in eyes for t in targets):
                    seen.add((x, y))
    return seen


def update_fog(m: dict, combatants: dict, radius_ft=None) -> int:
    """Add what the party sees now to what it has seen. Returns how many squares were newly revealed."""
    if not fog_on(m):
        return 0
    revealed = {tuple(p) for p in m["fog"].get("revealed", [])}
    now = visible_cells(m, combatants, radius_ft)
    new = now - revealed
    m["fog"]["revealed"] = sorted([list(p) for p in revealed | now])
    return len(new)


def fog_view(m: dict, combatants: dict, radius_ft=None) -> dict | None:
    if not fog_on(m):
        return None
    return {"visible": visible_cells(m, combatants, radius_ft),
            "revealed": {tuple(p) for p in m["fog"].get("revealed", [])}}


def seen_by_party(c: dict, fog: dict | None) -> bool:
    """Is this creature on the player map? Party-side creatures always are; others only where the party sees."""
    if fog is None or c.get("side") in ("party", "ally"):
        return True
    return bool(c.get("pos")) and any(p in fog["visible"] for p in footprint(c))


# =============================================================================== areas of effect

def _center(cells: list[tuple[int, int]]) -> tuple[float, float]:
    x0, y0, x1, y1 = _box(cells)
    return (x0 + x1 + 1) / 2, (y0 + y1 + 1) / 2


def area_cells(m: dict, shape: str, size_ft: int, origin: tuple[float, float],
               direction: tuple[float, float] | None = None, width_ft: int = 5,
               exclude: set | None = None) -> list[tuple[int, int]]:
    """Squares inside an area. Coordinates are in squares (1 = 5 ft); origin is a point, direction a vector."""
    shape = shape.lower()
    r = size_ft / 5
    exclude = exclude or set()
    if direction is not None:
        n = math.hypot(*direction) or 1.0
        direction = (direction[0] / n, direction[1] / n)
    out = []
    for y in range(m["h"]):
        for x in range(m["w"]):
            if (x, y) in exclude:
                continue
            vx, vy = x + 0.5 - origin[0], y + 0.5 - origin[1]
            if shape in ("sphere", "cylinder", "circle", "radius", "emanation"):
                inside = math.hypot(vx, vy) <= r + 1e-9
            elif shape == "cone":
                proj = vx * direction[0] + vy * direction[1]
                perp = abs(-vx * direction[1] + vy * direction[0])
                inside = 0 < proj <= r + 1e-9 and perp <= proj / 2 + 1e-9
            elif shape == "line":
                proj = vx * direction[0] + vy * direction[1]
                perp = abs(-vx * direction[1] + vy * direction[0])
                inside = 0 < proj <= r + 1e-9 and perp <= width_ft / 10 + 1e-9
            elif shape in ("cube", "square"):
                if direction is None:  # origin = the top-left corner of the cube
                    inside = 0 < vx < r and 0 < vy < r
                else:                  # the cube's near face touches the origin, centred on the direction
                    ax = (1, 0) if abs(direction[0]) >= abs(direction[1]) else (0, 1)
                    sign = 1 if (direction[0] if ax == (1, 0) else direction[1]) >= 0 else -1
                    along = (vx if ax == (1, 0) else vy) * sign
                    across = vy if ax == (1, 0) else vx
                    inside = 0 < along <= r + 1e-9 and abs(across) <= r / 2 + 1e-9
            else:
                raise RoboDMError(f"Unknown area shape '{shape}'. Use sphere, cylinder, cone, line or cube.")
            if inside:
                out.append((x, y))
    return out


def edge_point(cells: list[tuple[int, int]], toward: tuple[float, float]) -> tuple[float, float]:
    """Where a ray from the centre of a space toward a point leaves the space (a cone's or line's origin)."""
    cx, cy = _center(cells)
    dx, dy = toward[0] - cx, toward[1] - cy
    half = (_box(cells)[2] - _box(cells)[0] + 1) / 2
    if dx == 0 and dy == 0:
        return cx, cy
    t = half / max(abs(dx), abs(dy))
    return cx + dx * t, cy + dy * t


# =============================================================================== rendering

def token_codes(combatants: dict) -> dict[str, str]:
    """Two-character codes for the text map: G1 for Goblin 1, Mi for Mira, BB for Bog Brute…"""
    codes: dict[str, str] = {}
    used: set[str] = set()
    for c in combatants.values():
        words = re.findall(r"[A-Za-z]+|\d+", c["name"])
        letters = [w for w in words if w.isalpha()]
        num = next((w for w in reversed(words) if w.isdigit()), "")
        tries = []
        if letters and num:
            tries.append(letters[0][0].upper() + num[-1])
        if len(letters) >= 2:
            tries.append(letters[0][0].upper() + letters[1][0].upper())
        if letters:
            w = letters[0]
            tries += [w[0].upper() + ch.lower() for ch in w[1:]]
        tries += [chr(65 + i // 10) + str(i % 10) for i in range(260)]
        code = next(t for t in tries if len(t) == 2 and t not in used and t not in ("##", "~~", "oo", "TT", "ww"))
        codes[c["id"]] = code
        used.add(code)
    return codes


_TEXT_GLYPH = {".": " .", "#": "##", "+": "[]", "/": "/ ", "~": "~~", "w": "ww", "o": "oo", "T": "TT", "^": "!!"}


def render_text(m: dict, combatants: dict, current: str | None = None, reveal: bool = True,
                area: list | None = None, fog: dict | None = None, player: bool | None = None) -> str:
    """`fog` (from fog_view) is applied to the player view: unexplored squares blank, unseen creatures left out."""
    from robodm.combat import health_word

    codes = token_codes(combatants)
    player_fog = fog is not None and (player if player is not None else not reveal)
    if player_fog:
        combatants = {k: v for k, v in combatants.items() if seen_by_party(v, fog)}
    occ = occupants(combatants)
    area = set(map(tuple, area or []))
    header = "    " + "".join(f"{col_name(x):<2}" for x in range(m["w"]))
    lines = [header.rstrip()]
    for y in range(m["h"]):
        row = []
        for x in range(m["w"]):
            c = occ.get((x, y))
            if player_fog and (x, y) not in fog["revealed"] and (x, y) not in fog["visible"]:
                row.append("░░")
            elif c is not None:
                row.append(codes[c["id"]])
            elif (x, y) in area:
                row.append("**")
            else:
                row.append(_TEXT_GLYPH[terrain(m, x, y)])
        lines.append(f"{y + 1:>3} " + "".join(row).rstrip())
    legend = []
    for c in combatants.values():
        if c.get("status") == "dead":
            continue
        hp = f"{c['hp']}/{c['max_hp']}" if (reveal or c["kind"] == "pc") else health_word(c)
        where = fmt_cell(*c["pos"]) if c.get("pos") else "not placed"
        conds = ", ".join(x["name"] for x in c["conditions"])
        legend.append(f"{'▶' if c['id'] == current else ' '}{codes[c['id']]} {c['name']} · {where} · {hp}"
                      + (f" · {conds}" if conds else ""))
    used = sorted({ch for r in m["rows"] for ch in r if ch != "."})
    key = "  ".join(f"{_TEXT_GLYPH[ch].strip()} {TERRAIN[ch]['name']}" for ch in used)
    return "\n".join(lines + [""] + legend + ([f"Key: {key}" + ("  ** area" if area else "")] if key or area else []))


_SIDE_COLOR = {"party": "#2563eb", "ally": "#16a34a", "enemy": "#dc2626", "neutral": "#6b7280"}
_HEALTH_COLOR = {"unhurt": "#16a34a", "scratched": "#65a30d", "wounded": "#ca8a04", "bloodied": "#ea580c",
                 "near death": "#b91c1c", "down": "#111827", "dead": "#111827"}


SVG_CELL, SVG_MARGIN, SVG_LEGEND = 44, 26, 300


def svg_geometry(m: dict, combatants: dict, cell: int = SVG_CELL) -> dict:
    """Where the grid sits in render_svg's output (the web client turns clicks into squares with it)."""
    W, H = m["w"] * cell, m["h"] * cell
    return {"cell": cell, "margin": SVG_MARGIN, "w": m["w"], "h": m["h"],
            "total_w": SVG_MARGIN + W + 16 + SVG_LEGEND, "total_h": max(SVG_MARGIN + H + 16, 120 + 34 * len(combatants))}


def render_svg(m: dict, combatants: dict, current: str | None = None, reveal: bool = True,
               area: dict | None = None, path: list | None = None, background: Path | None = None,
               title: str = "", cell: int = SVG_CELL, fog: dict | None = None, player: bool | None = None) -> str:
    """A standalone SVG battle map: terrain, grid, labels, tokens with HP rings, area and path overlays, legend.
    With `fog` (from fog_view), the player view hides unexplored squares and unseen creatures; the DM view shades
    what the players can't see."""
    from robodm.combat import health_word

    player_fog = fog is not None and (player if player is not None else not reveal)   # `player`: the players' map
    if player_fog:
        combatants = {k: v for k, v in combatants.items() if seen_by_party(v, fog)}

    margin, legend_w = 26, 300
    W, H = m["w"] * cell, m["h"] * cell
    total_w, total_h = margin + W + 16 + legend_w, max(margin + H + 16, 120 + 34 * len(combatants))
    e = html.escape
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w} {total_h}" width="{total_w}" '
           f'height="{total_h}" font-family="Georgia, \'Times New Roman\', serif">',
           '<defs>'
           '<pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           '<line x1="0" y1="0" x2="0" y2="8" stroke="#8b6f47" stroke-width="2" opacity="0.55"/></pattern>'
           '<pattern id="waves" width="16" height="8" patternUnits="userSpaceOnUse">'
           '<path d="M0 4 Q4 0 8 4 T16 4" fill="none" stroke="#e0f2fe" stroke-width="1.4" opacity="0.8"/></pattern>'
           '<pattern id="stone" width="22" height="22" patternUnits="userSpaceOnUse">'
           '<rect width="22" height="22" fill="#44403c"/><path d="M0 11 H22 M11 0 V11 M5 11 V22 M17 11 V22" '
           'stroke="#292524" stroke-width="1.5"/></pattern>'
           '<filter id="glow"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/>'
           '<feMergeNode in="SourceGraphic"/></feMerge></filter>'
           '</defs>',
           f'<rect width="{total_w}" height="{total_h}" fill="#1c1917"/>',
           f'<g transform="translate({margin},{margin})">',
           f'<rect width="{W}" height="{H}" fill="#e7dcc3"/>']
    if background is not None and background.exists():
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".gif": "image/gif"}.get(background.suffix.lower(), "image/png")
        data = base64.b64encode(background.read_bytes()).decode("ascii")
        out.append(f'<image href="data:{mime};base64,{data}" width="{W}" height="{H}" preserveAspectRatio="none"/>')
    over = 0.45 if background is not None else 1.0
    for y in range(m["h"]):
        for x in range(m["w"]):
            ch = terrain(m, x, y)
            px, py = x * cell, y * cell
            if ch in "#+":
                out.append(f'<rect x="{px}" y="{py}" width="{cell}" height="{cell}" fill="url(#stone)" opacity="{over}"/>')
                if ch == "+":
                    out.append(f'<rect x="{px + cell * .2}" y="{py + cell * .1}" width="{cell * .6}" height="{cell * .8}" '
                               f'rx="3" fill="#92400e" stroke="#451a03" stroke-width="2"/>')
            elif ch == "/":
                out.append(f'<rect x="{px + cell * .1}" y="{py + cell * .42}" width="{cell * .8}" height="{cell * .16}" '
                           f'fill="#92400e" opacity="0.8"/>')
            elif ch == "~":
                out.append(f'<rect x="{px}" y="{py}" width="{cell}" height="{cell}" fill="url(#hatch)"/>')
            elif ch == "w":
                out.append(f'<rect x="{px}" y="{py}" width="{cell}" height="{cell}" fill="#3b82a0" opacity="{0.75 * over + .1}"/>'
                           f'<rect x="{px}" y="{py}" width="{cell}" height="{cell}" fill="url(#waves)"/>')
            elif ch == "o":
                out.append(f'<rect x="{px + cell * .18}" y="{py + cell * .22}" width="{cell * .64}" height="{cell * .56}" '
                           f'rx="4" fill="#a16207" stroke="#713f12" stroke-width="2" opacity="{max(over, .7)}"/>')
            elif ch == "T":
                out.append(f'<circle cx="{px + cell / 2}" cy="{py + cell / 2}" r="{cell * .42}" fill="#166534" '
                           f'stroke="#14532d" stroke-width="2" opacity="{max(over, .8)}"/>'
                           f'<circle cx="{px + cell * .42}" cy="{py + cell * .4}" r="{cell * .16}" fill="#22c55e" opacity="0.5"/>')
            elif ch == "^":
                out.append(f'<rect x="{px}" y="{py}" width="{cell}" height="{cell}" fill="#f97316" opacity="0.28"/>'
                           f'<text x="{px + cell / 2}" y="{py + cell * .66}" text-anchor="middle" font-size="{cell * .5}" '
                           f'fill="#9a3412">!</text>')
    for x in range(m["w"] + 1):
        out.append(f'<line x1="{x * cell}" y1="0" x2="{x * cell}" y2="{H}" stroke="#57534e" stroke-width="0.6" opacity="0.55"/>')
    for y in range(m["h"] + 1):
        out.append(f'<line x1="0" y1="{y * cell}" x2="{W}" y2="{y * cell}" stroke="#57534e" stroke-width="0.6" opacity="0.55"/>')
    if fog is not None:
        for y in range(m["h"]):
            for x in range(m["w"]):
                if (x, y) in fog["visible"]:
                    continue
                known = (x, y) in fog["revealed"]
                if player_fog:
                    out.append(f'<rect x="{x * cell}" y="{y * cell}" width="{cell}" height="{cell}" fill="#0c0a09" '
                               f'opacity="{0.55 if known else 1}"/>')
                else:
                    out.append(f'<rect x="{x * cell}" y="{y * cell}" width="{cell}" height="{cell}" fill="#1e1b4b" '
                               f'opacity="{0.18 if known else 0.38}"/>')
    if area:
        for x, y in area.get("cells", []):
            out.append(f'<rect x="{x * cell}" y="{y * cell}" width="{cell}" height="{cell}" fill="#f59e0b" opacity="0.38"/>')
        ox, oy = area.get("origin", (None, None))
        if ox is not None:
            out.append(f'<circle cx="{ox * cell}" cy="{oy * cell}" r="5" fill="#b45309" stroke="#fff" stroke-width="1.5"/>')
    if path and len(path) > 1:
        pts = " ".join(f"{(px + .5) * cell},{(py + .5) * cell}" for px, py in path)
        out.append(f'<polyline points="{pts}" fill="none" stroke="#0f172a" stroke-width="3" stroke-dasharray="7 5" '
                   f'stroke-linecap="round" opacity="0.75"/>')
    codes = token_codes(combatants)
    for c in combatants.values():
        if not c.get("pos"):
            continue
        s = size_squares(c.get("size", 1))
        cx, cy = (c["pos"][0] + s / 2) * cell, (c["pos"][1] + s / 2) * cell
        r = cell * s * 0.4
        dead = c.get("status") == "dead"
        fill = "#78716c" if dead else _SIDE_COLOR.get(c["side"], "#6b7280")
        if c["id"] == current:
            out.append(f'<circle cx="{cx}" cy="{cy}" r="{r + 5}" fill="none" stroke="#facc15" stroke-width="4" filter="url(#glow)"/>')
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" stroke="#0c0a09" stroke-width="2"/>')
        if not dead:
            word = health_word(c)
            if reveal or c["kind"] == "pc":
                frac = max(0.0, min(1.0, c["hp"] / max(1, c["max_hp"])))
                circ = 2 * math.pi * (r - 3)
                color = _HEALTH_COLOR.get(word, "#16a34a")
                out.append(f'<circle cx="{cx}" cy="{cy}" r="{r - 3}" fill="none" stroke="{color}" stroke-width="4" '
                           f'stroke-dasharray="{circ * frac:.1f} {circ:.1f}" transform="rotate(-90 {cx} {cy})"/>')
            else:
                out.append(f'<circle cx="{cx}" cy="{cy}" r="{r - 3}" fill="none" stroke="{_HEALTH_COLOR.get(word)}" stroke-width="3"/>')
        out.append(f'<text x="{cx}" y="{cy + cell * .13 * min(s, 2)}" text-anchor="middle" font-size="{cell * .36 * min(s, 2)}" '
                   f'font-weight="bold" fill="#fff">{e(codes[c["id"]])}</text>')
        if dead:
            out.append(f'<path d="M{cx - r * .6} {cy - r * .6} L{cx + r * .6} {cy + r * .6} M{cx + r * .6} {cy - r * .6} '
                       f'L{cx - r * .6} {cy + r * .6}" stroke="#fecaca" stroke-width="3"/>')
        if c["conditions"] and not dead:
            out.append(f'<circle cx="{cx + r * .75}" cy="{cy - r * .75}" r="{cell * .16}" fill="#7c3aed" stroke="#fff" stroke-width="1.2"/>'
                       f'<text x="{cx + r * .75}" y="{cy - r * .75 + cell * .06}" text-anchor="middle" font-size="{cell * .18}" '
                       f'fill="#fff">{len(c["conditions"])}</text>')
    for x in range(m["w"]):
        out.append(f'<text x="{x * cell + cell / 2}" y="-8" text-anchor="middle" font-size="12" fill="#d6d3d1">{col_name(x)}</text>')
    for y in range(m["h"]):
        out.append(f'<text x="-8" y="{y * cell + cell / 2 + 4}" text-anchor="end" font-size="12" fill="#d6d3d1">{y + 1}</text>')
    out.append("</g>")
    lx = margin + W + 16
    out.append(f'<text x="{lx}" y="{margin + 6}" font-size="17" font-weight="bold" fill="#fafaf9">{e(title or m.get("name") or "Battle map")}</text>')
    yy = margin + 34
    for c in combatants.values():
        dead = c.get("status") == "dead"
        hp = ("dead" if dead else f"HP {c['hp']}/{c['max_hp']}" if (reveal or c["kind"] == "pc") else health_word(c))
        where = fmt_cell(*c["pos"]) if c.get("pos") else "not placed"
        conds = ", ".join(x["name"] for x in c["conditions"])
        fill = "#78716c" if dead else _SIDE_COLOR.get(c["side"], "#6b7280")
        out.append(f'<circle cx="{lx + 11}" cy="{yy - 4}" r="11" fill="{fill}"/>'
                   f'<text x="{lx + 11}" y="{yy}" text-anchor="middle" font-size="10" font-weight="bold" fill="#fff">{e(codes[c["id"]])}</text>'
                   f'<text x="{lx + 30}" y="{yy - 3}" font-size="13" fill="{"#facc15" if c["id"] == current else "#fafaf9"}">'
                   f'{"▶ " if c["id"] == current else ""}{e(c["name"])}</text>'
                   f'<text x="{lx + 30}" y="{yy + 11}" font-size="10.5" fill="#a8a29e">{e(where)} · {e(hp)}'
                   f'{" · " + e(conds) if conds else ""}</text>')
        yy += 34
    out.append("</svg>")
    return "\n".join(out)


def render_html(svg: str, title: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<title>{html.escape(title)}</title><style>html,body{{margin:0;background:#1c1917}}"
            f"svg{{display:block;max-width:100%;height:auto;margin:auto}}</style></head><body>{svg}</body></html>")
