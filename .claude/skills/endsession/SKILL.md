---
name: endsession
description: End the current RoboDM play session — write the session summary, save key memories and quest updates, refresh the lore index, and commit and push the campaign so the next session (on any device) remembers everything.
argument-hint: "[anything to include in the summary]"
disable-model-invocation: true
allowed-tools: Bash(./dm:*), Bash(git status:*), Bash(git add:*), Bash(git commit:*), Bash(git push:*), Bash(git branch:*), Bash(git log:*), Read
---

Wrap up the session. Extra notes from the user: `$ARGUMENTS`

1. If combat is still running (`./dm combat status`), ask whether to end it; if yes, `./dm combat end`.
2. Review this session: `./dm session show` (the running log) plus the conversation.
3. Save anything durable that isn't recorded yet — choices and consequences, new NPCs/places (lore entries),
   promises, debts, loot, relationships, secrets revealed (`./dm memory add …`, `./dm lore add/update …`).
   Update quests (`./dm quest add/update/done/fail`) and character sheets (HP, slots, items, gold, XP).
4. Write the summary — one solid paragraph (5–8 sentences): where the party went, what they did, key choices,
   how it ended, and the cliffhanger/next goal:
   `./dm session end --summary "…"`
5. `./dm lore index`.
6. Persist it: `git add campaigns/ context/` then commit with a message like
   `Session 4: The drowned temple (campaign: <slug>)` and `git push` to the current branch (set the upstream with
   `git push -u origin <branch>` the first time). If push fails, say so and show the error — don't claim it's saved.
   In cloud/mobile sessions, remind the user that new sessions start from the default branch: merge this branch
   (or start future sessions on it) so the campaign carries over.
7. Tell the players: a short recap, XP/level-up news if any, and what to think about before next time.
