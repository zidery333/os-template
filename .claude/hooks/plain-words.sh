#!/usr/bin/env bash
# Stop — after each reply, look for the words in plain-words.tsv, the list
# beside this file. A hit goes back to the AI as a note to say those
# sentences again, plainly. It is feedback, not a block: a block shows up as
# a hook error after a reply that was fine.
#
# It never fails or holds up a chat. With no python3, no word list, or a
# reply it can't read, it says nothing and lets the reply stand. A reply that
# is already the rewrite is let through, so it can't go round in circles.
#
# To turn it off: delete plain-words.tsv, or put "OS_PLAIN_CHECK": "off" in
# the "env" part of .claude/settings.json.
set -u
[ "${OS_PLAIN_CHECK:-on}" = off ] && exit 0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || exit 0
LIST="$HERE/plain-words.tsv"
[ -f "$LIST" ] || exit 0

# Hooks run with few places to look for programs, so add the usual ones.
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${HOME:-/}/.local/bin:$PATH"
[ -z "${OS_PRETEND_NO_PYTHON:-}" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
# On a Mac without Apple's developer tools, /usr/bin/python3 is only a
# stand-in: it fails, and pops up an "install" box every time it runs, which
# here would be after every reply.
if [ "$(uname -s 2>/dev/null)" = Darwin ] && [ "$(command -v python3)" = /usr/bin/python3 ] \
   && ! xcode-select -p >/dev/null 2>&1; then
  exit 0
fi

python3 -c '
import json, re, sys

def reply(d):
    """The reply just given: from the field Claude Code sends it in, or else
    the last words of the chat record."""
    if d.get("stop_hook_active") is True:
        return ""                       # already the rewrite: let it stand
    for key in ("last_assistant_message", "assistant_message", "message"):
        said = d.get(key)
        if isinstance(said, str) and said.strip():
            return said
    path = d.get("transcript_path")
    if not isinstance(path, str) or not path:
        return ""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 2_000_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    parts = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "user":
            break                       # the reply is everything after this
        content = (entry.get("message") or {}).get("content") if entry.get("type") == "assistant" else None
        if isinstance(content, list):
            parts[:0] = [c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(p for p in parts if p)

try:
    data = json.loads(sys.stdin.read() or "{}")
    text = reply(data) if isinstance(data, dict) else ""
    if not text.strip():
        sys.exit(0)
    # Words being named, not used: code, quotes, tables and quoted lines.
    # Without this the check fires on its own word list. Bullets are read.
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\"[^\"]{0,200}\"", " ", text)
    text = re.sub("“[^”]{0,200}”", " ", text)
    text = re.sub(r"^\s*[|>].*$", " ", text, flags=re.M).lower()
    hits = []
    with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip() or line.lstrip().startswith("#") or "\t" not in line:
                continue
            word, plain = (part.strip() for part in line.rstrip("\n").split("\t", 1))
            # Whole words only, so "use" inside "because" is not a hit.
            if word and plain and re.search(r"(?<![a-z])" + re.escape(word.lower()) + r"(?![a-z])", text):
                hits.append("  \"%s\" -> say \"%s\"" % (word, plain))
    if hits:
        note = ("That reply used words from this folder’s plain-words list. Say it again, plainly:\n"
                + "\n".join(hits) + "\n\nRewrite only the sentences with those words in them. Keep "
                "everything else the same, keep it the same length or shorter, and do not mention "
                "this check or apologise. Just say the thing properly.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": note}}))
except Exception:
    pass
' "$LIST" 2>/dev/null
exit 0
