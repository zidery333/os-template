---
name: wrapup
description: >-
  Ends a session cleanly — makes sure the folder's own files are true about
  what happened today, offers to save it, and says whether it's safe to close.
  Use when the person says they're done, wrapping up, stopping for the day, or
  asks if they can close the chat.
when_to_use: >-
  'wrap up', '/wrapup', 'I'm done', 'that's it for today', 'can I close this',
  'are we done here', 'let's stop'.
---
Wrap up this session.

This is not a diary. Nothing here writes a log of what happened. The folder
is the record, so the job is to make sure its files are true before the chat
closes — then the next session starts from the truth, not from a summary.

## 1. Look back over this session

Only what changed or was said in this chat. Don't read the whole folder.

## 2. Make the files true

- **A project you worked on** — is its `brief.md` still right? The **State**
  and **Next** lines are the ones that go stale in a day. Rewrite them if
  they're wrong. Anything left half-done goes in **Next**, plainly.
- **A real choice made today** — one way picked over another reasonable way.
  If it isn't in that project's `decisions.md`, add a dated entry at the top
  saying why. If you're not sure it counts, ask in one line.
- **Something lasting they said about themselves** that isn't saved yet —
  offer once, in one line. Never save without a yes.
- **A subject you added to** — does its `what-i-think.md` still say what they
  think now?

If all of that is already true, change nothing. A wrap-up that finds nothing
to do worked.

## 3. Save it

If the folder is a git repository with changes, offer to commit them in one
line. On a yes:

```bash
git add -A && git commit -m "<what changed today, in a few words>"
```

## 4. Say

Three lines at most:

1. What got updated, or "nothing needed updating".
2. What's next, from the brief — or what's waiting on them, if anything.
3. "Safe to close." Only if it is. If something is unsaved or broken, say
   that instead.
