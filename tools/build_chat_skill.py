#!/usr/bin/env python3
"""Package RoboDM as a skill zip you can upload to claude.ai (Settings → Capabilities → Skills).

    python3 tools/build_chat_skill.py                 # skill + code + context library + references
    python3 tools/build_chat_skill.py --with-data     # also bundle local 5etools data (core books by default)
    python3 tools/build_chat_skill.py --with-data --sources PHB,MM,DMG,XGE,TCE,VGM,MTF

The bundle is written to dist/robodm-skill.zip. Data is only included from your own local copy
(`./dm rules fetch` first) and is never committed to the repository.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_SOURCES = ["PHB", "MM", "DMG", "XGE", "TCE"]


def add_tree(z: zipfile.ZipFile, src: Path, dest: str, skip: tuple = ()) -> int:
    n = 0
    for p in sorted(src.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith((".pyc", ".tmp"))                 and p.relative_to(src).parts[0] not in skip:
            z.write(p, f"{dest}/{p.relative_to(src).as_posix()}")
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--with-data", action="store_true", help="bundle local 5etools data")
    ap.add_argument("--sources", default=",".join(DEFAULT_SOURCES),
                    help="spell/monster books to include with --with-data (ALL for everything)")
    ap.add_argument("--out", default=str(ROOT / "dist" / "robodm-skill.zip"))
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    top = "robodm"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT / "chat" / "SKILL.md", f"{top}/SKILL.md")
        add_tree(z, ROOT / "robodm", f"{top}/robodm", skip=("web",))   # the web client needs a local machine
        add_tree(z, ROOT / "context", f"{top}/context")
        for ref in ("commands.md", "combat.md"):
            z.write(ROOT / ".claude" / "skills" / "dm" / "reference" / ref, f"{top}/reference/{ref}")
        z.write(ROOT / ".claude" / "agents" / "rules-lawyer.md", f"{top}/reference/rules-audit-procedure.md")
        if args.with_data:
            data = ROOT / "data" / "5etools"
            if not (data / "_meta.json").exists():
                print("No local data — run `./dm rules fetch` first.", file=sys.stderr)
                return 1
            wanted = None if args.sources.upper() == "ALL" else {s.strip().upper() for s in args.sources.split(",")}
            keep: set[str] = set()
            for folder in ("spells", "bestiary"):
                idx = json.loads((data / folder / "index.json").read_text(encoding="utf-8"))
                keep.update(f"{folder}/{f}" for src, f in idx.items() if wanted is None or src.upper() in wanted)
            n = 0
            for p in sorted(data.rglob("*.json")):
                rel = p.relative_to(data).as_posix()
                folder = rel.split("/")[0]
                if p.name == "_index.json":
                    continue  # rebuilt below for the subset
                if folder in ("spells", "bestiary") and p.name not in ("index.json", "sources.json", "legendarygroups.json") \
                        and rel not in keep:
                    continue
                if folder == "book":
                    continue
                z.write(p, f"{top}/data/5etools/{rel}")
                n += 1
            # Prebuilt index for the subset so lookups are instant in the sandbox.
            from robodm.rules.index import INDEX_FILE, INDEX_VERSION, RulesDB

            db = RulesDB(data)
            entries = db.build_index()
            if wanted is not None:
                entries = [e for e in entries if not (e["t"] in ("spell", "monster") and e["f"] not in keep)]
            meta = json.loads((data / "_meta.json").read_text(encoding="utf-8"))
            stamp = f"{meta.get('fetched_at', '')}|{meta.get('files', '')}|{meta.get('bytes', '')}"
            z.writestr(f"{top}/data/5etools/{INDEX_FILE}",
                       json.dumps({"version": INDEX_VERSION, "stamp": stamp, "entries": entries}))
            print(f"Bundled {n} data files ({'all books' if wanted is None else ', '.join(sorted(wanted))}).")
    size = out.stat().st_size / 1e6
    print(f"Wrote {out} ({size:.1f} MB). Upload it in claude.ai → Settings → Capabilities → Skills.")
    if size > 30:
        print("Warning: large bundle; if the upload is rejected, rebuild with fewer --sources.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
