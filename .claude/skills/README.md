# skills/

One folder per skill: `<name>/SKILL.md`.

A **skill** is a set of instructions Claude picks up on its own when a job
matches it. Most start on their own; some, like `/setup`, wait to be typed.

The settings block at the top has a `name` and a `description`. **The
description is the only thing that decides whether the skill ever fires.**
So write it to be matched, not to sound good: put in the words someone would
actually use when they want this.

Skills cover both kinds of job: ones that need judgment, and ones that are a
fixed list of steps. Type `/name` to force any of them.

## The shape

```markdown
---
name: check-my-writing
description: Checks writing against the person's rules — plain words, short
  sentences, answer first. Use when reviewing, editing, or drafting anything
  they will send to someone.
---

# Checking writing

## What to look for
...

## What good looks like
<a real example, not a description of one>

## What bad looks like
<a real example of the mistake, and the fixed version next to it>
```

## What makes a skill work

- **Show, don't describe.** One before-and-after example beats a paragraph
  about what good looks like.
- **Say what to do when the rule doesn't fit.** Every rule has an exception.
  If you don't name it, you get the rule applied somewhere silly.
- **Keep it short enough to actually be read.** A skill nobody finishes
  reading is a skill that half-fires.

## Where skills come from

Not from imagination. From the "things I do over and over" list in
`me/who-i-am.md`. Write that list first; the skills fall out of it.
