# When the source is really about an app

Read this when what you're adding is a walkthrough of some tool — a no-code
builder, a workflow app, a platform with a drag-and-drop screen.

This genre is huge and it's mostly wasted runtime. A typical video is 70–80%
showing you where to click. None of that transfers.

## The two rules

**1. Shape, not clicks.** Write down how the job was broken up: what was
handed to the AI, what stayed a plain script, what tools the AI was given,
where information was stored, what happened when something failed. Never
write down a menu name or a button.

**2. Every note has to end somewhere real.** If you can't name the file it
becomes, it was entertainment. Mark it THROW AWAY and move on.

## Where a kept idea lands

| It becomes | Path | When the source taught you… |
|---|---|---|
| Background knowledge | `CLAUDE.md` | a fact or habit Claude should always know |
| A decision | `work/<project>/decisions.md` | it changed how you're doing something you're already making |
| A skill | `.claude/skills/<name>/SKILL.md` | a repeatable job — steps you call by name, or a job that takes judgment |
| A helper | `.claude/agents/<name>.md` | a role worth its own separate memory and tools |
| A hook | `.claude/settings.json` | something that must happen *every* time, automatically |
| A connection | `.mcp.json` | Claude needs to reach a system it currently can't |

## Translating the common pieces

| What the app calls it | What it is here |
|---|---|
| An agent box with instructions | A helper in `.claude/agents/` |
| A workflow — a fixed chain of boxes | A skill |
| A tool or sub-workflow | A connected tool, or just a script run in the terminal |
| A trigger — on a schedule or a web call | A timer, or a hook |
| An error branch | A hook, or a checking step inside the skill |
| A memory box | Files on disk. Usually `CLAUDE.md` or a notes file. |
| A search database over your documents | **Usually nothing.** See below. |

## The search-database trap

The single most over-applied idea in this genre.

In a folder of files, **plain text search already is the lookup layer**.
Exact, always current, nothing to rebuild, nothing to go stale. A fancy
search database only earns its place when you have a large pile of ordinary
writing — not code — and you genuinely need fuzzy "find me something like
this" recall.

When a video says "now let's add a vector store", the honest translation is
almost always "now let's add a search".

## The too-many-helpers trap

Demos spread work across five AI helpers because five boxes look more
impressive than one. In practice one well-aimed helper with good tools beats
a committee, and every extra one starts from nothing and has to work out
again what you already knew.

Split the work only when the roles genuinely need separate memory or
separate tools.

## Noise to bin on sight

- Revenue claims ("my $6K/month agent"). Advertising, not a forecast. Take
  the structure, ignore the number.
- Downloadable templates. A file you import is not an idea you understand.
- Business, agency and money framing. Fine content. Just not this.
