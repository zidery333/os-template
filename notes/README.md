# notes/

What you've learned **from other people**. One folder per subject.

What you're making yourself lives in `work/`, not here. The split matters:
notes are opinions that should change when you learn better, and projects
have their own state and their own decisions.

Empty on purpose. Run `/new-subject <name>` to start one, or just share
something and it gets filed under the subject that fits, or a new one.

## What a subject folder looks like

```
notes/
  thrown-away.md          <- everything you looked at and binned
  subjects.md             <- the map. every subject, one line each.
  where-i-learn/          <- which sources are worth your time
  sleep/                  <- a subject
    what-i-think.md       <- what you believe today. Rewrite freely.
    sources/              <- the write-ups. Add, delete, don't rewrite.
      2026-02-04-morning-light.md
      2026-01-19-caffeine-timing.md
  photography/            <- another subject, same shape
    what-i-think.md
    sources/
```

Everything about one subject is in one folder. Open `notes/sleep/` and it's
all there.

## The two kinds of file

**`what-i-think.md` — what you think today.**
Rewrite it whenever you learn better. It's allowed to change completely. It
should read like you explaining the subject to a friend, not a pile of quotes.

**`sources/YYYY-MM-DD-name.md` — the write-up of one thing you read or watched.**
Add as many as you like, delete any you want — but don't go back and rewrite
one. It records what somebody claimed on a day, not what you now believe.

That split is the whole trick. When you change your mind, the write-up that
convinced you in the first place stays exactly as it was. So you can always
ask "where did I get this from?" and get a real answer — including when the
answer is "some guy on YouTube, once, with no evidence".

Here's what it buys you. A video tells you something in March. In June you
find out it was wrong. If you go back and edit the March file, you've quietly
erased the only evidence that this source told you something wrong — and that
evidence is exactly what the scorecard in `where-i-learn/` runs on. Do that a
few times and you can't tell a channel that's been right for a year from one
that's been wrong all along.

So when a write-up turns out to be wrong, don't fix it. Either add a dated
line at the bottom — `2026-06-14: this turned out to be wrong` — or just
update `what-i-think.md` and say there what changed your mind.

Typos, broken links and formatting are fine to fix. The rule is about not
changing **what was claimed**, not about keeping the file ugly.

## What a finished one actually looks like

Made up, but this is the shape. Notice how short it is, and that every line
says where it came from.

```markdown
# Sleep

What this covers: what actually changes how well I sleep.

## Light in the morning beats everything else

Ten minutes of daylight within an hour of waking. It sets the timer that
decides when you get sleepy that night — like winding a clock in the morning
so the alarm goes off at the right time.
*They measured it. Sleep researcher, 4 Feb — [write-up](sources/2026-02-04-morning-light.md).
Strongest thing in here.*

## Caffeine has a long tail

Half of it is still in you six hours later. A 3pm coffee is a 9pm coffee.
*They said so, twice, from two different people. Never tested it on myself.
[write-up](sources/2026-01-19-caffeine-timing.md)*

## Tried and it did nothing for me

- Magnesium before bed. Two weeks, no difference I could notice.
  *Everyone recommends this. It just didn't do anything.*
```

Three things that example gets right:

- **It's short.** Three ideas, not thirty.
- **Every claim says how sure it is**, and the weak ones are marked weak.
- **It records what didn't work.** That section is often the most valuable
  part, and it's the part everyone forgets to write.

## What a good subject file looks like

- Short. Over 250 lines and something checks it and tells you to split it.
- Every claim says how sure you are, in these exact four words:
  **they said so**, **they showed it**, **they measured it**, or
  **from the source**. Same four everywhere in the folder — see
  `.claude/guides/how-to-add-stuff.md` for what each one means.
- Every claim links to the write-up in `sources/` that produced it.
- Plain words. If you can't say it plainly, you don't understand it yet —
  which is itself worth writing down.

## Subjects you keep for the pleasure of it

Those rules are for things you want to *use* — where being wrong costs you
something, so you'd better know where a belief came from.

Plenty of what you learn isn't like that. Birds. A composer. How volcanoes
work. You are never going to act on it and that was never the point.

Two things change for a subject like that:

- **Labels are for claims, not for tastes.** "Half of caffeine is still in you
  six hours later" needs to say how sure you are. "This is the best thing I
  read all year" does not, and stamping *they said so* on it is silly.
- **Nothing has to be useful.** The throw-away rules in
  `.claude/guides/how-to-add-stuff.md` exist to stop a working folder filling
  with half-true advice. They are not there to make you justify being
  interested in something.

If the folder ever asks whether a subject is finished, and the answer is that
you just like it, put this line at the top of `what-i-think.md`:

```markdown
**This one has no end.**
```

Then it stops asking. Same line `work/` uses, same effect.

## What doesn't belong here

- Notes on how to click through some app. That's an interface, not an idea.
- A second file that says almost what an existing file says. Edit the first.
