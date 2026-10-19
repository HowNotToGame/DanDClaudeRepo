"""Combat tracker: initiative, HP, damage types, conditions, saves, attacks, death saves.

State lives in campaigns/<slug>/combat.json so an encounter survives a context
reset or a new session. Player characters stay in sync with their sheets in
party/<pc>.json (the sheet is the source of truth for PC hit points).
"""

from __future__ import annotations

import re
from typing import Any

from robodm import dice, receipts
from robodm.campaign import Campaign, pc_init_bonus, pc_save_bonus
from robodm.util import ABILITIES, RoboDMError, ability_mod, fmt_mod, normalize_ability, now_iso, read_json, slugify, write_json

CONDITIONS = ["blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled", "incapacitated", "invisible",
              "paralyzed", "petrified", "poisoned", "prone", "restrained", "stunned", "unconscious"]
AUTO_FAIL_STR_DEX = {"paralyzed", "petrified", "stunned", "unconscious"}
ATTACKER_DISADV = {"blinded": "attacker blinded", "poisoned": "attacker poisoned", "prone": "attacker prone",
                   "restrained": "attacker restrained", "frightened": "attacker frightened (if source in sight)"}
TARGET_GIVES_ADV = {"blinded": "target blinded", "paralyzed": "target paralyzed", "petrified": "target petrified",
                    "restrained": "target restrained", "stunned": "target stunned", "unconscious": "target unconscious"}
NO_ACTIONS = {"incapacitated", "paralyzed", "petrified", "stunned", "unconscious"}


def health_word(c: dict) -> str:
    if c.get("status") == "dead":
        return "dead"
    hp, mx = c.get("hp", 0), max(1, c.get("max_hp", 1))
    if hp <= 0:
        return "down"
    pct = hp / mx
    if pct >= 1:
        return "unhurt"
    if pct > 0.75:
        return "scratched"
    if pct > 0.5:
        return "wounded"
    if pct > 0.25:
        return "bloodied"
    return "near death"


