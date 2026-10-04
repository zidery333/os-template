---
name: learn
description: Learn how good practitioners actually do something, and bring back a note you can work from — from the official source and from people who do it for a living, including a video or a whole channel. Use when the user says teach me, how do the pros do this, I want to get good at, learn this properly, watch this video and, I keep doing this badly, or I'm curious about.
argument-hint: [the craft, or a link to learn it from]
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Bash(grep:*), Task, Agent, Read, Write, Edit, Grep, Glob, WebSearch, WebFetch
---

# Learn

They don't want a summary of a topic. Mostly they want to **do the thing**, the
way somebody good at it does it — but not always.

## First: is this for using, or for liking?

It changes everything after it, so know which before anything else. Most of the
time it's obvious; ask only when it isn't.

**For using**: they want it to change what they do or what they make. Everything
below applies. Be hard on the sources.

**For liking**: a subject they find interesting, something learned for the
pleasure of it, like birds or old maps. "I like this" is a complete reason. It
doesn't have to be useful or change anything, so never ask what they'll do with
it, and skip every step below marked *for using*. Nothing gets turned away for
not fitting.

## Start where they already are

`./os find "$ARGUMENTS"` first. Already covered? Extend that note and cite its
name — never write a rival one. Read its `## Who to trust` before any source:
whoever's advice held up when they tried it is read first, whoever's failed them
last or not at all, and say which. Then, in one message, only what changes the
answer. *For using*: what they'll do with it, and whether they've never tried,
are doing it badly, or are improving. If `$ARGUMENTS` says, skip the question.

## Two kinds of source, and a real note needs both

**The official one** — whoever defines the thing: its own documentation, the
standard, the originator's site or book. Definitions, numbers, current behaviour
and anything that moves come from here and nowhere else. Reach it by following
the link from the thing's own channel, repo, profile or book, never the first
search result — lookalike domains are the business model of some subjects. Cite
the URL and the date you read it. No official source, because it is a craft
rather than a product, is a fine answer: say so rather than promoting a blog.

**Practitioners** — people who do this for a living, showing real work. How it is
actually done, what they judge, what goes wrong. Usually YouTube, and usually
better than any article on the same subject. Count by *person*, not by upload,
and stop when new sources stop adding a step, a judgement call or a mistake —
that repetition *is* the method. Past three or four sources, hand it to
`@student`: six transcripts is 40,000 words and belongs in its own context.

Where they conflict, the official source wins on **what is true** and the
practitioner on **what is done**. Write the conflict down; don't smooth it over.
`SOURCES.md` here is how to reach and read either kind, and how to triage a pile
of transcripts into a read order.

## Judge the teacher before the lesson

Who made it, what they have actually shipped, and when. A confident stranger is
not a professional — say so when you couldn't tell. Almost everyone worth reading
sells something; where the pitch lands is what matters, and `SOURCES.md` has the
tells. Then stop, once, before writing: three lines on how much was advert and
what survived, whether the sources agree, and what it'll be called. Then ask.

## Write it

Keep only what transfers — the steps, the judgement behind each, the beginner
mistakes, the kit really used. *For using*: drop theory that changes nothing
they'd do on Monday. *For liking*: keep what makes it interesting to them. Every
subject hides one real disagreement; find it.

One note, or a folder if the sources really disagree. Ads, Excel, SEO: everyone
does the same thing with better tips, so blend them — the blend *is* the truth.
Trading, diet, lifting: rival schools, so `./os new ongoing "<Subject>"`, one file
per school, never blended. Mixing one school's entries with another's risk rules
is how people get hurt.

`./os new learning "<title>"` writes the note already in the right shape, and
`NOTE.md` here is how to fill it.

Then teach the folder the words — eight to fifteen terms this subject uses that
the folder doesn't, minus anything another domain already owns:

```bash
./os words <domain> "ad set" "learning phase" "roas"
```

That is how the next passing thought about it files itself. Say nothing about it.

Finally, *for using*, offer **one** thing that makes it usable: a job they'll
repeat becomes a skill (`/make-skill`), a craft they'll get better at becomes
`./os new ongoing "<the craft>"` with the practice step as its standard, and
neither means the note is enough — say so and stop.

## Rules

- Never teach from a source you didn't open — no summarising a video by its title,
  a page by its search snippet, or a book by its blurb. If the words won't come,
  say that rather than guessing.
- Where practitioners genuinely disagree, say who argues what. Don't flatten a
  live debate into one confident answer.
- One short quote per source, in quotes, attributed. Never reproduce a transcript,
  page, lesson or chapter wholesale — cite the timestamp or URL.
- Date anything where the craft moves. Six-year-old advice about a platform is a
  claim, not a fact, and so is undated documentation.
- Anything physical or carrying real risk — electrical, medical, legal, financial,
  structural — say where the note stops and a qualified person starts. Never write
  a trade plan or a dose.
- Quiet about risk **unless a source is reckless**: a claim the base rate
  contradicts earns one line, once, then teach.
- Same subject again? Read only what's new and append.
- They tried a source's advice and it worked, or didn't, in this chat or a later
  one? A dated line under that note's `## Who to trust`: who, what they said,
  what happened. Never change an old line; a new result is a new line.
- One craft per note. "Learn photography" is a bookshelf; get them to the thing
  they are actually stuck on.

## Done when

They have a name they can open, a first step small enough to do today (*for
using* only), and every claim traced to a named source — a timestamp, or a URL
with a date — they could go and check themselves. And the folder knows the
subject's words.
