---
name: snag
description: >-
  Writes down something wrong with this folder's own machinery — a command
  that did something surprising, a rule that made no sense, a step that
  should have happened by itself — in .claude/snags.md, counting repeats.
  With nothing after it, shows the list ready to send to the template's
  author. Also used by Claude, without asking, the moment the machinery gets
  in its way.
when_to_use: >-
  '/snag <what went wrong>', '/snag' on its own, 'that's a bug in the
  folder', 'the setup got that wrong', 'send my snags', 'what snags are
  there'. Also whenever a hook, skill or rule here gets in the way of the job.
argument-hint: "[what went wrong — leave out to see the list]"
---
Snag: $ARGUMENTS

A snag is something wrong with **this folder's machinery**: a skill, a hook,
a rule in `CLAUDE.md`, a README, the upgrade. Never the person's own work. A
note they wrote badly is not a snag; a check that nagged about a note they
wrote well is.

Repeats are the point. The same snag six times is a different job from one
seen once, so the count is what the author needs most.

## With words after it: write it down

1. Put the snag in one plain sentence about the machinery: what happened, and
   what should have happened. "`/tidy-up` flagged a heading the notes README
   says to use in every subject."
2. **Leave out everything personal.** No names, no subjects, no project
   names, nothing from their notes. Say "a subject file", not which one. The
   list is meant to be sent to someone else.
3. Read `.claude/snags.md`. If it isn't there, make it with the top part
   below.
4. If a line already says the same thing, even in other words, don't add a
   second one. Add 1 to its count and set its last-seen date to today.
   Otherwise add a new line at the bottom, first and last seen today, count 1.
5. Say one line: "Noted as a snag." Then carry on with what you were doing.

Each line looks like this:

```
- first 2026-09-27 · last 2026-10-02 · 3 times · `/tidy-up` flagged a heading the notes README says to use.
```

The top of a new file:

```
# Snags

What went wrong with this folder's machinery, not with anything of yours.
Written by /snag. Type /snag to see it ready to send.

```

## Without being asked

When a hook, skill or rule here gets in your way — it said something wrong,
did something surprising, made you do by hand what it should have done —
write the snag down as above, straight away, without asking. One line to the
person at most, then back to the job. Don't stop their work to talk about it.

## With nothing after it: show the list

1. Read `.claude/snags.md`. No file, or no lines: say "No snags yet." and stop.
2. Show every line, most repeated first, inside one code block so it copies
   cleanly. Above the lines, one heading line: `Snags from my OS folder`.
3. Under it, say: "Read it before you send it, in case something personal
   slipped in. Then paste it to **zidery333** on Discord."

Don't change the file while showing it.

## Never

- Put their own content in a snag: names, note text, project details.
- Delete lines. Once they've sent the list, they can empty the file
  themselves if they want to.
- Write a snag about their work, or about Claude Code itself.
