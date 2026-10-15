---
name: recap
description: Give a "previously on…" recap of the RoboDM campaign — last sessions, party status, open quests, unresolved threads.
argument-hint: "[short | long | session N]"
disable-model-invocation: true
allowed-tools: Bash(./dm:*), Read
---

Make a recap (`$ARGUMENTS`; default: short).

1. `./dm memory brief` (and `./dm session show N` if a specific session was asked for).
2. Write it like a TV "previously on…": 3–5 sentences for short, a few paragraphs for long. Cover the last
   session's key events, important choices and their consequences, the party's condition (HP, resources, notable
   items), and 2–3 open threads or quests.
3. Only state what's recorded. No new facts.
4. If a game is running, finish with the current scene and "What do you do?"
