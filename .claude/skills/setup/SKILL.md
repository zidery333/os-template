---
name: setup
description: >-
  First-time setup for this OS folder. Interviews the user and fills in the
  blank files.
when_to_use: >-
  Only when the user types /setup.
disable-model-invocation: true
---
Set this folder up for the person in front of you. It ships blank; your job
is to fill it in from a conversation, not from guesses.

Budget: about fifteen minutes of their time. Do not exceed it.

## Before you ask anything

Say what's about to happen, in three lines or fewer. Something like: "I'll
ask you about seven questions, then fill in the blank files. You can change
any answer later by editing the file. Nothing here is permanent."

**Then check for `me/setup-answers.md`, before any other check.** If it's
there, an earlier setup stopped halfway. Read it, say in one line how far it
got ("We got through question 3 last time — carrying on from 4"), and carry
on from the first question it doesn't answer. Don't ask anything again that
it already holds. If it answers every question, it stopped while writing the
files: go straight to writing them.

If it isn't there, check whether setup has already run. It hasn't if
`me/who-i-am.md` and `CLAUDE.md` both still have `TO FILL` in them — the
same test the folder check uses. Otherwise it has: stop and ask whether they
want to redo it or just change one part. Don't overwrite work.

## Rules for the interview

- **One question at a time.** A list of seven questions gets one answer.
- **Offer choices where choices exist.** Use the multiple-choice tool for
  anything with obvious options. Typing is work; picking is not.
- **Plain words.** A 13-year-old should follow every question.
- **Never ask what you can check.** Their operating system, what's installed,
  their git details — look those up yourself and confirm in one line.
- **Accept "I don't know".** Write `TO FILL — didn't know yet` and move on.
  An honest blank beats an invented answer, and they'll fill it in later when
  it starts to matter.
- **Never ask for a password, key, or anything private.** This folder gets
  copied and committed.
- **Write each answer down before asking the next question.** Add it to
  `me/setup-answers.md` — make the file with the first answer — under the
  question's number, in their words. A chat can crash or close at any
  moment, and fifteen minutes of answers held only in the chat are gone with
  it. The file is what counts, not your memory of the chat. If the write
  fails, say so and stop; don't keep asking questions nothing is saving.
- **Ignore nudges to save something while the interview runs.** Everything
  they tell you is already going into `me/setup-answers.md`, and from there
  into the real files at the end.

## What to ask

**1. What do you spend your time on?** One or two lines. Not a job title
unless they offer one — this is a home, and "two small children and I'm
teaching myself piano" is a better answer than most job titles. If they name
two things, ask whether the two overlap.

**2. What are you into, for no particular reason?** Birds, a composer, how
bridges stay up, a country you keep reading about. Nothing has to come of it.

This is question two on purpose. Ask about somebody's projects first and the
folder spends the rest of its life acting like the projects are the point.
They are not — this is a home. Answers here go under "What I'm into" in
`me/who-i-am.md`, and any subject that grows out of one gets the exact line
`**This one has no end.**` at the top, so nothing ever asks whether they have
finished with it.

Blank is fine and common. Ask once, lightly, and move on — this is an
invitation, not a form.

**3. Is there anything you're making, or keeping up with?** Anything counts —
code, a book, a business, a shed, a language, a garden. **"No" is a complete
answer and a common one.** Don't talk anybody into a project so the folder
has something in it; an empty `work/` is honest.

If there is something, for each one: what it is in one sentence, where the
real work lives, what state it's really in, and the one next thing to do.
"Where it lives" can be anything — a folder on this computer, an app, a
paper notebook, the back garden. If it's a folder, get the path: a blank
there costs them a question at the start of every future conversation.

**Then ask the question that decides the shape: is this one you want to
finish, or one you'll just keep doing?** For anything they'll just keep, put
the exact line `**This one has no end.**` in its brief instead of a finish
line, and don't ask what done looks like. For the rest, ask. Some things are
both — a business they mean to keep running, with a first goal like "a stall
at the market by spring". Those get the first goal as their finish line. This
one question is what stops the folder feeling like a job by week three.

