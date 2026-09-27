# Your OS

A memory folder for Claude Code.

Every chat with an AI starts from zero.

You explain yourself again. And again.

This folder holds it once. Claude reads it before every job.

Plain text files. No coding needed.

## Start

You need [Claude Code](https://claude.com/claude-code) (paid plan) on a Mac or Linux. On a Mac, run `xcode-select --install` once.

1. Click the green **Code** button, then **Download ZIP**. Unzip it.
2. Rename the folder `os` and move it to your home folder, so it lives at `~/os`.
3. Open Terminal. Type `cd ~/os`, then `claude`. Say yes to trust the folder.
4. Type `/setup`. Seven questions, about fifteen minutes.

Used `git clone` instead? Run `rm -rf .git` first, or a backup could send your notes here.

## Four rooms

```
me/       who you are
notes/    what you've learned
work/     what you're making
.claude/  the machinery
```

## Commands

```
/learn <url or paste>     keep the useful part of anything
/new-project <name>       start making something
/catch-me-up              what's waiting and what went stale
/update-os                take the newest version, keep your changes
/wrapup                   before you close a chat
```

The full list and how every part works: `.claude/README.md`.

## Two rules

Fewer notes, better notes.

Never rewrite `sources/` or `decisions.md`. They're the record.

## Make it better

Found something broken? Got a better idea?

Add me on Discord: **zidery333**.

Tell me what broke and what you'd change. A screenshot of the error helps.

The good ones go in the next version.

## What's proven

The machinery passes over a hundred checks: `bash .claude/tests/run.sh`.

It made Claude keep things filed. It didn't make Claude smarter, and it costs more.

Everything else is a guess.
