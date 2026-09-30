---
name: tidy
description: The weekly clean-up pass — stale projects, duplicates, unfiled items, things ready to put away, and anything actually broken. Use when the user says weekly review, start the week, plan my week, what should I do this week, clean house, tidy up, what's rotting, is my folder ok, check the system, or fix the folder.
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

- **Waiting to be filed** → `./os sort`, now.
- **Wasn't sure where these went** → read each one. Really something to do:
  `./os push <name>` makes it work. Right where it is: delete the `flags:` line
  at its top, and it stops being listed here.
- **Gone quiet** → one question: is this still real? Still real gets a next action
  doable this week; real but not being chased gets `./os hold <name>` — the common
  answer, and the easy one to miss; over gets `./os close <name>`.
- **Keeping level** → not a problem list. Read it to ask whether the standard is
  still being met, and act only if it isn't.
- **Marked done but still in Work** → `./os close <name>`. This is the most common
  reason a folder starts to feel heavy.
- **Doubles** → read both. Same thing? Merge into the older one, which has the
  inbound links, then close the newer. Say which one survived.
- **Done by hand every time** → the highest-value line on the page and the
  easiest to skip. Offer *one*: say the steps back in a sentence, and if that's
  right, write the skill while they're in front of you (`/make-skill`, or
  `./os new skill "<name>"`). One good skill beats three half-written ones.
- **Broken** → `./os check --fix` handles the mechanical. Two things sharing a
  name: keep the older, give the newer a name that says what it actually is. A
  skill with no `description:`: write one naming the trigger words. A broken
  link: relink to where the thing sits now.
- **Snags** → the folder's own faults, not their job to fix. Offer
  `./os snag --export` once so they have a page to hand over. Don't read the list out.

Finish with `./os check --fix`, then read every pushing item's `## Next action`
and name the three to start on this week, in order, with a clause each on why.
Three, not five. That last part is why they came.

## Rules

- Never put anything away or merge anything without an explicit yes. Deleting is
  never on the table.
- Batch your questions. One message with six decisions beats six messages.
- Fix causes, not symptoms: things landing in the wrong place repeatedly means
  `.os/words.json` is missing the words they actually use — add them.
- Held work is not on the three and is not mentioned unless it has a date due.
- Finish with the one thing they should change about how they use this.

## Done when

Nothing's waiting, nothing's broken, everything still being pushed has a next
action from this week, and they have three named things to start on.
