"""Combat on the battle map: the `./dm combat map|place|move|stand|dash|disengage|area|range` commands, and
the positional checks the rules gate runs for attacks, spells and initiative (reach, range, cover, line of
sight, movement). Every combat uses a battle map; `--ruling "why"` overrides a block on purpose.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from robodm import audit, battlemap as bm
from robodm.campaign import Campaign
from robodm.combat import NO_ACTIONS, Encounter
from robodm.util import RoboDMError, parse_csv_list, slugify, write_text

AREA_SHAPES = ("sphere", "cylinder", "cone", "line", "cube")


def diagonals(c: Campaign) -> str:
    return c.settings.get("grid_diagonals", "5")


def the_map(enc: Encounter, required: bool = True) -> dict | None:
    m = enc.state.get("map")
    if m is None and required:
        raise RoboDMError("No battle map yet. Draft one from the scene: `./dm combat map draw --text -` "
                          "(one character per 5-ft square), or `./dm combat map new 20x15`.")
    return m


def turn_problems(enc: Encounter, cb: dict, doing: str, reaction: bool = False, legendary: bool = False) -> list[str]:
    """Can this creature act right now? (alive, conscious, its turn or a legal reaction/legendary action)"""
    if cb["status"] == "dead":
        return [f"{cb['name']} is dead"]
    problems = []
    if cb["hp"] <= 0:
        problems.append(f"{cb['name']} is at 0 HP")
    inc = next((x["name"] for x in cb["conditions"] if x["name"] in NO_ACTIONS), None)
    if inc:
        problems.append(f"{cb['name']} is {inc} and can't take actions or reactions")
    if enc.state.get("round", 0) == 0:
        problems.append("initiative hasn't been rolled yet (`./dm combat init`)")
        return problems
    cur = enc.current()
    if reaction:
        if cb.get("reaction_used"):
            problems.append(f"{cb['name']} has already used its reaction since its last turn")
    elif legendary:
        if not cb.get("legendary_max"):
            problems.append(f"{cb['name']} has no legendary actions")
        elif cur and cur["id"] == cb["id"]:
            problems.append("legendary actions are taken at the end of another creature's turn, not on its own")
    elif cur and cur["id"] != cb["id"]:
        problems.append(f"it's {cur['name']}'s turn, not {cb['name']}'s — {doing} on someone else's turn needs "
                        "--reaction (e.g. an opportunity attack)" + (" or --legendary" if cb.get("legendary_max") else ""))
    return problems


def _unplaced(enc: Encounter) -> list[dict]:
    return [x for x in enc.combatants.values() if not x.get("pos") and x.get("status") != "dead"]


def init_problems(enc: Encounter) -> list[str]:
    """Every combat is fought on a battle map: before round 1 the map exists and everyone is on it."""
    if enc.state.get("round", 0) > 0:
        return []
    if not enc.state.get("map"):
        return ["there's no battle map yet: draft one from the scene (`./dm combat map draw --text -`) and place "
                "everyone (`./dm combat place …`) before rolling initiative"]
    left = _unplaced(enc)
    return [f"not on the map yet: {', '.join(x['name'] for x in left)} (`./dm combat place <who> <square>`, or "
            "`./dm combat place party A5` / `enemies H2` to place a group around a square)"] if left else []


def placed_problems(enc: Encounter, *creatures: dict) -> list[str]:
    if not enc.state.get("map"):
        if enc.state.get("round", 0) > 0:   # a fight that began before battle maps were required
            return ["this fight has no battle map: draft one from the scene (`./dm combat map draw --text -`) and "
                    "place everyone (`./dm combat place …`) so reach, range and cover can be checked"]
        return []
    return [f"{x['name']} isn't on the map (`./dm combat place {x['id']} <square>`)" for x in creatures if not x.get("pos")]


# =============================================================================== attacks & spells

def _parse_range(text: str | None) -> list[int] | None:
    if not text:
        return None
    parts = [int(p) for p in str(text).replace("ft", "").split("/") if p.strip().isdigit()]
    return [parts[0], parts[-1]] if parts else None


def attack_checks(c: Campaign, enc: Encounter, att: dict, tgt: dict, args) -> dict:
    """Reach/range, long-range and adjacent-enemy disadvantage, cover and line of sight for an attack."""
    res = {"problems": placed_problems(enc, att, tgt), "dis": [], "cover": 0, "ranged": bool(args.ranged),
           "within_5ft": None, "distance": None}
    m = enc.state.get("map")
    if not m or res["problems"]:
        return res
    diag = diagonals(c)
    d = bm.distance(bm.footprint(att), bm.footprint(tgt), diag)
    res["distance"], res["within_5ft"] = d, d <= 5
    atk = None
    if args.action_name:
        atk = next((x for x in att.get("attacks", []) if x["name"].lower().startswith(args.action_name.lower())), None)
    kind = "ranged" if args.ranged else (atk.get("kind", "melee") if atk else "melee")
    reach = int(args.reach or (atk.get("reach") if atk else 0) or 5)
    rng = _parse_range(args.range) or (atk.get("range") if atk else None)
    if kind == "both":
        kind = "melee" if d <= reach else "ranged"
    res["ranged"] = kind == "ranged"
    label = atk["name"] if atk else "the attack"
    if kind == "melee":
        if d > reach:
            res["problems"].append(f"{tgt['name']} is {d} ft away; {label} has a {reach}-ft reach (move first)")
    else:
        if rng:
            normal, long = rng
            if d > long:
                res["problems"].append(f"{tgt['name']} is {d} ft away, beyond {label}'s long range ({long} ft)")
            elif d > normal:
                res["dis"].append(f"long range ({d} ft > {normal})")
        for o in enc.combatants.values():
            if (o["id"] != att["id"] and o.get("pos") and o.get("status") != "dead" and o["hp"] > 0 and bm.hostile(att, o)
                    and not any(x["name"] in NO_ACTIONS for x in o["conditions"])
                    and bm.distance(bm.footprint(att), bm.footprint(o), diag) <= 5):
                res["dis"].append(f"ranged attack with {o['name']} within 5 ft")
                break
    cv = bm.cover(m, enc.combatants, tgt, attacker=att)
    if cv == 99:
        res["problems"].append(f"{tgt['name']} has total cover from {att['name']} (no clear line); move or pick another target")
    else:
        res["cover"] = cv
    return res


def spell_range(sp: dict) -> tuple[str, int | None]:
    """('feet', n) · ('touch', 5) · ('self', 0) · ('sight'|'unlimited'|'special', None) · area shapes ('cone', 15)…"""
    r = sp.get("range") or {}
    kind, dist = r.get("type", "point"), r.get("distance") or {}
    unit = dist.get("type", "")
    if kind == "point":
        if unit == "touch":
            return "touch", 5
        if unit == "self":
            return "self", 0
        if unit in ("feet", "miles"):
            return "feet", int(dist.get("amount", 0)) * (5280 if unit == "miles" else 1)
        return unit or "special", None
    if kind in ("radius", "sphere", "cone", "line", "cube", "hemisphere", "emanation", "cylinder"):
        return kind, int(dist.get("amount", 0)) if unit == "feet" else None
    return kind, None


def cast_target_problems(c: Campaign, enc: Encounter, caster: dict, sp: dict, name: str, targets: list[dict]) -> list[str]:
    m = enc.state.get("map")
    problems = placed_problems(enc, caster, *targets)
    if not m or problems:
        return problems
    kind, reach = spell_range(sp)
    for t in targets:
        d = bm.distance(bm.footprint(caster), bm.footprint(t), diagonals(c))
        if kind == "self" and t["id"] != caster["id"]:
            problems.append(f"{name} has a range of Self; it can't target {t['name']}")
        elif kind in ("feet", "touch") and reach is not None and d > reach:
            problems.append(f"{t['name']} is {d} ft away; {name}'s range is {'Touch' if kind == 'touch' else str(reach) + ' ft'}")
        if t["id"] != caster["id"] and bm.cover(m, enc.combatants, t, attacker=caster) == 99:
            problems.append(f"{t['name']} has total cover from {caster['name']}: no clear path for {name}")
    return problems


# =============================================================================== map command

def map_command(c: Campaign, enc: Encounter, args, out) -> bool:
    """`combat map <new|draw|set|show|render|save|load|list|background|clear>`; True if the state changed."""
    enc.require_active()
    sub = (args.args[0] if args.args else "show").lower()
    rest = args.args[1:]
    maps_dir = c.path("maps")
    if sub == "new":
        if not rest or "x" not in rest[0].lower():
            raise RoboDMError("Usage: combat map new <width>x<height> [\"name\"]   e.g. combat map new 20x15 \"Bridge\"")
        w, h = (int(v) for v in rest[0].lower().split("x", 1))
        enc.state["map"] = bm.new_map(w, h, " ".join(rest[1:]))
        out(f"🗺️ New {w}×{h} battle map ({w * 5}×{h * 5} ft), all open floor. Paint terrain with "
            "`./dm combat map set C3:E5 ~` or redraw it with `combat map draw`.")
        return True
    if sub == "draw":
        text = _read_text(args)
        m = bm.from_text(text, " ".join(rest) or (enc.state.get("map") or {}).get("name", "") or enc.state.get("name", ""))
        old = enc.state.get("map") or {}
        if old.get("background"):
            m["background"] = old["background"]
        if bm.fog_on(old):                               # a redrawn map starts unexplored, with the same fog settings
            m["fog"] = {"on": True, "light": old["fog"].get("light", "bright"), "revealed": []}
        enc.state["map"] = m
        refresh_fog(c, enc)
        bad = [x["name"] for x in enc.combatants.values() if x.get("pos") and bm.check_space(m, enc.combatants, x, tuple(x["pos"]))]
        out(f"🗺️ Battle map drawn: {m['w']}×{m['h']} squares ({m['w'] * 5}×{m['h'] * 5} ft).")
        if bad:
            out(f"⚠️ Now standing somewhere impossible: {', '.join(bad)} — re-place them.")
        out("```\n" + bm.render_text(m, enc.combatants, (enc.current() or {}).get("id")) + "\n```")
        return True
    m = the_map(enc)
    if sub == "set":
        if len(rest) < 2:
            raise RoboDMError("Usage: combat map set <squares> <terrain>   e.g. combat map set C3:E5 ~   (terrain: "
                              + " ".join(bm.TERRAIN) + ")")
        cells = bm.parse_cells(rest[0], m)
        bm.set_terrain(m, cells, rest[1])
        out(f"Set {len(cells)} square(s) to {bm.TERRAIN[rest[1]]['name']}.")
        return True
    if sub == "show":
        out("```\n" + bm.render_text(m, enc.combatants, (enc.current() or {}).get("id"), reveal=not args.players,
                                     area=(enc.state.get("last_area") or {}).get("cells"),
                                     fog=bm.fog_view(m, enc.combatants, vision_fn(c))) + "\n```")
        return False
    if sub == "fog":
        mode = (rest[0] if rest else "on").lower()
        if mode not in ("on", "off", "dark", "bright"):
            raise RoboDMError("Usage: combat map fog on|off [--dark]   ·   combat map fog dark|bright (the light level)")
        fog = m.setdefault("fog", {"on": False, "light": "bright", "revealed": []})
        if mode == "off":
            fog["on"] = False
            out("🌫 Fog of war off: players see the whole map.")
            return True
        fog["on"] = True
        if mode in ("dark", "bright") or args.dark:
            fog["light"] = "dark" if (mode == "dark" or args.dark) else "bright"
        n = bm.update_fog(m, enc.combatants, vision_fn(c))
        out(f"🌫 Fog of war on ({fog['light']}"
            + ("; in darkness the party sees as far as darkvision or a carried light: `combat vision <who> <ft>`" if fog["light"] == "dark" else "")
            + f"). The party can see {n} square(s) so far; the rest stays hidden until someone gets line of sight.")
        return True
    if sub in ("reveal", "hide"):
        if not bm.fog_on(m):
            raise RoboDMError("Fog of war is off (`combat map fog on`).")
        cells = [(x, y) for y in range(m["h"]) for x in range(m["w"])] if (rest and rest[0].lower() == "all") else \
            bm.parse_cells(rest[0], m) if rest else []
        if not cells:
            raise RoboDMError(f"Usage: combat map {sub} <squares|all>   e.g. combat map {sub} H2:L6")
        revealed = {tuple(p) for p in m["fog"].get("revealed", [])}
        revealed = revealed | set(cells) if sub == "reveal" else revealed - set(cells)
        m["fog"]["revealed"] = sorted([list(p) for p in revealed])
        out(f"🌫 {'Revealed' if sub == 'reveal' else 'Hid'} {len(cells)} square(s).")
        return True
    if sub == "render":
        out(f"🖼️ {render(c, enc, args)}")
        return False
    if sub == "save":
        name = slugify(" ".join(rest) or m.get("name") or "map")
        write_text(maps_dir / f"{name}.map", bm.to_text(m) + "\n")
        out(f"Saved the layout as campaigns/{c.slug}/maps/{name}.map (`combat map load {name}` next time).")
        return False
    if sub == "background":
        if args.clear or not rest:
            m.pop("background", None)
            out("Background image removed.")
            return True
        src = Path(rest[0]).expanduser()
        if not src.exists():
            raise RoboDMError(f"No image at {src}.")
        maps_dir.mkdir(parents=True, exist_ok=True)
        dest = maps_dir / f"{slugify(m.get('name') or 'battlemap')}-bg{src.suffix.lower()}"
        shutil.copyfile(src, dest)
        m["background"] = dest.relative_to(c.dir).as_posix()
        out(f"Background set ({dest.name}). It's stretched over the {m['w']}×{m['h']} grid, so use an image whose "
            "squares line up with the map's.")
        return True
    if sub == "clear":
        enc.state.pop("map", None)
        enc.state.pop("last_area", None)
        out("Battle map removed.")
        return True
    raise RoboDMError("Map commands: new, draw, set, show, render, save, load, list, background, clear, fog, reveal, hide.")


def map_load_or_list(c: Campaign, enc: Encounter, args, out) -> bool | None:
    """`combat map list|load` work without a current map; None means 'not one of these'."""
    sub = (args.args[0] if args.args else "").lower()
    maps_dir = c.path("maps")
    if sub == "list":
        found = sorted(maps_dir.glob("*.map")) if maps_dir.exists() else []
        out("Saved maps: " + (", ".join(p.stem for p in found) if found else "none (`combat map save <name>`)"))
        return False
    if sub == "load":
        enc.require_active()
        name = slugify(" ".join(args.args[1:]))
        p = maps_dir / f"{name}.map"
        if not p.exists():
            raise RoboDMError(f"No saved map '{name}'. Saved: " + ", ".join(x.stem for x in maps_dir.glob("*.map")))
        enc.state["map"] = bm.from_text(p.read_text(encoding="utf-8"), name)
        bg = next(iter(sorted(maps_dir.glob(f"{name}-bg.*"))), None)
        if bg:
            enc.state["map"]["background"] = bg.relative_to(c.dir).as_posix()
        out(f"🗺️ Loaded {name} ({enc.state['map']['w']}×{enc.state['map']['h']}).")
        return True
    return None


def _read_text(args) -> str:
    if getattr(args, "file", None):
        return Path(args.file).expanduser().read_text(encoding="utf-8")
    if getattr(args, "text", None) == "-":
        import sys

        return sys.stdin.read()
    if getattr(args, "text", None):
        return args.text.replace("\\n", "\n")
    raise RoboDMError("Give the map with --text - (and the rows on stdin), --text \"row1\\nrow2\" or --file path.")


def vision_fn(c: Campaign):
    """How far a party creature sees in darkness: `combat vision` override, else darkvision from its sheet."""
    def radius(cb: dict) -> int:
        if cb.get("vision") is not None:
            return int(cb["vision"])
        pc = c.find_pc(cb["pc"]) if cb.get("pc") else None
        for s in (pc or {}).get("senses", []) or cb.get("senses", []) or []:
            m = re.search(r"darkvision (\d+)", str(s), re.I)
            if m:
                return int(m.group(1))
        return 0
    return radius


def refresh_fog(c: Campaign, enc: Encounter) -> None:
    m = enc.state.get("map")
    if m and bm.fog_on(m):
        bm.update_fog(m, enc.combatants, vision_fn(c))


def path_preview(c: Campaign, enc: Encounter, who: str, to: str) -> dict:
    """What `combat move <who> <to>` would cost, without moving anyone (the web client's click-to-move)."""
    m = the_map(enc)
    cb = enc.find(who)
    if not cb.get("pos"):
        return {"ok": False, "who": cb["name"], "problems": [f"{cb['name']} isn't on the map"]}
    dest = bm.parse_cell(to, m)
    diag = diagonals(c)
    speed = enc.speed_now(cb)
    prone = enc.has(cb, "prone")
    budget = speed * (2 if cb.get("dashed") else 1) - int(cb.get("moved", 0))
    found = bm.find_path(m, enc.combatants, cb, dest, diag, prone)
    if found is None:
        why = bm.check_space(m, enc.combatants, cb, dest)
        return {"ok": False, "who": cb["name"], "id": cb["id"], "to": bm.fmt_cell(*dest),
                "problems": [f"can't reach {bm.fmt_cell(*dest)}" + (f": {why}" if why else " (no open path)")]}
    cost, path = found
    oas = [] if cb.get("disengaged") else bm.opportunity_attacks(m, enc.combatants, cb, path, diag)
    problems = turn_problems(enc, cb, "moving")
    if cost > budget:
        problems.append(f"{cost} ft is more than the {max(0, budget)} ft left this turn")
    return {"ok": not problems, "who": cb["name"], "id": cb["id"], "to": bm.fmt_cell(*dest), "cost": cost,
            "left": max(0, budget), "speed": speed, "path": [list(p) for p in path], "problems": problems,
            "opportunity": [h["name"] for h, _ in oas]}


def render(c: Campaign, enc: Encounter, args) -> str:
    m = the_map(enc)
    cur = (enc.current() or {}).get("id")
    title = f"{enc.state.get('name') or m.get('name') or 'Battle map'} — round {enc.state.get('round', 0)}"
    bg = c.dir / m["background"] if m.get("background") else None
    svg = bm.render_svg(m, enc.combatants, cur, reveal=not args.players, area=enc.state.get("last_area"),
                        path=enc.state.get("last_path"), background=bg, title=title,
                        fog=bm.fog_view(m, enc.combatants, vision_fn(c)))
    html_out = bool(args.html) or str(args.out or "").lower().endswith(".html")
    dest = Path(args.out).expanduser() if args.out else c.path(".cache", f"battlemap.{'html' if html_out else 'svg'}")
    write_text(dest, bm.render_html(svg, title) if html_out else svg)
    return str(dest.resolve())


# =============================================================================== placing & moving

def place(c: Campaign, enc: Encounter, args, out) -> None:
    """`combat place <who> <square> [<who> <square> …]`, or a group (`party`, `enemies`) around a square."""
    m = the_map(enc)
    items = list(args.args)
    if len(items) < 2 or len(items) % 2:
        raise RoboDMError("Usage: combat place <who> <square> [<who> <square> …]   (groups: party, enemies)")
    taken: set = set()
    problems, moves = [], []
    for who, cell in zip(items[::2], items[1::2]):
        anchor = bm.parse_cell(cell, m)
        group = enc.find_many([who]) if who.lower() in ("party", "enemies", "all-party", "all-enemies") else [enc.find(who)]
        for x in group:
            if len(group) > 1 or who.lower() in ("party", "enemies"):
                pos = bm.free_cells_near(m, {k: v for k, v in enc.combatants.items() if k != x["id"]}, x, anchor, taken)
            else:
                pos = anchor
                why = bm.check_space(m, enc.combatants, x, pos) or (
                    "another creature placed in this command" if set(bm.footprint(x, pos)) & taken else None)
                if why:
                    problems.append(f"{x['name']} can't stand at {cell.upper()}: {why}")
                    continue
            taken.update(bm.footprint(x, pos))
            moves.append((x, pos))
    audit.gate(c, problems, args)
    for x, pos in moves:
        before = bm.fmt_cell(*x["pos"]) if x.get("pos") else None
        x["pos"] = list(pos)
        enc.say(f"📍 {x['name']} {'→ ' + bm.fmt_cell(*pos) if not before else before + ' → ' + bm.fmt_cell(*pos)}"
                + (" (placed; no movement spent)" if enc.state.get("round", 0) > 0 else ""))
    refresh_fog(c, enc)


def move(c: Campaign, enc: Encounter, args, out) -> None:
    m = the_map(enc)
    if len(args.args) < 2:
        raise RoboDMError("Usage: combat move <who> <square> [--path C4,D5,E5] [--dash] [--disengage] [--mode fly]")
    cb = enc.find(args.args[0])
    dest = bm.parse_cell(args.args[1], m)
    diag = diagonals(c)
    problems = placed_problems(enc, cb) + turn_problems(enc, cb, "moving")
    mode = args.mode or "walk"
    speed = enc.speed_now(cb, mode)
    cost, path = None, []
    if cb.get("pos"):
        prone = enc.has(cb, "prone") and mode == "walk"
        if args.dash:
            cb["dashed"] = True
        budget = speed * (2 if cb.get("dashed") else 1) - int(cb.get("moved", 0))
        if args.path:
            path = [tuple(cb["pos"])] + [bm.parse_cell(p, m) for p in parse_csv_list(args.path)]
            if path[-1] != dest:
                path.append(dest)
            cost, why = bm.path_cost(m, enc.combatants, cb, path, diag, prone)
            if cost is None:
                problems.append(f"that path doesn't work: {why}")
            elif bm.check_space(m, enc.combatants, cb, dest):
                problems.append(f"{cb['name']} can't end at {args.args[1].upper()}: {bm.check_space(m, enc.combatants, cb, dest)}")
        else:
            found = bm.find_path(m, enc.combatants, cb, dest, diag, prone)
            if found is None:
                why = bm.check_space(m, enc.combatants, cb, dest)
                problems.append(f"{cb['name']} can't reach {args.args[1].upper()}" + (f": {why}" if why else " (no open path)"))
                cost, path = None, []
            else:
                cost, path = found
        if speed == 0:
            problems.append(f"{cb['name']}'s speed is 0 right now (grappled, restrained, incapacitated or exhausted)")
        elif cost is not None and cost > budget:
            problems.append(f"that's {cost} ft of movement and {cb['name']} has {max(0, budget)} ft left this turn "
                            f"(speed {speed}{', dashing' if cb.get('dashed') else ''}"
                            f"{', crawling while prone (stand up first: `combat stand`)' if prone else ''})")
    audit.gate(c, problems, args)
    if not path:
        raise RoboDMError("No path to move along.")
    if args.disengage:
        cb["disengaged"] = True
    start = bm.fmt_cell(*cb["pos"])
    oas = [] if cb.get("disengaged") else bm.opportunity_attacks(m, enc.combatants, cb, path, diag)
    hazards = [bm.fmt_cell(*p) for p in path[1:] if bm.terrain(m, *p) == "^"]
    cb["pos"] = list(dest)
    cb["moved"] = int(cb.get("moved", 0)) + int(cost or 0)
    enc.state["last_path"] = [list(p) for p in path]
    left = speed * (2 if cb.get("dashed") else 1) - cb["moved"]
    enc.say(f"🏃 {cb['name']} moves {start} → {bm.fmt_cell(*dest)} ({cost} ft; {max(0, left)} ft left"
            f"{', dashing' if cb.get('dashed') else ''}{', disengaged' if cb.get('disengaged') else ''})")
    for h, at in oas:
        atk = next((x["name"] for x in h.get("attacks", []) if x.get("kind", "melee") in ("melee", "both") and "to_hit" in x), None)
        enc.say(f"  ⚠️ Leaves {h['name']}'s reach at {bm.fmt_cell(*at)}: it may make an opportunity attack — "
                f"`./dm combat attack {h['id']} {cb['id']}{' --action ' + repr(atk.lower()) if atk else ''} --reaction`"
                " (if it hits and drops them, `combat place` them back at that square)", log=True)
    if hazards:
        enc.say(f"  ⚠️ Path crosses a hazard at {', '.join(hazards)}.")
    refresh_fog(c, enc)


def stand(c: Campaign, enc: Encounter, args) -> None:
    cb = enc.find(" ".join(args.args))
    problems = turn_problems(enc, cb, "standing up")
    if not enc.has(cb, "prone"):
        problems.append(f"{cb['name']} isn't prone")
    speed = enc.speed_now(cb)
    budget = speed * (2 if cb.get("dashed") else 1) - int(cb.get("moved", 0))
    if speed == 0 or budget < speed // 2:
        problems.append(f"standing up costs half its speed ({speed // 2} ft); {cb['name']} has {max(0, budget)} ft left")
    audit.gate(c, problems, args)
    cb["conditions"] = [x for x in cb["conditions"] if x["name"] != "prone"]
    cb["moved"] = int(cb.get("moved", 0)) + speed // 2
    enc.say(f"{cb['name']} stands up ({speed // 2} ft of movement).")


def flag(c: Campaign, enc: Encounter, args, which: str) -> None:
    cb = enc.find(" ".join(args.args))
    audit.gate(c, turn_problems(enc, cb, f"taking the {which} action"), args)
    cb["dashed" if which == "Dash" else "disengaged"] = True
    enc.say(f"{cb['name']} takes the {which} action" + (f" (movement this turn: {enc.speed_now(cb) * 2} ft)" if which == "Dash"
                                                         else " (no opportunity attacks this turn)") + ".")


# =============================================================================== areas & ranges

def area(c: Campaign, enc: Encounter, args, out) -> None:
    """`combat area <shape> <size> (--at SQUARE | --from WHO --toward SQUARE|WHO) [--by caster --spell name]`"""
    m = the_map(enc)
    shape, size = (args.args[0].lower() if args.args else ""), None
    if len(args.args) >= 2 and args.args[1].isdigit():
        size = int(args.args[1])
    problems = []
    caster = enc.find(args.by) if args.by else None
    sp = None
    if args.spell:
        from robodm.rules import default_db

        db = default_db()
        rec = db.lookup(args.spell, "spell")[0] if db.available else None
        sp = db.get(rec) if rec else None
        if sp and (shape not in AREA_SHAPES or size is None):
            kind, n = spell_range(sp)       # self-centred areas (Burning Hands: cone 15) come from the data
            if kind in AREA_SHAPES or kind in ("radius", "hemisphere", "emanation"):
                shape = {"radius": "sphere", "hemisphere": "sphere", "emanation": "sphere"}.get(kind, kind)
                size = n
    if shape not in AREA_SHAPES or not size:
        raise RoboDMError("Usage: combat area <sphere|cylinder|cone|line|cube> <feet> (--at E5 | --from mira "
                          "--toward E5) [--width 5] [--by mira --spell \"Fireball\"]")
    origin_from = enc.find(args.from_) if args.from_ else None
    if shape in ("cone", "line") and not origin_from:
        origin_from = caster
    exclude: set = set()
    if origin_from is not None:
        if not args.toward:
            raise RoboDMError(f"A {shape} from a creature needs --toward <square or creature>.")
        if not origin_from.get("pos"):
            raise RoboDMError(f"{origin_from['name']} isn't on the map.")
        try:
            tgt = bm.parse_cell(args.toward, m)
            toward = (tgt[0] + 0.5, tgt[1] + 0.5)
        except RoboDMError:
            other = enc.find(args.toward)
            toward = bm._center(bm.footprint(other))
        cells0 = bm.footprint(origin_from)
        origin = bm.edge_point(cells0, toward)
        direction = (toward[0] - origin[0], toward[1] - origin[1])
        exclude = set(cells0)
        if shape in ("sphere", "cylinder"):
            origin, direction, exclude = bm._center(cells0), None, set()
    else:
        if not args.at:
            raise RoboDMError("Give --at <square> (the point of origin) or --from <creature> --toward <square>.")
        x, y = bm.parse_cell(args.at, m)
        origin = (x, y) if (args.corner or shape == "cube") else (x + 0.5, y + 0.5)
        direction = None
        if caster and caster.get("pos") and sp:
            kind, reach = spell_range(sp)
            d = bm.distance(bm.footprint(caster), [(x, y)], diagonals(c))
            if kind == "feet" and reach is not None and d > reach:
                problems.append(f"{args.at.upper()} is {d} ft from {caster['name']}; {args.spell}'s range is {reach} ft")
            point = {"id": "_point", "pos": [x, y], "size": 1, "side": "neutral"}
            if bm.cover(m, enc.combatants, point, attacker=caster) == 99:
                problems.append(f"{caster['name']} has no clear line to {args.at.upper()} (total cover)")
    cells = bm.area_cells(m, shape, size, origin, direction, width_ft=args.width or 5, exclude=exclude)
    cellset = set(cells)
    hit, blocked, cover_bonus = [], [], {}
    for x in enc.combatants.values():
        if x.get("status") == "dead" or not x.get("pos") or not (set(bm.footprint(x)) & cellset):
            continue
        cv = bm.cover(m, enc.combatants, x, origin=origin)
        if cv == 99 and not args.through_corners:
            blocked.append(x)
            continue
        hit.append(x)
        if cv in (2, 5):
            cover_bonus[x["id"]] = cv
    audit.gate(c, problems, args)
    enc.state["last_area"] = {"shape": shape, "size": size, "origin": list(origin), "cells": [list(p) for p in cells],
                              "targets": [x["id"] for x in hit], "cover": cover_bonus,
                              "by": caster["id"] if caster else None, "spell": args.spell}
    where = f"at {args.at.upper()}" if args.at else f"from {origin_from['name']} toward {args.toward}"
    names = [x["name"] + (f" (+{cover_bonus[x['id']]} DEX, cover)" if x["id"] in cover_bonus else "") for x in hit]
    enc.say(f"💥 {size}-ft {shape} {where}: {len(cells)} squares — affects {', '.join(names) or 'no one'}"
            + (f"; total cover from the origin: {', '.join(x['name'] for x in blocked)}" if blocked else ""))
    if any(x["side"] in ("party", "ally") for x in hit) and any(x["side"] == "enemy" for x in hit):
        enc.say("  ⚠️ Allies are inside the area too.")
    if hit:
        save = None
        if sp and sp.get("savingThrow"):
            save = sp["savingThrow"][0][:3]
        enc.say(f"  Resolve: `./dm combat save {save or '<ability>'} area --dc <DC> --damage <dice> --type <type>"
                f"{' --spell ' + repr(args.spell) if args.spell else ''}{' --by ' + caster['id'] if caster else ''}`"
                " (DEX saves get the cover bonus automatically)", log=False)


def range_report(c: Campaign, enc: Encounter, args) -> list[str]:
    m = the_map(enc)
    if len(args.args) != 2:
        raise RoboDMError("Usage: combat range <creature> <creature>")
    a, b = enc.find(args.args[0]), enc.find(args.args[1])
    if not a.get("pos") or not b.get("pos"):
        raise RoboDMError("Both creatures need to be on the map.")
    d = bm.distance(bm.footprint(a), bm.footprint(b), diagonals(c))
    cv = bm.cover(m, enc.combatants, b, attacker=a)
    reach = bm.melee_reach(a)
    return [f"📏 {a['name']} ({bm.fmt_cell(*a['pos'])}) → {b['name']} ({bm.fmt_cell(*b['pos'])}): {d} ft · "
            f"{bm.COVER_NAMES[cv]} · {'within' if d <= reach else 'outside'} {a['name']}'s {reach}-ft reach"]
