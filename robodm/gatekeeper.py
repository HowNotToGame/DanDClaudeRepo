"""PreToolUse rules gate for Claude Code (`./dm hook pretool`).

Runs before every Bash/PowerShell/Edit/Write and code-running MCP tool call, so it imports nothing heavy:
only the rules-lawyer subagent may record `./dm audit` verdicts, and nobody edits game state (combat.json,
party sheets, audit records) behind the engine's back.
"""

from __future__ import annotations

import os
import re

# `./dm`, `.\dm`, `dm.cmd`, `python -m robodm`, `py -3 -m robodm` … followed by an audit verdict
_VERDICT_CMD = re.compile(r"(?:\bdm(?:\.cmd)?|-m\s+robodm)\s+(?:(?:-c|--campaign)\s+\S+\s+)?audit\s+(clear|pass|fail|reset-cleared)\b",
                          re.IGNORECASE)
_ENGINE_IMPORT = re.compile(r"robodm\.audit|from\s+robodm\s+import[^\n]*\baudit\b")
_DIRECT_WRITE = re.compile(r"campaigns[\\/][^\s'\"]+[\\/](?:combat\.json|party[\\/]|audit[\\/])", re.IGNORECASE)
_PROTECTED_PATH = re.compile(r"campaigns[\\/][^\\/]+[\\/](?:combat\.json$|party[\\/][^\\/]+\.json$|audit[\\/])", re.IGNORECASE)
_WRITE_HINT = re.compile(r">|\btee\b|\bsed\s+-i|\bcp\b|\bmv\b|\brm\b|\bpython|\bpy\s+-3|\bperl\b|open\(|"
                         r"\b(?:Set|Add|Clear)-Content\b|\bOut-File\b|\b(?:Remove|Copy|Move|New|Rename)-Item\b|"
                         r"\bWriteAll(?:Text|Lines|Bytes)\b", re.IGNORECASE)
_CODE_TOOL = re.compile(r"exec|run|shell|bash|eval|python|terminal|command|script", re.IGNORECASE)


def _strings(x) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    if isinstance(x, list):
        return [s for v in x for s in _strings(v)]
    return []


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


_DM_PREFIX = re.compile(r"^\s*(?:\./dm|\.\\dm|dm|dm\.cmd|python3?\s+-m\s+robodm|py\s+-3\s+-m\s+robodm)\s+"
                        r"(?:(?:-c|--campaign)\s+\S+\s+)?(?P<rest>.+)$", re.IGNORECASE | re.DOTALL)
_COMPANION_OWN = re.compile(r"^companion\s+(?:brief|recall|remember|agenda|attitude|interject)\s+(?P<slug>[a-z0-9-]+)\b")
_NPC_OWN = re.compile(r"^npc\s+(?:brief|recall|remember|attitude)\s+(?P<slug>[a-z0-9-]+)\b")
_COMPANION_READ = re.compile(r"^(?:rules\s+(?:show|search)|combat\s+(?:status|range)|combat\s+map\s+show)\b")
_SHELL_CONTROL = re.compile(r"[;|&<>`]|\$\(")
# Harmless wrappers models put around a command: `cd <this project> && …` in front, and `2>&1` or `| head …` behind.
_CD_PREFIX = re.compile(r"^\s*(?:cd|Set-Location|sl)\s+(?P<dir>\"[^\"]+\"|'[^']+'|\S+)\s*(?:&&|;)\s*", re.IGNORECASE)
_OUTPUT_TAIL = re.compile(r"(?:\s+2>&1)?(?:\s*\|\s*(?:head|Select-Object)(?:\s+-(?:[nc]|First)\s*\d+|\s+-\d+)?)?\s*$",
                          re.IGNORECASE)


def _norm_dir(p: str) -> str:
    return os.path.normcase(os.path.normpath(p.strip().strip("\"'"))).rstrip("\\/")


def _project_dirs(data: dict) -> set[str]:
    dirs = {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}     # this checkout of RoboDM
    for d in (data.get("cwd"), os.environ.get("CLAUDE_PROJECT_DIR")):
        if d:
            dirs.add(d)
    return {_norm_dir(d) for d in dirs}


def _companion_command(cmd: str, data: dict) -> str:
    """The command with the harmless wrappers removed: a `cd` into this project, this project's own launcher
    spelled as a full path, a trailing `2>&1` or `| head -n N`. Anything else is left alone (and refused)."""
    m = _CD_PREFIX.match(cmd)
    if m and _norm_dir(m.group("dir")) in _project_dirs(data):
        cmd = cmd[m.end():]
    cmd = _OUTPUT_TAIL.sub("", cmd.strip())
    launcher = re.match(r"^\s*(?:\"(?P<dq>[^\"]*?[\\/]dm(?:\.cmd)?)\"|'(?P<sq>[^']*?[\\/]dm(?:\.cmd)?)'|"
                        r"(?P<bare>[^\"'\s]*?[\\/]dm(?:\.cmd)?))(?=\s)", cmd)
    path = launcher and (launcher.group("dq") or launcher.group("sq") or launcher.group("bare"))
    if path and _norm_dir(os.path.dirname(path)) in _project_dirs(data):
        cmd = "./dm" + cmd[launcher.end():]
    return cmd


