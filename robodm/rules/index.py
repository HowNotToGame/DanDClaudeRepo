"""Local index over the 5etools data so lookups don't parse every file each time.

The index (`_index.json` inside the data folder) records, for every entry,
its type, name, source, which file holds it and where. It is rebuilt
automatically whenever the data is re-fetched.
"""

from __future__ import annotations

import copy
import difflib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from robodm import paths
from robodm.util import RoboDMError, norm_name, read_json, write_json

INDEX_FILE = "_index.json"
INDEX_VERSION = 5

# type -> list of (file, key). Files with "{folder}/index.json" are expanded.
SIMPLE_TYPES: dict[str, list[tuple[str, str]]] = {
    "item": [("items.json", "item"), ("items-base.json", "baseitem"), ("magicvariants.json", "magicvariant")],
    "itemgroup": [("items.json", "itemGroup")],
    "condition": [("conditionsdiseases.json", "condition"), ("conditionsdiseases.json", "status")],
    "disease": [("conditionsdiseases.json", "disease")],
    "action": [("actions.json", "action")],
    "rule": [("variantrules.json", "variantrule")],
    "feat": [("feats.json", "feat")],
    "race": [("races.json", "race")],
    "background": [("backgrounds.json", "background")],
    "optionalfeature": [("optionalfeatures.json", "optionalfeature")],
    "skill": [("skills.json", "skill")],
    "sense": [("senses.json", "sense")],
    "language": [("languages.json", "language")],
    "deity": [("deities.json", "deity")],
    "trap": [("trapshazards.json", "trap")],
    "hazard": [("trapshazards.json", "hazard")],
    "legendarygroup": [("bestiary/legendarygroups.json", "legendaryGroup")],
    "itemproperty": [("items-base.json", "itemProperty")],
}

TYPE_ALIASES = {
    "spells": "spell", "monsters": "monster", "creature": "monster", "creatures": "monster", "bestiary": "monster",
    "items": "item", "conditions": "condition", "status": "condition", "actions": "action", "rules": "rule",
    "variantrule": "rule", "feats": "feat", "races": "race", "species": "race", "backgrounds": "background",
    "classes": "class", "subclasses": "subclass", "feature": "classfeature", "features": "classfeature",
    "class-feature": "classfeature", "subclass-feature": "subclassfeature", "optfeature": "optionalfeature",
    "invocation": "optionalfeature", "maneuver": "optionalfeature", "fightingstyle": "optionalfeature",
    "skills": "skill", "senses": "sense", "languages": "language", "deities": "deity", "god": "deity",
    "traps": "trap", "hazards": "hazard", "books": "book", "quick": "quickref", "reference": "quickref",
    "lair": "legendarygroup", "diseases": "disease", "property": "itemproperty",
}

ALL_TYPES = [
    "condition", "spell", "monster", "item", "classfeature", "subclassfeature", "optionalfeature", "feat",
    "action", "quickref", "rule", "class", "subclass", "race", "subrace", "background", "skill", "sense",
    "language", "disease", "trap", "hazard", "deity", "legendarygroup", "itemgroup", "itemproperty", "book",
]

# Prefer core books when the same name exists in several sources.
SOURCE_PRIORITY = ["PHB", "MM", "DMG", "XGE", "TCE", "VGM", "MTF", "SCAG", "FTD", "MPMM", "BMT", "EGW", "ERLW"]

SCHOOLS = {
    "A": "Abjuration", "C": "Conjuration", "D": "Divination", "E": "Enchantment", "V": "Evocation",
    "I": "Illusion", "N": "Necromancy", "T": "Transmutation", "P": "Psionic",
}


def normalize_type(t: str | None) -> str | None:
    if not t:
        return None
    t = t.strip().lower().replace(" ", "")
    t = TYPE_ALIASES.get(t, t)
    if t not in ALL_TYPES:
        raise RoboDMError(f"Unknown rules type '{t}'. Types: {', '.join(ALL_TYPES)}")
    return t


def cr_to_float(cr: Any) -> float | None:
    if isinstance(cr, dict):
        cr = cr.get("cr")
    if cr is None or cr == "Unknown" or cr == "—":
        return None
    s = str(cr)
    if "/" in s:
        a, b = s.split("/", 1)
        return int(a) / int(b)
    try:
        return float(s)
    except ValueError:
        return None


