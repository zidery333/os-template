# .claude/

Everything Claude can run. The name is fixed by Claude Code — don't rename it.

| What | Where | What it is |
|---|---|---|
| Skills | `skills/<name>/SKILL.md` | A job. Type `/name` to force it, or let Claude start it when the moment fits. |
| Helpers | `agents/<name>.md` | A separate Claude sent off to do one job alone. Empty on purpose — see `agents/README.md` before adding one. |
| Hooks | `hooks/*.sh` | Plain shell that runs by itself when something happens. No model involved. |
| How it talks | `output-styles/plain-words.md` | Goes straight into Claude's instructions. The strongest lever on how replies read. |
| Settings | `settings.json` | Which hooks run, which output style is on, what's allowed without asking. |
| Claude Code's own memory | `settings.json` | Off here, so what Claude learns about you goes in `me/`, where you can read it, not in a hidden folder of Claude Code's. To turn it back on, delete the `"autoMemoryEnabled": false,` line. |
| Rulebooks | `guides/*.md` | Shared rules the skills point at instead of repeating. |
| The blank form | `guides/blank-note.md` | The shape every new write-up gets filled into. |
| Word list | `hooks/plain-words.tsv` | Words to swap for plainer ones. Checked after every reply. |
| Scripts | `scripts/*.sh` | Jobs you or a timer run. |
| Version record | `shipped.tsv` | What the template gave you, one fingerprint per file. How `/update-os` tells your changes apart. Leave it alone. |
| What changed | `CHANGES.md` | What each version of the template changed, file by file. |
| Snag list | `snags.md` | Yours: what went wrong with the machinery, written by `/snag`. Never shipped, never touched by an upgrade. |
| Tests | `tests/run.sh` | Checks that the checks still work. Run it after changing a hook. |

## The skills

| Skill | Starts on its own? |
|---|---|
| `save-to-my-os` | **Yes** — when you say something lasting about yourself. |
| `learn` | **Yes** — when you share a link or a document. |
| `what-do-i-know` | **Yes** — when you ask about a subject you have notes on, or why you chose something on a project. |
| `tidy-up` | **Yes** — when a file gets too long, two notes overlap, or a project has sat still for months. |
| `new-subject` | **Yes** — when something doesn't fit an existing subject. |
| `new-project` | **Yes** — when you start making something that has no folder. |
| `new-skill` | **Yes** — when you notice yourself doing the same job over and over. |
| `archive` | **Yes** — when you say a project is finished or dead. |
| `catch-me-up` | **Yes** — when you ask what's new. |
| `wrapup` | **Yes** — when you say you're done. Makes sure the files are up to date, offers to save them in the folder's history, then says it's safe to close. |
| `update-os` | **Yes** — when you ask to update the folder, or whether there's a new version. Always shows you first. |
| `snag` | **Yes** — when the folder's own machinery gets in the way, it writes that down in `snags.md`. Type `/snag` on its own to see the list, ready to send. |
| `setup` | No. Type `/setup`. It rewrites your files, so it waits to be asked. |

Add `disable-model-invocation: true` to any skill's settings block to make it
wait to be typed.

## The hooks

| File | When it runs | What it does |
|---|---|---|
| `session-start.sh` | Session opens | Tells Claude what in the folder needs attention. |
| `spot-worth-saving.sh` | Every message you send | Spots you stating a lasting fact, so Claude offers to save it. When you said "remember" or "from now on", Claude just saves it and says where. |
| `protect-the-record.sh` | Before a file is written, and before a command | Says so when the target is a write-up or a decision log — the two kinds that are never rewritten. An edit gets a reminder; replacing the whole file, a command that rewrites one, or moving or deleting a decision log asks you first. Adding to the end never asks, and nor does deleting a write-up. |
| `no-second-copy.sh` | Before a new write-up is created | Says so if the folder already holds one about the same source. Matches on the figures and names inside, not the file name. Turns the first try back so Claude reads it in time; a second try goes through. |
| `keep-tidy.sh` | After any file is written | Checks the file right then: too long, repeated headings, claims with no source. |
| `plain-words.sh` | After every reply | Sends the reply back if it used a word from `plain-words.tsv`. |

`spot-worth-saving.sh` reads `hooks/declined.tsv` first, so anything you have
already turned down is not offered again. Being asked twice is how people
learn to ignore the asking.

Every one is shell. Run any of them by hand to see what it does:

```bash
echo '{"user_prompt":"remember that I prefer short answers"}' | ./.claude/hooks/spot-worth-saving.sh
```

