"""Where RoboDM keeps things.

By default everything lives next to the `robodm` package (the repo root):

    campaigns/<slug>/   one folder per campaign (memory, party, lore, journal, combat)
    context/            the shared library of instruction context blocks
    data/5etools/       local copy of the 5etools 2014 data (fetched, not committed)

Override with environment variables when the package is installed elsewhere
(for example inside a claude.ai skill, where the skill folder is read-only):

    ROBODM_HOME   base directory for campaigns/ and context/
    ROBODM_DATA   directory holding the 5etools data
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def home() -> Path:
    env = os.environ.get("ROBODM_HOME")
    return Path(env).expanduser().resolve() if env else PACKAGE_DIR.parent


def campaigns_dir() -> Path:
    return home() / "campaigns"


def context_library_dir() -> Path:
    # The shipped library lives beside the package even when ROBODM_HOME moves campaigns.
    local = home() / "context"
    if local.exists():
        return local
    return PACKAGE_DIR.parent / "context"


def data_dir() -> Path:
    env = os.environ.get("ROBODM_DATA")
    if env:
        return Path(env).expanduser().resolve()
    return home() / "data" / "5etools"


def active_campaign_file() -> Path:
    return campaigns_dir() / ".active"
