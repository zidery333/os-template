# How to add something

Read this before running `/learn`. It's the
shared rulebook — the commands don't repeat it, they point here.

## Every source ends up as one of three things

| It becomes | Where it goes |
|---|---|
| **something you believe** | `notes/<subject>/what-i-think.md` — a new line, or a change to what's there |
| **something you can run** | a file in `.claude/skills/` or `.claude/agents/`, or a change to `CLAUDE.md` |
| **something that changes a project** | the project's `work/<project>/decisions.md`, saying what it changed and why |
| **nothing** | one line in `notes/thrown-away.md`, and the write-up is deleted |

Anything that survives also leaves its write-up in `notes/<subject>/sources/`,
so you can always trace it back to who said it and when.

## Nothing gets turned away for not fitting

Before any of the rules below: **whatever somebody hands you is in scope.**
Not just the subjects already in the folder, not just what they said they were
working on, not just what they set up on day one. If nothing here has a home
for it, make the home — a new subject with one source in it is a normal way
for a subject to start.

The rules below are about whether a *source* was any good. They are never a
test of whether a *topic* is allowed. A folder that makes somebody argue for
their own interests has stopped being theirs.

## First: is this for using, or for liking?

Ask before anything else, because the rest of this page only applies to one of
them.

**For using** — you want it to change what you do or what you build. Everything
below applies. Be hard on it.

**For liking** — a subject you find interesting, a thing you're learning for
the pleasure of it, something beautiful you want to keep. None of the
throw-away rules below apply, and it does not have to be useful, tested, or
about to change anything. "I like this" is a complete reason.

Don't ask which one it is out loud every time. Most of the time it's obvious.
Ask when it isn't.

## Throwing it away is the normal answer — for things you want to use

A session that ends with nothing kept is a **good** session. Throw it away
when the source is:

- **Button-pressing** — clicks, sign-up steps, "now open this menu". That's
  an app, not an idea.
- **A sales pitch** — the source's real aim is to sell you something. (Advice
  on pricing and selling is fine to keep when selling is what you do.)
- **Already covered** by something you have. **Edit the file you've got
  instead.** This one applies to everything, including things kept for liking:
  two write-ups of one source means neither can be trusted.
- **A fix for a problem you don't have** — a fancy search database where
  plain text search works; five helpers where one good one would do. This is
  about the fix being too big for the job, not about the subject being new to
  you. Wanting to read about something you have never touched is not a
  problem you don't have.

Never keep something weak just to make the session feel productive. That's
how a notes folder turns into landfill.

**None of that is a reason to bin something they're simply interested in.**
Curiosity does not have to justify itself, and a folder that makes somebody
argue for their own interests stops being theirs.

## Keep the shape, not the surface

Write down **how the thing works**, not **how it was demonstrated**.

Test: can you explain the idea without naming a single product, button, or
menu? If not, you learned an interface. That's strong evidence to throw it
away.

## Think about who's talking

Check `notes/where-i-learn/who-to-trust.md` before you extract anything. A
source that's mostly promotion should be read expecting to keep almost none
of it.

Official docs beat videos. When a video disagrees with the docs, **write the
disagreement down** instead of quietly picking one. Which is right is worth
knowing on its own.

## Say how sure you are — about claims, not about tastes

A label answers "how do I know this?". That question only has an answer for a
claim about how the world works. "Morning light sets your body clock" needs
one. "This poem is the best thing I read all year" does not, and pinning
*they said so* on it is silly.

So: label claims. Leave what someone likes unlabelled.

For a claim, most of what arrives is one person's opinion that has never been
tested. Say so, and carry the label into `notes/`. Four levels:

- **they said so** — they claimed it. Most video content is this.
- **they showed it** — they did it once on camera.
- **they measured it** — they ran it more than once and counted.
- **from the source** — docs, or the people who built the thing.

Ideas that sounded obviously correct have repeatedly turned out to change
nothing at all. That's why the label matters.

## Adding a new way to bring things in

Make a file at `.claude/skills/learn-<type>/SKILL.md`. It only needs to: get the
text, fill in `.claude/guides/blank-note.md`, apply the rules above, and file the
result. Don't copy the rules into it — point back here.
