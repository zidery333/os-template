#!/usr/bin/env bash
# PreToolUse (Write|Edit|MultiEdit|NotebookEdit|Bash) — Claude keeps one
# folder of its own for the whole computer, ~/.claude, plus ~/.claude.json.
# A skill, rule, style or setting put there turns up in every folder, not just
# this one. A chat asked to "add a skill" here once wrote one there, and it
# followed the person into their other folders (2026-11-10). So a change
# there is asked about first, with the reason, and the person decides.
#
# Asked about: a Write or Edit of a file in there, and a shell command that
# names it, unless every part of the command only reads (ls, cat, grep…).
# Never asked about: plan files (~/.claude/plans, where plan mode writes) and
# anything inside this folder. It reads the words of a command, so it misses
# the folder named through a variable other than $HOME. Never fails a
# session: anything unexpected lets the call through.
set -uo pipefail
IN=$(cat)
# Most calls never mention it. Those leave now, before python starts.
case "$IN" in *.claude*) ;; *) exit 0 ;; esac
PY="$(command -v python3 2>/dev/null)" || exit 0
# On a Mac without Apple's developer tools, /usr/bin/python3 is only a stand-in
# that pops up an install box every time it runs. Better silent than that.
if [ "$PY" = /usr/bin/python3 ] && [ "$(uname -s)" = Darwin ] \
   && ! xcode-select -p >/dev/null 2>&1; then
  exit 0
fi
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
printf '%s' "$IN" | "$PY" -c '
import json, os, re, shlex, sys

home = os.path.expanduser("~").rstrip("/")
if not home or home == "~":
    sys.exit(0)
root = os.path.realpath(sys.argv[1])
real_home = os.path.realpath(home)
GLOBAL = [os.path.join(real_home, ".claude"), os.path.join(real_home, ".claude.json")]
ALLOWED = [os.path.join(real_home, ".claude", "plans")]


def under(path, top):
    return path == top or path.startswith(top + os.sep)


def is_global(path):
    path = os.path.realpath(os.path.expanduser(path))
    if under(path, root) or any(under(path, a) for a in ALLOWED):
        return False
    return any(under(path, g) for g in GLOBAL)


try:
    call = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
tool, given = call.get("tool_name", ""), call.get("tool_input") or {}

why = ""
if tool == "Bash":
    command = str(given.get("command") or "")
    # Every way of writing the global folder: ~/.claude, $HOME/.claude,
    # ${HOME}/.claude, or the home folder spelled out.
    names = [r"~", r"\$HOME", r"\$\{HOME\}", re.escape(home)]
    if real_home != home:
        names.append(re.escape(real_home))
    spot = re.compile(r"(?:" + "|".join(names) + r")/\.claude(?:\.json)?(?=[/\s\"\x27;|&)]|$)")
    plan = re.compile(r"/plans(?:/|[\s\"\x27;|&)]|$)")
    hits = [m for m in spot.finditer(command)
            if m.group().endswith(".json") or not plan.match(command, m.end())]
    if hits:
        READ = {"ls", "cat", "head", "tail", "grep", "egrep", "rg", "wc", "diff",
                "stat", "file", "less", "more", "echo", "printf", "pwd", "test",
                "[", "true", "jq", "du", "realpath", "readlink", "tree", "basename",
                "dirname", "md5", "shasum", "sort", "uniq", "cut", "column"}
        only_reads = ">" not in re.sub(r"[12]?>\s*/dev/null|2>&1", "", command)
        for part in re.split(r"&&|\|\||[;|\n]", command):
            try:
                words = shlex.split(part)
            except ValueError:
                only_reads = False
                break
            while words and re.match(r"^\w+=", words[0]):
                words = words[1:]
            if not words:
                continue
            first = os.path.basename(words[0])
            # ./os never writes there, and git here only writes this folder:
            # a note or a commit message that mentions ~/.claude is just words.
            if words[0] in ("./os", "os") or (first == "bash" and words[1:2] in (["os"], ["./os"])):
                continue
            if first == "git" and len(words) > 1 and words[1] in (
                    "commit", "log", "show", "diff", "status", "grep", "add", "blame"):
                continue
            if first == "find":
                if any(w in ("-delete", "-exec", "-execdir", "-ok", "-fprint") for w in words):
                    only_reads = False
            elif first not in READ:
                only_reads = False
        if not only_reads:
            why = "this command changes Claude’s own folder for the whole computer (~/.claude)"
elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
    path = str(given.get("file_path") or given.get("notebook_path") or "")
    if path and is_global(path):
        why = "this writes into Claude’s own folder for the whole computer (" + path + ")"

if why:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "ask",
        "permissionDecisionReason": "Asking first: " + why + ". Anything put there "
            "applies in every folder, not just this one. A skill for this folder goes "
            "in .claude/skills here (./os new skill). Say yes only if you want it everywhere."}},
        ensure_ascii=False))
' "$ROOT" 2>/dev/null
exit 0
