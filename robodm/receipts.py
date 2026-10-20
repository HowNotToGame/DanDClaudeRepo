"""Receipts: the mechanical results of each rules command (who hit, who missed, damage dealt, saves and checks
passed or failed, who dropped or died), and a deterministic check that the DM's narration agrees with them.

While a rules command runs, the engine notes facts here (`note()`); `cli.main()` stores them on the command's
audit event. At the end of a reply, the Stop hook compares the reply text with this turn's facts (`check()`).
It's deliberately conservative: it only flags sentences that name a creature from the facts and clearly say the
opposite of what the dice said. The DM then sends a one-line correction (or nothing, if it was a false alarm).
"""

from __future__ import annotations

import re

_FACTS: list[dict] | None = None


def start() -> None:
    global _FACTS
    _FACTS = []


def stop() -> list[dict]:
    global _FACTS
    facts, _FACTS = _FACTS or [], None
    return facts


def note(kind: str, **fact) -> None:
    if _FACTS is not None:
        _FACTS.append({"kind": kind, **fact})


# =============================================================================== formatting

def fmt(f: dict) -> str:
    k = f["kind"]
    if k == "attack":
        res = "CRIT" if f.get("crit") else ("hit" if f.get("hit") else "miss")
        return f"{f['by']} → {f['target']}: {f.get('total')} vs AC {f.get('ac')} = {res}"
    if k == "damage":
        raw = f" (rolled {f['raw']})" if f.get("raw") not in (None, f.get("amount")) else ""
        return f"{f['target']} takes {f['amount']}{raw} → HP {f.get('hp')}/{f.get('max')}"
    if k == "heal":
        return f"{f['target']} heals {f['amount']} → HP {f.get('hp')}"
    if k in ("down", "dead"):
        return f"{f['target']} {'drops to 0 HP' if k == 'down' else 'dies'}"
    if k == "save":
        return f"{f['who']} {str(f.get('ability', '')).upper()} save {f.get('total')} vs DC {f.get('dc')} = " \
               f"{'success' if f.get('ok') else 'failure'}"
    if k == "check":
        vs = f"DC {f['dc']}" if f.get("dc") is not None else (f"vs {f['vs']}" if f.get("vs") is not None else "no DC (open)")
        res = {True: "success", False: "failure", None: "tie" if f.get("vs") is not None else "—"}[f.get("ok")]
        return f"{f['who']} {f.get('what')} {f.get('total')} {vs} = {res}"
    if k == "condition":
        return f"{f['target']} is now {f['name']}"
    return str(f)


# =============================================================================== the narration check

_SENT = re.compile(r"(?<=[.!?…])\s+|\n+")
_NEG = re.compile(r"\b(not|no|never|nearly|almost|would|could|might|if|unless|before)\b|n't", re.I)
_DEATH = re.compile(r"\b(dies|died|is dead|lies dead|falls dead|drops dead|slain|is killed|lifeless|perishes|"
                    r"breathes (?:its|his|her|their) last|is no more)\b", re.I)
_HIT = re.compile(r"\b(hits?|strikes? (?:true|home|deep)|struck|connects?|lands? (?:a|the|its|his|her|their)? ?"
                  r"(?:blow|hit|strike)|wounds?|bites? (?:into|deep)|cuts? (?:into|deep)|slices? (?:into|through)|"
                  r"pierces?|draws? blood|bloodies)\b", re.I)
_MISS = re.compile(r"\b(miss(?:es|ed)?|goes wide|wide of|glanc\w*|deflect\w*|parr\w*|dodg\w*|block\w*|"
                   r"fails? to|harmless\w*|skitters? off|clangs? off|whistles? past)\b", re.I)
_DMG_NUM = re.compile(r"\b(\d{1,3})\s+(?:points? of\s+)?(?:[a-z]+\s+)?damage\b", re.I)
_HP_NUM = re.compile(r"\b(?:HP|hit points?)\b\s*:?\s*\d+|\b\d+\s*(?:/\s*\d+\s*)?(?:HP|hit points?)\b", re.I)
_SUCCESS = re.compile(r"\b(success|succeeds?|succeeded|passes|passed)\b", re.I)
_FAIL = re.compile(r"\b(fail(?:s|ed|ure)?)\b", re.I)


def _aliases(names: list[str]) -> dict[str, list[str]]:
    """Full names, plus the bare word for a numbered creature when it's the only one of its kind."""
    out = {n: [n.lower()] for n in names}
    bases: dict[str, list[str]] = {}
    for n in names:
        base = re.sub(r"\s+\d+$", "", n).strip().lower()
        bases.setdefault(base, []).append(n)
    for base, members in bases.items():
        if len(members) == 1 and base != members[0].lower():
            out[members[0]].append(base)
    return out


def _mentions(sentence: str, aliases: list[str]) -> bool:
    s = sentence.lower()
    return any(re.search(rf"(?<![\w-]){re.escape(a)}(?![\w-])", s) for a in aliases)


# Checks the hidden-rolls block keeps secret: the reply says what the character notices, never the number.
HIDDEN_CHECKS = ("Perception", "Insight", "Investigation", "Stealth", "Survival")


