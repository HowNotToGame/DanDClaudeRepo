"""Random generators so improvised details come from real dice, not habit.

Language models drift toward the same few fantasy names; rolling on tables
keeps NPCs, taverns and weather varied and surprising.
"""

from __future__ import annotations

from robodm import dice

NAME_PARTS = {
    "human": {
        "first": ["Al", "Bran", "Cor", "Dar", "Ed", "Fen", "Gar", "Hal", "Is", "Jor", "Kas", "Lor", "Mar", "Nes", "Os",
                  "Per", "Quin", "Ros", "Sam", "Tam", "Ul", "Ves", "Wil", "Yar", "Ash", "Bet", "Cass", "Dor", "Em", "Gwen",
                  "Hes", "Ivo", "Jun", "Kit", "Lis", "Mae", "Nor", "Pell", "Rhea", "Sel", "Tor", "Una", "Wyn"],
        "end": ["ric", "an", "en", "wyn", "da", "ton", "ard", "ley", "a", "ia", "ette", "mund", "win", "ford", "ra",
                "ny", "lo", "ius", "ine", "elle", "ot", "son", "by", "et", "is"],
        "family": ["Ashdown", "Barrowe", "Coldwell", "Dunmore", "Fairweather", "Greaves", "Hollis", "Ironside", "Kettle",
                   "Larkspur", "Marsh", "Northcott", "Pike", "Quarry", "Redfern", "Saltmarsh", "Thatcher", "Underhill",
                   "Vane", "Whitlock", "Yarrow", "Bramble", "Cobb", "Drury", "Furrow", "Hask", "Lowe", "Mercer", "Rook"],
    },
    "elf": {
        "first": ["Ae", "Cael", "Ely", "Fae", "Gal", "Ith", "Lia", "Mir", "Nae", "Quel", "Rhi", "Syl", "Tha", "Vae",
                  "Yl", "Ari", "Bel", "Ciri", "Ela", "Ien", "Lue", "Ner", "Ola", "Sae", "Tael"],
        "end": ["ndril", "thar", "wyn", "riel", "lian", "ras", "nor", "sel", "vain", "ael", "ith", "ara", "iel",
                "ynn", "orin", "essa", "anis", "ros", "lith", "ion"],
        "family": ["Amakiir", "Galanodel", "Holimion", "Liadon", "Meliamne", "Nailo", "Siannodel", "Xiloscient",
                   "Ilphelkiir", "Brightleaf", "Moonwhisper", "Starbough", "Duskwater", "Silverfrond"],
    },
    "dwarf": {
        "first": ["Ad", "Bar", "Bru", "Dag", "Dor", "Eb", "Far", "Gar", "Har", "Kil", "Mor", "Orsik", "Rur", "Thor",
                  "Ulf", "Vond", "Am", "Bard", "Dis", "Eld", "Gunn", "Hlin", "Kath", "Risw", "Torb", "Vist"],
        "end": ["rik", "din", "nor", "gar", "grim", "in", "ak", "unn", "ra", "dra", "hild", "a", "ryn", "ek", "im",
                "ottir", "bek", "drum"],
        "family": ["Balderk", "Battlehammer", "Brawnanvil", "Dankil", "Fireforge", "Frostbeard", "Gorunn", "Holderhek",
                   "Ironfist", "Loderr", "Lutgehr", "Rumnaheim", "Strakeln", "Torunn", "Ungart", "Coppervein",
                   "Deepdelver", "Stonejaw"],
    },
    "halfling": {
        "first": ["Al", "An", "Cad", "Cor", "El", "Gar", "Lyle", "Mil", "Os", "Per", "Ros", "Wel", "Bree", "Call",
                  "Kith", "Lav", "Mer", "Nedd", "Pae", "Ser", "Ver", "Tobb", "Pip", "Fen"],
        "end": ["ton", "ric", "do", "o", "ie", "y", "a", "ina", "ry", "ble", "kin", "wick", "an", "ly"],
        "family": ["Brushgather", "Goodbarrel", "Greenbottle", "High-hill", "Hilltopple", "Leagallow", "Tealeaf",
                   "Thorngage", "Tosscobble", "Underbough", "Applebrook", "Puddlefoot", "Honeypot", "Quickstep"],
    },
    "orc": {
        "first": ["Dench", "Feng", "Gell", "Hen", "Hol", "Imsh", "Keth", "Krusk", "Mhurr", "Ront", "Shump", "Thokk",
                  "Baggi", "Emen", "Engong", "Kansif", "Myev", "Neega", "Ovak", "Ownka", "Shautha", "Sutha", "Vola",
                  "Yev", "Grul", "Ursk"],
        "end": ["", "", "a", "ak", "ug", "ash", "uk", "ra", "og", "ish"],
        "family": ["of the Broken Tusk", "Skullsplitter", "Bloodfang", "Ashmaw", "of Clan Grommash", "Ironhide",
                   "Stonebiter", "the Unbowed", "Redhand", "Gravelthroat"],
    },
    "gnome": {
        "first": ["Al", "Bod", "Bod", "Dim", "Eld", "Fon", "Glim", "Ger", "Orr", "Roon", "Seeb", "Wren", "Bimp",
                  "Caram", "Ella", "Lill", "Lor", "Nis", "Nyx", "Orla", "Roy", "Shamil", "Tana", "Zan"],
        "end": ["ston", "dle", "ble", "kin", "ick", "ywin", "o", "ra", "na", "ix", "bert", "wick", "ella", "pip"],
        "family": ["Beren", "Daergel", "Folkor", "Garrick", "Nackle", "Murnig", "Ningel", "Raulnor", "Scheppen",
                   "Timbers", "Turen", "Cogwhistle", "Fizzlebang", "Sprocket", "Gearloose"],
    },
    "tiefling": {
        "first": ["Ak", "Amn", "Bar", "Dam", "Ek", "Ia", "Kal", "Lev", "Mor", "Pel", "Ska", "Za", "Bry", "Cri",
                  "Kall", "Leu", "Mak", "Nem", "Orian", "Rie", "Ner", "Vel"],
        "end": ["menos", "akos", "ai", "aos", "emon", "ados", "ista", "ea", "is", "ith", "ona", "ra", "ix", "eus"],
        "family": ["", "", "Ash", "Despair", "Hope", "Ideal", "Mockery", "Open", "Quest", "Reverence", "Sorrow",
                   "Temerity", "Torment", "Weary"],
    },
    "dragonborn": {
        "first": ["Ar", "Bal", "Bhar", "Dona", "Ghe", "Hes", "Kri", "Med", "Mehen", "Nad", "Pand", "Patr", "Rhog",
                  "Shamash", "Shed", "Tar", "Akra", "Bir", "Dar", "Far", "Harann", "Hav", "Jhe", "Kava", "Kor",
                  "Mish", "Nala", "Perr", "Rai", "Sora", "Sur", "Thav", "Uad"],
        "end": ["jhan", "asar", "esh", "aar", "sh", "ax", "inn", "ar", "ash", "ian", "ra", "thi", "ann", "ira", "ix"],
        "family": ["Clethtinthiallor", "Daardendrian", "Delmirev", "Drachedandion", "Fenkenkabradon", "Kepeshkmolik",
                   "Kerrhylon", "Kimbatuul", "Linxakasendalor", "Myastan", "Nemmonis", "Norixius", "Ophinshtalajiir",
                   "Prexijandilin", "Shestendeliath", "Turnuroth", "Verthisathurgiesh", "Yarjerit"],
    },
}
NAME_PARTS["half-elf"] = {k: NAME_PARTS["human"][k] + NAME_PARTS["elf"][k] for k in ("first", "end", "family")}
NAME_PARTS["half-orc"] = {k: NAME_PARTS["human"][k] + NAME_PARTS["orc"][k] for k in ("first", "end", "family")}

