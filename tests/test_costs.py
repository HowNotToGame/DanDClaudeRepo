"""Phase E: turn speed (agent models, post-audit scope, brief size), usage tracking, and the playtest scorecard."""

from __future__ import annotations

import os
import queue
import shutil
import sys
from pathlib import Path

from robodm import audit, campaign, costs, memory, playtest
from robodm.web import bridge as bridge_mod, views
from tests.helpers import RoboDMTestCase
from tests.test_campaign_memory import run_cli

ROOT = Path(__file__).resolve().parent.parent

FAKE_DM = r'''
import json, sys
prompt = sys.stdin.read()
def out(o): print(json.dumps(o), flush=True)
out({"type": "system", "subtype": "init", "session_id": "pt-1"})
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "./dm memory brief"}}]}})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Rain hammers the gate of Brindle Keep. What do you do?"}]}})
out({"type": "result", "total_cost_usd": 0.05, "duration_ms": 1200, "num_turns": 2})
'''


class SpeedTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        agents = self.tmp / "agents"
        agents.mkdir()
        for name in ("rules-lawyer.md", "lore-keeper.md"):
            shutil.copy(ROOT / ".claude" / "agents" / name, agents / name)
        os.environ["ROBODM_AGENTS_DIR"] = str(agents)
        run_cli("campaign", "new", "Speed", "--premise", "test " * 400)
        run_cli("pc", "add", "Mira", "--class", "Wizard", "--level", "3", "--int", "16", "--skills", "arcana")

    def tearDown(self):
        os.environ.pop("ROBODM_AGENTS_DIR", None)
        super().tearDown()

    def ok(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 0, f"{argv}: {out}{err}")
        return out

    def test_turn_speed_sets_models_and_brief_size(self):
        agents = Path(os.environ["ROBODM_AGENTS_DIR"])
        out = self.ok("campaign", "set", "turn_speed", "fast")
        self.assertIn("rules-lawyer → haiku", out)
        self.assertIn("model: haiku", (agents / "rules-lawyer.md").read_text(encoding="utf-8"))
        self.assertIn("model: sonnet", (agents / "lore-keeper.md").read_text(encoding="utf-8"))   # added where missing
        self.assertLessEqual(len(memory.brief(campaign.load())), costs.BRIEF_BUDGETS["fast"] + 400)
        self.ok("campaign", "set", "turn_speed", "thorough")
        self.assertIn("model: opus", (agents / "rules-lawyer.md").read_text(encoding="utf-8"))
        self.assertEqual(costs.brief_budget(campaign.load()), 14000)
        self.assertEqual(run_cli("campaign", "set", "turn_speed", "ludicrous")[0], 1)
        self.ok("campaign", "set", "dm_model", "sonnet")
        self.assertEqual(run_cli("campaign", "set", "dm_model", "gpt-9")[0], 1)
        self.ok("campaign", "set", "dm_model", "default")
        self.assertEqual(campaign.load().settings["dm_model"], "")

    def test_fast_mode_auto_passes_routine_events_only(self):
        self.ok("campaign", "set", "turn_speed", "fast")
        audit.bump_turn(campaign.load())
        self.rng.push(12)
        self.ok("pc", "check", "mira", "arcana", "--dc", "10")
        self.ok("pc", "hp", "mira", "-3")
        run_cli("audit", "clear", "ruling", "runes", "--verdict", "CORRECT", "--notes", "fine")
        self.ok("ruling", "Mira may read the runes while running", "--key", "runes")
        reply = "🎲 Arcana 17 vs DC 10 — success. The runes make sense to Mira."
        r = audit.hook_stop(campaign.load(), {"last_assistant_message": reply})
        statuses = {e["cmd"].split()[2]: e["status"] for e in audit.events(campaign.load())}
        self.assertEqual(statuses["check"], "passed")
        self.assertEqual(statuses["hp"], "passed")
        self.assertEqual(r["decision"], "block")                          # the ruling still needs the rules-lawyer
        self.assertEqual([e["cmd"].split()[1] for e in audit.open_events(campaign.load())], ["ruling"])

    def test_balanced_mode_audits_everything(self):
        audit.bump_turn(campaign.load())
        self.ok("pc", "hp", "mira", "-3")
        r = audit.hook_stop(campaign.load(), {"last_assistant_message": "Mira winces."})
        self.assertEqual(r["decision"], "block")
        self.assertEqual(len(audit.open_events(campaign.load())), 1)

    def test_usage_is_recorded_for_the_host(self):
        br = bridge_mod.Bridge(lambda ev: None)
        br.handle({"type": "result", "total_cost_usd": 0.04, "duration_ms": 3000, "num_turns": 3}, "speed", [], set())
        br.handle({"type": "result", "total_cost_usd": 0.06, "duration_ms": 5000, "num_turns": 4}, "speed", [], set())
        u = costs.usage_summary(campaign.load())
        self.assertEqual((u["turns"], u["cost"], u["avg_seconds"]), (2, 0.1, 4.0))
        self.assertEqual(views.state(campaign.load(), "dm")["usage"]["turns"], 2)
        self.assertIsNone(views.state(campaign.load(), "player", seat="mira")["usage"])     # players don't see cost
        self.assertIn("Web DM this session: 2 turn(s), ≈$0.10", self.ok("status"))


class PlaytestTests(RoboDMTestCase):
    def test_the_harness_scores_a_run(self):
        script = self.tmp / "fake_dm.py"
        script.write_text(FAKE_DM, encoding="utf-8")
        os.environ["ROBODM_CLAUDE"] = f'"{sys.executable}" "{script}"'.replace("\\", "/")
        try:
            steps = [{"text": "Where are we?", "expect": ["brief"]},
                     {"as": "Brann", "text": "I force the gate.", "expect": ["roll"]},
                     {"as": "Tamsin", "private": True, "text": "I pocket the silver locket quietly.", "expect": ["private"]}]
            out = self.tmp / "card.md"
            home = os.environ["ROBODM_HOME"]
            res = playtest.run(script=steps, out=str(out), log=lambda *_: None)
            self.assertEqual(os.environ["ROBODM_HOME"], home)            # restored afterwards
        finally:
            os.environ.pop("ROBODM_CLAUDE", None)
        checks = {(i, n): ok for i, r in enumerate(res["results"], 1) for n, ok, _ in r["checks"]}
        self.assertTrue(checks[(1, "brief")])
        self.assertFalse(checks[(2, "dice")])                            # the fake DM never rolls
        self.assertTrue(checks[(1, "audit")])
        self.assertTrue(all(ok for n, ok, _ in res["overall"]))          # nothing leaked
        card = out.read_text(encoding="utf-8")
        self.assertIn(f"{res['passed']}/{res['total']} checks passed", card)
        self.assertIn("❌ dice", card)
        self.assertIn("≈$0.15", card)
        self.assertFalse(Path(res["results"] and res.get("scratch") or self.tmp / "nope").exists())
