---
name: handoff
description: Write the one note that lets another person or AI pick a project up cold. Use when the user says hand this off, brief someone, write it up for, or before sending a project to @builder.
argument-hint: [name]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Edit
---

# Handoff

The test of a handoff: someone who has never seen the project reads only this
and knows what to do first, what not to touch, and how they will know it's done.

```bash
./os show <name>
./os last
```

Then read the item's `README.md`: everything above `## Log`, and the newest ten
lines of `## Log`. Search the older Log lines when you need one; they can run
to hundreds of kilobytes.

## Do this

Write or refresh these sections in the item's README, nothing else:

- `## What good looks like` — theirs to write, never yours. If it's empty, ask
  them for it in one line and write their answer; no answer, and it goes
  under `## Open questions` as "What does good look like here?"
- `## Where it stands` — up to three sentences, as of today. Each one is
  something they said or the files show: a Log line, a decision, a ticked box.
  Nothing worked out from silence, like "nothing has been sent yet"; if it
  matters and nobody said, it goes under `## Open questions`.
- Pushing: `## Next action` — one action, concrete enough to start in five minutes,
  that they said or that plainly follows from what they said. None? Ask once;
  no answer, and it goes under `## Open questions` instead of a guess, with
  `## Next action` left empty: no box saying there's no step.
  Holding: no next action, by definition. `## Keeps coming back` instead — what
  recurs and how often, so whoever picks it up keeps it level and nothing more.
  Only what they said or the files show. How often unknown? Ask once; no answer,
  and it goes under `## Open questions`, never a guessed "weekly".
- `## Open questions` — what is undecided, and who decides it. One that has been
  answered comes out, and the answer goes in with `./os decide`.

`## Decisions` and `## Log` stay as they are; the reader gets them for free.
Who it's handed to goes in `## Where it stands`, not in Decisions.

## Rules

- Never summarise the conversation. Summarise the project.
- Anything you had to ask them to write this goes in the README, not the chat.
- If the next action is really three, write the first and list the other two
  under open questions.
- When this is for `@builder`, hand it the name only. The README is the brief.

## Done when

`./os show <name>` reads as a complete brief, and you would be happy to be the
one picking it up.
