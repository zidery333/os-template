#!/usr/bin/env bash
# Runs after Claude finishes a reply. Reads the reply back and, if it used a
# word from .claude/hooks/plain-words.tsv, sends it back to be said plainly.
#
# This is the only thing here that actually enforces plain speaking. A rule in
# a file is a suggestion; this is a check.
#
# It never checks its own rewrite, so it cannot get stuck in a loop.
# To turn it off, delete this hook from .claude/settings.json.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_python

[ "${OS_PLAIN_CHECK:-on}" = "off" ] && exit 0

LIST="$ROOT/.claude/hooks/plain-words.tsv"
[ -f "$LIST" ] || exit 0

IN=$(cat)
# Same rule as the other hooks: take whichever field name this version sends,
# rather than one and a silent failure. stop_hook_active means this reply is
# already the rewrite; checking it again could bounce forever.
MSG=$(printf '%s' "$IN" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    if d.get("stop_hook_active") is True: sys.exit(0)
    for k in ("last_assistant_message", "assistant_message", "message"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            print(v); break
except Exception: pass')

[ -n "$MSG" ] || exit 0

# Strip out anything being quoted rather than said: code spans, fenced blocks,
# and text in quote marks. Naming a word to talk about it is not using it, and
# without this the check fires on its own word list.
LOWER=$(printf '%s' "$MSG" | python3 -c '
import re, sys
t = sys.stdin.read()
t = re.sub(r"```.*?```", " ", t, flags=re.S)   # fenced code
t = re.sub(r"`[^`]*`", " ", t)                 # inline code
t = re.sub(r"\"[^\"]{0,200}\"", " ", t)         # double-quoted
t = re.sub(r"[\u201c][^\u201d]{0,200}[\u201d]", " ", t)  # curly quotes
t = re.sub(r"^\s*[|>].*$", " ", t, flags=re.M)  # table rows and quotes. Bullets are read.
sys.stdout.write(t.lower())
')
HITS=""
while IFS=$'\t' read -r WORD PLAIN; do
  case "${WORD:-}" in ''|'#'*) continue ;; esac
  [ -n "${PLAIN:-}" ] || continue
  # Whole words only, so 'use' inside 'because' is not a hit.
  if printf '%s' "$LOWER" | grep -qE "(^|[^a-z])${WORD}([^a-z]|$)"; then
    HITS="$HITS
  \"$WORD\" -> say \"$PLAIN\""
  fi
done < "$LIST"

[ -n "$HITS" ] || exit 0

REASON="That reply used words from this folder's plain-words list. Say it again, plainly:
$HITS

Rewrite only the sentences with those words in them. Keep everything else the
same, keep it the same length or shorter, and do not mention this check or
apologise — just say the thing properly."

# Feedback, not a block: Claude Code shows a block as a hook error, after an
# answer that was fine.
printf '%s' "$REASON" | python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":sys.stdin.read()}}))'
