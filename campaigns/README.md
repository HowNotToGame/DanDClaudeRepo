# Campaigns

Each folder here is one campaign — RoboDM's long-term memory. It's plain JSON and Markdown, so you can read and
edit it by hand, and it's committed to git so every session (desktop, web, phone) picks up where you left off.

```
campaigns/<slug>/
  campaign.json            name, premise, setting, tone, table settings, session counter, last summary
  party/<pc>.json          character sheets (HP, slots, resources, attacks, inventory…)
  memory.jsonl             durable facts: events, choices, NPCs, rulings, secrets (one JSON object per line)
  quests.json              quest log
  journal/session-NNN.md   per-session running log + summary
  journal/combat-log.md    blow-by-blow record of every finished fight
  lore/<type>/<entry>.md   world lore (frontmatter + Markdown); lore/INDEX.md is the generated world bible
  lore/_sources/           reference material the lore was built from
  context/<block>.md       instruction blocks just for this campaign
  combat.json              the encounter in progress, if any
.active                    slug of the active campaign
```

Create one with `./dm campaign new "Name"` or `/campaign new` in Claude Code.
