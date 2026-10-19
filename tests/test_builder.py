"""Phase C: the Character Forge (`pc build`), data-driven `pc levelup`, equipment, attunement and the shop.

The fixture data is invented (a dwarf-like "Stoneborn", a "Bogrunner" background, the Warrior and Sage classes)
but uses the real 5etools shapes."""

from __future__ import annotations

import json

from robodm import audit, campaign
from tests.helpers import RoboDMTestCase, write
from tests.test_campaign_memory import run_cli


def _extend(data_dir) -> None:
    d = data_dir
    write(d / "races.json", {"race": [
        {"name": "Stoneborn", "source": "TST", "size": ["M"], "speed": 25, "ability": [{"con": 2}], "darkvision": 60,
         "languageProficiencies": [{"common": True, "dwarvish": True}], "resist": ["poison"],
         "entries": [{"type": "entries", "name": "Stone Toughness", "entries": ["Tough."]}]},
        {"name": "Mixed", "source": "TST", "size": ["M"], "speed": 30,
         "ability": [{"cha": 2, "choose": {"from": ["str", "dex", "con", "int", "wis"], "count": 2}}],
         "skillProficiencies": [{"any": 1}], "entries": []}],
        "subrace": [
            {"name": "Hill", "source": "TST", "raceName": "Stoneborn", "raceSource": "TST", "ability": [{"wis": 1}],
             "entries": [{"type": "entries", "name": "Dwarven Toughness", "entries": ["+1 HP per level."]}]},
            {"source": "TST", "raceName": "Mixed", "raceSource": "TST"}]})
    write(d / "backgrounds.json", {"background": [
        {"name": "Bogrunner", "source": "TST", "skillProficiencies": [{"athletics": True, "survival": True}],
         "startingEquipment": [{"_": ["common clothes|phb", {"item": "pouch|phb", "containsValue": 1500}]}],
         "entries": [{"type": "entries", "name": "Feature: Mud Paths", "entries": ["You know the paths."]}]}]})
    cls_path = d / "class/class-warrior.json"
    data = json.loads(cls_path.read_text(encoding="utf-8"))
    data["class"][0].update({
        "proficiency": ["str", "con"],
        "startingProficiencies": {"armor": ["light", "medium", "heavy", "shield"], "weapons": ["simple", "martial"],
                                  "skills": [{"choose": {"from": ["athletics", "perception", "survival", "intimidation"],
                                                         "count": 2}}]},
        "startingEquipment": {"default": ["(a) {@item bog mail|tst} or (b) {@item hide vest|tst}"],
                              "goldAlternative": "{@dice 5d4 × 10|5d4 × 10|Starting Gold}"},
        "multiclassing": {"requirements": {"or": [{"str": 13, "dex": 13}]}},
        "subclassTitle": "Warrior Path",
        "classFeatures": ["Second Breath|Warrior|TST|1", "Surge|Warrior|TST|2",
                          {"classFeature": "Warrior Path|Warrior|TST|3", "gainSubclassFeature": True},
                          "Ability Score Improvement|Warrior|TST|4"]})
    data["subclass"][0]["classSource"] = "TST"
    write(cls_path, data)
    write(d / "class/class-sage.json", {"class": [{"name": "Sage", "source": "TST", "hd": {"number": 1, "faces": 6},
                                                   "proficiency": ["int", "wis"], "spellcastingAbility": "int",
                                                   "casterProgression": "full", "cantripProgression": [3, 3, 3, 4],
                                                   "preparedSpells": "<$level$> + <$int_mod$>",
                                                   "multiclassing": {"requirements": {"int": 13},
                                                                     "proficienciesGained": {"armor": []}},
                                                   "startingProficiencies": {"skills": [{"choose": {"from": ["arcana", "history"], "count": 2}}]},
                                                   "classFeatures": ["Spellcasting|Sage|TST|1"]}],
                                        "subclass": [], "classFeature": [], "subclassFeature": []})
    idx = json.loads((d / "class/index.json").read_text(encoding="utf-8"))
    idx["sage"] = "class-sage.json"
    write(d / "class/index.json", idx)
    base = json.loads((d / "items-base.json").read_text(encoding="utf-8"))
    base["baseitem"] += [
        {"name": "Bog Mail", "source": "TST", "type": "HA", "ac": 16, "strength": "16", "stealth": True, "armor": True,
         "weight": 55, "value": 7500},
        {"name": "Hide Vest", "source": "TST", "type": "MA", "ac": 12, "armor": True, "weight": 12, "value": 1000},
        {"name": "Reed Shield", "source": "TST", "type": "S", "ac": 2, "weight": 6, "value": 1000},
        {"name": "Mudbow", "source": "TST", "type": "R", "weaponCategory": "simple", "property": ["A", "2H"],
         "range": "80/320", "dmg1": "1d6", "dmgType": "P", "weapon": True, "weight": 2, "value": 2500}]
    write(d / "items-base.json", base)
    items = json.loads((d / "items.json").read_text(encoding="utf-8"))
    items["item"] += [{"name": f"Charm of Warding {n}", "source": "TST", "rarity": "uncommon", "reqAttune": True,
                       **({"bonusAc": "+1"} if n == 1 else {})} for n in range(1, 5)]
    write(d / "items.json", items)


