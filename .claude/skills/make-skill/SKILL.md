---
name: make-skill
description: Build a new reusable skill or helper for this folder — interview, write, check, register. Use when the user says make a skill, automate this, I keep asking you to do X, turn this into a command, make this a button, or build me a helper.
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

## Getting it right

Three things decide whether it works, and only they need asking about: **what
exact words they'll say when they want it**, **what finished looks like**, and
**what it must never do**. Ask them in one message, or skip entirely if
`$ARGUMENTS` already answers them.

```bash
./os new skill "<Name>"      # or: ./os new agent "<Name>"
```

Then replace the template — don't append to it:

- `description:` decides whether it ever runs. Lead with the use case, then the
  trigger words exactly as a person would say them.
- Say what the job is and what good looks like. Prescribe the steps only where
  the order genuinely matters; a capable model reading a clear contract beats one
  following a brittle script.
- Long reference material goes in a second file in the folder, mentioned in
  `SKILL.md` so it only loads when needed.
- For a helper: name its tools, and say exactly what its final message contains.
  Nothing else comes back.

Check it with `./os check`, then run it once in front of them on a real case. An
untested skill is a guess.

## Rules

- Never name it after a built-in: `doctor`, `review`, `run`, `init`, `debug`,
  `loop`, `help`, `status`, `config`, `model`, `code-review`. `./os new skill`
  refuses these outright.
- Lowercase-with-hyphens. The folder name *is* the command.
- One skill, one job. If the description needs "and", it's two skills.
- Under about 120 lines — `./os check` says when it isn't. Rules stay in
  `SKILL.md`; tables, specs and examples move to a `REFERENCE.md` beside it.
- Never write a skill for something done once. Just save a note.

## Done when

`./os check` is clean, it shows up in `.claude/CATALOG.md`, and you've run it once.
