---
name: new-skill
description: >-
  Writes a new skill into .claude/skills/, starting from a job the person
  already does by hand. Use when they say they keep doing the same thing over
  and over, ask how to teach Claude a job, ask for a new command or skill, or
  when /tidy-up spots a job done three times or more.
when_to_use: >-
  The user says 'I keep doing this', 'can you remember how to do this', 'make
  this a command', 'new skill', or notices themselves repeating a job. Also
  straight after finishing a task where they had to explain the same context
  they have explained before.
argument-hint: <what the skill should do>
---
Write a new skill: $ARGUMENTS

## 1. Check it should be a skill at all

**A hook** if it must happen every time and needs no thinking — checking a
file's length, spotting a word, counting files. Hooks are shell in
`.claude/hooks/`, they cost nothing, and they never forget.

**A skill** if it needs judgment. Deciding whether something is worth keeping.
Merging two notes. Working out which subject something belongs to.

If it's a hook, say so and stop. Writing it as a skill means it fires
sometimes, which is the worst of both.

## 2. Start from a job they already do

The good ones come from `me/who-i-am.md`, under "Things I do over and over" —
read it. A skill invented for a job nobody has actually done twice is a file
that never fires.

If that section is empty and they haven't named a real repeated job, say so.
"You've only done this once, let's see if it comes back" is a real answer and
saves them a file.

## 3. Name three times it should fire, before writing anything

Write down three real sentences they might type that should start this skill.
Not summaries — the actual words.

This is the whole job, because **the description is the only thing that
decides whether a skill ever runs**. Everything else in the file is what
happens *after* it fires. Get the three sentences first and the description
writes itself.

If you can't think of three, the skill is too narrow. Say that.

## 4. Write it

`.claude/skills/<name>/SKILL.md`. The rules are not style preferences — a
skill that breaks the first two silently never loads:

- **`name` must match the folder name exactly.** Lowercase, numbers and
  hyphens only. Never the words "claude" or "anthropic".
- **`description` must be there**, in the **third person** — "Retires a
  project", not "Retire a project" and not "I can help you retire". It's
  injected into the system prompt and a mixed point of view makes it fire
  unreliably. Say what it does *and* when to use it, in the words from step 3.
- **Under 500 lines.** Past that, put the detail in a second file beside it
  and point at that file from here, one level deep. Nothing this folder needs
  should come close.

```markdown
---
name: <same-as-the-folder>
description: >-
  <What it does, third person.> Use when <the situations from step 3>.
when_to_use: >-
  <Real sentences they would actually type.>
---
The steps, in plain words.
```

## 5. Two habits that make the difference

**Show what "done badly" looks like.** "Under 20 lines" beats "be brief". A
real before-and-after beats a paragraph describing one.

**Point at rules, don't copy them.** If a rule already lives in
`.claude/guides/how-to-add-stuff.md`, link to it. Copying means two skills
quietly follow two different versions of it a year from now.

## 6. Check it

```bash
./.claude/tests/run.sh
```

The folder check reads every skill's settings block at the start of each
session, so a broken name or a missing description gets reported. Run the
tests and confirm the count didn't drop.

Then add the job to "Things I do over and over" in `me/who-i-am.md` if it
isn't there, so the next one starts from a longer list.

## 7. Say

Two lines: the file you made, and the three sentences it should fire on — so
they can tell you straight away if one of them is wrong.
