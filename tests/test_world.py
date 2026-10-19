"""The living world: time and calendar, timers, rests, clocks, factions, scenes and witnesses, the off-screen tick
and knowledge propagation."""

from __future__ import annotations

import json
import os

from robodm import campaign, lore, memory, world
from tests.helpers import RoboDMTestCase, write
from tests.test_campaign_memory import run_cli


class WorldTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ROBODM_AGENTS_DIR"] = str(self.tmp / "agents")
        p = self.data_dir / "spells/spells-tst.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["spell"].append(
            {"name": "Long Ward", "source": "TST", "level": 1, "school": "A", "time": [{"number": 1, "unit": "action"}],
             "range": {"type": "point", "distance": {"type": "touch"}}, "components": {"v": True},
             "duration": [{"type": "timed", "duration": {"type": "hour", "amount": 8}}], "entries": ["A ward."]})
        write(p, data)
        from robodm.rules import index

        index.default_db.cache_clear()
        self.ok("campaign", "new", "Ash", "--premise", "test")
        run_cli("campaign", "set", "sources", "PHB,MM,TST")
        self.ok("pc", "add", "Mira", "--class", "Wizard", "--level", "3", "--int", "17", "--con", "12")
        self.camp = campaign.load()
        lore.add(self.camp, "Halda", "npc", "Watch captain.", summary="Captain of the Watch", related="oselle-velun")
        lore.add(self.camp, "Oselle Velun", "npc", "A merchant.", summary="Grain merchant", related="house-mostana")
        lore.add(self.camp, "House Mostana", "faction", "Traders.", summary="Calishite trading house")

    def tearDown(self):
        os.environ.pop("ROBODM_AGENTS_DIR", None)
        super().tearDown()

    def ok(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 0, f"{argv}: {out}{err}")
        return out

    def fails(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 1, f"{argv} should fail: {out}")
        return err

    def w(self) -> dict:
        return world.load(campaign.load())

    # ------------------------------------------------------------------ time
    def test_durations_points_and_the_calendar(self):
        t = 10 * world.DAY + 18 * 60                       # day 11, 18:00
        self.assertEqual(world.parse_duration("3h"), 180)
        self.assertEqual(world.parse_duration("1d 6h"), world.DAY + 360)
        self.assertEqual(world.parse_duration("2 weeks"), 2 * world.WEEK)
        self.assertEqual(world.parse_duration("10 rounds"), 1)
        self.assertEqual(world.parse_duration("until dawn", t), 12 * 60)      # 06:00 next day
        self.assertEqual(world.parse_duration("until day 12 06:00", t), 12 * 60)
        self.assertEqual(world.parse_point("noon", t), 11 * world.DAY + 12 * 60)
        self.assertEqual(world.parse_point("tomorrow dawn", t), 11 * world.DAY + 6 * 60)
        with self.assertRaises(Exception):
            world.parse_duration("soonish")
        self.assertEqual(world.time_word(t), "evening")
        self.assertEqual(world.harptos_date(1492, world.harptos_index("Tarsakh 30", 1492), 2), "Greengrass, 1492 DR")
        self.assertEqual(world.harptos_date(1492, world.harptos_index("Flamerule 30", 1492), 3), "Shieldmeet, 1492 DR")
        self.assertEqual(world.harptos_date(1493, world.harptos_index("Flamerule 30", 1493), 3), "Eleasis 1, 1493 DR")
        self.assertEqual(world.harptos_date(1492, world.harptos_index("Nightal 30", 1492), 2), "Hammer 1, 1493 DR")
        self.assertEqual(len(world._harptos_year(1492)), 366)
        self.assertEqual(len(world._harptos_year(1493)), 365)

    def test_set_and_advance(self):
        self.assertIn("isn't tracked", self.ok("time"))
        self.assertIn("doesn't track in-game time", self.fails("time", "advance", "1h"))
        out = self.ok("time", "set", "11", "18:00", "--calendar", "harptos", "--date", "Tarsakh 3", "--year", "1492")
        self.assertIn("Day 11 · 18:00 (evening) · Tarsakh 3, 1492 DR", out)
        self.assertIn("Day 12 · 06:00 (dawn) · Tarsakh 4, 1492 DR", self.ok("time", "advance", "until", "dawn"))
        self.assertIn("Tarsakh 4", self.ok("time"))

    def test_combat_time(self):
        self.ok("time", "set", "1", "08:00")
        self.ok("combat", "start", "Brawl")
        self.ok("combat", "party")
        self.assertIn("combat is running", self.fails("time", "advance", "1h"))
        self.ok("combat", "map", "new", "6x6")
        self.ok("combat", "place", "mira", "A1")
        self.ok("combat", "init", "mira", "12")
        for _ in range(12):
            self.ok("combat", "next")
        out = self.ok("combat", "end")
        self.assertIn("combat, 13 round(s)", out)
        self.assertEqual(self.w()["time"]["t"], 8 * 60 + 2)          # 13 rounds = 78 s → 2 minutes

    # ------------------------------------------------------------------ timers & rests
    def test_timers_rests_and_spells(self):
        self.ok("time", "set", "2", "20:00")
        self.ok("pc", "condition", "mira", "add", "poisoned", "--for", "2h")
        self.ok("pc", "condition", "mira", "add", "frightened", "--until-rest")
        run_cli("audit", "clear", "spell", "Long Ward", "--verdict", "CORRECT", "--notes", "ok")
        out = self.ok("pc", "cast", "mira", "Long Ward")
        self.assertIn("⌛ Timer t3: Long Ward lasts 8h", out)
        out = self.ok("time", "advance", "3h")
        self.assertIn("Expired: poisoned on Mira", out)
        conds = [c["name"] for c in campaign.load().get_pc("mira")["conditions"]]
        self.assertEqual(conds, ["frightened"])
        out = self.ok("pc", "rest", "party", "long")
        self.assertIn("Ends with the long rest: frightened on Mira", out)
        self.assertIn("⏳ +8h (long rest) → Day 3 · 07:00 (morning)", out)
        self.assertIn("Expired: Long Ward (Mira)", out)
        self.assertEqual(campaign.load().get_pc("mira")["conditions"], [])
        err = self.fails("pc", "rest", "mira", "long")
        self.assertIn("no more than one long rest in 24 hours", err)
        self.assertIn("RULES GATE", err)
        self.ok("pc", "rest", "mira", "long", "--ruling", "the table allows it", "--no-time")
        self.ok("pc", "rest", "mira", "short")
        self.assertEqual(self.w()["time"]["t"], 2 * world.DAY + 8 * 60)

    def test_zero_hp_cant_long_rest(self):
        self.ok("pc", "hp", "mira", "-99")
        self.assertIn("at least 1 hit point", self.fails("pc", "rest", "mira", "long"))

    # ------------------------------------------------------------------ clocks
    def test_clocks_and_deadlines(self):
        self.ok("time", "set", "3", "08:00")
        out = self.ok("clock", "add", "Lorard storms the gates", "--segments", "4", "--per", "1d", "--public",
                      "--on-fill", "the host attacks")
        self.assertIn("[k1] Lorard storms the gates □□□□ 0/4 · +1 every 1d", out)
        self.ok("clock", "add", "Halda needs proof", "--deadline", "day 4 noon")
        self.ok("clock", "tick", "lorard", "1", "--why", "the party insulted him")
        out = self.ok("time", "advance", "1d 6h")
        self.assertIn("Deadline passed: Halda needs proof", out)
        self.assertIn("◔ Lorard storms the gates +1 → 2/4", out)
        out = self.ok("time", "advance", "2d")
        self.assertIn("Clock filled: Lorard storms the gates", out)
        self.assertIn("the host attacks", out)
        self.assertIn("⏰ FILLED", self.ok("clock"))            # filled clocks stay listed until resolved
        self.ok("clock", "done", "k1")
        self.ok("clock", "done", "halda")
        self.assertIn("No clocks", self.ok("clock"))
        self.assertIn("done", self.ok("clock", "list", "--all"))

    # ------------------------------------------------------------------ factions
    def test_factions(self):
        out = self.ok("faction", "rep", "house mostana", "+2", "--why", "kept the deal quiet")
        self.assertIn("Friendly (+2)", out)
        self.ok("faction", "rep", "house-mostana", "+9")
        self.assertEqual(world.factions(campaign.load())[0]["rep"], 5)
        self.assertIn("is a npc entry, not a faction", self.fails("faction", "rep", "halda", "+1"))
        self.ok("faction", "rep", "The Choir", "-3", "--new")
        self.ok("clock", "add", "Mostana buys the Heartcoal", "--segments", "6")
        self.ok("faction", "agenda", "house-mostana", "add", "Buy the Heartcoal", "--clock", "k1")
        out = self.ok("faction")
        self.assertIn("**House Mostana** — Champion (+5)", out)
        self.assertIn("**The Choir** — Hostile (-3)", out)
        self.assertIn("f1 Buy the Heartcoal", out)

    # ------------------------------------------------------------------ scene & witnesses
    def make_okton(self):
        lore.add(campaign.load(), "Okton", "npc", "A fire giant smith.", summary="Fire giant smith")
        self.ok("companion", "new", "Okton", "--from-lore", "okton", "--status", "away")

    def test_scene_witnesses_and_companions(self):
        self.make_okton()
        out = self.ok("scene", "set", "Pyre Square", "--present", "halda,okton,Brother Fenn")
        self.assertIn("Brother Fenn isn't in the lore yet", out)
        self.assertEqual(world.scene_cast(campaign.load())[0], ("halda", "Halda"))
        from robodm import companions

        self.assertEqual(companions.state(campaign.load(), "okton")["status"], "present")
        out = self.ok("memory", "add", "Mira smashed the tally-board")
        self.assertIn("👁 witnessed by Halda, Okton, Brother Fenn", out)
        self.ok("memory", "add", "The steward is a spy", "--type", "secret")
        self.ok("memory", "add", "Mira whispered the password", "--known-by", "none")
        mems = memory.list_memories(campaign.load())
        self.assertEqual(mems[0]["known_by"], ["brother-fenn", "halda", "okton"])
        self.assertNotIn("known_by", mems[1])
        self.assertNotIn("known_by", mems[2])
        self.assertIn("tally-board", companions.brief(campaign.load(), "okton", record=False))   # he saw it
        self.assertNotIn("steward", companions.brief(campaign.load(), "okton", record=False))
        self.ok("scene", "leave", "okton")
        self.assertEqual(companions.state(campaign.load(), "okton")["status"], "away")
        self.ok("lore", "mind", "halda", "--feeling", "cornered", "--plan", "arrest the Choir's treasurer")
        brief = memory.brief(campaign.load())
        self.assertIn("## Scene", brief)
        self.assertIn("**Where:** Pyre Square", brief)
        self.assertIn("mind: feeling: cornered", brief)

    def test_gossip_suggests_related_npcs(self):
        self.ok("session", "start")
        self.ok("scene", "set", "Watch house", "--present", "halda")
        self.ok("memory", "add", "Mira bribed Halda with 50 gp")
        out = self.ok("world", "gossip")
        self.assertIn("could reach: Oselle Velun (via Halda)", out)
        self.assertNotIn("House Mostana", out)                  # two steps away: not suggested

    # ------------------------------------------------------------------ the off-screen tick
    def test_offscreen_tiers_rolls_and_resolution(self):
        self.make_okton()
        self.ok("companion", "agenda", "okton", "add", "Find the stolen tongs")
        self.ok("lore", "mind", "halda", "--plan", "arrest the Choir's treasurer")
        self.ok("clock", "add", "Mostana buys the Heartcoal", "--segments", "6")
        self.ok("faction", "agenda", "house-mostana", "add", "Buy the Heartcoal", "--clock", "k1")
        self.ok("faction", "pace", "house-mostana", "fast")
        self.ok("time", "set", "1", "08:00")
        self.assertNotIn("Off-screen", self.ok("time", "advance", "30m"))            # MICRO: nothing
        out = self.ok("time", "advance", "4h")                                        # MINOR: reminders, no rolls
        self.assertIn("Off-screen (MINOR", out)
        self.assertIn("Halda (away) is pursuing: arrest the Choir's treasurer", out)
        self.assertIn("Okton (away): Find the stolen tongs", out)
        self.assertNotIn("🎲", out)
        self.assertNotIn("House Mostana", out)
        self.assertIn("still open", self.fails("session", "end", "--summary", "x"))
        beats = world.open_beats(self.w())
        for b in beats:
            self.ok("world", "drop", b["id"])
        self.rng.push(5, 2, 3)                      # Mostana 5+1 = 6 (major), Halda 2, Okton 3
        out = self.ok("time", "advance", "2d")
        self.assertIn("Off-screen (MAJOR", out)
        self.assertIn("House Mostana: Buy the Heartcoal — 🎲 6: major step · clock k1 → 2/6 (surface a rumor of it)", out)
        self.assertIn("arrest the Choir's treasurer (🎲 2: no visible progress)", out)
        beat = next(b for b in world.open_beats(self.w()) if "Mostana" in b["text"])
        out = self.ok("world", "resolve", beat["id"], "Mostana's factor bought Oselle's granary debt", "--known-by", "oselle-velun")
        self.assertIn("resolved → memory", out)
        m = memory.list_memories(campaign.load(), mtype="world")[-1]
        self.assertEqual(m["known_by"], ["oselle-velun"])
        self.assertEqual(world.open_beats(self.w()), [])
        self.rng.push(1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)
        out = self.ok("time", "advance", "3w")                                      # HEAVY: 3 weekly rolls + a shift
        self.assertIn("Off-screen (HEAVY", out)
        self.assertIn("The world shifts", out)

    def test_brief_and_status_show_the_world(self):
        self.ok("time", "set", "11", "18:00")
        self.ok("clock", "add", "Ultimatum", "--deadline", "+2d", "--public")
        self.ok("faction", "rep", "house-mostana", "-1")
        brief = memory.brief(campaign.load())
        self.assertIn("🕰 **Day 11 · 18:00 (evening)**", brief)
        self.assertIn("## The world moves", brief)
        self.assertIn("Ultimatum · ⌛ 2d left (by day 13 18:00)", brief)
        self.assertIn("House Mostana: Wary (-1)", brief)
        self.assertIn("Time: Day 11", self.ok("status"))
