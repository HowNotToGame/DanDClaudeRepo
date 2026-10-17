#!/usr/bin/env python3
"""Banked (Pale Moon Blood Hunter) NPC sheet builder for RoboDM.

Usage:
  python3 tools/banked.py all                 # (re)build the five founding sheets (JSON + PDF)
  python3 tools/banked.py new spec.json       # build one sheet from a JSON spec (see banked/README.md)
  python3 tools/banked.py pdf <slug>|all      # (re)fill PDFs only
  python3 tools/banked.py present a,b,c|none  # mark who is currently with the party

Sheets live in campaigns/<campaign>/banked/<slug>.json (same schema as party/*.json).
PDFs need the optional 'pypdf' package and Books/Character-Sheet-Alternative-Form-Fillable.pdf.
"""
from __future__ import annotations
import json, sys, datetime, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAMPAIGN = ROOT / "campaigns" / "crown-of-ash"
BANKED = CAMPAIGN / "banked"
BLANK = ROOT / "Books" / "Character-Sheet-Alternative-Form-Fillable.pdf"
PDF_DIR = ROOT / "Books" / "Banked"
ABIL = ["str", "dex", "con", "int", "wis", "cha"]
SKILL_ABIL = {"acrobatics": "dex", "animal handling": "wis", "arcana": "int", "athletics": "str",
              "deception": "cha", "history": "int", "insight": "wis", "intimidation": "cha",
              "investigation": "int", "medicine": "wis", "nature": "int", "perception": "wis",
              "performance": "cha", "persuasion": "cha", "religion": "int", "sleight of hand": "dex",
              "stealth": "dex", "survival": "wis"}
BEASTS = {"Brown Bear": "CR 1", "Giant Eagle": "CR 1", "Wolf": "CR 1/4", "Boar": "CR 1/4",
          "Black Bear": "CR 1/2", "Giant Owl": "CR 1/4", "Giant Vulture": "CR 1", "Giant Swan": "homebrew",
          "Giant Raven": "homebrew", "Hippogriff": "CR 1", "Griffin": "CR 2", "Owlbear": "CR 3",
          "Manticore": "CR 3", "Roc": "CR 11", "Owl": "CR 0", "Eagle": "CR 0", "Crow": "CR 0", "Hawk": "CR 0"}
# Beast form tiers (Pale Moon, player canon): the form upgrades permanently at 7, 13 and 18; never ask for defaults.
TIER7 = {"Giant Vulture", "Giant Swan", "Giant Raven", "Giant Eagle", "Hippogriff", "Griffin"}
TIER13 = {"Owlbear", "Manticore"}


def mod(score): return (score - 10) // 2
def sign(n): return f"{n:+d}"


def rite_die(level):
    return "d4" if level < 5 else "d6" if level < 11 else "d8" if level < 17 else "d10"


