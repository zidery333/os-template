---
name: catchup
description: Catch up on everything in this folder — what's moving, what's gone quiet, what's waiting, what's broken. Use at the start of a session, or when the user asks where things stand, what should I work on, what's going on, catch me up, or what did I leave.
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read
---

# Catch up

Give them what they actually want at 9am: what's moving, what's gone quiet,
what's waiting, and what's broken — in the usual shape, no tables, no preamble.

```bash
./os status
./os tidy --json
./os update --check
```

The last one prints a line only when a newer version of this folder's program
is out. If it does, say so once, at the end, with the command: `./os update`.

Read the projects' `## Next action` lines for anything you're going to mention by
name. Say what to do first, and commit to an answer — that last sentence is the
whole point of the skill.

## Rules

- Never list everything. More than five in a section? Name three, count the rest.
- Never pad. Nothing quiet and nothing broken is a three-line answer, and that is
  the right length.
- Use the names so they can act on it: "open Q3 OKR Review".
- Say plainly when something should just be held or closed out. Quiet work
  usually wants `./os hold`, not `./os close`.
- No jargon and no health score. "Everything's in good shape."

## Done when

They know what to do next without asking a follow-up question.
