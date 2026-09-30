#!/usr/bin/env bash
# Runs just before a new write-up is created. If the folder already holds one
# about the same thing, it says so and names the file.
#
# Why this exists, and why it is a hook rather than a rule: on 2026-08-27 the
# same job was run twice on identical copies of this folder. The copy with all
# the rules loaded filed a second write-up of a study that was already here,
# with the existing file sitting in the same folder. The bare model, with no
# rules at all, found it and refused. The rule was there the whole time —
# CLAUDE.md says "Edit before adding", /learn says "Prefer editing what's
# already there" — and it lost in a long file, exactly as this folder's own
# notes say rules do.
#
# Matching on the file name does not work. The duplicate was called
# `roediger-karpicke-testing-effect` and the original `retrieval-practice`:
# same study, no shared words. So this reads what is about to be written and
# looks for the things a write-up of the same source cannot avoid repeating —
# the figures and the names.
#
# It turns the first try back, once. A warning that only rode along with the
# write reached Claude after the second copy already existed. But two sources
# really can share a number, and a second write-up of the same source from a
# different angle is sometimes right, so a second try at the same file goes
# through.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_python

IN=$(cat)

# Only new write-ups. An edit to an existing one is a different hook's job.
REL_AND_BODY=$(printf '%s' "$IN" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    if d.get("tool_name") != "Write":
        raise SystemExit
    ti = d.get("tool_input", {})
    print(ti.get("file_path", ""))
    print(ti.get("content", ""))
except SystemExit:
    pass
except Exception:
    pass')

FILE=$(printf '%s' "$REL_AND_BODY" | head -1)
[ -n "${FILE:-}" ] || exit 0
REL="${FILE#"$ROOT"/}"
case "$REL" in
  notes/*/sources/*.md) ;;
  *) exit 0 ;;
esac
case "$REL" in */sources/README.md) exit 0 ;; esac
[ -f "$FILE" ] && exit 0   # already there: that is an edit, not a second copy
MARK="$STATE/second-$(printf '%s' "$REL" | cksum | cut -d' ' -f1)"
[ -f "$MARK" ] && { rm -f "$MARK"; exit 0; }   # the second try

cd "$ROOT" 2>/dev/null || exit 0

WARNING=$(printf '%s' "$REL_AND_BODY" | python3 -c '
import re, sys, os, glob

lines = sys.stdin.read().split("\n")
target = lines[0]
body = "\n".join(lines[1:])
if not body.strip():
    raise SystemExit

def marks(text):
    """The bits two write-ups of the same source both have to contain."""
    # Drop the header block first. Every write-up carries a Date: and a
    # Subject:, so leaving them in makes every pair look related.
    text = re.sub(r"^(Date|From|Subject):.*$", "", text, flags=re.M)
    text = re.sub(r"^#.*—.*https?://\S+", "", text, flags=re.M)
    out = set()
    # Figures: 61%, 28.64%, 3,733. These are what a study is remembered by.
    out |= {m.replace(",", "") for m in re.findall(r"\d[\d,]*\.?\d*%", text)}
    out |= {m.replace(",", "") for m in re.findall(r"\b\d{3,}(?:,\d{3})*\b", text)}
    # Not years, though. Every episode of a show they follow shares one.
    out = {m for m in out if not re.fullmatch(r"(19|20)\d\d", m)}
    # Surnames and proper nouns, minus the ones every file here carries.
    common = {"Claude","Anthropic","OpenAI","The","This","That","They","It","A",
              "Date","From","Subject","How","What","Does","Decision","Their",
              "Read","Worth","One","Two","Three","Kept","So","But","And","Not",
              "Every","Their","YouTube","Google","Date"}
    out |= {w for w in re.findall(r"\b[A-Z][a-z]{3,}\b", text) if w not in common}
    return out

new = marks(body)
if len(new) < 3:
    raise SystemExit

target_dir = os.path.dirname(target)
hits = []
for path in glob.glob("notes/*/sources/*.md"):
    if os.path.basename(path) == "README.md":
        continue
    try:
        old = marks(open(path, encoding="utf-8").read())
    except Exception:
        continue
    shared = new & old
    # Two shared figures, or a figure plus two names, is not coincidence.
    figures = {s for s in shared if any(c.isdigit() for c in s)}
    if len(figures) >= 2 or (len(figures) >= 1 and len(shared) >= 3):
        hits.append((len(shared), path, sorted(shared)[:6]))

if not hits:
    raise SystemExit

hits.sort(reverse=True)
n, path, shared = hits[0]
extra = "" if len(hits) == 1 else "\n(%d other write-ups overlap too.)" % (len(hits) - 1)
msg = (
    "There may already be a write-up of this. `%s` shares %d specific things "
    "with what you are about to write, including: %s.%s\n\n"
    "Open it before creating a second file. If it is the same source, edit that "
    "one or add a dated line to it — a folder with two write-ups of one study "
    "cannot tell you what you actually read. If it genuinely is a different "
    "source that happens to share a figure, say so in the file and write it "
    "again: a second try at the same file goes through."
) % (path, n, ", ".join(shared), extra)

print(msg)
')
[ -n "$WARNING" ] || exit 0
: > "$MARK"
stop_tool deny "$WARNING"
