"""Export / import a whole campaign as one JSON save file.

Used for claude.ai chats (no persistent disk: download the save at the end,
upload it next time), backups, and moving a campaign between machines.
"""

from __future__ import annotations

import json
from pathlib import Path

from robodm import lore, memory, paths
from robodm.campaign import Campaign, pc_summary, set_active
from robodm.util import RoboDMError, now_iso, write_text

FORMAT = "robodm-campaign"
VERSION = 1
SKIP = {".gitkeep"}


def _campaign_files(camp: Campaign) -> list[Path]:
    """Files that belong in a save: everything except derived caches (.cache/) and temp files."""
    return [p for p in sorted(camp.dir.rglob("*"))
            if p.is_file() and p.name not in SKIP and not p.name.endswith(".tmp")
            and ".cache" not in p.relative_to(camp.dir).parts]


def autosave(camp: Campaign | None, folder: Path) -> Path | None:
    """claude.ai: keep <folder>/<slug>.robodm.json current after every command (ROBODM_AUTOSAVE=<folder>),
    so the latest save is always ready to download. Only rewrites it when a campaign file changed."""
    if camp is None:
        return None
    newest = max((p.stat().st_mtime_ns for p in _campaign_files(camp)), default=0)
    out = folder / f"{camp.slug}.robodm.json"
    stamp = camp.path(".cache", "autosave.json")
    try:
        last = json.loads(stamp.read_text(encoding="utf-8")).get("newest", -1) if stamp.exists() else -1
    except ValueError:
        last = -1
    if out.exists() and newest == last:
        return out
    export(camp, out)
    write_text(stamp, json.dumps({"newest": newest, "file": str(out), "at": now_iso()}))
    return out


def export(camp: Campaign, out: Path | None = None) -> Path:
    files = {}
    for p in _campaign_files(camp):
        try:
            files[p.relative_to(camp.dir).as_posix()] = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary reference files are not bundled
    bundle = {"format": FORMAT, "version": VERSION, "slug": camp.slug, "name": camp.name, "exported": now_iso(),
              "files": files}
    out = out or (paths.home() / f"{camp.slug}.robodm.json")
    write_text(out, json.dumps(bundle, ensure_ascii=False, indent=1))
    return out


def import_bundle(path: Path, slug: str | None = None, overwrite: bool = False) -> Campaign:
    try:
        bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RoboDMError(f"Can't read save file {path}: {e}") from e
    if bundle.get("format") != FORMAT:
        raise RoboDMError("That file isn't a RoboDM campaign save.")
    slug = slug or bundle["slug"]
    dest = paths.campaigns_dir() / slug
    if dest.exists() and any(dest.iterdir()) and not overwrite:
        raise RoboDMError(f"Campaign '{slug}' already exists here. Pass --overwrite to replace it, or --as <new-slug>.")
    root = dest.resolve()
    for rel, text in bundle["files"].items():
        target = (dest / rel).resolve()
        if root not in target.parents:
            raise RoboDMError(f"Unsafe path in save file: {rel}")
        write_text(target, text)
    data = json.loads((dest / "campaign.json").read_text(encoding="utf-8"))
    data["slug"] = slug
    write_text(dest / "campaign.json", json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    set_active(slug)
    return Campaign(slug)


def export_markdown(camp: Campaign, out: Path | None = None) -> Path:
    """Readable campaign digest — paste into a Claude Project's knowledge for chat-only play."""
    parts = [memory.brief(camp, recent=40, full=True), "\n---\n# Party sheets"]
    for pc in camp.party():
        parts.append(pc_summary(pc, full=True))
    quests = camp.quests()
    if quests:
        parts.append("\n---\n# All quests")
        parts.extend(f"- {memory.fmt_quest(q)}" for q in quests)
    summaries = camp.session_summaries(limit=999)
    if summaries:
        parts.append("\n---\n# Session summaries")
        parts.extend(f"**Session {n}:** {text}" for n, text in reversed(summaries))
    mems = memory.list_memories(camp)
    if mems:
        parts.append("\n---\n# All memories")
        parts.extend(f"- {memory.fmt_memory(m)}" for m in mems)
    entries = lore.all_entries(camp)
    if entries:
        parts.append("\n---\n# World lore")
        for e in sorted(entries, key=lambda x: (str(x["meta"].get("type")), str(x["meta"].get("name")))):
            parts.append("\n" + lore.fmt_entry(e, full=True))
    out = out or (paths.home() / f"{camp.slug}-digest.md")
    write_text(out, "\n".join(parts) + "\n")
    return out
