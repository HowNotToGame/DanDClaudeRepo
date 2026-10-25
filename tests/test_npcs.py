"""NPC records and minds: validation, stat block links, compact sheets, combat, the mind's brief, the NPC agents'
gate, the stop hook for NPCs voiced without their mind, and the People view."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from pathlib import Path

from robodm import audit, campaign, costs, engines, gatekeeper, hooks, lore, npcs, world
from robodm.combat import Encounter
from robodm.rules.statblock import combat_profile, format_monster
from robodm.web import views
from tests.helpers import RoboDMTestCase
from tests.test_campaign_memory import run_cli

ROOT = Path(__file__).resolve().parent.parent

HALDA = {
    "name": "Halda Barrowe", "tier": "major", "race": "human", "alignment": "lawful neutral",
    "role": "Watch-Captain", "voice": "gravel", "backstory": "A Redcollar all her life.",
    "relationships": [{"who": "junie", "how": "keeps an eye on her", "attitude": 1},
                      {"who": "oselle", "how": "takes her coin", "hidden": True}],
    "secrets": ["She has seen the Coal."], "dm_only": ["Marda will tell her about Lorard on day 14."],
    "sheet": {"class": "Warrior 5", "ac": 18, "ac_from": "plate", "hp": 44, "hit_dice": "5d10+10", "speed": 30,
              "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 11},
              "saves": ["str", "con"], "skills": {"athletics": 1, "perception": 2}, "languages": ["Common"],
              "actions": [{"name": "Multiattack", "text": "Two longsword attacks."},
                          {"name": "Longsword", "attack": "str", "reach": 5, "damage": "1d8 slashing", "magic": 1},
                          {"name": "Crossbow", "attack": "dex", "range": "80/320", "damage": "1d8 piercing"}],
              "spellcasting": {"ability": "wis", "slots": {"1": 2}, "spells": {"0": ["light"], "1": ["bless"]}}},
}
JUNIE = {"name": "Junie", "tier": "minor", "race": "human", "alignment": "chaotic good", "role": "Ashward street girl",
         "backstory": "Nine, ragged, sharp-eyed.", "relationships": [], "statblock": "Mire Imp|TST"}


class NpcTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ROBODM_AGENTS_DIR"] = str(self.tmp / "agents")
        self.assertEqual(run_cli("campaign", "new", "Ash", "--premise", "test")[0], 0)
        self.camp = campaign.load()
        self.camp.save_pc(campaign.new_pc("Chadickith", cls="Barbarian", level=3))
        lore.add(self.camp, "Halda Barrowe", "npc", "Watch captain.\n\n## DM secrets\n- Takes bribes.", summary="Captain")

    def tearDown(self):
        os.environ.pop("ROBODM_AGENTS_DIR", None)
        super().tearDown()

    def save(self, rec):
        path, problems, _ = npcs.save(self.camp, dict(rec))
        self.assertIsNotNone(path, problems)
        return npcs.find(self.camp, rec["name"])

    def test_validation(self):
        def problems(**kw):
            return npcs.validate(self.camp, dict(JUNIE, **kw))[0]

        self.assertEqual(problems(), [])
        self.assertTrue(any("stat block" in p for p in problems(statblock=None)))
        self.assertTrue(any("always have a character sheet" in p for p in problems(tier="major")))
        self.assertTrue(any("alignment" in p for p in problems(alignment="grumpy")))
        self.assertTrue(any("no stat block 'Gelatinous Accountant'" in p for p in problems(statblock="Gelatinous Accountant|MM")))
        self.assertTrue(any("relationships must be a list" in p for p in problems(relationships=None)))
        self.assertTrue(any("missing backstory" in p for p in problems(backstory="")))
        bad = dict(HALDA, sheet=dict(HALDA["sheet"], **{"class": "Wizard 5"}))
        self.assertTrue(any("class 'Wizard'" in p for p in npcs.validate(self.camp, bad)[0]))
        self.assertEqual(npcs.validate(self.camp, dict(bad, homebrew=True))[0], [])

    def test_statblock_refs_and_links(self):
        self.assertEqual(npcs.statblock_ref("Commoner|MM")[:2], ("Commoner", "MM"))
        self.assertEqual(npcs.statblock_ref("commoner_mm")[:2], ("commoner", "MM"))
        self.assertEqual(npcs.statblock_ref("http://localhost:5050/bestiary.html#bandit%20captain_mm")[:2],
                         ("bandit captain", "MM"))
        self.assertEqual(npcs.statblock_ref("Veteran (MM)")[:2], ("Veteran", "MM"))
        self.assertEqual(npcs.statblock_ref({"ref": "Guard|MM", "hp": 16})[2], {"hp": 16})
        self.assertEqual(npcs.statblock_url("Commoner", "MM"), "http://localhost:5050/bestiary.html#commoner_mm")
        self.assertEqual(npcs.statblock_url("Bandit Captain", "MM"), "http://localhost:5050/bestiary.html#bandit%20captain_mm")

    def test_compact_sheet_numbers(self):
        rec = self.save(HALDA)
        m = npcs.compile_sheet(rec)
        prof = combat_profile(m)
        atk = {a["name"]: a for a in prof["attacks"]}
        self.assertEqual((atk["Longsword"]["to_hit"], atk["Longsword"]["damage"]), (7, [["1d8+4", "slashing"]]))   # +3 STR +3 PB +1
        self.assertEqual((atk["Crossbow"]["to_hit"], atk["Crossbow"]["kind"]), (4, "ranged"))
        self.assertEqual((prof["ac"], prof["hp"], prof["saves"]["str"], prof["saves"]["dex"]), (18, 44, 6, 1))
        self.assertEqual(m["skill"], {"athletics": "+6", "perception": "+7"})                # expertise: +1 WIS + 2×3
        self.assertEqual(m["passive"], 17)
        text = format_monster(m)
        self.assertIn("spell save {@dc 12}".replace("{@dc 12}", "DC 12"), text.replace("{@dc 12}", "DC 12"))
        shown = npcs.statblock_text(self.camp, rec)
        self.assertIn("**Level** Warrior 5 · **PB** +3", shown)
        self.assertIn("Longsword", shown)

    def test_save_check_show_and_players_view(self):
        lines = npcs.save_text(self.camp, json.dumps([JUNIE, dict(HALDA, alignment="grumpy")]))
        self.assertTrue(lines[0].startswith("✔ Junie"))
        self.assertTrue(any(line.startswith("✘ Halda Barrowe not saved") for line in lines))
        res = npcs.check(self.camp)
        self.assertIn("halda-barrowe", res["missing"])                               # the lore NPC has no record yet
        self.save(HALDA)
        res = npcs.check(self.camp)
        self.assertNotIn("halda-barrowe", res["missing"])
        self.assertIn("oselle", res["missing"])                                      # named in a relationship
        dm = npcs.show(self.camp, npcs.find(self.camp, "Halda"))
        self.assertIn("Marda will tell her", dm)
        players = views.person(self.camp, "halda-barrowe", "player")["text"]
        for secret in ("Marda will tell her", "seen the Coal", "takes her coin", "lawful neutral", "A Redcollar"):
            self.assertNotIn(secret, players)
        self.assertIn("Watch-Captain", players)
        self.save(dict(JUNIE, name="Pellot Rook", hidden=True))
        self.assertNotIn("pellot-rook", [p["slug"] for p in views.people_list(self.camp, "player")])
        self.assertIn("pellot-rook", [p["slug"] for p in views.people_list(self.camp, "dm")])
        code, out, err = run_cli("npc", "show", "Junie")
        self.assertEqual(code, 0, err)
        self.assertIn("Stat block: Mire Imp (TST)", run_cli("npc", "statblock", "Junie")[1])

    def test_cli_save_from_stdin_and_add(self):
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(JUNIE))
        try:
            code, out, err = run_cli("npc", "save", "-")
        finally:
            sys.stdin = old
        self.assertEqual(code, 0, err)
        self.assertIn("✔ Junie", out)
        code, out, err = run_cli("npc", "add", "Garford Whitlock", "--race", "human", "--alignment", "neutral",
                                 "--backstory", "A hired watcher.", "--statblock", "Bog Brute|TST", "--rel", "junie:followed her")
        self.assertEqual(code, 0, err)
        rec = npcs.find(self.camp, "Garford")
        self.assertEqual((rec["tier"], rec["relationships"][0]["who"]), ("minor", "junie"))
        self.assertEqual(run_cli("npc", "add", "Nobody", "--race", "human")[0], 1)       # no alignment, backstory, stats

    def test_combat_uses_the_record(self):
        self.save(HALDA)
        self.save(dict(JUNIE, statblock={"ref": "Bog Brute|TST", "hp": 9}))
        bank = self.camp.path("banked")
        bank.mkdir(parents=True, exist_ok=True)
        pc = campaign.new_pc("Hesby Bramble", cls="Fighter", level=4, str=16, dex=12, con=14)
        (bank / "hesby-bramble.json").write_text(json.dumps(pc), encoding="utf-8")
        self.save(dict(JUNIE, name="Hesby Bramble", tier="major", statblock=None, sheet={"file": "banked/hesby-bramble.json"}))
        run_cli("combat", "start", "Yard")
        for who in ("Halda Barrowe", "Junie", "Hesby Bramble"):
            code, out, err = run_cli("combat", "add", who, "--side", "ally")
            self.assertEqual(code, 0, err)
        cbs = {c["name"]: c for c in Encounter(campaign.load()).combatants.values()}
        self.assertEqual((cbs["Halda Barrowe"]["ac"], cbs["Halda Barrowe"]["hp"], cbs["Halda Barrowe"]["side"]), (18, 44, "ally"))
        self.assertIn("Longsword", [a["name"] for a in cbs["Halda Barrowe"]["attacks"]])
        self.assertEqual((cbs["Junie"]["hp"], cbs["Junie"]["ref"]["name"]), (9, "Junie"))      # override, own name
        self.assertEqual(cbs["Hesby Bramble"]["max_hp"], pc["hp"]["max"])

    def test_the_mind_sees_only_what_they_know(self):
        self.save(HALDA)
        self.save(JUNIE)
        run_cli("memory", "add", "Halda saw the giant's boy at the Collarhouse.", "--known-by", "halda-barrowe")
        run_cli("memory", "add", "Lorard is Pellot Rook's son.", "--type", "secret")
        b = npcs.brief(self.camp, "halda-barrowe", situation="Chadickith asks about the gate.")
        self.assertIn("She has seen the Coal.", b)                                   # her own secrets
        self.assertIn("(known to you) Halda saw the giant's boy", b)
        self.assertIn("takes her coin (nobody else knows)", b)
        self.assertIn("Watch captain.", b)                                           # public lore about her
        for hidden in ("Marda will tell her", "Takes bribes", "Pellot Rook"):
            self.assertNotIn(hidden, b)
        self.assertIn("./dm npc remember halda-barrowe", b)
        self.assertGreaterEqual(npcs.load(self.camp, "halda-barrowe") and
                                __import__("robodm.companions", fromlist=["state"]).state(self.camp, "halda-barrowe")["consults"], 1)
        npcs.remember(self.camp, "Halda", "The loxodon asked about the gate; I told him nothing.")
        self.assertIn("I told him nothing", npcs.brief(self.camp, "halda-barrowe"))

    def test_npc_agents_are_gated(self):
        def bash(cmd, agent="npc"):
            return gatekeeper.pretool({"tool_name": "Bash", "agent_type": agent, "tool_input": {"command": cmd}})

        self.assertIsNone(bash("./dm -c ash npc brief halda-barrowe --situation \"…\""))
        self.assertIsNone(bash('./dm npc remember junie "He was kind to me."', agent="npc-major"))
        self.assertIsNone(bash("./dm npc attitude junie chadickith +1 --why \"stories\""))
        self.assertIsNone(bash("./dm rules show guard"))
        for cmd in ("./dm roll 1d20", "./dm companion brief okton", "./dm npc add X --race human",
                    "./dm npc save -", "./dm combat status", "./dm npc brief junie; ./dm audit pass all"):
            r = bash(cmd)
            self.assertTrue(r and r["hookSpecificOutput"]["permissionDecision"] == "deny", cmd)
        self.assertTrue(gatekeeper.pretool({"tool_name": "Write", "agent_type": "npc", "tool_input": {"file_path": "x"}}))

    def test_stop_hook_blocks_an_npc_voiced_without_their_mind(self):
        self.save(JUNIE)
        self.camp.start_session() if hasattr(self.camp, "start_session") else None
        audit.bump_turn(campaign.load())
        camp = campaign.load()
        self.assertIsNone(hooks.npc_stop(camp, '"Junie is lying," Chadickith mutters.'))   # only mentioned in a quote
        block = hooks.npc_stop(camp, 'Junie tugs your sleeve. "They come at night," she whispers.')
        self.assertIn("`npc` (slug junie)", block)
        self.assertIsNone(hooks.npc_stop(camp, 'Junie shrugs. "Told you."'))              # once per turn
        audit.bump_turn(camp)
        npcs.brief(campaign.load(), "junie", situation="…")
        self.assertIsNone(hooks.npc_stop(campaign.load(), 'Junie grins. "Told you."'))    # consulted this turn

    def test_notes_for_people_without_records(self):
        world.scene_set(self.camp, place="Collarhouse", present=["Halda Barrowe"])
        notes = hooks.npc_notes(campaign.load())
        self.assertTrue(notes and "Halda Barrowe" in notes[0])
        self.assertEqual(hooks.npc_notes(campaign.load()), [])                        # once per session
        self.save(HALDA)
        lines, _ = world.brief_lines(campaign.load())
        self.assertTrue(any("speaks through `npc-major` (slug halda-barrowe)" in x for x in lines))

    def test_generated_agent_files(self):
        for name, effort in (("npc", "low"), ("npc-major", "medium")):
            text = npcs.agent_text(name)
            self.assertIn(f"name: {name}\n", text)
            self.assertIn("model: haiku\n", text)
            self.assertIn(f"effort: {effort}\n", text)
            self.assertIn("./dm npc brief <npc>", text)
            repo = (ROOT / ".claude" / "agents" / f"{name}.md").read_text(encoding="utf-8")
            self.assertEqual(repo, text, "regenerate with `./dm npc agents`")

    def test_npc_creator_is_a_settings_role(self):
        self.assertIn("npc-creator", engines.AGENT_ROLES)
        agents = self.tmp / "agents"
        agents.mkdir(exist_ok=True)
        shutil.copy(ROOT / ".claude" / "agents" / "npc-creator.md", agents / "npc-creator.md")
        costs.set_frontmatter(agents / "npc-creator.md", "model", "sonnet")
        engines.update({"agents": {"claude": {"npc-creator": {"model": "opus", "effort": "high"}}}})
        costs.apply_speed(campaign.load())
        text = (agents / "npc-creator.md").read_text(encoding="utf-8")
        self.assertIn("model: opus\neffort: high", text)
        self.assertNotIn("npc", [r for r in engines.AGENT_ROLES if r != "npc-creator"])   # minds aren't a setting
