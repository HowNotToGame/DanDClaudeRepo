"""Dice rolling with standard notation.

Supported syntax (terms joined with + or -):

    d20, 1d20+5, 2d6+1d4+3, d%           basic dice and constants
    4d6kh3 / 4d6k3, 2d20kl1             keep highest / lowest N
    4d6dl1, 3d6dh1                      drop lowest / highest N
    2d6r1, 2d6r<3                       reroll matching dice until they stop matching
    2d6ro<3                             reroll matching dice once (Great Weapon Fighting)
    1d6!                                exploding dice (roll again on max)
    1d20min10, 1d8max4                  clamp each die

Comparisons for rerolls use real maths: `<3` means 1 or 2, `<=3` means 1-3.
Flags: advantage/disadvantage turn the first d20 into 2d20kh1/2d20kl1, and a
critical hit doubles every die group (or maximises one copy, as a house rule).

Rolls use a cryptographically fair RNG unless ROBODM_SEED is set (tests/replays).
"""

from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass, field

from robodm.util import RoboDMError, ability_mod, fmt_mod

_MAX_EXTRA_ROLLS = 100


def _make_rng() -> random.Random:
    seed = os.environ.get("ROBODM_SEED")
    return random.Random(seed) if seed is not None else random.SystemRandom()


RNG = _make_rng()


def set_seed(seed) -> None:
    global RNG
    RNG = random.Random(seed) if seed is not None else random.SystemRandom()


class DiceError(RoboDMError):
    pass


@dataclass
class Die:
    value: int
    kept: bool = True
    rerolled: list[int] = field(default_factory=list)
    exploded: bool = False

    def render(self) -> str:
        s = str(self.value)
        if self.rerolled:
            s = "/".join(f"~~{r}~~" for r in self.rerolled) + "→" + s
        if self.exploded:
            s += "!"
        return s if self.kept else f"~~{s}~~"


@dataclass
class DiceGroup:
    count: int
    sides: int
    sign: int = 1
    keep: tuple[str, int] | None = None
    drop: tuple[str, int] | None = None
    reroll: tuple[str, int, bool] | None = None  # (comparator, value, once)
    explode: bool = False
    minimum: int | None = None
    maximum: int | None = None
    mods_text: str = ""
    dice: list[Die] = field(default_factory=list)

    @property
    def notation(self) -> str:
        sides = "%" if self.sides == 100 and "%" in self.mods_text else str(self.sides)
        return f"{self.count}d{sides}{self.mods_text.replace('%', '')}"

    @property
    def total(self) -> int:
        return self.sign * sum(d.value for d in self.dice if d.kept)

    def kept_values(self) -> list[int]:
        return [d.value for d in self.dice if d.kept]


@dataclass
class Constant:
    value: int
    sign: int = 1

    @property
    def total(self) -> int:
        return self.sign * self.value


@dataclass
class RollResult:
    expression: str
    terms: list
    label: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(t.total for t in self.terms)

    @property
    def natural(self) -> int | None:
        """Natural d20 value when the roll is a single (kept) d20 roll."""
        d20s = [t for t in self.terms if isinstance(t, DiceGroup) and t.sides == 20]
        if len(d20s) != 1:
            return None
        kept = d20s[0].kept_values()
        return kept[0] if len(kept) == 1 else None

    @property
    def is_crit(self) -> bool:
        return self.natural == 20

    @property
    def is_fumble(self) -> bool:
        return self.natural == 1

    def breakdown(self) -> str:
        parts = []
        for i, t in enumerate(self.terms):
            sign = "-" if t.sign < 0 else "+"
            if isinstance(t, DiceGroup):
                body = f"{t.notation}[{', '.join(d.render() for d in t.dice)}]"
            else:
                body = str(t.value)
            parts.append(body if i == 0 and sign == "+" else f" {sign} {body}")
        return "".join(parts)

    def text(self) -> str:
        label = f"{self.label}: " if self.label else ""
        flag = ""
        if self.natural == 20:
            flag = " — NAT 20!"
        elif self.natural == 1:
            flag = " — nat 1"
        note = f" ({'; '.join(self.notes)})" if self.notes else ""
        return f"{label}{self.expression} → {self.breakdown()} = **{self.total}**{flag}{note}"

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "expression": self.expression,
            "total": self.total,
            "natural": self.natural,
            "breakdown": self.breakdown(),
            "notes": self.notes,
        }


