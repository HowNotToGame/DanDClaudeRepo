import unittest

from robodm import campaign
from robodm.combat import Encounter, parse_damage_spec
from robodm.rules.index import RulesDB
from robodm.rules.statblock import combat_profile
from tests.helpers import RoboDMTestCase


class CombatTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        self.camp = campaign.create("Test Camp")
        self.camp.save_pc(campaign.new_pc("Thorin", cls="Fighter", level=3, str=16, dex=12, con=15, ac=18))
        self.camp.save_pc(campaign.new_pc("Mira", cls="Wizard", level=3, dex=14, con=13, int=17, wis=12, ac=12))
        self.db = RulesDB(self.data_dir)
        self.enc = Encounter(self.camp)
        self.enc.start("Test fight")

    def profile(self, name):
        return combat_profile(self.db.get(self.db.find_exact(name, "monster")))

    def setup_fight(self):
        self.enc.add_pc(self.camp.get_pc("Thorin"))
        self.enc.add_pc(self.camp.get_pc("Mira"))
        self.enc.add_monster(self.profile("Mire Imp"))
        self.enc.add_monster(self.profile("Bog Brute"))
        self.rng.push(10, 15, 5, 2)  # thorin 11, mira 17, imp 8, brute 1
        self.enc.roll_initiative()

    def test_pc_stats_derived(self):
        thorin = self.camp.get_pc("Thorin")
        self.assertEqual(thorin["hp"]["max"], 28)  # 10+2 + 2*(6+2)
        self.assertEqual(campaign.pc_save_bonus(thorin, "str"), 5)
        mira = self.camp.get_pc("Mira")
        self.assertEqual(mira["spell_slots"], {"1": {"max": 4, "used": 0}, "2": {"max": 2, "used": 0}})
        self.assertEqual(campaign.pc_spell_dc(mira), 13)

    def test_initiative_order_and_round_start(self):
        self.setup_fight()
        names = [self.enc.combatants[i]["name"] for i in self.enc.state["order"]]
        self.assertEqual(names, ["Mira", "Thorin", "Mire Imp", "Bog Brute"])
        self.assertEqual(self.enc.state["round"], 1)
        self.assertEqual(self.enc.current()["name"], "Mira")

    def test_next_turn_wraps_and_skips_dead(self):
        self.setup_fight()
        self.enc.damage("mire-imp", 50)
        self.enc.next_turn()  # thorin
        self.enc.next_turn()  # imp is dead -> brute
        self.assertEqual(self.enc.current()["name"], "Bog Brute")
        self.enc.next_turn()
        self.assertEqual(self.enc.state["round"], 2)
        self.assertEqual(self.enc.current()["name"], "Mira")

    def test_resistance_immunity_vulnerability(self):
        self.setup_fight()
        self.assertEqual(self.enc.damage("mire-imp", 9, "cold"), 4)       # resistant, rounds down
        self.assertEqual(self.enc.damage("mire-imp", 9, "fire"), 0)       # immune
        self.assertEqual(self.enc.damage("bog-brute", 7, "fire"), 14)     # vulnerable

    def test_resistance_rounds_once_per_type_then_vulnerability(self):
        self.setup_fight()
        self.assertEqual(self.enc.damage("mire-imp", [(3, "cold"), (3, "cold")]), 3)   # 6 cold halved once, not 1+1
        self.enc.find("bog-brute")["resist"] = ["fire"]                                 # and fire-vulnerable
        self.assertEqual(self.enc.damage("bog-brute", 7, "fire"), 6)                   # 7 → 3 → 6 (PHB p.197)

    def test_conditional_resistance(self):
        self.setup_fight()
        self.assertEqual(self.enc.damage("mire-imp", 5, "slashing"), 5)                    # magical: no resistance
        self.assertEqual(self.enc.damage("mire-imp", 4, "slashing", nonmagical=True), 2)   # nonmagical: resisted
        imp = self.enc.find("mire-imp")
        imp["hp"] = 10
        self.assertEqual(self.enc.damage("mire-imp", 4, "slashing", nonmagical=True, silvered=True), 4)

    def test_temp_hp_absorbs_first_and_does_not_stack(self):
        self.setup_fight()
        self.enc.set_temp_hp("thorin", 5)
        self.enc.set_temp_hp("thorin", 3)
        self.enc.damage("thorin", 8)
        t = self.enc.find("thorin")
        self.assertEqual((t["temp_hp"], t["hp"]), (0, 25))

    def test_pc_drops_and_death_saves(self):
        self.setup_fight()
        self.enc.damage("mira", 17)
        mira = self.enc.find("mira")
        self.assertEqual(mira["status"], "down")
        self.assertTrue(self.enc.has(mira, "unconscious"))
        self.rng.push(12)
        self.enc.death_save("mira")
        self.rng.push(1)
        self.enc.death_save("mira")
        self.assertEqual(mira["death_saves"], {"success": 1, "fail": 2})
        self.enc.damage("mira", 1)  # damage while dying = 1 failure -> dead
        self.assertEqual(mira["status"], "dead")

    def test_death_save_nat20_and_stabilize(self):
        self.setup_fight()
        self.enc.damage("mira", 17)
        self.rng.push(20)
        self.enc.death_save("mira")
        mira = self.enc.find("mira")
        self.assertEqual((mira["hp"], mira["status"]), (1, "ok"))
        self.assertFalse(self.enc.has(mira, "unconscious"))

    def test_massive_damage_kills_outright(self):
        self.setup_fight()
        self.enc.damage("mira", 17 + 17)  # 17 HP, 17 left over = max HP
        self.assertEqual(self.enc.find("mira")["status"], "dead")

    def test_heal_from_zero(self):
        self.setup_fight()
        self.enc.damage("thorin", 30)
        self.enc.heal("thorin", 5)
        t = self.enc.find("thorin")
        self.assertEqual((t["hp"], t["status"]), (5, "ok"))
        self.assertTrue(self.enc.has(t, "prone"))
        self.assertFalse(self.enc.has(t, "unconscious"))

    def test_concentration_check_and_linked_conditions(self):
        self.setup_fight()
        self.enc.set_concentration("mira", "Still Mind")
        self.enc.add_condition("bog-brute", "paralyzed", rounds=10, save="wis:13", conc="mira")
        self.rng.push(5)  # Mira CON save: 5 + 1 = 6 vs DC 10 -> fails
        self.enc.damage("mira", 6)
        self.assertIsNone(self.enc.find("mira")["concentration"])
        self.assertFalse(self.enc.has(self.enc.find("bog-brute"), "paralyzed"))

    def test_incapacitated_ends_concentration(self):
        self.setup_fight()
        self.enc.set_concentration("mira", "Still Mind")
        self.enc.add_condition("mira", "incapacitated")
        self.assertIsNone(self.enc.find("mira")["concentration"])

    def test_knocking_out_a_pc_leaves_them_stable(self):
        self.setup_fight()
        self.enc.damage("thorin", 28, nonlethal=True)
        t = self.enc.find("thorin")
        self.assertEqual((t["hp"], t["status"]), (0, "stable"))
        self.assertTrue(self.enc.has(t, "unconscious"))
        self.enc.next_turn()                     # Thorin's turn: no death save while stable
        self.assertEqual(t["death_saves"], {"success": 0, "fail": 0})

    def test_concentration_holds(self):
        self.setup_fight()
        self.enc.set_concentration("mira", "Still Mind")
        self.rng.push(15)
        self.enc.damage("mira", 4)
        self.assertEqual(self.enc.find("mira")["concentration"], "Still Mind")

    def test_condition_repeat_save_and_duration(self):
        self.setup_fight()
        self.enc.add_condition("mire-imp", "paralyzed", rounds=10, save="wis:13")
        self.enc.next_turn()           # Mira -> Thorin
        self.enc.next_turn()           # Thorin -> Imp (paralyzed, can't act)
        self.rng.push(4)               # imp's end-of-turn WIS save: 4+1 = 5 -> fail
        self.enc.next_turn()           # end of imp's turn
        imp = self.enc.find("mire-imp")
        cond = next(c for c in imp["conditions"] if c["name"] == "paralyzed")
        self.assertEqual(cond["rounds"], 9)
        self.enc.next_turn()           # brute -> round 2 Mira
        self.enc.next_turn()           # Thorin
        self.enc.next_turn()           # Imp
        self.rng.push(15)              # success -> ends
        self.enc.next_turn()
        self.assertFalse(self.enc.has(imp, "paralyzed"))

    def test_condition_immunity(self):
        self.setup_fight()
        self.enc.add_condition("mire-imp", "poisoned")
        self.assertFalse(self.enc.has(self.enc.find("mire-imp"), "poisoned"))

    def test_saves_auto_fail_and_restrained(self):
        self.setup_fight()
        self.enc.add_condition("bog-brute", "paralyzed")
        self.rng.push(5, 5)  # damage only: no save is rolled, it fails automatically
        self.enc.saving_throw(["bog-brute"], "dex", 10, damage="2d6", dtype="acid")
        self.assertEqual(self.enc.find("bog-brute")["hp"], 30)
        self.enc.remove_condition("bog-brute", "paralyzed")
        self.enc.add_condition("thorin", "restrained")
        self.rng.push(18, 3)  # disadvantage: keeps 3 -> 3+1 = 4 fails DC 10
        self.enc.saving_throw(["thorin"], "dex", 10)
        self.assertTrue(any("failure" in m for m in self.enc.messages[-2:]))

    def test_area_save_half_damage(self):
        self.setup_fight()
        self.rng.push(3, 3, 3, 3, 3, 3)   # 6d6 = 18
        self.rng.push(19)                 # brute DEX save 19-1 = 18 vs 13 -> success -> 9 x2 (vulnerable) = 18
        self.enc.saving_throw(["bog-brute"], "dex", 13, damage="6d6", dtype="fire")
        self.assertEqual(self.enc.find("bog-brute")["hp"], 40 - 18)

    def test_attack_with_stat_block_action(self):
        self.setup_fight()
        self.rng.push(12)        # 12 + 6 = 18 vs AC 18 -> hit (meets AC)
        self.rng.push(4, 5)      # 2d8+4 = 13
        self.enc.attack("bog-brute", "thorin", action="club")
        self.assertEqual(self.enc.find("thorin")["hp"], 28 - 13)

    def test_attack_nat1_misses(self):
        self.setup_fight()
        self.enc.find("thorin")["ac"] = 2
        self.rng.push(1)
        self.enc.attack("bog-brute", "thorin", action="club")
        self.assertEqual(self.enc.find("thorin")["hp"], 28)

    def test_attack_prone_and_auto_crit(self):
        self.setup_fight()
        self.enc.add_condition("bog-brute", "prone")
        self.rng.push(2, 15, 3)  # advantage (melee vs prone): keep 15; damage 1d8+3 -> 3+3
        self.enc.attack("thorin", "bog-brute", to_hit=5, damage=[("1d8+3", "slashing")])
        self.assertEqual(self.enc.find("bog-brute")["hp"], 34)
        self.enc.add_condition("bog-brute", "paralyzed")
        self.rng.push(10, 11, 2, 2)  # adv: 11+5=16 vs AC 11 hit -> auto-crit 2d8+3 = 7
        self.enc.attack("thorin", "bog-brute", to_hit=5, damage=[("1d8+3", "slashing")])
        self.assertEqual(self.enc.find("bog-brute")["hp"], 27)

    def test_prone_target_depends_on_distance_not_attack_type(self):
        self.setup_fight()
        self.enc.add_condition("bog-brute", "prone")
        self.rng.push(15, 2)         # reach attack from 10 ft: disadvantage, keep 2 -> 7 vs AC 11 misses
        self.enc.attack("thorin", "bog-brute", to_hit=5, damage=[("1d8+3", "slashing")], within_5ft=False)
        self.assertEqual(self.enc.find("bog-brute")["hp"], 40)
        self.rng.push(2, 15, 3)      # ranged from 5 ft: advantage, keep 15 -> hit for 3+3
        self.enc.attack("thorin", "bog-brute", to_hit=5, damage=[("1d8+3", "piercing")], ranged=True, within_5ft=True)
        self.assertEqual(self.enc.find("bog-brute")["hp"], 34)

    def test_pc_save_half_damage(self):
        self.setup_fight()
        self.rng.push(3, 3, 3)       # 3d6 acid = 9
        self.rng.push(20)            # Thorin CON save 20 + 4 = 24 -> success -> half (4)
        self.enc.saving_throw(["thorin"], "con", 12, damage="3d6", dtype="acid")
        self.assertEqual(self.enc.find("thorin")["hp"], 24)

    def test_recharge_rolled_at_start_of_turn(self):
        self.setup_fight()
        imp = self.enc.find("mire-imp")
        imp["recharge_spent"] = ["Bog Breath (Recharge 5–6)"]
        self.enc.next_turn()         # Thorin
        self.rng.push(4)             # imp's turn starts: d6 = 4, needs 5
        self.enc.next_turn()
        self.assertEqual(imp["recharge_spent"], ["Bog Breath (Recharge 5–6)"])
        for _ in range(3):           # brute, Mira, Thorin
            self.enc.next_turn()
        self.rng.push(5)
        self.enc.next_turn()         # imp again: recharged
        self.assertEqual(imp["recharge_spent"], [])

    def test_players_dice_mode_prompts_and_accepts_results(self):
        self.camp.settings["dice_mode"] = "players"
        self.camp.save()
        self.setup_fight()  # monsters roll; PCs wait for initiative
        self.assertEqual(self.enc.state["round"], 0)
        self.enc.set_init("thorin", 12)
        self.enc.set_init("mira", 9)
        self.assertEqual(self.enc.state["round"], 1)
        self.enc.saving_throw(["mira"], "dex", 13)
        self.assertTrue(any("ask the player" in m for m in self.enc.messages))
        self.enc.saving_throw(["mira"], "dex", 13, results={"mira": 14})
        self.assertTrue(any("player rolled 14" in m and "success" in m for m in self.enc.messages))

    def test_end_combat_persists_and_logs(self):
        self.setup_fight()
        self.enc.damage("thorin", 6)
        self.enc.damage("mire-imp", 99)
        self.enc.save()
        summary = Encounter(self.camp).end(award_xp=True)
        self.assertIn("1× Mire Imp", summary)
        self.assertIn("200 XP", summary)
        thorin = self.camp.get_pc("Thorin")
        self.assertEqual(thorin["hp"]["current"], 22)
        self.assertEqual(thorin["xp"], 100)
        self.assertIn("Combat 'Test fight' ended", self.camp.journal_path().read_text())
        self.assertFalse(Encounter(self.camp).active)

    def test_state_survives_reload(self):
        self.setup_fight()
        self.enc.damage("bog-brute", 10)
        self.enc.save()
        again = Encounter(self.camp)
        self.assertEqual(again.find("bog-brute")["hp"], 30)
        self.assertEqual(again.current()["name"], "Mira")

    def test_find_by_prefix_and_ambiguity(self):
        self.setup_fight()
        self.assertEqual(self.enc.find("bog")["name"], "Bog Brute")
        self.enc.add_monster(self.profile("Mire Imp"))
        with self.assertRaises(Exception):
            self.enc.find("mire")

    def test_parse_damage_spec(self):
        self.assertEqual(parse_damage_spec("1d8+3 slashing, 2d6 fire"), [("1d8+3", "slashing"), ("2d6", "fire")])
        self.assertEqual(parse_damage_spec("1d6 + 2 piercing plus 1d4 poison"), [("1d6+2", "piercing"), ("1d4", "poison")])


if __name__ == "__main__":
    unittest.main()
