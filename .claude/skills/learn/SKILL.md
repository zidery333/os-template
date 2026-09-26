---
name: learn
description: >-
  Pulls something into the user's OS folder and files it — a YouTube video,
  an article, a PDF, pasted text, an idea of their own, or a subject they
  want to learn from scratch. Use when the user shares a link or a document
  and the useful part is worth keeping, asks whether something is worth
  saving, or names a subject they want to go and learn. Not when a link is
  shared only to fix or look something up for a job already under way.
when_to_use: >-
  The user pastes a URL, names a file to read, shares something they watched
  or read, names a subject with no link attached ('how to build muscle'), or
  says things like 'check this out', 'what do you make of this', 'is this
  worth saving', 'add this to my notes'.
argument-hint: <url, file path, pasted text, or a subject to go learn> [subject]
---
Learn from this and file it: $ARGUMENTS

This is the front door. It works out what it's been given and takes the right
route.

A link or file can be followed by a subject to file it under — `/learn <url>
sleep` puts it in `notes/sleep/`. That only applies when there's a link or a
file in front of it. `/learn how to build muscle` is a subject to go and
learn, not a link plus a folder called `muscle`.

**Never turn something away for not fitting.** Whatever they hand you is in
scope, full stop. Not the subjects already in the folder, not what they said
they were working on, not what they set up on day one. Curiosity arrives
before the folder does, and a folder that argues with what somebody wants to
read stops being theirs. If nothing here has a home for it, make the home.
The only reason to keep nothing is that the source itself was weak.

Read `.claude/guides/how-to-add-stuff.md` first — it holds the decision, the
throw-away rules, and how to weigh a source. If the material is really about
an app, also read `.claude/guides/ideas-not-apps.md`.

## 1. Work out what you were given

| What it looks like | What to do |
|---|---|
| A YouTube link | Pull the subtitles. |
| Any other link | Fetch the page. |
| A file path | Read it. PDFs and Word files included. |
| Pasted text | Use it as it is. |
| A subject, no link | Go and find sources. See below. |
| Nothing at all | Ask what they want to add. Don't guess. |
| Their own thought, out loud | Skip to step 3. It's already an idea, not a source. |

`.claude/guides/getting-the-text.md` has the how for each of those — the
subtitle commands, what to do with a page that won't fetch properly, how sure
each kind counts as. Read the part you need, not the whole thing.

### When they name a subject instead of a link

`/learn how to build muscle` means "go find this for me". It does **not** mean
"write down what you already know".

Never answer from your own knowledge here. A note with no source behind it
looks exactly like one they checked themselves, and six months later nobody
can tell which is which. That ruins the only thing this folder is for.

So:

1. Look in `notes/where-i-learn/who-to-trust.md` first. A source they
   already trust beats a stranger with better search ranking.
2. Search for the rest. Prefer whoever did the work over whoever is
   explaining it — the study over the article about the study, the docs over
   the tutorial.
3. Bring back **three to five**, no more. For each: one line on what it
   claims, and how sure it counts as (*they said so* / *they showed it* /
   *they measured it* / *from the source*).
4. Stop and let them pick. This is their folder, and picking is the part only
   they can do.
5. Then run the normal route on whichever they picked — fetch it, extract it,
   decide, file it.

If the search turns up nothing solid, say that. "I couldn't find anything
worth your time on this" is a real answer and a useful one.

If they gave you nothing, ask what they want to add. Don't guess.

## 2. Pull out the idea

Fill in `.claude/guides/blank-note.md`. Be brief — the tight boxes are the point.

The one that matters is **how it works, in three lines, naming no products
and no buttons**. If you can't do it, the source taught an interface rather
than an idea. That's strong evidence to throw it away.

Mark how sure it is honestly: *they said so* / *they showed it* / *they
measured it* / *from the source*. Most things are "they said so". Say it.

## 3. Decide

**First: is this for using or for liking?** The guide opens with this and it
changes everything after it. Something they want to act on gets the full
throw-away treatment. Something they're simply interested in does not — "I like
this" is a complete reason to keep it, and asking what they'll do with it is the
wrong question.

For things they want to use, apply the throw-away rules in
`.claude/guides/how-to-add-stuff.md`. Most should be thrown away, and a run that
ends that way worked correctly.

If they named a subject, use it. If no folder of that name exists, make it —
`what-i-think.md`, a `sources/` folder, and a line on `notes/subjects.md` —
and say in one line that you did. Don't stop and send them off to run
`/new-subject`. They already told you where it goes.

If they didn't name one, read `notes/subjects.md` and pick from there. Say
which one you picked, so a wrong guess is easy to catch. **That map is not a
list of what's allowed.** If nothing on it fits, make a new subject rather
than forcing the thing into the nearest one.

**When it only half fits**, say so rather than forcing it. This happens a
lot — a page about one thing carries one line about another. Two ways out,
and picking the wrong one is worse than asking:

- Most of it fits, one part doesn't → file it under the subject that fits and
  drop the part that doesn't. Say in your report what you dropped.
- It genuinely straddles two subjects → file it under the one the *belief*
  belongs to, not the one the source is about. The belief is what gets
  reread.

Prefer an existing subject when one genuinely fits — a second folder covering
the same ground is worse than a slightly crowded one. But "nothing fits" is a
reason to make a subject, not a reason to force or refuse. A subject with one
source in it is a normal way for a subject to start.

Then read that `notes/<subject>/what-i-think.md`. **Prefer editing what's already there.** A file that
nearly copies another file is worse than no file.

If it's the person's own idea rather than someone else's, there's no
write-up to file — put it straight into `notes/<subject>/what-i-think.md` marked as their
own thinking, and skip the `sources/` step.

## 4. File it

- Always save the write-up to
  `notes/<subject>/sources/YYYY-MM-DD-short-name.md`.
- **A belief** → write or edit `notes/<subject>/what-i-think.md`, carrying the how-sure
  label and a link back to the write-up.
- **Something runnable** → build the real file at the right path, not a stub.
- **Something that changes a project** → add an entry to that project's
  `work/<project>/decisions.md` saying what it changed and why. A new entry,
  never an edit to an old one.
- **Thrown away** → one line in `notes/thrown-away.md` with the
  reason, and delete the write-up.
- Either way, record what this source paid out in
  `notes/where-i-learn/who-to-trust.md` — **including when it paid out
  nothing**. That's the number that makes the scorecard honest. An official
  source has a **Record** column in the top table; everyone else has their
  own section further down.

## 5. Report

Two or three lines: what you decided, which file it went to, and for a
throw-away one sentence of why.

No summary of the content. If you find yourself recapping it, the extraction
failed — the whole job was to compress it into the fields above.
