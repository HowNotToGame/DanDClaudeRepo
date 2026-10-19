#!/usr/bin/env sh
# RoboDM command line: ./dm <command> [...]   (see ./dm --help; on Windows cmd/PowerShell use dm.cmd)
#
# Uses the first Python 3.9+ that actually runs. Each candidate is test-run, because on Windows `python3`
# is often the Microsoft Store placeholder: it's on PATH but only prints "Python was not found".
# The choice is remembered in .cache/dm-python (gitignored). If the remembered one disappears, or exits the
# way the placeholder does (9009, seen as 49 in Git Bash), it's forgotten and the candidates are tried again.
# Force one with ROBODM_PYTHON, e.g. ROBODM_PYTHON=/c/Python312/python.exe ./dm status
DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE="${ROBODM_DM_CACHE:-$DIR/.cache/dm-python}"
if [ "${OS:-}" = "Windows_NT" ]; then CANDIDATES="python py python3"; else CANDIDATES="python3 python"; fi
# The repo root goes on sys.path as an argument rather than through PYTHONPATH, whose separator differs
# between Windows (;) and everything else (:). Git Bash converts the /c/... path for Windows Python.
BOOT='import sys; sys.path.insert(0, sys.argv.pop(1)); from robodm.entry import main; sys.exit(main())'

probe() {
  PY=""
  PYARG=""
  for cand in $CANDIDATES; do
    arg=""
    [ "$cand" = "py" ] && arg="-3"
    if "$cand" $arg -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      PY="$(command -v "$cand")"
      PYARG="$arg"
      mkdir -p "$(dirname "$CACHE")" 2>/dev/null && printf '%s\n%s\n' "$PY" "$PYARG" > "$CACHE" 2>/dev/null
      return 0
    fi
  done
  echo "RoboDM needs Python 3.9+, and none of: $CANDIDATES runs." >&2
  echo "On Windows, 'python3' may be the Microsoft Store placeholder: install Python from python.org," >&2
  echo "or point ROBODM_PYTHON at a working python.exe." >&2
  exit 1
}

if [ -n "${ROBODM_PYTHON:-}" ]; then
  PYTHONIOENCODING=utf-8 exec "$ROBODM_PYTHON" -c "$BOOT" "$DIR" "$@"
fi
PY=""
PYARG=""
if [ -f "$CACHE" ]; then
  { IFS= read -r PY; IFS= read -r PYARG; } < "$CACHE"
  [ -n "$PY" ] && [ -x "$PY" ] || PY=""
fi
if [ -n "$PY" ]; then
  PYTHONIOENCODING=utf-8 "$PY" $PYARG -c "$BOOT" "$DIR" "$@"
  status=$?
  if [ "$status" -ne 49 ] && [ "$status" -ne 9009 ]; then exit "$status"; fi
  rm -f "$CACHE"   # the remembered Python now acts like the Store placeholder: look again
fi
probe
PYTHONIOENCODING=utf-8 exec "$PY" $PYARG -c "$BOOT" "$DIR" "$@"
