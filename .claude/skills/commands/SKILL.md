---
name: commands
description: Show everything you can ask for in this folder, like a bot's help menu — the skills, the helpers, and the plain things you can just say. Use when the user says commands, what can you do, help menu, what can I ask, list your commands, or menu.
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read
---

# Commands

They never type `./os`. They talk. So the menu is what they can *say*, not
what the terminal accepts.

```bash
./os brief --json --quiet     # where things stand, so the menu can be specific
```

Read `.claude/CATALOG.md` for the skills and helpers actually installed here,
including any they made themselves.

## Show

Three short groups, one line each, in this order. Use the person's words from
each skill's `description:`, never the file names.

- **Say it and it happens.** Write this down · what should I work on · find X ·
  we decided X · hand this off · start the week · wrap up · where does X live.
  One line per skill in the catalog.
- **Big jobs that go off on their own.** One line per helper: what you'd say
  to get it, and what comes back.
- **Anything else,** in one line: whatever they say gets written down and filed,
  and they never pick a folder.

If the folder is empty, skip the helpers group and end with what to say first.

## Rules

- No `./os` commands, no file paths, no slash names unless they ask how to
  type it. The `/name` goes in brackets after the line if they asked.
- Every line is something to say, in their words, not a feature.
- Under twenty lines. A menu they scroll is a menu they close.

## Done when

They can pick a line and say it.
