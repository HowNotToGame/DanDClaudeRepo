---
name: rules
description: Answer a D&D 5e (2014) rules question or check a ruling against the local 5etools data, with citations.
argument-hint: "<rules question or ruling to check>"
context: fork
agent: rules-lawyer
---

Rules question or ruling to check: `$ARGUMENTS`

Answer it using the local 5etools 2014 data, following your rules-lawyer procedure: look up every spell,
condition, feature, monster or rule involved (`./dm rules show …`, `./dm rules search … --text`), check the
campaign's house-rule blocks (`./dm context render`), and reply with a verdict or answer, the exact rule text
that decides it, citations (book + page), and what to change if a ruling was wrong.
