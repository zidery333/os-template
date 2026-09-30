#!/usr/bin/env bash
# SessionStart — hand Claude the state of the folder before the first prompt.
# Never blocks, never fails a session: every path exits 0.
set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
[ -x "$ROOT/os" ] || exit 0
[ -f "$ROOT/.os/config.json" ] || exit 0
if brief="$("$ROOT/os" brief --json --quiet 2>/dev/null)" && [ -n "$brief" ]; then
  printf '%s\n' "$brief"
  exit 0
fi
# No Python, or a typo in .os/config.json. Swallowed, it left Claude knowing
# nothing and nobody saying why, so say it in one line.
printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"./os could not run in this folder at the start of this session. Typing ./os in a terminal shows why."}}'
exit 0
