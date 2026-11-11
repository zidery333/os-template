---
name: make-skill
description: Build a new reusable skill or helper for this folder — interview, write, check, register. Use when the user says make a skill, automate this, I keep asking you to do X, turn this into a command, make this a button, or build me a helper — a new one, not handing this project to one (/handoff).
argument-hint: [skill|helper] [what it should do]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Write, Edit
---

# Make a skill

A **skill** is how a recurring job gets done — it runs in this conversation.
A **helper** is who does a big job that deserves its own head-space — it runs
apart and reports back. Pick before writing anything.

| Skill | Helper |
| --- | --- |
| A procedure or checklist | A job with its own context |
| Runs right here | Would flood this conversation |
| Needs what's already on screen | Needs its own tools and isolation |

If it must *always* hold no matter what the AI decides, it is neither — it's a
hook in `.claude/hooks/`. Say so.

## 1. Make the empty one here first

```bash
./os new skill "<Name>"      # or: ./os new helper "<Name>"
```

That puts it in this folder's `.claude/` and on the list. Every word you write
next goes into the file it made, and nowhere else. Replace what it wrote
there, comments included; don't add below it.

## 2. Write and test it with Anthropic's skill creator

If a skill called `skill-creator` is in your list (it may show as
`anthropic-skills:skill-creator`), read its `SKILL.md` and follow it for the
interview, the writing and the testing. It is better at that than this file.
These folder rules win wherever the two disagree:

- **Write into the file `./os new` made.** Never `~/.claude/skills`, never a
  claude.ai account skill: those show up in every folder they open.
- **Skip its packaging step.** No `.skill` file, nothing sent to upload. An
  uploaded skill becomes an account skill and leaves this folder.
- **Everything to do with testing goes in a scratch folder outside this
  one**: its test cases (`evals/`) and its runs (`<name>-workspace/`). Never
  inside the skill's folder or beside it, where a folder reads as a broken
  skill. Delete it once they've seen the results.
- **Small tests by default.** Two or three real cases, run once right here
  by following the new skill yourself, and shown to them in the chat. Its
  full loop (with-and-without runs, graders, the review page, the description
  tuner) runs many chats and uses a lot of their usage: offer it, say so
  plainly, and run it only on a yes. Its "always make the review page" is
  part of that loop.
- **Under about 120 lines.** Its own limit is 500; ours is shorter because
  `./os check` flags longer ones. Tables, specs and examples go in a
  `REFERENCE.md` beside it.
- **Talk like a person.** Its words (evals, assertions, benchmark) are yours,
  not theirs.

For a **helper**, use skill creator's interview and its testing idea, but the
file is a helper's: name its tools, and say exactly what its final message
contains. Nothing else comes back.

## 2b. Without skill creator

Another AI, or an account without it: do it by hand. Three things decide
whether it works, and only they need asking about: **what exact words they'll
say when they want it**, **what finished looks like**, and **what it must never
do**. Ask in one message, or skip if `$ARGUMENTS` already answers them. Then
replace the template, don't append to it:

- `description:` decides whether it ever runs. Lead with the use case, then the
  trigger words exactly as a person would say them.
- Say what the job is and what good looks like. Prescribe steps only where the
  order matters; a capable model reading a clear contract beats a brittle script.
- Long reference material goes in a second file, mentioned in `SKILL.md` so it
  only loads when needed.

## Rules

- Never name it after a built-in: `doctor`, `review`, `run`, `init`, `debug`,
  `loop`, `help`, `status`, `config`, `model`, `code-review`. `./os new skill`
  refuses these outright.
- Lowercase-with-hyphens. The folder name *is* the command.
- One skill, one job. If the description needs "and", it's two skills.
- Never write a skill for something done once. Just save a note.
- Before writing one, check the skills you were given for one from Anthropic
  that already does it. If one does, say so: using theirs beats a copy here.

## Done when

Run `./os sort`, so the list picks up the new description. Then `./os check` is
clean, it shows up in `.claude/CATALOG.md` as itself, and you've run it once in
front of them on a real case. An untested skill is a guess.