class ForgeTests(RoboDMTestCase):
    def setUp(self):
        super().setUp()
        _extend(self.data_dir)
        from robodm.rules import index

        index.default_db.cache_clear()
        self.ok("campaign", "new", "Forge", "--premise", "test")
        self.ok("campaign", "set", "sources", "TST,PHB")

    def ok(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 0, f"{argv}: {out}{err}")
        return out

    def blocked(self, *argv) -> str:
        code, out, err = run_cli(*argv)
        self.assertEqual(code, 1, f"{argv} should be blocked: {out}")
        return err

    BUILD = ("pc", "build", "Brann", "--race", "Hill Stoneborn", "--class", "Warrior", "--background", "Bogrunner",
             "--scores", "standard", "--assign", "str=15,dex=12,con=14,int=8,wis=13,cha=10")

    def test_forge_checks_everything_then_builds_the_sheet(self):
        err = self.blocked(*self.BUILD)
        self.assertIn("choose 2 Warrior skills from: athletics, perception, survival, intimidation", err)
        err = self.blocked(*self.BUILD, "--skills", "athletics,perception")
        self.assertIn("athletics already come from your race or background", err)
        bad = list(self.BUILD)
        bad[-1] = "str=15,dex=15,con=14,int=8,wis=13,cha=10"
        self.assertIn("the standard array is 15, 14, 13, 12, 10, 8", self.blocked(*bad, "--skills", "perception,intimidation"))
        out = self.ok(*self.BUILD, "--skills", "perception,intimidation", "--preview")
        self.assertIn("nothing saved", out)
        self.assertIsNone(campaign.load().find_pc("Brann"))
        out = self.ok(*self.BUILD, "--skills", "perception,intimidation", "--gear", "Bog Mail,Reed Shield,Mudsword,Mudbow")
        pc = campaign.load().get_pc("Brann")
        self.assertEqual(pc["race"], "Hill Stoneborn")
        self.assertEqual(pc["abilities"]["con"], 16)                    # 14 + 2
        self.assertEqual(pc["abilities"]["wis"], 14)                    # 13 + 1 (subrace)
        self.assertEqual(pc["hp"]["max"], 10 + 3 + 1)                   # d10 + CON + Dwarven Toughness
        self.assertEqual(pc["speed"], 25)
        self.assertEqual(sorted(pc["skill_proficiencies"]), ["athletics", "intimidation", "perception", "survival"])
        self.assertEqual(pc["save_proficiencies"], ["str", "con"])
        self.assertIn("Second Breath", pc["features"])
        self.assertIn("Mud Paths (background)", pc["features"])
        self.assertEqual(pc["gold"], 15)                                # the pouch
        self.assertEqual(pc["ac"], 18)                                  # Bog Mail 16 + shield 2
        self.assertIn("needs STR 16: speed −10 ft.", out)
        atk = {a["name"]: a for a in pc["attacks"]}
        self.assertEqual(atk["Mudsword"]["to_hit"], 4)                  # STR +2, prof +2
        self.assertEqual(atk["Mudsword"]["versatile"], "1d10+2")
        self.assertEqual(atk["Mudbow"]["range"], [80, 320])
        self.assertEqual(atk["Mudbow"]["damage"][0][0], "1d6+1")        # DEX +1
        self.assertIn("pc build", audit.events(campaign.load())[-1]["cmd"])     # the rules-lawyer audits it

    def test_point_buy_rolled_scores_and_racial_choices(self):
        args = ("pc", "build", "Ivy", "--race", "Mixed", "--class", "Warrior", "--background", "Bogrunner")
        err = self.blocked(*args, "--scores", "pointbuy", "--assign", "str=15,dex=15,con=15,int=10,wis=8,cha=8",
                           "--skills", "perception,intimidation,history")
        self.assertIn("those scores cost 29 points; the budget is 27", err)
        self.assertIn("Mixed: choose 2 different abilities for +1", err)
        out = self.ok(*args, "--scores", "roll")
        self.assertIn("Ability scores for Ivy", out)
        rolled = audit.rolled_scores(campaign.load(), "ivy")
        self.assertEqual(len(rolled), 6)
        self.assertIn("scores were already rolled for Ivy", self.blocked(*args, "--scores", "roll"))   # no fishing
        wrong = ",".join(f"{a}={v}" for a, v in zip(("str", "dex", "con", "int", "wis", "cha"), [18] * 6))
        self.assertIn("assign the scores that were rolled",
                      self.blocked(*args, "--scores", "roll", "--assign", wrong, "--asi", "str=1,dex=1",
                                   "--skills", "perception,intimidation,history"))
        right = ",".join(f"{a}={v}" for a, v in zip(("str", "dex", "con", "int", "wis", "cha"), rolled))
        self.ok(*args, "--scores", "roll", "--assign", right, "--asi", "str=1,dex=1",
                "--skills", "perception,intimidation,history", "--gold")
        pc = campaign.load().get_pc("Ivy")
        self.assertEqual(pc["abilities"]["cha"], rolled[5] + 2)
        self.assertEqual(pc["abilities"]["str"], rolled[0] + 1)
        self.assertGreater(pc["gold"], 15)                              # pouch + 5d4 × 10

    def warrior(self, level=1):
        self.ok(*self.BUILD, "--skills", "perception,intimidation", "--gear", "Hide Vest,Mudsword")
        for _ in range(level - 1):
            self.ok("pc", "levelup", "brann", "--subclass", "Mud", "--asi", "later")

    def test_levelup_adds_features_subclass_and_asi(self):
        self.warrior()
        out = self.ok("pc", "levelup", "brann")
        self.assertIn("＋ Feature: Surge", out)
        self.assertIn("＋ HP 14 → 24 (+10, average 6+3, +1 Dwarven Toughness)", out)
        out = self.ok("pc", "levelup", "brann", "--preview")
        self.assertIn("⚠️ needs: Warrior 3 gains a Warrior Path: add --subclass (options: Champion of Mud)", out)
        self.assertEqual(campaign.load().get_pc("brann")["level"], 2)
        self.assertIn("gains a Warrior Path", self.blocked("pc", "levelup", "brann"))
        out = self.ok("pc", "levelup", "brann", "--subclass", "Mud")
        self.assertIn("＋ Warrior Path: Champion of Mud", out)
        self.assertIn("＋ Feature: Mud (Champion of Mud)", out)
        self.assertIn("Ability Score Improvement", self.blocked("pc", "levelup", "brann"))
        self.assertIn("+2 to one score or +1 to two", self.blocked("pc", "levelup", "brann", "--asi", "str=3"))
        out = self.ok("pc", "levelup", "brann", "--asi", "str=2")
        self.assertIn("＋ Strength 15 → 17", out)
        pc = campaign.load().get_pc("brann")
        self.assertEqual((pc["level"], pc["subclass"]), (4, "Champion of Mud"))
        self.assertEqual({a["name"]: a for a in pc["attacks"]}["Mudsword"]["to_hit"], 5)   # STR 17 now

    def test_multiclass_prerequisites_and_homebrew_classes(self):
        self.warrior()
        err = self.blocked("pc", "levelup", "brann", "--class", "Sage")
        self.assertIn("multiclassing into Sage needs INT 13", err)
        self.ok("pc", "levelup", "brann", "--class", "Sage", "--ruling", "the table allows it")
        pc = campaign.load().get_pc("brann")
        self.assertEqual([(c["class"], c["level"]) for c in pc["classes"]], [("Warrior", 1), ("Sage", 1)])
        self.ok("pc", "add", "Kel", "--class", "Blood Hunter", "--level", "3", "--con", "14")
        out = self.ok("pc", "levelup", "kel")
        self.assertIn("isn't in the rules data: add its level-4 features by hand", out)
        self.assertEqual(campaign.load().get_pc("kel")["level"], 4)

    def test_equipment_attunement_and_shop(self):
        self.ok("pc", "add", "Mira", "--class", "Sage", "--dex", "14", "--str", "8")
        out = self.ok("pc", "equip", "mira", "Bog Mail")
        self.assertIn("isn't proficient with heavy armor", out)
        self.assertIn("AC 12 → 16", out)
        self.ok("pc", "unequip", "mira", "Bog Mail")
        self.assertEqual(campaign.load().get_pc("mira")["ac"], 12)
        for n in (1, 2, 3):
            self.ok("pc", "attune", "mira", f"Charm of Warding {n}")
        self.assertEqual(campaign.load().get_pc("mira")["ac"], 13)       # Charm 1: +1 AC
        self.assertIn("already attuned to 3 items", self.blocked("pc", "attune", "mira", "Charm of Warding 4"))
        self.assertIn("doesn't require attunement", self.blocked("pc", "attune", "mira", "Reed Shield"))
        self.ok("pc", "unattune", "mira", "Charm of Warding 1")
        self.assertEqual(campaign.load().get_pc("mira")["ac"], 12)
        self.ok("pc", "gold", "mira", "100")
        self.assertIn("Mudbow: 25 gp", self.ok("shop", "Mudbow"))
        self.assertIn("buys Mudbow for 25 gp → 75 gp left", self.ok("shop", "Mudbow", "--pc", "mira", "--buy"))
        self.assertIn("sells Mudbow for 12.5 gp → 87.5 gp", self.ok("shop", "Mudbow", "--pc", "mira", "--sell"))
        self.assertIn("carries about", self.ok("pc", "load", "mira"))

    def test_forge_options_for_the_web_client(self):
        from robodm import builder

        o = builder.forge_options(campaign.load())
        self.assertIn("Hill Stoneborn", o["races"])
        self.assertIn("Mixed", o["races"])                               # has an unnamed subrace: valid on its own
        self.assertNotIn("Stoneborn", o["races"])                        # needs a subrace
        warrior = next(c for c in o["classes"] if c["name"] == "Warrior")
        self.assertEqual((warrior["skill_count"], warrior["subclass_level"]), (2, 3))
        self.assertEqual(o["backgrounds"], ["Bogrunner"])
