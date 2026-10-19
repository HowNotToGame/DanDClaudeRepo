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


def command_words(argv: list[str]) -> tuple[str, ...]:
    """(command, action) the way ./dm parses argv, for allow/deny lists: `-c <slug>` or an option's value in front
    of the action can't hide what runs. argv ./dm can't parse falls back to its plain words (it won't run anyway)."""
    from robodm.cli import build_parser

    try:
        with CLI_LOCK, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            args, _ = build_parser().parse_known_args(argv)
    except SystemExit:
        return tuple(a for a in argv if not a.startswith("-"))[:2]
    action = getattr(args, "action", None)
    return (args.command,) if action is None else (args.command, str(action))


def parse_command(command: str) -> list[str]:
    """'./dm roll 1d20+5', 'dm roll …', 'python -m robodm roll …' or just 'roll …' → argv for cli.main."""
    argv = shlex.split(str(command).strip().strip("`").strip(), posix=True)
    while argv and argv[0] in ("./dm", "dm", ".\\dm", "dm.cmd", "./dm.cmd"):
        argv = argv[1:]
    if len(argv) >= 3 and argv[0].startswith("python") and argv[1:3] == ["-m", "robodm"]:
        argv = argv[3:]
    return argv
