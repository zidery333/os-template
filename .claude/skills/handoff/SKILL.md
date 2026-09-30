---
name: handoff
description: Write the one note that lets another person or AI pick a project up cold. Use when the user says hand this off, brief someone, write it up for, get a helper on this, or before sending a project to @builder.
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

- `## What good looks like` — only if empty. Never rewrite one they wrote.
- `## Where it stands` — three sentences, facts only, as of today.
- Pushing: `## Next action` — one action, concrete enough to start in five minutes.
  Holding: no next action, by definition. `## Keeps coming back` instead — what
  recurs and how often, so whoever picks it up keeps it level and nothing more.
- `## Open questions` — what is undecided, and who decides it. One that has been
  answered comes out, and the answer goes in with `./os decide`.

`## Decisions` and `## Log` stay as they are; the reader gets them for free.

## Rules

- Never summarise the conversation. Summarise the project.
- Anything you had to ask them to write this goes in the README, not the chat.
- If the next action is really three, write the first and list the other two
  under open questions.
- When this is for `@builder`, hand it the name only. The README is the brief.

## Done when

`./os show <name>` reads as a complete brief, and you would be happy to be the
one picking it up.
