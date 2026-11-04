---
name: subchat-chain
description: Run a big job as a main chat that hands each step to its own subchat, one after another, then checks the work and reports back. Use when a job has several steps, when the user says split this up, use subchats, run it as a chain, do it step by step in separate chats, or when the work is too big for one chat.
argument-hint: [the job]
---

# Subchat chain

A subchat is a separate Claude Code session that the main chat starts and hands
one job to, like sending a helper into another room with a note. The main chat
talks with the person: it asks, plans, starts subchats, checks what they did and
reports back. It doesn't do the big building work itself. Subchats do that.

## Do this

1. **Interview the person.** Ask what they want, what "done" looks like, and
   what they don't want touched. One question at a time. Stop when you could
   explain the job back to them in three sentences.
2. **Plan the steps.** Each step is one job with its own files and its own
   "done". Write down which folder each step works in. Steps in the same folder
   must run one at a time. Steps in different folders may run side by side.
   Show the plan to the person and get a yes before starting anything.
3. **Start one subchat per step** with `start_session`, using the brief below.
   Never put two steps in one subchat, even small ones. A subchat that does
   one thing is easy to check. One that does five is not.
4. **Chain them, so the person never clicks "start" on each one.** Pick one way:
   - **The main chat starts the next one.** When a subchat finishes, the app
     sends the main chat a note. The main chat checks the work (step 5), then
     starts or nudges the next subchat.
   - **Each subchat waits for a flag file.** Start them all at once. Step 2's
     brief says: wait in the background until `step-1.done` exists, then begin.
     Each subchat writes its own `step-N.done` as the very last thing, after its
     work is saved. Keep flag files in the shared notes folder the app gives
     the chain (or the scratchpad), never inside the project, where `./os`
     would try to file them.
5. **Check each result before you trust it.** A subchat's report is its own
   story, not proof. Open the files it says it changed. Look at the commit or
   checkpoint (`git show --stat <commit>`). Run the project's own check, like
   `./os check`. If something is wrong, send that same subchat a follow-up.
   Don't start a new one.
6. **Send the screenshots.** Each time a step finishes, send its screenshots to
   the person with `SendUserFile` and a one-line caption, combined picture
   first. If the step said `no screenshot: nothing visible changed`, say that
   in one line instead.
7. **When every step is done**, start one more subchat to look for bugs in
   everything the chain changed. Check its findings the same way. Then send the
   person one clean summary: what was built, what was checked, what is still
   broken or untested, and where each piece lives.

## The one thing that trips this up

A turn the app started on its own (a "subchat finished" note, not the person
typing) can't start a session in another folder. Plan for it:
- Start every subchat the chain will need while the person is still talking to you,
  with later steps waiting on flag files.
- Or reuse an idle subchat that is already there (see below).
- Or tell the person "say go and I'll start the next step", and stop.

## Reusing a subchat for a new job

Never stack a new job on top of an old chat. It still remembers the old job and
mixes them up. Instead, in this order:
1. Wait until it is idle, then clear it with `clear_session`.
2. Send it the full new brief, as if it had never seen anything.
3. Rename it with `set_session_title` to the new job's name.

## The brief

Every brief stands alone. The subchat may not see your rules or this chat, so
everything it needs goes in the note. Copy this and fill it in:

```
Job: <one sentence: what to make or fix>.

Background: <why, and anything it needs to know that it can't find in the files>.

Files and area: <the folder, the files to change, the files not to touch>.

Wait first: <only for a chained step: wait in the background until <path>/step-<N-1>.done exists, then begin. Leave this out otherwise.>

Rules:
1. You are a subchat. Do only this step. Start no more subchats unless this brief says so.
2. If you changed anything you can see, take 1–3 screenshots with the
   screenshot-grid skill, combined into one labelled picture, and list the full
   paths, combined picture first. If nothing visible changed, say
   "no screenshot: nothing visible changed".

Done means: <the exact things that must be true, e.g. tests pass, ./os check clean, checkpoint run>.
When done: <for a chained step: write <path>/step-<N>.done as the very last thing>.
End with a short message: what you did, the files and commit, the screenshot paths.
```

Rule 2 uses the screenshot-grid skill. If this folder doesn't have it, type
`./os addon screenshot-grid` to add it, or keep the rule and drop the skill's name.

## Rules

- One step, one subchat.
- Two subchats never work in the same folder at the same time.
- The main chat plans, checks and reports. It doesn't build.
- Look at the files and commits, not just the report.
- Say who pushes or releases in the brief. If it doesn't say, the subchat doesn't.

## Done when

Every step was checked by looking at the work itself, every step's screenshots
(or its "nothing visible changed" line) reached the person, the bug-review
subchat finished, and the person got one summary that says plainly what is
still broken or untested.
