# The Banked (NPC Blood Hunter sheets)

Sheets for Okton's converts, built from their pre-conversion level. All are Order of the Pale Moon (the only order Okton can give).
Each JSON uses the same schema as `party/*.json` plus: `npc: true`, `present` (is this person with the party?), `rank` (Raw, Forged, Tempered),
`beast`, `rites`, `rite_die`, `rite_dc`.

- **Present flag:** `python3 tools/banked.py present hesby-bramble,gwenis-dunmore` (or `none`). Add present Banked to a fight as allies.
- **New convert:** write a spec JSON (see the `SPECS` list in `tools/banked.py`) and run `python3 tools/banked.py new spec.json`.
  The script builds the JSON and a filled PDF in `Books/Banked/` (needs `pypdf`).
- **Rebuild PDFs:** `python3 tools/banked.py pdf all`.
- Level 3 gains Pale Moon features (beast shown as 'latent' until then). Level 4 gains an ability score improvement.
