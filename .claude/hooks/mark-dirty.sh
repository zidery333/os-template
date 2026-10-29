#!/usr/bin/env bash
# PostToolUse (Write|Edit|NotebookEdit|Bash) — remember that the tree changed,
# cheaply. Bash too: a file made with `cat >` or moved with `mv` changed it as
# much as a Write did, and the list wasn't rebuilt for it (audit, 2026-10-28).
# One touch. No scanning, no output, no measurable cost per edit.
set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
[ -d "$ROOT/.os" ] && : > "$ROOT/.os/.dirty" 2>/dev/null
exit 0
