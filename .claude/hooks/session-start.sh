#!/usr/bin/env bash
# Runs when a session starts. Tells Claude what state the folder is in.
#
# This is what makes tidying happen without anyone asking for it: every session
# opens already knowing what is messy.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# There used to be a second hook that wrote this same list down when a session
# ended, for this one to read back. It was pure duplication — both ran the same
# check over the same folder, so every item got announced twice, and if anything
# had been fixed in between the saved copy was the wrong one. The folder is the
# state. Reading it now is always right.
HEALTH="$(os_health)"
[ -n "$HEALTH" ] || exit 0
OUT="The OS folder needs attention:
$HEALTH
"

say_to_claude "$OUT
How to use this: don't dump it at the user or fix it all at once. Deal with at
most one item, at a natural moment, in one line. If they are busy with
something else, stay quiet and let it wait." "SessionStart"
