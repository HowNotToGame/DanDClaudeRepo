"""Long-term memory: recall search, the budgeted brief, automatic capture, autosave, cross-branch sync."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from robodm import audit, bundle, campaign, capture, hooks, lore, memory, recall
from tests.helpers import RoboDMTestCase
from tests.test_campaign_memory import run_cli


class RecallTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        self.camp = campaign.create("Recall Camp", premise="test")
        memory.add(self.camp, "The dragons of Mount Ember fled south after the comet", "event")
        memory.add(self.camp, "Elder Voss keeps the harbourmaster's ledger under the altar", "secret")
        memory.add(self.camp, "Mira owes the Thieves' Guild forty gold", "relationship", important=True)
        lore.add(self.camp, "Brannoc Vale", "npc", "A ferryman with one eye. He carries messages for the Guild.",
                 summary="One-eyed ferryman who owes the Guild")
        lore.add_source(self.camp, text="# The Drowned Coast\nSalt marshes.\n\n## Tides\nThe spring tide floods "
                                       "the causeway twice a day.", title="Coast notes")

    def kinds_refs(self, hits):
        return [(h["kind"], h["ref"]) for h in hits]

    def test_stemming_substring_and_typos(self):
        self.assertTrue(recall.FTS5, "this Python's sqlite3 should have FTS5")
        self.assertEqual(memory.search(self.camp, "dragon")[0]["ref"], "m0001")          # dragons → dragon
        self.assertEqual(memory.search(self.camp, "Brann")[0]["ref"], "brannoc-vale")    # substring (trigram)
        self.assertEqual(memory.search(self.camp, "Brannok")[0]["ref"], "brannoc-vale")  # typo → vocabulary fix
        hit = memory.search(self.camp, "causeway flooding")[0]
        self.assertEqual(hit["kind"], "source")                                         # reference sources too
        self.assertIn("**", hit["snippet"])

    def test_several_phrasings_fuse(self):
        hits = recall.search(self.camp, ["who has the ledger", "secret hidden under altar", "Elder Voss"])
        self.assertEqual(hits[0]["ref"], "m0002")
        self.assertEqual([h["kind"] for h in recall.search(self.camp, ["guild"], scope="lore")], ["lore"])

    def test_index_follows_changes(self):
        self.assertFalse([h for h in memory.search(self.camp, "griffon") if h["kind"] == "memory"])
        memory.add(self.camp, "A griffon nests on the bell tower", "location")
        self.assertEqual(memory.search(self.camp, "griffon")[0]["ref"], "m0004")
        memory.update(self.camp, "m0004", forget=True)
        self.assertFalse(memory.search(self.camp, "griffon"))

    def test_plain_fallback_without_fts5(self):
        old = recall.FTS5
        recall.FTS5 = False
        try:
            self.assertEqual(recall.search(self.camp, ["dragons comet"])[0]["ref"], "m0001")
        finally:
            recall.FTS5 = old

    def test_recall_cli_and_stats(self):
        code, out, _ = run_cli("recall", "ferryman", "one eye")
        self.assertEqual(code, 0)
        self.assertIn("Brannoc Vale", out.splitlines()[0])
        self.assertIn("Recall index:", run_cli("recall", "--stats")[1])
        self.assertIn("Mira owes", run_cli("memory", "search", "debt", "--or", "owes gold")[1])
        self.assertIn("m0003", run_cli("memory", "list", "--important")[1])
        self.assertNotIn("m0001", run_cli("memory", "list", "--important")[1])


class BriefTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        self.camp = campaign.create("Brief Camp", premise="test")

    def test_budget_trims_low_priority_first_and_says_how_to_get_the_rest(self):
        for i in range(40):
            memory.add(self.camp, f"Important fact number {i}: " + "lorem ipsum " * 40, "event", important=True)
        for i in range(40):
            memory.add(self.camp, f"Ordinary memory {i} " + "dolor sit " * 30, "event")
        for i in range(30):
            lore.add(self.camp, f"Npc {i}", "npc", "Body.", summary="A person of note " * 5)
        b = memory.brief(self.camp, budget=6000)
        self.assertLess(len(b), 8000)
        self.assertIn("## Party", b)                                   # priority 0 survives
        self.assertIn("Important fact number 39", b)                   # newest key facts kept
        self.assertIn("`./dm memory list --important`", b)            # …and the rest is pointed to
        self.assertIn("not shown", b)
        full = memory.brief(self.camp, full=True)
        self.assertIn("Important fact number 0", full)
        self.assertGreater(len(full), 25000)

    def test_relevant_older_memories_surface(self):
        memory.add(self.camp, "The lighthouse keeper Orsa is secretly a smuggler", "secret")
        for i in range(30):
            memory.add(self.camp, f"Filler event {i}", "event")
        self.camp.start_session()
        self.camp.log("The party rows out to the lighthouse to question Orsa")
        b = memory.brief(self.camp)
        self.assertIn("Relevant to the current scene", b)
        self.assertIn("lighthouse keeper Orsa", b)


class CaptureTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        self.camp = campaign.create("Capture Camp", premise="test")
        self.camp.save_pc(campaign.new_pc("Mira", cls="Wizard", level=2))
        lore.add(self.camp, "Saltmere", "settlement", "A town.", summary="Fishing town")

    def test_transcript_only_during_a_session(self):
        hooks.prompt(self.camp, {"prompt": "I look around"})
        self.assertEqual(capture.entries(self.camp), [])               # no session running: not captured
        self.camp.start_session()
        hooks.prompt(self.camp, {"prompt": "I look around the harbour"})
        hooks.prompt(self.camp, {"prompt": "/roll 1d20"})              # slash commands aren't play
        hooks.stop(self.camp, {"last_assistant_message": "Gulls wheel over **Saltmere**."})
        hooks.stop(self.camp, {"last_assistant_message": "Gulls wheel over **Saltmere**."})   # repeat: skipped
        rows = capture.entries(self.camp)
        self.assertEqual([who for _, who, _ in rows], ["Player", "DM"])
        self.assertEqual(rows[0][2], "I look around the harbour")
        self.assertIn("**Player**", memory.brief(self.camp))           # last exchanges in the brief
        self.assertEqual(memory.search(self.camp, "gulls wheel")[0]["kind"], "transcript")
        self.assertIn("I look around the harbour", run_cli("session", "transcript")[1])
        self.assertEqual(self.camp.session_summaries(), [])            # transcripts aren't journals

    def test_unrecorded_names_reminder(self):
        self.camp.start_session()
        reply = ("You meet **Brannoc Vale**, a ferryman from **Saltmere**, at **The Gilded Eel**. "
                 "Mira's roll: **17** — **success**. **Mira** nods. **CRITICAL HIT!**")
        self.assertEqual(capture.unrecorded_names(self.camp, reply), ["Brannoc Vale", "The Gilded Eel"])
        hooks.stop(self.camp, {"last_assistant_message": reply})
        lore.add(self.camp, "Brannoc Vale", "npc", "Ferryman.", summary="Ferryman")   # recorded in between
        r = hooks.prompt(self.camp, {"prompt": "I ask him about the Eel"})
        ctx = r["hookSpecificOutput"]["additionalContext"]
        self.assertIn("The Gilded Eel", ctx)
        self.assertNotIn("Brannoc", ctx)
        self.assertIsNone(hooks.prompt(self.camp, {"prompt": "next"}))   # reminded once

    def test_prompt_merges_with_the_audit_reminder(self):
        self.camp.start_session()
        run_cli("pc", "hp", "mira", "-2")                                 # a rules event
        hooks.stop(self.camp, {"last_assistant_message": "You reach **The Gilded Eel**."})
        ctx = hooks.prompt(self.camp, {"prompt": "go on"})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("still unaudited", ctx)
        self.assertIn("The Gilded Eel", ctx)


class AutosaveTests(RoboDMTestCase):
    def test_autosave_after_commands_and_cache_excluded(self):
        out_dir = self.tmp / "outputs"
        os.environ["ROBODM_AUTOSAVE"] = str(out_dir)
        try:
            self.assertEqual(run_cli("campaign", "new", "Save Camp")[0], 0)
            run_cli("memory", "add", "The bridge is out")
            save = out_dir / "save-camp.robodm.json"
            self.assertTrue(save.exists())
            run_cli("recall", "bridge")                                   # builds .cache/recall.db
            run_cli("memory", "add", "The ford is guarded")
            files = json.loads(save.read_text(encoding="utf-8"))["files"]
            self.assertIn("memory.jsonl", files)
            self.assertIn("ford is guarded", files["memory.jsonl"])
            self.assertFalse([f for f in files if f.startswith(".cache")])
            before = save.stat().st_mtime_ns
            run_cli("memory", "brief")                                    # nothing changed: not rewritten
            self.assertEqual(save.stat().st_mtime_ns, before)
        finally:
            os.environ.pop("ROBODM_AUTOSAVE", None)


@unittest.skipUnless(shutil.which("git"), "needs git")
class SyncTests(RoboDMTestCase):
    """A phone session pushed campaign progress to its own branch; the next session must pick it up."""

    def git(self, cwd: Path, *args: str) -> str:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        self.assertEqual(r.returncode, 0, f"git {' '.join(args)}: {r.stderr}")
        return r.stdout

    def setUp(self):
        super().setUp()
        self.origin = self.tmp / "origin.git"
        self.git(self.tmp, "init", "--bare", "-b", "main", str(self.origin))
        self.a = self.tmp / "home"                                      # ROBODM_HOME: the desktop checkout
        self.git(self.a, "init", "-b", "main")
        for k, v in (("user.name", "Test"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            self.git(self.a, "config", k, v)
        self.git(self.a, "remote", "add", "origin", str(self.origin))
        camp = campaign.create("Sync Camp", premise="test")
        memory.add(camp, "Session 1 happened", "event")
        self.git(self.a, "add", "campaigns")
        self.git(self.a, "commit", "-m", "Session 1")
        self.git(self.a, "push", "-q", "origin", "main")
        # the "phone" clone plays session 2 on its own branch
        self.b = self.tmp / "phone"
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.b))
        for k, v in (("user.name", "Phone"), ("user.email", "p@example.com"), ("commit.gpgsign", "false")):
            self.git(self.b, "config", k, v)
        self.git(self.b, "checkout", "-q", "-b", "claude/session-2")
        with open(self.b / "campaigns" / "sync-camp" / "memory.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "m0002", "ts": "2026-10-18T10:00:00", "session": 2, "type": "event",
                                 "text": "Session 2 happened on the phone", "tags": []}) + "\n")
        self.git(self.b, "commit", "-qam", "Session 2: on the phone")
        self.git(self.b, "push", "-q", "origin", "claude/session-2")

    def test_check_then_apply(self):
        code, out, _ = run_cli("sync", "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("Newer: sync-camp on origin/claude/session-2", out)
        self.assertNotIn("phone", memory.brief(campaign.load("sync-camp")))
        code, out, _ = run_cli("sync")
        self.assertIn("Synced sync-camp", out)
        self.assertIn("Session 2 happened on the phone", (self.a / "campaigns/sync-camp/memory.jsonl").read_text())
        self.assertIn("campaigns/sync-camp/memory.jsonl", self.git(self.a, "diff", "--cached", "--name-only"))
        self.git(self.a, "commit", "-qm", "Session 3 start")
        self.assertIn("up to date", run_cli("sync", "--check")[1])

    def test_uncommitted_local_changes_are_kept(self):
        memory.add(campaign.load("sync-camp"), "Unsaved desktop note", "event")
        out = run_cli("sync")[1]
        self.assertIn("Skipped", out)
        self.assertIn("Unsaved desktop note", (self.a / "campaigns/sync-camp/memory.jsonl").read_text())

    def test_session_start_hook_reports_on_desktop_and_applies_in_cloud(self):
        os.environ["ROBODM_SYNC"] = ""
        self.assertIn("Run `./dm sync`", hooks.session_start({"source": "startup"}))
        os.environ["CLAUDE_CODE_REMOTE"] = "true"
        try:
            out = hooks.session_start({"source": "startup"})
        finally:
            os.environ.pop("CLAUDE_CODE_REMOTE", None)
            os.environ.pop("ROBODM_SYNC", None)
        self.assertIn("RoboDM sync: ⤵ Synced sync-camp", out)


class ContextModeCompatTests(unittest.TestCase):
    def test_code_running_mcp_tools_are_checked(self):
        deny = lambda r: bool(r) and r["hookSpecificOutput"]["permissionDecision"] == "deny"
        ctx = "mcp__plugin_context-mode_context-mode__ctx_execute"
        self.assertTrue(deny(audit.hook_pretool({"tool_name": ctx, "tool_input": {
            "language": "shell", "code": "./dm audit pass all"}})))
        self.assertTrue(deny(audit.hook_pretool({"tool_name": "mcp__x__ctx_batch_execute", "tool_input": {
            "commands": [{"label": "a", "command": "cd repo && ./dm audit clear spell Shield --verdict CORRECT"}]}})))
        self.assertIsNone(audit.hook_pretool({"tool_name": ctx, "agent_type": "rules-lawyer",
                                              "tool_input": {"code": "./dm audit pass all"}}))
        self.assertIsNone(audit.hook_pretool({"tool_name": ctx, "tool_input": {"code": "./dm combat status"}}))
        # tools that don't run code aren't policed for merely mentioning the command
        self.assertIsNone(audit.hook_pretool({"tool_name": "mcp__docs__update", "tool_input": {
            "payload": "Run `./dm audit pass all` > notes in campaigns/x/combat.json"}}))


if __name__ == "__main__":
    unittest.main()
