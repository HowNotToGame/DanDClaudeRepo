"""The DM loop for local models (Ollama, KoboldCpp, any OpenAI-compatible server).

In Claude Code, the skills, hooks and subagents do this; here RoboDM does it itself, every turn:

1. hooks.prompt(): transcript, a new audit turn, notes for the DM (unrecorded names, companions, off-screen
   beats, a rewind).
2. The model gets a condensed DM prompt + the campaign brief + recent chat, and a `dm` tool that runs ./dm
   commands in-process (no shell). Native tool calling where the server supports it; a <dm>…</dm> text protocol
   otherwise; tool calls written as plain JSON text (a common Llama 3.1 habit) are caught too.
3. A command blocked for a first-use pre-check gets one from the local rules-lawyer (same model, JSON verdict),
   then runs again. Rules-lawyer verdicts can't be issued by the DM itself.
4. The receipt check: if the reply contradicts the dice, the model rewrites it.
5. The post-audit: routine mechanical events pass (when the effort allows), the rest go to the local
   rules-lawyer; failures are fixed with their `--fix` command and the players get a one-line correction. An
   answer the rules-lawyer can't give is recorded honestly as `unverified`, never as passed.
6. hooks.stop(): transcript of the reply (marked private when it is), unrecorded names.
"""

from __future__ import annotations

import json
import re
import threading
import time

from robodm import audit, campaign, companions, costs, hooks, memory, runner
from robodm.engines import agent_profile, effort_profile, llm, prompts
from robodm.util import RoboDMError, read_json, truncate, write_json

DM_TOOL = {"type": "function", "function": {
    "name": "dm", "description": "Run one RoboDM ./dm command (dice, checks, combat, memory, lore, rules lookups, time). "
                                 "Returns its output, which is the truth.",
    "parameters": {"type": "object", "properties": {"command": {
        "type": "string", "description": "The command after ./dm, e.g. pc check brann athletics --dc 15"}},
        "required": ["command"]}}}
COMPANION_TOOL = {"type": "function", "function": {
    "name": "companion", "description": "Ask a companion (an NPC with its own mind) what they do or say right now.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "The companion's name"},
        "situation": {"type": "string", "description": "What is happening and what they're reacting to"}},
        "required": ["name", "situation"]}}}

DENIED = (("audit", "clear"), ("audit", "pass"), ("audit", "fail"), ("audit", "reset-cleared"), ("campaign", "delete"),
          ("campaign", "use"), ("campaign", "new"), ("import",), ("sync",), ("web",), ("hook",), ("playtest",),
          ("rules", "fetch"), ("export",))
_DM_TAG = re.compile(r"<dm>(.*?)</dm>", re.S | re.I)
_COMP_TAG = re.compile(r'<companion\s+name="([^"]+)">(.*?)</companion>', re.S | re.I)
_PY_TAG = re.compile(r"<\|python_tag\|>|<\|eom_id\|>|<\|eot_id\|>")
_GATE = re.compile(r'audit brief (\w+) "([^"]+)"(?: --use "([^"]*)")?')
_ECHO = re.compile(r"(?m)^\s*[`*_>]*\s*(?:\./)?dm\s+(?:pc|combat|roll|memory|lore|scene|time|rules|recall|whisper|clock|"
                   r"faction|world|quest|session|ruling|companion)\b.*$")
_ROLL_TEXT = re.compile(r"🎲|\b(?:rolls?|rolled|rolling)\b[^.\n]{0,40}\d|\b\d*d(?:4|6|8|10|12|20|100)\b\s*[+-]?\s*\d*\s*=\s*\d+", re.I)
ROLLING = {("roll",), ("pc", "check"), ("pc", "save"), ("combat", "attack"), ("combat", "save"), ("combat", "damage"),
           ("combat", "cast"), ("combat", "deathsave"), ("pc", "cast")}          # initiative doesn't count
_FIGHT_TEXT = re.compile(r"\b(?:hits?|misses|missed|damage|wounds?|slash(?:es)?|strikes?)\b[^.\n]{0,60}\d|\d[^.\n]{0,30}"
                         r"\b(?:damage|to hit|vs\.? AC)\b", re.I)
HISTORY_KEEP = 8                 # recent chat messages sent with each turn (the brief carries the rest)


# =============================================================================== parsing tool calls from text

