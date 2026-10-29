---
name: tidy
description: The weekly clean-up pass — stale projects, duplicates, unfiled items, things ready to put away, and anything actually broken. Use when the user says weekly review, start the week, plan my week, clean house, tidy up, what's rotting, is my folder ok, check the system, or fix the folder. For "what should I work on" alone, that is /catchup.
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Edit
---

# Tidy

A folder like this dies two ways: things stop going in, or nothing is ever looked
at again. This fixes the second one. Once a week is plenty.

```bash
./os tidy        # what has gone stale, doubled up, or is done and still sitting
./os check       # what is actually broken
./os snag        # what the folder itself got wrong
```

For each thing on those pages, **propose the specific action and then take it**
once they agree. Describing the problem back to them is not the job.

## What each finding wants

- **on the go** → what they're pushing on. Nothing to fix here; it's the list the
  three for this week come from. One with an empty `## Next action` gets a
  question, not a made-up step.
- **dropped in, not filed yet** → `./os sort`, now.
- **I wasn't sure where these went** → read each one. Really something to do:
  `./os push <name>` makes it work. Right where it is: delete the `flags:` line
  at its top, and it stops being listed here.
- **not touched in a while**, and **gone quiet — still pushing these?** → one question: is this still real? Still real gets a next action
  doable this week; real but not being chased gets `./os hold <name>` — the common
  answer, and the easy one to miss; over gets `./os close <name>`.
- **keeping level** → not a problem list. Read it to ask whether the standard is
  still being met, and act only if it isn't.
- **STILL KEEPING THESE UP?** → held work nobody has touched in a long time. Ask
  once, in the batch: still kept up gets a line in its `## Log`; over gets
  `./os close <name>` on their yes.
- **marked done but still sitting in Work** → `./os close <name>`. This is the most common
  reason a folder starts to feel heavy.
- **might be the same thing twice** → read both. Same thing? Merge into the older one, which has the
  inbound links, then close the newer. Say which one survived.
- **DONE BY HAND EVERY TIME?** → the highest-value line on the page and the
  easiest to skip. It is found by words like "every week" in a note, so check
  two things first. Is something already doing it on its own — a scheduled
  task, a timer, a skill? Then it's no job for a skill; say so in a line. And
  how often is it really done by hand? Count the times in its Log or its dates.
  A note usually has neither, and no record is not the same as rarely: when
  nothing shows it, ask how often they really do it. Done by hand, and often?
  Offer *one*: say the steps back in a sentence, and if that's right, write the
  skill while they're in front of you (`/make-skill`, or
  `./os new skill "<name>"`). One good skill beats three half-written ones.
- **CROWDED — COULD BE SPLIT INTO SUBJECT FOLDERS** → offer `./os sort --regroup`
  to show the plan. Nothing moves until they say yes to it.
- **SOURCES /LEARN HAS KEPT** → safe to drop, since they are fetched again when
  needed. Mention it once; `./os learn --cached` lists them.
- **Broken**, from `./os check` → `./os check --fix` handles the mechanical. Two things sharing a
  name in different folders is fine, not broken: a command asks which one, so
  leave them. A skill with no `description:`: write one naming the trigger
  words. A note with "no subject set": offer the subject that fits, and set its
  `domain:` on a yes. A broken link: relink to where the thing sits now.
- **Snags**, from `./os snag` → the folder's own faults, not their job to fix. Offer
  `./os snag --export` once so they have a page to hand over, and tell them to
  read it before they send it. Don't read the list out.

Finish with `./os check --fix`, then read every pushing item's `## Next action`
and name the three to start on this week, in order, with a clause each on why.
Fewer than three with a next action? Name those, and ask about the rest.
Three, not five. That last part is why they came.

## Rules

- Never put anything away or merge anything without an explicit yes. Deleting is
  never on the table.
- Batch your questions. One message with six decisions beats six messages.
- Fix causes, not symptoms: things landing in the wrong place repeatedly means
  the folder is missing the words they actually use — add them with
  `./os words <subject> "<word>"`.
- Held work is not on the three and is not mentioned unless it has a date due.
- Finish with the one thing they should change about how they use this.

## Done when

Nothing's waiting, nothing's broken, everything still being pushed has a next
action from this week, and they have three named things to start on.
