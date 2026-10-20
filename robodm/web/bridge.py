"""The DM behind the web client: a headless Claude Code (`claude -p`) running in this repo.

Because it is the real Claude Code, everything RoboDM sets up applies: the dm skill, the hooks (rules gate,
post-audit, transcripts), the rules-lawyer and the companion subagents. Each player message is one
`claude -p --resume <session>` run whose `stream-json` output is turned into chat events for the page: a chip for
each tool the DM uses (dice, combat, memory, a companion's agent…) as it happens, and the reply once the whole turn
is finished. The text is held back until then, because the hooks can send the DM back to work after it first
writes (the post-audit, a receipt correction), and players should read the finished reply, not the drafts. The DM
runs with CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1, so a subagent it launches (the rules-lawyer, a companion) runs in
the foreground and the DM waits for its answer inside the same turn.

The prompt goes in on stdin, never on the command line, so nothing the player types is parsed by a shell.
Find Claude Code with ROBODM_CLAUDE (a path or a command), else `claude` on PATH; ROBODM_CLAUDE_ARGS adds flags.
It runs with the project's permission allow-list (.claude/settings.json): a tool call that would need a prompt is
refused, since nobody is at a terminal to answer it.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from robodm import paths
from robodm.util import now_iso, read_json, write_json

WEB_CONTEXT = (
    "[RoboDM web client] The player is using RoboDM's local web client. Your text appears in a chat panel next to "
    "a live battle map, initiative tracker, party sheets, companions panel and dice tray, which update by themselves "
    "from the campaign files. Run the game with the dm skill (.claude/skills/dm/SKILL.md); load the brief first if "
    "you haven't this conversation. Write the fiction and short dice lines; don't paste raw tool output, text maps or "
    "image files (the client draws the map). Players may report rolls from the web dice tray (\"I rolled 17\"); "
    "messages may start with the speaking character's name. Several players may share the table, each at a seat. "
    "\"[OOC from X]\" is an out-of-character question: answer briefly as the DM, without advancing the scene or "
    "running rules commands. \"[Private from X — …]\" is for that player alone: your reply is shown only to them "
    "(record what matters with --known-by); to whisper a player during a public reply, use `./dm whisper <pc> \"…\"`. "
    "\"[Round of declared actions …]\" lists what everyone does this round: resolve it as one beat. \"I move to D4\" "
    "comes from a player clicking the map: run `./dm combat move`.")
# Separates that preamble from the player's words, so the transcript hook records only what they said.
PREAMBLE_END = "--- player ---"


def player_text(prompt: str) -> str:
    """The player's own words from a web-client prompt (the first prompt of a conversation carries WEB_CONTEXT)."""
    if not prompt.startswith("[RoboDM web client]"):
        return prompt
    if f"\n{PREAMBLE_END}\n" in prompt:
        return prompt.split(f"\n{PREAMBLE_END}\n", 1)[1]
    return re.sub(r"^\[RoboDM web client\].*?Campaign: [\w-]+\.\s*", "", prompt, flags=re.S)   # older prompts


def claude_command() -> list[str] | None:
    env = os.environ.get("ROBODM_CLAUDE")
    if env:
        return [env] if os.path.exists(env) else shlex.split(env)
    exe = shutil.which("claude")
    if not exe:
        return None
    if exe.lower().endswith((".cmd", ".bat")):   # npm's Windows shim: run node on the script, skip cmd.exe parsing
        pkg = Path(exe).parent / "node_modules" / "@anthropic-ai" / "claude-code"
        if (pkg / "bin" / "claude.exe").exists():          # newer packages ship a native binary
            return [str(pkg / "bin" / "claude.exe")]
        node = shutil.which("node")
        if (pkg / "cli.js").exists() and node:
            return [node, str(pkg / "cli.js")]
    return [exe]


# The DM's own CLI, in every spelling a headless run might use. Passed explicitly because Claude Code ignores the
# project's permissions.allow until the folder has been trusted interactively, and `-p` can't ask.
DM_TOOLS = ("Bash(./dm *)", "Bash(./dm:*)", "PowerShell(./dm *)", r"PowerShell(.\dm *)", r"PowerShell(.\dm.cmd *)",
            "PowerShell(dm *)", "Bash(python -m robodm *)", "Bash(python3 -m robodm *)")


