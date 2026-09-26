---
name: tidy-up
description: >-
  Cleans up the OS folder — merges notes that repeat each other, throws out
  what has gone stale, settles projects nobody has touched, and shrinks files
  that grew too long. Use when a notes file passes 250 lines, when two notes
  say the same thing, when a project has sat still for months, or when the
  person asks for a clean-up.
when_to_use: >-
  A notes file is too long, two notes overlap, a project hasn't moved in
  months, there are more active projects than the person can really work on,
  the session-start check says there are too many problems to handle one at
  a time, or the user says the folder is getting messy.
---
Go through the folder and make it smaller. Run this every month or two.

**The goal is fewer files and fewer words, not more.** If this ends with more
than it started with, it went wrong.

**One thing is off limits.** Anything carrying `**This one has no end.**` is
not stale, not a candidate for archiving, and not clutter. It's quiet because
that is what it is like. Tidy the folder around it and leave it alone. The same
goes for a subject somebody keeps because they enjoy it: shorten it if it has
got baggy, never bin it for not being useful.

## 1. Start from what the checks already know

Don't hunt by hand for anything a script has already found:

```bash
# too-long files, repeated headings, orphaned write-ups, stale subjects and
# projects, projects off the map, stray files at the top level
bash -c 'source .claude/hooks/lib.sh && os_rot'

# the same heading twice inside one file
find notes work -name 'what-i-think.md' -o -name 'brief.md' 2>/dev/null \
  | while read -r f; do d=$(grep '^## ' "$f" | sort | uniq -d); \
      [ -n "$d" ] && echo "$f: $d"; done

# headings with nothing under them
find notes -mindepth 2 -maxdepth 2 -name 'what-i-think.md' -exec \
  awk '/^## /{h=$0; getline l; if (l ~ /^$/) {getline l; if (l ~ /^##|^$/) print FILENAME": empty -> "h}}' {} \;

# what each source has really paid out, in the shape who-to-trust.md wants
{ grep -h '^| 20' notes/thrown-away.md 2>/dev/null \
    | awk -F'|' '{gsub(/^ +| +$/,"",$3); print $3 "\tthrown"}'
  find notes -path '*/sources/*' -name '[0-9]*.md' -exec grep -h '^From:' {} + 2>/dev/null \
    | sed 's/^From: *//' | awk '{gsub(/ +$/,""); print $0 "\tkept"}'
} | awk -F'\t' '{ if ($2=="kept") k[$1]++; else t[$1]++; seen[$1]=1 }
     END { for (s in seen)
             printf "%d looked at -> %d kept, %d thrown away\t%s\n", k[s]+t[s], k[s]+0, t[s]+0, s }' \
  | sort -rn
```

`os_rot` is the same list a session start shows the worst three of. This is
the only place the whole thing is visible.

## 2. Then look for what no script can find

- **Two notes that disagree.** Don't just pick one. Work out which source was
  stronger, keep that, and write down that the other was wrong. Knowing a
  source misled you is worth as much as the correction.
- **Padding.** Lists that restate their heading. Sentences that could go with
  nothing lost. A paragraph explaining what the paragraph above already said.
- **Two files that are really one subject**, under names that don't look
  alike. The script catches repeated headings; it can't tell that `postgres`
  and `databases` are the same pile.
- **A belief that quietly stopped being true.** Nothing flags this. Only you
  know you gave up on it in March.

## 3. Check what the files claim against what happened

Read the last month of git history, then:

- Is "what's on my mind lately" in `me/who-i-am.md` still true? Empty is
  a fine and permanent answer — never treat a blank one as a fault.
- Does each `brief.md` still describe the state that project is really in?
- Does `who-to-trust.md` match the recount above? It won't, quite. Fix the
  file, not the count — and look for **one source written two ways**, which
  is what usually makes it wrong. Pick a spelling and go back over the old
  rows.
- A source with a long run of nothing kept is information, not a gap. Say so
  in its section. That is the entire reason for keeping a record.
- Any job done by hand three times or more? That's a skill waiting to be
  written. Say so. Count times it was really done, not words like "weekly"
  in a note. And check it isn't already running on its own — a scheduled
  task, a timer, a skill — before you call it a job done by hand.
- Any correction they had to give you twice? That belongs in `CLAUDE.md`, so
  it never has to be said a third time.

## 4. Rules while you work

- **Never rewrite `notes/<subject>/sources/` or `work/<project>/decisions.md`.**
  You may delete a useless write-up, and you may fix a typo or a dead link.
  Never change what a source claimed or what was chosen — not the wrong ones,
  not the embarrassing ones. Those are the evidence. If one turned out to be
  wrong, add a dated line at the bottom saying so and leave the rest alone.
- **Don't delete a belief for being unproven.** Most of them are. Delete it
  when it's wrong, replaced, or about something that no longer exists.
- **Ask before deleting anything they wrote themselves.** Say what you'd cut
  and why, and wait.

## 5. Report

Files removed, files merged, words cut, and the one thing that needs a
decision only they can make.

If the folder was already in good shape, say so in one line and stop.
Inventing work to look useful is exactly the failure this command exists to
clean up.
