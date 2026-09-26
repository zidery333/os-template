# agents/

One file per helper: `<name>.md`.

A **helper** is a separate Claude sent off to do one job on its own, with its
own fresh memory. The settings block at the top has a `name`, a
`description`, and optionally which `tools` and `model` it gets.

## Make one only when you really need it

A helper starts from nothing. It has to work out from scratch everything the
main conversation already knows. That's the cost, and it's a real one.

Two reasons that justify it:

1. **The job would flood the main conversation.** Reading a hundred files to
   answer one question — send a helper, get the answer back, keep the mess
   out of the way.
2. **The job needs different tools.** A helper that can only read, never
   write, for instance.

"It feels tidier" is not a reason. Neither is "the video had five of them."

## The shape

```markdown
---
name: find-it
description: Searches a large codebase and reports back where things are.
tools: Read, Grep, Glob
---

You search and report. You never change files.

Say where things are and how they connect. Don't explain what the code
should do — that's not your job.

If you can't find it, say so plainly. Don't guess a path.
```

Give it one job and tell it what **not** to do. A helper with a vague brief
comes back with a vague answer.
