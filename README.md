# Your OS

A memory folder for Claude Code.

Every chat with an AI starts from zero.

You explain yourself again. And again.

This folder holds it once. Claude reads it before every job.

Plain text files. No coding needed.

## Start

You need a paid Claude plan. Paste each command into Terminal (Mac: Cmd+Space, type Terminal, press Return. Ubuntu: Ctrl+Alt+T).

1. Click the green **Code** button, then **Download ZIP**. Unzip it.
2. `xcode-select --install`, click Install, wait. Ubuntu: `sudo apt install git curl`.
3. `curl -fsSL https://claude.ai/install.sh | bash`. If it says `~/.local/bin` isn't in your PATH, paste the line it gives. Reopen Terminal ([help](https://code.claude.com/docs/en/setup)).
4. `mv ~/Downloads/os-template-main ~/os`. If your Mac asks about Downloads, click Allow.
5. `cd ~/os && claude`. Log in and trust the folder.
6. Type `/setup`. Seven questions, about fifteen minutes.

Next time, just step 5. Used `git clone` instead? Run `rm -rf .git` first, or your notes go into the template's own history.

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
/snag <what broke>        write it down; on its own, the list to send
```

Everything else: `.claude/README.md`.

## Two rules

Fewer notes, better notes.

Never rewrite `sources/` or `decisions.md`. They're the record.

## Make it better

Broken thing or better idea? Send your `/snag` list to **zidery333** on Discord.

## What's proven

The machinery passes over a hundred checks: `bash .claude/tests/run.sh`.

It made Claude keep things filed. It didn't make Claude smarter, and it costs more.

Everything else is a guess.