_TERM_RE = re.compile(
    r"\s*([+-])?\s*(?:(\d*)\s*d\s*(\d+|%)((?:kh\d*|kl\d*|k\d+|dh\d*|dl\d*|ro?(?:<=|>=|<|>|=)?\d+|!|min\d+|max\d+)*)|(\d+))",
    re.IGNORECASE,
)
_MOD_RE = re.compile(r"(kh|kl|k|dh|dl)(\d*)|(ro|r)(<=|>=|<|>|=)?(\d+)|(!)|(min|max)(\d+)", re.IGNORECASE)


def parse(expression: str) -> list:
    expr = re.sub(r"\s*([+\-])\s*", r"\1", expression.strip().lower())
    if not expr:
        raise DiceError("Empty dice expression.")
    if re.search(r"\s", expr):
        raise DiceError(f"Missing + or - between terms in '{expression}'.")
    pos, terms = 0, []
    while pos < len(expr):
        m = _TERM_RE.match(expr, pos)
        if not m or m.end() == pos:
            raise DiceError(f"Can't read dice expression '{expression}' near '{expr[pos:]}'.")
        sign_txt, count_txt, sides_txt, mods, const = m.groups()
        if terms and not sign_txt:
            raise DiceError(f"Missing + or - between terms in '{expression}'.")
        sign = -1 if sign_txt == "-" else 1
        if const is not None:
            terms.append(Constant(int(const), sign))
        else:
            count = int(count_txt) if count_txt else 1
            sides = 100 if sides_txt == "%" else int(sides_txt)
            if count < 1 or count > 1000:
                raise DiceError("Dice count must be between 1 and 1000.")
            if sides < 1 or sides > 10000:
                raise DiceError("Dice sides must be between 1 and 10000.")
            group = DiceGroup(count=count, sides=sides, sign=sign, mods_text=(mods or "") + ("%" if sides_txt == "%" else ""))
            for mm in _MOD_RE.finditer(mods or ""):
                kd, kd_n, rr, rr_cmp, rr_n, bang, clamp, clamp_n = mm.groups()
                if kd:
                    n = int(kd_n) if kd_n else 1
                    if kd in ("kh", "k"):
                        group.keep = ("h", n)
                    elif kd == "kl":
                        group.keep = ("l", n)
                    elif kd == "dh":
                        group.drop = ("h", n)
                    else:
                        group.drop = ("l", n)
                elif rr:
                    group.reroll = (rr_cmp or "=", int(rr_n), rr == "ro")
                elif bang:
                    if sides == 1:
                        raise DiceError("Can't explode a 1-sided die.")
                    group.explode = True
                elif clamp == "min":
                    group.minimum = int(clamp_n)
                else:
                    group.maximum = int(clamp_n)
            if group.reroll and _reroll_matches_all(group):
                raise DiceError(f"Reroll rule in '{expression}' matches every face; it would never stop.")
            terms.append(group)
        pos = m.end()
    return terms


def _matches(value: int, cmp: str, n: int) -> bool:
    return {
        "=": value == n,
        "<": value < n,
        "<=": value <= n,
        ">": value > n,
        ">=": value >= n,
    }[cmp]


def _reroll_matches_all(group: DiceGroup) -> bool:
    cmp, n, once = group.reroll
    return not once and all(_matches(v, cmp, n) for v in range(1, group.sides + 1))


def _roll_die(sides: int) -> int:
    return RNG.randint(1, sides)


