---
name: new-project
description: >-
  Starts a new project in work/, with its brief and its decision log. Also
  covers things the user keeps up rather than finishes — a language, a
  garden, an instrument. Use before anything that has no folder yet.
when_to_use: >-
  The user starts making something new — code, writing, a business, a
  physical thing — or takes up something ongoing they mean to keep at, like
  an instrument, a language, gardening, or a fitness habit, and there is no
  work/<name>/ folder for it yet. Also when they say 'new project', 'I'm
  starting X', 'I've taken up X', or ask where some new thing should live.
argument-hint: <project-name>
---
Start a new project: $ARGUMENTS

## First, check it needs to exist

Read `work/projects.md`. If something already there covers this, say so and
suggest adding to that project instead. Two projects that overlap is how a
`work/` folder goes bad — you do a piece of work and then can't remember
which of the two it belonged to.

Then count the **active** ones that have an end — leave out any whose brief
says `**This one has no end.**`. Three is a lot. If this would be the fourth,
say so plainly and ask which one is really an idea. An idea is one line in
`work/projects.md` under **Ideas**, with no folder at all.

Don't refuse to make it. Say the count, then do what they say.

## Then make it

Use a plain, lowercase, hyphenated name. `lighthouse-novel`, not
`MyBook2_FINAL`. Someone reading the folder in a year should know what it is
from the name alone.

```bash
mkdir -p work/<name>
```

**`work/<name>/brief.md`** — the five lines from `work/README.md`, and
nothing else:

```markdown
# <Name>

**What it is:** <one sentence a stranger would understand>
**Where the work lives:** <an actual path, or "this folder">
**State:** <honestly. "nothing built yet" is a real state.>
**Next:** <one job, not a list>
**Done looks like:** <the single thing that would tell you it worked>
```

Ask for anything you don't know rather than inventing it.

**First work out whether this thing has an end at all.** Ask plainly: is this
something you want to finish, or something you'll just keep doing?

*Something they'll keep doing* — a language, a garden, an instrument, reading
everything one author wrote, keeping this folder working. Replace the
`**Done looks like:**` line with exactly:

```markdown
**This one has no end.**
```

Then nothing will ask when it's finished, call it stale after three months, or
count it towards having too many on. Don't push back on this and don't ask what
they'll do with it. A home has things in it that aren't going anywhere.

*Something they want to finish* — then **push on "done looks like"**. It's the
line people skip and the one that stops a project running forever. If they
genuinely don't know yet, write `TO FILL — not decided`, which shows up in
`/catch-me-up` until they answer.

If they hesitate, it has no end. People know when something is a job.

**`work/<name>/decisions.md`** — the heading and nothing under it:

```markdown
# <Name> — decisions

Newest at the top. Never edit an old entry; add a new one saying what
changed your mind. See [the rules](../README.md).
```

Don't pre-fill a first decision. Starting the project isn't a decision, it's
the thing the folder is for.

## Then add it to the map

One line in `work/projects.md` under **Active**:

```markdown
- [<name>](<name>/brief.md) — <the same one sentence>
```

Don't write "active" on it. It's under a heading that says so.

Delete the "Nothing yet." line under that heading once you've added the
first one. **Don't skip this.** The folder check looks for every project on
the map, so one you made but didn't list gets reported as a fault at the
start of the next session.

## Then say

One line: the folder you made, and what's still blank in the brief.
