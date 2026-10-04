---
name: find
description: Find and pull together anything in this folder — notes, projects, decisions, files, past sessions. Use when the user asks what did I write about X, where is that thing, did I already, what did we decide about, look that up, or search my notes.
argument-hint: [what to look for]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Grep, Glob
---

# Find

Answer the question they actually asked, from what is in the folder, in a way
they can check.

```bash
./os find "$ARGUMENTS" --limit 15
```

Ranked, covers everything including the archive, and forgives plurals and word
endings, so don't waste turns on those. It never searches for a different word on
its own: when a word matches nothing, it offers a near word as a command (`./os find bake`).
Run that only if it's plainly what they meant. When it comes back thin, their
synonyms and a `Grep` across `Notes/` and `Work/` for the distinctive words
usually find it.

Read the top few properly before answering. Snippets are for ranking, not for
quoting.

## Rules

- Cite **name · path** for everything you used.
- If the answer changed over time, say which version is current and when it
  changed. `## Decisions` and `## Log` are dated and append-only, so the last
  entry wins.
- Never invent a link between two notes. If they only share a word, say that.
- Don't say "I couldn't find anything" off one query. When it genuinely isn't
  there, say so and offer to write the question down.
- Three or more notes covering the same ground is worth mentioning — they
  probably want them merged.

## Done when

They have the answer and can check every part of it by opening one name you cited.