def cr_text(cr: Any) -> str:
    if isinstance(cr, dict):
        return str(cr.get("cr", "?"))
    return str(cr) if cr is not None else "?"


def monster_type(m: dict) -> str:
    t = m.get("type")
    if isinstance(t, dict):
        base = t.get("type")
        if isinstance(base, dict):  # {"choose": [...]}
            base = "/".join(base.get("choose", []))
        return str(base)
    return str(t or "")


class RulesDB:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else paths.data_dir()
        self._files: dict[str, Any] = {}
        self._index: list[dict] | None = None

    # ------------------------------------------------------------------ files
    @property
    def available(self) -> bool:
        return (self.data_dir / "spells" / "index.json").exists() or (self.data_dir / "_meta.json").exists()

    def require(self) -> None:
        if not self.available:
            raise RoboDMError(
                f"No local 5etools data found in {self.data_dir}.\n"
                "Run `./dm rules fetch` once (≈45 MB download) and try again."
            )

    def load_file(self, rel: str) -> Any:
        if rel not in self._files:
            path = self.data_dir / rel
            if not path.exists():
                self._files[rel] = None
            else:
                with open(path, encoding="utf-8") as fh:
                    self._files[rel] = json.load(fh)
        return self._files[rel]

    # ------------------------------------------------------------------ index
    @property
    def index(self) -> list[dict]:
        if self._index is None:
            self._index = self._load_or_build_index()
        return self._index

    def _load_or_build_index(self) -> list[dict]:
        self.require()
        cached = read_json(self.data_dir / INDEX_FILE)
        # Keyed on the fetch metadata (not file mtimes) so a copied or unzipped data folder keeps its index.
        meta = read_json(self.data_dir / "_meta.json") or {}
        stamp = f"{meta.get('fetched_at', '')}|{meta.get('files', '')}|{meta.get('bytes', '')}"
        if cached and cached.get("version") == INDEX_VERSION and cached.get("stamp") == stamp:
            return cached["entries"]
        entries = self.build_index()
        try:
            write_json(self.data_dir / INDEX_FILE, {"version": INDEX_VERSION, "stamp": stamp, "entries": entries})
        except OSError:
            pass  # read-only data dir: just keep it in memory
        return entries

    def build_index(self) -> list[dict]:
        out: list[dict] = []
        spell_classes = self._spell_class_lookup()

        def add(t, e, rel, key, i, **extra):
            if not isinstance(e, dict) or not e.get("name"):
                return
            rec = {"t": t, "n": e["name"], "s": e.get("source", ""), "f": rel, "k": key, "i": i}
            if e.get("srd"):
                rec["srd"] = 1
            rec.update({k: v for k, v in extra.items() if v not in (None, "", [])})
            out.append(rec)

        for folder, key, t in (("spells", "spell", "spell"), ("bestiary", "monster", "monster")):
            idx = self.load_file(f"{folder}/index.json") or {}
            for fname in idx.values():
                rel = f"{folder}/{fname}"
                data = self.load_file(rel)
                if not data:
                    continue
                for i, e in enumerate(data.get(key, [])):
                    if t == "spell":
                        classes = spell_classes.get((norm_name(e.get("name", "")), e.get("source", "").upper()), [])
                        add(t, e, rel, key, i, lvl=e.get("level"), sch=e.get("school"), cls=classes,
                            conc=1 if any(d.get("concentration") for d in e.get("duration", []) if isinstance(d, dict)) else None,
                            rit=1 if (e.get("meta") or {}).get("ritual") else None)
                    else:
                        add(t, e, rel, key, i, cr=cr_text(e.get("cr")) if "cr" in e else None,
                            mt=monster_type(e) if "type" in e else None, env=e.get("environment"),
                            cp=1 if "_copy" in e else None)
                self._files.pop(rel, None)

        cls_idx = self.load_file("class/index.json") or {}
        for fname in cls_idx.values():
            rel = f"class/{fname}"
            data = self.load_file(rel) or {}
            for key, t in (("class", "class"), ("subclass", "subclass"), ("classFeature", "classfeature"),
                           ("subclassFeature", "subclassfeature")):
                for i, e in enumerate(data.get(key, [])):
                    extra = {}
                    if t in ("classfeature", "subclassfeature"):
                        extra = {"cl": e.get("className"), "lv": e.get("level"), "sc": e.get("subclassShortName")}
                    elif t == "subclass":
                        extra = {"cl": e.get("className")}
                    add(t, e, rel, key, i, **extra)
            self._files.pop(rel, None)

        for t, specs in SIMPLE_TYPES.items():
            for rel, key in specs:
                data = self.load_file(rel) or {}
                for i, e in enumerate(data.get(key, [])):
                    extra = {}
                    if t == "item":
                        extra = {"rar": e.get("rarity"), "it": (e.get("type") or "").split("|")[0] or None}
                    add(t, e, rel, key, i, **extra)

        # Subraces are stored as e.g. name "Hill", raceName "Dwarf": index them as "Dwarf (Hill)" / "Hill Dwarf".
        races = self.load_file("races.json") or {}
        for i, e in enumerate(races.get("subrace", [])):
            if isinstance(e, dict) and e.get("raceName"):
                sub = e.get("name")
                if not sub:
                    continue
                rec = {"t": "subrace", "n": f"{e['raceName']} ({sub})", "s": e.get("source", ""), "f": "races.json",
                       "k": "subrace", "i": i, "al": [f"{sub} {e['raceName']}"], "race": e["raceName"]}
                if e.get("srd"):
                    rec["srd"] = 1
                out.append(rec)

        qr = self.load_file("generated/bookref-quick.json")
        if qr:
            chapters = (qr.get("data") or {}).get("bookref-quick", [])
            for ci, ch in enumerate(chapters):
                for si, sec in enumerate(ch.get("entries", [])):
                    if isinstance(sec, dict) and sec.get("name"):
                        out.append({"t": "quickref", "n": sec["name"], "s": "PHB", "f": "generated/bookref-quick.json",
                                    "k": "quickref", "i": [ci, si]})
                        for ei, sub in enumerate(sec.get("entries", [])):
                            if isinstance(sub, dict) and sub.get("name") and sub.get("type") in ("entries", "section", "inset"):
                                out.append({"t": "quickref", "n": sub["name"], "s": "PHB",
                                            "f": "generated/bookref-quick.json", "k": "quickref", "i": [ci, si, ei],
                                            "parent": sec["name"]})

        book_dir = self.data_dir / "book"
        if book_dir.exists():
            for path in sorted(book_dir.glob("book-*.json")):
                rel = f"book/{path.name}"
                data = self.load_file(rel) or {}
                src = path.stem.replace("book-", "").upper()
                self._index_book(out, _book_root(data), rel, src, [], 0)
                self._files.pop(rel, None)
        return out

    def _index_book(self, out, entries, rel, src, path, depth):
        if depth > 3 or not isinstance(entries, list):
            return
        for i, e in enumerate(entries):
            if isinstance(e, dict) and e.get("name") and e.get("type") in ("section", "entries", "inset", "chapter"):
                p = path + [i]
                out.append({"t": "book", "n": e["name"], "s": src, "f": rel, "k": "data", "i": p})
                self._index_book(out, e.get("entries", []), rel, src, p + ["entries"], depth + 1)

    def _spell_class_lookup(self) -> dict:
        lookup = {}
        src = self.load_file("spells/sources.json") or {}
        for source, spells in src.items():
            for name, info in spells.items():
                classes = sorted({c.get("name") for c in info.get("class", []) if isinstance(c, dict)})
                lookup[(norm_name(name), source.upper())] = classes
        return lookup

    # ------------------------------------------------------------------ fetch entries
    def get(self, rec: dict, resolve: bool = True) -> dict:
        data = self.load_file(rec["f"])
        if data is None:
            raise RoboDMError(f"Data file missing: {rec['f']} (re-run `./dm rules fetch`).")
        if rec["k"] == "quickref":
            ci, si, *rest = rec["i"]
            node = data["data"]["bookref-quick"][ci]["entries"][si]
            if rest:
                node = node["entries"][rest[0]]
            return node
        if rec["t"] == "book":
            node: Any = _book_root(data)
            for step in rec["i"]:
                node = node[step]
            return node
        entry = data[rec["k"]][rec["i"]]
        if resolve and isinstance(entry, dict) and "_copy" in entry:
            entry = self.resolve_copy(entry, rec["t"], rec["k"])
        return entry

    def resolve_copy(self, entry: dict, t: str, key: str, depth: int = 0) -> dict:
        info = entry["_copy"]
        base_rec = self.find_exact(info.get("name", ""), t, info.get("source"))
        if not base_rec or depth > 5:
            out = dict(entry)
            out.setdefault("_notes", []).append(f"copy of {info.get('name')} could not be resolved")
            return out
        base = copy.deepcopy(self.get(base_rec, resolve=False))
        if "_copy" in base:
            base = self.resolve_copy(base, t, key, depth + 1)
        notes = list(base.get("_notes", []))
        for prop, mods in (info.get("_mod") or {}).items():
            for mod in mods if isinstance(mods, list) else [mods]:
                if isinstance(mod, str):
                    mod = {"mode": mod}
                if not _apply_mod(base, prop, mod):
                    notes.append(f"unapplied modification '{mod.get('mode')}' on {prop}")
        if info.get("_templates"):
            names = ", ".join(tp.get("name", "?") for tp in info["_templates"])
            notes.append(f"template(s) not applied: {names}")
        for k, v in entry.items():
            if k != "_copy":
                base[k] = v
        for k in ("srd", "basicRules", "otherSources", "referenceSources", "reprintedAs"):
            if k not in entry:
                base.pop(k, None)
        if notes:
            base["_notes"] = notes
        return base

    # ------------------------------------------------------------------ search
    def _filtered(self, t: str | None) -> list[dict]:
        if t is None:
            return self.index
        return [r for r in self.index if r["t"] == t]

    def find_exact(self, name: str, t: str | None = None, source: str | None = None) -> dict | None:
        target = norm_name(name)
        cands = [r for r in self._filtered(t)
                 if norm_name(r["n"]) == target or any(norm_name(a) == target for a in r.get("al", ()))]
        if source:
            exact_src = [r for r in cands if r["s"].upper() == source.upper()]
            if exact_src:
                return exact_src[0]
        return self._best(cands)

    def _best(self, cands: list[dict]) -> dict | None:
        if not cands:
            return None

        def key(r):
            src = r["s"].upper()
            prio = SOURCE_PRIORITY.index(src) if src in SOURCE_PRIORITY else len(SOURCE_PRIORITY)
            tprio = ALL_TYPES.index(r["t"]) if r["t"] in ALL_TYPES else 99
            return (tprio, 0 if r.get("srd") else 1, prio)

        return sorted(cands, key=key)[0]

    def search(self, query: str, t: str | None = None, limit: int = 15, source: str | None = None) -> list[dict]:
        q = norm_name(query)
        if not q:
            return []
        words = q.split()
        scored = []
        for r in self._filtered(t):
            if source and r["s"].upper() != source.upper():
                continue
            n = norm_name(r["n"])
            for alias in r.get("al", ()):
                if q in norm_name(alias):
                    n = norm_name(alias)
            if n == q:
                score = 100
            elif n.startswith(q):
                score = 85 - min(len(n) - len(q), 20) * 0.5
            elif all(w in n.split() for w in words):
                score = 70
            elif q in n:
                score = 60
            elif all(any(nw.startswith(w) for nw in n.split()) for w in words):
                score = 55
            else:
                ratio = difflib.SequenceMatcher(None, q, n).ratio()
                if ratio < 0.72:
                    continue
                # 0.72 -> 35 … 1.0 -> 65: close typos (ratio >= ~0.91) clear the lookup threshold of 55
                score = 35 + (ratio - 0.72) / 0.28 * 30
            src = r["s"].upper()
            if src in SOURCE_PRIORITY:
                score += 3 - SOURCE_PRIORITY.index(src) * 0.1
            if r.get("srd"):
                score += 1
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        seen, results = set(), []
        for score, r in scored:
            key = (r["t"], norm_name(r["n"]), r["s"], str(r["i"]))
            if key in seen:
                continue
            seen.add(key)
            results.append({**r, "score": round(score, 1)})
            if len(results) >= limit:
                break
        return results

    def lookup(self, name: str, t: str | None = None, source: str | None = None) -> tuple[dict | None, list[dict]]:
        """Best record for a name plus alternatives (other sources/types or close matches)."""
        exact = self.find_exact(name, t, source)
        if exact:
            target = norm_name(name)
            alts = [r for r in self._filtered(t) if r is not exact and
                    (norm_name(r["n"]) == target or any(norm_name(a) == target for a in r.get("al", ())))]
            return exact, alts[:8]
        results = self.search(name, t, limit=8, source=source)
        if results and results[0]["score"] >= 55:
            return results[0], results[1:]
        return None, results

    def fulltext(self, query: str, t: str | None = None, limit: int = 20) -> list[dict]:
        """Grep rendered entry text (slower; use when the name is unknown)."""
        from robodm.rules.render import render_entries

        pattern = re.compile(re.escape(query), re.IGNORECASE)
        hits = []
        for r in self._filtered(t):
            if r["t"] == "book" and t != "book":
                continue
            try:
                e = self.get(r, resolve=False)
            except Exception:
                continue
            fields = []
            if isinstance(e, dict) and r["t"] in ("book", "quickref"):
                # only the section's own text, so the deepest matching section wins over its parent chapters
                fields = [x for x in e.get("entries", []) if not (isinstance(x, dict) and x.get("name"))]
            elif isinstance(e, dict):
                for k in ("entries", "entriesHigherLevel", "trait", "action", "reaction", "legendary", "bonus", "headerEntries"):
                    if k in e:
                        fields.append(e[k])
            text = render_entries(fields)
            m = pattern.search(text)
            if m:
                start = max(0, m.start() - 70)
                snippet = text[start : m.end() + 90].replace("\n", " ")
                hits.append({**r, "snippet": ("…" if start else "") + snippet + "…"})
                if len(hits) >= limit:
                    break
        return hits


