"""Campaign recall: one search over everything the campaign knows.

Memories, session logs and summaries, play transcripts, the combat log, lore entries, lore reference
sources, character sheets and quests are indexed in SQLite FTS5 (Python's built-in sqlite3), two ways:

  * words, Porter-stemmed and ranked by BM25 with titles weighted 5x: "dragons" finds "dragon";
  * trigrams, for substrings and partial names: "Brann" finds "Brannoc Vale".

The result lists are merged by Reciprocal Rank Fusion. A whole-phrase match adds a bonus list, and a word
with no hits is corrected against the index's own vocabulary ("Brannok" → "brannoc"). Several phrasings of
one question are fused the same way, which is how the DM searches by meaning: give 2–4 wordings (names,
synonyms, what it was about) and the entries that match several of them rise to the top.

The index is a cache (campaigns/<slug>/.cache/recall.db, gitignored). It is rebuilt whenever a source file
changes. If this Python's SQLite lacks FTS5, the plain BM25 ranker in util.rank_documents is used instead.

(The approach was inspired by the context-mode project's knowledge base; this is an independent stdlib
implementation.)
"""

from __future__ import annotations

import difflib
import hashlib
import re
import sqlite3
from pathlib import Path

from robodm.campaign import Campaign
from robodm.util import rank_documents, read_json, read_jsonl, truncate

RRF_K = 60
SCOPES = {
    "all": None,
    "memory": {"memory"},
    "journal": {"journal", "summary", "transcript", "combat"},
    "transcript": {"transcript"},
    "lore": {"lore", "source"},
    "party": {"pc"},
    "quests": {"quest"},
    "companions": {"companion"},
}
_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)
_SCHEMA_VERSION = "3"


def _fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        con.execute("create virtual table t using fts5(x, tokenize='trigram')")
        con.close()
        return True
    except sqlite3.Error:
        return False


FTS5 = _fts5_available()


# =============================================================================== documents

def source_files(camp: Campaign) -> list[Path]:
    """Every file the index is built from (its signature decides when to rebuild)."""
    out = [camp.path("memory.jsonl"), camp.path("quests.json")]
    for sub, pattern in (("party", "*.json"), ("journal", "*.md"), ("lore", "**/*"), ("companions", "**/*")):
        d = camp.path(sub)
        if d.exists():
            out.extend(p for p in d.glob(pattern) if p.is_file())
    return sorted(p for p in out if p.exists())


def _signature(camp: Campaign) -> str:
    h = hashlib.sha1(_SCHEMA_VERSION.encode())
    for p in source_files(camp):
        st = p.stat()
        h.update(f"{p.relative_to(camp.dir).as_posix()}|{st.st_mtime_ns}|{st.st_size}\n".encode())
    return h.hexdigest()


