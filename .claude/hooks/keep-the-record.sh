#!/usr/bin/env bash
# PreToolUse (Write|Bash) — a Write replaces a whole file. When the file it would
# replace has dated lines under ## Decisions or ## Log that the new text has
# lost, the person is asked first: those two only ever grow, and ./os undo
# cannot bring back a hand edit. A shell command that rewrites such a file
# (sed -i, perl -i, a single > or >|, truncate, tee without -a, cp or mv onto
# it) is asked about the same way; what sed or > leaves can't be known before
# it runs, so those always ask. An Edit is never asked about, since adding a
# line or fixing a typo is an edit, and nor is >>, tee -a, a command that only
# reads, or ./os. Never fails a session: anything unexpected lets the call
# through. It reads the words of a command, so it misses a file named through
# a variable, or a rewrite run by another program (bash -c, xargs, a script).
set -uo pipefail
IN=$(cat)
# Every shell command comes through here, so most leave now, before python.
case "$IN" in
  *'"tool_name":"Bash"'*|*'"tool_name": "Bash"'*)
    case "$IN" in *sed*|*perl*|*'>'*|*truncate*|*tee*|*cp*|*mv*) ;; *) exit 0 ;; esac ;;
esac
PY="$(command -v python3 2>/dev/null)" || exit 0
# On a Mac without Apple's developer tools, /usr/bin/python3 is only a stand-in
# that pops up an install box every time it runs. Better silent than that.
if [ "$PY" = /usr/bin/python3 ] && [ "$(uname -s)" = Darwin ] \
   && ! xcode-select -p >/dev/null 2>&1; then
  exit 0
