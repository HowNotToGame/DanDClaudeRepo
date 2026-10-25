"""The web client: access control, player/DM filtering, the dice and command endpoints, events, and the chat
bridge (driven by a fake Claude Code that prints stream-json)."""

from __future__ import annotations

import http.client
import json
import os
import queue
import sys
import threading

from robodm import campaign, lore, memory
from robodm.web import bridge as bridge_mod, server
from tests.helpers import RoboDMTestCase
from tests.test_campaign_memory import run_cli

TOKEN = "test-token-123"

OKTON = """A fire giant smith who raised the boy.

## DM secrets
- He forged the crown and never told anyone."""

FAKE_CLAUDE = r'''
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"args": args, "prompt": prompt, "fg": os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"),
                         "web": os.environ.get("ROBODM_WEB_CLIENT")}) + "\n")
if os.environ.get("FAKE_NO_PARTIAL") and "--include-partial-messages" in args:
    sys.stderr.write("error: unknown option '--include-partial-messages'\n")
    sys.exit(1)
def out(o):
    print(json.dumps(o), flush=True)
sid = args[args.index("--resume") + 1] if "--resume" in args else "sess-1"
out({"type": "system", "subtype": "init", "session_id": sid})
if "--include-partial-messages" in args:
    out({"type": "stream_event", "event": {"type": "message_start"}})
    for chunk in ("The torches ", "gutter."):
        out({"type": "stream_event", "event": {"type": "content_block_delta",
                                               "delta": {"type": "text_delta", "text": chunk}}})
out({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": "./dm roll 1d20+5 --label Athletics"}},
    {"type": "text", "text": "The torches gutter."}]}})
out({"type": "assistant", "parent_tool_use_id": "t1", "message": {"content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": "./dm rules show Grappled"}},
    {"type": "text", "text": "lawyer chatter"}]}})
out({"type": "result", "total_cost_usd": 0.01, "duration_ms": 5, "num_turns": 2})
# the Stop hook sent the DM back to work (the post-audit): it adds a rules note, and the run reports again
if "--include-partial-messages" in args:
    out({"type": "stream_event", "event": {"type": "message_start"}})
    out({"type": "stream_event", "event": {"type": "content_block_delta",
                                           "delta": {"type": "text_delta", "text": "Rules check: fine."}}})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Rules check: fine."}]}})
out({"type": "result", "total_cost_usd": 0.02, "duration_ms": 7, "num_turns": 1})
'''


class WebTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ROBODM_AGENTS_DIR"] = str(self.tmp / "agents")
        self.assertEqual(run_cli("campaign", "new", "Ash", "--premise", "A city of cinders")[0], 0)
        run_cli("pc", "add", "Chadickith", "--class", "Barbarian", "--level", "3", "--str", "16", "--ac", "14")
        lore.add(campaign.load(), "Okton", "npc", OKTON, summary="Ancient fire giant smith")
        lore.add(campaign.load(), "Halda", "npc", "Watch captain.\n\n## DM secrets\n- Halda takes bribes.",
                 summary="Captain of the Watch")
        self.srv, self.hub = server.make_server("127.0.0.1", 0, TOKEN)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for k in ("ROBODM_AGENTS_DIR", "ROBODM_CLAUDE", "FAKE_CLAUDE_LOG", "FAKE_NO_PARTIAL"):
            os.environ.pop(k, None)
        super().tearDown()

    # ------------------------------------------------------------------ helpers
    def request(self, method, path, body=None, headers=None, token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"X-RoboDM-Token": TOKEN} if token else {}
        h.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        headers: dict[str, str] = {}
        for k, v in r.getheaders():                     # several Set-Cookie headers: keep them all
            headers[k.lower()] = f"{headers[k.lower()]}\n{v}" if k.lower() in headers else v
        return r.status, headers, raw

    def get(self, path, **kw):
        code, _, raw = self.request("GET", path, **kw)
        self.assertEqual(code, 200, raw[:300])
        return json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode("utf-8")

    def post(self, path, body, **kw):
        code, _, raw = self.request("POST", path, body, **kw)
        return code, json.loads(raw)

    def start_battle(self):
        for argv in (("combat", "start", "Yard"), ("combat", "party"), ("combat", "add", "mire imp"),
                     ("combat", "map", "new", "8x6"), ("combat", "place", "chadickith", "B2", "mire-imp", "E4")):
            code, out, err = run_cli(*argv)
            self.assertEqual(code, 0, f"{argv}: {out}{err}")

    # ------------------------------------------------------------------ access
    def test_token_host_and_origin_are_enforced(self):
        self.assertEqual(self.request("GET", "/api/state", token=False)[0], 401)
        self.assertEqual(self.request("GET", "/api/state?token=wrong", token=False)[0], 401)
        code, headers, _ = self.request("GET", f"/?token={TOKEN}", token=False)
        self.assertEqual(code, 200)
        self.assertIn(f"robodm_token={TOKEN}", headers["set-cookie"])
        self.assertIn("HttpOnly", headers["set-cookie"])
        self.assertEqual(self.request("GET", "/api/state", token=False,
                                      headers={"Cookie": f"robodm_token={TOKEN}"})[0], 200)
        # DNS rebinding: right token, but the page was loaded from someone else's hostname
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": f"evil.example:{self.port}"})[0], 401)
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": f"evil.example@127.0.0.1:{self.port}"})[0], 401)
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": f"[::1]:{self.port}"})[0], 200)
        code, body = self.post("/api/roll", {"expr": "1d4"}, headers={"Origin": "http://evil.example"})
        self.assertEqual(code, 403)
        code, body = self.post("/api/roll", {"expr": "1d4"}, headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(code, 200)
        self.assertEqual(self.request("GET", "/static/../server.py")[0], 404)
        self.assertEqual(self.request("GET", "/static/app.js")[0], 200)

    def test_commands_from_the_browser_skip_the_verdicts(self):
        code, body = self.post("/api/command", {"argv": "./dm audit pass e1"})
        self.assertEqual(code, 403)
        code, body = self.post("/api/command", {"argv": ["--quiet", "audit", "clear", "spell", "X"]})
        self.assertEqual(code, 403)
        self.assertEqual(self.post("/api/command", {"argv": ["campaign", "delete", "ash"]})[0], 403)
        self.assertEqual(self.post("/api/command", {"argv": ["-c", "ash", "audit", "pass", "all"]})[0], 403)
        self.assertEqual(self.post("/api/command", {"argv": ["audit", "--notes", "ok", "pass", "all"]})[0], 403)
        self.assertEqual(self.post("/api/command", {"argv": 5})[0], 400)
        code, body = self.post("/api/command", {"argv": ["pc", "show", "chadickith"]})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertIn("Chadickith", body["out"])

    def test_roll(self):
        self.rng.push(15)
        code, body = self.post("/api/roll", {"expr": "1d20+3", "label": "Athletics"})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertIn("18", body["text"])

    # ------------------------------------------------------------------ views
    def test_player_view_hides_monster_hp_and_the_dm_view_shows_it(self):
        self.start_battle()
        player = self.get("/api/state")
        dm = self.get("/api/state?view=dm")
        rows = {r["id"]: r for r in player["combat"]["order"]}
        self.assertIsNone(rows["mire-imp"]["hp"])
        self.assertIsNone(rows["mire-imp"]["ac"])
        self.assertEqual(rows["chadickith"]["pos"], "B2")
        self.assertIsNotNone(rows["chadickith"]["hp"])
        self.assertEqual({r["id"]: r for r in dm["combat"]["order"]}["mire-imp"]["hp"], 10)
        self.assertEqual(player["party"][0]["name"], "Chadickith")
        self.assertEqual(player["campaign"]["name"], "Ash")
        svg = self.get("/api/map.svg")
        self.assertTrue(svg.lstrip().startswith("<svg"))
        self.assertIn("Mire Imp", svg)
        self.assertNotIn("HP 10/10", svg)
        self.assertIn("HP 10/10", self.get("/api/map.svg?view=dm"))

    def test_lore_and_companions_keep_secrets_out_of_the_player_view(self):
        self.assertNotIn("forged the crown", self.get("/api/lore/okton")["body"])
        self.assertIn("forged the crown", self.get("/api/lore/okton?view=dm")["body"])
        self.assertEqual(run_cli("companion", "new", "Okton", "--from-lore", "okton", "--status", "party")[0], 0)
        run_cli("companion", "attitude", "okton", "chadickith", "2")
        run_cli("companion", "attitude", "okton", "halda", "-2")
        card = self.get("/api/companion/okton")
        self.assertEqual(card["public"], "Ancient fire giant smith")
        self.assertEqual(card["mood"], {"Chadickith": "warm"})                 # not how he feels about Halda
        self.assertNotIn("persona", card)
        dm = self.get("/api/companion/okton?view=dm")
        self.assertIn("Halda", dm["mood"])
        self.assertIn("forged the crown", dm["persona"])

    def test_world_view_keeps_hidden_clocks_minds_and_beats_for_the_dm(self):
        for argv in (("time", "set", "11", "18:00"),
                     ("clock", "add", "Lorard's ultimatum", "--deadline", "+2d", "--public"),
                     ("clock", "add", "The Choir's ritual", "--segments", "6", "--on-fill", "the Ash-Wyrm wakes"),
                     ("lore", "mind", "halda", "--plan", "frame the treasurer"),
                     ("scene", "set", "Watch house", "--present", "halda"),
                     ("time", "advance", "4h")):
            code, out, err = run_cli(*argv)
            self.assertEqual(code, 0, f"{argv}: {out}{err}")
        player = self.get("/api/state")["world"]
        dm = self.get("/api/state?view=dm")["world"]
        self.assertEqual(player["day"], 11)
        self.assertEqual(player["word"], "night")
        self.assertEqual([c["name"] for c in player["clocks"]], ["Lorard's ultimatum"])
        self.assertEqual(len(dm["clocks"]), 2)
        self.assertEqual(dm["clocks"][1]["on_fill"], "the Ash-Wyrm wakes")
        self.assertNotIn("on_fill", player["clocks"][0])
        self.assertEqual(player["cast"][0]["name"], "Halda")
        self.assertNotIn("mind", player["cast"][0])
        self.assertNotIn("beats", player)
        self.assertNotIn("treasurer", json.dumps(player))
        self.assertIn("frame the treasurer", dm["cast"][0]["mind"])

    def test_recall_hides_secrets_and_dm_notes_from_players(self):
        camp = campaign.load()
        memory.add(camp, "The cinder bell rings at midnight in the old forge", "event")
        memory.add(camp, "The cinder bell is really a cage for a fire spirit", "secret")
        memory.add(camp, "Chadickith does not know the cinder bell holds his mother", "npc")
        player = " ".join(h["snippet"] + h.get("title", "") for h in self.get("/api/recall?q=cinder+bell")["hits"])
        dm = " ".join(h["snippet"] + h.get("title", "") for h in self.get("/api/recall?q=cinder+bell&view=dm")["hits"])
        self.assertIn("midnight", player)
        self.assertNotIn("cage", player)
        self.assertNotIn("mother", player)
        self.assertIn("cage", dm)
        self.assertEqual(self.get("/api/recall")["hits"], [])
        self.assertIn("A city of cinders", self.get("/api/recap")["text"])

    # ------------------------------------------------------------------ events
    def test_events_stream_says_hello_then_relays(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/api/events", headers={"X-RoboDM-Token": TOKEN})
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.fp.readline().strip(), b"event: hello")
        r.fp.readline(), r.fp.readline()
        for _ in range(50):                                   # wait for the subscription to register
            if self.hub.clients:
                break
            threading.Event().wait(0.02)
        self.hub.publish("changed", {"at": 1})
        self.assertEqual(r.fp.readline().strip(), b"event: changed")
        self.assertEqual(json.loads(r.fp.readline().decode().split(":", 1)[1]), {"at": 1})
        conn.close()

    # ------------------------------------------------------------------ the DM bridge
    def fake_claude(self):
        script = self.tmp / "fake_claude.py"
        script.write_text(FAKE_CLAUDE, encoding="utf-8")
        log = self.tmp / "fake_claude.log"
        os.environ["ROBODM_CLAUDE"] = f'"{sys.executable}" "{script}"'.replace("\\", "/")
        os.environ["FAKE_CLAUDE_LOG"] = str(log)
        return log

    def run_turn(self, br, events, text, as_pc=""):
        self.assertTrue(br.send("ash", text, as_pc))
        got = []
        while True:
            ev = events.get(timeout=30)
            got.append(ev)
            if ev["kind"] == "idle":
                return got

    def test_a_revised_reply_replaces_the_draft(self):
        from robodm import audit

        self.assertEqual(bridge_mod.compose(["Insight 6 against her 5. She is afraid.", "[REVISED]\nShe is afraid.",
                                             "Rules check: fine."]), "She is afraid.\n\nRules check: fine.")
        self.assertEqual(bridge_mod.compose(["The gate holds.", "[KEEP]"]), "The gate holds.")
        self.assertEqual(bridge_mod.compose(["The gate holds.", "", "Rules check: fine."]),
                         "The gate holds.\n\nRules check: fine.")
        os.environ["ROBODM_WEB_CLIENT"] = "1"
        try:
            camp = campaign.load()
            audit.bump_turn(camp)
            audit.record_event(camp, "./dm pc check chadickith athletics --dc 10", ["🎲 Athletics 12 vs DC 10 → success"])
            block = audit.hook_stop(campaign.load(), {"last_assistant_message": "You heave the gate open."})
            self.assertEqual(block["decision"], "block")
            self.assertIn("[REVISED]", block["reason"])
            self.assertIn("foreground", block["reason"])
        finally:
            os.environ.pop("ROBODM_WEB_CLIENT", None)

    def test_bridge_holds_the_reply_until_the_turn_is_done_and_resumes(self):
        log = self.fake_claude()
        events: queue.Queue = queue.Queue()
        br = bridge_mod.Bridge(events.put)
        self.assertTrue(br.status("ash")["available"])
        got = self.run_turn(br, events, "I shove the guard", as_pc="Chadickith")
        kinds = [e["kind"] for e in got]
        self.assertEqual(kinds[0], "start")
        self.assertEqual([e for e in got if e["kind"] in ("delta", "replace")], [])     # no drafts on the page
        tools = [e for e in got if e["kind"] == "tool"]
        self.assertEqual((tools[0]["icon"], tools[0]["sub"]), ("🎲", False))
        self.assertEqual((tools[1]["icon"], tools[1]["sub"]), ("⚖️", True))       # the lawyer's lookup, nested
        self.assertNotIn("lawyer chatter", json.dumps(got))
        self.assertEqual([e for e in got if e["kind"] == "done"][0]["text"], "The torches gutter.\n\nRules check: fine.")
        metas = [e for e in got if e["kind"] == "meta"]
        self.assertEqual([(m["cost"], m["ms"], m["turns"]) for m in metas], [(0.02, 12, 3)])   # one chip per turn
        self.assertLess(kinds.index("done"), kinds.index("meta"))
        self.assertEqual(br.session_id("ash"), "sess-1")
        self.assertEqual([m["role"] for m in br.history("ash")], ["player", "dm"])
        self.assertEqual(br.history("ash")[0]["text"], "Chadickith: I shove the guard")

        self.run_turn(br, events, "I look around")
        calls = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
        self.assertIn("[RoboDM web client]", calls[0]["prompt"])
        self.assertEqual((calls[0]["fg"], calls[0]["web"]), ("1", "1"))      # subagents in the foreground
        # the transcript hook records only the player's words, not the preamble
        self.assertEqual(bridge_mod.player_text(calls[0]["prompt"]), "Chadickith: I shove the guard")
        self.assertEqual(bridge_mod.player_text("[RoboDM web client] Blah blah. Campaign: ash. I hide"), "I hide")
        self.assertEqual(bridge_mod.player_text("I look around"), "I look around")
        self.assertNotIn("--resume", calls[0]["args"])
        self.assertEqual(calls[1]["args"][calls[1]["args"].index("--resume") + 1], "sess-1")
        self.assertEqual(calls[1]["prompt"], "I look around")
        br.reset("ash")
        self.assertIsNone(br.session_id("ash"))

    def test_bridge_falls_back_when_partial_messages_are_unsupported(self):
        self.fake_claude()
        os.environ["FAKE_NO_PARTIAL"] = "1"
        events: queue.Queue = queue.Queue()
        br = bridge_mod.Bridge(events.put)
        got = self.run_turn(br, events, "Hello")
        self.assertFalse(br.partial_ok)
        self.assertNotIn("error", [e["kind"] for e in got])
        self.assertEqual([e for e in got if e["kind"] == "done"][-1]["text"], "The torches gutter.\n\nRules check: fine.")

    def test_chat_endpoint_without_claude_explains_how_to_install(self):
        os.environ["ROBODM_CLAUDE"] = str(self.tmp / "missing-claude.exe")
        state = self.get("/api/state")
        self.assertIn("available", state["dm"])
        self.assertEqual(self.post("/api/chat", {"message": "  "})[0], 400)

    def test_classify(self):
        c = bridge_mod.classify
        self.assertEqual(c("Bash", {"command": "./dm combat attack chadickith mire-imp"})[0], "⚔️")
        self.assertEqual(c("Bash", {"command": "python -m robodm companion brief okton"})[0], "🗣")
        self.assertEqual(c("Bash", {"command": "./dm memory add 'x'"})[0], "🧠")
        self.assertEqual(c("Agent", {"subagent_type": "rules-lawyer", "description": "post-audit"}),
                         ("⚖️", "consulting rules-lawyer: post-audit"))
        self.assertEqual(c("Agent", {"subagent_type": "companion-okton"})[0], "🗣")
        self.assertEqual(c("Skill", {"skill": "dm"}), ("📜", "skill dm"))