**Go deep on one, and one only** — whichever they sound most alive about, and
ask which if it isn't obvious. Five things about three projects is fifteen
answers inside a single question and it will eat the whole fifteen minutes. For
the others take a name and a sentence, write `TO FILL` on the rest, and say
they can finish it next time they touch that project. **Except the ones with
no end:** those get only "What it is" and the no-end line — no `TO FILL`,
because nothing about a garden is waiting to be answered, and the folder
check would ask about it every session. Past three that they mean to finish
(don't count the no-end ones), ask which are real and put the rest under
Ideas with no folder. Nothing at all is a fine answer — don't invent a
project to fill the folder.

**4. How do you want answers?** Two picks, one after the other. How long:
very short / normal / full detail. Then: explain your thinking / just do it
and tell me it's done / ask me each time. Then the follow-up that actually
matters: *what makes you stop reading?*

**5. When something goes wrong partway through a job, when do you want to
hear?** Offer: straight away / at the end / only if it stops the job.

**6. What subjects do you want to keep notes on?** Two or three. These become
files in `notes/`. Fewer is better — they can add more any time.

Say this out loud when you ask, because people assume the opposite: **this is
a starting point, not a fence.** `/learn` will take anything they hand it,
whether or not it has anything to do with what they answered here, and it
makes the subject itself when nothing fits. Nothing they say now closes
anything off later.

**Anything they named in question 2 belongs here too**, if they want notes on
it. A subject you read for pleasure is as real a subject as one you need. If
it's already a project from question 3, say how the two split: the notes are
what they learn from others, the doing lives in `work/`.

**7. Where do you learn from now?** Blogs, docs, channels, people. Get links
if they have them. Say plainly that anything they name gets tracked, not
trusted — it earns a record over several goes.

## What to check without asking

Run these and report the findings in a single line, not a wall:

```bash
sw_vers 2>/dev/null || uname -a
pwd
# A Mac without Apple's developer tools has only stand-ins for git and
# python3 in /usr/bin, and running one pops up an install box.
nodev=; sw_vers >/dev/null 2>&1 && ! xcode-select -p >/dev/null 2>&1 && nodev=1
if [ -n "$nodev" ]; then echo 'developer tools: missing'
else git config user.name; git config user.email; fi
for t in yt-dlp gh node python3; do
  p=$(command -v $t) || p='not installed'
  [ -n "$nodev" ] && [ "$p" = "/usr/bin/$t" ] && p='not installed'
  printf '%s: %s\n' "$t" "$p"
done
```

Report which are missing as well as which are there. "yt-dlp: not installed"
is the useful half — it's what `/learn` uses to read a video someone hands
you. If it's missing, say `/learn` can still take a pasted transcript. Don't
push an install.

If it says `developer tools: missing`, git and python3 aren't really there,
and most of this folder's checks stay off until they are. Write
`developer tools missing — run xcode-select --install` into
`me/my-setup.md`, and say that in the one-line report. Leave git's name and
email until the tools are in: running git before then pops up the same box.

**If either `git config` line ran and printed nothing,** git doesn't know their name
and email. On many machines, Linux above all, every save with git then fails,
the nightly one and `/wrapup`'s included. Give them the two lines to run, with
their own name and email in: `git config --global user.name "Their Name"` and
`git config --global user.email them@example.com`.

**Then look at where the folder is.** On a Mac (`sw_vers` printed a
version), if `pwd` is inside `~/Downloads`, `~/Documents` or `~/Desktop`,
say so in one line: macOS blocks timed jobs from reading those, so anything
on a timer — the daily commit — fails silently every day. Moving the folder
somewhere plain like `~/os` fixes it. Tell them; don't move it yourself.

**And note anything installed somewhere odd** — a tool under
`/Library/Frameworks/...` or `~/Library/Python/...` won't be found by a timed
job at all. Write the full path into `me/my-setup.md`.

Anything you find goes into `me/my-setup.md` marked `(checked)`. Anything you
inferred goes in marked `(guessed)`.

## Then write the files

Write all of these from `me/setup-answers.md` before you say anything about
being done. Once every one is written, check each answer in that file landed
somewhere, then delete it. It was only there so a crash couldn't lose
anything, and left behind it would make the next `/setup` think it had
stopped halfway.

- **`me/who-i-am.md`** — from answers 1, 2, 4 and 5. Answers 4 and 5 both go
  under "How I like work delivered". Answer 2 goes under
  "What I'm into", which carries no marker: leave it genuinely empty if they
  had nothing, rather than writing something in to fill the space.
  Each blank is a single
  `TO FILL` line with a comment under it: replace the line, and drop the
  comment unless it says to keep it. Leave the "things I do over and over"
  section alone; it fills in later.
  **Never write `TO FILL` into a section like that.** The marker means
  someone has to answer something. Sections that fill in on their own must
  not carry it, or every session from now on reports the folder as unfinished
  when it isn't.
- **`me/my-setup.md`** — from what you checked, plus anything they mentioned.
- **`work/<project>/brief.md`** — one per project from answer 3, in the
  shape shown in `.claude/skills/new-project/SKILL.md`. Plus an empty `work/<project>/decisions.md`
  holding only its heading. If they aren't making anything yet, create no
  folders at all — an empty `work/` is honest and a fake project stub is not.
- **`work/projects.md`** — one line per project under **Active**, and a line
  under **Ideas** for anything they mentioned but aren't actually doing.
  Delete the "Nothing yet." line under a heading once it has a real line.
  Same reason as the subject map below: a project that isn't on the map gets
  reported as a fault at their next session.
- **`CLAUDE.md`** — the "How to talk to me" list. Keep the default lines
  unless an answer goes against one; change those, and add anything from
  answers 4 and 5 in their own words. Delete the `TO FILL` comment. Leave the
  rest of the file alone.
- **`notes/<subject>/what-i-think.md`** — one per subject from answer 6, each a stub that
  says what the subject covers and nothing more. Plus the matching folder
  `notes/<subject>/sources/` holding a short README that points at
  `notes/README.md` for the rules — the same one `/new-subject` writes.
- **`notes/subjects.md`** — one line for every subject you just made, in the
  shape already shown in that file:

  ```markdown
  - [sleep](sleep/what-i-think.md) — what actually changes how well I sleep.
  ```

  Delete the "Nothing yet." line once you have added the first one. **Do not
  skip this.** The folder check looks for every subject on this map, so a
  subject you made but did not list gets reported as a fault at the start of
  their very next session — which is a rotten first impression of a folder
  that is working fine.
- **`notes/where-i-learn/who-to-trust.md`** — official docs and blogs for
  whatever they work on in the top table; you can suggest these yourself,
  they may not know them. But only for tools they have already chosen. If
  the choice is still open — which blockchain, which language, which shop
  platform — leave it out and name the choice as the thing still to decide.
  A guess written here looks like their decision. Everything else from
  answer 7 goes under "no record yet". Delete a "Nothing yet." line once
  something goes in its place; with nothing to add, leave it as it is.
  **Do not write a single word about how good any of them is.** You
  don't know yet, and a guess written on day one gets read as fact in a
  month.

## Then start a history

Once `me/setup-answers.md` is gone, start a history for the folder, without
asking, so what gets saved at the end of a session can be taken back.
`/wrapup` and the nightly save both add to it. Put their name in where it
says `Their Name`, or leave it out if they didn't give one; it is used only
when git has no name set.

```bash
bash .claude/scripts/history.sh start "Their Name"
```

What it printed decides the one line you add to the finish below:

- `history: started` — "I also started a history for this folder, so what
  gets saved at the end of a session can be taken back."
- `history: the template's own` — they downloaded it with `git clone`, so
  their notes would go into the template's own history. Say so in one line
  and give them the command to start fresh; don't run it yourself:
  `rm -rf .git && bash .claude/scripts/history.sh start`
- `history: couldn't start` — say that in one line, with the reason it
  gave.
- Anything else — say nothing about it.

## Then finish

Say three things, in under ten lines total:

1. What got written, as a short list of file names.
2. The one thing still worth filling in, and why it matters. Usually where
   the work lives, or what "done" looks like.
3. What to do next: `/learn <url>` to keep something you read,
   `/new-project <name>` to start making something, `/catch-me-up` to see
   what's waiting.

Then stop. Don't summarise the interview back at them.
