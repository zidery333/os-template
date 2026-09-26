#!/usr/bin/env bash
# Runs just before any file is written or edited. If the target is one of the
# two kinds of file that are never supposed to be rewritten, it says so.
#
# Those two — notes/<subject>/sources/ and work/<project>/decisions.md — are
# the whole reason this folder is worth keeping for years. Everything else here
# is a current opinion that can change freely. Those two are a record of what
# was claimed, and what was chosen, on a date.
#
# It warns; it does not block. Appending a dated "this turned out to be wrong"
# line is an edit, and so is fixing a typo or a dead link — both are allowed,
# and both look identical to a script. So this raises its hand and leaves the
# judgment where it belongs.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_python

IN=$(cat)
read -r TOOL FILE <<< "$(printf '%s' "$IN" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print(d.get("tool_name", ""), d.get("tool_input", {}).get("file_path", ""))
except Exception: pass')"

[ -n "${FILE:-}" ] || exit 0
REL="${FILE#"$ROOT"/}"

case "$REL" in
  notes/*/sources/*.md) KIND="a write-up. It records what somebody claimed, on a date." ;;
  work/*/decisions.md|work/*/decisions-older.md) KIND="a decision log. It records what was chosen, and why, on a date." ;;
  *) exit 0 ;;
esac
# Archived work is NOT exempt. An archived decision log is the one people
# actually go back to a year later — "we tried that, here is what happened" —
# so it is the last thing that should be quietly rewritten.
#
# A file that does not exist yet is being created, which is the normal way
# both of these get written.
[ -f "$FILE" ] || exit 0
# A brand new source README is scaffolding, not a record.
case "$REL" in */sources/README.md) exit 0 ;; esac

if [ "${TOOL:-}" = "Write" ]; then
  MSG="About to replace all of $REL. That is $KIND

Replacing it loses what it said, and that is the one thing in this folder that
is not allowed to change. If the claim turned out to be wrong, append a dated
line saying so and leave the rest exactly as written. Only carry on with a
full overwrite if the person has asked for it outright."
else
  MSG="Editing $REL. That is $KIND

Fixing a typo or a dead link is fine. Changing what was claimed or chosen is
not — add a new dated entry instead, and leave the old text alone."
fi

say_to_claude "$MSG" PreToolUse