fi
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
OUT=$(printf '%s' "$IN" | "$PY" -c '
import glob, json, os, re, shlex, sys

OPS = "();<>|&"
HIDE = str.maketrans(OPS, "")
BACK = str.maketrans("", OPS)


def tokens(line):
    """The words and operators of one line. A > or | inside quotes stays
    hidden in its word until the word is used, so it is never taken for a
    redirect: grep ">" only reads."""
    # shlex builds one long word a letter at a time, and a single line of a
    # megabyte ran past the 10 seconds a hook has (audit, 2026-10-29). A run
    # that long is text, never a file name, so it is cut to one letter first;
    # quotes and redirects around it stay where they are.
    line = re.sub(r"[^\s\x27\"\\<>|&;()]{1000,}", "X", line)
    # A long quoted string, spaces and all, is text too: one of 800 KB took
    # seven seconds, and past 1 MB the hook ran out of time (hooks sweep,
    # 2026-10-29). Only its first thousand letters are kept, cut here where
    # the quotes are known: a pattern paired one closing quote with the next
    # opening one and hid a real > between them (twelfth review).
    out, quote, escaped, run = [], "", False, 0
    for ch in line:
        inside = bool(quote)
        if escaped:
            escaped, ch = False, ch.translate(HIDE)
            # Inside double quotes, an escaped quote, backslash, ` or $ is
            # followed here and given to shlex as plain letters; any other
            # backslash stays, as bash keeps it: a\b.md is a name (thirteenth
            # and pre-merge reviews, 2026-10-29).
            if inside and ch in "\"\\`$" and out and out[-1] == "\\":
                out[-1], ch = "x", "x"
        elif ch == "\\" and quote != "\x27":
            escaped = True
        elif ch in "\x27\"" and quote in ("", ch):
            quote = "" if quote else ch
        elif quote:
            ch = ch.translate(HIDE)
        if inside and quote:
            run += 1
            if run > 1000:
                continue
            if run == 1000 and ch == "\\":
                ch = "x"      # its partner is cut: never leave it escaping the close
        else:
            run = 0
        out.append(ch)
    try:
        lex = shlex.shlex("".join(out), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex), True
    except ValueError:
        return line.split(), False


def named(cwd, word):
    """The files a word of a command names, with any * ? [ filled in."""
    word = os.path.expanduser(word)
    path = os.path.join(cwd, word)
    if re.search(r"[*?[]", word):
        return glob.glob(word if os.path.isabs(word) else os.path.join(glob.escape(cwd), word)) or [path]
    return [path]


def read(path, limit=None):
    """The words in a file, or None. Only a plain file, and at most
    `limit` bytes: a pipe named by a cp hung the call, and a 1.5 GB video
    took 3 GB of memory (hooks sweep, 2026-10-29)."""
    try:
        if not os.path.isfile(path):
            return None
        if limit is not None and os.path.getsize(path) > limit:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def in_place(name, rest):
    """Whether sed or perl options edit the file in place: -i (or sed -I),
    alone or in a bundle of letters. A letter that takes the rest of the
    bundle as its argument (sed -e..., perl -Mstrict) ends the search."""
    edit, stop = ("i", "eEMmIdDVxC0") if name == "perl" else ("iI", "efl")
    for w in rest:
        if w == "--in-place" or w.startswith("--in-place="):
            return True
        if w.startswith("-") and not w.startswith("--"):
            for c in w[1:]:
                if c in edit:
                    return True
                if c in stop:
                    break
    return False


def rewrites(cmd, cwd):
    """(file, its new text) for each file the command would rewrite whole, in
    the order it names them. The new text is None where it is not known."""
    cmd = cmd.replace("\\\r\n", "").replace("\\\n", "")   # a line ending in \ goes on
    lines, ends = [], []
    for line in cmd.split("\n"):            # a heredoc body is text, not commands
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            continue
        toks, parsed = tokens(line)
        if parsed:
            ends += [toks[i + 1].translate(BACK).lstrip("-") for i in range(len(toks) - 1) if toks[i] == "<<"]
        else:
            ends += [w for q, w in re.findall(r"(?<!<)<<(?!<)-?\s*([\x27\"]?)(\w+)\1", line)]
        lines.append(toks)
    found = []
    for toks in lines:
        words, onto, target = [], [], None
        for t in toks + [";"]:
            if target is not None:            # what a redirect points at
                if target:
                    onto.append(t.translate(BACK))
                target = None
                continue
            if not (t and set(t) <= set(OPS)):
                words.append(t.translate(BACK))
                continue
            if ">" in t or "<" in t:
                target = ">" in t and ">>" not in t   # a single > or >| empties the file
                continue
            # the end of one command
            while words and (re.match(r"\w+=", words[0]) or
                             words[0] in ("sudo", "command", "env", "nohup", "time")):
                words.pop(0)
            name = os.path.basename(words[0]) if words else ""
            rest = words[1:]
            args = [w for w in rest if not w.startswith("-")]
            flags = "".join(w[1:] for w in rest if w.startswith("-") and not w.startswith("--"))
            if (name in ("sed", "gsed", "perl") and in_place(name, rest)) or name == "truncate" \
                    or (name == "tee" and "a" not in flags and "--append" not in rest):
                onto += args
            found += [(p, None) for w in onto for p in named(cwd, w)]
            if name in ("cp", "mv") and len(args) > 1:
                for src in args[:-1]:
                    dest = os.path.join(cwd, os.path.expanduser(args[-1]))
                    if os.path.isdir(dest):
                        dest = os.path.join(dest, os.path.basename(src.rstrip("/")))
                    # The new words only matter where a note would be
                    # replaced; past 2 MB they are "not known".
                    if not os.path.isfile(dest):
                        continue
                    found.append((dest, read(os.path.join(cwd, os.path.expanduser(src)), 2_000_000)))
            if name in ("cd", "pushd") and args:
                cwd = os.path.join(cwd, os.path.expanduser(args[0]))
            words, onto = [], []
    return found


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


try:
    call = json.load(sys.stdin)
    tool = call.get("tool_name", "Write")
    if tool == "Bash":
        calls = rewrites(str(call["tool_input"]["command"]), call.get("cwd") or os.getcwd())
    elif tool == "Write":
        calls = [(call["tool_input"]["file_path"], call["tool_input"]["content"])]
    else:
        sys.exit(0)
    for path, new in calls:
        old = read(path, 10_000_000) if os.path.isfile(path) else None
        if old is None:
            continue
        still = {raw.strip() for raw in (new or "").splitlines()}
        gone = [section for section, line in dated(old) if line not in still]
        if gone:
            break
    else:
        sys.exit(0)
except Exception:
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
if tool == "Bash":
    may = "would" if new is not None else "could"
    reason = (f"Claude wants to run a command that rewrites {name} ({where}), and {lost} in it "
              f"{may} be taken out or changed. ")
    context = (f"{where} has {len(gone)} dated line(s) under ## Decisions and ## Log, and this "
               "command rewrites the file. In this folder those two sections are append-only "
               "(AGENTS.md, rule 4); adding with >>, ./os decide, or an Edit that leaves them in "
               "place goes through without asking the person.")
else:
    reason = (f"Claude wants to replace the whole of {name} ({where}), and {lost} "
              "in it would be taken out or changed. ")
    context = (f"{where} has {len(gone)} dated line(s) under ## Decisions and ## Log "
               "that this Write leaves out or changes. In this folder those two sections are "
               "append-only (AGENTS.md, rule 4); an Edit that leaves them in place "
               "goes through without asking the person.")
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason":
        reason + "Those are only ever added to. Say yes only if you asked for that.",
    "additionalContext": context}}))
' "$ROOT" 2>/dev/null)
# Only an answer is passed on: a python3 that printed anything else had it
# taken as the hook's answer (hooks sweep, 2026-10-29).
case "$OUT" in
  "{"*) printf '%s\n' "$OUT" ;;
esac
exit 0