def build(spec):
    L = spec["level"]; pb = 2 + (L - 1) // 4
    for need in ("race_name", "beast"):
        if need not in spec:
            raise SystemExit(f"spec is missing '{need}': always ask the player for the convert's race and beast")
    b = spec["beast"]
    if L >= 18 and b != "Roc": raise SystemExit("level 18+ Banked take the Roc")
    if 13 <= L < 18 and b not in TIER13: raise SystemExit("level 13-17 Banked take Owlbear or Manticore")
    if 7 <= L < 13 and b not in TIER7: raise SystemExit("level 7-12 Banked take Giant Vulture/Swan/Raven/Eagle, Hippogriff or Griffin")
    if L < 7 and BEASTS.get(b, "") not in ("CR 0", "CR 1/4", "CR 1/2", "CR 1"): raise SystemExit("level 3-6 beasts must be CR 1 or lower")
    rac = spec.get("racial", 1)
    ab = {a: spec["array"][a] + (rac.get(a, 0) if isinstance(rac, dict) else rac) for a in ABIL}
    for a, n in spec.get("asi", {}).items(): ab[a] += n
    m = {a: mod(ab[a]) for a in ABIL}
    hp = 12 + m["con"] + (L - 1) * (7 + m["con"])
    skills = [s.lower() for s in spec["skills"]]
    style = spec["style"]; attacks = []
    for w in spec["weapons"]:
        stat = w["ability"]; to_hit = m[stat] + pb; dmg = m[stat]
        if style == "Archery" and w.get("ranged"): to_hit += 1
        if style == "Versatile Fighting" and not w.get("ranged"): to_hit += 1
        if style == "Balanced Fighting" and not w.get("ranged"): dmg += 2
        attacks.append({"name": w["name"], "to_hit": to_hit,
                        "damage": [[f"{w['dice']}{dmg:+d}" if dmg else w["dice"], w["type"]]]})
    dc = 8 + pb + m["con"]
    rd = rite_die(L)
    feats = [f"BLOOD HUNTER {L} (Alternate, laserllama v2.2; Banked of Okton's order). Rite Die {rd}, Rite save DC {dc}",
             f"Fighting Style: {style}",
             f"Hunter's Bane: Occult Lore ({spec['occult']}); Sinister Insight (bonus action, CON check, DC 8+CR)"]
    if L >= 2:
        feats.append("Blood Rites known: " + ", ".join(spec["rites"]) + " (each free once per rest, then Vital Sacrifice)")
        feats.append("Crimson Offering (bonus action + Vital Sacrifice; 1 hour; Rite Die extra damage)")
        feats.append("Vital Sacrifice: lose current and max HP equal to the Rite Die until a long rest")
    if L >= 3:
        feats.append(f"Order of the Pale Moon: Feral Senses; Lycanthropic Curse ({spec['beast']}, {BEASTS[spec['beast']]}); Hybrid Form")
    else:
        feats.append(f"Order of the Pale Moon at level 3 (Okton gives only this order). Latent beast: {spec['beast']}.")
    if L >= 4: feats.append("Ability Score Improvement (4th)")
    if spec.get("traits"): feats.append(f"Race ({spec['race_name']}): {spec['traits']}")
    feats.append("Banked: advantage on saves against losing control of the burn; can bleed the heat off with a Vital Sacrifice (drops one stage for 24 hours)")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    sheet = {
        "name": spec["name"], "slug": spec["slug"], "player": "", "npc": True, "present": False,
        "race": spec.get("race_name", "Human"), "class": "Blood Hunter (Alternate)",
        "subclass": "Order of the Pale Moon" if L >= 3 else "Pale Moon (from level 3)", "level": L,
        "background": spec.get("background", "City Watch"), "abilities": ab, "ac": spec["ac"], "speed": spec.get("speed", 30),
        "hp": {"max": hp, "current": hp, "temp": 0}, "hit_dice": {"die": 12, "max": L, "used": 0},
        "save_proficiencies": ["con", "cha"], "skill_proficiencies": skills, "expertise": [],
        "jack_of_all_trades": False, "initiative_bonus": None, "spellcasting_ability": "", "spell_slots": {},
        "spells": [], "attacks": attacks,
        "resources": {**{f"Rite of {r} (free use)": {"max": 1, "used": 0, "reset": "short"} for r in spec["rites"]},
                      **({"Lycanthropic Shift (free use)": {"max": 1, "used": 0, "reset": "short"}} if L >= 3 else {})},
        "conditions": [], "inventory": spec["inventory"], "gold": spec.get("gold", 5), "xp": 0,
        "death_saves": {"success": 0, "fail": 0}, "features": feats,
        "notes": f"Age {spec['age']}, {spec['gender']}. Pre-conversion level {L}. Rank: Raw (converted day 3). {spec['look']} {spec['hook']}",
        "created": now, "updated": now,
        "rites": spec["rites"], "beast": spec["beast"], "rank": "Raw", "rite_die": rd, "rite_dc": dc, "prof": pb,
    }
    return sheet


