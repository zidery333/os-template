---
name: save-to-my-os
description: >-
  Saves a lasting fact about the user into their OS folder — something they
  prefer, a habit, their machine or tools, the state of a project, or
  something they worked out that would otherwise be forgotten when the
  session ends. Offers it without being asked, then waits for a yes.
when_to_use: >-
  The user states something true about themselves rather than asking a
  question — 'remember that', 'from now on', 'I prefer', 'I always', 'my
  setup is', 'I use X', 'that worked', 'turns out the problem was'. Also
  after fixing something where the cause is worth keeping, or when the user
  corrects how you work.
---

# Saving something worth keeping

The whole point of this folder is that the user should never have to explain
the same thing twice. When they tell you something lasting, it gets written
down — or it's gone the moment the session ends.

## 1. Is it actually lasting?

Most of what people say is about right now. Save it only if it would still be
true and still be useful **in three months**.

Save it:

- how they want work done, or a correction they gave you
- their machine, tools, versions, where things live on disk
- what a project is, where its work lives, what state it's in
- why they picked one way over another on a project
- something they tried that worked, or didn't
- a decision and the reason behind it

Don't save it:

- anything about this one task
- something already written down — go **edit that line** instead
- something you inferred and they never confirmed, unless you mark it a guess
- anything private: passwords, keys, account numbers. This folder gets
  committed and copied.

When in doubt, don't. A folder full of half-true facts is worse than a thin one.

## The eight reasons people say no

Learn these and you will offer far less often, which is the real goal. Every
offer costs the user a moment, so a wrong one is not free.

1. **Thinking out loud.** "Ugh, I always forget this syntax." That is a
   complaint, not a fact about them.
2. **True today, noise in a month.** "I'm on the login bug." Right now is not
   the same as lasting.
3. **Too obvious to earn a line.** "I use git." Everyone does. It tells a
   future session nothing.
4. **You misread it.** They said something in passing and you heard a rule.
5. **Private.** Money, health, a client's name, anything they would not want
   in a folder that gets committed and pushed. **Never offer these at all.**
6. **Already written down**, somewhere they know about. Go edit that line
   instead of asking.
7. **Not decided yet.** "I think I prefer X." They are still making up their
   mind, and writing it down would freeze a guess into a rule.
8. **Bad timing.** Deep in a bug at 2am. The fact is fine; the moment is not.

Numbers 7 and 8 are **not-now**, not no. The others are **no**.

## 2. Where it goes

| What it is | File |
|---|---|
| How they want **you** to work — length, detail, what to ask about | `me/who-i-am.md` |
| How **they** work — their own habits at their own craft | `me/my-setup.md`, under "How I do things" |
| Machine, tools, versions, paths, timed jobs | `me/my-setup.md` |
| A project — what, where, what state, what next | `work/<project>/brief.md` |
| Why they chose something on a project | `work/<project>/decisions.md` |
| Something learned about a subject | `notes/<subject>/what-i-think.md` |
| A rule you should follow every single time | `CLAUDE.md` |

Those first two rows get mixed up constantly. "I always sharpen before a
glue-up" is a fact about their craft, not about how they want answers
written — it goes in `my-setup.md`. If it would still be true when they
stopped using Claude entirely, it isn't about you.

No subject fits? Use `/new-subject` first. No project folder yet? Use
`/new-project`. Don't invent a folder halfway through.

A decision goes in `decisions.md` **as a new entry, never as an edit to an
old one.** When they change their mind, the old entry stays as written and a
new one says what changed it.

## 3. Check what they already turned down

Read `.claude/hooks/declined.tsv` before offering. If this fact, or anything
close to it, is on that list:

- marked `no` → **stay silent.** Do not offer, do not mention it.
- marked `not-now` → you may offer again, but only if it comes up naturally.
  Not on the same day.

Being asked twice about the same thing is how people learn to ignore the
asking. Once they ignore it, the whole folder stops working.

## 4. Ask, in one line

**Answer their actual question first.** Then offer, in one short line:

> Want me to save that to `me/who-i-am.md`?

Rules for the asking:

- **One line.** Not a paragraph about why it's valuable.
- **Name the exact file.** "Want me to remember that?" is vague and gets a
  vague answer.
- **Ask once.** If they say no, or say nothing, drop it. Never ask twice about
  the same fact.
- **Never save without asking.** They may have been thinking out loud.
- **Don't interrupt.** If they're deep in something, let it wait. A fact
  worth keeping will come up again; a badly timed question just teaches them
  to ignore you.

## 5. If they say no, write that down

Add one line to `.claude/hooks/declined.tsv`:

```
2026-08-23	works mornings only	me/who-i-am.md	no
```

Tab-separated. The last column is `no` or `not-now` — use the eight reasons
above to pick. If they gave a reason, put it after that word.

Do this **every time**, even when the no was obvious. A record of what they
did not want is worth almost as much as a record of what they did.

Then move on. Don't acknowledge it, don't explain what you wrote.

## 6. Write it small

One or two lines, in **their** words, not yours. Mark how solid it is:
`(said)` if they told you, `(guessed)` if you worked it out.

Add the date only when it's the sort of thing that goes out of date.

Then say only which file it went in. One line. No summary of what you wrote —
they just told you, they know.

## 7. Edit before adding

Before writing a new line, read the file. If something close is already there,
**change that line** instead of adding a second one. Two files that half agree
is how this folder becomes useless — you stop trusting either of them.
