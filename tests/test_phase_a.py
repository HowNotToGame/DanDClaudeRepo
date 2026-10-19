"""Phase A: turn snapshots and `./dm undo`, receipts (the narration check), and DC-before-the-roll."""

from __future__ import annotations

from robodm import audit, campaign, capture, hooks, lore, memory, receipts, snapshots
from robodm.web import views
from tests.helpers import RoboDMTestCase
from tests.test_campaign_memory import run_cli


class Base(RoboDMTestCase):
    def ok(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 0, f"{argv}: {out}{err}")
        return out

    def fails(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 1, f"{argv} should fail: {out}")
        return err

    def camp(self):
        return campaign.load()


class UndoTests(Base):
    def setUp(self):
        super().setUp()
        self.ok("campaign", "new", "Ash", "--premise", "test")
        self.ok("pc", "add", "Mira", "--class", "Wizard", "--level", "3", "--con", "14")
        self.ok("session", "start")

    def turn(self, said: str) -> None:
        """A new player message: what the prompt hook does in Claude Code."""
        hooks.prompt(self.camp(), {"prompt": said})

    def hp(self) -> int:
        return self.camp().get_pc("mira")["hp"]["current"]

    def test_undo_redo_and_what_survives(self):
        full = self.hp()
        self.turn("Mira jumps the chasm")
        self.ok("pc", "hp", "mira", "-5")
        self.ok("memory", "add", "Mira fell into the chasm")
        lore.add(self.camp(), "Brannoc Vale", "npc", "A ferryman.", summary="Ferryman")
        self.ok("audit", "pass", "all")
        self.turn("Mira drinks the strange water")
        self.ok("pc", "hp", "mira", "-3")
        self.assertEqual(self.hp(), full - 8)
        self.assertIn("“Mira drinks the strange water”  ← `./dm undo` goes here", self.ok("undo", "--list"))

        self.turn("undo that")
        out = self.ok("undo")
        self.assertIn("Rewound to the start of turn 2 (“Mira drinks the strange water”)", out)
        self.assertIn("1 rules event(s) from the undone turn(s) are marked undone", out)
        self.assertEqual(self.hp(), full - 5)
        self.assertEqual([e["status"] for e in audit.events(self.camp())], ["passed", "undone"])
        self.assertEqual(audit.open_events(self.camp()), [])                     # nothing left to audit

        out = self.ok("undo")                                                   # and one more turn back
        self.assertIn("start of turn 1", out)
        self.assertEqual(self.hp(), full)
        self.assertEqual(memory.list_memories(self.camp()), [])
        self.assertFalse(self.camp().path("lore", "npc", "brannoc-vale.md").exists())
        transcript = capture.transcript_path(self.camp()).read_text(encoding="utf-8")
        self.assertIn("Mira drinks the strange water", transcript)               # what was said stays said
        self.assertIn("↶ Rewound to the start of turn 1", transcript)
        self.assertIn("Can't go back 2 turn(s)", self.fails("undo", "2"))   # only turn 0 (the setup) is left

        self.assertIn("Redo", self.ok("undo", "--redo"))
        self.assertEqual(self.hp(), full - 5)
        self.assertTrue(self.camp().path("lore", "npc", "brannoc-vale.md").exists())

    def test_the_dm_is_told_and_the_web_offers_it(self):
        self.turn("Mira opens the box")
        self.ok("pc", "hp", "mira", "-2")
        self.assertEqual(views.state(self.camp(), "dm")["undo"]["label"], "Mira opens the box")
        self.assertIsNone(views.state(self.camp(), "player")["undo"])
        self.ok("undo")
        note = hooks.prompt(self.camp(), {"prompt": "what now?"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("rewound to the start of turn 1", note)
        self.assertIn("didn't happen", note)
        again = hooks.prompt(self.camp(), {"prompt": "and now?"})
        self.assertNotIn("rewound", str(again))                                  # told once

    def test_read_only_commands_take_no_snapshot(self):
        self.turn("look around")
        self.ok("pc", "show", "mira")
        self.ok("memory", "brief")
        turns = lambda: [s["turn"] for s in snapshots.listing(self.camp())]     # noqa: E731
        self.assertEqual(turns(), [0])                                           # setup was turn 0
        self.ok("pc", "hp", "mira", "-1")
        self.assertEqual(turns(), [0, 1])
        self.ok("pc", "hp", "mira", "-1")                                        # still one per turn
        self.assertEqual(turns(), [0, 1])


class DCTests(Base):
    def setUp(self):
        super().setUp()
        self.ok("campaign", "new", "Ash", "--premise", "test")
        self.ok("pc", "add", "Mira", "--class", "Wizard", "--level", "3", "--int", "16", "--skills", "arcana")

    def test_contests_open_checks_and_facts(self):
        self.assertIn("set the DC before the roll", self.fails("pc", "check", "mira", "arcana"))
        self.rng.push(10)
        self.assertIn("vs 14 (contest) → **success**", self.ok("pc", "check", "mira", "arcana", "--vs", "14"))
        self.rng.push(9)
        self.assertIn("tie: nothing changes", self.ok("pc", "check", "mira", "arcana", "--vs", "14"))
        self.rng.push(3)
        self.assertIn("open check: no fixed DC", self.ok("pc", "check", "mira", "arcana", "--open"))
        self.ok("pc", "check", "mira", "arcana", "--ruling", "the table wanted a quick roll")
        facts = audit.turn_facts(self.camp())
        self.assertEqual([(f["total"], f["ok"]) for f in facts[:3]], [(15, True), (14, None), (8, None)])
        self.assertIn("Mira Arcana 15 vs 14 = success", receipts.fmt(facts[0]))


class ReceiptTests(Base):
    MISS = [{"kind": "attack", "by": "Mira", "target": "Mire Imp", "hit": False, "crit": False, "total": 7, "ac": 13}]

    def test_misses_hits_deaths_and_numbers(self):
        check = receipts.check
        self.assertTrue(check("Mira's arrow hits the Mire Imp square in the chest.", self.MISS))
        self.assertEqual(check("Mira's arrow misses the Mire Imp by a hair.", self.MISS), [])
        self.assertEqual(check("The wind howls over the bog.", self.MISS), [])
        hit = [{"kind": "attack", "by": "Mira", "target": "Mire Imp", "hit": True, "total": 18, "ac": 13},
               {"kind": "damage", "target": "Mire Imp", "amount": 4, "raw": 8, "hp": 6, "max": 10}]
        self.assertTrue(check("The Mire Imp dies screaming.", hit))
        self.assertEqual(check("The Mire Imp would die if hit again.", hit), [])
        self.assertTrue(check("The Mire Imp takes 9 damage.", hit))
        self.assertEqual(check("The Mire Imp takes 8 fire damage, but it resists: 4.", hit), [])
        self.assertTrue(check("The arrow misses the Mire Imp entirely.", hit))
        dead = hit + [{"kind": "dead", "target": "Mire Imp"}]
        self.assertEqual(check("The Mire Imp dies screaming.", dead), [])
        self.assertTrue(check("The Mire Imp is down to 6 HP.", hit, hide_monster_hp=True))
        self.assertEqual(check("The Mire Imp looks bloodied.", hit, hide_monster_hp=True), [])
        alive = {"Goblin 1": {"hp": 7, "max_hp": 7, "kind": "monster", "status": "ok"}}
        self.assertTrue(check("Goblin 1 falls dead.", [], alive))

    def test_checks_and_saves(self):
        f = [{"kind": "check", "who": "Mira", "what": "Persuasion", "total": 7, "dc": 11, "ok": False}]
        self.assertTrue(receipts.check("🎲 Persuasion 17 vs DC 11 — success", f))
        self.assertTrue(receipts.check("🎲 Persuasion 7 vs DC 11 — success", f))
        self.assertEqual(receipts.check("🎲 Persuasion 7 vs DC 11 — failure", f), [])
        s = [{"kind": "save", "who": "Goblin 1", "ability": "DEX", "dc": 13, "total": 15, "ok": True}]
        self.assertTrue(receipts.check("Goblin 1 fails its DEX save and is engulfed.", s))
        self.assertEqual(receipts.check("Goblin 1 makes its DEX save and leaps clear.", s), [])


class StopHookTests(Base):
    def setUp(self):
        super().setUp()
        self.ok("campaign", "new", "Ash", "--premise", "test")
        self.ok("pc", "add", "Mira", "--class", "Wizard", "--level", "3", "--dex", "14", "--ac", "12")
        for argv in (("combat", "start", "Bog"), ("combat", "party"), ("combat", "add", "mire imp"),
                     ("combat", "map", "new", "6x6"), ("combat", "place", "mira", "A1", "mire-imp", "B1"),
                     ("combat", "init", "mira", "20"), ("combat", "init", "mire-imp", "5")):
            self.ok(*argv)
        audit.bump_turn(self.camp())

    def test_a_miss_narrated_as_a_hit_is_caught_once(self):
        self.rng.push(2)                                                          # 2 + 5 = 7 vs AC 13
        self.ok("combat", "attack", "mira", "mire-imp", "--to-hit", "5", "--damage", "1d6 piercing")
        ev = audit.events(self.camp())[-1]
        self.assertEqual(ev["facts"][0]["hit"], False)
        self.assertIn("Mechanical results (authoritative", audit.pending_packet(self.camp()))
        reply = "Mira's dagger hits the Mire Imp, and black ichor spatters the reeds."
        r = audit.hook_stop(self.camp(), {"last_assistant_message": reply})
        self.assertEqual(r["decision"], "block")
        self.assertIn("receipt check", r["reason"])
        self.assertIn("every attack on Mire Imp this turn missed", r["reason"])
        r = audit.hook_stop(self.camp(), {"last_assistant_message": "Correction: the dagger missed."})
        self.assertIn("rules audit required", r["reason"])                         # now only the audit
        self.assertNotIn("receipt check", r["reason"])
        self.assertIn("✅ The reply agrees", self.ok("audit", "receipts", "--reply", "Mira's dagger misses the Mire Imp."))