def allowed_tools() -> list[str]:
    settings = read_json(paths.PACKAGE_DIR.parent / ".claude" / "settings.json") or {}
    extra = ((settings.get("permissions") or {}).get("allow")) or []
    return list(dict.fromkeys([*DM_TOOLS, *extra]))


def classify(name: str, inp: dict) -> tuple[str, str]:
    """(icon, short label) for a tool call, for the chat's activity chips."""
    if name == "Bash":
        cmd = " ".join(str(inp.get("command", "")).split())
        rest = (cmd.split("-m robodm", 1)[-1] if "-m robodm" in cmd else cmd.split("dm ", 1)[-1] if "dm " in cmd else cmd).strip()
        icon = ("🎲" if rest.startswith(("roll", "pc check", "pc save")) else
                "⚔️" if rest.startswith("combat") else
                "⚖️" if rest.startswith(("audit", "ruling", "rules")) else
                "🗣" if rest.startswith("companion") else
                "🧠" if rest.startswith(("memory", "recall", "lore", "quest", "session")) else "⚙️")
        return icon, rest[:120]
    if name in ("Agent", "Task"):
        who = inp.get("subagent_type") or "subagent"
        icon = "⚖️" if who == "rules-lawyer" else "🗣" if str(who).startswith("companion") else "🤖"
        return icon, f"consulting {who}" + (f": {inp.get('description')}" if inp.get("description") else "")
    if name == "Skill":
        return "📜", f"skill {inp.get('skill', '')}"
    if name in ("Read", "Grep", "Glob"):
        return "📖", f"{name.lower()} {inp.get('file_path') or inp.get('pattern') or ''}"[:120]
    return "⚙️", name


def compose(parts: list[str]) -> str:
    """The reply the players see: everything the DM wrote this turn, unless a later message starts with
    `[REVISED]` (sent after a hook asked for a fix), which replaces what came before it."""
    from robodm.audit import REVISED

    kept: list[str] = []
    for p in parts:
        p = p.strip()
        if p.startswith(REVISED):
            kept = []
            p = p[len(REVISED):].strip()
        if p:
            kept.append(p)
    return "\n\n".join(kept)


