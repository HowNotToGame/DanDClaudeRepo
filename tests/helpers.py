"""Test helpers: temporary RoboDM homes, scripted dice, and a tiny fake 5etools dataset.

The fixture data is invented (not copied from any book) but uses the real 5etools JSON shapes.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from robodm import dice


class ScriptedRNG:
    """Stands in for dice.RNG: returns queued values for randint, then falls back to a seeded RNG."""

    def __init__(self, values=None):
        import random

        self.values = list(values or [])
        self._fallback = random.Random(1234)

    def push(self, *values):
        self.values.extend(values)

    def randint(self, a, b):
        if self.values:
            v = self.values.pop(0)
            assert a <= v <= b, f"scripted {v} outside {a}..{b}"
            return v
        return self._fallback.randint(a, b)

    def randrange(self, n):
        return self._fallback.randrange(n)


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make_fixture_data(root: Path) -> Path:
    d = root / "data"
    write(d / "spells/index.json", {"TST": "spells-tst.json"})
    write(d / "spells/sources.json", {"TST": {"Ember Burst": {"class": [{"name": "Wizard", "source": "PHB"}]}}})
    write(d / "spells/spells-tst.json", {"spell": [
        {"name": "Ember Burst", "source": "TST", "page": 1, "level": 3, "school": "V",
         "time": [{"number": 1, "unit": "action"}], "range": {"type": "point", "distance": {"type": "feet", "amount": 120}},
         "components": {"v": True, "s": True, "m": "a pinch of ash"},
         "duration": [{"type": "instant"}],
         "entries": ["Each creature in a 10-foot sphere makes a {@skill Dexterity} save, taking {@damage 6d6} fire damage on a "
                     "failure or half as much on a success. See {@condition prone|PHB|knocked down}."],
         "entriesHigherLevel": [{"type": "entries", "name": "At Higher Levels",
                                 "entries": ["+{@scaledamage 6d6|3-9|1d6} per slot level above 3rd."]}],
         "savingThrow": ["dexterity"]},
        {"name": "Still Mind", "source": "TST", "level": 2, "school": "E", "time": [{"number": 1, "unit": "action"}],
         "range": {"type": "point", "distance": {"type": "feet", "amount": 60}}, "components": {"v": True},
         "duration": [{"type": "timed", "duration": {"type": "minute", "amount": 1}, "concentration": True}],
         "entries": ["A humanoid must succeed on a Wisdom save or be {@condition paralyzed}."]},
    ]})
    write(d / "bestiary/index.json", {"TST": "bestiary-tst.json"})
    write(d / "bestiary/legendarygroups.json", {"legendaryGroup": []})
    write(d / "bestiary/bestiary-tst.json", {"monster": [
        {"name": "Mire Imp", "source": "TST", "page": 2, "size": ["S"], "type": {"type": "fiend", "tags": ["devil"]},
         "alignment": ["L", "E"], "ac": [{"ac": 13, "from": ["natural armor"]}], "hp": {"average": 10, "formula": "3d6"},
         "speed": {"walk": 20, "fly": 40}, "str": 6, "dex": 16, "con": 12, "int": 10, "wis": 12, "cha": 14,
         "save": {"dex": "+5"}, "resist": ["cold", {"resist": ["bludgeoning", "piercing", "slashing"],
                                                    "note": "from nonmagical attacks that aren't silvered", "cond": True}],
         "immune": ["fire", "poison"], "conditionImmune": ["poisoned"], "senses": ["darkvision 120 ft."], "passive": 11,
         "languages": ["Infernal"], "cr": "1",
         "action": [{"name": "Sting", "entries": ["{@atk mw} {@hit 5} to hit, reach 5 ft., one target. {@h}6 "
                                                  "({@damage 1d6 + 3}) piercing damage plus 7 ({@damage 2d6}) poison damage."]},
                    {"name": "Bog Breath {@recharge 5}", "entries": ["Each creature in a 15-foot cone must make a {@dc 12} "
                                                                     "Constitution saving throw, taking 10 ({@damage 3d6}) "
                                                                     "acid damage on a failed save, or half as much damage on a successful one."]}],
         "environment": ["swamp"]},
        {"name": "Mire Imp Elder", "source": "TST", "_copy": {"name": "Mire Imp", "source": "TST", "_mod": {
            "*": {"mode": "replaceTxt", "replace": "the imp", "with": "the elder", "flags": "i"},
            "action": {"mode": "appendArr", "items": {"name": "Curse", "entries": ["The elder curses a creature."]}}}},
         "hp": {"average": 30, "formula": "6d8 + 3"}, "cr": "3"},
        {"name": "Bog Brute", "source": "TST", "size": ["L"], "type": "giant", "alignment": ["C", "E"], "ac": [11],
         "hp": {"average": 40, "formula": "5d10 + 10"}, "speed": {"walk": 40}, "str": 18, "dex": 8, "con": 15, "int": 5,
         "wis": 7, "cha": 7, "passive": 8, "cr": "2", "vulnerable": ["fire"],
         "action": [{"name": "Club", "entries": ["{@atk mw} {@hit 6} to hit, reach 5 ft., one target. {@h}13 "
                                                 "({@damage 2d8 + 4}) bludgeoning damage."]}],
         "environment": ["swamp", "hill"]},
    ]})
    write(d / "class/index.json", {"warrior": "class-warrior.json"})
    write(d / "class/class-warrior.json", {
        "class": [{"name": "Warrior", "source": "TST", "hd": {"number": 1, "faces": 10}, "proficiency": ["str", "con"],
                   "classFeatures": ["Second Breath|Warrior|TST|1", "Surge|Warrior|TST|2"]}],
        "subclass": [{"name": "Champion of Mud", "shortName": "Mud", "source": "TST", "className": "Warrior",
                      "subclassFeatures": ["Mud|Warrior|TST|Mud|TST|3"]}],
        "classFeature": [{"name": "Surge", "source": "TST", "className": "Warrior", "classSource": "TST", "level": 2,
                          "entries": ["Take one additional action once per short rest."]}],
        "subclassFeature": []})
    write(d / "conditionsdiseases.json", {"condition": [
        {"name": "Prone", "source": "PHB", "srd": True, "entries": [{"type": "list", "items": ["You crawl.", "Melee attacks against you have advantage."]}]}],
        "disease": [], "status": [{"name": "Concentration", "source": "PHB", "entries": ["Keep focus."]}]})
    write(d / "items.json", {"item": [{"name": "Lantern of Echoes", "source": "TST", "rarity": "rare", "wondrous": True,
                                       "reqAttune": True, "entries": ["It hums."]}], "itemGroup": []})
    write(d / "items-base.json", {"baseitem": [{"name": "Mudsword", "source": "TST", "type": "M", "rarity": "none",
                                                "weaponCategory": "martial", "dmg1": "1d8", "dmgType": "S",
                                                "property": ["V"], "dmg2": "1d10", "value": 1500, "weight": 3}],
                                  "itemProperty": []})
    for f, key in (("magicvariants.json", "magicvariant"), ("actions.json", "action"), ("variantrules.json", "variantrule"),
                   ("feats.json", "feat"), ("races.json", "race"), ("backgrounds.json", "background"),
                   ("optionalfeatures.json", "optionalfeature"), ("skills.json", "skill"), ("senses.json", "sense"),
                   ("languages.json", "language"), ("deities.json", "deity"), ("trapshazards.json", "trap")):
        write(d / f, {key: []})
    write(d / "generated/bookref-quick.json", {"data": {"bookref-quick": [
        {"type": "entries", "entries": [{"type": "section", "name": "Cover", "entries": ["Half cover gives +2 to AC."]}]}]}})
    write(d / "_meta.json", {"fetched_at": "2026-01-01T00:00:00", "mirror": "fixture", "files": 1, "bytes": 1,
                             "sources": "all"})
    return d


class RoboDMTestCase(unittest.TestCase):
    """Gives each test an isolated ROBODM_HOME and fixture rules data, plus scripted dice."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="robodm-test-"))
        self._env = {k: os.environ.get(k) for k in ("ROBODM_HOME", "ROBODM_DATA")}
        os.environ["ROBODM_HOME"] = str(self.tmp / "home")
        (self.tmp / "home").mkdir()
        self.data_dir = make_fixture_data(self.tmp)
        os.environ["ROBODM_DATA"] = str(self.data_dir)
        from robodm.rules import index

        index.default_db.cache_clear()
        self.rng = ScriptedRNG()
        self._old_rng = dice.RNG
        dice.RNG = self.rng

    def tearDown(self):
        dice.RNG = self._old_rng
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        from robodm.rules import index

        index.default_db.cache_clear()
        shutil.rmtree(self.tmp, ignore_errors=True)
