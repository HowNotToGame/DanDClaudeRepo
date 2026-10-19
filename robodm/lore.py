"""World lore: one Markdown file per entry with frontmatter, plus reference sources.

    campaigns/<slug>/lore/<type>/<entry>.md     canon entries (npc, location, faction, …)
    campaigns/<slug>/lore/_sources/<file>       reference material to build lore from
    campaigns/<slug>/lore/INDEX.md              generated world bible index

Entry frontmatter: name, type, summary (one line), tags, status, related, canon (true|draft).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from robodm.campaign import Campaign
from robodm.util import (
    RoboDMError,
    dump_frontmatter,
    iter_files,
    now_iso,
    parse_csv_list,
    parse_frontmatter,
    rank_documents,
    slugify,
    truncate,
    write_text,
)

LORE_TYPES = ["world", "region", "location", "settlement", "faction", "npc", "deity", "history", "culture", "creature",
              "item", "language", "plane", "event", "other"]


def lore_dir(camp: Campaign) -> Path:
    return camp.path("lore")


def all_entries(camp: Campaign) -> list[dict]:
    out = []
    for p in iter_files(lore_dir(camp)):
        if p.name == "INDEX.md" or "_sources" in p.parts:
            continue
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        out.append({"slug": p.stem, "path": p, "meta": meta, "body": body.strip()})
    return out


def find(camp: Campaign, name: str) -> dict:
    target = slugify(name)
    entries = all_entries(camp)
    for e in entries:
        if e["slug"] == target or slugify(str(e["meta"].get("name", ""))) == target:
            return e
    aliases = [e for e in entries if target in [slugify(str(a)) for a in e["meta"].get("aliases", []) or []]]
    if len(aliases) == 1:
        return aliases[0]
    partial = [e for e in entries if target in e["slug"]]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise RoboDMError(f"'{name}' matches several entries: {', '.join(e['slug'] for e in partial)}")
    raise RoboDMError(f"No lore entry '{name}'. Try `./dm lore search {name}`.")


def add(camp: Campaign, name: str, ltype: str, text: str, summary: str = "", tags: str = "", status: str = "",
        related: str = "", aliases: str = "", canon: bool = True, overwrite: bool = False) -> Path:
    ltype = ltype.lower()
    if ltype not in LORE_TYPES:
        raise RoboDMError(f"Lore type must be one of: {', '.join(LORE_TYPES)}")
    slug = slugify(name)
    for e in all_entries(camp):
        if e["slug"] == slug and not overwrite:
            raise RoboDMError(f"Lore entry '{slug}' already exists ({e['path']}). Use `lore update` or --overwrite.")
    meta = {"name": name, "type": ltype}
    if summary:
        meta["summary"] = summary
    if tags:
        meta["tags"] = parse_csv_list(tags)
    if status:
        meta["status"] = status
    if related:
        meta["related"] = [slugify(r) for r in parse_csv_list(related)]
    if aliases:
        meta["aliases"] = parse_csv_list(aliases)
    meta["canon"] = canon
    meta["created_session"] = camp.session.get("number", 0)
    meta["updated"] = now_iso()
    path = lore_dir(camp) / ltype / f"{slug}.md"
    write_text(path, dump_frontmatter(meta, text or summary))
    return path


def update(camp: Campaign, name: str, append: str = "", fields: dict | None = None, body: str | None = None) -> Path:
    e = find(camp, name)
    meta = dict(e["meta"])
    for k, v in (fields or {}).items():
        if v is None:
            continue
        if k in ("tags", "related", "aliases"):
            meta[k] = parse_csv_list(v) if isinstance(v, str) else v
        elif k == "canon":
            meta[k] = str(v).lower() in ("1", "true", "yes")
        else:
            meta[k] = v
    meta["updated"] = now_iso()
    text = body if body is not None else e["body"]
    if append:
        text = text.rstrip() + f"\n\n**Update (session {camp.session.get('number', 0)}):** {append.strip()}"
    new_type = meta.get("type", "other")
    path = lore_dir(camp) / new_type / f"{e['slug']}.md"
    write_text(path, dump_frontmatter(meta, text))
    if path != e["path"]:
        e["path"].unlink()
    return path


def search(camp: Campaign, query: str, limit: int = 10) -> list[dict]:
    docs = []
    for e in all_entries(camp):
        m = e["meta"]
        docs.append({"slug": e["slug"], "type": m.get("type"), "name": m.get("name"),
                     "summary": m.get("summary", ""),
                     "text": f"{m.get('name', '')} {m.get('name', '')} {' '.join(map(str, m.get('aliases', []) or []))} "
                             f"{m.get('summary', '')} {' '.join(map(str, m.get('tags', []) or []))} {e['body']}"})
    return rank_documents(query, docs, limit=limit)


def build_index(camp: Campaign) -> Path:
    entries = all_entries(camp)
    lines = [f"# {camp.name} — World Bible Index", "",
             f"_{len(entries)} entries · generated {now_iso()[:16]} · edit the entries, not this file_", ""]
    by_type: dict[str, list[dict]] = {}
    for e in entries:
        by_type.setdefault(e["meta"].get("type", "other"), []).append(e)
    for t in LORE_TYPES:
        if t not in by_type:
            continue
        lines.append(f"## {t.capitalize()}")
        for e in sorted(by_type[t], key=lambda x: str(x["meta"].get("name", x["slug"]))):
            m = e["meta"]
            flag = "" if m.get("canon", True) is not False else " _(draft)_"
            status = f" [{m['status']}]" if m.get("status") else ""
            summary = m.get("summary") or truncate(e["body"], 120)
            rel = e["path"].relative_to(lore_dir(camp)).as_posix()
            lines.append(f"- [{m.get('name', e['slug'])}]({rel}){status}{flag} — {summary}")
        lines.append("")
    sources = list_sources(camp)
    if sources:
        lines.append("## Reference sources")
        lines.extend(f"- _sources/{p.name}" for p in sources)
    path = lore_dir(camp) / "INDEX.md"
    write_text(path, "\n".join(lines) + "\n")
    return path


def sources_dir(camp: Campaign) -> Path:
    return lore_dir(camp) / "_sources"


def list_sources(camp: Campaign) -> list[Path]:
    d = sources_dir(camp)
    return sorted(p for p in d.iterdir() if p.is_file()) if d.exists() else []


def add_source(camp: Campaign, file: str | None = None, text: str | None = None, title: str = "") -> Path:
    d = sources_dir(camp)
    d.mkdir(parents=True, exist_ok=True)
    if file:
        src = Path(file).expanduser()
        if not src.exists():
            raise RoboDMError(f"File not found: {file}")
        dest = d / (slugify(title) + src.suffix if title else src.name)
        shutil.copyfile(src, dest)
        return dest
    if text:
        dest = d / f"{slugify(title or 'reference')}.md"
        write_text(dest, (f"# {title}\n\n" if title else "") + text)
        return dest
    raise RoboDMError("Give a file path or --text.")


def fmt_entry(e: dict, full: bool = False) -> str:
    m = e["meta"]
    head = f"**{m.get('name', e['slug'])}** ({m.get('type', '?')}){' [' + m['status'] + ']' if m.get('status') else ''}"
    if m.get("canon") is False:
        head += " _(draft)_"
    if not full:
        return f"{head} — {m.get('summary') or truncate(e['body'], 140)}"
    lines = [head]
    if m.get("summary"):
        lines.append(f"_{m['summary']}_")
    if m.get("aliases"):
        lines.append(f"Also known as: {', '.join(map(str, m['aliases']))}")
    if m.get("tags"):
        lines.append(f"Tags: {', '.join(map(str, m['tags']))}")
    if m.get("related"):
        lines.append(f"Related: {', '.join(map(str, m['related']))}")
    mind = [f"{k[5:]}: {m[k]}" for k in ("mind_feeling", "mind_goal", "mind_opinion", "mind_plan") if m.get(k)]
    if mind:
        lines.append(f"Mind (DM only): {' · '.join(mind)}")
    lines.append("")
    lines.append(e["body"])
    return "\n".join(lines)
