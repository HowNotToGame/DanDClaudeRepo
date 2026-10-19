"""Download the 5etools (2014 rules) data to a local folder.

The data is fetched onto *your* machine/session on demand and is never
committed to this repository (see .gitignore). Sources tried in order:

    local   http://localhost:5050/data (your own 5e.tools server; override with
            the ROBODM_LOCAL_5ETOOLS env var, e.g. http://localhost:5050)
    github  raw files from the 5etools 2014 source mirror on GitHub
    site    https://2014.5e.tools/data (the live site serves the same JSON)

Use `--sources PHB,MM,...` to limit spells and monsters to specific books
(smaller download, handy for phones or claude.ai skill bundles).
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from robodm.util import RoboDMError, now_iso, write_json

MIRRORS = {
    "local": os.environ.get("ROBODM_LOCAL_5ETOOLS", "http://localhost:5050").rstrip("/") + "/data",
    "github": "https://raw.githubusercontent.com/5etools-mirror-3/5etools-2014-src/main/data",
    "site": "https://2014.5e.tools/data",
}

CORE_FILES = [
    "spells/index.json",
    "spells/sources.json",
    "bestiary/index.json",
    "bestiary/legendarygroups.json",
    "class/index.json",
    "items.json",
    "items-base.json",
    "magicvariants.json",
    "conditionsdiseases.json",
    "actions.json",
    "variantrules.json",
    "feats.json",
    "races.json",
    "backgrounds.json",
    "optionalfeatures.json",
    "skills.json",
    "senses.json",
    "languages.json",
    "deities.json",
    "trapshazards.json",
    "generated/bookref-quick.json",
]

# Book ids for --books (full rules text, searchable with `rules search --type book`).
BOOK_FILES = {
    "PHB": "book/book-phb.json",
    "DMG": "book/book-dmg.json",
    "MM": "book/book-mm.json",
    "XGE": "book/book-xge.json",
    "TCE": "book/book-tce.json",
    "VGM": "book/book-vgm.json",
    "MTF": "book/book-mtf.json",
    "SCAG": "book/book-scag.json",
}

# Fetched by default: the full rules text the rules-lawyer searches (`rules search … --type book --text`).
DEFAULT_BOOKS = ["PHB", "DMG", "XGE", "TCE"]

META_FILE = "_meta.json"


def _ssl_context() -> ssl.SSLContext:
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        path = os.environ.get(var)
        if path and Path(path).exists():
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


def _get(url: str, ctx: ssl.SSLContext, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RoboDM/1.0 (+5etools data fetch)"})
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except Exception as e:  # network hiccup: back off and retry
            last = e
        time.sleep(2 ** attempt)
    raise last  # type: ignore[misc]


def is_fetched(data_dir: Path) -> bool:
    return (data_dir / META_FILE).exists()


def fetch(
    data_dir: Path,
    mirror: str = "auto",
    sources: list[str] | None = None,
    books: list[str] | None = None,
    workers: int = 8,
    log=print,
) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    ctx = _ssl_context()
    mirrors = list(MIRRORS) if mirror == "auto" else [mirror]
    base = None
    errors = []
    for name in mirrors:
        if name not in MIRRORS:
            raise RoboDMError(f"Unknown mirror '{name}'. Choose from: auto, {', '.join(MIRRORS)}")
        try:
            _get(f"{MIRRORS[name]}/spells/index.json", ctx, retries=1 if name == "local" else 2)
            base = MIRRORS[name]
            log(f"Using mirror: {name} ({base})")
            break
        except Exception as e:
            errors.append(f"{name}: {e}")
    if base is None:
        raise RoboDMError(
            "Could not reach any 5etools data mirror.\n  " + "\n  ".join(errors)
            + "\nCheck the network (cloud sessions: the environment's network policy must allow"
            " raw.githubusercontent.com or 2014.5e.tools)."
        )

    wanted_sources = {s.upper() for s in sources} if sources else None
    total_bytes = 0
    files_done: list[str] = []

    def save(rel: str) -> int:
        blob = _get(f"{base}/{rel}", ctx)
        json.loads(blob)  # validate before writing
        dest = data_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        return len(blob)

    def run_batch(rels: list[str], optional: bool = False) -> None:
        nonlocal total_bytes
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(save, r): r for r in rels}
            for fut in as_completed(futures):
                rel = futures[fut]
                try:
                    total_bytes += fut.result()
                    files_done.append(rel)
                except urllib.error.HTTPError as e:
                    if optional and e.code == 404:
                        log(f"  skipped {rel} (not on mirror)")
                    else:
                        raise RoboDMError(f"Failed to download {rel}: {e}") from e
                except Exception as e:
                    raise RoboDMError(f"Failed to download {rel}: {e}") from e

    log(f"Downloading {len(CORE_FILES)} core files…")
    run_batch(CORE_FILES)

    batch: list[str] = []
    for index_rel, folder in (("spells/index.json", "spells"), ("bestiary/index.json", "bestiary")):
        idx = json.loads((data_dir / index_rel).read_text(encoding="utf-8"))
        for src, fname in idx.items():
            if wanted_sources is None or src.upper() in wanted_sources:
                batch.append(f"{folder}/{fname}")
    cls_idx = json.loads((data_dir / "class/index.json").read_text(encoding="utf-8"))
    batch.extend(f"class/{fname}" for fname in cls_idx.values())
    log(f"Downloading {len(batch)} spell/bestiary/class files…")
    run_batch(batch)

    if books:
        rels = []
        for b in books:
            rel = BOOK_FILES.get(b.upper()) or f"book/book-{b.lower()}.json"
            rels.append(rel)
        log(f"Downloading {len(rels)} book(s)…")
        run_batch(rels, optional=True)

    meta = {
        "fetched_at": now_iso(),
        "mirror": base,
        "sources": sorted(wanted_sources) if wanted_sources else "all",
        "books": [b.upper() for b in books] if books else [],
        "files": len(files_done),
        "bytes": total_bytes,
        "ruleset": "D&D 5e (5etools local server)" if base == MIRRORS["local"] else "D&D 5e 2014 (5etools)",
    }
    write_json(data_dir / META_FILE, meta)
    log(f"Fetched {len(files_done)} files ({total_bytes / 1_000_000:.1f} MB) into {data_dir}")
    return meta
