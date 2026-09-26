---
name: catch-me-up
description: >-
  Short catch-up on the OS folder — what is waiting, where each project got
  to, what changed this week, and what needs a decision only the user can
  make.
when_to_use: >-
  The user says 'catch me up', 'what's new', 'where were we', or opens a
  session asking what needs doing.
---
Put together today's catch-up. **Under 20 lines.** A catch-up that isn't
brief has failed — leave a section out rather than padding it.

## Gather

```bash
# what only they can decide
grep -rl "TO FILL" me/ CLAUDE.md notes/ work/ 2>/dev/null

# where each project got to
find work -mindepth 2 -maxdepth 2 -name brief.md ! -path 'work/archive/*' \
  -exec grep -H '^\*\*Next:\*\*' {} + 2>/dev/null

# half-finished: write-ups nothing links to, and whether they have gone stale.
# Uses find -mtime rather than date arithmetic, because stat spells its flags
# differently on a Mac and on Linux.
find notes -path '*/sources/*' -name '[0-9]*.md' 2>/dev/null | while read -r f; do
  b="notes/$(basename "$(dirname "$(dirname "$f")")")/what-i-think.md"
  grep -qF "$(basename "$f")" "$b" 2>/dev/null && continue
  if [ -n "$(find "$f" -mtime +14 2>/dev/null)" ]
    then echo "over two weeks  $f"
    else echo "recent          $f"
  fi
done

# what moved, and what got binned
git log --oneline --since="7 days ago" -- notes/ work/ me/ CLAUDE.md
grep '^| 20' notes/thrown-away.md | tail -5

# everything the folder thinks is worn out. A session start shows the worst
# three; this is the only place the whole list is visible.
bash -c 'source .claude/hooks/lib.sh && os_rot'
```

A write-up nothing links to is one that never got a decision. That's the
question worth asking — a plain list of every write-up would be mostly
finished ones.

## Write

Four sections, in this order, because the first thing they read should be the
thing only they can do. Leave out any that's empty — don't write "nothing
here".

**Needs you** — decisions nobody else can make. Anything still saying
`TO FILL` goes here. These are the highest-value lines in the folder.

**Projects** — each active project's "what I'm doing next" line, one line
each, and nothing else. Say plainly if one hasn't moved in a month; don't
dress that up as progress.

**Half-finished** — write-ups that never got a decision. Flag the ones past
two weeks: by the rules in `.claude/guides/how-to-add-stuff.md` that's a
throw-away, not a backlog.

**Changed** — what moved in `notes/`, `work/` or `me/` this week, in plain
words. "Nothing changed" is a real answer; say it in one line. In the first
week the log still holds the commits that built the folder itself — that
isn't news, skip it.

## Rules

- **Don't summarise a write-up nobody has read yet.** A title is not
  knowledge, and an invented summary is worse than no summary.
- **Don't manufacture urgency.** A quiet week should read as a quiet week.
- If nothing needs doing, say that first and stop.
