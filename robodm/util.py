"""Shared helpers: JSON/frontmatter IO, slugs, ability math, text search."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ABILITIES = ["str", "dex", "con", "int", "wis", "cha"]
ABILITY_NAMES = {
    "str": "Strength",
    "dex": "Dexterity",
    "con": "Constitution",
    "int": "Intelligence",
    "wis": "Wisdom",
    "cha": "Charisma",
}

SKILLS = {
    "acrobatics": "dex",
    "animal handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight of hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}


class RoboDMError(Exception):
    """An error with a message meant for the user (printed without a traceback)."""


# --------------------------------------------------------------------------- abilities

def ability_mod(score: int) -> int:
    return math.floor((int(score) - 10) / 2)


def fmt_mod(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def proficiency_bonus(level: int) -> int:
    return (max(1, int(level)) - 1) // 4 + 2


def normalize_ability(text: str) -> str:
    t = text.strip().lower()[:3]
    if t not in ABILITIES:
        raise RoboDMError(f"Unknown ability '{text}'. Use one of: {', '.join(ABILITIES)}")
    return t


def normalize_skill(text: str) -> str | None:
    t = re.sub(r"[_-]+", " ", text.strip().lower())
    if t in SKILLS:
        return t
    for name in SKILLS:
        if name.replace(" ", "") == t.replace(" ", ""):
            return name
    return None


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# --------------------------------------------------------------------------- text

def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "untitled"


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def norm_name(text: str) -> str:
    """Normalise a name for matching: lowercase, drop punctuation, collapse spaces."""
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_csv_list(text: str | None) -> list[str]:
    if not text:
        return []
    return [p.strip() for p in str(text).split(",") if p.strip()]


STOPWORDS = set(
    "a an and are as at be by for from has he her his in is it its of on or she that the their them"
    " they this to was were with who what when where which will you your we our i me my".split()
)


def tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOPWORDS and len(w) > 1]


def rank_documents(query: str, docs: list[dict], text_key: str = "text", limit: int = 10) -> list[dict]:
    """Small BM25-style ranker. Each doc is a dict with a text field; returns top docs with 'score'."""
    q_terms = tokenize(query)
    if not q_terms:
        return []
    tokenized = [tokenize(d.get(text_key, "")) for d in docs]
    n = len(docs) or 1
    avg_len = (sum(len(t) for t in tokenized) / n) or 1
    df: dict[str, int] = {}
    for toks in tokenized:
        for term in set(toks):
            df[term] = df.get(term, 0) + 1
    phrase = query.lower().strip()
    results = []
    for doc, toks in zip(docs, tokenized):
        if not toks:
            continue
        score = 0.0
        for term in q_terms:
            tf = toks.count(term)
            if not tf:
                # prefix match (e.g. "goblin" vs "goblins")
                tf = sum(1 for t in toks if t.startswith(term) or term.startswith(t) and len(t) > 3) * 0.5
            if not tf:
                continue
            idf = math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * len(toks) / avg_len))
        if score and len(phrase) > 3 and phrase in doc.get(text_key, "").lower():
            score *= 1.5
        if score:
            results.append({**doc, "score": round(score, 3)})
    results.sort(key=lambda d: d["score"], reverse=True)
    return results[:limit]


def truncate(text: str, width: int = 160) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


# --------------------------------------------------------------------------- json io

def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_text(path: Path, text: str) -> None:
    """Atomic write so an interrupted session never leaves a half-written save."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# --------------------------------------------------------------------------- frontmatter
# A tiny YAML subset: `key: value`, inline lists `[a, b]`, block lists (`- item`),
# booleans, ints. Enough for lore entries and context blocks without PyYAML.

def _parse_scalar(value: str) -> Any:
    v = value.strip()
    if not v:
        return ""
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(p) for p in _split_inline_list(inner)]
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", "~"):
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _split_inline_list(inner: str) -> list[str]:
    parts, buf, quote = [], "", None
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf += ch
        elif ch == ",":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf)
    return [p.strip() for p in parts]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: dict[str, Any] = {}
    current_list_key = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        m_item = re.match(r"^\s+-\s+(.*)$", raw) or (re.match(r"^-\s+(.*)$", raw) if current_list_key else None)
        if m_item and current_list_key:
            meta.setdefault(current_list_key, [])
            if not isinstance(meta[current_list_key], list):
                meta[current_list_key] = []
            meta[current_list_key].append(_parse_scalar(m_item.group(1)))
            continue
        m = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", raw)
        if m:
            key, value = m.group(1), m.group(2)
            if value.strip() == "":
                meta[key] = []
                current_list_key = key
            else:
                meta[key] = _parse_scalar(value)
                current_list_key = None
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def _dump_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_dump_scalar(x) for x in v) + "]"
    s = str(v)
    if s == "" or re.search(r"[:#\[\],]|^\s|\s$|^[-'\"]", s) or s.lower() in ("true", "false", "null", "yes", "no"):
        return '"' + s.replace('"', "'") + '"'
    return s


def dump_frontmatter(meta: dict, body: str) -> str:
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {_dump_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


def iter_files(root: Path, pattern: str = "*.md") -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob(pattern) if p.is_file() and not p.name.startswith("."))
