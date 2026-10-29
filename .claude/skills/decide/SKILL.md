---
name: decide
description: Write a settled decision into the item it belongs to, with what it rules out. Use when the user says let's go with, we decided, final answer, settled, lock that in, or states a choice between options as done, including "from now on" about one piece of work. Not for asking what was decided: that is /find.
argument-hint: [name] [the decision]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*)
---

# Decide

A decision that lives in the chat is lost by Friday. One that lives in the item
is still there a year later, under the item's own name.

```bash
./os decide <name> "<the decision> — rules out <what it rules out>"
```

With nothing said or on the table that it rules out: `./os decide <name> "<the decision>"`.

The decision goes in the item's `## Decisions`, append-only, dated. If it belongs
to no item, `./os save "<the decision, and what it rules out>"` files it as a note.

## What a good decision line looks like

- Their words for the choice, kept exactly.
- What it rules out, in one clause, when they said it or the other option was
  on the table in this chat. Otherwise leave the clause off. A made-up
  consequence is a false line in their record, and worse than none.
- Why, only if they said why. Never invent a reason.

## Rules

- Don't ask which item when one is plainly being discussed: write it there.
  If two fit, ask once which; never write it into one they didn't name.
- Never rewrite an earlier decision. A reversal is a new line saying what it
  replaces.
- Reply with the paragraph only: what was written and where.

## Done when

The line is in `## Decisions` and `./os show <name>` lists it under decided.
