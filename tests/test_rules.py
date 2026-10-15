import unittest

from robodm.rules.format import format_item, format_record, format_spell
from robodm.rules.index import RulesDB
from robodm.rules.render import render_entries, strip_tags
from robodm.rules.statblock import combat_profile, format_monster, parse_attacks
from tests.helpers import RoboDMTestCase


class RenderTests(unittest.TestCase):
    def test_tags(self):
        cases = {
            "{@spell fireball}": "fireball",
            "{@spell fireball|phb|Fireball!}": "Fireball!",
            "{@damage 2d6 + 3}": "2d6 + 3",
            "{@hit 4}": "+4",
            "{@hit -1}": "-1",
            "{@dc 15}": "DC 15",
            "{@atk mw,rw}": "Melee or Ranged Weapon Attack:",
            "{@h}": "Hit: ",
            "{@recharge 5}": "(Recharge 5–6)",
            "{@recharge}": "(Recharge 6)",
            "{@scaledamage 8d6|3-9|1d6}": "1d6",
            "{@quickref Vision and Light|PHB|2||heavily obscured}": "heavily obscured",
            "{@b {@spell shield}}": "shield",
            "{@chance 25}": "25 percent",
        }
        for raw, expected in cases.items():
            self.assertEqual(strip_tags(raw), expected, raw)

    def test_entries_list_and_table(self):
        text = render_entries([
            {"type": "entries", "name": "Trick", "entries": ["Do a thing."]},
            {"type": "list", "items": ["one", "two"]},
            {"type": "table", "colLabels": ["d4", "Effect"], "rows": [[{"type": "cell", "roll": {"min": 1, "max": 2}}, "Boom"]]},
        ], depth=1)
        self.assertIn("**Trick.** Do a thing.", text)
        self.assertIn("- one", text)
        self.assertIn("| 1–2 | Boom |", text)


class RulesDBTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        self.db = RulesDB(self.data_dir)

    def test_index_types(self):
        types = {r["t"] for r in self.db.index}
        for t in ("spell", "monster", "class", "classfeature", "subclass", "condition", "item", "quickref"):
            self.assertIn(t, types)
        spell = self.db.find_exact("Ember Burst", "spell")
        self.assertEqual(spell["cls"], ["Wizard"])
        self.assertEqual(spell["lvl"], 3)

    def test_index_is_cached(self):
        _ = self.db.index
        self.assertTrue((self.data_dir / "_index.json").exists())
        again = RulesDB(self.data_dir)
        self.assertEqual(len(again.index), len(self.db.index))

    def test_lookup_exact_fuzzy_and_missing(self):
        rec, _ = self.db.lookup("mire imp")
        self.assertEqual(rec["n"], "Mire Imp")
        rec, _ = self.db.lookup("Embr Burst")  # typo
        self.assertEqual(rec["n"], "Ember Burst")
        rec, alts = self.db.lookup("zzzz nothing")
        self.assertIsNone(rec)

    def test_search_ranks_exact_first(self):
        hits = self.db.search("mire imp")
        self.assertEqual(hits[0]["n"], "Mire Imp")
        self.assertIn("Mire Imp Elder", [h["n"] for h in hits])

    def test_fulltext(self):
        hits = self.db.fulltext("pinch of ash", "spell")
        self.assertEqual(hits, [])  # components aren't entry text
        hits = self.db.fulltext("half as much", "spell")
        self.assertEqual(hits[0]["n"], "Ember Burst")

    def test_spell_format(self):
        rec = self.db.find_exact("Ember Burst", "spell")
        text = format_spell(self.db.get(rec), rec.get("cls"))
        self.assertIn("3rd-level evocation", text)
        self.assertIn("M (a pinch of ash)", text)
        self.assertIn("6d6 fire damage", text)
        self.assertIn("Classes** Wizard", text)
        conc = format_spell(self.db.get(self.db.find_exact("Still Mind", "spell")))
        self.assertIn("Concentration, up to 1 minute", conc)

    def test_monster_format_and_profile(self):
        m = self.db.get(self.db.find_exact("Mire Imp", "monster"))
        text = format_monster(m)
        self.assertIn("Small fiend (devil), lawful evil", text)
        self.assertIn("**CR** 1 (200 XP)", text)
        p = combat_profile(m)
        self.assertEqual(p["ac"], 13)
        self.assertEqual(p["hp"], 10)
        self.assertEqual(p["saves"]["dex"], 5)
        self.assertEqual(p["saves"]["con"], 1)
        self.assertEqual(p["init_bonus"], 3)
        self.assertEqual(p["resist"], ["cold"])
        self.assertEqual(p["immune"], ["fire", "poison"])
        self.assertEqual(p["cond_defenses"][0]["types"], ["bludgeoning", "piercing", "slashing"])
        self.assertEqual(p["condition_immune"], ["poisoned"])

    def test_parse_attacks(self):
        m = self.db.get(self.db.find_exact("Mire Imp", "monster"))
        attacks = {a["name"]: a for a in parse_attacks(m)}
        self.assertEqual(attacks["Sting"]["to_hit"], 5)
        self.assertEqual(attacks["Sting"]["damage"], [["1d6+3", "piercing"], ["2d6", "poison"]])
        breath = attacks["Bog Breath (Recharge 5–6)"]
        self.assertEqual(breath["save_dc"], 12)
        self.assertEqual(breath["save_ability"], "con")
        self.assertTrue(breath["half_on_save"])
        self.assertEqual(breath["recharge"], 5)

    def test_copy_resolution(self):
        rec = self.db.find_exact("Mire Imp Elder", "monster")
        m = self.db.get(rec)
        self.assertEqual(m["hp"]["average"], 30)
        self.assertEqual(m["ac"][0]["ac"], 13)  # inherited
        self.assertEqual(m["action"][-1]["name"], "Curse")
        self.assertEqual(m["cr"], "3")

    def test_items_and_quickref_and_class(self):
        item = self.db.get(self.db.find_exact("Mudsword", "item"))
        text = format_item(item)
        self.assertIn("1d8 slashing (versatile 1d10)", text)
        self.assertIn("15 gp", text)
        qr = format_record(self.db, self.db.find_exact("Cover", "quickref"))
        self.assertIn("Half cover gives +2", qr)
        cls = format_record(self.db, self.db.find_exact("Warrior", "class"))
        self.assertIn("Hit Die** d10", cls)
        self.assertIn("2: Surge", cls)
        self.assertIn("Champion of Mud", cls)


if __name__ == "__main__":
    unittest.main()
