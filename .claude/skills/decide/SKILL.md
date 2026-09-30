---
name: decide
description: Write a settled decision into the item it belongs to, with what it rules out. Use when the user says let's go with, we decided, final answer, settled, lock that in, or states a choice between options as done.
argument-hint: [name] [the decision]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*)
---

# Decide

A decision that lives in the chat is lost by Friday. One that lives in the item
is still there a year later, under the item's own name.

```bash
./os decide <name> "<the decision> — rules out <what it rules out>"
```

The decision goes in the item's `## Decisions`, append-only, dated. If it belongs
to no item, `./os save "<the decision, and what it rules out>"` files it as a note.

## What a good decision line looks like

- Their words for the choice, kept exactly.
- What it rules out, in one clause. A decision with no consequence isn't one.
- Why, only if they said why. Never invent a reason.

## Rules

- Never ask which item. Pick the one being discussed; if two fit, write it to
  both and say so.
- Never rewrite an earlier decision. A reversal is a new line saying what it
  replaces.
- Reply with the paragraph only: what was written and where.

## Done when

The line is in `## Decisions` and `./os show <name>` lists it under decided.
