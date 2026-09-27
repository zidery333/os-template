# Your OS

A memory folder for Claude Code. It remembers who you are, what you've
learned, and what you're making — so Claude doesn't start from zero every
time you talk to it.

It's just text files. You can read every part of it yourself, and delete any
part you don't want. You don't need to know how to code. ("OS" here means
your own system for life and work, not Windows or macOS.)

## What you need

- **Claude Code.** Anthropic's AI helper that works with files on your
  computer. It needs a paid Claude plan. Get it at
  [claude.com/claude-code](https://claude.com/claude-code).
- **A Mac or Linux computer.** Windows hasn't been tried.
- **On a Mac, Apple's developer tools.** The folder's checks need python3,
  which comes with them. Open Terminal and run `xcode-select --install` once.
  If it says they're already installed, you're set. Without them the folder
  still works, but its checks stay switched off.

## Start here

**1. Get the folder.** On this page, click the green **Code** button, then
**Download ZIP**. Unzip it. You get a folder called `os-template-main`.

**2. Put it somewhere plain.** Rename it `os` and move it into your home
folder, so it lives at `~/os`. On a Mac, jobs that run on a timer can't read
`Downloads`, `Documents` or `Desktop`. That only matters if you later turn on
the nightly backup, but moving it now is easiest.

**3. Open it in Claude Code.** Open Terminal (on a Mac: Cmd+Space, type
Terminal, press Return). Type `cd ~/os` and press Return, then type `claude`
and press Return. Say yes when it asks whether to trust the folder. Then type:

```
/setup
```

It asks seven questions — what you do, what you're into, how you like
answers written — and fills in the blank files. Takes about fifteen minutes.
Nothing is permanent; you can change any answer later by editing the file.

**Want to know the machinery works before you trust it?** Type this in
Terminal, inside the folder:

```bash
bash .claude/tests/run.sh
```

Over a hundred checks, about five seconds. The last line should say
"0 failed".

**Used `git clone` instead of the ZIP?** Run `rm -rf .git` in the folder
first. Otherwise your notes stay linked to this page, and a backup could try
to send them here.

## Four rooms

```
me/       who you are. Read first, every time.
notes/    what you've learned from other people. One folder per subject.
work/     what you're making. One folder per project.
.claude/  the machinery. This name is fixed by Claude Code.
```

That's the whole structure, and it doesn't change as the folder grows. Each
of the four has a README explaining itself, and `.claude/README.md` covers
everything inside `.claude/`, so you can always find out what something is
for without asking.

Inside `notes/` and `work/`, everything about one thing lives in one folder:

```
notes/sleep/what-i-think.md      what you believe today. Rewrite freely.
notes/sleep/sources/             the write-ups behind it. Don't rewrite these.

work/novel/brief.md              what it is, where it stands, what's next.
work/novel/decisions.md          what you chose and why. Don't rewrite these.
work/novel/chapters/             the actual work, in whatever shape it wants.
```

## The idea

Most people explain themselves to an AI over and over. Same background, same
preferences, same project, every single chat. This folder holds that
explanation once, in files, so it's already there.

Then it grows two ways.

**You learn things.** Watch a video or read something good, run one command,
and the useful part lands in `notes/`. The rest gets thrown away on purpose.
Six months later `notes/` is a small pile of things you actually believe, and
each one says where it came from.

**You make things.** Every project gets a folder with a brief and a decision
log. The brief keeps changing. The decision log never does — and in eighteen
months, when you look at something odd in your own work and think "why did I
do that?", it's the only thing that answers.

**And you keep things.** Not everything finishes. A language, a garden, an
instrument, a subject you read about because you like it. Put the line
`**This one has no end.**` in its brief and the folder stops asking when
you'll be done, stops calling it stale, and stops counting it against you.
This is meant to be a home, not a workplace, and a home has things in it that
aren't going anywhere.

## What it does without being asked

You don't have to run anything. Five things happen on their own, and all of
them are plain shell — no AI involved, so they cost nothing and add no delay.

**It notices when you've said something worth keeping.** Tell it "I always
work in the mornings" and it offers, in one line, to write that down. You say
yes or you ignore it. It never saves without asking, and it never asks twice
about the same thing.

**It tidies as it goes.** Every time a file is written it gets checked right
then — too long, the same heading twice, a claim with no source. There is no
cleanup day, because nothing is left to pile up.

**It knows what state it's in.** Every session starts by checking the folder
and quietly telling Claude what needs attention — a subject nobody has
touched in a year, a project nobody has touched in three months, a file
sitting loose at the top level, six active projects when three is plenty.
You hear about at most one thing, at a natural moment.

**It guards the two files that matter.** When something is about to overwrite
a write-up or a decision log, it says so first. Those are the records — a
typo fix is fine, quietly changing what was claimed is not. It warns rather
than blocks, because appending a dated correction looks the same to a script.

**It checks its own writing.** After every reply, the words go through a list
in `.claude/hooks/plain-words.tsv`. Use a stuffy word and the reply gets sent
back to be said plainly. Edit that list to taste, or delete the hook.

It also remembers what you said no to. Turn down an offer to save something
and it goes in `.claude/hooks/declined.tsv`, so you never get asked twice.

## Typing things yourself

All of these also work as slash commands when you want to force one:

```
/setup                    # first time only — sets the folder up for you
/learn <url or paste>     # anything: video, article, PDF, a chat, your own idea
/new-project <name>       # start making something
/new-skill <what it does> # teach it a job you keep doing by hand
/new-subject <name>       # start learning about something
/archive <name>           # retire a project without deleting it
/update-os                # take the newest version of this template, keep your changes
/save-to-my-os            # write down something lasting about you
/what-do-i-know <thing>   # answer from your own notes, not the internet
/catch-me-up              # what's waiting, what changed, what needs you
/wrapup                   # before you close a chat: files up to date, safe to close
/tidy-up                  # merge repeats, delete what went stale
```

Each is a file in `.claude/skills/`. Open any of them and change it. (On a
Mac, Finder hides folders that start with a dot. Press Cmd+Shift+. to see
them.)

## How to use it

Mostly: just work. It'll ask when it spots something worth keeping.

Once a week — and always after months away — run `/catch-me-up`. It tells you
where each project got to, what's gone stale and what's waiting, which beats
opening files and trying to remember.

## Rules that keep it healthy

**Don't let it grow just because growing feels good.** Fewer, better notes.
Three things you're trying to finish, not eight. A session where you throw
everything away is a good session: most of what you read isn't worth keeping,
and a folder full of half-true notes is worse than a small one.

That rule is about things you mean to *use*. It has nothing to say about what
you're merely interested in — keep as much of that as you like. Curiosity
doesn't have to justify itself here.

**Edit before you add.** If a new file is almost the same as one you have,
change the old one instead of making a second one.

**Say how sure you are.** Most of what you read is one person's opinion and
has never been tested. Write that down next to it.

**Never rewrite `notes/<subject>/sources/` or `work/<project>/decisions.md`.**
Those are records of what somebody claimed, and what you chose, on a day.
Everything else here is your current opinion and can change any time. Keeping
those two honest is the whole reason the folder is worth having in five
years.

## Making it yours

Nothing here is fixed. Some things people change on day one:

- **Don't want to be corrected on word choice?** Delete the `Stop` hook from
  `.claude/settings.json`.
- **Want different limits?** They're environment settings at the top of
  `.claude/hooks/lib.sh`: how long a file can get, how many projects count as
  too many, how many warnings a session start shows.

Changed something in `.claude/hooks/`? Run `./.claude/tests/run.sh`.
Over a hundred checks, about five seconds, and it tells you which one you broke.

## Getting a newer version

The template gets fixes. To take them, type:

```
/update-os
```

It fetches the newest version from this page, and shows you what would change
before it changes anything. Files you never
touched get the new version. Files you changed stay yours — the new version
is put beside them, and Claude helps you carry over what's worth having. Files
you deleted stay deleted. Your notes, projects and answers are never touched.
It upgrades this folder where it is, and keeps the old version of every file
it replaces in `.claude/.upgrade/before/`, just in case.

To hear when there's a new version, click **Watch** at the top of this page,
then **Custom**, then tick **Releases**.

`.claude/CHANGES.md` says what changed in each version. `.claude/shipped.tsv`
is how the upgrade tells your changes from the template's — leave it alone.

## Got more than one OS folder?

Some people end up with a few — one for work, one for home. `.claude/home/` is
an optional home folder that looks after all of them from one place. It shows
what's on the go in each, runs their checks, and sends a shared skill to every
one. It never writes into your notes or work, never deletes, and sends nothing
until you say so. To use it, copy it out beside your folders and read its README:

```bash
cp -R .claude/home ~/os-home
```

## Keeping it safe

Git is the whole backup story. A folder you just downloaded isn't a git
repository yet. Check with `git status`; if it says "not a git repository",
make it one, once:

```bash
git init && git add -A && git commit -m "starting point"
```

After that, commit whenever you've done something you'd hate to lose:

```bash
git add -A && git commit -m "what changed"
```

To keep a copy somewhere else, make an empty private repo and point this at
it. Then `.claude/scripts/daily-commit.sh` can do the committing for you once
a day — read it before you turn it on, the instructions are at the top of the
file.

You don't have to do any of this. It works fine as files on one machine. But
five years of your own thinking is worth a backup.

## What's actually proven here

Less than you'd hope, and here it is.

**The machinery works.** `./.claude/tests/run.sh` runs over a hundred checks.
Every one of them is a bug that was really in this folder at some point. That
tells you the hooks do what this page says they do, and nothing beyond that.

**The rest was measured once, on two jobs, and the result is mixed.** With
the folder switched on, Claude kept things filed, labelled and up to date.
Switched off, none of that happened. But it didn't make Claude any smarter —
on one job it did worse, filing a note twice. And it cost about 1.5 to 3
times as much. Treat that as a hint, not a finding.

**The shape is a guess.** Four rooms, two files that never get rewritten —
that is one way of organising things, not a tested one. Everything you put in
`notes/` is a guess too, which is why every note says where it came from and
how sure you were.
