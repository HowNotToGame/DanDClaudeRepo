"""Mandatory rules checking: the pre-check gate, rules-lawyer clearances, post-audit events and the hooks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from robodm import audit, campaign, memory
from tests.helpers import RoboDMTestCase, write
from tests.test_campaign_memory import run_cli

ROOT = Path(__file__).resolve().parent.parent


class AuditTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        # extra fixture spells: a bonus-action spell, an action cantrip, a 1-minute ritual
        p = self.data_dir / "spells/spells-tst.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["spell"] += [
            {"name": "Misty Hop", "source": "TST", "level": 2, "school": "C", "time": [{"number": 1, "unit": "bonus"}],
             "range": {"type": "point", "distance": {"type": "self"}}, "components": {"v": True},
             "duration": [{"type": "instant"}], "entries": ["You teleport 30 feet."]},
            {"name": "Sparkle", "source": "TST", "level": 0, "school": "V", "time": [{"number": 1, "unit": "action"}],
             "range": {"type": "point", "distance": {"type": "feet", "amount": 60}}, "components": {"v": True},
             "duration": [{"type": "instant"}], "entries": ["A spark."], "spellAttack": ["R"]},
            {"name": "Long Ward", "source": "TST", "level": 1, "school": "A", "time": [{"number": 1, "unit": "minute"}],
             "range": {"type": "point", "distance": {"type": "touch"}}, "components": {"v": True},
             "duration": [{"type": "timed", "duration": {"type": "hour", "amount": 8}}], "meta": {"ritual": True},
             "entries": ["A ward."]},
        ]
        write(p, data)
        from robodm.rules import index

        index.default_db.cache_clear()
        self.assertEqual(run_cli("campaign", "new", "Gate Camp", "--premise", "test")[0], 0)
        run_cli("campaign", "set", "sources", "PHB,MM,TST")
        run_cli("pc", "add", "Thorin", "--class", "Fighter", "--level", "3", "--str", "16", "--con", "14", "--ac", "18")
        run_cli("pc", "add", "Mira", "--class", "Wizard", "--level", "3", "--dex", "14", "--int", "17", "--ac", "12")
        run_cli("combat", "start", "Gate test")
        run_cli("combat", "party")
        run_cli("combat", "add", "mire imp")
        run_cli("combat", "add", "bog brute")
        run_cli("combat", "map", "new", "10x6", "Gate yard")
        run_cli("combat", "place", "mira", "A1", "thorin", "B1", "mire-imp", "C1", "bog-brute", "D1")
        for who, n in (("mira", 20), ("thorin", 15), ("mire-imp", 10), ("bog-brute", 5)):
            run_cli("combat", "init", who, str(n))
        self.camp = campaign.load()

    def ok(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 0, f"{argv}: {out}{err}")
        return out

    def blocked(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 1, f"{argv} should be blocked: {out}")
        self.assertIn("RULES GATE", err)
        return err

    def clear(self, kind, name, notes="ok"):
        self.ok("audit", "clear", kind, name, "--verdict", "CORRECT", "--notes", notes, "--cite", "TST p.1")

    # ------------------------------------------------------------------ pre-check: clearances
    def test_uncleared_spell_blocks_then_cast_spends_slot(self):
        err = self.blocked("combat", "cast", "mira", "Still Mind")
        self.assertIn('audit brief spell "Still Mind"', err)
        brief = self.ok("audit", "brief", "spell", "still mind", "--use", "Mira targets the brute")
        self.assertIn("Pre-check: spell still mind", brief)
        self.assertIn("Mira targets the brute", brief)
        self.assertIn("condition paralyzed", brief)          # mentioned in the text, not yet cleared
        self.clear("spell", "still mind", "humanoids only; WIS save")
        out = self.ok("combat", "cast", "mira", "Still Mind")
        self.assertIn("📘 Cleared spell Still Mind: humanoids only", out)
        self.assertIn("level 2 slots 1/2 left", out)
        self.assertIn("concentrating on **Still Mind**", out)
        self.assertEqual(campaign.load().get_pc("Mira")["spell_slots"]["2"]["used"], 1)

    def test_denied_item_stays_blocked(self):
        self.ok("audit", "clear", "spell", "Still Mind", "--verdict", "INCORRECT", "--notes", "the brute isn't humanoid")
        err = self.blocked("combat", "cast", "mira", "Still Mind")
        self.assertIn("DENIED", err)
        self.assertIn("isn't humanoid", err)
        err = self.blocked("combat", "cast", "mira", "Still Mind", "--ruling", "rule of cool")
        self.assertIn("DENIED", err)                          # --ruling can't override the rules-lawyer

    def test_self_clear_requires_lookup(self):
        code, _, err = run_cli("audit", "clear", "spell", "Still Mind", "--verdict", "CORRECT", "--self")
        self.assertEqual(code, 1)
        self.assertIn("Look it up first", err)
        self.ok("rules", "show", "still mind")
        self.ok("audit", "clear", "spell", "Still Mind", "--verdict", "CORRECT", "--self")
        self.assertEqual(audit.cleared(campaign.load())["spell:still-mind"]["by"], "self")

    def test_ruling_command_and_feature_resource(self):
        err = self.blocked("ruling", "Grappling while holding a shield is fine", "--key", "grapple-shield")
        self.assertIn('audit brief ruling "grapple-shield"', err)
        self.clear("ruling", "grapple-shield", "one free hand is enough")
        out = self.ok("ruling", "Grappling while holding a shield is fine", "--key", "grapple-shield")
        self.assertIn("Cleared ruling grapple-shield", out)
        self.assertTrue(any("grapple-shield" in m["text"] for m in memory.list_memories(campaign.load(), mtype="rule")))
        self.ok("pc", "resource", "thorin", "add", "Second Wind", "--max", "1", "--reset", "short")
        self.blocked("pc", "resource", "thorin", "use", "Second Wind")
        self.clear("feature", "Second Wind", "bonus action; 1d10 + fighter level")
        self.assertIn("Cleared feature Second Wind", self.ok("pc", "resource", "thorin", "use", "Second Wind"))

    # ------------------------------------------------------------------ pre-check: code gate
    def test_slots_spell_list_and_casting_time(self):
        self.clear("spell", "Ember Burst")
        self.assertIn("no level 3 spell slots", self.blocked("combat", "cast", "mira", "Ember Burst"))
        err = self.blocked("combat", "cast", "thorin", "Ember Burst")
        self.assertIn("isn't on the Fighter spell list", err)
        self.assertIn("it's Mira's turn", err)
        self.clear("spell", "Long Ward")
        self.assertIn("longer than a combat turn", self.blocked("combat", "cast", "mira", "Long Ward"))
        run_cli("combat", "end")
        self.assertIn("ritual, no slot", self.ok("pc", "cast", "mira", "Long Ward", "--ritual"))
        self.assertEqual(campaign.load().get_pc("Mira")["spell_slots"]["1"]["used"], 0)
        self.ok("pc", "cast", "mira", "Long Ward")
        self.assertEqual(campaign.load().get_pc("Mira")["spell_slots"]["1"]["used"], 1)

    def test_bonus_action_spell_rule(self):
        for s in ("Misty Hop", "Still Mind", "Sparkle"):
            self.clear("spell", s)
        self.ok("combat", "cast", "mira", "Misty Hop")
        self.assertIn("bonus-action spell rule", self.blocked("combat", "cast", "mira", "Still Mind"))
        out = self.ok("combat", "cast", "mira", "Sparkle")       # a 1-action cantrip is still allowed
        self.assertIn("--to-hit 5", out)                          # spell attack = DC 13 - 8
        self.ok("combat", "next")
        self.ok("combat", "next")
        self.ok("combat", "next")
        self.ok("combat", "next")                                 # Mira's next turn: the rule resets
        self.ok("combat", "cast", "mira", "Still Mind")

    def test_turns_reactions_and_incapacitation(self):
        err = self.blocked("combat", "attack", "mire-imp", "thorin", "--action", "sting")
        self.assertIn("it's Mira's turn, not Mire Imp's", err)
        self.ok("combat", "attack", "mire-imp", "thorin", "--action", "sting", "--reaction")
        self.assertIn("already used its reaction", self.blocked("combat", "attack", "mire-imp", "thorin",
                                                                "--action", "sting", "--reaction"))
        self.assertIn("can't attack itself", self.blocked("combat", "attack", "mira", "mira", "--to-hit", "3",
                                                          "--damage", "1d4 piercing"))
        self.blocked("combat", "condition", "bog-brute", "add", "stunned")    # condition not cleared yet
        self.clear("condition", "stunned")
        self.ok("combat", "condition", "bog-brute", "add", "stunned")
        err = self.blocked("combat", "attack", "bog-brute", "mira", "--action", "club", "--reaction")
        self.assertIn("is stunned and can't take actions or reactions", err)
        self.assertIn("legendary actions", self.blocked("combat", "attack", "bog-brute", "mira", "--action", "club",
                                                        "--legendary"))

    def test_conditions_damage_types_and_spell_dc(self):
        self.assertIn("15 standard conditions", self.blocked("combat", "condition", "thorin", "add", "hexed"))
        self.ok("combat", "condition", "thorin", "add", "hexed", "--custom")
        self.assertIn("isn't a damage type", self.blocked("combat", "damage", "thorin", "5", "--type", "firey"))
        self.clear("spell", "Still Mind")
        err = self.blocked("combat", "save", "wis", "bog-brute", "--dc", "15", "--spell", "Still Mind", "--by", "mira")
        self.assertIn("spell save DC is 13", err)
        self.rng.push(2)
        self.ok("combat", "save", "wis", "bog-brute", "--dc", "13", "--spell", "Still Mind", "--by", "mira")
        self.assertIn("isn't a concentration spell", self.blocked("combat", "conc", "mira", "Ember Burst"))

    def test_override_is_logged_and_flagged(self):
        out = self.ok("combat", "attack", "mire-imp", "thorin", "--action", "sting", "--ruling", "surprise round")
        self.assertIn("vs AC 18", out)
        ev = audit.events(campaign.load())[-1]
        self.assertTrue(any("Mira's turn" in p for p in ev["override"]))
        self.assertIn("DM OVERRIDE", self.ok("audit", "pending"))
        rules = memory.list_memories(campaign.load(), mtype="rule")
        self.assertTrue(any("surprise round" in m["text"] and "override" in m["tags"] for m in rules))

    # ------------------------------------------------------------------ post-audit
    def test_events_backstop_fail_and_fix(self):
        self.ok("combat", "damage", "thorin", "5", "--type", "fire")
        self.ok("combat", "damage", "thorin", "3")
        evs = audit.open_events(campaign.load())
        self.assertEqual([e["status"] for e in evs], ["pending", "pending"])
        self.assertIn("combat damage thorin 5", evs[0]["cmd"])
        packet = self.ok("audit", "pending")
        self.assertIn("Post-audit: 2 event(s) (e0001–e0002)", packet)
        self.ok("audit", "turn")                                  # the player spoke again: a new turn
        err = self.blocked("combat", "damage", "thorin", "1")
        self.assertIn("previous reply left 2", err)
        self.ok("audit", "fail", "e0001", "--notes", "it was 4", "--fix", "./dm combat heal thorin 1")
        self.ok("audit", "pass", "all")
        self.blocked("combat", "damage", "thorin", "1")           # the failure isn't fixed yet
        code, _, err = run_cli("combat", "heal", "thorin", "1", "--fix", "e0002")
        self.assertIn("isn't one", err)                           # --fix only for failed events
        self.ok("combat", "heal", "thorin", "1", "--fix", "e0001")
        statuses = {e["id"]: e["status"] for e in audit.events(campaign.load())}
        self.assertEqual(statuses, {"e0001": "resolved", "e0002": "passed", "e0003": "fix"})
        self.ok("combat", "damage", "thorin", "1")                # unlocked
        self.assertIn("1 event(s) awaiting the post-audit", self.ok("memory", "brief"))

    def test_session_end_needs_a_clean_audit(self):
        self.ok("combat", "damage", "thorin", "2")
        self.ok("combat", "end")
        self.assertIn("unaudited", run_cli("session", "end", "--summary", "x")[2])
        self.ok("audit", "pass", "e1-e2")
        self.assertIn("closed", self.ok("session", "end", "--summary", "x"))

    def test_rules_audit_setting_is_gone(self):
        code, _, err = run_cli("campaign", "set", "rules_audit", "off")
        self.assertEqual(code, 1)
        self.assertIn("mandatory", err)
        self.assertNotIn("rules_audit", campaign.load().settings)

    # ------------------------------------------------------------------ hooks
    def test_hook_pretool(self):
        bash = lambda cmd, **kw: audit.hook_pretool({"tool_name": "Bash", "tool_input": {"command": cmd}, **kw})
        deny = lambda r: r["hookSpecificOutput"]["permissionDecision"] == "deny"
        self.assertTrue(deny(bash("./dm audit pass all")))
        self.assertTrue(deny(bash("cd x && PYTHONPATH=. python -m robodm audit clear spell Shield --verdict CORRECT")))
        self.assertIsNone(bash("./dm audit pass all", agent_type="rules-lawyer"))
        self.assertTrue(deny(bash("./dm audit pass all --self", agent_type="rules-lawyer")))
        self.assertTrue(deny(bash("./dm audit reset-cleared --yes", agent_type="rules-lawyer")))
        self.assertIsNone(bash("./dm audit pending"))
        self.assertIsNone(bash("./dm combat status"))
        self.assertTrue(deny(bash("python -c \"from robodm import audit; audit.mark(...)\"")))
        self.assertTrue(deny(bash("echo '{}' > campaigns/gate-camp/combat.json")))
        self.assertIsNone(bash("cat campaigns/gate-camp/combat.json"))
        edit = lambda path: audit.hook_pretool({"tool_name": "Edit", "tool_input": {"file_path": path}})
        self.assertTrue(deny(edit("C:\\repo\\campaigns\\gate-camp\\combat.json")))
        self.assertTrue(deny(edit("/repo/campaigns/gate-camp/party/mira.json")))
        self.assertTrue(deny(edit("/repo/campaigns/gate-camp/audit/events.jsonl")))
        self.assertIsNone(edit("/repo/campaigns/gate-camp/lore/npc/voss.md"))

    def test_hook_stop_and_prompt(self):
        camp = campaign.load()
        self.assertIsNone(audit.hook_stop(camp, {}))              # nothing to audit
        self.ok("combat", "damage", "thorin", "2")
        r = audit.hook_stop(camp, {"last_assistant_message": "Thorin takes 2 and grins."})
        self.assertEqual(r["decision"], "block")
        self.assertIn("./dm audit pending", r["reason"])
        self.assertIn("Thorin takes 2 and grins.", self.ok("audit", "pending"))
        self.assertEqual(audit.hook_stop(camp, {"stop_hook_active": True})["decision"], "block")
        self.assertIn("systemMessage", audit.hook_stop(camp, {"stop_hook_active": True}))   # gives up after 2
        r = audit.hook_prompt(camp, {"prompt": "I attack"})
        self.assertEqual(audit.current_turn(camp), 1)
        self.assertIn("still unaudited", r["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(audit.hook_stop(camp, {})["decision"], "block")   # new turn: blocks again
        self.assertIsNone(audit.hook_stop(None, {}))

    def hook(self, mode, payload) -> subprocess.CompletedProcess:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, "-m", "robodm", "hook", mode], input=text, capture_output=True,
                              text=True, encoding="utf-8", cwd=str(ROOT), env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                              timeout=60)

    def test_hook_command_end_to_end(self):
        self.ok("combat", "damage", "thorin", "2")
        self.assertEqual(json.loads(self.hook("stop", {"stop_hook_active": False}).stdout)["decision"], "block")
        r = self.hook("pretool", {"tool_name": "Bash", "tool_input": {"command": "./dm audit pass all"}})
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        r = self.hook("pretool", {"tool_name": "PowerShell", "tool_input": {"command": ".\\dm.cmd audit pass all"}})
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        r = self.hook("pretool", {"tool_name": "PowerShell",
                                  "tool_input": {"command": "Set-Content campaigns\\gate-camp\\combat.json '{}'"}})
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.hook("prompt", {"prompt": "hi"}).returncode, 0)
        self.assertEqual(audit.current_turn(campaign.load()), 1)
        r = self.hook("stop", "not json")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_hook_session_start(self):
        r = self.hook("session-start", {"source": "startup"})
        self.assertIn("RoboDM: Rules data: ready", r.stdout)
        self.assertIn("active: gate-camp", r.stdout)
        run_cli("session", "start")
        r = self.hook("session-start", {"source": "compact"})
        self.assertIn("a play session is in progress. Context was compacted", r.stdout)
        self.assertIn("# 📜 Gate Camp", r.stdout)              # the brief
        self.assertIn("Gate test — Round 1", r.stdout)         # combat status
        self.assertIn("Rules data", self.hook("session-start", "").stdout)   # no JSON: still reports status


class LauncherTests(RoboDMTestCase):
    """`./dm` (sh) and `dm.cmd` must find a Python that runs, even when `python3` is a broken placeholder."""

    def launch(self, cmd: list[str], fake: Path | None = None, **env) -> subprocess.CompletedProcess:
        e = dict(os.environ, **{"ROBODM_DM_CACHE": str(self.tmp / "dm-python"), **env})
        if fake is not None:
            e["PATH"] = os.pathsep.join([str(fake), e.get("PATH", "")])
        return subprocess.run(cmd + ["status"], capture_output=True, text=True, encoding="utf-8", cwd=str(self.tmp),
                              env=e, timeout=60)

    def fakebin(self, broken: list[str], working: list[str] = ()) -> Path:
        """A directory of fake interpreters: `broken` ones act like the Microsoft Store placeholder
        (print "Python was not found", exit 49); `working` ones forward to the Python running the tests."""
        d = self.tmp / "fakebin"
        d.mkdir(exist_ok=True)
        real = Path(sys.executable).as_posix()
        for name in broken:
            (d / name).write_text("#!/bin/sh\necho 'Python was not found' >&2\nexit 49\n", newline="\n")
            (d / f"{name}.cmd").write_text("@echo Python was not found 1>&2\r\n@exit /b 49\r\n", newline="")
            (d / name).chmod(0o755)
        for name in working:  # like the py launcher, accept and drop a leading -3
            (d / name).write_text(f'#!/bin/sh\n[ "$1" = "-3" ] && shift\nexec "{real}" "$@"\n', newline="\n")
            (d / f"{name}.cmd").write_text(f'@echo off\r\nif "%~1"=="-3" shift\r\n"{Path(sys.executable)}" '
                                           '%1 %2 %3 %4 %5 %6 %7 %8 %9\r\n', newline="")
            (d / name).chmod(0o755)
        return d

    def assert_ran(self, r: subprocess.CompletedProcess) -> None:
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RoboDM", r.stdout)
        self.assertIn("Rules data: ready", r.stdout)            # the test's ROBODM_DATA reached the engine

    @unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
    def test_sh_launcher_skips_broken_python3(self):
        sh = shutil.which("sh")
        fake = self.fakebin(broken=["python3"], working=["python"])
        self.assert_ran(self.launch([sh, str(ROOT / "dm")], fake, OS=""))              # tries python3 first
        self.assert_ran(self.launch([sh, str(ROOT / "dm")], fake, OS="Windows_NT"))    # tries python first
        self.assert_ran(self.launch([sh, str(ROOT / "dm")], ROBODM_PYTHON=Path(sys.executable).as_posix()))
        r = self.launch([sh, str(ROOT / "dm")], self.fakebin(broken=["python3", "python", "py"]), OS="",
                        ROBODM_PYTHON="", ROBODM_DM_CACHE=str(self.tmp / "fresh-cache"))
        if r.returncode != 0:                                   # unless a real python sits later on PATH
            self.assertIn("Microsoft Store placeholder", r.stderr)

    @unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
    def test_sh_launcher_remembers_its_python(self):
        sh, cache = shutil.which("sh"), self.tmp / "dm-python"
        fake = self.fakebin(broken=["python3"], working=["python"])
        self.assert_ran(self.launch([sh, str(ROOT / "dm")], fake, OS=""))
        remembered = cache.read_text(encoding="utf-8").splitlines()[0]
        self.assertTrue(remembered.endswith("fakebin/python"), remembered)
        self.assert_ran(self.launch([sh, str(ROOT / "dm")], fake, OS=""))              # reuses it
        cache.write_text("/nonexistent/python\n\n", encoding="utf-8")                # gone: probes again
        self.assert_ran(self.launch([sh, str(ROOT / "dm")], fake, OS=""))
        self.assertNotIn("nonexistent", cache.read_text(encoding="utf-8"))
        # the remembered interpreter turns into the Store placeholder: forgotten, and another one is found
        placeholder = self.tmp / "fakebin" / "python3"
        cache.write_text(f"{placeholder.as_posix()}\n\n", encoding="utf-8")
        self.assert_ran(self.launch([sh, str(ROOT / "dm")], fake, OS=""))
        self.assertTrue(cache.read_text(encoding="utf-8").splitlines()[0].endswith("fakebin/python"))

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_cmd_launcher_skips_broken_python(self):
        fake = self.fakebin(broken=["python", "python3"], working=["py"])
        self.assert_ran(self.launch(["cmd", "/c", str(ROOT / "dm.cmd")], fake))         # falls through to py -3
        self.assert_ran(self.launch(["cmd", "/c", str(ROOT / "dm.cmd")], ROBODM_PYTHON=sys.executable))


if __name__ == "__main__":
    unittest.main()
