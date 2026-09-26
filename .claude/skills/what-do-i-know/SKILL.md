---
name: what-do-i-know
description: >-
  Answers a question from what this person has already written down — their
  notes on a subject, or the reasons they recorded on one of their own
  projects — instead of from general knowledge, and says where each part came
  from and how sure it is. Use when they ask what they already know, read,
  or decided about something that has a file in notes/ or work/.
when_to_use: >-
  The user asks 'what do I know about X', 'what did I decide about Y', 'why
  did we do it this way', 'have I looked at this before', 'didn't I read
  something about this'.
argument-hint: <question>
---
Answer this from what's in this folder: $ARGUMENTS

This is the payoff for keeping notes. Not a search — an answer, built from
what this person has actually decided they believe.

## 1. Look in the right places, in order

1. `notes/subjects.md` — the list of every subject, to find the right one.
2. `notes/<subject>/what-i-think.md` — what they think now. This is the answer.
3. `notes/<subject>/sources/` — where those beliefs came from, if they need
   the receipts.
4. `me/` — their situation, which often changes what the right answer is.
5. `work/<project>/decisions.md` — if the question touches something they
   are making, they may have already decided this and written down why.
6. `notes/thrown-away.md` — worth checking. "You looked at this
   already and threw it away, for this reason" is a genuinely useful answer.

Search rather than reading everything. Don't load the whole folder.

## 2. Answer

Lead with the answer. Then, for each part of it, say **where it came from and
how sure it is** — one short line, like:

> from a video by X, 12 March. They said so, never tested it.

That line is the difference between this and a search engine. Without it, a
stranger's guess from a year ago reads exactly like something proven.

## 3. Say what you don't have

If the folder doesn't cover it, say that in one line. Then either:

- offer to answer from general knowledge, clearly labelled as **not from your
  notes**, or
- offer to go find something and `/learn` it properly.

Don't quietly fill the gap with general knowledge dressed up as their own
note. That poisons the one thing this folder is for.

## 4. Flag the rot

If you notice while looking:

- two notes that disagree with each other
- something over a year old about a fast-moving subject
- a belief with no source attached

Mention it in one line at the end. Don't fix it now — that's `/tidy-up`.