def documents(camp: Campaign) -> list[dict]:
    """Searchable chunks: kind, ref, title, body, show (one display line), boost."""
    from robodm import lore
    from robodm.memory import fmt_memory, fmt_quest

    docs: list[dict] = []

    def add(kind, ref, title, body, show, boost=1.0):
        if (title or body).strip():
            docs.append({"kind": kind, "ref": ref, "title": title, "body": body, "show": show, "boost": boost})

    for m in read_jsonl(camp.path("memory.jsonl")):
        if m.get("retired"):
            continue
        add("memory", m["id"], f"{m.get('type', '')} {' '.join(m.get('tags', []))}", m["text"], fmt_memory(m),
            1.25 if m.get("important") else 1.0)
    jdir = camp.path("journal")
    if jdir.exists():
        for p in sorted(jdir.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            if p.name.startswith("transcript-"):
                _transcript_docs(p, text, add)
            elif p.name == "combat-log.md":
                fight = ""
                for line in text.splitlines():
                    if line.startswith("## "):
                        fight = line[3:].strip()
                    elif line.startswith("- ") or line.startswith("**Result:**"):
                        add("combat", p.stem, fight, line.lstrip("- "), f"combat log ({fight}): {truncate(line.lstrip('- '), 200)}")
            else:
                log, _, summary = text.partition("\n## Summary")
                for line in log.splitlines():
                    line = line.strip()
                    if len(line) > 12 and not line.startswith("#"):
                        add("journal", p.stem, p.stem, line, f"{p.stem}: {truncate(line, 220)}")
                if summary.strip():
                    add("summary", p.stem, f"{p.stem} summary", summary.strip(),
                        f"{p.stem} summary: {truncate(summary.strip(), 220)}", 1.15)
    for e in lore.all_entries(camp):
        meta = e["meta"]
        aliases = " ".join(map(str, meta.get("aliases", []) or [])) if isinstance(meta.get("aliases"), list) else str(meta.get("aliases") or "")
        add("lore", e["slug"], f"{meta.get('name', '')} {aliases}",
            f"{meta.get('summary', '')} {' '.join(map(str, meta.get('tags', []) or []))} {e['body']}",
            f"lore/{meta.get('type', '?')}/{e['slug']}: {meta.get('name')} — {truncate(meta.get('summary') or e['body'], 180)}",
            1.1)
    for p in lore.list_sources(camp):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for heading, chunk in _chunks(text):
            add("source", p.name, f"{p.stem} {heading}", chunk, f"source {p.name} § {heading or 'start'}: {truncate(chunk, 160)}")
    for pc in camp.party():
        add("pc", pc["slug"], pc["name"],
            f"{pc.get('race', '')} {pc.get('class', '')} {pc.get('notes', '')} {' '.join(pc.get('inventory', []))} "
            f"{' '.join(pc.get('features', []))}",
            f"PC {pc['name']}: {truncate(pc.get('notes', '') or pc.get('class', ''), 160)}")
    cdir = camp.path("companions")
    if cdir.exists():
        for p in sorted(cdir.glob("*/persona.md")):
            text = p.read_text(encoding="utf-8")
            add("companion", p.parent.name, p.parent.name.replace("-", " "), text,
                f"companion {p.parent.name}: {truncate(' '.join(text.split()), 200)}", 1.05)
        for p in sorted(cdir.glob("*/memory.jsonl")):
            for m in read_jsonl(p):
                add("companion", p.parent.name, f"{p.parent.name} remembers", m.get("text", ""),
                    f"{p.parent.name} remembers (s{m.get('session', 0)}): {truncate(m.get('text', ''), 200)}")
    for q in read_json(camp.path("quests.json")) or []:
        add("quest", q["id"], q["title"], f"{q.get('notes', '')} {q.get('giver', '')}", f"quest {fmt_quest(q)}")
    return docs


_TRANSCRIPT_HEAD = re.compile(r"^### \[([^\]]+)\] (Player|DM)\s*$", re.MULTILINE)


def _transcript_docs(p: Path, text: str, add) -> None:
    heads = list(_TRANSCRIPT_HEAD.finditer(text))
    for i, h in enumerate(heads):
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)].strip()
        add("transcript", p.stem, f"{p.stem} {h.group(2)}", body,
            f"{p.stem} [{h.group(1)}] {h.group(2)}: {truncate(' '.join(body.split()), 200)}", 0.9)


def _chunks(text: str, size: int = 1500) -> list[tuple[str, str]]:
    """Split a reference document by Markdown headings, then by paragraphs into ~size-character chunks."""
    out: list[tuple[str, str]] = []
    heading, buf = "", []

    def flush():
        body = "\n".join(buf).strip()
        while body:
            cut = body.rfind("\n\n", 0, size) if len(body) > size else len(body)
            cut = cut if cut > size // 3 else min(len(body), size)
            out.append((heading, body[:cut].strip()))
            body = body[cut:].strip()

    for line in text.splitlines():
        if re.match(r"^#{1,4} ", line):
            flush()
            heading, buf = line.lstrip("#").strip(), []
        else:
            buf.append(line)
    flush()
    return out


# =============================================================================== index

def _db_path(camp: Campaign) -> Path:
    return camp.path(".cache", "recall.db")


def _connect(camp: Campaign) -> sqlite3.Connection:
    p = _db_path(camp)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10)
    con.executescript("""
        create table if not exists meta(k text primary key, v text);
        create table if not exists docs(id integer primary key, kind text, ref text, title text, show text, boost real);
        create virtual table if not exists fts_words using fts5(title, body, tokenize='porter unicode61 remove_diacritics 2');
        create virtual table if not exists fts_tri using fts5(title, body, tokenize='trigram');
        create table if not exists words(w text primary key);   -- unstemmed vocabulary, for typo correction
    """)
    return con


def ensure_index(camp: Campaign) -> sqlite3.Connection:
    con = _connect(camp)
    sig = _signature(camp)
    row = con.execute("select v from meta where k='sig'").fetchone()
    if not row or row[0] != sig:
        docs = documents(camp)
        with con:
            for t in ("docs", "fts_words", "fts_tri", "words"):
                con.execute(f"delete from {t}")
            for i, d in enumerate(docs, 1):
                con.execute("insert into docs values (?,?,?,?,?,?)", (i, d["kind"], d["ref"], d["title"], d["show"], d["boost"]))
                con.execute("insert into fts_words(rowid, title, body) values (?,?,?)", (i, d["title"], d["body"]))
                con.execute("insert into fts_tri(rowid, title, body) values (?,?,?)", (i, d["title"], d["body"]))
            vocab = {w.lower() for d in docs for w in _WORD.findall(f"{d['title']} {d['body']}") if len(w) >= 4}
            con.executemany("insert into words values (?)", ((w,) for w in sorted(vocab)))
            con.execute("insert or replace into meta values ('sig', ?)", (sig,))
    return con


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _ranked(con: sqlite3.Connection, table: str, expr: str, limit: int = 60) -> list[int]:
    if not expr:
        return []
    try:
        return [r[0] for r in con.execute(
            f"select rowid from {table} where {table} match ? order by bm25({table}, 5.0, 1.0) limit ?", (expr, limit))]
    except sqlite3.Error:
        return []