def _companion(agent: str, tool: str, tin: dict, data: dict | None = None) -> dict | None:
    """Companion and NPC subagents see the world only through their brief: read-only ./dm commands and their own
    memory. Companion agents use `./dm companion …`; the `npc` agent uses `./dm npc …`."""
    npc = agent == "npc"
    family = "npc brief|recall|remember|attitude" if npc else "companion brief|recall|remember|agenda|attitude|interject"
    if tool not in ("Bash", "PowerShell"):
        return _deny(f"You work only through `./dm {family.split()[0]} …` commands; you can't touch files or tools.")
    cmd = _companion_command(str(tin.get("command", "")), data or {})
    m = _DM_PREFIX.match(cmd)
    bare = re.sub(r'"(?:\\.|[^"\\])*"|\'[^\']*\'', '""', cmd)   # quoted text (a memory, a query) may say anything
    if _SHELL_CONTROL.search(bare) or "\n" in bare.strip() or not m:
        return _deny(f"{'NPCs' if npc else 'Companions'} can run single `./dm {family} <npc>` "
                     "commands, `./dm rules show|search`, and `./dm combat status|range|map show --players`. Nothing else. "
                     f"Run it from the current folder exactly as written, e.g. `./dm -c <campaign> {family.split()[0]} brief "
                     "<npc>`, with nothing chained to it.")
    rest = m.group("rest").strip()
    own = (_NPC_OWN if npc else _COMPANION_OWN).match(rest)
    if own and npc:
        return None                       # the generic npc agent plays whichever NPC the DM names
    if own:
        slug = own.group("slug")
        if agent != "companion" and not agent.endswith("-" + slug):
            return _deny(f"You are {agent}: you can only read and write your own companion records, not {slug}'s.")
        return None
    if _COMPANION_READ.match(rest):
        if rest.startswith(("combat status", "combat map")) and "--players" not in rest:
            return _deny("Add --players: you see the fight the way your character does (no hidden numbers).")
        return None
    return _deny("You can't roll dice, read other records or change the game: propose it in your answer and the DM "
                 "will run it.")


def pretool(data: dict) -> dict | None:
    """PreToolUse: only the rules-lawyer records verdicts; companions stay inside their knowledge; nobody edits
    game state behind the engine's back."""
    tool = data.get("tool_name", "")
    tin = data.get("tool_input") or {}
    agent = str(data.get("agent_type") or "")
    if agent.startswith("companion") or agent == "npc":
        return _companion(agent, tool, tin, data)
    runs_code = tool.startswith("mcp__") and _CODE_TOOL.search(tool.split("__")[-1])
    if tool in ("Bash", "PowerShell") or runs_code:
        # MCP tools that run code (e.g. context-mode's ctx_execute / ctx_batch_execute) get the same checks
        # as Bash, over every string in their input, so they can't be used to slip past the gate.
        cmd = "\n".join(_strings(tin)) if runs_code else tin.get("command", "")
        m = _VERDICT_CMD.search(cmd)
        if m:
            verb = m.group(1)
            if verb == "reset-cleared":
                return _deny("Only the user can reset rules-lawyer clearances (run it yourself in a terminal).")
            if re.search(r"--self\b", cmd):
                return _deny("`--self` verdicts are for claude.ai only. In Claude Code the rules-lawyer subagent records verdicts.")
            if data.get("agent_type") != "rules-lawyer":
                return _deny(f"Only the rules-lawyer subagent can run `./dm audit {verb}`. Launch it with the output of "
                             "`./dm audit brief …` (pre-check) or `./dm audit pending` (post-audit).")
        if _ENGINE_IMPORT.search(cmd) and data.get("agent_type") != "rules-lawyer":
            return _deny("Use the `./dm audit …` commands; audit records aren't changed from scripts.")
        if _DIRECT_WRITE.search(cmd) and _WRITE_HINT.search(cmd):
            return _deny("Game state (combat.json, party sheets, audit records) changes only through `./dm` commands, "
                         "so the rules gate sees every change.")
        return None
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(tin.get("file_path") or tin.get("notebook_path") or "")
        if _PROTECTED_PATH.search(path):
            return _deny("Game state (combat.json, party sheets, audit records) changes only through `./dm` commands, "
                         "so the rules gate sees every change.")
    return None
