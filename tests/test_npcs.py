"""NPC records (race, alignment, backstory, relationships, stat block or sheet) and NPC minds (the `npc` agent)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from robodm import campaign, costs, gatekeeper, hooks, lore, npcs, world
from robodm.combat import Encounter
from tests.helpers import RoboDMTestCase
from tests.test_campaign_memory import run_cli

HALDA = {
    "name": "Halda Barrowe", "tier": "major", "race": "human", "alignment": "lawful neutral",
    "role": "Watch-Captain", "backstory": "Fifteen years on the Sky Gallows details; now she holds the gates.",
    "relationships": [{"who": "Marda Ironside", "how": "her most trusted captain"}],
    "secrets": ["She has nightmares about the anointings."],
    "dm_only": ["Her old price was a year's pay for the Watch."],
    "sheet": {"class": "Fighter 7", "ac": 17, "ac_from": "half plate", "hp": 62, "hit_dice": "7d10+21",
              "abilities": {"str": 16, "dex": 12, "con": 16, "int": 11, "wis": 14, "cha": 13},
              "saves": ["str", "con"], "skills": {"athletics": 1, "perception": 2},
              "actions": [{"name": "Multiattack", "text": "Two longsword attacks."},
                          {"name": "Longsword", "attack": "str", "reach": 5, "damage": "1d8 slashing"},
                          {"name": "Crossbow", "attack": "dex", "range": "100/400", "damage": "1d10 piercing",
                           "extra_damage": ["1d6 fire"]}],
              "spellcasting": {"ability": "wis", "slots": {"1": 2}, "spells": {"0": ["Ember Burst"], "1": ["Ember Burst"]}}},
}
BRUTE = {"name": "Gorm Halfhand", "race": "half-orc", "alignment": "chaotic neutral",
         "backstory": "A dockside bruiser.", "relationships": [], "statblock": "bog brute|tst"}


class NpcTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        self.agents = self.tmp / "agents"
        os.environ["ROBODM_AGENTS_DIR"] = str(self.agents)
        self.assertEqual(run_cli("campaign", "new", "Ash", "--premise", "A city of cinders")[0], 0)
        self.camp = campaign.load()
        self.camp.save_pc(campaign.new_pc("Chadickith", cls="Barbarian", level=3))

    def ok(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 0, f"{argv}: {out}{err}")
        return out

    def test_validation(self):
        v = lambda rec: npcs.validate(self.camp, npcs.normalize(rec))
        self.assertEqual(v(BRUTE), [])
        self.assertEqual(v(HALDA), [])
        self.assertTrue(any("race" in p for p in v({**BRUTE, "race": ""})))
        self.assertTrue(any("alignment" in p for p in v({**BRUTE, "alignment": "mostly nice"})))
        self.assertTrue(any("statblock" in p for p in v({k: x for k, x in BRUTE.items() if k != "statblock"})))
        self.assertTrue(any("isn't in the rules data" in p for p in v({**BRUTE, "statblock": "dragon tortoise|mm"})))
        self.assertTrue(any("character sheet" in p for p in v({**BRUTE, "tier": "major"})))   # majors need sheets
        dead = {k: x for k, x in BRUTE.items() if k != "statblock"} | {"status": "dead"}
        self.assertEqual(v(dead), [])                                                        # the dead need no stats
        self.assertTrue(any("damage" in p for p in v({**HALDA, "sheet": {**HALDA["sheet"], "actions": [
            {"name": "Axe", "attack": "str", "damage": "a lot"}]}})))
        self.assertEqual(npcs.normalize({**BRUTE, "relationships": None})["relationships"], [])

    def test_sheet_numbers_are_computed(self):
        m = npcs.compile_sheet(npcs.normalize(HALDA))
        self.assertEqual(m["save"], {"str": "+6", "con": "+6"})                  # +3 STR/CON, PB +3 at level 7
        self.assertEqual(m["skill"], {"athletics": "+6", "perception": "+8"})    # expertise doubles PB
        self.assertEqual(m["passive"], 18)
        sword = next(a for a in m["action"] if a["name"] == "Longsword")["entries"][0]
        self.assertIn("{@hit 6}", sword)
        self.assertIn("{@damage 1d8 + 3}", sword)
        bow = next(a for a in m["action"] if a["name"] == "Crossbow")["entries"][0]
        self.assertIn("{@atk rw} {@hit 4}", bow)
        self.assertIn("plus 3 ({@damage 1d6}) fire damage", bow)
        self.assertIn("{@dc 13}", m["spellcasting"][0]["headerEntries"][0])      # 8 + PB 3 + WIS 2
        from robodm.rules.statblock import combat_profile, format_monster

        prof = combat_profile(m)
        self.assertEqual((prof["ac"], prof["hp"]), (17, 62))
        self.assertEqual([a["to_hit"] for a in prof["attacks"]], [6, 4])
        self.assertIn("Fighter 7", format_monster(m))
        self.assertEqual(npcs.sheet_pb({"cr": "9"}), 4)

    def test_import_find_show_and_check(self):
        drafts = self.tmp / "drafts.json"
        drafts.write_text(json.dumps({"npcs": [HALDA, BRUTE]}), encoding="utf-8")
        self.assertIn("Saved 2", self.ok("npc", "import", str(drafts)))
        self.assertEqual(run_cli("npc", "import", str(drafts))[0], 1)            # already there without --overwrite
        self.ok("npc", "import", str(drafts), "--overwrite")
        self.assertEqual(npcs.find(self.camp, "halda"), "halda-barrowe")
        self.assertEqual(npcs.find(self.camp, "Gorm Halfhand"), "gorm-halfhand")
        rec = npcs.load(self.camp, "halda-barrowe")
        self.assertEqual(rec["relationships"][0]["who"], "marda-ironside")
        shown = self.ok("npc", "show", "halda")
        self.assertIn("DM only", shown)
        self.assertIn("+6 to hit", shown)
        self.assertIn("Bog Brute", self.ok("npc", "statblock", "gorm"))
        lore.add(self.camp, "Isine Kettle", "npc", "The king's cupbearer.", summary="Cupbearer")
        out = self.ok("npc", "check")
        self.assertIn("Isine Kettle", out)
        self.ok("npc", "add", "Isine Kettle", "--race", "human", "--alignment", "lawful good", "--backstory",
                "Thirty years the king's cupbearer.", "--statblock", "bog brute", "--lore", "isine-kettle",
                "--rel", "halda-barrowe: protects her")
        self.assertIn("Every named character has a complete record", self.ok("npc", "check"))
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps([{**BRUTE, "name": "Teo Varrow"}, {**BRUTE, "name": "Tess", "race": ""}]), encoding="utf-8")
        code, out, err = run_cli("npc", "import", str(bad))
        self.assertEqual(code, 1)
        self.assertIn("Saved 1: Teo Varrow", out + err)                          # the good one still goes in

    def test_the_mind_sees_only_what_the_npc_knows(self):
        npcs.save(self.camp, HALDA)
        run_cli("memory", "add", "Halda sealed the gates at midnight.", "--known-by", "halda-barrowe")
        run_cli("memory", "add", "The Choir meets in the crypt.")
        self.ok("npc", "remember", "halda", "The loxodon kept his word about the brothers.")
        self.ok("npc", "attitude", "halda", "Chadickith", "+2", "--why", "kept his word")
        b = self.ok("npc", "brief", "halda", "--situation", "A loxodon asks her to open the gate.")
        self.assertIn("nightmares about the anointings", b)                      # her own secret
        self.assertNotIn("year's pay", b)                                         # dm_only stays hidden
        self.assertIn("sealed the gates at midnight", b)                          # known_by her
        self.assertNotIn("crypt", b)                                              # not known to her
        self.assertIn("kept his word about the brothers", b)
        self.assertIn("Chadickith: attitude +2", b)
        self.assertIn("open the gate", b)
        self.assertEqual(npcs.load(self.camp, "halda-barrowe")["mind"]["consults"], 1)
        self.assertIn("kept his word", self.ok("npc", "recall", "halda", "loxodon word"))
        # a companion's mind is its companion persona, not an NPC record
        self.ok("companion", "new", "Okton", "--status", "party")
        npcs.save(self.camp, {**BRUTE, "name": "Okton"})
        b = self.ok("npc", "brief", "okton")
        self.assertIn("# You are Okton", b)
        self.assertIn("FRICTION", b)                                              # the companion brief's answer shape

    def test_combat_from_statblock_sheet_and_sheet_file(self):
        npcs.save(self.camp, HALDA)
        npcs.save(self.camp, BRUTE)
        banked = self.camp.path("banked", "hesby.json")
        banked.parent.mkdir(parents=True, exist_ok=True)
        sheet = campaign.new_pc("Hesby Bramble", cls="Fighter", level=4)
        sheet["hp"] = {"max": 45, "current": 45, "temp": 0}
        banked.write_text(json.dumps(sheet), encoding="utf-8")
        npcs.save(self.camp, {**BRUTE, "name": "Hesby Bramble", "tier": "major", "sheet": {"file": "banked/hesby.json"},
                              "statblock": None})
        self.ok("combat", "start", "Brawl")
        self.ok("combat", "add", "Halda Barrowe", "--side", "ally")
        self.ok("combat", "add", "Gorm Halfhand")
        self.ok("combat", "add", "Hesby Bramble", "--side", "ally")
        enc = Encounter(campaign.load())
        got = {c["name"]: c for c in enc.combatants.values()}
        self.assertEqual((got["Halda Barrowe"]["hp"], got["Halda Barrowe"]["ac"]), (62, 17))
        self.assertEqual(got["Gorm Halfhand"]["hp"], 40)                          # Bog Brute's average
        self.assertEqual((got["Hesby Bramble"]["hp"], got["Hesby Bramble"]["kind"]), (45, "npc"))
        self.assertFalse(got["Hesby Bramble"].get("pc"))                          # never synced to a party sheet

    def test_npc_agent_gate_and_fixed_model(self):
        def bash(cmd, agent="npc"):
            r = gatekeeper.pretool({"tool_name": "Bash", "agent_type": agent, "tool_input": {"command": cmd}})
            return r is None

        self.assertTrue(bash("./dm npc brief halda-barrowe"))
        self.assertTrue(bash('./dm -c ash npc remember halda-barrowe "The giant smelled of iron."'))
        self.assertTrue(bash("./dm combat status --players"))
        self.assertFalse(bash("./dm npc show halda-barrowe"))                    # the full record has DM-only notes
        self.assertFalse(bash("./dm companion brief okton"))
        self.assertFalse(bash("./dm roll 1d20"))
        self.assertFalse(bash("./dm npc brief halda-barrowe", agent="companion-okton"))
        text = npcs.agent_text()
        self.assertIn("model: haiku\neffort: medium\n", text)
        repo = Path(__file__).resolve().parent.parent / ".claude" / "agents" / "npc.md"
        self.assertEqual(repo.read_text(encoding="utf-8"), text, "regenerate with `./dm npc agent`")
        # the table's turn speed and ⚙ choices never touch the npc agent
        self.agents.mkdir(parents=True, exist_ok=True)
        (self.agents / "npc.md").write_text(text, encoding="utf-8")
        self.ok("campaign", "set", "turn_speed", "thorough")
        self.assertEqual((self.agents / "npc.md").read_text(encoding="utf-8"), text)

    def test_scene_lines_and_missing_record_note(self):
        npcs.save(self.camp, HALDA)
        lore.add(self.camp, "Teo Varrow", "npc", "A ferryman.", summary="Ferryman")
        self.ok("session", "start")
        world.scene_set(self.camp, place="the Saltgate", present=["Halda Barrowe", "Teo Varrow"])
        scene, _ = world.brief_lines(campaign.load(), dm=True)
        text = "\n".join(scene)
        self.assertIn("`npc` agent (halda-barrowe)", text)
        self.assertIn("Teo Varrow", text)
        self.assertIn("no NPC record yet", text)
        notes = hooks.npc_notes(campaign.load())
        self.assertTrue(notes and "Teo Varrow" in notes[0])
        self.assertEqual(hooks.npc_notes(campaign.load()), [])                  # once per session
        player_scene, _ = world.brief_lines(campaign.load(), dm=False)
        self.assertNotIn("npc", "\n".join(player_scene).replace("Varrow", ""))

    def test_npc_creator_is_a_table_setting_but_npc_minds_are_not(self):
        from robodm import engines

        engines.update({"agents": {"claude": {"npc-creator": {"model": "haiku"}}}})
        self.assertRaises(Exception, engines.update, {"agents": {"claude": {"npc": {"model": "opus"}}}})
        self.assertRaises(Exception, engines.update, {"agents": {"ollama": {"npc-creator": {"model": "x"}}}})
        self.agents.mkdir(parents=True, exist_ok=True)
        repo = Path(__file__).resolve().parent.parent / ".claude" / "agents" / "npc-creator.md"
        (self.agents / "npc-creator.md").write_text(repo.read_text(encoding="utf-8"), encoding="utf-8")
        costs.apply_speed(self.camp)
        self.assertIn("model: haiku", (self.agents / "npc-creator.md").read_text(encoding="utf-8"))
