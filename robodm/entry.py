"""Process entry point used by the `dm` / `dm.cmd` launchers and `python -m robodm`.

`dm hook <mode>` skips loading the full CLI: PreToolUse runs before every tool call, so its import cost
matters. Everything else goes to robodm.cli.main.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "hook":
        from robodm import hooks

        if argv[1] in hooks.MODES:
            return hooks.main(argv[1])
    from robodm.cli import main as cli_main

    return cli_main(argv)
