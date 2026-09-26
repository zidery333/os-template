#!/usr/bin/env bash
# Runs right after any file is written or edited. If it is a note in notes/,
# work/ or me/, check it there and then.
#
# This is why there is no cleanup day: a file gets checked at the moment it
# grows, not months later when nobody remembers what is in it.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_python

FILE=$(cat | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print(d.get("tool_input", {}).get("file_path", ""))
except Exception: pass')

[ -n "$FILE" ] || exit 0
[ -f "$FILE" ] || exit 0
# Only notes written in this folder's rooms. A long code file inside a project
# folder is not a note, and a file outside this folder is none of its business.
case "$FILE" in "$ROOT"/*) ;; *) exit 0 ;; esac
REL="${FILE#"$ROOT"/}"
case "$REL" in
  notes/*.md|me/*.md|work/*.md) ;;
  *) exit 0 ;;
esac
case "$REL" in *README.md) exit 0 ;; esac
# Anything already archived is finished. Leave it alone.
case "$REL" in work/archive/*) exit 0 ;; esac
MSGS=""

LINES=$(wc -l < "$FILE" | tr -d ' ')
case "$REL" in
  # A decision log is supposed to grow for years. Telling someone to split it
  # the way you split a notes file would be exactly wrong, so it gets its own
  # limit and its own advice.
  work/*/decisions.md)
    if [ "$LINES" -gt "$BIG_LOG_LINES" ]; then
      MSGS="$REL is now $LINES lines. Don't cut it — move entries older than a year into work/$(basename "$(dirname "$REL")")/decisions-older.md and leave a line at the bottom pointing there."
    fi
    ;;
  # A brief is five lines. Past about thirty it has turned into a diary, and
  # the diary belongs in the decision log.
  work/*/brief.md)
    if [ "$LINES" -gt 30 ]; then
      MSGS="$REL is $LINES lines. A brief is five: what it is, where the work lives, what state, what's next, what done looks like. Move the rest into decisions.md or delete it."
    fi
    ;;
  *)
    if [ "$LINES" -gt "$BIG_FILE_LINES" ]; then
      MSGS="$REL is now $LINES lines, past the $BIG_FILE_LINES-line limit. Before moving on, either split it into two subjects or cut the weakest part. Do not just keep adding."
    fi
    ;;
esac

# The same heading twice in one file means something got written down twice.
# Skipped for decision logs, where two entries can honestly share a heading.
DUP=""
case "$REL" in work/*/decisions.md) ;; *)
  DUP=$(grep '^## ' "$FILE" 2>/dev/null | sort | uniq -d | head -2) ;;
esac
if [ -n "$DUP" ]; then
  MSGS="$MSGS
$REL has the same heading more than once: $(echo "$DUP" | tr '\n' ';') Merge them now."
fi

# A claim with no source and no 'my own thinking' label is a claim from nowhere.
if printf '%s' "$REL" | grep -q '^notes/[^/]*/what-i-think\.md$'; then
  # grep -c already prints 0 when nothing matches, and exits 1 while doing it.
  # Adding '|| echo 0' on the end printed a second 0, so the count came out as
  # "0\n0" and every comparison below blew up.
  HEADS=$(grep -c '^## ' "$FILE" 2>/dev/null); HEADS=${HEADS:-0}
  SRCS=$(grep -c 'sources/\|my own\|I tried\|I tested\|untested' "$FILE" 2>/dev/null); SRCS=${SRCS:-0}
  if [ "$HEADS" -gt 2 ] && [ "$SRCS" -eq 0 ]; then
    MSGS="$MSGS
$REL has $HEADS sections and not one of them says where it came from. Add the source, or mark it as their own thinking."
  fi
fi

[ -n "$MSGS" ] || exit 0
say_to_claude "$(printf '%s' "$MSGS" | sed '/./,$!d')" PostToolUse
