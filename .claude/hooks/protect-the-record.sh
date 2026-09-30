#!/usr/bin/env bash
# Runs just before any file is written or edited, and before any shell
# command. If the target is one of the two kinds of file that are never
# supposed to be rewritten, it says so.
#
# Those two — notes/<subject>/sources/ and work/<project>/decisions.md — are
# the whole reason this folder is worth keeping for years. Everything else here
# is a current opinion that can change freely. Those two are a record of what
# was claimed, and what was chosen, on a date.
#
# An edit only gets a reminder. Appending a dated "this turned out to be wrong"
# line is an edit, and so is fixing a typo or a dead link — both are allowed,
# and both look identical to a script. Replacing the whole file is different,
# and so is a command that rewrites one, or moves or deletes a decision log:
# those ask the person first. Deleting or moving a write-up is fine; the notes
# rules allow it. A word to Claude alone arrives next to the result, after the old text
# is already gone.
IN=$(cat)
# Every shell command comes through here, so most leave now, before python.
case "$IN" in *decisions*|*sources*) ;; *) exit 0 ;; esac
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_python

# For a shell command, find the first record it would rewrite, move or delete.
# Appending with >> or tee -a is what the rules ask for, so that passes.
read -r TOOL FILE <<< "$(printf '%s' "$IN" | python3 -c '
import fnmatch, glob, json, os, re, shlex, sys

ROOT = sys.argv[1]
REAL = os.path.realpath(ROOT)
KINDS = ("notes/*/sources/*.md", "work/*/decisions.md", "work/*/decisions-older.md")

def record(cwd, word, kinds=KINDS):
    word = os.path.expanduser(os.path.expandvars(word))
    if re.search(r"[*?[]", word):
        paths = glob.glob(os.path.join(glob.escape(cwd), word))
    else:
        paths = [os.path.join(cwd, word)]
    for p in paths:
        rel = os.path.relpath(os.path.realpath(p), REAL)
        if (os.path.isfile(p) and not rel.endswith("sources/README.md")
                and any(fnmatch.fnmatch(rel, k) for k in kinds)):
            return os.path.join(ROOT, rel)
    return ""

def rewritten(cmd, cwd):
    lines, ends = [], []
    for line in cmd.split("\n"):            # a heredoc body is text, not commands
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            continue
        ends += [w for q, w in re.findall(r"(?<!<)<<(?!<)-?\s*([\x27\"]?)(\w+)\1", line)]
        lines.append(line)
    for line in lines:
        try:
            lex = shlex.shlex(line, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            toks = list(lex) + [";"]
        except ValueError:
            toks = line.split() + [";"]
        words, hits, gone, target = [], [], [], None
        for t in toks:
            if target is not None:            # what a redirect points at
                if target:
                    hits.append(t)
                target = None
            elif not (t and set(t) <= set("();<>|&")):
                words.append(t)
            elif ">" in t or "<" in t:
                target = ">" in t and ">>" not in t   # a single > empties the file
            else:                             # end of one command
                while words and (re.match(r"\w+=", words[0]) or
                                 words[0] in ("sudo", "command", "env", "nohup", "time")):
                    words.pop(0)
                name = os.path.basename(words[0]) if words else ""
                args = [w for w in words[1:] if not w.startswith("-")]
                flags = "".join(w[1:].split(".")[0] for w in words[1:]
                                if w.startswith("-") and not w.startswith("--"))
                opts = " ".join(w for w in words if w.startswith("--"))
                if name in ("sed", "gsed", "perl") and ("i" in flags or "--in-place" in opts):
                    hits += args
                elif name in ("truncate", "shred"):
                    hits += args
                elif name in ("rm", "unlink"):    # a write-up may be deleted,
                    gone += args                  # a decision log may not
                elif name == "mv" and args:       # nor moved, but a write-up may
                    gone += args[:-1]
                    hits.append(args[-1])         # moving onto one replaces it
                elif name == "tee" and "a" not in flags and "--append" not in opts:
                    hits += args
                elif name == "cp" and args:
                    hits.append(args[-1])
                for h in hits:
                    found = record(cwd, h)
                    if found:
                        return found
                for h in gone:
                    found = record(cwd, h, KINDS[1:])
                    if found:
                        return found
                if name == "cd" and args:
                    cwd = os.path.join(cwd, os.path.expanduser(args[0]))
                words, hits, gone = [], [], []
    return ""

try:
    d = json.load(sys.stdin)
    tool, ti = d.get("tool_name", ""), d.get("tool_input", {})
    if tool == "Bash":
        print(tool, rewritten(ti.get("command", ""), d.get("cwd") or os.getcwd()))
    else:
        print(tool, ti.get("file_path", ""))
except Exception: pass' "$ROOT")"

[ -n "${FILE:-}" ] || exit 0
REL="${FILE#"$ROOT"/}"
P="${REL%/*}"; P="${P##*/}"

case "$REL" in
  notes/*/sources/*.md) KIND="a write-up. It records what somebody claimed, on a date."
                        WHAT="the write-up $REL" ;;
  work/*/decisions.md) KIND="a decision log. It records what was chosen, and why, on a date."
                       WHAT="the decision log for $P ($REL)" ;;
  work/*/decisions-older.md) KIND="a decision log. It records what was chosen, and why, on a date."
                             WHAT="the older entries of the decision log for $P ($REL)" ;;
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

KEEP="If the claim turned out to be wrong, append a dated line saying so and
leave the rest exactly as written."
# The person reads these, in the box that asks them, so one line each.
LOST="unless that is what you asked for. The usual way to change one is to add a new dated line and leave the old ones alone."

case "${TOOL:-}" in
  Write)
    stop_tool ask "Claude wants to replace the whole of $WHAT. What it says now will be lost, $LOST" \
      "About to replace all of $REL. That is $KIND

Replacing it loses what it said, and that is the one thing in this folder that
is not allowed to change. $KEEP The person was asked before it went ahead." ;;
  Bash)
    stop_tool ask "Claude wants to run a command that rewrites, moves or deletes $WHAT. What it says now could be lost, $LOST" \
      "That command rewrites, moves or deletes $REL. That is $KIND

$KEEP To add to it, append with >> or use Edit. The person was asked first." ;;
  *)
    say_to_claude "Editing $REL. That is $KIND

Fixing a typo or a dead link is fine. Changing what was claimed or chosen is
not — add a new dated entry instead, and leave the old text alone." PreToolUse ;;
esac