def _corrections(con: sqlite3.Connection, terms: list[str]) -> list[str]:
    """Close vocabulary words for query words that match nothing (typo tolerance)."""
    fixed = []
    vocab = None
    for t in terms:
        if len(t) < 4 or _ranked(con, "fts_words", _quote(t), 1):
            continue
        if vocab is None:
            vocab = [r[0] for r in con.execute("select w from words")]
        close = difflib.get_close_matches(t, vocab, n=8, cutoff=0.75)
        # typos rarely change a word's first letter: "Isene" means Isine, not "sense" or "scene"
        same_start = [w for w in close if w[:1] == t[:1]]
        fixed.extend((same_start or close)[:2])
    return fixed


def _fts_lists(con: sqlite3.Connection, query: str) -> list[list[int]]:
    terms = [t.lower() for t in _WORD.findall(query)]
    if not terms:
        return []
    lists = [_ranked(con, "fts_words", " OR ".join(_quote(t) for t in terms)),
             _ranked(con, "fts_tri", " OR ".join(_quote(t) for t in terms if len(t) >= 3))]
    if len(terms) > 1:
        lists.append(_ranked(con, "fts_words", _quote(" ".join(terms))))     # whole phrase
    fixed = _corrections(con, terms)
    if fixed:
        lists.append(_ranked(con, "fts_words", " OR ".join(_quote(t) for t in fixed)))
    return [lst for lst in lists if lst]


def search(camp: Campaign, queries: list[str] | str, limit: int = 12, scope: str = "all") -> list[dict]:
    """Rank the campaign's knowledge for one or more phrasings. Each hit: kind, ref, show, snippet, score."""
    if isinstance(queries, str):
        queries = [queries]
    queries = [q for q in queries if q and q.strip()]
    if not queries:
        return []
    kinds = SCOPES.get(scope or "all")
    if FTS5:
        try:
            return _search_fts(camp, queries, limit, kinds)
        except sqlite3.Error:
            pass  # a locked or damaged cache: fall back to the in-memory ranker
    return _search_plain(camp, queries, limit, kinds)


def _fuse(lists: list[list], boost=lambda key: 1.0) -> list[tuple]:
    scores: dict = {}
    for lst in lists:
        for rank, key in enumerate(lst, 1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(((s * boost(k), k) for k, s in scores.items()), key=lambda x: -x[0])


def _search_fts(camp: Campaign, queries: list[str], limit: int, kinds: set | None) -> list[dict]:
    con = ensure_index(camp)
    try:
        meta = {r[0]: r[1:] for r in con.execute("select id, kind, ref, show, boost from docs")}
        lists = []
        for q in queries:
            for lst in _fts_lists(con, q):
                lists.append([i for i in lst if i in meta and (kinds is None or meta[i][0] in kinds)])
        fused = _fuse(lists, boost=lambda i: meta[i][3] or 1.0)[:limit]
        words = " OR ".join(_quote(t.lower()) for q in queries for t in _WORD.findall(q))
        hits = []
        for score, i in fused:
            kind, ref, show, _ = meta[i]
            snip = ""
            if words:
                row = con.execute("select snippet(fts_words, 1, '**', '**', '…', 14) from fts_words "
                                  "where fts_words match ? and rowid = ?", (words, i)).fetchone()
                snip = row[0] if row else ""
            hits.append({"kind": kind, "ref": ref, "show": show, "snippet": " ".join(snip.split()), "score": round(score * 1000, 2)})
        return hits
    finally:
        con.close()


def _search_plain(camp: Campaign, queries: list[str], limit: int, kinds: set | None) -> list[dict]:
    docs = [dict(d, text=f"{d['title']} {d['title']} {d['body']}") for d in documents(camp)
            if kinds is None or d["kind"] in kinds]
    for i, d in enumerate(docs):
        d["_i"] = i
    lists = [[d["_i"] for d in rank_documents(q, docs, limit=60)] for q in queries]
    fused = _fuse(lists, boost=lambda i: docs[i]["boost"])[:limit]
    return [{"kind": docs[i]["kind"], "ref": docs[i]["ref"], "show": docs[i]["show"], "snippet": "",
             "score": round(s * 1000, 2)} for s, i in fused]


def stats(camp: Campaign) -> str:
    if not FTS5:
        return "Recall: plain BM25 (this Python's SQLite has no FTS5)."
    con = ensure_index(camp)
    try:
        counts = dict(con.execute("select kind, count(*) from docs group by kind").fetchall())
    finally:
        con.close()
    return "Recall index: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + " (FTS5: stemmed words + trigrams)"
