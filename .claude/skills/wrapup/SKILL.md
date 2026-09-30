---
name: wrapup
description: Close out a working session — write down what happened, update the projects touched, file anything loose, keep a checkpoint, and say when it's safe to close. Use when the user says done for now, wrap up, end session, save my progress, that's enough for today, can I close this, or before they walk away from real work.
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

Last, keep a checkpoint: `./os checkpoint "<what changed today, in a few words>"`.
It keeps every file as it is now in the folder's own history, so an edit made by
hand after this can be taken back. `./os undo` can't do that: it only reverses
what `./os` did. Footage in `Work/Content` is left out.

- **It kept one, started one, or nothing had changed** — everything is saved.
- **No git on this computer** — the files are still saved; say in one bullet that
  hand edits can't be taken back until git is installed, with the line it gave.
- **A history `./os` didn't start** — it's theirs, so offer to save it, in one
  line. On a yes: `git add -A && git commit -m "<what changed today>"`.
- **Anything else** (the history came with the download, or belongs to a bigger
  folder around this one) — don't run git yourself. Say its line in one bullet.

Report in the usual shape: a paragraph on what moved, bullets for what was
decided and what's next, question bullets for what's still open. End the
paragraph with "It's all saved, so it's safe to close." — but only if it is: if
something couldn't be written down or filed, say that instead.

## Rules

- Never rewrite an old log entry or decision. Append a correction with today's date.
- Never mark something done that they didn't say was done.
- A decision with no stated consequence isn't a decision. Write what it rules out.
- Nothing meaningful happened? One line saying so. Don't manufacture progress.

## Done when

Everything touched has a dated line in its `## Log`, and everything they pushed on
has a current next action — held work doesn't need one, that's what holding means.
A checkpoint was kept, or the reply says why not, and says whether it's safe to close.
