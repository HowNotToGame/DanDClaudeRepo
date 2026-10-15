from __future__ import annotations

import contextlib
import io
import json
import unittest

from robodm import bundle, campaign, context_blocks, lore, memory, random_tables
from robodm.cli import main
from robodm.util import RoboDMError, dump_frontmatter, parse_frontmatter
from tests.helpers import RoboDMTestCase


def run_cli(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class FrontmatterTests(unittest.TestCase):
    def test_roundtrip(self):
        meta = {"name": "Krag: the Chief", "type": "npc", "tags": ["goblin", "cragmaw"], "canon": True, "n": 3}
        text = dump_frontmatter(meta, "Body text.")
        parsed, body = parse_frontmatter(text)
        self.assertEqual(parsed, meta)
        self.assertEqual(body.strip(), "Body text.")

    def test_block_lists(self):
        parsed, body = parse_frontmatter("---\ntitle: X\ntags:\n  - a\n  - b\n---\nhello")
        self.assertEqual(parsed, {"title": "X", "tags": ["a", "b"]})
        self.assertEqual(body, "hello")


class CampaignMemoryTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        self.camp = campaign.create("Sunken Crown", premise="A drowned kingdom rises.")

    def test_create_sets_active_and_rejects_duplicates(self):
        self.assertEqual(campaign.active_slug(), "sunken-crown")
        with self.assertRaises(RoboDMError):
            campaign.create("Sunken Crown")

    def test_sessions_and_journal(self):
        self.assertEqual(self.camp.start_session("Arrival"), 1)
        self.camp.log("Met the ferryman Brannoc.")
        self.camp.end_session("The party reached the drowned temple.")
        self.assertFalse(self.camp.session["active"])
        self.assertEqual(self.camp.session_summaries()[0], (1, "The party reached the drowned temple."))
        self.assertEqual(self.camp.start_session(), 2)
        self.camp.log("Second session beat.")
        text = self.camp.journal_path(2).read_text()
        self.assertIn("Second session beat.", text)

    def test_log_after_summary_stays_in_log_section(self):
        self.camp.start_session()
        self.camp.end_session("Done.")
        self.camp.session["active"] = True
        self.camp.session["number"] = 1
        self.camp.log("Late note")
        text = self.camp.journal_path(1).read_text()
        self.assertLess(text.index("Late note"), text.index("## Summary"))

    def test_memory_add_search_edit_forget(self):
        m1 = memory.add(self.camp, "Mira promised Elder Voss to return the idol", "choice", ["voss", "idol"])
        memory.add(self.camp, "The ferryman Brannoc owes the Guild 300 gold", "npc", ["brannoc"], important=True)
        self.assertEqual(m1["id"], "m0001")
        hits = memory.search(self.camp, "what did we promise Voss")
        self.assertEqual(hits[0]["ref"], "m0001")
        memory.update(self.camp, "m0001", text="Mira swore an oath to Elder Voss")
        self.assertIn("swore", memory.list_memories(self.camp)[0]["text"])
        memory.update(self.camp, "m0001", forget=True)
        self.assertEqual([m["id"] for m in memory.list_memories(self.camp)], ["m0002"])

    def test_quests(self):
        q = memory.quest_add(self.camp, "Recover the idol", giver="Elder Voss")
        memory.quest_update(self.camp, "idol", note="Learned it's in the temple")
        memory.quest_update(self.camp, q["id"], status="done")
        self.assertEqual(self.camp.quests()[0]["status"], "done")
        with self.assertRaises(RoboDMError):
            memory.quest_update(self.camp, "q999", status="done")

    def test_lore_lifecycle(self):
        lore.add(self.camp, "Brannoc Vale", "npc", "A one-eyed ferryman.", summary="Ferryman who owes the Guild",
                 tags="ferry,guild", status="alive", aliases="Old One-Eye")
        lore.add(self.camp, "Saltmere", "settlement", "A fishing town.", summary="Fishing town on the drowned coast")
        self.assertEqual(lore.find(self.camp, "old one-eye")["slug"], "brannoc-vale")
        lore.update(self.camp, "Brannoc Vale", append="Paid his debt.", fields={"status": "retired"})
        e = lore.find(self.camp, "brannoc")
        self.assertEqual(e["meta"]["status"], "retired")
        self.assertIn("Paid his debt.", e["body"])
        self.assertEqual(lore.search(self.camp, "fishing coast")[0]["slug"], "saltmere")
        idx = lore.build_index(self.camp).read_text()
        self.assertIn("[Brannoc Vale](npc/brannoc-vale.md)", idx)
        with self.assertRaises(RoboDMError):
            lore.add(self.camp, "Saltmere", "settlement", "dup")

    def test_context_blocks(self):
        names = {b["name"] for b in context_blocks.all_blocks(self.camp)}
        self.assertIn("safety-lines-and-veils", names)
        self.assertEqual(context_blocks.active_blocks(self.camp), [])
        context_blocks.set_enabled(self.camp, "tone-grimdark", True)
        context_blocks.set_enabled(self.camp, "safety-lines-and-veils", True)
        context_blocks.add(self.camp, "No fumbles", "Natural 1s on attacks just miss.", scope="combat", priority=60)
        rendered = context_blocks.render(self.camp)
        # priority order: safety (100) > house rule (60) > tone (40)
        self.assertLess(rendered.index("Lines, veils"), rendered.index("No fumbles"))
        self.assertLess(rendered.index("No fumbles"), rendered.index("Grimdark"))
        self.assertNotIn("No fumbles", context_blocks.render(self.camp, scope="social"))
        context_blocks.set_enabled(self.camp, "no-fumbles", False)
        self.assertNotIn("No fumbles", context_blocks.render(self.camp))
        context_blocks.remove(self.camp, "no-fumbles")
        with self.assertRaises(RoboDMError):
            context_blocks.remove(self.camp, "tone-grimdark")  # library blocks can't be deleted

    def test_brief_contains_everything(self):
        self.camp.save_pc(campaign.new_pc("Mira", cls="Wizard", level=2, race="Elf"))
        self.camp.start_session()
        self.camp.log("Entered the flooded crypt")
        memory.add(self.camp, "Never trust the harbourmaster", "secret", important=True)
        memory.quest_add(self.camp, "Find the crown")
        lore.add(self.camp, "Saltmere", "settlement", "Town.", summary="Fishing town")
        context_blocks.set_enabled(self.camp, "tone-horror", True)
        b = memory.brief(self.camp)
        for needle in ("Sunken Crown", "A drowned kingdom", "Mira", "Entered the flooded crypt",
                       "Never trust the harbourmaster", "Find the crown", "Saltmere", "tone-horror"):
            self.assertIn(needle, b)

    def test_rest_and_levelup_math(self):
        pc = campaign.new_pc("Thorin", cls="Warlock", level=5, con=14)
        self.assertEqual(pc["spell_slots"], {"3": {"max": 2, "used": 0, "pact": True}})
        pc["spell_slots"]["3"]["used"] = 2
        pc["resources"] = {"Hex": {"max": 1, "used": 1, "reset": "long"}}
        campaign.rest(pc, "short")
        self.assertEqual(pc["spell_slots"]["3"]["used"], 0)
        self.assertEqual(pc["resources"]["Hex"]["used"], 1)
        campaign.rest(pc, "long")
        self.assertEqual(pc["resources"]["Hex"]["used"], 0)
        paladin = campaign.new_pc("P", cls="Paladin", level=5)
        self.assertEqual(paladin["spell_slots"], {"1": {"max": 4, "used": 0}, "2": {"max": 2, "used": 0}})
        ek = campaign.new_pc("E", cls="Fighter", subclass="Eldritch Knight", level=3)
        self.assertEqual(ek["spell_slots"], {"1": {"max": 2, "used": 0}})
        ek7 = campaign.new_pc("E", cls="Fighter", subclass="Eldritch Knight", level=7)
        self.assertEqual(ek7["spell_slots"], {"1": {"max": 4, "used": 0}, "2": {"max": 2, "used": 0}})
        ranger2 = campaign.new_pc("R", cls="Ranger", level=2)
        self.assertEqual(ranger2["spell_slots"], {"1": {"max": 2, "used": 0}})
        self.assertEqual(campaign.new_pc("R", cls="Ranger", level=1)["spell_slots"], {})
        art = campaign.new_pc("A", cls="Artificer", level=1)
        self.assertEqual(art["spell_slots"], {"1": {"max": 2, "used": 0}})

    def test_export_import_roundtrip(self):
        memory.add(self.camp, "A fact", "event")
        lore.add(self.camp, "Saltmere", "settlement", "Town.")
        out = bundle.export(self.camp, self.tmp / "save.json")
        imported = bundle.import_bundle(out, slug="copy")
        self.assertEqual(imported.slug, "copy")
        self.assertEqual(memory.list_memories(imported)[0]["text"], "A fact")
        self.assertEqual(lore.find(imported, "saltmere")["meta"]["type"], "settlement")
        with self.assertRaises(RoboDMError):
            bundle.import_bundle(out, slug="copy")

    def test_import_rejects_path_traversal(self):
        evil = self.tmp / "evil.json"
        evil.write_text(json.dumps({"format": "robodm-campaign", "slug": "x", "files": {"../../pwned.txt": "hi",
                                                                                         "campaign.json": "{}"}}))
        with self.assertRaises(RoboDMError):
            bundle.import_bundle(evil)

    def test_random_names_avoid_overused(self):
        for _ in range(200):
            first = random_tables.name("elf", with_family=False).lower()
            self.assertNotIn(first, random_tables.OVERUSED)


class CLITests(RoboDMTestCase):
    def test_full_flow(self):
        code, out, _ = run_cli("campaign", "new", "CLI Camp", "--premise", "test")
        self.assertEqual(code, 0, out)
        code, out, _ = run_cli("pc", "add", "Thorin", "--class", "Fighter", "--level", "3", "--str", "16", "--con", "14",
                               "--ac", "18", "--skills", "athletics")
        self.assertEqual(code, 0)
        self.assertIn("Athletics +5", out)
        self.rng.push(10)
        code, out, _ = run_cli("pc", "check", "thorin", "athletics", "--dc", "15")
        self.assertIn("**15**", out)
        self.assertIn("success", out)
        self.assertEqual(run_cli("campaign", "set", "dice_mode", "players")[0], 0)
        code, out, _ = run_cli("pc", "check", "thorin", "athletics")
        self.assertIn("Ask", out)
        code, _, err = run_cli("campaign", "set", "dice_mode", "sometimes")
        self.assertEqual(code, 1)
        self.assertIn("must be one of", err)
        run_cli("campaign", "set", "dice_mode", "auto")
        code, out, _ = run_cli("combat", "start", "Bog fight")
        code, out, _ = run_cli("combat", "party")
        code, out, _ = run_cli("combat", "add", "mire imp", "--count", "2")
        self.assertIn("Mire Imp 2", out)
        code, out, _ = run_cli("combat", "add", "Cart", "--hp", "20", "--ac", "12")
        self.assertIn("Cart", out)
        self.rng.push(15, 5, 6, 2)
        code, out, _ = run_cli("combat", "init")
        self.assertIn("Round 1", out)
        code, out, _ = run_cli("combat", "attack", "mire-imp-1", "thorin", "--action", "sting")
        self.assertEqual(code, 0, out)
        code, out, _ = run_cli("combat", "status", "--players")
        self.assertNotIn("HP 10/10", out)
        code, out, _ = run_cli("combat", "end")
        self.assertIn("ended after", out)
        code, out, _ = run_cli("memory", "add", "Thorin owes a debt", "--type", "relationship", "--tags", "debt")
        code, out, _ = run_cli("memory", "search", "debt")
        self.assertIn("Thorin owes a debt", out)
        code, out, _ = run_cli("lore", "add", "Saltmere", "--type", "settlement", "--summary", "Fishing town",
                               "--text", "Smells of fish.")
        code, out, _ = run_cli("memory", "brief")
        self.assertIn("Saltmere", out)
        code, out, _ = run_cli("context", "enable", "tone-heroic,mobile-friendly")
        code, out, _ = run_cli("context", "render")
        self.assertIn("Heroic", out)
        code, out, _ = run_cli("rules", "show", "ember burst")
        self.assertIn("Ember Burst", out)
        code, out, _ = run_cli("rules", "list", "spell", "--class", "wizard")
        self.assertIn("Ember Burst", out)
        code, out, _ = run_cli("rules", "encounter", "--party", "4x3", "--monsters", "mire imp x2, bog brute")
        self.assertIn("DEADLY", out)  # 850 XP x2 = 1,700 >= 1,600
        code, out, _ = run_cli("session", "end", "--summary", "We fought in a bog.")
        self.assertIn("closed", out)

    def test_errors_are_friendly(self):
        code, _, err = run_cli("memory", "brief")
        self.assertEqual(code, 1)
        self.assertIn("No campaigns yet", err)


if __name__ == "__main__":
    unittest.main()