class Bridge:
    """One DM conversation per campaign. `send()` runs a turn in a thread and reports through `emit`."""

    def __init__(self, emit: Callable[[dict], None]):
        self.emit = emit
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.partial_ok = True
        self.audience: list[str] | None = None        # a private turn's events go only to these seats (and the host)
        self.stop_event = threading.Event()             # cancels a local engine's turn
        self._results: list[dict] = []                  # this run's `result` events (one chip at the end)
        self._emit_raw = emit
        self.emit = self._emit

    def _emit(self, ev: dict) -> None:
        if self.audience and ev.get("kind") != "idle":
            ev = dict(ev, audience=self.audience)
        self._emit_raw(ev)

    def _held(self, ev: dict) -> None:
        """A local engine's events, minus the text it streams: the page gets the reply with `done`."""
        if ev.get("kind") not in ("delta", "replace"):
            self.emit(ev)

    # ------------------------------------------------------------------ persistence
    def _state_path(self, slug: str) -> Path:
        return paths.campaigns_dir() / slug / ".cache" / "web.json"

    def session_id(self, slug: str) -> str | None:
        return (read_json(self._state_path(slug)) or {}).get("session_id")

    def _set_session(self, slug: str, sid: str | None) -> None:
        st = read_json(self._state_path(slug)) or {}
        st["session_id"] = sid
        write_json(self._state_path(slug), st)

    def _setting(self, slug: str, key: str):
        try:
            from robodm import campaign

            return campaign.load(slug).settings.get(key)
        except Exception:
            return None

    def reset(self, slug: str) -> None:
        self._set_session(slug, None)
        from robodm.engines import local

        local.reset(slug)

    def history_path(self, slug: str) -> Path:
        return paths.campaigns_dir() / slug / ".cache" / "web-chat.jsonl"

    def history(self, slug: str, n: int = 200, seat: str | None = None, host: bool = True) -> list[dict]:
        """The chat so far; private exchanges only for the host and the seat they belong to."""
        p = self.history_path(slug)
        if not p.exists():
            return []
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        rows = [r for r in rows if host or not r.get("private_to") or r.get("private_to") == seat]
        return rows[-n:]

    def _log(self, slug: str, rec: dict) -> None:
        p = self.history_path(slug)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ status
    @property
    def busy(self) -> bool:
        return self.lock.locked()

    def status(self, slug: str) -> dict:
        from robodm import engines

        cfg = engines.load()
        if engines.is_local(cfg):
            ok, detail = engines.reachable(cfg)
            sub = cfg[cfg["engine"]]
            return {"available": ok, "busy": self.busy, "session": None, "engine": cfg["engine"],
                    "engine_name": engines.ENGINES[cfg["engine"]], "model": sub.get("model"),
                    "effort": cfg.get("effort") or "medium", "command": f"{cfg['engine']} · {sub.get('model')}",
                    "help": None if ok else engines.help_text(cfg) + f" ({detail})"}
        cmd = claude_command()
        return {"available": cmd is not None, "busy": self.busy, "session": self.session_id(slug),
                "engine": "claude", "engine_name": "Claude Code", "model": cfg["claude"].get("model") or "default",
                "effort": cfg.get("effort") or "default",
                "command": " ".join(cmd) if cmd else None,
                "help": None if cmd else ("Install Claude Code's CLI (`npm install -g @anthropic-ai/claude-code`, then run "
                                          "`claude` once to sign in), or set ROBODM_CLAUDE to its path. Until then the "
                                          "table works and you can play in a Claude Code session beside it.")}

    # ------------------------------------------------------------------ a turn
    def send(self, slug: str, text: str, as_pc: str = "", private_to: str | None = None, ooc: bool = False,
             raw: bool = False) -> bool:
        """One turn. `private_to` (a PC slug): only that seat and the host see it. `ooc`: an out-of-character
        question. `raw`: send the text as it is (a composed round)."""
        if not self.lock.acquire(blocking=False):
            return False
        who = as_pc or "a player"
        if raw:
            message = text
        elif private_to:
            message = (f"[Private from {who} — only {who} and the DM see this exchange; your reply is shown only to "
                       f"{who}.] {text}")
        elif ooc:
            message = f"[OOC from {who}] {text}"
        else:
            message = f"{as_pc}: {text}" if as_pc else text
        self.audience = [private_to] if private_to else None
        rec = {"role": "player", "text": message, "ts": now_iso()}
        if private_to:
            rec["private_to"] = private_to
        self._log(slug, rec)
        threading.Thread(target=self._run, args=(slug, message, private_to), daemon=True).start()
        return True

    def cancel(self) -> None:
        self.stop_event.set()
        p = self.proc
        if p and p.poll() is None:
            p.kill()

    def _run(self, slug: str, message: str, private_to: str | None = None) -> None:
        from robodm import engines

        self.stop_event.clear()
        try:
            cfg = engines.load()
            if engines.is_local(cfg):                    # Ollama / KoboldCpp / OpenAI-compatible: RoboDM runs the loop
                ok, detail = engines.reachable(cfg, fresh=True)
                if not ok:
                    self.emit({"kind": "error", "text": engines.help_text(cfg) + f" ({detail})"})
                    return
                from robodm.engines.local import LocalDM

                self.emit({"kind": "start", "text": message})
                text = LocalDM(cfg, self._held, self.stop_event).turn(slug, message, private_to)
                rec = {"role": "dm", "text": text or "", "ts": now_iso(), "engine": cfg["engine"]}
                if private_to:
                    rec["private_to"] = private_to
                self._log(slug, rec)
                return
            cmd = claude_command()
            if not cmd:
                self.emit({"kind": "error", "text": self.status(slug)["help"]})
                return
            sid = self.session_id(slug)
            prompt = message if sid else f"{WEB_CONTEXT}\n\nCampaign: {slug}.\n{PREAMBLE_END}\n{message}"
            self.emit({"kind": "start", "text": message})
            text = self._turn(cmd, sid, prompt, slug)
            if text is None and self.partial_ok is False:
                text = self._turn(cmd, sid, prompt, slug)        # retried without partial messages
            rec = {"role": "dm", "text": text or "", "ts": now_iso()}
            if private_to:
                rec["private_to"] = private_to
            self._log(slug, rec)
        except Exception as e:  # never leave the page hanging
            self.emit({"kind": "error", "text": f"DM bridge error: {e}"})
        finally:
            self.proc = None
            self.emit({"kind": "idle"})
            self.audience = None
            self.lock.release()

    def _turn(self, cmd: list[str], sid: str | None, prompt: str, slug: str) -> str | None:
        args = cmd + ["-p", "--output-format", "stream-json", "--verbose"]
        if self.partial_ok:
            args.append("--include-partial-messages")
        if sid:
            args += ["--resume", sid]
        from robodm import engines

        cfg = engines.load()
        model = cfg["claude"].get("model") or self._setting(slug, "dm_model")
        if model:
            args += ["--model", model]
        if cfg.get("effort"):
            args += ["--effort", cfg["effort"]]
        args += ["--allowedTools", *allowed_tools()]
        args += shlex.split(os.environ.get("ROBODM_CLAUDE_ARGS", ""))    # e.g. --model …, --permission-mode …
        root = paths.PACKAGE_DIR.parent
        # Foreground subagents: the DM waits for the rules-lawyer's verdict instead of ending its turn early (and
        # writing "still pending" notes each time the Stop hook sends it back). ROBODM_WEB_CLIENT tells the hooks.
        env = dict(os.environ, PYTHONIOENCODING="utf-8", CLAUDE_CODE_DISABLE_BACKGROUND_TASKS="1", ROBODM_WEB_CLIENT="1")
        self.proc = subprocess.Popen(args, cwd=str(root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        proc = self.proc
        errs: list[str] = []          # drained alongside stdout: a full stderr pipe would stall the child
        drain = threading.Thread(target=lambda: errs.append(proc.stderr.read()), daemon=True)
        drain.start()
        proc.stdin.write(prompt)
        proc.stdin.close()
        parts: list[str] = []
        streamed: set[str] = set()
        self._results: list[dict] = []
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            self.handle(ev, slug, parts, streamed)
        code = proc.wait()
        drain.join()
        err = "".join(errs)
        proc.stdout.close()
        proc.stderr.close()
        if code != 0 and not parts:
            if self.partial_ok and "include-partial-messages" in err:
                self.partial_ok = False
                return None
            if sid and ("session" in err.lower() and "not found" in err.lower()):
                self._set_session(slug, None)
            self.emit({"kind": "error", "text": (err.strip().splitlines() or [f"claude exited with code {code}"])[-1]})
        text = compose(parts)
        self.emit({"kind": "done", "text": text})
        if self._results:                  # one chip for the whole turn, however many results the run reported
            last = self._results[-1]
            self.emit({"kind": "meta", "cost": last.get("total_cost_usd"),
                       "ms": sum(r.get("duration_ms") or 0 for r in self._results) or None,
                       "turns": sum(r.get("num_turns") or 0 for r in self._results) or None})
        return text

    def handle(self, ev: dict, slug: str, parts: list[str], streamed: set[str]) -> None:
        """Turn one stream-json event into chat events. Subagents' own chatter stays internal."""
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
            self._set_session(slug, ev["session_id"])
            return
        top = not ev.get("parent_tool_use_id")
        if t == "stream_event" and top:
            e = ev.get("event") or {}
            if e.get("type") == "message_start":
                parts.append("")
            elif e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                chunk = e["delta"].get("text", "")
                if not parts:
                    parts.append("")
                parts[-1] += chunk
                mid = (ev.get("message") or {}).get("id") or "live"
                streamed.add(mid)
            return
        if t == "assistant":
            msg = ev.get("message") or {}
            for block in msg.get("content") or []:
                if block.get("type") == "tool_use":
                    icon, label = classify(block.get("name", ""), block.get("input") or {})
                    self.emit({"kind": "tool", "icon": icon, "label": label, "sub": not top})
                elif block.get("type") == "text" and top and not streamed:
                    parts.append(block.get("text", ""))
            return
        if t == "result":
            self._results.append(ev)
            try:
                from robodm import campaign, costs

                costs.record_usage(campaign.load(slug), ev.get("total_cost_usd"), ev.get("duration_ms"),
                                   ev.get("num_turns"), private=bool(self.audience))
            except Exception:
                pass                     # usage is informational; never break a turn over it
