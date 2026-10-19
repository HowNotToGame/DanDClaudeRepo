"""Companions: personas, knowledge-limited briefs, state, agent files, the companion gate and the hooks."""

from __future__ import annotations

import os
from pathlib import Path

from robodm import audit, campaign, companions as co, gatekeeper, hooks, lore, memory
from tests.helpers import RoboDMTestCase
from tests.test_campaign_memory import run_cli

OKTON = """A fire giant smith who raised the boy.

## What people see
- Calls Chadickith "Small Tusk".

## DM secrets
- He forged the crown and never told anyone.
- He doesn't know the queen poisoned the well."""


class CompanionTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ROBODM_AGENTS_DIR"] = str(self.tmp / "agents")
        self.assertEqual(run_cli("campaign", "new", "Ash", "--premise", "test")[0], 0)
        self.camp = campaign.load()
        self.camp.save_pc(campaign.new_pc("Chadickith", cls="Barbarian", level=3))
        lore.add(self.camp, "Okton", "npc", OKTON, summary="Ancient fire giant smith")
        lore.add(self.camp, "Halda", "npc", "Watch captain.\n\n## DM secrets\n- Halda takes bribes from Oselle.",
                 summary="Captain of the Watch")

    def tearDown(self):
        os.environ.pop("ROBODM_AGENTS_DIR", None)
        super().tearDown()

    def ok(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 0, f"{argv}: {out}{err}")
        return out

    def make_okton(self):
        self.ok("companion", "new", "Okton", "--from-lore", "okton", "--alignment", "lawful neutral", "--status", "party")
        meta, sections = co.load_persona(self.camp, "okton")
        sections["Secrets"] = "- You forged the Crown of Ash."
        sections["Drives"] = "- Kill the Gallowsbird."
        co.save_persona(self.camp, "okton", meta, sections)

    def test_new_from_lore_keeps_dm_secrets_out_of_the_brief(self):
        self.make_okton()
        meta, sections = co.load_persona(self.camp, "okton")
        self.assertTrue(meta["draft"])
        self.assertIn("Small Tusk", sections["Core"])
        self.assertIn("queen poisoned the well", sections["DM only"])
        b = co.brief(self.camp, "okton")
        self.assertIn("# You are Okton", b)
        self.assertIn("You forged the Crown of Ash", b)                 # his own secret: he knows it
        self.assertIn("Kill the Gallowsbird", b)
        self.assertNotIn("queen poisoned", b)                           # DM-only stays hidden
        self.assertNotIn("bribes", b)                                   # other NPCs' secrets too
        self.assertIn("Captain of the Watch", b)                        # …but their public face is known
        self.assertIn("code (a craft", b)                               # lawful neutral guidance
        self.assertNotIn("(fill in", b)                                 # unfinished sections aren't shown

    def test_knowledge_is_limited_to_what_the_character_could_know(self):
        self.make_okton()
        memory.add(self.camp, "The Choir meets in the crypt at midnight", "secret", known_by=["Okton"])
        memory.add(self.camp, "The steward is a spy for Oselle", "secret")
        self.ok("companion", "remember", "okton", "Small Tusk ignored my warning about the crown & I let it go")
        b = co.brief(self.camp, "okton")
        self.assertIn("Choir meets in the crypt", b)
        self.assertIn("ignored my warning", b)
        self.assertNotIn("steward is a spy", b)
        self.assertIn("crypt", self.ok("companion", "recall", "okton", "choir crypt"))
        self.assertIn("Nothing you know", self.ok("companion", "recall", "okton", "steward spy"))
        self.assertNotIn("bribes", self.ok("companion", "recall", "okton", "Halda bribes Oselle"))

    def test_status_agenda_attitude_and_the_dm_brief(self):
        self.make_okton()
        self.ok("companion", "agenda", "okton", "add", "Keep the Heartcoal out of everyone's hands")
        self.ok("companion", "attitude", "okton", "chadickith", "3", "--why", "raised him")
        self.ok("companion", "attitude", "okton", "chadickith", "+1")                      # capped at +3
        st = co.state(self.camp, "okton")
        self.assertEqual(st["attitude"]["chadickith"]["score"], 3)
        dm = memory.brief(self.camp)
        self.assertIn("Companions in play", dm)
        self.assertIn("**Okton** (party; Chadickith +3; 1 agenda item(s)) — consult `companion`", dm)
        self.assertIn("[a1] Keep the Heartcoal", co.brief(self.camp, "okton"))
        self.ok("companion", "leave", "okton")
        self.assertNotIn("Companions in play", memory.brief(self.camp))
        self.assertIn("you're not in this scene", co.brief(self.camp, "okton"))
        self.ok("companion", "agenda", "okton", "done", "a1")
        self.assertNotIn("[a1]", co.brief(self.camp, "okton"))

    def test_table_notes_and_council(self):
        self.make_okton()
        self.ok("companion", "note", "okton", "Okton never raises his voice")
        self.assertIn("Okton never raises his voice", co.brief(self.camp, "okton"))
        self.assertIn("Table notes", co.brief(self.camp, "okton"))
        out = self.ok("companion", "council", "Do we raid the crypt tonight?")
        self.assertIn("in parallel", out)
        self.assertIn("`companion`", out)
        self.assertIn("raid the crypt", out)

    def test_agent_files(self):
        self.make_okton()
        self.ok("companion", "agent", "okton", "--model", "haiku")
        text = (self.tmp / "agents" / "companion-okton.md").read_text(encoding="utf-8")
        self.assertIn("name: companion-okton", text)
        self.assertIn("model: haiku", text)
        self.assertIn("tools: Bash", text)
        self.assertIn("./dm -c ash companion brief okton", text)
        self.assertIn("campaign: ash", text)
        self.assertEqual(co.agent_name(self.camp, "okton"), "companion-okton")
        self.ok("companion", "agent", "--generic")
        self.assertIn("name: companion", (self.tmp / "agents" / "companion.md").read_text(encoding="utf-8"))

    def test_repo_generic_agent_is_current(self):
        repo = Path(__file__).resolve().parent.parent / ".claude" / "agents" / "companion.md"
        self.assertEqual(repo.read_text(encoding="utf-8"), co.generic_agent_text(),
                         "regenerate with `./dm companion agent --generic`")

    def test_companion_gate(self):
        def bash(cmd, agent="companion-okton"):
            return gatekeeper.pretool({"tool_name": "Bash", "agent_type": agent, "tool_input": {"command": cmd}})

        deny = lambda r: bool(r) and r["hookSpecificOutput"]["permissionDecision"] == "deny"
        self.assertIsNone(bash("./dm -c ash companion brief okton --combat"))
        self.assertIsNone(bash('./dm companion remember okton "Small Tusk & I argued; he won"'))
        self.assertIsNone(bash("./dm rules show fireball"))
        self.assertIsNone(bash("./dm combat status --players"))
        self.assertIsNone(bash("./dm companion brief halda", agent="companion"))      # generic agent: any NPC
        self.assertTrue(deny(bash("./dm companion brief halda")))                     # but not someone else's records
        self.assertTrue(deny(bash("./dm combat status")))                             # sees the fight as a character
        self.assertTrue(deny(bash("./dm combat attack okton roc --action greatsword")))
        self.assertTrue(deny(bash("./dm roll 1d20")))
        self.assertTrue(deny(bash("./dm companion brief okton; cat campaigns/ash/memory.jsonl")))
        self.assertTrue(deny(bash("cat campaigns/ash/lore/npc/halda.md")))
        self.assertTrue(deny(gatekeeper.pretool({"tool_name": "Write", "agent_type": "companion-okton",
                                                 "tool_input": {"file_path": "x.md"}})))
        self.assertIsNone(gatekeeper.pretool({"tool_name": "Bash", "tool_input": {"command": "./dm roll 1d20"}}))

    def test_hooks_interjections_and_unconsulted_voices(self):
        self.make_okton()
        self.camp.start_session()
        self.ok("companion", "interject", "okton", "the crown is warm again")
        ctx = hooks.prompt(campaign.load(), {"prompt": "I head to the forge"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Okton wants to speak up about: the crown is warm again", ctx)
        co.brief(campaign.load(), "okton")                                            # consulted → heard
        self.assertIsNone(co.state(self.camp, "okton")["interject"])
        audit.bump_turn(campaign.load())
        hooks.stop(campaign.load(), {"last_assistant_message": 'Okton folds his arms. "No," he rumbles.'})
        ctx = hooks.prompt(campaign.load(), {"prompt": "why not?"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("voiced Okton without consulting `companion`", ctx)
        run_cli("campaign", "set", "companion_agents", "always")
        r = hooks.stop(campaign.load(), {"last_assistant_message": 'Okton: "Put it down, Small Tusk."'})
        self.assertEqual(r["decision"], "block")
        self.assertIn("companion_agents=always", r["reason"])
        co.brief(campaign.load(), "okton")
        self.assertIsNone(hooks.stop(campaign.load(), {"last_assistant_message": 'Okton: "Fine."'}))

    def test_dm_recall_finds_companion_records(self):
        self.make_okton()
        self.ok("companion", "remember", "okton", "I tasted the guard's new gorget and spat")
        hits = run_cli("recall", "gorget", "--scope", "companions")[1]
        self.assertIn("okton remembers", hits)
