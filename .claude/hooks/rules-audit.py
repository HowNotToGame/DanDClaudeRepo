#!/usr/bin/env python3
"""PostToolUse hook: nudge the DM to have the rules-lawyer subagent audit combat.

Fires after `./dm combat end` (audit the whole fight) and, when the campaign's
rules_audit setting is `always`, after `./dm combat next` starts a new round.
Silent otherwise. Reads the campaign setting so `off` disables it.
"""
import json
import os
import re
import subprocess
import sys

# A real invocation of the CLI, not the text appearing inside a heredoc or a quoted string.
_CALL = r"(?:^|[;&|(]\s*|\n\s*)(?:\./dm|python3? -m robodm)\s+(?:-c\s+\S+\s+)?combat\s+{action}\b"


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    cmd = (data.get("tool_input") or {}).get("command", "")
    ended = re.search(_CALL.format(action="end"), cmd)
    advanced = re.search(_CALL.format(action="next"), cmd)
    if not ended and not advanced:
        return 0
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        show = subprocess.run([os.path.join(root, "dm"), "campaign", "show"], capture_output=True, text=True, timeout=20)
        mode = json.loads(show.stdout).get("settings", {}).get("rules_audit", "key")
    except Exception:
        mode = "key"
    if mode == "off":
        return 0
    response = json.dumps(data.get("tool_response", ""))
    msg = None
    if ended and "ended after" in response:
        msg = ("RoboDM rules audit: the combat just ended. Launch the rules-lawyer subagent (in the background if "
               "possible) to audit this fight: give it the encounter log from campaigns/<slug>/journal/combat-log.md "
               "(latest section) plus any rulings you made by hand. Apply its corrections with ./dm commands and tell "
               "the players in one line.")
    elif mode == "always" and advanced and "**Round" in response:
        msg = ("RoboDM rules audit (rules_audit=always): a new round began. Have the rules-lawyer subagent check the "
               "previous round (`./dm combat log --n 30`) before or while you narrate; fix anything it flags.")
    if msg:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