SPECS = [
 dict(name="Hesby Bramble", slug="hesby-bramble", race_name="Bronze Dragonborn", racial=dict(str=2,cha=1), traits="Metallic ancestry (bronze): lightning breath (15 ft line, DEX save, 2d6 at 1st; uses = PB per long rest), resistance to lightning, Draconic Presence", level=4, age=46, gender="male",
      array=dict(con=15, str=14, wis=13, dex=12, cha=10, int=8), asi=dict(con=1, str=1),
      skills=["athletics", "insight", "survival", "history", "religion"], occult="Religion",
      style="Balanced Fighting", rites=["Binding", "Blindness"], beast="Eagle", ac=17,
      weapons=[dict(name="Spear (one hand)", ability="str", dice="1d6", type="piercing")],
      inventory=["Spear", "Shield", "Scale mail", "Redcollar gorget", "Bleeding-knife", "Wife's ribbon", "Rations"], gold=12,
      look="Thick, grizzled, grey-bearded, a sergeant's flat gaze.",
      hook="Watch sergeant who drew the Pyrehouse beat. Wife Marta thinks he is ill. Loyal to Halda and Okton."),
 dict(name="Gwenis Dunmore", slug="gwenis-dunmore", race_name="Human", level=3, age=29, gender="female",
      array=dict(con=15, dex=14, wis=13, str=12, cha=10, int=8),
      skills=["athletics", "insight", "survival", "investigation", "nature"], occult="Nature",
      style="Archery", rites=["Revelation", "Siphoning"], beast="Crow", ac=14,
      weapons=[dict(name="Light crossbow", ability="dex", dice="1d8", type="piercing", ranged=True),
               dict(name="Shortsword", ability="dex", dice="1d6", type="piercing")],
      inventory=["Light crossbow", "20 bolts", "Shortsword", "Studded leather", "Redcollar gorget", "Bleeding-knife"], gold=8,
      look="Lean, sharp-eyed, a long braid and a hunter's stillness.",
      hook="Came back from the rage in one clear moment and remembers all of it. Guilty over the roc's blood. Feels Okton like a banked fire in the next room. Sister Kita."),
 dict(name="Kita Dunmore", slug="kita-dunmore", race_name="Human", level=2, age=22, gender="female",
      array=dict(dex=15, con=14, wis=13, cha=12, str=10, int=8),
      skills=["athletics", "insight", "acrobatics", "survival", "nature"], occult="Nature",
      style="Dual Wielding", rites=["Agony", "Binding"], beast="Crow", ac=15,
      weapons=[dict(name="Shortsword (main)", ability="dex", dice="1d6", type="piercing"),
               dict(name="Shortsword (off hand)", ability="dex", dice="1d6", type="piercing")],
      inventory=["Two shortswords", "Studded leather", "Redcollar gorget", "Bleeding-knife", "Sling"], gold=5,
      look="Lanky, bandaged shoulder, dry grin.",
      hook="Gwenis's younger sister. Jokes to keep the heat down; fiercely protective."),
 dict(name="Cassmund Furrow", slug="cassmund-furrow", race_name="Half-Orc", racial=dict(str=2,con=1), traits="Darkvision 60 ft, Relentless Endurance (drop to 1 HP instead of 0, once per long rest), Savage Attacks (extra weapon die on a crit)", level=2, age=24, gender="male",
      array=dict(str=15, con=14, dex=13, wis=12, cha=10, int=8),
      skills=["athletics", "insight", "history", "investigation", "arcana"], occult="Arcana",
      style="Versatile Fighting", rites=["Siphoning", "Binding"], beast="Hawk", ac=15,
      weapons=[dict(name="Longsword (two-handed 1d10)", ability="str", dice="1d8", type="slashing")],
      inventory=["Longsword", "Chain shirt", "Redcollar gorget", "Bleeding-knife", "Lucky coin"], gold=6,
      look="Tall, earnest, wide-eyed; a farmer's hands.",
      hook="Farm boy turned Watch recruit. Quick to laugh, quick to be afraid of himself."),
 dict(name="Samson Cobb", slug="samson-cobb", race_name="Giff", racial=dict(str=2,con=1), traits="Astral Spark, Firearms Mastery, Hippo Build (advantage on Strength checks and Strength saves; powerful build)", level=2, age=31, gender="male",
      array=dict(con=15, str=14, dex=13, wis=12, cha=10, int=8),
      skills=["athletics", "insight", "survival", "history", "religion"], occult="Religion",
      style="Great Weapon Fighting", rites=["Agony", "Blindness"], beast="Owl", ac=16,
      weapons=[dict(name="Maul", ability="str", dice="2d6", type="bludgeoning")],
      inventory=["Maul", "Scale mail", "Redcollar gorget", "Bleeding-knife", "Dockworker's gloves"], gold=4,
      look="Broad, quiet, scar through one eyebrow.",
      hook="Ex-dockworker, steady and slow to speak. Keeps promises."),
]


