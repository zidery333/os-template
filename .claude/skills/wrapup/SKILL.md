---
name: wrapup
description: Close out a working session — write down what happened, update the projects touched, file anything loose. Use when the user says done for now, wrap up, end session, save my progress, that's enough for today, or before they walk away from real work.
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Edit, Write
---

# Wrap up

The session isn't over until the folder knows what happened — **written into the
thing it happened to**, not into a diary. That is where it is still findable a
year from now, by the name of the thing itself.

## What goes where

In each item's `README.md` that today touched:

- one dated line appended to `## Log` — what was worked on, what's blocked and on what
- anything actually decided appended to `## Decisions`, with what it rules out
  (`./os decide <name> "<choice> — rules out <the other>"`)
- `## Next action` (pushing) or `## Where it stands` (holding) **overwritten**
- ran out of next actions but isn't over? `./os hold <name>` — the honest ending for
  most sessions, and far more common than closing something

Facts only: no summary of the conversation, no adjectives.

Anything decided that belongs to no one item, and any loose end raised and not
resolved, goes to `./os save "<the decision, and what it rules out>"` — it gets
filed as a note under its own name.

If this folder keeps its own end-of-session steps in `wrapup-extra.md`,
inside `.claude/`, do what it says.

Then settle it: `./os sort`. Anything about the folder itself that got in the way
today and isn't already written down: `./os snag "<what happened>"`.

Report in the usual shape: a paragraph on what moved, bullets for what was
decided and what's next, question bullets for what's still open.

## Rules

- Never rewrite an old log entry or decision. Append a correction with today's date.
- Never mark something done that they didn't say was done.
- A decision with no stated consequence isn't a decision. Write what it rules out.
- Nothing meaningful happened? One line saying so. Don't manufacture progress.

## Done when

Everything touched has a dated line in its `## Log`, and everything they pushed on
has a current next action — held work doesn't need one, that's what holding means.
