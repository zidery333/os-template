---
name: new-subject
description: >-
  Starts a new subject in notes/, with its matching sources folder — whether
  the user means to act on it or is simply interested in it. Use before
  saving something that fits no subject that already exists.
when_to_use: >-
  Something is about to be saved and no existing
  notes/<subject>/what-i-think.md covers it. Also when the user says they
  have got into something, are curious about something, or want to start
  reading about a topic with no use in mind.
argument-hint: <subject-name>
---
Start a new subject: $ARGUMENTS

## First, try to talk them out of it

Read `notes/subjects.md`. It lists every subject and what each one covers. If
one of them covers most of this, say so and suggest adding to that file
instead.

Two subjects that overlap is the most common way a notes folder goes bad:
you write something down, then can't remember which of the two files it went
in, so you write it again in the other one.

Only make a new subject when it genuinely has its own sources and its own
ideas.

## Then make it

Use a plain, lowercase, hyphenated name. `photography`, not `PhotoStuff`.
`claude-code`, not `cc`. Someone reading the folder in a year should know
what's in it from the name alone.

Make the folder and both files:

```bash
mkdir -p notes/<name>/sources
```

**`notes/<name>/what-i-think.md`** — a stub, four lines at most:

```markdown
# <Name>

What this covers: <one sentence>

Nothing here yet.
```

Do not pre-fill it with headings for things nobody has learned. Empty headings
look like gaps and get filled with padding.

**If this is a subject they're keeping for the pleasure of it**, rather than
something they mean to act on, add this line under "What this covers":

```markdown
**This one has no end.**
```

Then nobody gets asked in a year whether it's finished or dead. Some subjects
are neither — birds are not a project. Offer the line; don't interrogate them
about which kind it is.

**`notes/<name>/sources/README.md`** — three lines:

```markdown
# <Name> sources

One file per thing read or watched, named `YYYY-MM-DD-short-name.md`.
Add and delete freely. Don't rewrite what a source claimed — see
[the rules](../../README.md).
```

## Then add it to the map

Add one line to `notes/subjects.md`, in the same shape as the ones already
there:

```markdown
- [<name>](<name>/what-i-think.md) — <the same one sentence>
```

Skipping this is how the folder goes bad. A subject nobody can find gets
written a second time under a different name.

## Then say

One line: the folder you made, and that things can now be filed into it.
