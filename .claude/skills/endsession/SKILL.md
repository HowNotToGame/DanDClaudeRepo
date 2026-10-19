---
name: endsession
description: End the current RoboDM play session — write the session summary, save key memories and quest updates, refresh the lore index, and commit and push the campaign so the next session (on any device) remembers everything.
argument-hint: "[anything to include in the summary]"
disable-model-invocation: true
allowed-tools: Agent, Bash(./dm:*), Bash(git status:*), Bash(git add:*), Bash(git commit:*), Bash(git push:*), Bash(git branch:*), Bash(git log:*), Read
---

Wrap up the session. Extra notes from the user: `$ARGUMENTS`

1. If combat is still running (`./dm combat status`), ask whether to end it; if yes, `./dm combat end`.
   Then finish the post-audit: `./dm audit status`. If anything is open, launch the rules-lawyer with
   `./dm audit pending` and apply its fixes. `session end` is refused until nothing is open.
1b. Companions in play (`./dm companion list`): launch each party companion's agent once, in parallel, with a
   one-paragraph summary of the session from their point of view, asking them to reflect: what they'll remember,
   how their attitudes and agenda changed. They record it themselves with `./dm companion remember|attitude|agenda`.
1c. The world: `./dm world` — resolve every open off-screen beat (`./dm world resolve <id> "…"` or `world drop`).
   Then `./dm world gossip`: for each suggestion that would really spread, record it as a rumour that those people
   now know (`./dm memory add "Rumor: …" --type rumor --known-by a,b`; rumours can be partial or wrong).
2. Review this session: `./dm session show` (the running log), `./dm session transcript` (everything said, captured
   automatically) and the conversation.
3. Save anything durable that isn't recorded yet — choices and consequences, new NPCs/places (lore entries),
   promises, debts, loot, relationships, secrets revealed (`./dm memory add …`, `./dm lore add/update …`).
   Update quests (`./dm quest add/update/done/fail`) and character sheets (HP, slots, items, gold, XP).
4. Write the summary — one solid paragraph (5–8 sentences): where the party went, what they did, key choices,
   how it ended, and the cliffhanger/next goal:
   `./dm session end --summary "…"`
5. `./dm lore index`.
6. Persist it: `git add campaigns/ context/` (this includes the session transcript) then commit with a message like
   `Session 4: The drowned temple (campaign: <slug>)` and `git push` to the current branch (set the upstream with
   `git push -u origin <branch>` the first time). If push fails, say so and show the error — don't claim it's saved.
   In cloud/mobile sessions, remind the user that new sessions start from the default branch: merge this branch
   (or start future sessions on it) so the campaign carries over.
7. Tell the players: a short recap with the in-game date (`./dm time`), XP/level-up news if any, public clocks
   that are ticking, and what to think about before next time.