def _json_calls(text: str) -> tuple[list[dict], str]:
    """Tool calls written as JSON text ({"name": "dm", "parameters": {...}}), and the text without them."""
    calls, rest = [], text
    for m in re.finditer(r"\{[^{}]*\"name\"\s*:\s*\"(dm|companion)\"[^{}]*\{[^{}]*\}[^{}]*\}", text, re.S):
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        args = obj.get("parameters") or obj.get("arguments") or {}
        if isinstance(args, str):
            args = {"command": args}
        calls.append({"id": f"text_{len(calls) + 1}", "name": obj.get("name", "dm"), "arguments": args})
        rest = rest.replace(m.group(0), "")
    return calls, rest


def text_calls(content: str) -> list[dict]:
    calls = [{"id": f"tag_{i}", "name": "dm", "arguments": {"command": m.strip()}}
             for i, m in enumerate(_DM_TAG.findall(content), 1)]
    calls += [{"id": f"comp_{i}", "name": "companion", "arguments": {"name": n.strip(), "situation": s.strip()}}
              for i, (n, s) in enumerate(_COMP_TAG.findall(content), 1)]
    if not calls:
        calls, _ = _json_calls(_PY_TAG.sub("", content))
    return calls


def strip_calls(content: str) -> str:
    text = _PY_TAG.sub("", content)
    text = _DM_TAG.sub("", text)
    text = _COMP_TAG.sub("", text)
    text = _ECHO.sub("", text)                  # commands pasted into the narration
    _, text = _json_calls(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def visible_so_far(content: str) -> str:
    """What can be shown while a reply streams: no tags, no half-written tag, no JSON tool call."""
    text = strip_calls(content)
    for opener in ("<dm", "<companion", "<|"):
        i = text.find(opener)
        if i >= 0:
            text = text[:i]
    if text.endswith("<"):
        text = text[:-1]
    if text.lstrip().startswith("{"):
        return ""
    return text


def _parse_json(text: str) -> dict | None:
    text = _PY_TAG.sub("", text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


# =============================================================================== the engine

class LocalDM:
    def __init__(self, cfg: dict, emit, cancel: threading.Event | None = None):
        self.cfg = cfg
        self.engine = cfg["engine"]
        self.sub = cfg[self.engine]
        self.emit = emit
        self.cancel = cancel or threading.Event()
        self.profile = effort_profile(cfg)
        self.lawyer = agent_profile(cfg, "rules-lawyer")
        self.companion = agent_profile(cfg, "companion")
        self.tool_mode = self.sub.get("tool_mode", "native")
        self.rounds = 0
        self.tokens = 0
        self.streamed = ""
        self.rolled = False                     # did any dice go through ./dm this turn?

    # ------------------------------------------------------------------ model calls
    def _call(self, messages: list[dict], tools: list[dict] | None, json_mode: bool = False, stream: bool = True,
              temperature: float | None = None, model: str | None = None, max_tokens: int | None = None) -> dict:
        self.rounds += 1
        base = self.streamed

        def on_delta(content: str) -> None:
            vis = visible_so_far(content)
            if not vis:
                return
            sep = "\n\n" if base and not base.endswith("\n") else ""
            full = base + sep + vis
            if full.startswith(self.streamed) and len(full) > len(self.streamed):
                self.emit({"kind": "delta", "text": full[len(self.streamed):]})
                self.streamed = full

        res = llm.chat(self.cfg, self.engine, messages, tools=tools, json_mode=json_mode,
                       on_delta=on_delta if stream else None,
                       temperature=self.profile["temperature"] if temperature is None else temperature,
                       max_tokens=max_tokens, cancel=self.cancel, model=model)
        self.tokens += res.get("tokens", 0)
        return res

    # ------------------------------------------------------------------ the turn
    def turn(self, slug: str, message: str, private_to: str | None = None) -> str:
        started = time.time()
        camp = campaign.load(slug)
        notes = hooks.prompt(camp, {"prompt": message}) or {}
        extra = (notes.get("hookSpecificOutput") or {}).get("additionalContext", "")
        user = message + (f"\n\n[RoboDM notes for the DM — not from the players] {extra}" if extra else "")
        messages = [{"role": "system", "content": self.system(campaign.load(slug))}] + self._history(slug) + \
                   [{"role": "user", "content": user}]
        ooc = message.startswith("[OOC")
        reply = self._loop(slug, messages, allow_tools=not ooc)
        if not ooc and self._invented(slug, reply):
            reply = self._unbacked(slug, messages, reply)
        reply = self._no_player_lines(slug, reply)
        if not ooc:
            reply = self._receipts(slug, messages, reply)
            reply = self._post_audit(slug, reply)
        if self.profile.get("review") and not ooc:
            reply = self._review(slug, messages, reply)
        hooks.stop(campaign.load(slug), {"last_assistant_message": reply})
        self._remember(slug, message, reply)
        ms = int((time.time() - started) * 1000)
        self.emit({"kind": "done", "text": reply})
        self.emit({"kind": "meta", "ms": ms, "turns": self.rounds, "cost": 0, "model": self.sub.get("model"),
                   "engine": self.engine, "tokens": self.tokens})
        costs.record_usage(campaign.load(slug), 0, ms, self.rounds, private=bool(private_to), engine=self.engine)
        return reply

    def system(self, camp) -> str:
        from robodm import context_blocks
        from robodm.combat import Encounter

        num_ctx = int(self.sub.get("num_ctx") or 8192)
        budget = min(costs.brief_budget(camp), max(2500, (num_ctx - 6500) * 3))
        parts = [prompts.DM_SYSTEM.replace("{length}", prompts.LENGTH.get(camp.settings.get("narration", "standard"),
                                                                           prompts.LENGTH["standard"]))]
        active = [s for s in companions.active(camp)] if camp.settings.get("companion_agents", "key") != "off" else []
        if active:
            names = ", ".join(companions.load_persona(camp, s)[0].get("name", s) for s in active)
            parts.append(prompts.COMPANIONS.format(names=names))
        if self.tool_mode == "text":
            parts.append(prompts.TEXT_PROTOCOL)
        parts += ["=== CAMPAIGN BRIEF ===", memory.brief(camp, budget=budget)]
        blocks = context_blocks.render(camp)
        if blocks.strip():
            parts += ["=== INSTRUCTION BLOCKS (follow them; house rules override the rules as written) ===",
                      truncate(blocks, 2500)]
        enc = Encounter(camp)
        if enc.active:
            parts += ["=== COMBAT IN PROGRESS ===", enc.status_text(reveal=True)]
        return "\n\n".join(parts)

    def _tools(self, slug: str) -> list[dict]:
        camp = campaign.load(slug)
        tools = [DM_TOOL]
        if companions.active(camp) and camp.settings.get("companion_agents", "key") != "off":
            tools.append(COMPANION_TOOL)
        return tools

    def _loop(self, slug: str, messages: list[dict], allow_tools: bool = True) -> str:
        text = self._loop_rounds(slug, messages, allow_tools)
        if text != self.streamed:
            self.emit({"kind": "replace", "text": text})
            self.streamed = text
        return text

    def _loop_rounds(self, slug: str, messages: list[dict], allow_tools: bool = True) -> str:
        parts: list[str] = []
        for _ in range(self.profile["rounds"]):
            if self.cancel.is_set():
                break
            tools = self._tools(slug) if allow_tools and self.tool_mode == "native" else None
            try:
                res = self._call(messages, tools)
            except llm.ToolsUnsupported:
                self.tool_mode = "text"                     # this server/model: fall back to the text protocol
                messages[0] = dict(messages[0], content=messages[0]["content"] + "\n\n" + prompts.TEXT_PROTOCOL)
                self.emit({"kind": "tool", "icon": "⚙️", "label": "no native tool calling here: using the text protocol",
                           "sub": True})
                continue
            content = res["content"]
            calls = res["tool_calls"] if allow_tools else []
            native = bool(calls)
            if allow_tools and not calls:
                calls = text_calls(content)
            visible = strip_calls(content)
            if not calls:
                return visible or "\n\n".join(parts)
            if visible:
                parts = [visible]                   # kept only if nothing better comes (usually filler)
            if native:
                messages.append({"role": "assistant", "content": content, "tool_calls": calls})
                for c in calls:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "name": c["name"],
                                     "content": self._exec(slug, c)})
            else:
                messages.append({"role": "assistant", "content": content})
                results = [f"<result of=\"{c['arguments'].get('command') or c['arguments'].get('name', '')}\">\n"
                           f"{self._exec(slug, c)}\n</result>" for c in calls]
                messages.append({"role": "user", "content": "Tool results:\n" + "\n".join(results) +
                                 "\nRun more commands if you need to; otherwise write your narration for the players."})
        messages.append({"role": "user", "content": "Stop using tools now and write your reply to the players."})
        res = self._call(messages, None)
        return strip_calls(res["content"]) or "\n\n".join(parts)

    # ------------------------------------------------------------------ tools
    def _exec(self, slug: str, call: dict) -> str:
        args = call.get("arguments") or {}
        if call.get("name") == "companion":
            return self._companion(slug, str(args.get("name", "")), str(args.get("situation", "")))
        command = str(args.get("command") or args.get("cmd") or args.get("input") or "").strip()
        from robodm.web.bridge import classify

        icon, label = classify("Bash", {"command": "./dm " + command})
        self.emit({"kind": "tool", "icon": icon, "label": label, "sub": False})
        try:
            argv = runner.parse_command(command)
        except ValueError as e:
            return f"Couldn't read that command ({e}). Quote names with spaces: dm lore add \"Old Mill\" --type location"
        if not argv:
            return "Empty command."
        words = [a for a in argv if not a.startswith("-")]
        if any(tuple(words[:len(d)]) == d for d in DENIED) or "--self" in argv:
            return ("That command isn't available to the DM (rules verdicts come from the rules-lawyer; campaigns "
                    "aren't switched or deleted from the table).")
        code, out, err = runner.run_cli(argv)
        if code and "hasn't been cleared by the rules-lawyer" in err:
            return self._precheck_and_retry(slug, argv, err)
        if code == 0 and (tuple(words[:1]) in ROLLING or tuple(words[:2]) in ROLLING):
            self.rolled = True
        if code and "No active combat" in err:
            err += "\nStart the fight first: dm combat setup \"Name\" --monsters \"goblin x2\""
        text = (out + ("\n" + err if err else "")).strip()
        return truncate(text or "(done, no output)", 3500)

    def _precheck_and_retry(self, slug: str, argv: list[str], err: str) -> str:
        m = _GATE.search(err)
        if not m:
            return err.strip()
        kind, name, use = m.group(1), m.group(2), m.group(3) or ""
        self.emit({"kind": "tool", "icon": "⚖️", "label": f"local rules-lawyer: pre-check {kind} {name}", "sub": True})
        _, brief, _ = runner.run_cli(["audit", "brief", kind, name] + (["--use", use] if use else []))
        data = self._lawyer_json([{"role": "system", "content": prompts.PRECHECK_SYSTEM},
                                  {"role": "user", "content": brief}],
                                 lambda d: str(d.get("verdict", "")).upper() in ("CORRECT", "INCORRECT"))
        verdict = str(data.get("verdict", "")).upper()
        notes = str(data.get("notes", "")).strip()
        if verdict not in ("CORRECT", "INCORRECT"):
            verdict, notes = "CORRECT", ("(unverified: the local rules-lawyer's answer couldn't be read) " + notes).strip()
        camp = campaign.load(slug)
        audit.clear(camp, kind, name, verdict, notes, str(data.get("cite", "")))
        cl = audit.cleared(camp)
        key = audit.item_key(kind, name)
        if key in cl:
            cl[key]["by"] = f"local rules-lawyer ({self.lawyer['model']})"
            audit._save_cleared(camp, cl)
        if verdict == "INCORRECT":
            return f"Rules-lawyer pre-check: INCORRECT — {notes}. That isn't allowed; choose something else."
        code, out, err2 = runner.run_cli(argv)
        return truncate(f"(rules-lawyer pre-check: CORRECT — {notes})\n" + (out + err2).strip(), 3500)

    def _lawyer_json(self, messages: list[dict], usable) -> dict:
        """Ask the local rules-lawyer (its own model and effort); at high effort and up, ask again when the
        answer can't be read."""
        data: dict = {}
        for _ in range(1 + self.lawyer["retries"]):
            res = self._call(messages, None, json_mode=True, stream=False, temperature=0.1,
                             model=self.lawyer["model"] or None)
            data = _parse_json(res["content"]) or {}
            if usable(data):
                break
        return data

    def _companion(self, slug: str, name: str, situation: str) -> str:
        camp = campaign.load(slug)
        if camp.settings.get("companion_agents", "key") == "off":
            return "Companions are voiced by you in this campaign (companion_agents is off)."
        try:
            cslug = companions.find(camp, name)
        except RoboDMError as e:
            return str(e)
        meta, _ = companions.load_persona(camp, cslug)
        self.emit({"kind": "tool", "icon": "🗣", "label": f"consulting {meta.get('name', cslug)}", "sub": True})
        brief = companions.brief(camp, cslug, situation=situation, record=True)
        res = self._call([{"role": "system", "content": brief + prompts.COMPANION_WRAP},
                          {"role": "user", "content": situation or "What do you do?"}], None, stream=False,
                         temperature=self.companion["temperature"], max_tokens=self.companion["max_tokens"],
                         model=self.companion["model"] or None)
        said = strip_calls(res["content"]) or "(no answer)"
        return f"{meta.get('name', cslug)} answers (their own choice; you decide outcomes): {said}"

    # ------------------------------------------------------------------ checks after the reply
    def _invented(self, slug: str, reply: str) -> bool:
        """Dice or an attack outcome in the reply that no ./dm command produced this turn."""
        if not self.rolled and _ROLL_TEXT.search(reply):
            return True
        facts = audit.turn_facts(campaign.load(slug))
        fought = any(f["kind"] in ("attack", "damage", "save") for f in facts)
        from robodm.combat import Encounter

        return Encounter(campaign.load(slug)).active and not fought and bool(_FIGHT_TEXT.search(reply))

    def _no_player_lines(self, slug: str, reply: str) -> str:
        """The DM never speaks for the players: drop lines like "Tamsin: I move closer"."""
        names = [pc["name"] for pc in campaign.load(slug).party()]
        if not names:
            return reply
        pat = re.compile(r"(?m)^\s*\**(?:" + "|".join(re.escape(n) for n in names) + r")\**\s*:\s*I\b.*$\n?")
        cleaned = pat.sub("", reply).strip()
        if cleaned != reply.strip() and cleaned:
            self.emit({"kind": "replace", "text": cleaned})
            self.streamed = cleaned
            return cleaned
        return reply

    def _unbacked(self, slug: str, messages: list[dict], reply: str) -> str:
        """The reply shows dice, but nothing was rolled through ./dm: roll for real, or narrate without numbers."""
        self.emit({"kind": "tool", "icon": "🧾", "label": "the reply showed dice that were never rolled — redoing it",
                   "sub": True})
        self.emit({"kind": "replace", "text": ""})
        self.streamed = ""
        from robodm.combat import Encounter

        enc = Encounter(campaign.load(slug))
        hint = ""
        if enc.active:
            cur = enc.current() or {}
            ids = ", ".join(f"{c['id']} ({c['name']})" for c in enc.combatants.values() if c.get("status") != "dead")
            hint = (f"\nThe fight is on. Combatant ids: {ids}. It is {cur.get('name', '?')}'s turn (id {cur.get('id', '?')}). "
                    f"Resolve that turn with e.g. dm combat attack {cur.get('id', '<attacker>')} <target-id> --action <weapon>, "
                    "then dm combat next. If a player acts out of turn, tell them whose turn it is.")
        retry = messages + [{"role": "assistant", "content": reply}, {"role": "user", "content":
                "RoboDM: your reply shows dice or an attack result, but no such roll went through the dm tool this "
                "turn, so it is invented. Run the dm command for it now (for a new fight, start with dm combat "
                "setup), then write the reply from its real result. If no roll is needed, write it without numbers."
                + hint}]
        new = self._loop(slug, retry, allow_tools=True)
        if not self.rolled and _ROLL_TEXT.search(new):
            new = _ROLL_TEXT.sub("", new)       # still invented: at least don't show made-up numbers
        self.emit({"kind": "replace", "text": new})
        self.streamed = new
        return new

    def _receipts(self, slug: str, messages: list[dict], reply: str) -> str:
        problems = audit.narration_problems(campaign.load(slug), reply)
        if not problems or not reply:
            return reply
        self.emit({"kind": "tool", "icon": "🧾", "label": "receipt check: the reply contradicted the dice — rewriting",
                   "sub": True})
        res = self._call(messages + [{"role": "assistant", "content": reply}, {"role": "user", "content":
                         "RoboDM receipt check — your reply contradicts the dice:\n" + "\n".join(f"- {p}" for p in problems)
                         + "\nRewrite your whole reply so it matches the dice exactly. No tools."}], None, stream=False)
        new = strip_calls(res["content"]) or reply
        self.emit({"kind": "replace", "text": new})
        self.streamed = new
        return new

    def _post_audit(self, slug: str, reply: str) -> str:
        camp = campaign.load(slug)
        audit.save_last_reply(camp, reply)
        opened = audit.open_events(camp)
        if not opened:
            return reply
        clean = not audit.narration_problems(camp, reply)
        if clean and (self.lawyer["scope"] == "judgement" or costs.speed(camp) == "fast"):
            evs = audit.events(camp)
            for e in evs:
                if costs.autopassable(e):
                    e["status"] = "passed"
                    e["by"] = f"engine (local, rules-lawyer effort {self.lawyer['effort']}: computed by the engine, receipts agreed)"
            audit._write_events(camp, evs)
        remaining = audit.open_events(campaign.load(slug))
        if not remaining:
            return reply
        self.emit({"kind": "tool", "icon": "⚖️", "label": f"local rules-lawyer: post-audit of {len(remaining)} event(s)",
                   "sub": True})
        packet = audit.pending_packet(campaign.load(slug))
        data = self._lawyer_json([{"role": "system", "content": prompts.LAWYER_SYSTEM},
                                  {"role": "user", "content": packet}],
                                 lambda d: isinstance(d.get("verdicts"), list) and bool(d["verdicts"]))
        verdicts = {str(v.get("id")): v for v in data.get("verdicts", []) if isinstance(v, dict)}
        corrections = []
        by = f"local rules-lawyer ({self.lawyer['model']})"
        for e in remaining:
            v = verdicts.get(e["id"])
            camp = campaign.load(slug)
            if not v or str(v.get("verdict", "")).lower() not in ("pass", "fail"):
                audit.mark(camp, [e["id"]], "unverified", "the local rules-lawyer gave no readable verdict", by=by)
                continue
            if str(v["verdict"]).lower() == "pass":
                audit.mark(camp, [e["id"]], "passed", str(v.get("notes", "")), by=by)
                continue
            notes = str(v.get("notes", "")).strip() or "a rules mistake"
            fix = str(v.get("fix", "")).strip()
            audit.mark(camp, [e["id"]], "failed", notes, fix, by=by)
            fixed = False
            if fix:
                try:
                    argv = runner.parse_command(fix) + ["--fix", e["id"]]
                    code, _, _ = runner.run_cli(argv)
                    fixed = code == 0
                except ValueError:
                    fixed = False
            if not fixed:
                audit.mark(campaign.load(slug), [e["id"]], "resolved", f"noted for the players: {notes}")
            corrections.append(f"⚖️ Rules check: {notes}")
        if corrections:
            extra = "\n\n" + "\n".join(corrections)
            self.emit({"kind": "delta", "text": extra})
            self.streamed += extra
            reply += extra
        return reply

    def _review(self, slug: str, messages: list[dict], reply: str) -> str:
        """xhigh/max effort: one more look at the reply before it's final."""
        res = self._call(messages + [{"role": "assistant", "content": reply}, {"role": "user", "content":
                         "Check your reply once more: does it follow the tool results and the campaign brief, end with a "
                         "clear prompt for the players, and avoid deciding what player characters do? If it's fine, "
                         "answer with exactly OK. Otherwise write the corrected reply. No tools."}], None, stream=False)
        out = strip_calls(res["content"])
        if not out or out.strip().upper().rstrip(".") == "OK":
            return reply
        self.emit({"kind": "replace", "text": out})
        return out

    # ------------------------------------------------------------------ chat memory
    def _path(self, slug: str):
        return campaign.load(slug).path(".cache", "local-chat.json")

    def _history(self, slug: str) -> list[dict]:
        rows = read_json(self._path(slug)) or []
        return [{"role": r["role"], "content": r["content"]} for r in rows[-HISTORY_KEEP:]]

    def _remember(self, slug: str, message: str, reply: str) -> None:
        rows = read_json(self._path(slug)) or []
        rows += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        write_json(self._path(slug), rows[-60:])


def reset(slug: str) -> None:
    try:
        p = campaign.load(slug).path(".cache", "local-chat.json")
        if p.exists():
            p.unlink()
    except RoboDMError:
        pass