def write_sheet(sheet):
    BANKED.mkdir(parents=True, exist_ok=True)
    p = BANKED / f"{sheet['slug']}.json"
    if p.exists():
        old = json.loads(p.read_text()); sheet["present"] = old.get("present", False); sheet["created"] = old.get("created", sheet["created"])
    p.write_text(json.dumps(sheet, indent=2))
    return p


def fill_pdf(sheet):
    try:
        import pypdf
        from pypdf.generic import NameObject, TextStringObject
    except ImportError:
        print("pypdf not installed; skipping PDF"); return None
    r = pypdf.PdfReader(str(BLANK)); w = pypdf.PdfWriter(clone_from=r); pg = w.pages[0]
    annots = pg["/Annots"]; acro = w._root_object["/AcroForm"]
    for idx, name, ft in ((47, "Insight", "/Tx"), (67, "ChBx Medicine", "/Btn")):
        ref = annots[idx]; a = ref.get_object(); par = a["/Parent"].get_object()
        for i, k in enumerate(par["/Kids"]):
            if k.idnum == ref.idnum: del par["/Kids"][i]; break
        del a["/Parent"]; a[NameObject("/T")] = TextStringObject(name); a[NameObject("/FT")] = NameObject(ft)
        for key in ("/DA", "/Ff", "/Q"):
            if key in par: a[NameObject(key)] = par[key]
        acro["/Fields"].append(ref)
    ab = sheet["abilities"]; pb = sheet["prof"]; L = sheet["level"]
    prof_sk = set(sheet["skill_proficiencies"])
    def sk(s): return sign(mod(ab[SKILL_ABIL[s]]) + (pb if s in prof_sk else 0))
    save = lambda a: sign(mod(ab[a]) + (pb if a in sheet["save_proficiencies"] else 0))
    f = {"ClassLevel": f"Blood Hunter {L}" + (" (Pale Moon)" if L >= 3 else ""), "Background": sheet["background"],
         "CharacterName": sheet["name"], "Race ": sheet["race"], "XP": "Milestone", "Inspiration": sign(pb),
         "AC": str(sheet["ac"]), "Initiative": sign(mod(ab["dex"])), "Speed": "30 ft",
         "HPMax": str(sheet["hp"]["max"]), "HPCurrent": str(sheet["hp"]["current"]), "HPTemp": "0",
         "HDTotal": f"{L}d12", "HD": f"{L} of {L}", "Passive": str(10 + mod(ab["wis"]) + (pb if "perception" in prof_sk else 0))}
    for a, ST in zip(ABIL, ("STR", "DEX", "CON", "INT", "WIS", "CHA")):
        f[ST] = str(ab[a]); f["CHamod" if a == "cha" else ST + "mod" + (" " if a == "dex" else "")] = sign(mod(ab[a]))
    for i, a in enumerate(ABIL): f["SavingThrows" + ("" if i == 0 else str(i + 1))] = save(a)
    fields = {"athletics": "Athletics", "acrobatics": "Acrobatics", "sleight of hand": "SleightofHand", "stealth": "Stealth",
              "arcana": "Arcana", "history": "History", "investigation": "Investigation", "nature": "Nature",
              "religion": "Religion", "animal handling": "Animal Handling", "insight": "Insight", "medicine": "Medicine",
              "perception": "Perception", "survival": "Survival", "deception": "Deception", "intimidation": "Intimidation",
              "performance": "Performance", "persuasion": "Persuasion"}
    for s, fld in fields.items(): f[fld] = sk(s)
    f["PersonalityTraits "] = sheet["notes"][:160]; f["Bonds"] = "Okton's Order of the Banked Fire."; f["Flaws"] = "Carries the burn; the heat is always near."
    for i, atk in enumerate(sheet["attacks"][:3]):
        nm = ["Wpn Name", "Wpn Name 2", "Wpn Name 3"][i]; ab_ = ["Wpn1 AtkBonus", "Wpn2 AtkBonus ", "Wpn3 AtkBonus  "][i]; dm = ["Wpn1 Damage", "Wpn2 Damage ", "Wpn3 Damage "][i]
        f[nm] = atk["name"][:14]; f[ab_] = sign(atk["to_hit"]); f[dm] = f"{atk['damage'][0][0]}"[:12]
    f["AttacksSpellcasting"] = f"Rite Die {sheet['rite_die']}, Rite DC {sheet['rite_dc']}\nRites: {', '.join(sheet['rites'])}\nBeast: {sheet['beast']} ({BEASTS[sheet['beast']]}), Pale Moon from level 3\nRank: {sheet['rank']}"
    f["Features and Traits"] = "\n".join(sheet["features"])
    f["ProficienciesLang"] = "Armor: light, medium, shields\nWeapons: simple, martial\nTools: alchemist's supplies\nLanguages: Common\nSaves: CON, CHA"
    f["Equipment"] = ", ".join(sheet["inventory"]) + f", {sheet['gold']} gp\n" + sheet["notes"]
    w.update_page_form_field_values(pg, f, auto_regenerate=False)
    chk = ["ST Constitution", "ST Charisma"] + ["ChBx " + {"sleight of hand": "Sleight", "animal handling": "Animal"}.get(s, s.title()) for s in prof_sk]
    w.update_page_form_field_values(pg, {c: "/Yes" for c in chk}, auto_regenerate=False)
    small = {"Features and Traits": "/Helv 5 Tf 0 g", "Equipment": "/Helv 6 Tf 0 g", "AttacksSpellcasting": "/Helv 6 Tf 0 g",
             "ProficienciesLang": "/Helv 6.5 Tf 0 g", "PersonalityTraits ": "/Helv 6 Tf 0 g", "Bonds": "/Helv 6.5 Tf 0 g", "Flaws": "/Helv 6.5 Tf 0 g"}
    for ref in pg["/Annots"]:
        a = ref.get_object(); n = a.get("/T")
        if n is None and "/Parent" in a: n = a["/Parent"].get_object().get("/T")
        if n in small: a[NameObject("/DA")] = TextStringObject(small[n])
    w.set_need_appearances_writer(True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    out = PDF_DIR / f"{sheet['slug']}.pdf"; w.write(str(out)); return out


def main(argv):
    if len(argv) < 2: print(__doc__); return 1
    cmd = argv[1]
    if cmd == "all":
        for s in SPECS:
            sh = build(s); print("wrote", write_sheet(sh).name, "HP", sh["hp"]["max"], "AC", sh["ac"], fill_pdf(sh))
    elif cmd == "new":
        spec = json.loads(Path(argv[2]).read_text()); sh = build(spec); print("wrote", write_sheet(sh), fill_pdf(sh))
    elif cmd == "pdf":
        for p in sorted(BANKED.glob("*.json")):
            if argv[2] in ("all", p.stem): print(fill_pdf(json.loads(p.read_text())))
    elif cmd == "present":
        names = set() if argv[2] == "none" else set(argv[2].split(","))
        for p in sorted(BANKED.glob("*.json")):
            d = json.loads(p.read_text()); d["present"] = p.stem in names; p.write_text(json.dumps(d, indent=2))
        print("present:", ", ".join(sorted(names)) or "nobody")
    else: print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
