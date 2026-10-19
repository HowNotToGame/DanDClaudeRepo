---
name: remember
description: Save something to RoboDM's long-term campaign memory, or recall what the campaign knows about a topic.
argument-hint: "<fact to remember>  |  ? <question about the past>"
disable-model-invocation: true
allowed-tools: Bash(./dm:*)
---

Input: `$ARGUMENTS`

- If it starts with `?` or reads like a question ("what did the mayor say about the mine?"):
  run `./dm recall "<phrasing 1>" "<phrasing 2>" "<phrasing 3>"` (names, synonyms, what it was about; it searches
  memories, journals, transcripts, lore and sources at once), read the most relevant journal/lore files if needed,
  and answer from what's recorded. Say plainly if nothing is recorded — never invent past events.
- Otherwise save it: pick the best type (event, choice, npc, location, item, quest, lore, rule, secret,
  relationship, other), 1–4 lowercase tags, and run
  `./dm memory add "<fact, written as a clear standalone sentence>" --type <type> --tags <tags>`.
  Add `--important` if the user says it's crucial or it's a safety line/veil.
  If it's really a quest, use `./dm quest add`; if it defines a new NPC/place, also create a lore entry.
- Confirm in one line.