class Encounter:
    def __init__(self, camp: Campaign):
        self.camp = camp
        self.path = camp.path("combat.json")
        self.state: dict[str, Any] = read_json(self.path) or self._empty()
        self.messages: list[str] = []
        self._sync_from_sheets()

    @staticmethod
    def _empty() -> dict:
        return {"active": False, "name": "", "round": 0, "turn": 0, "order": [], "combatants": {}, "log": [],
                "xp_defeated": 0, "defeated": []}

    # ------------------------------------------------------------------ persistence
    @property
    def active(self) -> bool:
        return bool(self.state.get("active"))

    @property
    def combatants(self) -> dict[str, dict]:
        return self.state["combatants"]

    def require_active(self) -> None:
        if not self.active:
            raise RoboDMError("No active combat. Start one with `./dm combat start`.")

    def save(self) -> None:
        self._sync_to_sheets()
        write_json(self.path, self.state)

    def _sync_from_sheets(self) -> None:
        for c in self.combatants.values():
            if c.get("pc"):
                pc = self.camp.find_pc(c["pc"])
                if pc:
                    c["hp"] = pc["hp"]["current"]
                    c["max_hp"] = pc["hp"]["max"]
                    c["temp_hp"] = pc["hp"].get("temp", 0)
                    c["ac"] = pc.get("ac", c.get("ac"))
                    c["death_saves"] = dict(pc.get("death_saves", {"success": 0, "fail": 0}))

    def _sync_to_sheets(self) -> None:
        for c in self.combatants.values():
            if c.get("pc"):
                pc = self.camp.find_pc(c["pc"])
                if not pc:
                    continue
                pc["hp"]["current"] = c["hp"]
                pc["hp"]["temp"] = c.get("temp_hp", 0)
                pc["death_saves"] = c.get("death_saves", {"success": 0, "fail": 0})
                keep = [x for x in pc.get("conditions", []) if isinstance(x, dict) and x.get("persistent")]
                pc["conditions"] = keep + [dict(x) for x in c.get("conditions", [])]
                self.camp.save_pc(pc)

    def say(self, msg: str, log: bool = True) -> None:
        self.messages.append(msg)
        if log:
            rnd = self.state.get("round", 0)
            self.state["log"].append(f"R{rnd}: {re.sub(r'[*]', '', msg)}")
            self.state["log"] = self.state["log"][-300:]

    # ------------------------------------------------------------------ setup
    def start(self, name: str = "") -> None:
        if self.active:
            raise RoboDMError(f"Combat '{self.state.get('name') or 'unnamed'}' is already running. End it first (`./dm combat end`).")
        self.state = self._empty()
        self.state.update({"active": True, "name": name, "round": 0, "started": now_iso()})
        self.say(f"⚔️ Combat started{': ' + name if name else ''}. Add combatants, then roll initiative.")

    def _unique_id(self, base: str) -> str:
        base = slugify(base)
        if base not in self.combatants:
            return base
        n = 2
        while f"{base}-{n}" in self.combatants:
            n += 1
        return f"{base}-{n}"

    def _new(self, cid: str, name: str, **kw) -> dict:
        c = {
            "id": cid, "name": name, "kind": kw.get("kind", "npc"), "side": kw.get("side", "enemy"),
            "init": kw.get("init"), "init_bonus": kw.get("init_bonus", 0), "dex": kw.get("dex", 10),
            "ac": kw.get("ac", 10), "hp": kw.get("hp", 1), "max_hp": kw.get("max_hp", kw.get("hp", 1)),
            "temp_hp": 0, "saves": kw.get("saves") or {ab: 0 for ab in ABILITIES},
            "resist": kw.get("resist", []), "immune": kw.get("immune", []), "vulnerable": kw.get("vulnerable", []),
            "cond_defenses": kw.get("cond_defenses", []), "condition_immune": kw.get("condition_immune", []),
            "conditions": [], "concentration": None, "status": "ok",
            "death_saves": {"success": 0, "fail": 0}, "attacks": kw.get("attacks", []),
            "legendary_max": kw.get("legendary_actions", 0), "legendary_left": kw.get("legendary_actions", 0),
            "recharge_spent": [], "reaction_used": False, "xp": kw.get("xp", 0), "notes": kw.get("notes", ""),
            "pos": None, "size": kw.get("size", "M"), "speed": int(kw.get("speed", 30) or 0),
            "speeds": kw.get("speeds") or {"walk": int(kw.get("speed", 30) or 0)}, "moved": 0,
        }
        if kw.get("pc"):
            c["pc"] = kw["pc"]
        if kw.get("ref"):
            c["ref"] = kw["ref"]
        self.combatants[cid] = c
        if self.state.get("round", 0) > 0 and c["init"] is not None:
            self._insert_in_order(cid)
        return c

    def add_pc(self, pc: dict, init: int | None = None) -> dict:
        if any(c.get("pc") == pc["slug"] for c in self.combatants.values()):
            raise RoboDMError(f"{pc['name']} is already in this combat.")
        cid = self._unique_id(pc["name"].split()[0])
        saves = {ab: pc_save_bonus(pc, ab) for ab in ABILITIES}
        c = self._new(cid, pc["name"], kind="pc", side="party", init=init, init_bonus=pc_init_bonus(pc),
                      dex=pc["abilities"].get("dex", 10), ac=pc.get("ac", 10), hp=pc["hp"]["current"],
                      max_hp=pc["hp"]["max"], saves=saves, attacks=pc.get("attacks", []), pc=pc["slug"],
                      resist=pc.get("resist", []), immune=pc.get("immune", []), vulnerable=pc.get("vulnerable", []),
                      size=pc.get("size", "M"), speed=pc.get("speed", 30))
        c["temp_hp"] = pc["hp"].get("temp", 0)
        c["death_saves"] = dict(pc.get("death_saves", {"success": 0, "fail": 0}))
        c["conditions"] = [dict(x) for x in pc.get("conditions", []) if isinstance(x, dict) and not x.get("persistent")]
        if c["hp"] <= 0:
            c["status"] = "down"
        self.say(f"Added **{pc['name']}** (PC) — HP {c['hp']}/{c['max_hp']}, AC {c['ac']}, init {fmt_mod(c['init_bonus'])}")
        return c

    def add_monster(self, profile: dict, count: int = 1, roll_hp: bool = False, side: str = "enemy",
                    name: str | None = None, init: int | None = None) -> list[dict]:
        added = []
        base_name = name or profile["name"]
        existing = [c for c in self.combatants.values() if c.get("ref", {}).get("name") == profile["name"]]
        numbered = count > 1 or existing
        start_n = len(existing) + 1
        for i in range(count):
            label = f"{base_name} {start_n + i}" if numbered else base_name
            hp = profile["hp"]
            hp_note = ""
            if roll_hp and profile.get("hp_formula"):
                try:
                    r = dice.roll(profile["hp_formula"])
                    hp = max(1, r.total)
                    hp_note = f" (rolled {profile['hp_formula']})"
                except dice.DiceError:
                    pass
            c = self._new(self._unique_id(label), label, kind="monster", side=side, init=init,
                          init_bonus=profile["init_bonus"], dex=profile["abilities"].get("dex", 10), ac=profile["ac"],
                          hp=hp, max_hp=hp, saves=profile["saves"], resist=profile["resist"], immune=profile["immune"],
                          vulnerable=profile["vulnerable"], cond_defenses=profile.get("cond_defenses", []),
                          condition_immune=profile.get("condition_immune", []), attacks=profile.get("attacks", []),
                          legendary_actions=profile.get("legendary_actions", 0), xp=profile.get("xp", 0),
                          size=profile.get("size", "M"), speed=profile.get("speed", 30), speeds=profile.get("speeds"),
                          ref={"name": profile["name"], "source": profile.get("source"), "cr": profile.get("cr")})
            added.append(c)
            self.say(f"Added **{label}** (CR {profile.get('cr', '?')}) — HP {hp}{hp_note}, AC {c['ac']}, init {fmt_mod(c['init_bonus'])}")
        return added

    def add_custom(self, name: str, hp: int, ac: int, init_bonus: int = 0, side: str = "enemy", count: int = 1,
                   init: int | None = None, saves: dict | None = None, size: str = "M", speed: int = 30) -> list[dict]:
        out = []
        for i in range(count):
            label = f"{name} {i + 1}" if count > 1 else name
            c = self._new(self._unique_id(label), label, kind="npc", side=side, init=init, init_bonus=init_bonus,
                          ac=ac, hp=hp, max_hp=hp, saves=saves or {ab: 0 for ab in ABILITIES}, size=size, speed=speed)
            out.append(c)
            self.say(f"Added **{label}** — HP {hp}, AC {ac}, init {fmt_mod(init_bonus)}")
        return out

    def remove(self, target: str) -> None:
        c = self.find(target)
        self.combatants.pop(c["id"])
        if c["id"] in self.state["order"]:
            idx = self.state["order"].index(c["id"])
            self.state["order"].remove(c["id"])
            if idx < self.state["turn"]:
                self.state["turn"] -= 1
            if self.state["order"]:
                self.state["turn"] %= len(self.state["order"])
        self.say(f"Removed {c['name']} from combat.")

    # ------------------------------------------------------------------ lookup
    def find(self, target: str) -> dict:
        t = target.strip().lower()
        if t in self.combatants:
            return self.combatants[t]
        sl = slugify(target)
        if sl in self.combatants:
            return self.combatants[sl]
        exact = [c for c in self.combatants.values() if c["name"].lower() == t]
        if len(exact) == 1:
            return exact[0]
        pref = [c for c in self.combatants.values() if c["name"].lower().startswith(t) or c["id"].startswith(sl)]
        if len(pref) == 1:
            return pref[0]
        if not pref:
            raise RoboDMError(f"No combatant '{target}'. In combat: {', '.join(self.combatants) or 'nobody'}")
        raise RoboDMError(f"'{target}' is ambiguous: {', '.join(c['id'] for c in pref)}")

    def find_many(self, targets: list[str]) -> list[dict]:
        out = []
        for t in targets:
            if t.lower() in ("all-enemies", "enemies"):
                out.extend(c for c in self.combatants.values() if c["side"] == "enemy" and c["status"] != "dead")
            elif t.lower() == "area":
                area = self.state.get("last_area") or {}
                if not area.get("targets"):
                    raise RoboDMError("No area targets yet: run `./dm combat area …` first.")
                out.extend(self.combatants[i] for i in area["targets"] if i in self.combatants)
            elif t.lower() in ("party", "all-party"):
                out.extend(c for c in self.combatants.values() if c["side"] == "party" and c["status"] != "dead")
            else:
                out.append(self.find(t))
        seen, uniq = set(), []
        for c in out:
            if c["id"] not in seen:
                seen.add(c["id"])
                uniq.append(c)
        return uniq

    def current(self) -> dict | None:
        order = self.state.get("order", [])
        if not order or self.state.get("round", 0) == 0:
            return None
        return self.combatants.get(order[self.state["turn"] % len(order)])

    # ------------------------------------------------------------------ initiative
    def _auto_rolls_for(self, c: dict) -> bool:
        return c["kind"] != "pc" or self.camp.settings.get("dice_mode") == "auto"

    def roll_initiative(self, reroll: bool = False) -> list[str]:
        self.require_active()
        waiting = []
        for c in self.combatants.values():
            if c["init"] is not None and not reroll:
                continue
            if not self._auto_rolls_for(c):
                c["init"] = None
                waiting.append(c["name"])
                continue
            r = dice.roll_d20(c["init_bonus"], label=f"{c['name']} initiative")
            c["init"] = r.total
            self.say(r.text())
        if waiting:
            self.say(f"Waiting on player initiative rolls: {', '.join(waiting)} — record with `./dm combat init <name> <total>`.", log=False)
        self._sort()
        if not waiting and self.state["round"] == 0:
            self._begin()
        return waiting

    def set_init(self, target: str, value: int) -> None:
        c = self.find(target)
        c["init"] = int(value)
        self.say(f"{c['name']} initiative = {value}")
        if self.state["round"] > 0:
            cur = self.current()
            self._sort()
            if cur and cur["id"] in self.state["order"]:
                self.state["turn"] = self.state["order"].index(cur["id"])
        else:
            self._sort()
            if all(x["init"] is not None for x in self.combatants.values()):
                self._begin()

    def _sort(self) -> None:
        ready = [c for c in self.combatants.values() if c["init"] is not None]
        ready.sort(key=lambda c: (-c["init"], -c["init_bonus"], -c.get("dex", 10), c["name"]))
        self.state["order"] = [c["id"] for c in ready]

    def _insert_in_order(self, cid: str) -> None:
        cur = self.current()
        self._sort()
        if cur and cur["id"] in self.state["order"]:
            self.state["turn"] = self.state["order"].index(cur["id"])

    def _begin(self) -> None:
        if not self.state["order"]:
            return
        self.state["round"] = 1
        self.state["turn"] = 0
        self.say("**Round 1** — initiative order: " + ", ".join(
            f"{self.combatants[i]['name']} ({self.combatants[i]['init']})" for i in self.state["order"]))
        self._start_of_turn(self.current())

    # ------------------------------------------------------------------ turns
    def next_turn(self) -> None:
        self.require_active()
        missing = [c["name"] for c in self.combatants.values() if c["init"] is None]
        if self.state["round"] == 0:
            if missing:
                raise RoboDMError(f"Initiative still missing for: {', '.join(missing)}")
            self._sort()
            self._begin()
            return
        cur = self.current()
        if cur:
            self._end_of_turn(cur)
        order = self.state["order"]
        for _ in range(len(order)):
            self.state["turn"] += 1
            if self.state["turn"] >= len(order):
                self.state["turn"] = 0
                self.state["round"] += 1
                self.say(f"**Round {self.state['round']}**")
            nxt = self.current()
            if nxt and nxt["status"] != "dead":
                break
        self._start_of_turn(self.current())

    def _start_of_turn(self, c: dict | None) -> None:
        if not c:
            return
        c["reaction_used"] = False
        c["spells_this_turn"] = []
        c["moved"] = 0
        c["dashed"] = False
        c["disengaged"] = False
        if c.get("legendary_max"):
            c["legendary_left"] = c["legendary_max"]
        notes = []
        for name in list(c.get("recharge_spent", [])):
            atk = next((a for a in c.get("attacks", []) if a["name"] == name), None)
            need = atk.get("recharge", 6) if atk else 6
            r = dice.roll("1d6")
            if r.total >= need:
                c["recharge_spent"].remove(name)
                notes.append(f"{name} recharged (d6={r.total})")
            else:
                notes.append(f"{name} not recharged (d6={r.total}, needs {need}+)")
        self._tick_conditions(c, "start")
        conds = ", ".join(self._cond_label(x) for x in c["conditions"])
        head = f"▶ **{c['name']}'s turn** (HP {c['hp']}/{c['max_hp']}, AC {c['ac']}{', ' + conds if conds else ''})"
        if c.get("concentration"):
            head += f" · concentrating on {c['concentration']}"
        self.say(head)
        for n in notes:
            self.say(f"  {n}")
        if c["kind"] == "pc" and c["hp"] <= 0 and c["status"] == "down":
            if self._auto_rolls_for(c):
                self.death_save(c["id"])
            else:
                self.say(f"  {c['name']} is dying — ask the player for a death saving throw, then `./dm combat deathsave {c['id']} --result <d20>`.", log=False)
        if any(x["name"] in NO_ACTIONS for x in c["conditions"]):
            self.say(f"  {c['name']} is incapacitated and can't take actions or reactions this turn.")

    def _end_of_turn(self, c: dict) -> None:
        self._tick_conditions(c, "end")

    def _tick_conditions(self, c: dict, when: str) -> None:
        keep = []
        for cond in c["conditions"]:
            if cond.get("tick", "end") != when:
                keep.append(cond)
                continue
            save = cond.get("save")
            if save and when == "end":
                if self._auto_rolls_for(c):
                    r = self._save_roll(c, save["ability"])
                    r.label = ""
                    ok = r.total >= save["dc"] and not self._auto_fail(c, save["ability"])
                    self.say(f"  {c['name']} repeats {save['ability'].upper()} save vs DC {save['dc']} to end {cond['name']}: "
                             f"{r.text()} → {'**success**, ' + cond['name'] + ' ends' if ok else 'failure'}")
                    if ok:
                        continue
                else:
                    self.say(f"  {c['name']} may repeat a {save['ability'].upper()} save (DC {save['dc']}) to end "
                             f"{cond['name']} — `./dm combat save {c['id']} {save['ability']} --dc {save['dc']} --ends {cond['name']} --result <total>`", log=False)
            if cond.get("rounds") is not None:
                cond["rounds"] -= 1
                if cond["rounds"] <= 0:
                    self.say(f"  {cond['name']} on {c['name']} has expired.")
                    continue
            keep.append(cond)
        c["conditions"] = keep

    @staticmethod
    def _cond_label(cond: dict) -> str:
        s = cond["name"]
        if cond["name"] == "exhaustion":
            s += f" {cond.get('level', 1)}"
        if cond.get("rounds") is not None:
            s += f" {cond['rounds']}r"
        return s

    # ------------------------------------------------------------------ conditions
    def add_condition(self, target: str, name: str, rounds: int | None = None, tick: str = "end",
                      save: str | None = None, source: str = "", conc: str | None = None, level: int = 1) -> None:
        c = self.find(target)
        name = name.lower()
        if name not in CONDITIONS:
            self.say(f"(note: '{name}' is not one of the 15 standard conditions; tracking it anyway)", log=False)
        if name in c.get("condition_immune", []):
            self.say(f"**{c['name']} is immune to {name}** — not applied.")
            return
        cond: dict[str, Any] = {"name": name, "tick": tick}
        if rounds is not None:
            cond["rounds"] = int(rounds)
        if save:
            ab, _, dc = save.partition(":")
            cond["save"] = {"ability": normalize_ability(ab), "dc": int(dc)}
        if source:
            cond["source"] = source
        if conc:
            caster = self.find(conc)
            cond["conc"] = caster["id"]
        if name == "exhaustion":
            existing = next((x for x in c["conditions"] if x["name"] == "exhaustion"), None)
            if existing:
                existing["level"] = min(6, existing.get("level", 1) + level)
                self.say(f"{c['name']} exhaustion → level {existing['level']}")
                if existing["level"] >= 6:
                    self._kill(c, "exhaustion level 6")
                return
            cond["level"] = level
        c["conditions"] = [x for x in c["conditions"] if x["name"] != name] + [cond]
        extra = []
        if name in ("paralyzed", "petrified", "stunned", "unconscious") and c.get("concentration"):
            self._break_concentration(c, f"{name}")
        if name == "unconscious":
            extra.append("also prone")
            if not any(x["name"] == "prone" for x in c["conditions"]):
                c["conditions"].append({"name": "prone", "tick": "end"})
        receipts.note("condition", target=c["name"], name=name)
        self.say(f"{c['name']} is now **{self._cond_label(cond)}**{' (' + source + ')' if source else ''}"
                 f"{' — save ' + save.upper() + ' ends' if save else ''}{'; ' + ', '.join(extra) if extra else ''}")

    def remove_condition(self, target: str, name: str) -> None:
        c = self.find(target)
        before = len(c["conditions"])
        c["conditions"] = [x for x in c["conditions"] if x["name"] != name.lower()]
        if len(c["conditions"]) == before:
            raise RoboDMError(f"{c['name']} isn't {name}.")
        self.say(f"{c['name']} is no longer {name}.")

    def speed_now(self, c: dict, mode: str = "walk") -> int:
        """Speed this turn after conditions: 0 if grappled, restrained or incapacitating; halved at exhaustion 2+."""
        if any(self.has(c, x) for x in ("grappled", "restrained", "paralyzed", "petrified", "stunned", "unconscious")):
            return 0
        base = int((c.get("speeds") or {}).get(mode, c.get("speed", 30) if mode == "walk" else 0) or 0)
        ex = self.exhaustion(c)
        return 0 if ex >= 5 else base // 2 if ex >= 2 else base

    def has(self, c: dict, name: str) -> bool:
        return any(x["name"] == name for x in c["conditions"])

    def exhaustion(self, c: dict) -> int:
        e = next((x for x in c["conditions"] if x["name"] == "exhaustion"), None)
        return e.get("level", 1) if e else 0

    # ------------------------------------------------------------------ concentration
    def set_concentration(self, target: str, spell: str | None) -> None:
        c = self.find(target)
        if spell:
            if c.get("concentration"):
                self._break_concentration(c, f"started concentrating on {spell}")
            c["concentration"] = spell
            self.say(f"{c['name']} is concentrating on **{spell}**.")
        else:
            self._break_concentration(c, "ended voluntarily")

    def _break_concentration(self, c: dict, why: str) -> None:
        spell = c.get("concentration")
        if not spell:
            return
        c["concentration"] = None
        self.say(f"💥 {c['name']} loses concentration on **{spell}** ({why}).")
        for other in self.combatants.values():
            linked = [x for x in other["conditions"] if x.get("conc") == c["id"]]
            if linked:
                other["conditions"] = [x for x in other["conditions"] if x.get("conc") != c["id"]]
                self.say(f"  {other['name']} is freed from {', '.join(x['name'] for x in linked)}.")

    # ------------------------------------------------------------------ damage & healing
    def _defense_multiplier(self, c: dict, dtype: str, nonmagical: bool, silvered: bool, adamantine: bool) -> tuple[float, str]:
        if not dtype:
            return 1.0, ""
        dtype = dtype.lower()
        if dtype in c.get("immune", []):
            return 0.0, "immune"
        mult, note = 1.0, ""
        if dtype in c.get("resist", []):
            mult, note = 0.5, "resistant"
        for cd in c.get("cond_defenses", []):
            if dtype not in cd.get("types", []):
                continue
            text = cd.get("note", "").lower()
            applies = False
            if "nonmagical" in text or "non-magical" in text:
                applies = nonmagical
                if applies and "silvered" in text and silvered:
                    applies = False
                if applies and "adamantine" in text and adamantine:
                    applies = False
            if applies:
                if cd["kind"] == "immune":
                    return 0.0, f"immune ({cd['note']})"
                if cd["kind"] == "resist":
                    mult, note = 0.5, f"resistant ({cd['note']})"
            else:
                self.say(f"  (check: {c['name']} has conditional {cd['kind']} to {dtype} {cd.get('note', '')}"
                         f" — not applied{'; pass --nonmagical if it qualifies' if 'magical' in text else ''})", log=False)
        if dtype in c.get("vulnerable", []):
            mult, note = (1.0 if mult == 0.5 else 2.0), ("vulnerable" if mult != 0.5 else "resistant+vulnerable")
        return mult, note

    def damage(self, target: str, parts: list[tuple[int, str]] | int, dtype: str = "", nonmagical: bool = False,
               crit: bool = False, source: str = "", nonlethal: bool = False, silvered: bool = False,
               adamantine: bool = False) -> int:
        self.require_active()
        c = self.find(target)
        if isinstance(parts, int):
            parts = [(parts, dtype)]
        total, notes = 0, []
        for amount, dt in parts:
            mult, why = self._defense_multiplier(c, dt, nonmagical, silvered, adamantine)
            dealt = int(amount * mult) if mult != 0.5 else amount // 2
            if why:
                notes.append(f"{amount} {dt} → {dealt} ({why})")
            total += dealt
        self._apply_damage(c, total, crit=crit, source=source, nonlethal=nonlethal, notes=notes,
                           raw=sum(a for a, _ in parts))
        return total

    def _apply_damage(self, c: dict, total: int, crit: bool = False, source: str = "", nonlethal: bool = False,
                      notes: list[str] | None = None, raw: int | None = None) -> None:
        notes = notes or []
        if c["status"] == "dead":
            self.say(f"{c['name']} is already dead.")
            return
        absorbed = min(c.get("temp_hp", 0), total)
        if absorbed:
            c["temp_hp"] -= absorbed
            notes.append(f"{absorbed} absorbed by temp HP")
        remaining = total - absorbed
        was_down = c["hp"] <= 0
        before = c["hp"]
        c["hp"] = max(0, c["hp"] - remaining)
        overflow = remaining - before if remaining > before else 0
        src = f" from {source}" if source else ""
        msg = f"🩸 **{c['name']}** takes **{total}** damage{src}"
        if notes:
            msg += f" ({'; '.join(notes)})"
        msg += f" → HP {c['hp']}/{c['max_hp']}"
        if c.get("temp_hp"):
            msg += f" (+{c['temp_hp']} temp)"
        self.say(msg)
        receipts.note("damage", target=c["name"], amount=total, raw=raw, hp=c["hp"], max=c["max_hp"],
                      pc=c["kind"] == "pc")
        if total <= 0:
            return
        if c["kind"] == "pc":
            if was_down:
                if remaining >= c["max_hp"]:
                    self._kill(c, "massive damage while dying")
                    return
                fails = 2 if crit else 1
                c["death_saves"]["fail"] += fails
                self.say(f"  Damage while dying: +{fails} death save failure ({c['death_saves']['fail']}/3).")
                if c["death_saves"]["fail"] >= 3:
                    self._kill(c, "three failed death saves")
                elif c["status"] == "stable":
                    c["status"] = "down"
                return
            if c["hp"] == 0:
                if overflow >= c["max_hp"]:
                    self._kill(c, f"massive damage ({overflow} over max HP)")
                    return
                c["status"] = "down"
                c["death_saves"] = {"success": 0, "fail": 0}
                if not self.has(c, "unconscious"):
                    c["conditions"].append({"name": "unconscious", "tick": "end", "auto": "dying"})
                    c["conditions"].append({"name": "prone", "tick": "end"})
                self.say(f"  **{c['name']} drops to 0 HP and falls unconscious!** Death saves begin on their turn.")
                receipts.note("down", target=c["name"])
                self._break_concentration(c, "dropped to 0 HP")
                return
        else:
            if c["hp"] == 0:
                if nonlethal:
                    c["status"] = "stable"
                    if not self.has(c, "unconscious"):
                        c["conditions"].append({"name": "unconscious", "tick": "end"})
                    self.say(f"  {c['name']} is knocked out (nonlethal).")
                    receipts.note("down", target=c["name"])
                    self._break_concentration(c, "knocked out")
                else:
                    self._kill(c, "reduced to 0 HP")
                return
        if c.get("concentration") and total > 0:
            dc = max(10, total // 2)
            if self._auto_rolls_for(c):
                r = self._save_roll(c, "con")
                r.label = ""
                # Saving throws have no automatic success/failure on a natural 20/1 (PHB p.179).
                if r.total >= dc:
                    self.say(f"  Concentration check (DC {dc}): {r.text()} → holds **{c['concentration']}**")
                else:
                    self.say(f"  Concentration check (DC {dc}): {r.text()} → **fails**")
                    self._break_concentration(c, f"failed DC {dc} concentration save")
            else:
                self.say(f"  {c['name']} must make a **DC {dc} Constitution save** to keep concentration on "
                         f"{c['concentration']} — `./dm combat save {c['id']} con --dc {dc} --concentration --result <total>`")

    def _kill(self, c: dict, why: str) -> None:
        c["status"] = "dead"
        c["hp"] = 0
        self._break_concentration(c, "died")
        self.say(f"💀 **{c['name']} dies** ({why}).")
        receipts.note("dead", target=c["name"])
        if c["kind"] != "pc":
            self.state["xp_defeated"] = self.state.get("xp_defeated", 0) + c.get("xp", 0)
            self.state.setdefault("defeated", []).append({"name": c["name"], "ref": c.get("ref", {}).get("name"), "xp": c.get("xp", 0)})

    def heal(self, target: str, amount: int, source: str = "") -> None:
        c = self.find(target)
        if c["status"] == "dead":
            raise RoboDMError(f"{c['name']} is dead; healing doesn't work (needs revivify or similar).")
        before = c["hp"]
        c["hp"] = min(c["max_hp"], c["hp"] + amount)
        msg = f"💚 **{c['name']}** heals {c['hp'] - before}{' from ' + source if source else ''} → HP {c['hp']}/{c['max_hp']}"
        if before <= 0 < c["hp"]:
            c["status"] = "ok"
            c["death_saves"] = {"success": 0, "fail": 0}
            c["conditions"] = [x for x in c["conditions"] if not (x["name"] == "unconscious")]
            msg += " — back on their feet (still prone)"
        self.say(msg)
        receipts.note("heal", target=c["name"], amount=c["hp"] - before, hp=c["hp"], max=c["max_hp"])

    def set_temp_hp(self, target: str, amount: int) -> None:
        c = self.find(target)
        if amount <= c.get("temp_hp", 0):
            self.say(f"{c['name']} keeps {c['temp_hp']} temp HP (temporary HP doesn't stack; higher value kept).")
            return
        c["temp_hp"] = amount
        self.say(f"{c['name']} gains {amount} temporary HP.")

    # ------------------------------------------------------------------ rolls
    def _auto_fail(self, c: dict, ability: str) -> bool:
        return ability in ("str", "dex") and any(self.has(c, x) for x in AUTO_FAIL_STR_DEX)

    def _save_roll(self, c: dict, ability: str, adv: bool = False, dis: bool = False, extra: int = 0) -> dice.RollResult:
        notes = []
        if ability == "dex" and self.has(c, "restrained"):
            dis = True
            notes.append("restrained: disadv. on DEX saves")
        if self.exhaustion(c) >= 3:
            dis = True
            notes.append("exhaustion 3+: disadv.")
        r = dice.roll_d20(int(c["saves"].get(ability, 0)) + extra, adv=adv, dis=dis, label=f"{c['name']} {ability.upper()} save")
        if extra:
            notes.append(f"+{extra} from cover")
        r.notes.extend(notes)
        return r

    def saving_throw(self, targets: list[str], ability: str, dc: int, damage: str | None = None, dtype: str = "",
                     half: bool = True, on_fail: str | None = None, rounds: int | None = None, repeat: str | None = None,
                     conc: str | None = None, source: str = "", adv: bool = False, dis: bool = False,
                     results: dict[str, int] | None = None, ends: str | None = None, concentration: bool = False,
                     nonmagical: bool = False, bonus: dict[str, int] | None = None) -> None:
        ability = normalize_ability(ability)
        cs = self.find_many(targets)
        results = {k.lower(): v for k, v in (results or {}).items()}
        dmg_roll = None
        if damage:
            dmg_roll = dice.roll(damage, label="damage")
            self.say(f"Damage roll: {dmg_roll.text()}{' ' + dtype if dtype else ''}")
        for c in cs:
            given = results.get(c["id"]) if c["id"] in results else results.get(c["name"].lower())
            if given is None and c["kind"] == "pc" and not self._auto_rolls_for(c):
                self.say(f"⏳ {c['name']}: ask the player for a {ability.upper()} save — then re-run with `--results {c['id']}=<total>`.", log=False)
                continue
            total = None
            if self._auto_fail(c, ability):
                ok, txt = False, "automatically fails (incapacitating condition)"
            elif given is not None:
                total = int(given)
                ok, txt = total >= dc, f"player rolled {given}"
            else:
                r = self._save_roll(c, ability, adv=adv, dis=dis, extra=(bonus or {}).get(c["id"], 0))
                r.label = ""
                total = r.total
                ok, txt = r.total >= dc, r.text()
            receipts.note("save", who=c["name"], ability=ability.upper(), dc=dc, total=total, ok=ok)
            self.say(f"{'✅' if ok else '❌'} {c['name']} {ability.upper()} save vs DC {dc}: {txt} → **{'success' if ok else 'failure'}**")
            if concentration and not ok:
                self._break_concentration(c, f"failed DC {dc} concentration save")
            if ends and ok:
                before = len(c["conditions"])
                c["conditions"] = [x for x in c["conditions"] if x["name"] != ends.lower()]
                if len(c["conditions"]) < before:
                    self.say(f"  {c['name']} is no longer {ends}.")
            if dmg_roll is not None:
                amount = dmg_roll.total if not ok else (dmg_roll.total // 2 if half else 0)
                if amount:
                    self.damage(c["id"], [(amount, dtype)], source=source, nonmagical=nonmagical)
                else:
                    self.say(f"  {c['name']} takes no damage.")
            if on_fail and not ok:
                save_spec = f"{ability}:{dc}" if repeat else None
                self.add_condition(c["id"], on_fail, rounds=rounds, tick=repeat or "end", save=save_spec, source=source, conc=conc)

    def attack(self, attacker: str, target: str, action: str | None = None, to_hit: int | None = None,
               damage: list[tuple[str, str]] | None = None, adv: bool = False, dis: bool = False, ranged: bool = False,
               nonmagical: bool = False, result: int | None = None, nat: int | None = None,
               damage_result: int | None = None, nonlethal: bool = False, silvered: bool = False,
               within_5ft: bool | None = None, cover: int = 0, extra_dis: list[str] | None = None) -> None:
        self.require_active()
        a = self.find(attacker)
        t = self.find(target)
        label = action or "attack"
        if action:
            atk = next((x for x in a.get("attacks", []) if x["name"].lower().startswith(action.lower())), None)
            if not atk:
                names = ", ".join(x["name"] for x in a.get("attacks", [])) or "none recorded"
                raise RoboDMError(f"{a['name']} has no attack '{action}'. Known: {names}")
            label = atk["name"]
            if atk.get("recharge") and atk["name"] in a.get("recharge_spent", []):
                raise RoboDMError(f"{atk['name']} hasn't recharged yet.")
            if to_hit is None:
                to_hit = atk.get("to_hit")
            if damage is None:
                damage = [tuple(d) for d in atk.get("damage", [])]
            if atk.get("recharge"):
                a.setdefault("recharge_spent", []).append(atk["name"])
            if to_hit is None:
                raise RoboDMError(f"{atk['name']} isn't an attack roll (it may be a saving-throw effect: use `combat save`).")
        if to_hit is None:
            raise RoboDMError("Give --action or --to-hit.")
        # advantage / disadvantage from conditions
        reasons_adv, reasons_dis = [], []
        if adv:
            reasons_adv.append("stated")
        if dis:
            reasons_dis.append("stated")
        for cond, why in ATTACKER_DISADV.items():
            if self.has(a, cond):
                reasons_dis.append(why)
        if self.exhaustion(a) >= 3:
            reasons_dis.append("attacker exhaustion 3+")
        if self.has(a, "invisible"):
            reasons_adv.append("attacker invisible")
        for cond, why in TARGET_GIVES_ADV.items():
            if self.has(t, cond):
                reasons_adv.append(why)
        if self.has(t, "invisible"):
            reasons_dis.append("target invisible")
        reasons_dis.extend(extra_dis or [])
        if self.has(t, "prone"):
            (reasons_dis if ranged else reasons_adv).append("target prone" + (" (ranged)" if ranged else " (within 5 ft)"))
        has_adv, has_dis = bool(reasons_adv), bool(reasons_dis)
        if any(self.has(a, x) for x in NO_ACTIONS):
            self.say(f"(warning: {a['name']} is incapacitated and normally can't attack)", log=False)
        if result is not None:
            total = int(result)
            natural = nat
            roll_txt = f"player rolled {total}{' (nat ' + str(nat) + ')' if nat else ''}"
        else:
            r = dice.roll_d20(int(to_hit), adv=has_adv and not has_dis, dis=has_dis and not has_adv, label=f"{a['name']} → {t['name']} ({label})")
            total, natural, roll_txt = r.total, r.natural, r.text()
        adv_txt = ""
        if has_adv or has_dis:
            adv_txt = f" [adv: {', '.join(reasons_adv) or '—'} | dis: {', '.join(reasons_dis) or '—'}" + (
                " → cancel]" if has_adv and has_dis else "]")
        crit = natural == 20
        ac = t["ac"] + (cover if cover < 99 else 0)
        hit = crit or (natural != 1 and total >= ac)
        melee_close = (not ranged) if within_5ft is None else within_5ft
        if hit and not crit and melee_close and (self.has(t, "paralyzed") or self.has(t, "unconscious")):
            crit = True
            adv_txt += " (auto-crit: melee hit on paralyzed/unconscious target)"
        verdict = "**CRITICAL HIT!**" if crit else ("**hit**" if hit else ("miss (nat 1)" if natural == 1 else "miss"))
        cover_txt = f" ({t['ac']} +{cover} {'half' if cover == 2 else 'three-quarters'} cover)" if cover in (2, 5) else ""
        self.say(f"🎯 {roll_txt} vs AC {ac}{cover_txt} → {verdict}{adv_txt}")
        receipts.note("attack", by=a["name"], target=t["name"], hit=hit, crit=crit, total=total, ac=ac,
                      weapon=label)
        if not hit or not damage:
            return
        parts: list[tuple[int, str]] = []
        if damage_result is not None:
            parts = [(int(damage_result), damage[0][1] if damage else "")]
            self.say(f"  Damage (player rolled): {damage_result}")
        else:
            for expr, dt in damage:
                dr = dice.roll(expr, crit=crit, crit_rule=self.camp.settings.get("crit_rule", "double_dice"))
                parts.append((max(0, dr.total), dt))
                self.say(f"  Damage: {dr.text()} {dt}".rstrip())
        self.damage(t["id"], parts, nonmagical=nonmagical, crit=crit, source=f"{a['name']}'s {label}",
                    nonlethal=nonlethal, silvered=silvered)

    def death_save(self, target: str, result: int | None = None) -> None:
        c = self.find(target)
        if c["kind"] != "pc":
            raise RoboDMError("Only player characters make death saves (monsters die at 0 HP).")
        if c["hp"] > 0:
            raise RoboDMError(f"{c['name']} isn't dying.")
        if c["status"] in ("dead", "stable"):
            raise RoboDMError(f"{c['name']} is {c['status']}.")
        if result is None:
            r = dice.roll("1d20", label=f"{c['name']} death save")
            value, txt = r.total, r.text()
        else:
            value, txt = int(result), f"player rolled {result}"
        ds = c["death_saves"]
        if value == 20:
            c["hp"] = 1
            c["status"] = "ok"
            c["death_saves"] = {"success": 0, "fail": 0}
            c["conditions"] = [x for x in c["conditions"] if x["name"] != "unconscious"]
            self.say(f"☠️ Death save: {txt} → **NAT 20! {c['name']} regains 1 HP and wakes up!**")
            return
        if value == 1:
            ds["fail"] += 2
        elif value >= 10:
            ds["success"] += 1
        else:
            ds["fail"] += 1
        self.say(f"☠️ Death save: {txt} → {ds['success']}✓ / {ds['fail']}✗")
        if ds["fail"] >= 3:
            self._kill(c, "three failed death saves")
        elif ds["success"] >= 3:
            c["status"] = "stable"
            self.say(f"  {c['name']} is **stable** (unconscious at 0 HP; regains 1 HP in 1d4 hours).")

    def stabilize(self, target: str) -> None:
        c = self.find(target)
        if c["hp"] > 0 or c["status"] == "dead":
            raise RoboDMError(f"{c['name']} doesn't need stabilizing.")
        c["status"] = "stable"
        c["death_saves"] = {"success": 0, "fail": 0}
        self.say(f"{c['name']} is stabilized.")

    def use_legendary(self, target: str, cost: int = 1, action: str = "") -> None:
        c = self.find(target)
        if not c.get("legendary_max"):
            raise RoboDMError(f"{c['name']} has no legendary actions.")
        if c["legendary_left"] < cost:
            raise RoboDMError(f"{c['name']} has only {c['legendary_left']} legendary action(s) left this round.")
        c["legendary_left"] -= cost
        self.say(f"{c['name']} uses a legendary action{': ' + action if action else ''} ({c['legendary_left']}/{c['legendary_max']} left)")

    # ------------------------------------------------------------------ status & end
    def status_text(self, reveal: bool = True) -> str:
        if not self.active:
            return "No active combat."
        cur = self.current()
        title = f"⚔️ {self.state.get('name') or 'Combat'} — Round {self.state.get('round', 0)}"
        lines = [title]
        order = self.state["order"] + [cid for cid in self.combatants if cid not in self.state["order"]]
        for cid in order:
            c = self.combatants[cid]
            marker = "▶" if cur and cid == cur["id"] else " "
            init = f"{c['init']:>2}" if c["init"] is not None else "--"
            tag = {"pc": "PC", "monster": "", "npc": ""}.get(c["kind"], "")
            side = "" if c["side"] in ("enemy", "party") else f" ({c['side']})"
            if c["status"] == "dead":
                lines.append(f"{marker} {init}  ~~{c['name']}~~ 💀")
                continue
            hp = f"HP {c['hp']}/{c['max_hp']}" + (f"+{c['temp_hp']}t" if c.get("temp_hp") else "")
            if not reveal and c["kind"] != "pc":
                hp = health_word(c)
            else:
                hp += f" ({health_word(c)})" if c["kind"] != "pc" else ""
            extra = []
            if c["kind"] == "pc" and c["hp"] <= 0:
                ds = c["death_saves"]
                extra.append(f"{c['status'].upper()} {ds['success']}✓/{ds['fail']}✗")
            if c["conditions"]:
                extra.append("[" + ", ".join(self._cond_label(x) for x in c["conditions"]) + "]")
            if c.get("concentration"):
                extra.append(f"conc: {c['concentration']}")
            if c.get("legendary_max"):
                extra.append(f"LA {c['legendary_left']}/{c['legendary_max']}")
            if c.get("recharge_spent"):
                extra.append("spent: " + ", ".join(c["recharge_spent"]))
            if c.get("pos"):
                from robodm.battlemap import fmt_cell

                extra.append(f"@{fmt_cell(*c['pos'])}")
            if c.get("notes"):
                extra.append(f"📍 {c['notes']}")
            lines.append(f"{marker} {init}  **{c['name']}**{' ' + tag if tag else ''}{side} · {hp} · AC {c['ac']}"
                         + (" · " + " ".join(extra) if extra else ""))
        waiting = [c["name"] for c in self.combatants.values() if c["init"] is None]
        if waiting:
            lines.append(f"(no initiative yet: {', '.join(waiting)})")
        return "\n".join(lines)

    def end(self, award_xp: bool = False) -> str:
        self.require_active()
        defeated = self.state.get("defeated", [])
        xp = self.state.get("xp_defeated", 0)
        pcs = [c for c in self.combatants.values() if c["kind"] == "pc"]
        counts: dict[str, int] = {}
        for d in defeated:
            counts[d.get("ref") or d["name"]] = counts.get(d.get("ref") or d["name"], 0) + 1
        summary = f"Combat '{self.state.get('name') or 'unnamed'}' ended after {self.state.get('round', 0)} round(s)."
        if counts:
            summary += " Defeated: " + ", ".join(f"{n}× {k}" for k, n in counts.items()) + f" ({xp} XP"
            if pcs:
                summary += f", {xp // len(pcs)} each for {len(pcs)} PCs"
            summary += ")."
        fallen = [c["name"] for c in pcs if c["status"] == "dead"]
        down = [c["name"] for c in pcs if c["status"] in ("down", "stable")]
        if fallen:
            summary += f" Fallen: {', '.join(fallen)}."
        if down:
            summary += f" Unconscious: {', '.join(down)}."
        # combat-only conditions end with the encounter
        for c in self.combatants.values():
            c["conditions"] = [x for x in c["conditions"] if x["name"] in ("exhaustion",) or x.get("auto") == "dying"
                               or (c["kind"] == "pc" and c["hp"] <= 0 and x["name"] in ("unconscious", "prone"))]
            c["concentration"] = None
        self._sync_to_sheets()
        if award_xp and pcs and xp:
            each = xp // len(pcs)
            for c in pcs:
                pc = self.camp.find_pc(c["pc"])
                if pc:
                    pc["xp"] = pc.get("xp", 0) + each
                    self.camp.save_pc(pc)
            summary += f" Awarded {each} XP to each PC."
        self.camp.log(summary)
        archive = self.camp.path("journal", "combat-log.md")
        prev = archive.read_text(encoding="utf-8") if archive.exists() else "# Combat log\n"
        from robodm.util import write_text

        write_text(archive, prev.rstrip("\n") + f"\n\n## {self.state.get('name') or 'Combat'} — {now_iso()[:16]}\n"
                   + "\n".join(f"- {x}" for x in self.state["log"][-120:]) + f"\n\n**Result:** {summary}\n")
        self.state = self._empty()
        write_json(self.path, self.state)
        self.messages.append(summary)
        return summary


def parse_damage_spec(spec: str) -> list[tuple[str, str]]:
    """'1d8+3 slashing, 2d6 fire' -> [('1d8+3','slashing'), ('2d6','fire')]"""
    out = []
    for part in re.split(r",|\bplus\b", spec):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([0-9d+\-%khlro!<>=minax\s]+?)\s*([a-z]+)?$", part, re.IGNORECASE)
        if not m:
            raise RoboDMError(f"Can't read damage '{part}'. Use e.g. '1d8+3 slashing'.")
        out.append((m.group(1).replace(" ", ""), (m.group(2) or "").lower()))
    return out


def ability_save_bonus_from_scores(scores: dict, profs: list[str], pb: int) -> dict:
    return {ab: ability_mod(scores.get(ab, 10)) + (pb if ab in profs else 0) for ab in ABILITIES}
