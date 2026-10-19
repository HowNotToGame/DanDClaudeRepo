"""Run a ./dm command in-process and capture its output (the web client and the local DM engine use this).

One command at a time: stdout capture is process-wide, and so are the campaign files.
"""

from __future__ import annotations

import contextlib
import io
import shlex
import threading

CLI_LOCK = threading.RLock()


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    from robodm.cli import main

    out, err = io.StringIO(), io.StringIO()
    with CLI_LOCK, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as e:          # argparse: a malformed command prints usage and exits; don't take us with it
            code = e.code if isinstance(e.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def parse_command(command: str) -> list[str]:
    """'./dm roll 1d20+5', 'dm roll …', 'python -m robodm roll …' or just 'roll …' → argv for cli.main."""
    argv = shlex.split(str(command).strip().strip("`").strip(), posix=True)
    while argv and argv[0] in ("./dm", "dm", ".\\dm", "dm.cmd", "./dm.cmd"):
        argv = argv[1:]
    if len(argv) >= 3 and argv[0].startswith("python") and argv[1:3] == ["-m", "robodm"]:
        argv = argv[3:]
    return argv