# Names language models overuse. Rerolled if generated.
OVERUSED = {"elara", "lyra", "kael", "thorne", "seraphina", "aria", "zephyr", "elowen", "aldric", "mira", "lira",
            "kaelen", "theron", "vex", "nyx", "sylas", "elric", "garrick", "finn", "rowan"}

OCCUPATIONS = ["blacksmith", "innkeeper", "fisher", "temple acolyte", "caravan guard", "herbalist", "tax collector",
               "rat-catcher", "cartographer", "brewer", "ferry operator", "retired soldier", "street urchin",
               "minor noble", "miller", "gravedigger", "scribe", "hunter", "fence for stolen goods", "midwife",
               "bard", "beekeeper", "shipwright", "watch sergeant", "alchemist's apprentice", "pilgrim", "tanner",
               "moneylender", "stable hand", "wandering tinker", "librarian", "smuggler", "chandler", "weaver",
               "ex-cultist", "toll keeper", "dockworker", "falconer", "glassblower", "village elder"]
TRAITS = ["speaks in a whisper", "laughs at their own jokes", "constantly eating", "suspicious of magic",
          "overly formal", "chews on a pipe stem", "collects odd trinkets", "refers to self in third person",
          "nervous, fidgety hands", "booming voice", "quotes scripture", "terrible at lying", "flirtatious",
          "blunt to the point of rudeness", "obsessed with cleanliness", "always cold", "superstitious",
          "a little deaf", "exaggerates everything", "keeps a pet rat", "hums old war songs", "never makes eye contact",
          "squints constantly", "gossips relentlessly", "deeply polite", "cracks knuckles", "missing a finger",
          "wears too much perfume", "has a lisp", "taps rhythms on tables"]
MOTIVATIONS = ["pay off a crushing debt", "protect a younger sibling", "find a missing spouse", "get revenge on a noble",
               "earn enough to leave town", "prove their worth to a parent", "hide a magical talent",
               "atone for a past crime", "win back a lost love", "keep the business afloat", "uncover the truth about a death",
               "climb the social ladder", "serve their god faithfully", "be left alone", "find a legendary relic",
               "keep a dangerous secret buried", "become famous", "break a family curse"]
