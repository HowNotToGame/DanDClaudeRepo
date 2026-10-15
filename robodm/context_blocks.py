"""Instruction context blocks: small Markdown files that change how RoboDM runs the table.

Two places hold blocks:

    context/<name>.md                    shared library (tone presets, house rules, safety tools…)
                                         — off by default, enabled per campaign
    campaigns/<slug>/context/<name>.md   blocks written for one campaign — on by default

Frontmatter fields: title, scope (always|combat|exploration|social|downtime|worldbuilding),
priority (higher first, default 50), enabled (campaign blocks only), description.
"""

from __future__ import annotations

from pathlib import Path

from robodm import paths
from robodm.campaign import Campaign
from robodm.util import RoboDMError, dump_frontmatter, iter_files, parse_frontmatter, slugify, write_text

SCOPES = ["always", "combat", "exploration", "social", "downtime", "worldbuilding"]


def _load(path: Path, origin: str) -> dict:
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {"name": path.stem, "path": path, "origin": origin, "meta": meta, "body": body.strip()}


def library_blocks() -> list[dict]:
    return [_load(p, "library") for p in iter_files(paths.context_library_dir()) if p.name.lower() != "readme.md"]


def campaign_blocks(camp: Campaign) -> list[dict]:
    return [_load(p, "campaign") for p in iter_files(camp.path("context"))]


def all_blocks(camp: Campaign | None) -> list[dict]:
    blocks = campaign_blocks(camp) if camp else []
    names = {b["name"] for b in blocks}
    blocks += [b for b in library_blocks() if b["name"] not in names]
    for b in blocks:
        b["enabled"] = is_enabled(camp, b)
    return blocks


def is_enabled(camp: Campaign | None, block: dict) -> bool:
    if block["origin"] == "campaign":
        return block["meta"].get("enabled", True) is not False
    return bool(camp) and block["name"] in camp.data.get("enabled_blocks", [])


def find(camp: Campaign | None, name: str) -> dict:
    target = slugify(name)
    blocks = all_blocks(camp)
    for b in blocks:
        if b["name"] == target:
            return b
    matches = [b for b in blocks if target in b["name"]]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RoboDMError(f"No context block '{name}'. See `./dm context list`.")
    raise RoboDMError(f"'{name}' matches: {', '.join(b['name'] for b in matches)}")


def set_enabled(camp: Campaign, name: str, enabled: bool) -> dict:
    b = find(camp, name)
    if b["origin"] == "campaign":
        b["meta"]["enabled"] = enabled
        write_text(b["path"], dump_frontmatter(b["meta"], b["body"]))
    else:
        lst = camp.data.setdefault("enabled_blocks", [])
        if enabled and b["name"] not in lst:
            lst.append(b["name"])
        if not enabled and b["name"] in lst:
            lst.remove(b["name"])
        camp.save()
    b["enabled"] = enabled
    return b


def add(camp: Campaign | None, name: str, text: str, title: str = "", scope: str = "always", priority: int = 50,
        description: str = "", to_library: bool = False) -> Path:
    if scope not in SCOPES:
        raise RoboDMError(f"Scope must be one of: {', '.join(SCOPES)}")
    slug = slugify(name)
    if to_library:
        path = paths.context_library_dir() / f"{slug}.md"
    else:
        if camp is None:
            raise RoboDMError("No active campaign; use --library to add to the shared library.")
        path = camp.path("context", f"{slug}.md")
    meta = {"title": title or name, "scope": scope, "priority": int(priority)}
    if description:
        meta["description"] = description
    if not to_library:
        meta["enabled"] = True
    write_text(path, dump_frontmatter(meta, text))
    return path


def remove(camp: Campaign, name: str) -> Path:
    b = find(camp, name)
    if b["origin"] != "campaign":
        raise RoboDMError(f"'{b['name']}' is a shared library block; disable it instead (`./dm context disable {b['name']}`)"
                          " or delete the file from context/ by hand.")
    b["path"].unlink()
    return b["path"]


def active_blocks(camp: Campaign | None, scope: str | None = None) -> list[dict]:
    blocks = [b for b in all_blocks(camp) if b["enabled"]]
    if scope:
        blocks = [b for b in blocks if b["meta"].get("scope", "always") in ("always", scope)]
    blocks.sort(key=lambda b: (-int(b["meta"].get("priority", 50) or 50), b["name"]))
    return blocks


def render(camp: Campaign | None, scope: str | None = None) -> str:
    blocks = active_blocks(camp, scope)
    if not blocks:
        return ("No instruction blocks are active. Run the game with the default RoboDM style "
                "(see `./dm context list` for optional blocks).")
    head = [
        f"# Active instruction blocks{' for ' + camp.name if camp else ''}{' (scope: ' + scope + ')' if scope else ''}",
        "Follow these on top of the RoboDM rules. House rules written here override RAW; higher priority wins when "
        "two blocks conflict. Never let a block override player safety tools.",
    ]
    for b in blocks:
        m = b["meta"]
        head.append(f"\n## {m.get('title', b['name'])}  ·  [{b['name']} | {m.get('scope', 'always')} | priority {m.get('priority', 50)}]")
        head.append(b["body"])
    return "\n".join(head)