def check(reply: str, facts: list[dict], creatures: dict[str, dict] | None = None,
          hide_monster_hp: bool = False, hidden_checks: tuple[str, ...] = ()) -> list[str]:
    """Contradictions between the reply and the facts. `creatures`: name → {"hp", "max_hp", "kind", "status"} from
    the live combat state (used for creatures that weren't damaged this turn). `hidden_checks`: skills whose totals
    the table rolls in secret (the hidden-rolls block)."""
    if not reply or not (facts or creatures):
        return []
    creatures = creatures or {}
    sentences = [s for s in _SENT.split(reply) if s.strip()]
    names = sorted({f.get("target") or f.get("who") or "" for f in facts} | {f.get("by", "") for f in facts} |
                   set(creatures), key=len, reverse=True)
    names = [n for n in names if n]
    al = _aliases(names)
    problems: list[str] = []

    def said(name: str, *patterns: re.Pattern, negated_ok: bool = False) -> list[str]:
        hits = []
        for s in sentences:
            if _mentions(s, al.get(name, [name.lower()])) and all(p.search(s) for p in patterns):
                if negated_ok or not _NEG.search(s):
                    hits.append(s.strip())
        return hits

    # Who is dead / alive by the end of the turn.
    dead = {f["target"] for f in facts if f["kind"] == "dead"}
    last_hp: dict[str, tuple[int, int]] = {}
    for f in facts:
        if f["kind"] in ("damage", "heal") and f.get("hp") is not None:
            last_hp[f["target"]] = (f["hp"], f.get("max") or 0)
    for name in names:
        if name in dead:
            continue
        hp = last_hp.get(name) or ((creatures[name]["hp"], creatures[name].get("max_hp", 0))
                                   if name in creatures and creatures[name].get("status") != "dead" else None)
        if hp and hp[0] > 0:
            for s in said(name, _DEATH):
                problems.append(f"“{_short(s)}” — but {name} is alive (HP {hp[0]}/{hp[1]}).")

    # Attacks: misses narrated as hits, hits narrated as misses.
    attacks: dict[str, list[dict]] = {}
    for f in facts:
        if f["kind"] == "attack":
            attacks.setdefault(f["target"], []).append(f)
    damaged = {f["target"] for f in facts if f["kind"] == "damage" and f.get("amount", 0) > 0}
    for target, atks in attacks.items():
        if all(not a.get("hit") for a in atks) and target not in damaged:
            for s in said(target, _HIT):
                if not _MISS.search(s):
                    who = ", ".join(sorted({a["by"] for a in atks}))
                    problems.append(f"“{_short(s)}” — but every attack on {target} this turn missed ({who}).")
        if all(a.get("hit") for a in atks) and target in damaged:
            for s in said(target, _MISS, negated_ok=True):
                if not _HIT.search(s):
                    problems.append(f"“{_short(s)}” — but the attack on {target} hit.")

    # Damage numbers that don't match what was dealt.
    for name in names:
        dealt = [f for f in facts if f["kind"] == "damage" and f["target"] == name]
        if not dealt:
            continue
        ok = {f["amount"] for f in dealt} | {f.get("raw") for f in dealt if f.get("raw") is not None} | \
             {sum(f["amount"] for f in dealt)}
        for s in sentences:
            if not _mentions(s, al.get(name, [name.lower()])):
                continue
            others = [n for n in names if n != name and _mentions(s, al.get(n, [n.lower()]))]
            if others:
                continue          # two creatures in one sentence: can't tell whose damage it is
            for m in _DMG_NUM.finditer(s):
                if int(m.group(1)) not in ok:
                    problems.append(f"“{_short(s)}” — {name} took {', '.join(str(f['amount']) for f in dealt)} damage, "
                                    f"not {m.group(1)}.")

    # Monster HP shown while it should be hidden.
    if hide_monster_hp:
        monsters = [n for n in names if (creatures.get(n, {}).get("kind") or "monster") != "pc" and
                    not any(f.get("target") == n and f.get("pc") for f in facts)]
        for name in monsters:
            for s in sentences:
                if _mentions(s, al.get(name, [name.lower()])) and _HP_NUM.search(s):
                    problems.append(f"“{_short(s)}” — hide_monster_hp is on: describe {name} as "
                                    "scratched/wounded/bloodied/near death instead of HP numbers.")

    # Secret checks shown with their numbers.
    hidden = {h.lower() for h in hidden_checks}
    for f in facts:
        word = str(f.get("what") or "")
        if f["kind"] != "check" or f.get("total") is None or word.lower() not in hidden:
            continue
        for line in reply.splitlines():
            if re.search(rf"\b{re.escape(word)}\b\D{{0,12}}?\d{{1,2}}\b", line, re.I):
                problems.append(f"“{_short(line)}” — hidden-rolls is on: say what {f.get('who') or 'the character'} "
                                f"notices or reads, without the {word} number (or the opposing roll).")

    # Checks and saves: the number shown and the outcome claimed.
    for f in facts:
        if f["kind"] not in ("check", "save") or f.get("total") is None:
            continue
        word = str(f.get("what") or f.get("ability") or "")
        if not word:
            continue
        same = [g for g in facts if g["kind"] == f["kind"] and str(g.get("what") or g.get("ability")) == word]
        for line in reply.splitlines():
            if not re.search(rf"\b{re.escape(word)}\b", line, re.I):
                continue
            if f["kind"] == "save" and not _mentions(line, al.get(f["who"], [f["who"].lower()])):
                continue
            fact = f
            m = re.search(rf"\b{re.escape(word)}\b\D{{0,12}}?(\d{{1,2}})\b", line, re.I)
            if m and f["kind"] == "check":
                n = int(m.group(1))
                match = [g for g in same if g.get("total") == n]
                if not match:
                    problems.append(f"“{_short(line)}” — the {word} roll was "
                                    f"{', '.join(str(g['total']) for g in same)}, not {n}.")
                    continue
                fact = match[0]
            if fact.get("ok") is None:
                continue
            claims_ok, claims_fail = bool(_SUCCESS.search(line)), bool(_FAIL.search(line))
            if claims_ok != claims_fail and claims_ok != bool(fact["ok"]):
                problems.append(f"“{_short(line)}” — the dice say {'success' if fact['ok'] else 'failure'} "
                                f"({fmt(fact)}).")
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:8]


def _short(s: str, n: int = 140) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
