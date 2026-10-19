"""Local DM engines: Ollama (/api/chat) and OpenAI-compatible servers like KoboldCpp, against fake servers that
speak the real wire formats. Covers native tool calls, tool calls written as text, the <dm> text protocol, the
fallback when tools are unsupported, pre-checks, the receipt rewrite, the local post-audit and the bridge."""

from __future__ import annotations

import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from robodm import audit, campaign, capture, engines
from robodm.engines import llm, local
from robodm.web.bridge import Bridge
from tests.helpers import RoboDMTestCase
from tests.test_campaign_memory import run_cli


class FakeLLM:
    """A model server. `script` is a list of replies, one per call: {"content": str, "tool_calls": [(name, args)],
    "status": int, "error": str}. Requests are kept in `requests`."""

    def __init__(self, flavor: str):
        self.flavor = flavor
        self.script: list[dict] = []
        self.requests: list[dict] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = {"models": [{"name": "llama3.1:8b"}]} if self.path == "/api/tags" else \
                    {"data": [{"id": "koboldcpp/Llama-3.1-8B"}]}
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(req)
                step = fake.script.pop(0) if fake.script else {"content": "The torchlight flickers. What do you do?"}
                if step.get("status"):
                    raw = json.dumps({"error": step["error"]}).encode()
                    self.send_response(step["status"])
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                self.send_response(200)
                self.end_headers()
                text = step.get("content", "")
                calls = step.get("tool_calls", [])
                if fake.flavor == "ollama":
                    for i in range(0, len(text), 12):
                        self.wfile.write((json.dumps({"message": {"role": "assistant", "content": text[i:i + 12]},
                                                      "done": False}) + "\n").encode())
                    if calls:
                        self.wfile.write((json.dumps({"message": {"role": "assistant", "content": "", "tool_calls": [
                            {"function": {"name": n, "arguments": a}} for n, a in calls]}, "done": False}) + "\n").encode())
                    self.wfile.write((json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                                                  "eval_count": 40, "prompt_eval_count": 900}) + "\n").encode())
                else:
                    for i in range(0, len(text), 12):
                        self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': text[i:i + 12]}}]})}\n\n".encode())
                    for idx, (n, a) in enumerate(calls):
                        arg = json.dumps(a)
                        first = {"choices": [{"delta": {"tool_calls": [{"index": idx, "id": f"c{idx}", "function": {
                            "name": n, "arguments": arg[:10]}}]}}]}
                        rest = {"choices": [{"delta": {"tool_calls": [{"index": idx, "function": {"arguments": arg[10:]}}]}}]}
                        self.wfile.write(f"data: {json.dumps(first)}\n\ndata: {json.dumps(rest)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class EngineTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        run_cli("campaign", "new", "Local", "--premise", "A keep under siege")
        run_cli("pc", "add", "Brann", "--class", "Fighter", "--level", "3", "--str", "16", "--skills", "athletics")
        run_cli("session", "start")
        self.events: list[dict] = []
        self.fakes: list[FakeLLM] = []

    def tearDown(self):
        for f in self.fakes:
            f.close()
        super().tearDown()

    def fake(self, flavor="ollama", **sub) -> FakeLLM:
        f = FakeLLM(flavor)
        self.fakes.append(f)
        name = "ollama" if flavor == "ollama" else "kobold"
        engines.update({"engine": name, name: {"url": f.url, "model": "llama3.1:8b", **sub}})
        return f

    def dm(self, message: str, private_to=None) -> str:
        self.events = []
        return local.LocalDM(engines.load(), self.events.append).turn("local", message, private_to)

    def kinds(self, kind):
        return [e for e in self.events if e["kind"] == kind]

    def test_native_tool_calls_with_ollama(self):
        f = self.fake("ollama")
        self.rng.push(14)                                                   # 14 + 5 = 19
        f.script = [{"tool_calls": [("dm", {"command": "pc check brann athletics --dc 15"})]},
                    {"content": "🎲 Athletics 19 vs DC 15 — success. The gate groans open. What do you do?"}]
        reply = self.dm("Brann: I force the gate.")
        self.assertIn("gate groans open", reply)
        req = f.requests[0]
        self.assertEqual(req["tools"][0]["function"]["name"], "dm")
        self.assertEqual(req["options"]["num_ctx"], 12288)
        self.assertIn("=== CAMPAIGN BRIEF ===", req["messages"][0]["content"])
        self.assertEqual(f.requests[1]["messages"][-1]["role"], "tool")
        self.assertIn("vs DC 15", f.requests[1]["messages"][-1]["content"])
        self.assertTrue(self.kinds("delta"))
        self.assertEqual(self.kinds("tool")[0]["icon"], "🎲")
        camp = campaign.load()
        self.assertEqual([e["status"] for e in audit.events(camp)], ["passed"])        # routine: auto-passed
        rows = capture.entries(camp)
        self.assertEqual([r[1] for r in rows], ["Player", "DM"])                      # transcript, like the hooks
        self.assertEqual(len(f.requests), 2)                                          # no rules-lawyer call needed

    def test_tool_calls_as_text_and_the_text_protocol(self):
        f = self.fake("ollama")
        self.rng.push(10)
        f.script = [{"content": '{"name": "dm", "parameters": {"command": "roll 1d20+2"}}'},
                    {"content": "🎲 12. The die settles. What do you do?"}]
        reply = self.dm("Roll for me")
        self.assertNotIn('"name"', reply)
        self.assertIn("1d20+2", f.requests[1]["messages"][-1]["content"])
        engines.update({"ollama": {"tool_mode": "text"}})
        self.rng.push(3)
        f.script = [{"content": "Let me see.\n<dm>roll 1d6</dm>"}, {"content": "You rolled low. What now?"}]
        self.dm("Again")
        self.assertNotIn("tools", f.requests[2])
        self.assertIn("TOOL FORMAT", f.requests[2]["messages"][0]["content"])
        self.assertIn("Tool results", f.requests[3]["messages"][-1]["content"])

    def test_fallback_when_tools_are_unsupported(self):
        f = self.fake("ollama")
        f.script = [{"status": 400, "error": "registry.ollama.ai/library/tinyllama does not support tools"},
                    {"content": "Quiet night. What do you do?"}]
        reply = self.dm("We camp.")
        self.assertIn("Quiet night", reply)
        self.assertIn("TOOL FORMAT", f.requests[1]["messages"][0]["content"])
        self.assertIn("text protocol", " ".join(e["label"] for e in self.kinds("tool")))

    def test_koboldcpp_openai_streaming_tool_calls(self):
        f = self.fake("kobold")
        self.rng.push(14)
        f.script = [{"tool_calls": [("dm", {"command": "pc check brann athletics --dc 15"})]},
                    {"content": "🎲 Athletics 19 vs DC 15 — success. It opens."}]
        self.assertEqual(engines.list_models(engines.load()), ["koboldcpp/Llama-3.1-8B"])
        reply = self.dm("Brann: I force the gate.")
        self.assertIn("It opens", reply)
        self.assertEqual(f.requests[1]["messages"][-1]["role"], "tool")
        self.assertEqual(f.requests[1]["messages"][-2]["tool_calls"][0]["function"]["name"], "dm")

    def test_receipt_rewrite_precheck_denials_and_post_audit(self):
        f = self.fake("ollama")
        self.rng.push(2)                                                    # 2 + 5 = 7: a failure
        f.script = [{"tool_calls": [("dm", {"command": "pc check brann athletics --dc 15"}),
                                    ("dm", {"command": "audit pass all"})]},
                    {"content": "🎲 Athletics 18 vs DC 15 — success!"},
                    {"content": "🎲 Athletics 7 vs DC 15 — failure. The gate holds. What now?"}]
        reply = self.dm("Brann: I force the gate.")
        self.assertIn("failure", reply)
        self.assertEqual(self.kinds("replace")[0]["text"], reply)
        self.assertIn("isn't available to the DM", f.requests[1]["messages"][-1]["content"])
        # a first-use ruling: the local rules-lawyer pre-checks it, then the command runs; the post-audit fails it
        f.script = [{"tool_calls": [("dm", {"command": 'ruling "Brann can climb the wall one-handed" --key climb'})]},
                    {"content": '{"verdict": "CORRECT", "notes": "Athletics check needed", "cite": "PHB p.175"}'},
                    {"content": "Brann climbs. What do you do?"},
                    {"content": json.dumps({"verdicts": [{"id": "e0002", "verdict": "fail",
                                                         "notes": "climbing one-handed needs a DC 15 Athletics check",
                                                         "fix": "pc check brann athletics --dc 15"}]})}]
        self.rng.push(15)
        reply = self.dm("Brann: I climb the wall one-handed.")
        camp = campaign.load()
        self.assertEqual(audit.cleared(camp)["ruling:climb"]["by"], "local rules-lawyer (llama3.1:8b)")
        self.assertIn("⚖️ Rules check: climbing one-handed needs a DC 15 Athletics check", reply)
        statuses = {e["id"]: e["status"] for e in audit.events(camp)}
        self.assertEqual(statuses["e0002"], "resolved")                     # fixed by the rules-lawyer's command
        self.assertEqual(audit.open_events(camp), [])
        self.assertEqual(sum(1 for r in f.requests if r.get("format") == "json"), 2)   # pre-check + post-audit

    def test_unreadable_audit_is_unverified_not_passed(self):
        f = self.fake("ollama")
        run_cli("audit", "clear", "ruling", "jump", "--verdict", "CORRECT", "--notes", "ok")
        f.script = [{"tool_calls": [("dm", {"command": 'ruling "Brann jumps the gap" --key jump'})]},
                    {"content": "Brann leaps. What now?"}, {"content": "I think it's fine?"}]
        self.dm("Brann: I jump the gap.")
        self.assertEqual([e["status"] for e in audit.events(campaign.load())], ["unverified"])

    def test_bridge_runs_the_local_engine(self):
        f = self.fake("ollama")
        f.script = [{"content": "Rain on the battlements. What do you do?"}]
        q: queue.Queue = queue.Queue()
        br = Bridge(q.put)
        st = br.status("local")
        self.assertEqual((st["available"], st["engine"], st["model"]), (True, "ollama", "llama3.1:8b"))
        self.assertTrue(br.send("local", "Where are we?", "Brann"))
        got = []
        while True:
            ev = q.get(timeout=20)
            got.append(ev)
            if ev["kind"] == "idle":
                break
        self.assertEqual([e["kind"] for e in got][0], "start")
        self.assertIn("Rain on the battlements", next(e["text"] for e in got if e["kind"] == "done"))
        self.assertEqual(next(e for e in got if e["kind"] == "meta")["engine"], "ollama")
        self.assertEqual(br.history("local")[-1]["engine"], "ollama")
        f.close()
        self.fakes.remove(f)
        engines._REACH.clear()
        self.assertFalse(br.status("local")["available"])
        self.assertIn("ollama pull", br.status("local")["help"])

    def test_settings_api_and_engine_command(self):
        import http.client

        from robodm.web import server, table

        f = self.fake("ollama")
        srv, hub = server.make_server("127.0.0.1", 0, "tok")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def req(method, path, body=None, seat=None):
                conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
                h = {"Cookie": f"robodm_seat={seat}.{table.seat_key('tok', seat)}"} if seat else {"X-RoboDM-Token": "tok"}
                data = json.dumps(body).encode() if body is not None else None
                if data:
                    h["Content-Type"] = "application/json"
                conn.request(method, path, body=data, headers=h)
                r = conn.getresponse()
                out = json.loads(r.read())
                conn.close()
                return r.status, out

            code, st = req("GET", "/api/settings")
            self.assertEqual((code, st["engine"]["engine"], st["reachable"]), (200, "ollama", True))
            self.assertEqual(st["models"], ["llama3.1:8b"])
            self.assertEqual(st["campaign"]["turn_speed"], "balanced")
            self.assertEqual(req("GET", "/api/settings", seat="brann")[0], 403)          # players can't touch it
            self.assertEqual(req("POST", "/api/settings", {"engine": {"engine": "claude"}}, seat="brann")[0], 403)
            code, st = req("POST", "/api/settings", {"engine": {"engine": "claude", "effort": "high",
                                                                "claude": {"model": "opus"}},
                                                     "campaign": {"turn_speed": "fast", "hide_monster_hp": False}})
            self.assertEqual((st["engine"]["engine"], st["engine"]["effort"], st["engine"]["claude"]["model"]),
                             ("claude", "high", "opus"))
            self.assertEqual(campaign.load().settings["turn_speed"], "fast")
            self.assertIs(campaign.load().settings["hide_monster_hp"], False)
            code, m = req("GET", f"/api/settings/models?engine=ollama&url={f.url}")
            self.assertEqual(m["models"], ["llama3.1:8b"])
        finally:
            srv.shutdown()
            srv.server_close()
        code, out, _ = run_cli("engine", "set", "ollama", "--model", "llama3.1:8b", "--effort", "low", "--ctx", "8192")
        self.assertEqual(code, 0)
        self.assertIn("✔ reachable", out)
        self.assertEqual(engines.load()["ollama"]["num_ctx"], 8192)
        self.assertEqual(engines.load()["effort"], "low")

    def test_settings_validation(self):
        self.assertRaises(Exception, engines.update, {"engine": "gpt5"})
        self.assertRaises(Exception, engines.update, {"effort": "turbo"})
        self.assertRaises(Exception, engines.update, {"ollama": {"num_ctx": 100}})
        self.assertRaises(Exception, engines.update, {"claude": {"model": "llama"}})
        cfg = engines.update({"engine": "openai", "openai": {"api_key": "sk-secret", "url": "http://x:1/v1/"}})
        self.assertEqual(cfg["openai"]["url"], "http://x:1/v1")
        self.assertEqual(engines.public(cfg)["openai"]["api_key"], "••••")
        engines.update({"openai": {"api_key": "••••"}})                     # the mask coming back changes nothing
        self.assertEqual(engines.load()["openai"]["api_key"], "sk-secret")
