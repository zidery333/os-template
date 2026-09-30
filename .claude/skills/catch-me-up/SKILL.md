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

# what moved, and what got binned. With no history to read, the files
# changed this week instead.
bash .claude/scripts/history.sh moved
grep '^| 20' notes/thrown-away.md | tail -5

# whether a newer version is out. The request carries nothing about them, and
# offline or slow it says nothing. The number after the dot counts, so .10
# comes after .9.
mine=$(awk -F'\t' '$1 == "# version" { print $2; exit }' .claude/shipped.tsv 2>/dev/null)
latest=$(curl -fsS --max-time 5 https://api.github.com/repos/zidery333/os-template/releases/latest 2>/dev/null |
  sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' | head -1)
newest=$(printf '%s\n%s\n' "$mine" "$latest" | sort -t. -k1,1 -k2,2n | tail -1)
[ -n "$mine" ] && [ -n "$latest" ] && [ "$newest" = "$latest" ] && [ "$latest" != "$mine" ] &&
  echo "newer version out: $latest"

# everything the folder thinks is worn out. A session start shows the first
# three; this shows the whole list, as /tidy-up does.
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
`TO FILL` goes here. These are the highest-value lines in the folder. If the
check above printed `newer version out`, add one line at the end: "A newer
version of this folder is out — /update-os brings it in."

**Projects** — each active project's "what I'm doing next" line, one line
each, and nothing else. Say plainly if one with an end hasn't moved in three
months; don't dress that up as progress. Never say it about one whose brief
says `**This one has no end.**`

**Half-finished** — write-ups that never got a decision. Flag the ones past
two weeks and ask, one line each, whether to add what it taught or throw it
away.

**Changed** — what moved in `notes/`, `work/` or `me/` this week, in plain
words. "Nothing changed" is a real answer; say it in one line. The
`Set up` save is the folder being made, not news; leave it out.

## Rules

- **Don't summarise a write-up nobody has read yet.** A title is not
  knowledge, and an invented summary is worse than no summary.
- **Don't manufacture urgency.** A quiet week should read as a quiet week.
- If nothing needs doing, say that first and stop.
