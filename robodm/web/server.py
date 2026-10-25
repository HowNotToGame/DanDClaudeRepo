"""The local web client's server: `./dm web` (standard library only).

Security: binds to 127.0.0.1 unless --lan; every request needs the access token (in the URL once, then a cookie,
or the X-RoboDM-Token header) or a player's seat link (robodm/web/table.py); requests whose Host isn't this
server are refused (DNS-rebinding guard). The token lives in .cache/web-token so bookmarks keep working;
`--new-token` replaces it (and revokes every seat link). The token holder is the host: DM view, rewind, rounds,
links. A seat is one player: player view, their own sheet and private messages.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import queue
import secrets
import shlex
import socket
import threading
import time
import urllib.parse
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from robodm import battlemap as bm, campaign, companions, costs, paths
from robodm.combat import Encounter
from robodm.util import RoboDMError
from robodm.web import table, views
from robodm.web.bridge import Bridge

STATIC = Path(__file__).resolve().parent / "static"
DENY = (("audit", "clear"), ("audit", "pass"), ("audit", "fail"), ("audit", "reset-cleared"), ("campaign", "delete"),
        ("hook",), ("web",), ("import",), ("sync",), ("rules", "fetch"), ("playtest",), ("engine",))


def token_path() -> Path:
    return paths.home() / ".cache" / "web-token"


def get_token(renew: bool = False) -> str:
    p = token_path()
    if p.exists() and not renew:
        tok = p.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(18)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(tok, encoding="utf-8")
    return tok


class Hub:
    """Fan-out of server-sent events to every open page, plus a watcher that announces campaign changes."""

    def __init__(self):
        self.clients: list[tuple[queue.Queue, str | None]] = []      # (queue, seat slug or None for the host)
        self.lock = threading.Lock()
        self._sig = None
        self.bridge = Bridge(lambda ev: self.publish("chat", ev))

    def subscribe(self, seat: str | None = None) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self.lock:
            self.clients.append((q, seat))
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            self.clients = [c for c in self.clients if c[0] is not q]

    def publish(self, event: str, data: dict) -> None:
        """To every page, or, when data has an "audience" (seat slugs), only to those seats and the host."""
        audience = data.get("audience") if isinstance(data, dict) else None
        with self.lock:
            for q, seat in list(self.clients):
                if audience and seat is not None and seat not in audience:
                    continue
                try:
                    q.put_nowait((event, data))
                except queue.Full:
                    pass

    def signature(self) -> str:
        """Cheap fingerprint of every campaign file (caches excluded)."""
        root = paths.campaigns_dir()
        parts = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != ".cache"]
            for f in filenames:
                try:
                    st = os.stat(os.path.join(dirpath, f))
                    parts.append(f"{dirpath}/{f}:{st.st_mtime_ns}:{st.st_size}")
                except OSError:
                    pass
        return str(hash(tuple(sorted(parts))))

    def watch(self, interval: float = 1.0) -> None:
        while True:
            try:
                sig = self.signature()
                if self._sig is not None and sig != self._sig:
                    self.publish("changed", {"at": time.time()})
                self._sig = sig
            except Exception:
                pass
            time.sleep(interval)


from robodm.runner import command_words, run_cli  # noqa: E402  (shared with the local DM engine)


class Handler(BaseHTTPRequestHandler):
    server_version = "RoboDM"
    hub: Hub
    token: str
    allowed_hosts: set

    # ------------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass                        # a browser closed its connection (tab closed, page reloaded)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None, set_cookies: list | None = None) -> None:
        self.send_response(code)
        for ck in set_cookies or []:
            self.send_header("Set-Cookie", ck)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def text(self, s: str, code: int = 200, ctype: str = "text/plain; charset=utf-8") -> None:
        self._send(code, s.encode("utf-8"), ctype)

    def _query(self) -> dict:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _principal(self) -> tuple[str, str | None] | None:
        """("host", None) for the token holder, ("seat", <pc slug>) for a player's seat link, or None."""
        if getattr(self, "_who", 0) != 0:
            return self._who
        self._who = None
        raw_host = self.headers.get("Host") or ""
        try:                                       # "127.0.0.1:8642", "[::1]:8642" → the bare host
            host = "" if "@" in raw_host else (urllib.parse.urlsplit("//" + raw_host).hostname or "")
        except ValueError:
            return None
        if host not in self.allowed_hosts:
            return None
        q = self._query()
        jar = cookies.SimpleCookie(self.headers.get("Cookie") or "")
        if q.get("seat") and q.get("key"):
            slug, key = q["seat"][0], q["key"][0]
            self._who = ("seat", slug) if table.check_seat(self.token, slug, key) and self._is_pc(slug) else None
            return self._who
        given = [q.get("token", [""])[0], self.headers.get("X-RoboDM-Token") or "",
                 jar["robodm_token"].value if "robodm_token" in jar else ""]
        if any(g and hmac.compare_digest(g.encode("utf-8"), self.token.encode("utf-8")) for g in given):
            self._who = ("host", None)
        elif "robodm_seat" in jar and "." in jar["robodm_seat"].value:
            slug, key = jar["robodm_seat"].value.split(".", 1)
            if table.check_seat(self.token, slug, key) and self._is_pc(slug):
                self._who = ("seat", slug)
        return self._who

    def _is_pc(self, slug: str) -> bool:
        """A seat is only good while its character is in the active campaign's party."""
        c = self._camp()
        return bool(c and c.pc_path(slug).exists())

    def _authorized(self) -> bool:
        return self._principal() is not None

    @property
    def seat(self) -> str | None:
        p = self._principal()
        return p[1] if p and p[0] == "seat" else None

    def _seat_name(self, c) -> str | None:
        if not self.seat:
            return None
        pc = c.find_pc(self.seat) if c else None
        return pc["name"] if pc else self.seat

    def _camp(self):
        try:
            return campaign.load()
        except RoboDMError:
            return None

    def _view(self) -> str:
        if self.seat:
            return "player"              # a seat never gets the DM view
        return "dm" if self._query().get("view", ["player"])[0] == "dm" else "player"

    MAX_BODY = 200_000

    def _read_body(self) -> None:
        """Read the request body up front, even for requests that will be refused: on Windows, closing a socket
        with unread data resets the connection instead of delivering the reply."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        self._too_large = n > self.MAX_BODY
        self._raw = self.rfile.read(min(n, self.MAX_BODY + 1)) if n > 0 else b""

    def _body(self) -> dict:
        if getattr(self, "_too_large", False):
            raise RoboDMError("Request too large.")
        return json.loads((getattr(self, "_raw", b"") or b"{}").decode("utf-8") or "{}")

    # ------------------------------------------------------------------ GET
    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not self._authorized():
            return self.text("RoboDM: open the link printed by `./dm web` (it carries the access token).", 401)
        try:
            if path in ("/", "/index.html"):
                body = (STATIC / "index.html").read_bytes()
                q = self._query()
                cookies_out = []
                if q.get("seat") and self.seat:            # a player's join link: this browser is now that seat
                    cookies_out = [f"robodm_seat={self.seat}.{q['key'][0]}; Path=/; HttpOnly; SameSite=Strict",
                                   "robodm_token=; Path=/; Max-Age=0"]
                elif q.get("token"):
                    cookies_out = [f"robodm_token={self.token}; Path=/; HttpOnly; SameSite=Strict",
                                   "robodm_seat=; Path=/; Max-Age=0"]
                return self._send(200, body, "text/html; charset=utf-8", None, cookies_out)
            if path.startswith("/static/"):
                f = (STATIC / path[len("/static/"):]).resolve()
                if STATIC not in f.parents or not f.is_file():
                    return self.text("Not found", 404)
                return self._send(200, f.read_bytes(), (mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                                  + ("; charset=utf-8" if f.suffix in (".js", ".css", ".html") else ""))
            if path == "/api/events":
                return self._events()
            c = self._camp()
            view = self._view()
            if path == "/api/state":
                if c is None:
                    return self.json({"campaign": None, "dm": self.hub.bridge.status("")})
                data = views.state(c, view, seat=self.seat)
                data["dm"] = self.hub.bridge.status(c.slug)
                data["host"] = self.seat is None
                return self.json(data)
            if c is None:
                return self.json({"error": "No campaign yet: create one with `./dm campaign new`."}, 404)
            if path == "/api/map.svg":
                enc = Encounter(c)
                if not enc.active or not enc.state.get("map"):
                    return self.text("<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>", 200, "image/svg+xml")
                from robodm import tactics

                m = enc.state["map"]
                bg = c.dir / m["background"] if m.get("background") else None
                reveal = view == "dm" or not c.settings.get("hide_monster_hp", True)
                fog = bm.fog_view(m, enc.combatants, tactics.vision_fn(c))
                route = enc.state.get("last_path")
                preview = self._query().get("preview", [""])[0]
                if preview and ":" in preview:
                    who, to = preview.split(":", 1)
                    if self.seat:
                        who = (views._seat_info(c, self.seat) or {}).get("combatant") or who
                    try:
                        route = tactics.path_preview(c, enc, who, to).get("path") or route
                    except RoboDMError:
                        pass
                svg = bm.render_svg(m, enc.combatants, (enc.current() or {}).get("id"), reveal=reveal,
                                    area=enc.state.get("last_area"), path=route, background=bg,
                                    title=f"{enc.state.get('name') or m.get('name') or 'Battle map'} — round {enc.state.get('round', 0)}",
                                    fog=fog, player=view != "dm")
                return self._send(200, svg.encode("utf-8"), "image/svg+xml; charset=utf-8")
            if path == "/api/recap":
                return self.json({"text": views.recap(c, view)})
            if path == "/api/recall":
                qs = [q for q in self._query().get("q", []) if q.strip()]
                return self.json({"hits": views.search(c, qs, view) if qs else []})
            if path == "/api/lore":
                return self.json({"entries": views.lore_list(c, view)})
            if path.startswith("/api/lore/"):
                return self.json(views.lore_entry(c, urllib.parse.unquote(path.split("/", 3)[3]), view))
            if path == "/api/people":
                return self.json({"people": views.people_list(c, view)})
            if path.startswith("/api/people/"):
                return self.json(views.person(c, urllib.parse.unquote(path.split("/", 3)[3]), view))
            if path.startswith("/api/companion/"):
                slug = companions.find(c, urllib.parse.unquote(path.split("/", 3)[3]))
                return self.json(views.companion_card(c, slug, view == "dm"))
            if path == "/api/journal":
                n = self._query().get("session", [None])[0]
                return self.json(views.journal(c, view, int(n) if n else None, seat_name=self._seat_name(c)))
            if path == "/api/path":
                from robodm import tactics

                enc = Encounter(c)
                to = self._query().get("to", [""])[0]
                who = self._query().get("who", [""])[0]
                if self.seat:
                    who = (views._seat_info(c, self.seat) or {}).get("combatant") or ""
                    if not who:
                        return self.json({"ok": False, "problems": ["your character isn't in this fight"]})
                if not enc.active or not to:
                    return self.json({"ok": False, "problems": ["no fight / no square"]})
                who = who or (enc.current() or {}).get("id") or ""
                return self.json(tactics.path_preview(c, enc, who, to))
            if path == "/api/seats":
                if self.seat:
                    return self.json({"error": "only the host can see the seat links"}, 403)
                return self.json({"seats": [{"slug": pc["slug"], "name": pc["name"], "player": pc.get("player", ""),
                                             "key": table.seat_key(self.token, pc["slug"])} for pc in c.party()],
                                  "lan": lan_addresses() if self.server.server_address[0] not in ("127.0.0.1", "localhost") else [],
                                  "port": self.server.server_address[1]})
            if path == "/api/rules":
                q = self._query().get("q", [""])[0]
                code, out, err = run_cli(["rules", "show", q]) if q else (0, "", "")
                return self.json({"text": out or err})
            if path in ("/api/settings", "/api/settings/models"):
                if self.seat:
                    return self.json({"error": "only the host can change the DM engine and table settings"}, 403)
                if path == "/api/settings/models":
                    return self.json(settings_models(self._query().get("engine", [""])[0], self._query().get("url", [""])[0]))
                return self.json(settings_payload(c))
            if path == "/api/forge":
                from robodm import builder

                return self.json(builder.forge_options(c))
            if path == "/api/chat/history":
                return self.json({"messages": self.hub.bridge.history(c.slug, seat=self.seat, host=self.seat is None)})
            return self.text("Not found", 404)
        except RoboDMError as e:
            return self.json({"error": str(e)}, 400)

    def _events(self) -> None:
        q = self.hub.subscribe(self.seat)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b"event: hello\ndata: {}\n\n")
            self.wfile.flush()
            while True:
                try:
                    event, data = q.get(timeout=15)
                    payload = json.dumps(data, ensure_ascii=False)
                    self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.hub.unsubscribe(q)

    # ------------------------------------------------------------------ POST
    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        self._read_body()
        if not self._authorized():
            return self.json({"error": "unauthorized"}, 401)
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).hostname not in self.allowed_hosts:
            return self.json({"error": "cross-site request refused"}, 403)
        try:
            body = self._body()
            c = self._camp()
            host_only = ("/api/chat/cancel", "/api/chat/reset", "/api/round", "/api/whisper", "/api/settings")
            if self.seat and path in host_only:
                return self.json({"error": "only the host can do that"}, 403)
            if path == "/api/settings":
                from robodm import engines

                applied = []
                if isinstance(body.get("engine"), dict) and body["engine"]:
                    engines.update(body["engine"])
                    engines._REACH.clear()
                    if "claude" in (body["engine"].get("agents") or {}):
                        applied = costs.apply_speed(c)        # write the agent files' model: and effort:
                problems = []
                for key, value in (body.get("campaign") or {}).items():
                    if c is None:
                        break
                    code, out, err = run_cli(["campaign", "set", str(key), str(value).lower() if isinstance(value, bool) else str(value)])
                    if code:
                        problems.append(err.replace("Error: ", "").strip())
                self.hub.publish("changed", {"settings": True})
                payload = settings_payload(self._camp())
                if problems:
                    payload["problems"] = problems
                if applied:
                    payload["applied"] = applied
                return self.json(payload)
            if path == "/api/roll":
                argv = ["roll", str(body.get("expr", "1d20"))]
                if body.get("adv"):
                    argv.append("--adv")
                if body.get("dis"):
                    argv.append("--dis")
                if body.get("label"):
                    argv += ["--label", str(body["label"])]
                code, out, err = run_cli(argv)
                return self.json({"ok": code == 0, "text": (out or err).strip()})
            if path == "/api/command":
                argv = body.get("argv")
                if isinstance(argv, str):
                    argv = shlex.split(argv)
                if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
                    return self.json({"error": "argv must be a list of strings"}, 400)
                if argv[0] in ("./dm", "dm"):
                    argv = argv[1:]
                words = command_words(argv)
                if any(words[:len(d)] == d for d in DENY):
                    return self.json({"error": f"`{' '.join(argv[:2])}` isn't available from the web client."}, 403)
                if self.seat and not table.seat_may_run(argv, self.seat):
                    return self.json({"error": "From a seat you can roll and manage your own character "
                                               "(level up, equip, attune); the rest goes through the DM."}, 403)
                code, out, err = run_cli(argv)
                return self.json({"ok": code == 0, "out": out, "err": err})
            if c is None:
                return self.json({"error": "No campaign yet."}, 404)
            if path == "/api/chat":
                text = str(body.get("message", "")).strip()
                if not text:
                    return self.json({"error": "empty message"}, 400)
                as_pc = self._seat_name(c) if self.seat else str(body.get("as", "")).strip()
                private_to = None
                if body.get("private"):
                    pc = c.find_pc(as_pc) if as_pc else None
                    if not pc:
                        return self.json({"error": "say who's whispering (Playing as…)"}, 400)
                    private_to = pc["slug"]
                if not self.hub.bridge.send(c.slug, text, as_pc, private_to=private_to, ooc=bool(body.get("ooc"))):
                    return self.json({"error": "The DM is still answering."}, 409)
                return self.json({"ok": True})
            if path == "/api/whisper":
                from robodm import whispers

                rec = whispers.add(c, str(body.get("to", "")), str(body.get("text", "")))
                self.hub.publish("whisper", dict(rec, audience=[rec["pc"]]))
                return self.json({"ok": True, "whisper": rec})
            if path == "/api/round":
                action = body.get("action")
                if action == "start":
                    table.start_round(c)
                elif action == "stop":
                    table.stop_round(c)
                elif action == "send":
                    if not self._send_round(c):
                        return self.json({"error": "nothing declared yet, or the DM is still answering"}, 409)
                self.hub.publish("changed", {"round": True})
                return self.json({"ok": True, "round": table.round_state(c)})
            if path == "/api/round/declare":
                who = self._seat_name(c) if self.seat else str(body.get("as", "")).strip()
                if not who or not c.find_pc(who):
                    return self.json({"error": "say who's acting (Playing as…)"}, 400)
                st = table.declare(c, c.find_pc(who)["name"], str(body.get("text", "")))
                if not st["waiting"]:
                    self._send_round(c)                    # everyone has declared: the DM resolves the round
                self.hub.publish("changed", {"round": True})
                return self.json({"ok": True, "round": table.round_state(c)})
            if path == "/api/chat/cancel":
                self.hub.bridge.cancel()
                return self.json({"ok": True})
            if path == "/api/chat/reset":
                self.hub.bridge.reset(c.slug)
                return self.json({"ok": True})
            return self.json({"error": "not found"}, 404)
        except (RoboDMError, ValueError) as e:
            return self.json({"error": str(e)}, 400)


def _send_round(self, c) -> bool:
    if self.hub.bridge.busy:
        return False
    msg = table.compose(c)
    return bool(msg) and self.hub.bridge.send(c.slug, msg, raw=True)


Handler._send_round = _send_round


TABLE_SETTINGS = ("turn_speed", "narration", "companion_agents", "dice_mode", "hide_monster_hp", "crit_rule",
                  "grid_diagonals")


def settings_payload(c) -> dict:
    """The ⚙ panel: the DM engine (this machine) and the campaign's table settings."""
    from robodm import engines
    from robodm.web.bridge import claude_command

    cfg = engines.load()
    if engines.is_local(cfg):
        ok, detail = engines.reachable(cfg, fresh=True)
    else:
        cmd = claude_command()
        ok, detail = cmd is not None, ("Claude Code CLI found" if cmd else "Claude Code's CLI isn't installed")
    models, model_error = [], ""
    try:
        models = engines.list_models(cfg)
    except Exception as e:                         # the server is down: say so, keep the page usable
        model_error = str(getattr(e, "reason", e))
    out = {"engine": engines.public(cfg), "engines": engines.ENGINES, "efforts": list(engines.EFFORTS),
           "claude_models": [m for m in engines.CLAUDE_MODELS if m], "models": models, "model_error": model_error,
           "reachable": ok, "detail": detail, "effort_profiles": engines.EFFORT_PROFILES,
           "agent_roles": engines.AGENT_ROLES, "local_roles": list(engines.LOCAL_ROLES),
           "lawyer_profiles": engines.LAWYER_PROFILES, "companion_profiles": engines.COMPANION_PROFILES,
           "agent_defaults": {role: costs.AGENT_MODELS[costs.speed(c)][role] for role in engines.AGENT_ROLES},
           "turn_speed": costs.speed(c)}
    if c is not None:
        out["campaign"] = {k: c.settings.get(k) for k in TABLE_SETTINGS}
        out["choices"] = {k: v for k, v in campaign.SETTING_CHOICES.items() if k in TABLE_SETTINGS}
        out["choices"]["hide_monster_hp"] = [True, False]
    return out


