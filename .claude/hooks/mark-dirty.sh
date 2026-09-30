#!/usr/bin/env bash
# PostToolUse (Write|Edit) — remember that the tree changed, cheaply.
# One touch. No scanning, no output, no measurable cost per edit.
set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
[ -d "$ROOT/.os" ] && : > "$ROOT/.os/.dirty" 2>/dev/null
exit 0
