#!/usr/bin/env bash
# PreToolUse (Write) — a Write replaces a whole file. When the file it would
# replace has dated lines under ## Decisions or ## Log that the new text has
# lost, the person is asked first: those two only ever grow, and ./os undo
# cannot bring back a hand edit. An Edit is never asked about, since adding a
# line or fixing a typo is an edit. Never fails a session: anything
# unexpected lets the write through.
set -uo pipefail
PY="$(command -v python3 2>/dev/null)" || exit 0
# On a Mac without Apple's developer tools, /usr/bin/python3 is only a stand-in
# that pops up an install box every time it runs. Better silent than that.
if [ "$PY" = /usr/bin/python3 ] && [ "$(uname -s)" = Darwin ] \
   && ! xcode-select -p >/dev/null 2>&1; then
  exit 0
fi
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
"$PY" -c '
import json, os, re, sys

try:
    call = json.load(sys.stdin)
    path = call["tool_input"]["file_path"]
    new = call["tool_input"]["content"]
    if call.get("tool_name", "Write") != "Write" or not os.path.isfile(path):
        sys.exit(0)
    with open(path, encoding="utf-8", errors="replace") as fh:
        old = fh.read()
except Exception:
    sys.exit(0)

HEADING = re.compile(r"^(#{1,6})\s+(.*?)[\s#]*$")
DATED = re.compile(r"^(?:[-*+]|#{1,6})?\s*[*_\[(]*\d{4}-\d\d-\d\d")


def dated(text):
    """(section, line) for each dated line under ## Decisions or ## Log."""
    out, section, level = [], None, 0
    for line in (raw.strip() for raw in text.splitlines()):
        head = HEADING.match(line)
        if head and (section is None or len(head.group(1)) <= level):
            name = head.group(2).strip().lower()
            section, level = (name, len(head.group(1))) if name in ("decisions", "log") else (None, 0)
            continue
        if section and DATED.match(line):
            out.append((section, line))
    return out


still = {raw.strip() for raw in new.splitlines()}
gone = [section for section, line in dated(old) if line not in still]
if not gone:
    sys.exit(0)

head = re.match(r"\A---[ \t]*\n(.*?)\n---", old, re.S)
title = re.search(r"^title:[ \t]*(.+?)[ \t]*$", head.group(1), re.M) if head else None
name = (title.group(1).strip("\"\x27") if title else "") or (
    os.path.basename(os.path.dirname(path)) if os.path.basename(path).lower() in ("readme.md", "index.md")
    else os.path.splitext(os.path.basename(path))[0])
root = os.path.realpath(sys.argv[1])
real = os.path.realpath(path)
where = os.path.relpath(real, root) if real.startswith(root + os.sep) else path


def count(n, one, many):
    return f"{n} {one if n == 1 else many}"


parts = [count(gone.count("decisions"), "decision", "decisions"),
         count(gone.count("log"), "log line", "log lines")]
lost = " and ".join(p for p in parts if not p.startswith("0 "))
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason":
        f"Claude wants to replace the whole of {name} ({where}), and {lost} "
        "in it would be taken out or changed. Those are only ever added to. "
        "Say yes only if you asked for that.",
    "additionalContext":
        f"{where} has {len(gone)} dated line(s) under ## Decisions and ## Log "
        "that this Write leaves out or changes. In this folder those two sections are "
        "append-only (AGENTS.md, rule 4); an Edit that leaves them in place "
        "goes through without asking the person."}}))
' "$ROOT" 2>/dev/null
exit 0
