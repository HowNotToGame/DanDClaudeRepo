"""Phase D: fog of war, player seats, private messages and whispers, round mode, click-to-move previews."""

from __future__ import annotations

import http.client
import json
import os
import threading

from robodm import battlemap as bm, campaign, capture, hooks, whispers
from robodm.web import server, table, views
from tests.helpers import RoboDMTestCase
from tests.test_campaign_memory import run_cli

TOKEN = "table-token-xyz"
ROOMS = """\
.....#.....
.....#.....
.....+.....
.....#....."""


def creature(cid, name, side, pos, kind="monster"):
    return {"id": cid, "name": name, "side": side, "pos": list(pos), "size": "M", "conditions": [], "kind": kind,
            "hp": 7, "max_hp": 7, "status": "ok"}


class FogTests(RoboDMTestCase):
    def test_walls_doors_and_darkness(self):
        m = bm.from_text(ROOMS)
        m["fog"] = {"on": True, "light": "bright", "revealed": []}
        cs = {"pc": creature("pc", "Mira", "party", (1, 1), "pc"), "gob": creature("gob", "Goblin", "enemy", (8, 2))}
        seen = bm.visible_cells(m, cs)
        self.assertIn((5, 1), seen)                                   # the wall itself
        self.assertFalse(any(x > 5 for x, _ in seen))                 # nothing past it
        self.assertFalse(bm.seen_by_party(cs["gob"], {"visible": seen}))
        m["rows"][2] = "....." + "/" + "....."                        # open the door
        seen = bm.visible_cells(m, cs)
        self.assertIn((8, 2), seen)
        self.assertTrue(bm.seen_by_party(cs["gob"], {"visible": seen}))
        m["fog"]["light"] = "dark"
        self.assertEqual(bm.visible_cells(m, cs, lambda c: 0), {(1, 1)})
        self.assertEqual(len(bm.visible_cells(m, cs, lambda c: 5)), 9)
        bm.update_fog(m, cs, lambda c: 5)
        fog = bm.fog_view(m, cs, lambda c: 5)
        text = bm.render_text(m, cs, reveal=False, fog=fog)
        self.assertIn("░░", text)
        self.assertNotIn("Goblin", text)
        self.assertIn("Goblin", bm.render_text(m, cs, reveal=True, fog=fog))          # the DM sees it
        svg = bm.render_svg(m, cs, reveal=True, fog=fog, player=True)                  # monster HP shown, still fogged
        self.assertNotIn("Goblin", svg)
        self.assertIn('fill="#0c0a09"', svg)

    def test_fog_commands_and_the_player_initiative_list(self):
        run_cli("campaign", "new", "Fog", "--premise", "test")
        run_cli("pc", "add", "Mira", "--class", "Wizard", "--level", "3", "--dex", "14")
        for argv in (("combat", "start", "Rooms"), ("combat", "party"), ("combat", "add", "mire imp"),
                     ("combat", "map", "draw", "--text", ROOMS.replace("\n", "\\n")),
                     ("combat", "place", "mira", "B2", "mire-imp", "I2"), ("combat", "map", "fog", "on")):
            code, out, err = run_cli(*argv)
            self.assertEqual(code, 0, f"{argv}: {out}{err}")
        camp = campaign.load()
        player = views.combat_view(camp, dm=False)
        self.assertEqual([r["name"] for r in player["order"]], ["Mira"])               # the imp is behind the wall
        self.assertEqual(len(views.combat_view(camp, dm=True)["order"]), 2)
        self.assertEqual(player["geometry"]["cell"], bm.SVG_CELL)
        code, out, _ = run_cli("combat", "map", "show", "--players")
        self.assertNotIn("Mire Imp", out)
        self.assertEqual(run_cli("combat", "map", "reveal", "all")[0], 0)
        self.assertEqual(run_cli("combat", "map", "fog", "dark")[0], 0)
        run_cli("combat", "vision", "mira", "60")
        self.assertEqual(run_cli("combat", "map", "fog", "off")[0], 0)
        self.assertEqual(len(views.combat_view(campaign.load(), dm=False)["order"]), 2)


class SeatTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        os.environ["ROBODM_AGENTS_DIR"] = str(self.tmp / "agents")
        run_cli("campaign", "new", "Table", "--premise", "test")
        run_cli("pc", "add", "Mira", "--class", "Wizard", "--level", "3", "--dex", "14")
        run_cli("pc", "add", "Brann", "--class", "Fighter", "--level", "3", "--str", "16")
        run_cli("pc", "item", "brann", "add", "Secret letter")
        run_cli("session", "start")
        self.srv, self.hub = server.make_server("127.0.0.1", 0, TOKEN)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        os.environ.pop("ROBODM_AGENTS_DIR", None)
        super().tearDown()

    def req(self, method, path, body=None, seat=None, host=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {}
        if host:
            headers["X-RoboDM-Token"] = TOKEN
        if seat:
            headers["Cookie"] = f"robodm_seat={seat}.{table.seat_key(TOKEN, seat)}"
        data = json.dumps(body).encode() if body is not None else None
        if data:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        raw = r.read()
        hdrs = r.getheaders()
        conn.close()
        try:
            return r.status, json.loads(raw), hdrs
        except ValueError:
            return r.status, raw.decode("utf-8", "replace"), hdrs

    def test_join_links_and_what_a_seat_sees(self):
        key = table.seat_key(TOKEN, "mira")
        code, _, hdrs = self.req("GET", f"/?seat=mira&key={key}")
        self.assertEqual(code, 200)
        cookies = [v for k, v in hdrs if k.lower() == "set-cookie"]
        self.assertTrue(any(c.startswith(f"robodm_seat=mira.{key}") for c in cookies))
        self.assertTrue(any(c.startswith("robodm_token=;") for c in cookies))          # not the host on this browser
        self.assertEqual(self.req("GET", "/?seat=mira&key=forged")[0], 401)
        self.assertEqual(self.req("GET", "/api/state", seat="nobody")[0], 401)
        code, st, _ = self.req("GET", "/api/state?view=dm", seat="mira")
        self.assertEqual(st["view"], "player")                                          # never the DM view
        self.assertFalse(st["host"])
        self.assertEqual(st["seat"]["name"], "Mira")
        sheets = {p["name"]: p for p in st["party"]}
        self.assertFalse(sheets["Mira"].get("public"))
        self.assertTrue(sheets["Brann"]["public"])
        self.assertEqual(sheets["Brann"]["inventory"], [])                              # not their secret letter
        self.assertEqual(self.req("GET", "/api/seats", seat="mira")[0], 403)
        code, seats, _ = self.req("GET", "/api/seats", host=True)
        self.assertEqual({s["slug"]: s["key"] for s in seats["seats"]}["brann"], table.seat_key(TOKEN, "brann"))

    def test_what_a_seat_may_do(self):
        self.assertEqual(self.req("POST", "/api/roll", {"expr": "1d20"}, seat="mira")[0], 200)
        self.assertEqual(self.req("POST", "/api/command", {"argv": ["pc", "show", "mira"]}, seat="mira")[0], 200)
        self.assertEqual(self.req("POST", "/api/command", {"argv": ["pc", "hp", "brann", "-5"]}, seat="mira")[0], 403)
        self.assertEqual(self.req("POST", "/api/command", {"argv": ["undo"]}, seat="mira")[0], 403)
        self.assertEqual(self.req("POST", "/api/round", {"action": "start"}, seat="mira")[0], 403)
        self.assertEqual(self.req("POST", "/api/chat/reset", {}, seat="mira")[0], 403)
        self.assertEqual(self.req("POST", "/api/whisper", {"to": "brann", "text": "psst"}, seat="mira")[0], 403)

    def test_private_messages_reach_only_their_seat(self):
        got = {"mira": [], "brann": [], None: []}
        qs = {k: self.hub.subscribe(k) for k in got}
        self.hub.publish("chat", {"kind": "delta", "text": "secret", "audience": ["mira"]})
        self.hub.publish("chat", {"kind": "idle"})
        for k, q in qs.items():
            while not q.empty():
                got[k].append(q.get()[1].get("text"))
        self.assertEqual(got["mira"], ["secret", None])
        self.assertEqual(got[None], ["secret", None])                    # the host sees everything
        self.assertEqual(got["brann"], [None])                           # only the idle
        # the transcript marks it, and player-facing views leave it out for everyone else
        camp = campaign.load()
        hooks.prompt(camp, {"prompt": "[Private from Mira — only Mira and the DM see this exchange; your reply is "
                                      "shown only to Mira.] I pocket the ruby"})
        hooks.stop(campaign.load(), {"last_assistant_message": "Nobody saw you take it."})
        rows = capture.entries(campaign.load())
        self.assertTrue(rows[-2][2].startswith("🔒 [private Mira→DM] I pocket the ruby"))
        self.assertTrue(rows[-1][2].startswith("🔒 [private DM→Mira] Nobody saw"))
        camp = campaign.load()
        self.assertEqual(len(views.journal(camp, "player", seat_name="Mira")["transcript"]), 2)
        self.assertEqual(views.journal(camp, "player", seat_name="Brann")["transcript"], [])
        self.assertEqual(len(views.journal(camp, "dm")["transcript"]), 2)
        self.assertNotIn("ruby", views.recap(camp, "player"))
        self.assertEqual([h for h in views.search(camp, ["ruby pocket"], "player") if h["kind"] == "transcript"], [])

    def test_whispers_and_chat_history(self):
        code, out, _ = run_cli("whisper", "brann", "The letter is a forgery.")
        self.assertEqual(code, 0)
        code, st, _ = self.req("GET", "/api/state", seat="brann")
        self.assertEqual([w["text"] for w in st["whispers"]], ["The letter is a forgery."])
        code, st, _ = self.req("GET", "/api/state", seat="mira")
        self.assertEqual(st["whispers"], [])
        self.assertIn("forgery", run_cli("whisper", "list")[1])
        br = self.hub.bridge
        br._log("table", {"role": "player", "text": "[Private from Mira …] hi", "private_to": "mira", "ts": "t"})
        br._log("table", {"role": "player", "text": "Brann: I open the door", "ts": "t"})
        self.assertEqual(len(br.history("table", seat="mira", host=False)), 2)
        self.assertEqual(len(br.history("table", seat="brann", host=False)), 1)

    def test_round_mode_gathers_then_sends_one_message(self):
        sent = []
        self.hub.bridge.send = lambda slug, text, as_pc="", private_to=None, ooc=False, raw=False: sent.append(text) or True
        self.assertEqual(self.req("POST", "/api/round", {"action": "start"}, host=True)[0], 200)
        code, r, _ = self.req("POST", "/api/round/declare", {"text": "I cast Shield on Brann"}, seat="mira")
        self.assertEqual(r["round"]["waiting"], ["Brann"])
        self.assertEqual(sent, [])
        code, r, _ = self.req("POST", "/api/round/declare", {"text": "I charge the ogre"}, seat="brann")
        self.assertEqual(len(sent), 1)                                   # everyone declared: off to the DM
        self.assertIn("- Mira: I cast Shield on Brann", sent[0])
        self.assertIn("- Brann: I charge the ogre", sent[0])
        self.assertEqual(r["round"]["actions"], {})                      # a fresh round is gathering
        self.req("POST", "/api/round", {"action": "stop"}, host=True)
        self.assertFalse(table.round_state(campaign.load())["active"])

    def test_click_to_move_preview(self):
        for argv in (("combat", "start", "Yard"), ("combat", "party"), ("combat", "add", "mire imp"),
                     ("combat", "map", "new", "8x6"), ("combat", "place", "mira", "A1", "brann", "B1", "mire-imp", "B2"),
                     ("combat", "init", "mira", "20"), ("combat", "init", "brann", "15"), ("combat", "init", "mire-imp", "5")):
            code, out, err = run_cli(*argv)
            self.assertEqual(code, 0, f"{argv}: {out}{err}")
        code, p, _ = self.req("GET", "/api/path?to=A4", seat="mira")
        self.assertTrue(p["ok"], p)
        self.assertEqual((p["who"], p["cost"], p["left"]), ("Mira", 15, 30))
        self.assertEqual(p["opportunity"], ["Mire Imp"])                  # leaves the imp's reach
        code, p, _ = self.req("GET", "/api/path?to=H6&who=mire-imp", seat="brann")
        self.assertEqual(p["who"], "Brann")                               # a seat only moves its own character
        self.assertIn("it's Mira's turn", " ".join(p["problems"]))
        code, svg, _ = self.req("GET", "/api/map.svg?preview=mira:A4", seat="mira")
        self.assertIn("polyline", svg)
