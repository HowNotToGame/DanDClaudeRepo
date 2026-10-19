# Using RoboDM in Claude chat (claude.ai)

Claude Code is the primary way to run RoboDM (real files, subagents, hooks). For claude.ai there are two options.

## Option A — Upload the RoboDM skill (recommended)

Needs code execution enabled in claude.ai (Settings → Capabilities).

```bash
./dm rules fetch                                   # once, to have local data to bundle
python3 tools/build_chat_skill.py --with-data      # → dist/robodm-skill.zip (~2 MB, core books); `python` on Windows
# or without rules data (smaller; Claude will try `rules fetch` itself if the sandbox has network):
python3 tools/build_chat_skill.py
```

Upload `dist/robodm-skill.zip` in claude.ai → Settings → Capabilities → Skills. Then just say "let's play D&D".

- **Memory between chats:** the sandbox is wiped when a chat ends. RoboDM saves the campaign with
  `dm export` and offers you the `.robodm.json` file — download it, and upload it at the start of the next chat.
  Moving between Claude Code and claude.ai works the same way (`./dm import file.robodm.json`).
- **Rules checking:** there are no subagents in chat, so the skill does an explicit self-audit pass instead.
- `--sources PHB,MM,DMG,XGE,TCE,VGM,MTF` (or `ALL`) controls which books' spells/monsters are bundled.

## Option B — Claude Project, no code

Paste `PROJECT_INSTRUCTIONS.md` into a Project's custom instructions. Put your campaign digest
(`./dm export --markdown`) and setting documents into the Project knowledge. Claude tracks combat in a compact
status block and asks players to roll physical dice. Less automation, works anywhere.
