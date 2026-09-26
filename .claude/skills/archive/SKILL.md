---
name: archive
description: >-
  Retires a project — moves it to work/archive/ and marks it done or dropped
  on the map, without deleting anything. Use when a project is finished,
  abandoned, or has clearly stopped.
when_to_use: >-
  The user says a project is done, finished, shipped, dead, dropped, or that
  they have stopped working on it. Also when the folder check flags a project
  nobody has touched in months and they confirm it is over.
argument-hint: <project-name>
---
Retire this project: $ARGUMENTS

Archiving is not deleting. The folder moves and stops being checked for going
stale. Everything in it stays readable.

## 1. Check it's really over

Ask one question, not three: **done, or dropped?**

- **Done** — it reached what `brief.md` said "done looks like", or near
  enough that they're happy.
- **Dropped** — they stopped. Fine and common. Say it plainly on the map;
  a project quietly marked "done" when it was abandoned is a lie you'll
  believe in a year.

If they're unsure, it's **paused**, not archived. Leave it where it is and
say so. Paused projects still get flagged after three months, which is the
point — a pause that lasts a year was a drop.

**A thing with no end is not a candidate for this.** If `brief.md` carries the
line `**This one has no end.**`, quiet is its normal state, not a warning sign.
Somebody who hasn't touched the piano since March has not dropped the piano.
Only archive one if they say outright that they've stopped for good — and then
it's **dropped**, not done, because it never had a done.

## 2. Write the ending first

Before moving anything, add one last entry at the top of
`work/<name>/decisions.md`:

```markdown
## <today> — Stopped here

<What state it ended in. What you'd do differently. What you'd tell someone
starting the same thing.>
```

This is the most valuable entry in the file and the easiest one to skip.
Three lines is plenty. Write it from what's actually in the brief and the
log — ask them for the "what I'd do differently" part rather than guessing.

Then update `brief.md` so its **State** line says how it ended. Leave the
rest of the brief exactly as it was; it's a record of what the plan had been.

## 3. Move it

```bash
git mv work/<name> work/archive/<name>
```

Use `git mv` if the folder is tracked, plain `mv` if it isn't. If a folder
of that name is already in `archive/`, stop and ask — don't overwrite one
and don't invent `name-2`.

## 4. Fix the map

In `work/projects.md`:

- Delete its line from **Active**.
- Add a line under **Done and dropped**:

```markdown
- [<name>](archive/<name>/brief.md) — <date>, <done or dropped>. <one line
  on how it ended>
```

If **Active** is now empty, put the "Nothing yet." line back so the next
person to read it isn't confused by a bare heading.

## 5. Say

Two lines: where it went, and the one thing from its decision log worth
remembering next time. Not a summary of the project — they lived it.

## What this skill never does

**It never deletes.** If they want it gone for good, say that deleting is
fine and let them do it, or do it only when they say so outright. The
decision log is the part that outlives the project, and it's small.
