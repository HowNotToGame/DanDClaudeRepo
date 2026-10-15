import unittest

from robodm import dice
from robodm.rules.encounter import multiplier, parse_monster_list, parse_party, rate
from tests.helpers import ScriptedRNG


class DiceTests(unittest.TestCase):
    def setUp(self):
        self.rng = ScriptedRNG()
        self._old = dice.RNG
        dice.RNG = self.rng

    def tearDown(self):
        dice.RNG = self._old

    def test_basic_expression(self):
        self.rng.push(14)
        r = dice.roll("1d20+5")
        self.assertEqual(r.total, 19)
        self.assertEqual(r.natural, 14)
        self.assertIn("**19**", r.text())

    def test_multiple_groups_and_negative_constant(self):
        self.rng.push(3, 4, 2)
        r = dice.roll("2d6 + 1d4 - 1")
        self.assertEqual(r.total, 3 + 4 + 2 - 1)
        self.assertIsNone(r.natural)

    def test_keep_and_drop(self):
        self.rng.push(1, 5, 3, 6)
        self.assertEqual(dice.roll("4d6kh3").total, 14)
        self.rng.push(1, 5, 3, 6)
        self.assertEqual(dice.roll("4d6dl1").total, 14)
        self.rng.push(9, 4)
        self.assertEqual(dice.roll("2d20kl1").total, 4)

    def test_advantage_and_disadvantage(self):
        self.rng.push(3, 17)
        r = dice.roll("1d20+2", adv=True)
        self.assertEqual(r.total, 19)
        self.assertEqual(r.natural, 17)
        self.rng.push(3, 17)
        self.assertEqual(dice.roll("1d20+2", dis=True).total, 5)
        self.rng.push(8)
        r = dice.roll("1d20", adv=True, dis=True)
        self.assertEqual(r.total, 8)
        self.assertIn("cancel", r.notes[0])

    def test_nat20_and_nat1_flags(self):
        self.rng.push(20)
        self.assertTrue(dice.roll("1d20+1").is_crit)
        self.rng.push(1)
        self.assertTrue(dice.roll("1d20+1").is_fumble)

    def test_crit_doubles_dice_not_modifiers(self):
        self.rng.push(2, 3)
        r = dice.roll("1d8+3", crit=True)
        self.assertEqual(r.total, 8)
        self.assertEqual(r.expression, "2d8+3")

    def test_crit_max_plus_roll(self):
        self.rng.push(2)
        r = dice.roll("1d8+3", crit=True, crit_rule="max_plus_roll")
        self.assertEqual(r.total, 2 + 3 + 8)

    def test_reroll_once_gwf(self):
        self.rng.push(1, 2, 6)  # first die 1 -> reroll -> 2 (kept, rerolled once only); second die 6
        r = dice.roll("2d6ro<3")
        self.assertEqual(r.total, 8)

    def test_reroll_until(self):
        self.rng.push(1, 1, 4)
        self.assertEqual(dice.roll("1d6r1").total, 4)

    def test_exploding(self):
        self.rng.push(6, 6, 2)
        self.assertEqual(dice.roll("1d6!").total, 14)

    def test_min_clamp(self):
        self.rng.push(3)
        self.assertEqual(dice.roll("1d20min10").total, 10)

    def test_percentile(self):
        self.rng.push(42)
        self.assertEqual(dice.roll("d%").total, 42)

    def test_errors(self):
        for bad in ("", "abc", "1d", "2d6 3", "1d1!", "1d6r<7", "0d6"):
            with self.assertRaises(dice.DiceError, msg=bad):
                dice.roll(bad)

    def test_average(self):
        self.assertEqual(dice.average("2d6+3"), 10)
        self.assertEqual(dice.average("1d20"), 10.5)

    def test_stats_standard_array(self):
        self.assertEqual([s["score"] for s in dice.roll_stats("standard")], [15, 14, 13, 12, 10, 8])


class EncounterMathTests(unittest.TestCase):
    def test_party_parsing(self):
        self.assertEqual(parse_party("4x3"), [3, 3, 3, 3])
        self.assertEqual(parse_party("2x5,1x4"), [5, 5, 4])
        self.assertEqual(parse_party("3,4"), [3, 4])

    def test_multipliers(self):
        self.assertEqual(multiplier(1, 4), 1)
        self.assertEqual(multiplier(2, 4), 1.5)
        self.assertEqual(multiplier(4, 4), 2)
        self.assertEqual(multiplier(15, 4), 4)
        self.assertEqual(multiplier(1, 2), 1.5)   # small party: next multiplier up
        self.assertEqual(multiplier(1, 6), 0.5)   # big party: next multiplier down

    def test_dmg_example(self):
        # four level-3 PCs vs 4 goblins (50 XP) + bugbear (200 XP): 400 base x2 = 800 adjusted -> medium
        res = rate([3, 3, 3, 3], [50, 50, 50, 50, 200])
        self.assertEqual(res["adjusted_xp"], 800)
        self.assertEqual(res["difficulty"], "medium")
        self.assertEqual(res["xp_per_pc"], 100)

    def test_monster_list(self):
        self.assertEqual(parse_monster_list("goblin x4, bugbear, 2 wolves"), [("goblin", 4), ("bugbear", 1), ("wolves", 2)])


if __name__ == "__main__":
    unittest.main()
