# How to work with me

Claude reads this file at the start of every conversation. It's the most
important file in the folder.

Run `/setup` and it fills in the blanks from your answers. After that, edit
this file by hand whenever something annoys you.

## How to talk to me

<!-- TO FILL: /setup rewrites this list from your answers. What's below is a
     sensible starting point — keep the ones you like, delete the rest. -->

- **Keep it short.** Under 150 words unless I ask for more. One-line answer,
  one-line reply.
- **Answer first.** No warm-up, no repeating my question back at me.
- **Short sentences. One idea each.**
- **Plain words only.** If a 13-year-old wouldn't know a word, use a different
  one — or explain it in the same sentence using something ordinary as a
  comparison.
- **No tables, no headings, no long bullet lists** for a normal answer. Just
  say it.
- **Don't list options you aren't going to pick.** Pick one and say why.
- **Tell me when something is broken or unfinished.** I'd rather hear bad
  news than a tidy summary that isn't true.

These rules apply everywhere, not just chat — file names, commit messages,
code comments, all of it.

## Where things are

Four rooms. Everything belongs in one of them.

- `me/` — who I am. **Read this before doing anything for me.**
- `notes/` — what I've learned from other people. One folder per subject.
- `work/` — what I'm making, and what I'm keeping up. One folder each.
- `.claude/` — the machinery. This name is fixed by Claude Code.

Read only the part the job needs. Don't read the whole folder.

Big files — video, photos, raw recordings — live outside this folder. Write
down where they are instead. Never move files in from elsewhere on the
computer without asking first.

Inside `notes/`:

- `notes/subjects.md` — the map. Every subject, one line each.
- `notes/<subject>/what-i-think.md` — what I think today. Rewrite it whenever
  I learn better.
- `notes/<subject>/sources/` — the write-up of each thing I read or watched,
  with a date. Add and delete freely; **don't rewrite what a source claimed.**
- `notes/where-i-learn/` — which sources are worth my time.
- `notes/thrown-away.md` — one line for everything looked at and binned. Add
  to it every time something gets thrown away, including why.

Inside `work/`:

- `work/projects.md` — the map. Every project, one line each.
- `work/<project>/brief.md` — what it is, where it stands, what's next, what
  done looks like. Rewrite it whenever it stops being true.
- `work/<project>/decisions.md` — what I chose and why, dated, newest on top.
  **Never edit an old entry.** Add a new one saying what changed my mind.
- `work/archive/` — finished and dropped projects. Don't check these for
  going stale; that's the point of them.

## Not everything here finishes

This is a home, not a workplace. Some of it I mean to finish; some I just keep —
a language, a garden, a subject I read for pleasure. Those carry the exact line
`**This one has no end.**` in their `brief.md` or `what-i-think.md`, and then
nothing asks when they'll be done or calls them stale.

So: never push me towards a finish line I didn't ask for — offer the line
instead. And curiosity doesn't have to justify itself; the throw-away rules are
for things I want to *use*. `work/README.md` and `notes/README.md` have the
rest.

## The one rule that makes this worth keeping

**Two files never get rewritten: `sources/` and `decisions.md`.**

Everything else here is my current opinion and can change freely. Those two
are a record of what was claimed and what was chosen, on a date. That's what
lets me trace a belief back to who told me, spot a source that has been wrong
before, and answer "why on earth did I do it that way?" eighteen months
later.

When one turns out to be wrong, add a dated line saying so. Don't quietly
change it. A record you edit is a record you can't trust, and then it's worth
nothing.

Fixing a typo or a dead link is fine. The rule is about not changing **what
was claimed or chosen**.

## What runs on its own

Set up in `.claude/settings.json`, all of it plain shell — no model calls, so
it costs nothing and adds no delay.

- **When a session starts**, the folder is checked and anything that needs
  attention is handed to you. Deal with at most one item, at a natural moment.
  Never dump the list at me.
- **When I say something lasting** — a preference, a habit, my setup, a
  project's state — you get told to consider saving it. Answer my actual
  question first, then offer in one line. Never save without asking, and never
  ask twice about the same thing.
- **After any file in `notes/`, `work/` or `me/` is written**, it gets checked
  for being too long, saying the same thing twice, or making claims with no
  source. Fix it then and there, not later.
- **Before any file is written**, if the target is a write-up or a decision
  log you get told so. Those two are never rewritten. Fixing a typo is fine;
  changing what was claimed or chosen is not.
- **After every reply**, the words are checked against
  `.claude/hooks/plain-words.tsv`. A hit sends the reply back to be said plainly.
No log of the session is ever written. The folder itself is the state, so
the check at the start of the next one reads the truth rather than a saved
copy of it. `/wrapup` only makes sure the folder's own files are true before
the chat closes.

## Working rules

- **Throwing it away is the normal answer.** Most things you read don't
  transfer. A session that keeps nothing is a good session, not a wasted one.
- **Edit before adding.** A file that nearly copies an existing one is worse
  than no file.
- **Say what hasn't been tested.** Most of `notes/` is one person's opinion.
  Label it that way.
- **Don't guess about me.** If `me/` doesn't say, ask — or write your guess
  down and mark it a guess, so it can be corrected.
- **Write down real decisions.** When we pick one way over another on a
  project and the other was reasonable, add an entry to that project's
  `decisions.md` with the reason. Not what we did — why we chose it. About
  one a week is right; more than that is logging activity.
- **This folder only.** Never read, write, or list files in any other OS
  folder, even if I point you at one. If something outside is needed, say so
  and let me copy it in myself. The one exception: `/update-os` reads a fresh,
  never-used download of the template.
- **Log snags.** When this folder's machinery gets in your way, write it down with `/snag` without asking. Never about my own work.

## What's actually proven here

<!-- Blank on purpose. Fills in as you test things and find out what's
     actually true. No marker here — nothing is waiting on you. -->

Nothing yet. Treat everything in `notes/` as a current best guess that shows
its source, not as settled fact.