def settings_models(engine: str, url: str = "") -> dict:
    """Models on a server, optionally at a URL that isn't saved yet (the "Test connection" button)."""
    from robodm import engines

    cfg = engines.load()
    engine = engine if engine in engines.ENGINES else cfg["engine"]
    if url and engine != "claude":
        cfg[engine]["url"] = url.rstrip("/")
    try:
        return {"engine": engine, "ok": True, "models": engines.list_models(cfg, engine)}
    except Exception as e:
        return {"engine": engine, "ok": False, "models": [], "error": str(getattr(e, "reason", e))}


def lan_addresses() -> list[str]:
    addrs = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def make_server(host: str = "127.0.0.1", port: int = 8642, token: str | None = None) -> tuple[ThreadingHTTPServer, Hub]:
    hub = Hub()
    allowed = {"127.0.0.1", "localhost", "::1", "[::1]"}
    if host not in ("127.0.0.1", "localhost"):
        allowed |= set(lan_addresses()) | {socket.gethostname().lower(), host}
    handler = type("RoboDMHandler", (Handler,), {"hub": hub, "token": token or get_token(), "allowed_hosts": allowed})
    srv_cls = ThreadingHTTPServer
    if os.name == "nt":   # on Windows SO_REUSEADDR lets a second server bind a busy port and the old one keeps answering
        srv_cls = type("RoboDMServer", (ThreadingHTTPServer,), {"allow_reuse_address": False})
    try:
        srv = srv_cls((host, port), handler)
    except OSError as e:
        raise RoboDMError(f"Port {port} is busy (is `./dm web` already running?). Stop it, or use --port.") from e
    srv.daemon_threads = True
    return srv, hub


