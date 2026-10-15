#!/usr/bin/env sh
# RoboDM command line: ./dm <command> [...]   (see ./dm --help)
DIR="$(cd "$(dirname "$0")" && pwd)"
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else echo "RoboDM needs Python 3.8+ (python3 not found)." >&2; exit 1; fi
PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" PYTHONIOENCODING=utf-8 exec "$PY" -m robodm "$@"