They stay quiet when there's nothing to say. A hook that always speaks gets
ignored, which defeats the point of having one.

`plain-words.sh` skips anything in quote marks, backticks, code blocks, or a
table row. Naming a word to talk about it is not the same as using it — without
that, the check fires on its own word list.

**To turn one off**, delete its block from `settings.json`. Nothing else
breaks.

## Which files here do you edit?

Almost all of it, actually. The hooks are the only part you'd normally leave
alone, and even those are short enough to read.

| File | What it's for |
|---|---|
| `skills/*/SKILL.md` | The jobs. Open any of them and change the wording. |
| `guides/*.md` | The shared rules — what's worth keeping, what gets thrown away. Change these and every skill follows. |
| `output-styles/plain-words.md` | How replies read. The strongest single lever in the folder. |
| `hooks/plain-words.tsv` | Words to swap for plainer ones. Add and delete freely. |
| `hooks/declined.tsv` | What you've said no to saving. Claude appends; you can empty it. |
| `settings.json` | Which hooks run, and what's allowed without asking. |

Changing a guide is the edit that reaches furthest. The skills all point at
`guides/how-to-add-stuff.md` rather than repeating its rules, so one edit
there changes how everything decides what to keep.

The knobs are environment settings. Set them in the `env` block of
`settings.json`, like `"env": { "OS_BIG_FILE_LINES": "400" }`, and the hooks
pick them up. Don't change the numbers in `hooks/lib.sh`: an update can
replace that file, and your number with it. `OS_BIG_FILE_LINES` (how long a
notes file can get, default 250), `OS_BIG_LOG_LINES` (a decision log, default
600), `OS_MAX_PROJECTS` (default 4), `OS_ROT_SHOWN` (warnings per session
start, default 3), `OS_SHARED_HEADINGS` (headings every subject file may
share, separated by semicolons; it replaces the three in `hooks/lib.sh`, so
copy those in too) and `OS_PLAIN_CHECK=off` to silence the word check.

## Changing a hook without breaking it

```bash
./.claude/tests/run.sh
```

Over a hundred checks, about five seconds. Every one of them is a bug that was
really in this folder at some point, so a failure means you have put one
back — the message tells you which.

It builds a throwaway folder in a temp directory and points the real hooks at
that, so your own notes and work are never touched and nothing is left
behind. Adding a test is a few lines at the bottom: build the situation, then
`ok`, `no` or `quiet` with what should happen.

The tests worth knowing about are the ones that assert **silence**. Most of
the value in these hooks is not speaking — a check that cries wolf twice
teaches you to skim the two that were right.

## Which should I make — a skill or a hook?

**A hook** when it must happen *every* time and needs no thinking. Checking a
file's length. Spotting a word. Counting files.

**A skill** when it needs judgment. Deciding whether something is worth
keeping. Merging two notes. Working out which subject something belongs to.

Hooks are cheap and reliable but stupid. Skills are smart but only run when
something decides they're relevant. Most good setups use a hook to *notice*
and a skill to *decide* — that's exactly how the saving works here.

## Making your own skill

```markdown
---
name: my-skill
description: >-
  What it does and when to use it. This is the only thing that decides
  whether Claude ever starts it on its own — so write it with the words
  you'd actually say, not words that sound good.
when_to_use: >-
  Extra trigger phrases. Real sentences you might type.
---

The steps, in plain words.
```

Two habits that make the difference:

**Say what "done badly" looks like.** "Under 20 lines" beats "be brief".

**Don't repeat rules that live in another file.** Point at it. Then when the
rule changes you only change it once — and you won't end up with two skills
quietly following two different versions of it.

## The scripts

- `scripts/daily-commit.sh` — **optional.** Saves this folder in its history
  once a day, and pushes it too if you've set up a remote. `/setup` starts
  that history; if there is none, the first run starts it. With no remote it
  saves and says so. If it stops working, the next session tells you.
  Read it before turning it on. Setup is in the comment at the top.
- `scripts/history.sh` — the skills run it for you. It starts the folder's
  history for `/setup`, says whether there's anything to save for
  `/wrapup`, and lists what changed this week for `/catch-me-up`. It never
  adds to a history that came with `git clone`.
- `scripts/upgrade.sh` — takes the newest version of the template, fetched
  from its GitHub page. `/update-os` runs it for you and helps with the part
  a script can't do. Run it with `--preview` first to see what it would
  change. It never overwrites a file
  you changed and never brings back one you deleted.
