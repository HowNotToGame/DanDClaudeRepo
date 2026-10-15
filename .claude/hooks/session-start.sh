#!/bin/bash
# RoboDM SessionStart hook.
#  - startup (cloud): download the 5etools rules data if missing (it's gitignored, so a
#    fresh cloud clone doesn't have it). Never blocks the session on failure.
#  - compact / clear / resume: if a play session is in progress, re-inject the campaign
#    brief and active instruction blocks so the DM picks up exactly where it left off.
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT" || exit 0

INPUT="$(cat 2>/dev/null || true)"
SOURCE="$(printf '%s' "$INPUT" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("source",""))
except Exception: print("")' 2>/dev/null)"

if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && [ ! -f data/5etools/_meta.json ]; then
  if ! ./dm rules fetch --if-missing >/tmp/robodm-fetch.log 2>&1; then
    echo "RoboDM: could not download the 5etools rules data (see /tmp/robodm-fetch.log)."
    echo "Rules lookups will fail until \`./dm rules fetch\` succeeds — the environment's network policy must allow raw.githubusercontent.com."
  fi
fi

case "$SOURCE" in
  compact|clear|resume)
    if ./dm status 2>/dev/null | grep -q "(in progress)"; then
      echo "RoboDM: a play session is in progress. Context was ${SOURCE}ed — here is the campaign state; keep running the game with the dm skill."
      ./dm memory brief 2>/dev/null
      echo
      ./dm context render 2>/dev/null
      echo
      ./dm combat status 2>/dev/null | grep -v "^No active combat" || true
      exit 0
    fi
    ;;
esac

./dm status 2>/dev/null | head -4 | sed 's/^/RoboDM: /' || true
exit 0