def _book_root(data: dict) -> list:
    """Book files are {"bookData": [{"data": [...]}]} (older mirrors: {"data": [...]})."""
    if isinstance(data.get("bookData"), list) and data["bookData"]:
        return data["bookData"][0].get("data", [])
    return data.get("data", [])


def _apply_mod(obj: dict, prop: str, mod: dict) -> bool:
    mode = mod.get("mode")
    if mode == "replaceTxt":
        flags = re.IGNORECASE if "i" in (mod.get("flags") or "") else 0
        try:
            pattern = re.compile(mod["replace"], flags)
        except re.error:
            return False
        repl = re.sub(r"\$(\d)", r"\\\1", mod.get("with", ""))
        props = ["trait", "action", "bonus", "reaction", "legendary", "mythic", "spellcasting", "variant", "entries"] if prop == "*" else [prop]

        def walk(o):
            if isinstance(o, str):
                return pattern.sub(repl, o)
            if isinstance(o, list):
                return [walk(x) for x in o]
            if isinstance(o, dict):
                return {k: (walk(v) if k in ("entries", "headerEntries", "footerEntries", "items", "entry") else v) for k, v in o.items()}
            return o

        for p in props:
            if p in obj:
                obj[p] = walk(obj[p])
        return True
    items = mod.get("items")
    if items is not None and not isinstance(items, list):
        items = [items]
    arr = obj.get(prop)
    if mode == "appendArr":
        obj[prop] = (arr or []) + items
        return True
    if mode == "prependArr":
        obj[prop] = items + (arr or [])
        return True
    if mode == "insertArr":
        arr = list(arr or [])
        i = mod.get("index", len(arr))
        obj[prop] = arr[:i] + items + arr[i:]
        return True
    if mode == "replaceArr" and isinstance(arr, list):
        target = mod.get("replace")
        for i, x in enumerate(arr):
            nm = x.get("name") if isinstance(x, dict) else x
            if (isinstance(target, dict) and target.get("index") == i) or nm == target:
                obj[prop] = arr[:i] + items + arr[i + 1 :]
                return True
        return False
    if mode == "removeArr" and isinstance(arr, list):
        names = mod.get("names") or mod.get("items") or []
        if not isinstance(names, list):
            names = [names]
        obj[prop] = [x for x in arr if (x.get("name") if isinstance(x, dict) else x) not in names]
        return True
    return False


@lru_cache(maxsize=1)
def default_db() -> RulesDB:
    return RulesDB()
