"""Battle maps: grid geometry, movement, cover and areas, the gate on the map, rendering, multiclass slots."""

from __future__ import annotations

import unittest

from robodm import battlemap as bm, campaign
from robodm.combat import Encounter
from tests.helpers import RoboDMTestCase
from tests.test_campaign_memory import run_cli

YARD = """\
..........
..........
....#.....
....#.....
..........
...o......"""


def creature(cid, pos, side="enemy", size="M", **kw):
    return {"id": cid, "name": cid.title(), "pos": list(pos), "size": size, "side": side, "status": "ok", "hp": 10,
            "max_hp": 10, "conditions": [], "attacks": [], "kind": "monster", "reaction_used": False, **kw}


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.m = bm.from_text(YARD)

    def test_cells_and_map_text(self):
        self.assertEqual(bm.parse_cell("A1"), (0, 0))
        self.assertEqual(bm.parse_cell("c4"), (2, 3))
        self.assertEqual(bm.fmt_cell(26, 9), "AA10")
        self.assertEqual(bm.parse_cells("B2:C3", self.m), [(1, 1), (2, 1), (1, 2), (2, 2)])
        self.assertEqual((self.m["w"], self.m["h"]), (10, 6))
        with self.assertRaises(Exception):
            bm.from_text("..X..")
        self.assertEqual(bm.from_text(". . #\n# . .")["rows"], ["..#", "#.."])   # spaced rows work too

    def test_distance_and_diagonals(self):
        a, b = [(0, 0)], [(2, 2)]
        self.assertEqual(bm.distance(a, [(1, 0)]), 5)
        self.assertEqual(bm.distance(a, b), 10)                        # PHB: every diagonal 5 ft
        self.assertEqual(bm.distance(a, b, "5-10-5"), 15)              # DMG variant: 5 + 10
        big = creature("ogre", (3, 3), size="L")                        # occupies D4:E5
        self.assertEqual(bm.distance([(2, 3)], bm.footprint(big)), 5)

    def test_paths_walls_difficult_terrain_and_creatures(self):
        mover = creature("mira", (2, 2), side="party")
        cost, path = bm.find_path(self.m, {"mira": mover}, mover, (6, 2))
        self.assertEqual(cost, 20)                                     # round the wall at E3/E4 (diagonals cost 5)
        self.assertFalse({(4, 2), (4, 3)} & set(path))
        self.assertEqual(bm.find_path(self.m, {"mira": mover}, mover, (6, 2), "5-10-5")[0], 25)
        mover["pos"] = [2, 5]
        self.assertEqual(bm.path_cost(self.m, {}, mover, [(2, 5), (3, 5), (4, 5)])[0], 15)   # crate square costs 10
        cost, path = bm.find_path(self.m, {"mira": mover}, mover, (4, 5))
        self.assertEqual(cost, 10)                                      # cheaper to step round the crate
        self.assertNotIn((3, 5), path)
        foe = creature("goblin", (3, 0))
        friend = creature("thorin", (3, 1), side="party")
        mover["pos"] = [2, 0]
        group = {"mira": mover, "goblin": foe, "thorin": friend}
        self.assertIsNone(bm.path_cost(self.m, group, mover, [(2, 0), (3, 0)])[0])    # can't cross a hostile space
        self.assertEqual(bm.path_cost(self.m, group, mover, [(2, 0), (3, 1), (4, 1)])[0], 15)   # ally space: difficult
        self.assertIsNotNone(bm.check_space(self.m, group, mover, (3, 1)))          # can't end there

    def test_opportunity_attacks(self):
        mover = creature("mira", (1, 1), side="party")
        foe = creature("goblin", (2, 1))
        group = {"mira": mover, "goblin": foe}
        path = [(1, 1), (0, 1)]
        self.assertEqual([h["id"] for h, _ in bm.opportunity_attacks(self.m, group, mover, path)], ["goblin"])
        foe["reaction_used"] = True
        self.assertEqual(bm.opportunity_attacks(self.m, group, mover, path), [])

    def test_cover(self):
        archer = creature("archer", (2, 2), side="party")
        behind_wall = creature("goblin", (6, 2))
        self.assertEqual(bm.cover(self.m, {}, behind_wall, attacker=archer), 2)       # sight lines graze the wall's top edge
        tall = bm.from_text("....#.....\n" * 4)
        self.assertEqual(bm.cover(tall, {}, behind_wall, attacker=archer), 99)        # fully behind a wall
        in_open = creature("goblin", (6, 0))
        self.assertEqual(bm.cover(self.m, {}, in_open, attacker=archer), 0)
        crate_cover = creature("goblin", (4, 5))
        archer["pos"] = [1, 5]
        self.assertEqual(bm.cover(self.m, {}, crate_cover, attacker=archer), 2)        # low obstacle: half
        shield = creature("guard", (2, 5))
        self.assertEqual(bm.cover(self.m, {"guard": shield}, crate_cover, attacker=archer), 2)
        archer["pos"] = [2, 2]                                                         # C3, beside the wall's top
        self.assertEqual(bm.cover(self.m, {}, creature("g", (5, 3)), attacker=archer), 99)   # F4: right behind it
        self.assertEqual(bm.cover(self.m, {}, creature("g", (6, 4)), attacker=archer), 5)    # G5: past the corner
        self.assertEqual(bm.cover(self.m, {}, creature("g", (4, 4)), attacker=archer), 2)    # E5: just below it

    def test_areas(self):
        m = bm.new_map(15, 15)
        fireball = bm.area_cells(m, "sphere", 20, (7.5, 7.5))
        self.assertIn((7, 3), fireball)
        self.assertNotIn((3, 3), fireball)
        self.assertEqual(len(fireball), len(set(fireball)))
        caster = [(7, 7)]
        origin = bm.edge_point(caster, (10.5, 7.5))
        cone = bm.area_cells(m, "cone", 15, origin, (1, 0), exclude=set(caster))
        self.assertIn((8, 7), cone)
        self.assertIn((10, 8), cone)
        self.assertNotIn((7, 7), cone)
        self.assertNotIn((6, 7), cone)
        line = bm.area_cells(m, "line", 30, origin, (1, 0), exclude=set(caster))
        self.assertEqual(sorted(line), [(x, 7) for x in range(8, 14)])
        cube = bm.area_cells(m, "cube", 10, (2, 2))
        self.assertEqual(sorted(cube), [(2, 2), (2, 3), (3, 2), (3, 3)])

    def test_text_render(self):
        text = bm.render_text(self.m, {"g": creature("goblin 1", (1, 1)) | {"name": "Goblin 1", "kind": "monster"}},
                              reveal=False)
        self.assertIn("G1", text)
        self.assertIn("##", text)
        self.assertIn("Goblin 1 · B2", text)


class BattleTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        run_cli("campaign", "new", "Map Camp", "--premise", "test")
        run_cli("pc", "add", "Thorin", "--class", "Fighter", "--level", "3", "--str", "16", "--con", "14", "--ac", "18",
                "--speed", "25")
        run_cli("pc", "add", "Mira", "--class", "Wizard", "--level", "5", "--dex", "14", "--int", "17", "--ac", "12")
        run_cli("pc", "attack", "mira", "add", "Shortbow", "--to-hit", "5", "--damage", "1d6+2 piercing", "--range", "80/320")
        run_cli("combat", "start", "Yard")
        run_cli("combat", "party")
        run_cli("combat", "add", "mire imp")

    def ok(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 0, f"{argv}: {out}{err}")
        return out

    def blocked(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 1, f"{argv} should be blocked: {out}")
        self.assertIn("RULES GATE", err)
        return err

    def setup_map(self):
        self.ok("combat", "map", "draw", "--text", YARD.replace("\n", "\\n"))
        self.ok("combat", "place", "thorin", "B2", "mira", "A1", "mire-imp", "C2")
        self.ok("combat", "init", "mira", "20")
        self.ok("combat", "init", "thorin", "15")
        self.ok("combat", "init", "mire-imp", "10")

    def test_initiative_needs_a_map_and_placements(self):
        self.assertIn("no battle map yet", self.blocked("combat", "init"))
        self.ok("combat", "map", "new", "10x6")
        self.ok("combat", "place", "thorin", "B2")
        self.assertIn("not on the map yet: Mira, Mire Imp", self.blocked("combat", "init"))
        self.ok("combat", "place", "party", "A1", "enemies", "H5")
        self.assertIn("Round 1", self.ok("combat", "init", "--reroll"))

    def test_movement_budget_walls_and_opportunity_attacks(self):
        self.setup_map()
        self.ok("combat", "next")                                    # Thorin's turn (speed 25)
        self.assertIn("can't reach E3", self.blocked("combat", "move", "thorin", "E3"))     # a wall
        err = self.blocked("combat", "move", "thorin", "H6")
        self.assertIn("ft left this turn (speed 25", err)
        out = self.ok("combat", "move", "thorin", "B4")
        self.assertIn("B2 → B4 (10 ft; 15 ft left", out)
        self.assertIn("Leaves Mire Imp's reach", out)                   # imp at C2 gets an opportunity attack
        self.assertIn("--reaction", out)
        self.ok("combat", "move", "thorin", "B6", "--disengage")
        self.assertIn("ft left this turn", self.blocked("combat", "move", "thorin", "E6"))
        self.assertIn("it's Thorin's turn", self.blocked("combat", "move", "mira", "A2"))

    def test_attack_reach_range_and_cover(self):
        self.setup_map()
        self.ok("combat", "next")
        self.ok("combat", "next")                                    # the imp's turn, at C2
        self.rng.push(10, 3, 2)
        out = self.ok("combat", "attack", "mire-imp", "thorin", "--action", "sting")   # adjacent: fine
        self.assertIn("📏 5 ft", out)
        err = self.blocked("combat", "attack", "mire-imp", "mira", "--action", "sting")    # A1 is 10 ft away
        self.assertIn("5-ft reach", err)
        self.ok("combat", "next")                                    # round 2: Mira
        self.ok("combat", "move", "mira", "A5")
        self.rng.push(12, 3)
        out = self.ok("combat", "attack", "mira", "mire-imp", "--action", "shortbow")
        self.assertIn("ranged", out)
        run_cli("combat", "place", "mire-imp", "F3")                 # behind the wall at E3/E4
        self.assertIn("total cover", self.blocked("combat", "attack", "mira", "mire-imp", "--action", "shortbow"))

    def test_area_targets_and_save_cover_bonus(self):
        self.setup_map()
        run_cli("audit", "clear", "spell", "Ember Burst", "--verdict", "CORRECT", "--notes", "ok")
        out = self.ok("combat", "area", "sphere", "10", "--at", "C2", "--by", "mira", "--spell", "Ember Burst")
        self.assertIn("affects", out)
        self.assertIn("Mire Imp", out)
        self.assertIn("Allies are inside the area too", out)          # Thorin at B2
        enc = Encounter(campaign.load())
        self.assertEqual(sorted(enc.state["last_area"]["targets"]), ["mire-imp", "thorin"])
        self.rng.push(3, 3, 3, 3, 3, 3, 15, 10)
        out = self.ok("combat", "save", "dex", "area", "--dc", "13", "--damage", "6d6", "--type", "fire")
        self.assertIn("Mire Imp DEX save", out)
        self.assertIn("Thorin DEX save", out)

    def test_render_save_and_load(self):
        self.setup_map()
        out = self.ok("combat", "map", "show")
        self.assertIn("Th", out)
        self.assertIn("##", out)
        path = self.ok("combat", "map", "render", "--html").strip().split(" ", 1)[1]
        with open(path, encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("<svg", page)
        self.assertIn("Mire Imp", page)
        self.ok("combat", "map", "save", "yard")
        self.assertIn("yard", self.ok("combat", "map", "list"))
        self.ok("combat", "map", "clear")
        self.ok("combat", "map", "load", "yard")
        self.assertIn("##", self.ok("combat", "map", "show"))


class MulticlassTests(RoboDMTestCase):
    def test_multiclass_slot_table(self):
        mc = campaign.multiclass_slots
        self.assertEqual(mc(campaign.parse_classes("Wizard 3, Cleric 2")),
                         {"1": {"max": 4, "used": 0}, "2": {"max": 3, "used": 0}, "3": {"max": 2, "used": 0}})
        # one Spellcasting class: its own table (Paladin 3 has 3 first-level slots, not caster level 1's two)
        self.assertEqual(mc(campaign.parse_classes("Fighter 5, Paladin 3")), {"1": {"max": 3, "used": 0}})
        self.assertEqual(mc(campaign.parse_classes("Paladin 4, Sorcerer 3"))["3"]["max"], 2)   # caster level 5
        both = mc(campaign.parse_classes("Warlock 3, Sorcerer 2"))
        self.assertEqual(both["pact"], {"max": 2, "used": 0, "pact": True, "level": 2})
        self.assertEqual(both["1"]["max"], 3)

    def test_cli_set_levelup_and_pact_casting(self):
        run_cli("campaign", "new", "MC Camp")
        run_cli("campaign", "set", "sources", "PHB,TST")
        run_cli("pc", "add", "Vex", "--class", "Sorcerer", "--level", "2", "--cha", "16")
        code, out, _ = run_cli("pc", "set", "vex", "classes", "Sorcerer 2, Warlock 3")
        self.assertEqual(code, 0, out)
        self.assertIn("Sorcerer 2 / Warlock 3", out)
        self.assertIn("pact L2 2/2", out)
        code, out, _ = run_cli("pc", "levelup", "vex", "--class", "Warlock")
        self.assertIn("(Warlock 4)", out)
        pc = campaign.load().get_pc("vex")
        self.assertEqual(pc["level"], 6)
        run_cli("audit", "clear", "spell", "Still Mind", "--verdict", "CORRECT", "--notes", "ok")
        code, out, err = run_cli("pc", "cast", "vex", "Still Mind", "--pact")
        self.assertEqual(code, 0, out + err)
        self.assertIn("pact slots 1/2 left", out)


if __name__ == "__main__":
    unittest.main()
