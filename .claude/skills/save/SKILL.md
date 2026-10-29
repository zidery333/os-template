---
name: save
description: Write something down into this folder and file it — a thought, a link, a quote, a file, a half-formed idea. Use whenever the user says remember this, save this, note that, write this down, jot this down, don't let me forget, from now on, I prefer, always, stop doing (about them or their answers), or drops something mid-conversation that shouldn't be lost. Not a rule for one piece of work (/decide), and not "save my progress" (/wrapup).
argument-hint: [what to save]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Edit
---

# Save

Saving costs nothing. Deciding costs everything. Never ask where it should go.

```bash
./os save "<their words, plus context this chat or the folder gave>"
./os save <path>          # something already on disk
```

`./os save` works out what the thing is, which phase it starts in, and where it
goes — a sentence with a next action comes back **pushing**, one describing a
standard they keep comes back **holding**. It is named and filed by the time the
command returns, so never follow it with `./os new work` for the same thing.

## What a good save looks like

Their words, kept exactly. Add context only when this chat or the folder already
gave it: what it's about, the source, a date their "Friday" means. Never a guess
at where it came from or why, like "action from a meeting": every later chat
reads the note as true. Five things in one message is five saves, not one.

## A file

`./os save <path>` puts a PDF or a photo in `Notes/` with a `<name>.card.md`
beside it, and search reads the card, not the file. So open the file, then write
one sentence of what it is and 3–5 tags into its card, like
`tags: [lease, flat, landlord]`. If a camera or scanner named it (`scan-0012`,
`img-4031`), `./os rename <name> "<what it is>"` renames the file and its card.

## About them

"Call me Sam", "keep answers short", "I'm vegetarian": a fact about them, or
about how they want answers, is not a note of its own. It is one line in their
About me note, marked (said). `./os save` puts it there itself when their words
start with "call me" or "keep answers", or start with "from now on" or "I
prefer" and go on about them or their answers. It starts the note if there is
none, the way `./os new note "About me" --domain personal` would. For anything
else about them, put "About me:" in front:

```bash
./os save "About me: <their words>"
```

"Always…" and "stop doing…" about how they want things done go straight into
About me the same way, in their words, with no question first. A rule for one piece of work ("from
now on we invoice on Fridays") is not about them: it is a decision for that
work, with `./os decide <name> "<their words>"`.

Only what they said goes in About me. Something you noticed about them is a
question to ask, not a line to write.

## Rules

- Never ask a clarifying question first. A rough save beats a lost thought.
- Keep their exact words for anything quoted. Only paraphrase the context you add.
- Don't announce a plan to create a project — the save already did it.
- Reply with the paragraph only: what you wrote down and where it went. Say "I
  wrote that down — it's in Notes as how-to-run-a-retro", not "captured" and not
  the folder mechanics.

## Done when

They've heard one sentence confirming it's safe.
