---
name: save
description: Write something down into this folder and file it — a thought, a link, a quote, a file, a half-formed idea. Use whenever the user says remember this, save this, note that, write this down, jot this down, don't let me forget, from now on, I prefer, always, stop doing, or drops something mid-conversation that shouldn't be lost.
argument-hint: [what to save]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Edit
---

# Save

Saving costs nothing. Deciding costs everything. Never ask where it should go.

```bash
./os save "<their words, plus the context they didn't say>"
./os save <path>          # something already on disk
```

`./os save` works out what the thing is, which phase it starts in, and where it
goes — a sentence with a next action comes back **pushing**, one describing a
standard they keep comes back **holding**. It is named and filed by the time the
command returns, so never follow it with `./os new work` for the same thing.

## What a good save looks like

Their words, kept exactly, plus the context future-them will need and didn't say:
what it's about, why it came up, the source if there is one. Five things in one
message is five saves, not one.

## A file

`./os save <path>` puts a PDF or a photo in `Notes/` with a `<name>.card.md`
beside it, and search reads the card, not the file. So open the file, then write
one sentence of what it is and 3–5 tags into its card, like
`tags: [lease, flat, landlord]`. If a camera or scanner named it (`scan-0012`,
`img-4031`), `./os rename <name> "<what it is>"` renames the file and its card.

## About them

"Call me Sam", "keep answers short", "I'm vegetarian": a fact about them, or
about how they want answers, is not a note of its own. It is one line in their
About me note — `./os open "About me"` says where that is, and
`./os new note "About me" --domain personal` makes one if there is none. Every
session starts with its first lines, so what matters most goes near the top.

"From now on…", "I prefer…", "always…" and "stop doing…" are the same thing:
how they want things done. Each goes straight into About me as one line in their
words, with no question first. It is not a note, a task or a settings change.

## Rules

- Never ask a clarifying question first. A rough save beats a lost thought.
- Keep their exact words for anything quoted. Only paraphrase the context you add.
- Don't announce a plan to create a project — the save already did it.
- Reply with the paragraph only: what you wrote down and where it went. Say "I
  wrote that down — it's in Notes as how-to-run-a-retro", not "captured" and not
  the folder mechanics.

## Done when

They've heard one sentence confirming it's safe.
