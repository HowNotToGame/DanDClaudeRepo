"""Cost and speed: the `turn_speed` table setting, and what the web client's DM turns cost.

`./dm campaign set turn_speed fast|balanced|thorough` trades thoroughness for time and usage:

| | fast | balanced (default) | thorough |
|---|---|---|---|
| rules-lawyer / companions / lore-keeper models | haiku / haiku / sonnet | sonnet / sonnet / sonnet | opus / opus / opus |
| post-audit | routine mechanical events auto-pass (the engine computed them and the receipt check agreed) | every event | every event |
| brief size | 6,000 chars | 9,000 | 14,000 |

What never changes: the code gate before every rules command, first-use pre-checks by the rules-lawyer, the
receipt check of the narration, and the audit of anything involving judgement (spells, conditions, rulings,
overrides, character changes). `dm_model` (web client only) picks the DM's own model.

A model or effort chosen for a subagent in the web client's ⚙ panel (or `./dm engine agent`) wins over the turn
speed; it lives in the per-machine engine config (`robodm/engines`), and `apply_speed` writes both into the agent
files' frontmatter (`model:`, `effort:`). Without a chosen effort the agent inherits the DM's.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from robodm.campaign import Campaign
from robodm.util import now_iso, read_jsonl

SPEEDS = ("fast", "balanced", "thorough")
AGENT_MODELS = {"fast": {"rules-lawyer": "haiku", "lore-keeper": "sonnet", "companion": "haiku"},
                "balanced": {"rules-lawyer": "sonnet", "lore-keeper": "sonnet", "companion": "sonnet"},
                "thorough": {"rules-lawyer": "opus", "lore-keeper": "opus", "companion": "opus"}}
BRIEF_BUDGETS = {"fast": 6000, "balanced": 9000, "thorough": 14000}
# Rules commands whose result the engine computes itself (dice, arithmetic, resistances, death saves…): in fast
# mode they pass the post-audit automatically when there was no override and the narration matched the dice.
MECHANICAL = {("combat", "attack"), ("combat", "damage"), ("combat", "dmg"), ("combat", "heal"), ("combat", "temp"),
              ("combat", "save"), ("combat", "deathsave"), ("combat", "stabilize"), ("combat", "end"),
              ("pc", "hp"), ("pc", "temp"), ("pc", "check"), ("pc", "save"), ("pc", "hitdie"), ("pc", "rest"),
              ("pc", "slot")}


def speed(camp: Campaign | None) -> str:
    s = (camp.settings.get("turn_speed") if camp else None) or "balanced"
    return s if s in SPEEDS else "balanced"


def brief_budget(camp: Campaign | None) -> int:
    return BRIEF_BUDGETS[speed(camp)]


# =============================================================================== agent models

def set_frontmatter(path: Path, key: str, value: str | None) -> bool:
    """Set (or, with a blank value, remove) `key:` in an agent file's frontmatter. True if the file changed."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    end = text.index("\n---", 3)
    head, rest = text[:end], text[end:]
    pat = rf"(?m)^{re.escape(key)}:.*$"
    if value:
        new_head = re.sub(pat, f"{key}: {value}", head) if re.search(pat, head) else head + f"\n{key}: {value}"
    else:
        new_head = re.sub(pat + r"\n?", "", head).rstrip("\n")
    if new_head == head:
        return False
    path.write_text(new_head + rest, encoding="utf-8")
    return True


def set_model(path: Path, model: str) -> bool:
    """Set `model:` in an agent file's frontmatter. True if the file changed."""
    return set_frontmatter(path, "model", model)


def agent_choice(camp: Campaign | None, role: str) -> tuple[str, str, bool]:
    """(model, effort, chosen) for a Claude Code subagent: the ⚙ choice, else the turn speed's model and no effort
    (the agent inherits the DM's). `chosen` says the model was picked explicitly."""
    from robodm import engines

    a = engines.agent(engines.load(), role, "claude")
    return (a["model"] or AGENT_MODELS[speed(camp)][role]), a["effort"], bool(a["model"])


def describe(model: str, effort: str, chosen: bool = False) -> str:
    return f"{model}{'' if chosen else ' (turn speed)'}" + (f" · effort {effort}" if effort else "")


def apply_speed(camp: Campaign | None) -> list[str]:
    """Point the subagents at their models and efforts: the ⚙ choices, else this campaign's turn speed."""
    from robodm import companions

    root = companions.agents_dir()
    lines = []
    for name in ("rules-lawyer", "lore-keeper"):
        model, effort, chosen = agent_choice(camp, name)
        changed = set_frontmatter(root / f"{name}.md", "model", model)
        changed = set_frontmatter(root / f"{name}.md", "effort", effort) or changed
        if changed:
            lines.append(f"{name} → {describe(model, effort, chosen)}")
    if camp is not None:
        model, effort, chosen = agent_choice(camp, "companion")
        for slug in companions.all_slugs(camp):
            p = companions.write_agent(camp, slug, model=model, effort=effort)
            lines.append(f"{p.stem} → {describe(model, effort, chosen)}")
        generic = root / "companion.md"
        text = companions.generic_agent_text(model, effort)
        if generic.exists() and generic.read_text(encoding="utf-8") != text:
            generic.write_text(text, encoding="utf-8")
            lines.append(f"companion → {describe(model, effort, chosen)}")
    return lines


# =============================================================================== the post-audit in fast mode

def autopassable(event: dict) -> bool:
    words = [w for w in str(event.get("cmd", "")).split() if not w.startswith("-")]
    key = tuple(words[1:3]) if len(words) >= 3 and words[0].endswith("dm") else ()
    return (event.get("status") == "pending" and not event.get("override") and not event.get("fixes")
            and key in MECHANICAL)


# =============================================================================== usage (web client turns)

def _usage_path(camp: Campaign) -> Path:
    return camp.path(".cache", "web-usage.jsonl")


def record_usage(camp: Campaign, cost: float | None, ms: int | None, turns: int | None, private: bool = False,
                 engine: str = "claude") -> None:
    p = _usage_path(camp)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": now_iso(), "session": camp.session.get("number", 0), "cost": cost, "ms": ms, "turns": turns,
           "private": private, "speed": speed(camp), "engine": engine}
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def usage_summary(camp: Campaign) -> dict:
    rows = read_jsonl(_usage_path(camp))
    sess = camp.session.get("number", 0)
    cur = [r for r in rows if r.get("session") == sess]

    def total(rs):
        return round(sum(r.get("cost") or 0 for r in rs), 4)

    return {"session": sess, "turns": len(cur), "cost": total(cur), "all_turns": len(rows), "all_cost": total(rows),
            "avg_seconds": round(sum(r.get("ms") or 0 for r in cur) / len(cur) / 1000, 1) if cur else None,
            "last_cost": (cur[-1].get("cost") if cur else None), "speed": speed(camp),
            "engine": (cur[-1].get("engine", "claude") if cur else None)}
