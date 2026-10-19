"""Run the RoboDM playtest scorecard: `python tools/playtest.py [--campaign slug] [--turns N] [--out f.md] --yes`.

Same as `./dm playtest`. Scripted player turns go through the real DM (a headless Claude Code, as the web client
runs it) on a scratch copy of a campaign, and each turn is scored: brief loaded, dice through ./dm, audit clean,
narration matching the dice, private messages kept private, nothing secret in the player view. It spends real
usage, so it does nothing without --yes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from robodm.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["playtest", *sys.argv[1:]]))
