# me/

Who you are. Claude reads this before doing anything for you, so it stops
guessing and stops asking you the same questions every week.

Two files:

```
who-i-am.md   what you do, what you're into, how you want answers written
my-setup.md   your machine, your tools, timers, and your own habits
```

The split between them is worth getting right, because it's easy to get
backwards. `who-i-am.md` is how you want **Claude** to work. `my-setup.md` is
how **you** work — your machine, and under "How I do things", your own habits
at your own craft. If a line would still be true when you stopped using
Claude tomorrow, it belongs in `my-setup.md`.

Projects don't live here. They live in `work/`, one folder each, because a
project has its own state and its own decisions and both change far faster
than you do.

Both ship blank. Run `/setup` and they get filled in from a short
conversation, or just type over the blanks yourself.

## The one rule

**Mark how solid each line is.** In `who-i-am.md`: `(said)` if you told
Claude yourself, `(guessed)` if Claude worked it out from watching you,
`(unsure)` if it came from somewhere and nobody checked. In `my-setup.md`:
`(checked)` if it was really looked at, `(guessed)` if not.

Guesses are useful and cheap to fix. Don't delete them — correct them.

## What doesn't belong here

Passwords, keys, account numbers, anything about your money or your health.
This folder gets copied and committed. Keep it to the boring stuff.

## Keeping it true

The most valuable section is "How I like work delivered" in `who-i-am.md`.
Add a line to it every time something annoys you. It gets more useful over
time than anything else in the folder.

The most likely thing to go stale is "what's on my mind lately". Fix it when
it stops being true — `/tidy-up` checks it against what you've actually been
doing. Leaving it empty is also fine; nothing here requires you to have a
main objective.