SECRETS = ["is a spy for a rival faction", "owes money to the thieves' guild", "killed someone years ago",
           "is secretly noble-born", "worships a forbidden god", "is a lycanthrope", "stole their identity",
           "knows where a body is buried", "is being blackmailed", "has a hidden child", "is a deserter",
           "is in love with their employer", "forged their credentials", "sold out a friend", "has no secret at all — "
           "and is suspicious of everyone who does"]
TAVERN_ADJ = ["Drunken", "Prancing", "Rusty", "Sleeping", "Golden", "Crooked", "Laughing", "Silver", "Wandering",
              "Salty", "Broken", "Jolly", "Howling", "Black", "Velvet", "Gilded", "Thirsty", "Muddy", "Lucky", "Weeping",
              "Sly", "Blind", "Bloody", "Copper", "Leaky"]
TAVERN_NOUN = ["Dragon", "Pony", "Anchor", "Giant", "Goose", "Lantern", "Tankard", "Boar", "Wyvern", "Mermaid",
               "Hound", "Owl", "Kettle", "Crown", "Barrel", "Goblin", "Stag", "Raven", "Mule", "Wheel", "Badger",
               "Thistle", "Cockatrice", "Oak", "Fiddle"]
TAVERN_QUIRK = ["the ale is watered down", "a famous bard plays here on moonless nights", "the owner is a retired adventurer",
                "there's a cursed painting above the hearth", "fights are settled by arm-wrestling", "a ghost orders a drink nightly",
                "the cook is a halfling with a fiery temper", "rooms are suspiciously cheap", "a notice board is full of bounties",
                "the house special is eel pie", "the floor slopes badly", "locals bet on rat races in the cellar"]
WEATHER = {
    "temperate": ["clear and mild", "overcast", "light drizzle", "steady rain", "fog until midday", "windy",
                  "thunderstorm by evening", "humid and still", "crisp and bright", "heavy downpour"],
    "cold": ["light snow", "bitter wind", "freezing fog", "clear and frigid", "blizzard (heavy obscurement, difficult terrain)",
             "sleet", "overcast and grey", "hoarfrost on everything"],
    "hot": ["scorching sun", "dry wind", "sandstorm on the horizon", "sweltering and still", "brief monsoon downpour",
            "heat haze", "dust devils", "humid, buzzing with insects"],
}
HOOKS = ["A child goes missing and the only witness is a talking crow.",
         "A merchant pays triple to escort a locked crate that hums at night.",
         "The town well has started whispering names.",
         "A dying knight presses a signet ring into a PC's hand and says 'Don't let him wear it.'",
         "Two rival guilds both hire the party for the same job.",
         "A map tattooed on a corpse leads somewhere that shouldn't exist.",
         "Every bell in the city rang at midnight; no one rang them.",
         "A noble's wedding needs bodyguards — the bride insists she's being replaced by a doppelganger.",
         "Livestock are found drained of blood in perfect circles.",
         "A bounty poster appears with one of the PCs' faces on it.",
         "A ship arrives in port with no crew and a full hold of gold.",
         "An old friend sends a letter dated ten years in the future.",
         "The local temple's relic was stolen and replaced with an exact fake.",
         "A dragon offers a contract instead of a threat.",
         "Miners broke into a sealed vault and stopped coming up."]


def _pick(seq):
    return seq[dice.RNG.randrange(len(seq))]


def name(culture: str = "human", with_family: bool = True) -> str:
    parts = NAME_PARTS.get(culture.lower(), NAME_PARTS["human"])
    for _ in range(20):
        first = _pick(parts["first"]) + _pick(parts["end"])
        if first.lower() not in OVERUSED:
            break
    first = first[0].upper() + first[1:]
    if with_family:
        fam = _pick(parts["family"])
        if fam:
            return f"{first} {fam}"
    return first


def npc(culture: str | None = None) -> dict:
    culture = culture or _pick(["human"] * 5 + ["elf", "dwarf", "halfling", "gnome", "half-elf", "half-orc", "tiefling",
                                             "dragonborn", "orc"])
    return {
        "name": name(culture),
        "race": culture,
        "occupation": _pick(OCCUPATIONS),
        "trait": _pick(TRAITS),
        "motivation": _pick(MOTIVATIONS),
        "secret": _pick(SECRETS),
    }


def tavern() -> dict:
    style = dice.RNG.randrange(3)
    adj, noun = _pick(TAVERN_ADJ), _pick(TAVERN_NOUN)
    if style == 0:
        title = f"The {adj} {noun}"
    elif style == 1:
        title = f"The {noun} and {_pick(TAVERN_NOUN)}"
    else:
        title = f"{name('human', with_family=False)}'s {noun}"
    return {"name": title, "quirk": _pick(TAVERN_QUIRK), "keeper": npc()["name"]}


def weather(climate: str = "temperate") -> str:
    return _pick(WEATHER.get(climate, WEATHER["temperate"]))


def hook() -> str:
    return _pick(HOOKS)


def pick_from(options: list[str]) -> str:
    return _pick(options)