def serve(host: str = "127.0.0.1", port: int = 8642, open_browser: bool = False, renew: bool = False, log=print) -> None:
    import sys

    def say(msg: str) -> None:
        log(msg)
        sys.stdout.flush()          # the server then blocks; make sure the link is visible

    token = get_token(renew)
    srv, hub = make_server(host, port, token)
    threading.Thread(target=hub.watch, daemon=True).start()
    real_port = srv.server_address[1]
    url = f"http://127.0.0.1:{real_port}/?token={token}"
    say(f"🎲 RoboDM table is open: {url}")
    if host not in ("127.0.0.1", "localhost"):
        for a in lan_addresses():
            say(f"   host link on your network: http://{a}:{real_port}/?token={token}  (keep this one to yourself)")
    try:
        party = campaign.load().party()
    except RoboDMError:
        party = []
    if party:
        base = f"http://{(lan_addresses() or ['127.0.0.1'])[0] if host not in ('127.0.0.1', 'localhost') else '127.0.0.1'}:{real_port}"
        say("   Player seats (one link per character; a seat sees the player view, its own sheet and private messages):")
        for pc in party:
            say(f"     {pc['name']}: {base}/?seat={pc['slug']}&key={table.seat_key(token, pc['slug'])}")
    say("   `./dm web --new-token` revokes every link.")
    if not hub.bridge.status("")["available"]:
        say("   DM chat: Claude Code's CLI wasn't found. " + hub.bridge.status("")["help"])
    say("   Ctrl+C to stop.")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
