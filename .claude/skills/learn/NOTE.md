# The note to write

Make it a note outright rather than saving prose and hoping — a method full of
numbered steps reads as *work* to the sorter:

```bash
./os new learning "How <the craft> is actually done" --domain <the subject's own domain>
```

`learning`, not `note`: it is still a note and still lands in `Notes/`, but it
comes out already carrying the shape below instead of a blank one.

**The domain is the subject's, not `learning`.** Meta ads is `marketing`, squat
form is `personal`, Postgres indexes is `engineering`. Put every learned note in
`learning` and the domain stops telling `./os find` anything.
`./os words` with no arguments lists the ones this folder knows.

## The shape

The headings are already in the file, each with a comment saying what belongs
under it. Fill them in and delete the comment as you go; delete a heading only
where the note genuinely has nothing for it (`## Kit` is the usual one).
`## Who to trust` is the exception: it starts empty, so leave it and its comment.

Leave the `---` block at the top alone. Don't improvise headings and don't nest
the method underneath something else — the numbered steps are the point of the
note and they belong at the top level where they can be scanned. If you are
looking at `## What it says`, you made a plain note: `./os undo` and start again
with `./os new learning`.

Cite a `[MM:SS]` for every claim. Cite where the claim *starts*: sentences run
across a stamp, so the line you found it on is often its second half.

## Topping up later

Same subject again? Append a dated section under `## The method` and extend
`## Sources`. Never rewrite what's there.

`## Who to trust` fills up from what happens when they try the advice, one
dated line each time, and an old line is never changed:

```
- 2026-10-04 — Sam Lee: clamp the glue-up for 30 minutes [04:12]. Tried it
  twice; both joints came apart the next day. Check his times elsewhere first.
```