def _roll_group(group: DiceGroup) -> None:
    group.dice = []
    pending = group.count
    extra = 0
    while pending > 0:
        pending -= 1
        die = Die(_roll_die(group.sides))
        if group.reroll:
            cmp, n, once = group.reroll
            tries = 0
            while _matches(die.value, cmp, n) and tries < _MAX_EXTRA_ROLLS:
                die.rerolled.append(die.value)
                die.value = _roll_die(group.sides)
                tries += 1
                if once:
                    break
        if group.minimum is not None:
            die.value = max(die.value, group.minimum)
        if group.maximum is not None:
            die.value = min(die.value, group.maximum)
        group.dice.append(die)
        if group.explode and die.value == group.sides and extra < _MAX_EXTRA_ROLLS:
            die.exploded = True
            pending += 1
            extra += 1
    if group.keep or group.drop:
        order = sorted(range(len(group.dice)), key=lambda i: group.dice[i].value)
        if group.keep:
            mode, n = group.keep
            keep_idx = set(order[-n:] if mode == "h" else order[:n]) if n > 0 else set()
        else:
            mode, n = group.drop
            drop_idx = set(order[-n:] if mode == "h" else order[:n]) if n > 0 else set()
            keep_idx = set(range(len(group.dice))) - drop_idx
        for i, d in enumerate(group.dice):
            d.kept = i in keep_idx


def roll(
    expression: str,
    *,
    adv: bool = False,
    dis: bool = False,
    crit: bool = False,
    crit_rule: str = "double_dice",
    label: str = "",
) -> RollResult:
    terms = parse(expression)
    notes = []
    if adv and dis:
        notes.append("advantage and disadvantage cancel")
    elif adv or dis:
        target = next((t for t in terms if isinstance(t, DiceGroup) and t.sides == 20 and t.count == 1 and not t.keep), None)
        if target is None:
            notes.append("no single d20 to apply advantage/disadvantage to")
        else:
            target.count = 2
            target.keep = ("h", 1) if adv else ("l", 1)
            target.mods_text = ("kh1" if adv else "kl1") + target.mods_text
            notes.append("advantage" if adv else "disadvantage")
    maxed: list[DiceGroup] = []
    if crit:
        if crit_rule == "max_plus_roll":
            for t in list(terms):
                if isinstance(t, DiceGroup):
                    maxed.append(t)
            notes.append("critical: max dice + roll")
        else:
            for t in terms:
                if isinstance(t, DiceGroup):
                    t.count *= 2
                    if t.keep or t.drop:
                        # keep/drop counts scale with the doubled pool
                        if t.keep:
                            t.keep = (t.keep[0], t.keep[1] * 2)
                        if t.drop:
                            t.drop = (t.drop[0], t.drop[1] * 2)
            notes.append("critical: dice doubled")
    for t in terms:
        if isinstance(t, DiceGroup):
            _roll_group(t)
    for g in maxed:
        kept = len(g.kept_values()) if (g.keep or g.drop) else g.count
        terms.append(Constant(kept * g.sides, g.sign))
    shown = expression.strip()
    if crit and crit_rule != "max_plus_roll":
        shown = _crit_expression(expression)
    return RollResult(expression=shown, terms=terms, label=label, notes=notes)


def _crit_expression(expression: str) -> str:
    def dbl(m):
        n = int(m.group(1)) if m.group(1) else 1
        return f"{n * 2}d"

    return re.sub(r"(\d*)d(?=\d|%)", dbl, expression.strip(), flags=re.IGNORECASE)   # not the d of dl/dh


def roll_d20(bonus: int = 0, *, adv: bool = False, dis: bool = False, label: str = "") -> RollResult:
    expr = f"1d20{fmt_mod(bonus)}" if bonus else "1d20"
    return roll(expr, adv=adv, dis=dis, label=label)


def roll_stats(method: str = "4d6") -> list[dict]:
    """Roll ability scores. method: 4d6 (drop lowest), 3d6, or standard (array)."""
    if method == "standard":
        return [{"score": s, "rolls": [], "mod": ability_mod(s)} for s in (15, 14, 13, 12, 10, 8)]
    expr = "4d6dl1" if method == "4d6" else "3d6"
    out = []
    for _ in range(6):
        r = roll(expr)
        out.append({"score": r.total, "rolls": [d.render() for d in r.terms[0].dice], "mod": ability_mod(r.total)})
    return out


def average(expression: str) -> float:
    """Expected value of a simple expression (keep/drop/reroll ignored)."""
    total = 0.0
    for t in parse(expression):
        if isinstance(t, DiceGroup):
            total += t.sign * t.count * (t.sides + 1) / 2
        else:
            total += t.total
    return total
