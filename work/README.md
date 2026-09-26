# work/

What you're making. **One folder per project.**

`notes/` is what you've learned from other people. `work/` is what you're
building yourself. The two are different jobs and they wear out differently,
so they live apart.

Empty on purpose. Run `/new-project <name>` to start one.

## What a project folder looks like

```
work/
  projects.md            <- the map. every project, one line each.
  archive/               <- finished and abandoned ones. still readable.
  novel/                 <- a project
    brief.md             <- what it is, where it stands, what's next
    decisions.md         <- what you chose and why, with dates
    chapters/            <- the actual work. any shape you like.
  bike-shed/             <- another project, same two files
    brief.md
    decisions.md
```

Two files are required. Everything else is the work itself, in whatever
shape the work wants — folders of text, code, images, a spreadsheet.

Keep `brief.md` short. Past about thirty lines it has stopped being a brief
and turned into a diary, and the diary belongs in `decisions.md`.

## The two files

**`brief.md` — what this is and where it stands.**

Five lines, rewritten whenever it stops being true:

- What it is, in one sentence a stranger would understand.
- Where the real work lives, if it isn't in this folder — a folder path, an
  app, a notebook.
- What state it's in, honestly. "It runs but the numbers are wrong" beats
  "in progress".
- What you're doing next. One job, not a list.
- What "done" looks like. A number if you can manage one.

That last line is the hardest to write and the most valuable — for anything
you're trying to finish. A thing you mean to finish, with no finish line, runs
forever.

## Things you never finish

Not everything here has an end, and pretending otherwise is how a folder starts
feeling like a job.

Learning an instrument. A garden. A language. Cooking. Reading everything one
writer wrote. Keeping this folder working. None of them finish, and none of
them are worse for it.

For those, write this instead of a finish line:

```markdown
**This one has no end.**
```

That exact line, anywhere in `brief.md`. Everything else stays the same — it
still gets a brief, still gets a decision log, still goes on the map. What
changes is that the folder stops asking. It won't ask when you'll be done, it
won't call it stale after three months of not touching it, and it won't count
towards "you have too many projects".

Because those three questions are right for work and wrong for a life. Nobody
should have to explain to their own folder why they haven't played the piano
since March.

You can take the line out later. Something you kept for years for no reason
sometimes turns into something you want to finish, and that's a normal thing
to happen, not a correction.

**`decisions.md` — what you chose, and why.**

One entry per real decision, newest at the top, dated:

```markdown
## 2026-08-26 — Writing it in plain text, not a database

Tried a database first. Setup took a day and I still couldn't search it.
Plain files are slower at scale and I will never reach that scale.
```

A heading with the date and the choice, then two or three lines on why. That's
the whole shape.

This is the file that makes the folder worth keeping for years. In eighteen
months you will look at something odd in your own project and think "why on
earth did I do that?" — and this is the only place that answers.

**Never edit an old decision.** Same rule as `notes/<subject>/sources/`: when
you change your mind, add a new entry saying what changed it. The old one
stays as written. A decision log you edit is a decision log you can't trust.

**It's allowed to grow forever.** Everything else here gets told off for
getting long; this one doesn't. Past about 600 lines you'll be asked to move
entries older than a year into `decisions-older.md` next to it, and leave a
line at the bottom pointing there. That's a move, not a cut — nothing gets
thrown away.

## What counts as a decision worth writing down

- You picked one way over another and the other was reasonable.
- You tried something and it didn't work, so you stopped.
- You said no to a feature, a tool, or a whole direction.
- Something surprised you and it changed the plan.

Not decisions: what you did today, what you're going to do, anything you'd
have done the same way without thinking.

If you write more than about one a week, you're logging activity rather than
decisions.

## Finishing a project

Run `/archive <name>`. It moves the folder into `work/archive/` and marks the
line in `projects.md` as done, with the date.

Archived is not deleted. The decision log outlives the project — it's the
part you'll actually reread. Delete a project only when you'd be happy never
to see it again.

## What keeps this from turning into a graveyard

**Three active projects is a lot. Five is too many.** Anything past that is
an idea, not a project. Ideas go under the **Ideas** heading in
`projects.md` — one line, no folder, no files. Give an idea a folder on the
day you actually start it.

A project nobody has touched in three months gets flagged at the start of a
session. That's not a nag to work on it — it's a question. Is it paused, or
is it dead? Both are fine answers. Pretending is not.
