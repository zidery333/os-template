#!/usr/bin/env bash
# Checks that the folder's own checks still work.
#
#   ./.claude/tests/run.sh
#
# Run it after changing anything in .claude/hooks/. Every test here is a bug
# that was really in this folder at some point, so a failure means you have
# put one of them back.
#
# It builds a throwaway OS folder in a temp directory and points the real
# hooks at that. Your own notes and work are never touched, and nothing is
# left behind.
set -uo pipefail

REAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
export CLAUDE_PROJECT_DIR="$FIX"

pass=0; fail=0; skipped=0
say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { skipping "$1" && return; if printf '%s' "$2" | grep -qF "$3"; then pass=$((pass+1)); printf '  ok    %s\n' "$1"
         else fail=$((fail+1)); printf '  FAIL  %s\n        wanted to see: %s\n        got: %s\n' "$1" "$3" "${2:-<nothing>}"; fi; }
no()   { skipping "$1" && return; if printf '%s' "$2" | grep -qF "$3"; then fail=$((fail+1)); printf '  FAIL  %s\n        should not have said: %s\n' "$1" "$3"
         else pass=$((pass+1)); printf '  ok    %s\n' "$1"; fi; }
quiet(){ skipping "$1" && return; if [ -z "$(printf '%s' "$2" | tr -d '[:space:]')" ]; then pass=$((pass+1)); printf '  ok    %s\n' "$1"
         else fail=$((fail+1)); printf '  FAIL  %s\n        should have said nothing, said: %s\n' "$1" "$2"; fi; }
# On a machine without git, curl or unzip, the checks that use them failed,
# and /update-os won't call an upgrade done while anything fails. Now they
# are skipped there, and say why. `needs git` starts a stretch of checks that
# need git; `needs` on its own ends it.
lacking=""
needs(){ lacking=""; for t in "$@"; do command -v "$t" >/dev/null 2>&1 || lacking="$t"; done; }
skipping(){ [ -n "$lacking" ] || return 1; skipped=$((skipped+1)); printf '  skip  %s (%s is not installed)\n' "$1" "$lacking"; }

# ---------------------------------------------------------------------------
# Building a fake folder to test against
# ---------------------------------------------------------------------------
blank_folder() {
  rm -rf "$FIX"; mkdir -p "$FIX/notes" "$FIX/work/archive" "$FIX/me" "$FIX/.claude"
  printf '# Subjects\n\nNothing yet.\n'  > "$FIX/notes/subjects.md"
  printf '# Projects\n\n## Active\n\nNothing yet.\n' > "$FIX/work/projects.md"
  printf '# Who I am\n\nAnswered.\n'     > "$FIX/me/who-i-am.md"
  printf '# My setup\n\nAnswered.\n'     > "$FIX/me/my-setup.md"
  printf '# How to work with me\n'       > "$FIX/CLAUDE.md"
  # The word check reads its list from the folder it is checking, so the
  # fixture needs the real one. Copying it means these tests run against the
  # list you actually ship, not a stand-in.
  mkdir -p "$FIX/.claude/hooks"
  cp "$REAL/.claude/hooks/plain-words.tsv" "$FIX/.claude/hooks/" 2>/dev/null
}
subject() {  # subject <name> <body-file-content>
  mkdir -p "$FIX/notes/$1/sources"; printf '%s' "$2" > "$FIX/notes/$1/what-i-think.md"
}
writeup() {  # writeup <subject> <filename> <content>
  mkdir -p "$FIX/notes/$1/sources"; printf '%s' "$3" > "$FIX/notes/$1/sources/$2"
}
on_subject_map() { printf -- '- [%s](%s/what-i-think.md) — x\n' "$1" "$1" >> "$FIX/notes/subjects.md"; }
project() {  # project <name>
  mkdir -p "$FIX/work/$1"
  printf '# %s\n\n**Next:** something\n' "$1" > "$FIX/work/$1/brief.md"
  printf '# %s — decisions\n' "$1"           > "$FIX/work/$1/decisions.md"
}
on_project_map() { printf -- '- [%s](%s/brief.md) — x\n' "$1" "$1" >> "$FIX/work/projects.md"; }
age()  { find "$2" -exec touch -t "$1" {} \; 2>/dev/null; touch -t "$1" "$2"; }
rot()  { bash -c "source '$REAL/.claude/hooks/lib.sh' && os_rot" 2>&1; }
skill() {  # skill <folder> <file-contents>
  mkdir -p "$FIX/.claude/skills/$1"
  [ -n "${2:-}" ] && printf '%s' "$2" > "$FIX/.claude/skills/$1/SKILL.md"
  return 0
}
guard() {  # guard <tool> <relative-path>
  python3 -c "import json,sys;print(json.dumps({'tool_name':'$1','tool_input':{'file_path':'$FIX/$2'}}))" \
    | "$REAL/.claude/hooks/protect-the-record.sh" 2>&1
}
guard_cmd() {  # guard_cmd <shell command>, run from the top of the folder
  python3 -c "import json,sys;print(json.dumps({'tool_name':'Bash','tool_input':{'command':sys.argv[1]},'cwd':sys.argv[2]}))" "$1" "$FIX" \
    | "$REAL/.claude/hooks/protect-the-record.sh" 2>&1
}
health(){ bash -c "source '$REAL/.claude/hooks/lib.sh' && os_health" 2>&1; }
second() {  # second <relative-path> <content-about-to-be-written>
  python3 -c "import json,sys;print(json.dumps({'tool_name':'Write','tool_input':{'file_path':'$FIX/$1','content':sys.argv[1]}}))" "$2" \
    | "$REAL/.claude/hooks/no-second-copy.sh" 2>&1
}
tidy() { printf '{"tool_input":{"file_path":"%s"}}' "$1" | "$REAL/.claude/hooks/keep-tidy.sh" 2>&1; }
history_script() {  # the history script works from its own folder, so it goes in the fixture
  mkdir -p "$FIX/.claude/scripts"; cp "$REAL/.claude/scripts/history.sh" "$FIX/.claude/scripts/"
}
no_dev_tools() {  # a Mac without Apple's developer tools: its git only pops up a box
  mkdir -p "$FIX/bin"
  printf '#!/bin/sh\necho "ProductVersion: 15.0"\n' > "$FIX/bin/sw_vers"
  printf '#!/bin/sh\nexit 2\n'                     > "$FIX/bin/xcode-select"
  printf '#!/bin/sh\necho git-was-run\n'           > "$FIX/bin/git"
  chmod +x "$FIX/bin/"*
}

OLD=$(date -v-400d +%Y%m%d0900 2>/dev/null || date -d '400 days ago' +%Y%m%d0900)
MID=$(date -v-120d +%Y%m%d0900 2>/dev/null || date -d '120 days ago' +%Y%m%d0900)

# ---------------------------------------------------------------------------
say "A healthy folder says nothing at all"
blank_folder
subject clean '# c

What this covers: x.

## An idea
text [w](sources/2026-01-01-a.md)
'
writeup clean 2026-01-01-a.md '# w'
on_subject_map clean
project live; on_project_map live
quiet "nothing to report" "$(rot)"

# ---------------------------------------------------------------------------
say "Subjects"
blank_folder
subject toolong "$(seq 1 300)"; on_subject_map toolong
ok "a subject file past the limit is flagged" "$(rot)" "Too long to stay useful"

blank_folder
subject orphan '# o

What this covers: x.
'
writeup orphan 2026-01-01-lonely.md '# w'; on_subject_map orphan
ok "a write-up nothing links to is flagged" "$(rot)" "2026-01-01-lonely.md is not linked"

blank_folder
subject linked '# l

What this covers: x.

## idea
[w](sources/2026-01-01-used.md)
'
writeup linked 2026-01-01-used.md '# w'; on_subject_map linked
quiet "a write-up that is linked is left alone" "$(rot)"

blank_folder
subject retract '# r

What this covers: x.
'
writeup retract 2026-01-01-wrong.md '# w

2026-06-14: this turned out to be wrong.
'
on_subject_map retract
quiet "a retracted write-up is finished, not rotting" "$(rot)"

blank_folder
subject a '# a

What this covers: x.

## The same idea
one [w](sources/x.md)
'
subject b '# b

What this covers: x.

## The same idea
two [w](sources/x.md)
'
on_subject_map a; on_subject_map b
ok "one idea written in two subjects is flagged" "$(rot)" "## The same idea"

blank_folder
subject a '# a

What this covers: x.

## Tried and it did nothing for me
- one
'
subject b '# b

What this covers: x.

## Tried and it did nothing for me
- two
'
on_subject_map a; on_subject_map b
no "a section heading the README teaches is not a repeat" "$(rot)" "Tried and it did nothing"

blank_folder
subject unmapped '# u

What this covers: x.
'
ok "a subject missing from the map is flagged" "$(rot)" "notes/unmapped is not listed"

blank_folder
subject a.b '# a

What this covers: x.
'
on_subject_map axb
ok "a dot in a name is not a wildcard" "$(rot)" "notes/a.b is not listed"

blank_folder
subject parked '# p

What this covers: x.
'
printf -- '<!-- - [parked](parked/what-i-think.md) — on hold -->\n' >> "$FIX/notes/subjects.md"
ok "a commented-out map line does not count as listed" "$(rot)" "notes/parked is not listed"

blank_folder
subject ancient '# a

What this covers: x.
'
on_subject_map ancient; age "$OLD" "$FIX/notes/ancient"
ok "a subject untouched for a year is flagged" "$(rot)" "has not changed in a year"

# ---------------------------------------------------------------------------
say "Projects"
blank_folder
mkdir -p "$FIX/work/bare"; on_project_map bare
out=$(rot)
ok "a project with no brief is flagged"     "$out" "work/bare has no brief.md"
ok "a project with no decision log is flagged" "$out" "work/bare has no decisions.md"
no "an empty folder is not called abandoned" "$out" "has not changed in three months"

blank_folder
project unmapped
ok "a project missing from the map is flagged" "$(rot)" "work/unmapped is not listed"

blank_folder
project frozen; on_project_map frozen; age "$MID" "$FIX/work/frozen"
ok "a project untouched for months is flagged" "$(rot)" "has not changed in three months"

blank_folder
project fresh; on_project_map fresh
no "a project touched today is left alone" "$(rot)" "has not changed"

blank_folder
mkdir -p "$FIX/work/archive/old"
printf '# old\n' > "$FIX/work/archive/old/brief.md"
age "$OLD" "$FIX/work/archive/old"
quiet "archived work is exempt from every check" "$(rot)"

blank_folder
for p in a b c d e; do project "$p"; on_project_map "$p"; done
ok "too many at once is flagged" "$(rot)" "5 projects in work/ with an end"

# ---------------------------------------------------------------------------
say "Things with no end are not nagged"
# A folder that asks "isn't this finished yet?" about a garden or a language is
# a workplace. A brief or a subject file carrying the no-end line is left alone
# by every check that assumes things finish.
NOEND='**This one has no end.**'

blank_folder
project garden; on_project_map garden
printf '# garden\n\n%s\n' "$NOEND" > "$FIX/work/garden/brief.md"
age "$MID" "$FIX/work/garden"
quiet "a project with no end is not asked when it will be done" "$(rot)"

blank_folder
project shed; on_project_map shed
age "$MID" "$FIX/work/shed"
ok "a project that does have an end is still asked" "$(rot)" "has not changed in three months"

blank_folder
for p in a b c d e; do project "$p"; on_project_map "$p"; done
for p in a b c; do printf '# %s\n\n%s\n' "$p" "$NOEND" > "$FIX/work/$p/brief.md"; done
no "things you keep do not count towards too many projects" "$(rot)" "projects in work/ with an end"

# /catch-me-up told Claude to call out any project that hadn't moved in a
# month, and never mentioned the line, so the garden got called out too.
ok "/catch-me-up knows the no-end line" "$(cat "$REAL/.claude/skills/catch-me-up/SKILL.md")" "$NOEND"

# A download has no git history, so the "what moved" line only ever printed
# an error, and the catch-up could never say what changed.
blank_folder; history_script
printf 'x\n' > "$FIX/notes/fresh.md"; printf 'x\n' > "$FIX/notes/stale.md"; touch -t "$OLD" "$FIX/notes/stale.md"
# A download keeps the zip's dates, so a week after a release every blank
# form it came with read as changed this week.
printf 'blank\n' > "$FIX/notes/blank.md"
printf '%s\tnotes/blank.md\n' "$(cksum < "$FIX/notes/blank.md" | awk '{ print $1 "-" $2 }')" > "$FIX/.claude/shipped.tsv"
moved=$(awk '/^# what moved/{f=1} f&&/^$/{exit} f' "$REAL/.claude/skills/catch-me-up/SKILL.md")
out=$(cd "$FIX" && GIT_DIR="$FIX/no-git" bash -c "$moved" 2>&1)
ok "with no git history, /catch-me-up lists this week's files" "$out" "notes/fresh.md"
no "and not older ones"                                        "$out" "stale.md"
no "and not a blank form still as it came"                     "$out" "blank.md"
no "and prints no git error"                                   "$out" "not a git repository"
# On a Mac without developer tools, git is a stand-in that pops up an install
# box. The fallback was for exactly that Mac, and it ran git first anyway.
no_dev_tools
out=$(cd "$FIX" && PATH="$FIX/bin:/usr/bin:/bin" bash -c "$moved" 2>&1)
no "on a Mac without developer tools, /catch-me-up doesn't run git" "$out" "git-was-run"
ok "and lists this week's files instead"                           "$out" "notes/fresh.md"

blank_folder
subject birds "# birds

What this covers: birds.

$NOEND

## An idea
text [w](sources/2026-01-01-a.md)
"
writeup birds 2026-01-01-a.md '# w'
on_subject_map birds
age "$OLD" "$FIX/notes/birds"
quiet "a subject kept for love is not called dead after a year" "$(rot)"

blank_folder
subject tax '# tax

What this covers: tax.

## An idea
text [w](sources/2026-01-01-a.md)
'
writeup tax 2026-01-01-a.md '# w'
on_subject_map tax
age "$OLD" "$FIX/notes/tax"
ok "a subject without the line is still called out" "$(rot)" "has not changed in a year"

# ---------------------------------------------------------------------------
say "The folder itself"
blank_folder
printf 'scratch\n' > "$FIX/loose-note.txt"
ok "a document left at the top level is flagged" "$(rot)" "loose-note.txt sits loose"

blank_folder
printf 'x\n' > "$FIX/.DS_Store"
quiet "a dotfile at the top level is ignored" "$(rot)"

blank_folder
printf '# Who I am\n\nTO FILL\n' > "$FIX/me/who-i-am.md"
ok "an unanswered blank is reported" "$(health)" "Still blank"

# A folder straight off the download has every starting file blank. That is
# not the same as a half-finished setup and must not be answered the same way.
blank_folder
printf '# Who I am\n\nTO FILL\n'          > "$FIX/me/who-i-am.md"
printf '# How to work with me\n\nTO FILL\n' > "$FIX/CLAUDE.md"
out="$(health)"
ok "a folder nobody has set up is told to offer /setup" "$out" "never been set up"
no "and is not nagged file by file instead"            "$out" "Still blank"

# A /setup that stopped while writing the files leaves its answers behind.
# The check used to list the files still blank, which invites asking every
# question again by hand, and never said the answers were there.
blank_folder
printf '# How to work with me\n\nTO FILL\n' > "$FIX/CLAUDE.md"
printf '1. Nursing, and a garden.\n'       > "$FIX/me/setup-answers.md"
out="$(health)"
ok "a setup that stopped partway is told to finish with /setup" "$out" "me/setup-answers.md"
no "and is not nagged file by file instead"                     "$out" "Still blank"

blank_folder
mkdir -p "$FIX/work/archive/done"
printf '# d\n\n**Done looks like:** TO FILL\n' > "$FIX/work/archive/done/brief.md"
quiet "a blank inside archived work is not reported" "$(health)"

blank_folder
project copyhost; on_project_map copyhost
printf 'not mine\n' > "$FIX/work/copyhost/.not-my-os"
mkdir -p "$FIX/work/copyhost/template/me"
printf '# Who I am\n\nTO FILL\n' > "$FIX/work/copyhost/template/me/who-i-am.md"
quiet "a blank inside a copy of an OS folder is not reported" "$(health)"

printf '# Who I am\n\nTO FILL\n' > "$FIX/me/who-i-am.md"
out="$(health)"
ok "the marker only covers its own folder" "$out" "me/who-i-am.md"
no "the copy stays out of the list"        "$out" "work/copyhost"

# macOS awk refuses a line break in a -v value. A second marked copy made the
# whole blanks line vanish, the person's own blanks with it.
project copytwo; on_project_map copytwo
printf 'not mine\n' > "$FIX/work/copytwo/.not-my-os"
printf '# Who I am\n\nTO FILL\n' > "$FIX/work/copytwo/who-i-am.md"
out="$(health)"
ok "with two copies, your own blanks are still reported" "$out" "me/who-i-am.md"
no "and neither copy is"                                 "$out" "work/copytwo"

# Every file in a project was read for blanks, videos too. One 2 GB export
# took the session check past its fifteen-second limit, and everything it
# had to say was thrown away.
blank_folder
project film; on_project_map film
printf 'junk TO FILL junk\n' > "$FIX/work/film/export.mov"
no "a file that is not a note is not read for blanks" "$(health)" "export.mov"

# The session check shows the first three problems. It called the rest "more
# like that" even when they were not, so a quiet project behind three unlinked
# write-ups never came up.
blank_folder
subject sleep '# s

What this covers: x.
'
on_subject_map sleep
for i in 1 2 3 4; do writeup sleep "2026-01-0$i-w.md" '# w'; done
project shed; on_project_map shed; age "$MID" "$FIX/work/shed"
out="$(health)"
ok "the count names the kinds it holds back" "$out" "projects untouched for three months"
no "and does not call them all the same"      "$out" "more like that"

# ---------------------------------------------------------------------------
say "Checking a file as it is written"
blank_folder
subject long "$(seq 1 300)"
ok "a long notes file is told to split"  "$(tidy "$FIX/notes/long/what-i-think.md")" "past the 250-line limit"

blank_folder; project log
{ printf '# log — decisions\n'; seq 1 700; } > "$FIX/work/log/decisions.md"
out=$(tidy "$FIX/work/log/decisions.md")
ok "a long decision log is told to move, not cut" "$out" "decisions-older.md"
no "a long decision log is never told to split"   "$out" "past the 250-line limit"

blank_folder; project dup
printf '# d — decisions\n\n## 2026-01-01 — A\none\n\n## 2026-01-01 — A\ntwo\n' > "$FIX/work/dup/decisions.md"
quiet "two decisions may share a heading" "$(tidy "$FIX/work/dup/decisions.md")"

blank_folder; project fat
{ printf '# fat\n'; seq 1 60; } > "$FIX/work/fat/brief.md"
ok "a brief that became a diary is flagged" "$(tidy "$FIX/work/fat/brief.md")" "A brief is five"

blank_folder
subject nosrc '# n

## one
a

## two
b

## three
c
'
ok "claims with no source are flagged" "$(tidy "$FIX/notes/nosrc/what-i-think.md")" "not one of them says where it came from"
# The birds fix below told every subject that first-hand notes need no label,
# though only a subject with no end lets them off.
no "and a subject that has an end isn't told first-hand notes need nothing" "$(tidy "$FIX/notes/nosrc/what-i-think.md")" "saw for themselves"

# Three headings about her own bird feeder were told to add a source, though
# labels are for claims, not tastes.
blank_folder
subject birds '# Birds

**This one has no end.**

## Goldfinches
at the feeder

## The wren
my favourite

## Long-tailed tits
in pairs
'
ok "tastes are not told to find a source" "$(tidy "$FIX/notes/birds/what-i-think.md")" "Tastes and what they saw for themselves need nothing"

blank_folder
mkdir -p "$FIX/work/archive/old"; { printf '# o\n'; seq 1 300; } > "$FIX/work/archive/old/brief.md"
quiet "archived files are not checked" "$(tidy "$FIX/work/archive/old/brief.md")"

# Every .md inside a project was checked like a note, so a novel's chapter was
# told to cut its weakest part and a meeting log to merge its headings.
blank_folder; project book
mkdir -p "$FIX/work/book/chapters"; seq 1 400 > "$FIX/work/book/chapters/c.md"
quiet "a long chapter inside a project is left alone" "$(tidy "$FIX/work/book/chapters/c.md")"
printf '# Meetings\n\n## Actions\na\n\n## Actions\nb\n' > "$FIX/work/book/meetings.md"
quiet "a meeting log inside a project may repeat a heading" "$(tidy "$FIX/work/book/meetings.md")"

# ---------------------------------------------------------------------------
say "Skills the person adds themselves"
blank_folder
skill good '---
name: good
description: >-
  Does a thing. Use when a thing needs doing.
---
Steps.
'
quiet "a well-formed skill is left alone" "$(rot)"

blank_folder; skill no-file ""
ok "a folder with no SKILL.md is flagged" "$(rot)" "has no SKILL.md"

blank_folder; skill no-block 'just some text, no settings block
'
ok "a missing settings block is flagged" "$(rot)" "does not start with a --- settings block"

blank_folder; skill wrong-name '---
name: something-else
description: >-
  x
---
Steps.
'
ok "a name that does not match its folder is flagged" "$(rot)" "Claude Code goes by the folder"

blank_folder; skill no-desc '---
name: no-desc
---
Steps.
'
ok "a missing description is flagged" "$(rot)" "has no description"

blank_folder; skill too-long "$(printf -- '---\nname: too-long\ndescription: x\n---\n'; seq 1 600)"
ok "a skill past five hundred lines is flagged" "$(rot)" "split the detail into a second file"

# ---------------------------------------------------------------------------
say "Guarding the two files that are never rewritten"
blank_folder
mkdir -p "$FIX/notes/s/sources" "$FIX/work/p" "$FIX/work/archive/old"
printf '# w\n' > "$FIX/notes/s/sources/2026-01-01-a.md"
printf '# d\n' > "$FIX/work/p/decisions.md"
printf '# d\n' > "$FIX/work/archive/old/decisions.md"
printf '# b\n' > "$FIX/work/p/brief.md"
printf '# t\n' > "$FIX/notes/s/what-i-think.md"
printf '# r\n' > "$FIX/notes/s/sources/README.md"

ok    "overwriting a write-up is called out"      "$(guard Write notes/s/sources/2026-01-01-a.md)" "About to replace all of"
ok    "editing a write-up gets a lighter word"    "$(guard Edit notes/s/sources/2026-01-01-a.md)"  "Fixing a typo or a dead link is fine"
ok    "overwriting a decision log is called out"  "$(guard Write work/p/decisions.md)"             "records what was chosen"
ok    "an archived decision log is guarded too"   "$(guard Write work/archive/old/decisions.md)"   "records what was chosen"
quiet "a brief may be rewritten freely"           "$(guard Write work/p/brief.md)"
quiet "a belief file may be rewritten freely"     "$(guard Write notes/s/what-i-think.md)"
quiet "writing a brand new write-up is fine"      "$(guard Write notes/s/sources/2026-09-09-new.md)"
quiet "a sources README is scaffolding"           "$(guard Write notes/s/sources/README.md)"
quiet "anything outside the two kinds is ignored" "$(guard Write me/who-i-am.md)"

# The warning reached Claude next to the result, after the old log was gone.
# A whole-file replace now stops and asks the person first.
out=$(guard Write work/p/decisions.md)
ok    "replacing a decision log asks the person first" "$out" '"permissionDecision":"ask"'
ok    "and tells them in plain words what would be lost" "$out" "Claude wants to replace the whole of the decision log for p"
ok    "replacing a write-up asks too"             "$(guard Write notes/s/sources/2026-01-01-a.md)" '"permissionDecision":"ask"'
no    "an edit is only a reminder, never a question" "$(guard Edit work/p/decisions.md)" "permissionDecision"

# Changes made through a shell command never reached the guard at all.
out=$(python3 -c 'import json,re,sys
for e in json.load(open(sys.argv[1]))["hooks"]["PreToolUse"]:
  if any("protect-the-record" in h["command"] for h in e["hooks"]) and re.fullmatch(e.get("matcher", ""), "Bash"): print("reaches")' "$REAL/.claude/settings.json")
ok    "shell commands are sent to the guard"      "$out" "reaches"
ok    "sed -i on a decision log asks"             "$(guard_cmd "sed -i '' 's/beds/pots/' work/p/decisions.md")" '"permissionDecision":"ask"'
ok    "so does cd into the project first"         "$(guard_cmd "cd work/p && sed -i.bak 's/beds/pots/' decisions.md")" '"permissionDecision":"ask"'
ok    "a single > over a write-up asks"           "$(guard_cmd "echo gone > notes/s/sources/2026-01-01-a.md")" '"permissionDecision":"ask"'
ok    "moving a decision log away asks"           "$(guard_cmd "mv work/p/decisions.md /tmp/old.md")" '"permissionDecision":"ask"'
# notes/README.md says write-ups may be deleted freely, and the guard asked
# before every rm or mv of one.
quiet "deleting a write-up is allowed"            "$(guard_cmd "rm notes/s/sources/2026-01-01-a.md")"
quiet "and so is moving one"                      "$(guard_cmd "mv notes/s/sources/2026-01-01-a.md notes/s/sources/2026-01-01-b.md")"
ok    "deleting a decision log still asks"        "$(guard_cmd "rm work/p/decisions.md")" '"permissionDecision":"ask"'
ok    "moving a file on top of a write-up asks"   "$(guard_cmd "mv /tmp/x.md notes/s/sources/2026-01-01-a.md")" '"permissionDecision":"ask"'
ok    "and so does sed -i on one"                 "$(guard_cmd "sed -i '' 's/a/b/' notes/s/sources/2026-01-01-a.md")" '"permissionDecision":"ask"'
ok    "tee without -a asks"                       "$(guard_cmd "printf x | tee work/archive/old/decisions.md")" '"permissionDecision":"ask"'
quiet "appending with >> is what the rules want"  "$(guard_cmd "cat >> work/p/decisions.md <<'EOF'
## 2026-09-29 — pots, not beds
We don't have room > 2 beds. rm the old plan.
EOF")"
quiet "so is tee -a"                              "$(guard_cmd "printf x | tee -a work/p/decisions.md")"
quiet "a single > making a new write-up is fine"  "$(guard_cmd "cat > notes/s/sources/2026-09-09-new.md <<'EOF'
# New
EOF")"

# ---------------------------------------------------------------------------
say "Speaking plainly"
blank_folder
p1="t$RANDOM"; p2="t$RANDOM"
ok    "a stuffy word sends the reply back" \
      "$(printf '{"last_assistant_message":"we should leverage this","prompt_id":"%s"}' "$p1" | "$REAL/.claude/hooks/plain-words.sh")" \
      "leverage"
quiet "the same reply is only checked once" \
      "$(printf '{"last_assistant_message":"we should leverage this","prompt_id":"%s","stop_hook_active":true}' "$p1" | "$REAL/.claude/hooks/plain-words.sh")"
quiet "a word inside backticks is being named, not used" \
      "$(printf '{"last_assistant_message":"the list has `leverage` in it","prompt_id":"%s"}' "$p2" | "$REAL/.claude/hooks/plain-words.sh")"
# Before Claude Code 2.1.196 there was no prompt_id, so the check keyed on the
# session alone and went quiet for good after its first catch.
sid="no-prompt-id-$$"
printf '{"session_id":"%s","last_assistant_message":"we should leverage this"}' "$sid" | "$REAL/.claude/hooks/plain-words.sh" >/dev/null
ok    "without a prompt_id, a later reply is still checked" \
      "$(printf '{"session_id":"%s","last_assistant_message":"next week, utilize the compost"}' "$sid" | "$REAL/.claude/hooks/plain-words.sh")" \
      "utilize"
# It answered with a block, which Claude Code shows as a hook error after a
# normal reply, and it told Claude the person had asked for words they never saw.
out=$(printf '{"last_assistant_message":"we should leverage this","prompt_id":"t%s"}' "$RANDOM" | "$REAL/.claude/hooks/plain-words.sh")
ok    "a stuffy word goes back as feedback, not a hook error" "$out" '"hookEventName": "Stop"'
no    "and it is not a block"                                "$out" '"decision"'
no    "and it does not claim the person asked for it"        "$out" "the user has asked"
# Ordinary school words were on the list, with swaps that made no sense.
quiet "a river delta is not a stuffy word" \
      "$(printf '{"last_assistant_message":"how a delta forms where the river meets the sea","prompt_id":"t%s"}' "$RANDOM" | "$REAL/.claude/hooks/plain-words.sh")"
quiet "nor is granular disintegration" \
      "$(printf '{"last_assistant_message":"freeze-thaw and granular disintegration","prompt_id":"t%s"}' "$RANDOM" | "$REAL/.claude/hooks/plain-words.sh")"

# ---------------------------------------------------------------------------
say "Spotting something worth saving"
blank_folder
ok    "a lasting fact is noticed (user_input)" \
      "$(printf '{"user_input":"i always work in the mornings"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")" \
      "me/who-i-am.md"
  ok    "a lasting fact is noticed (user_prompt)" \
      "$(printf '{"user_prompt":"i always work in the mornings"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")" \
      "me/who-i-am.md"
ok    "pushback on an answer is noticed too" \
      "$(printf '{"user_prompt":"that was way too long"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")" \
      "pushback"
quiet "an ordinary question is left alone" \
      "$(printf '{"user_prompt":"what time is it in Berlin"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
# A habit at their own craft goes in my-setup.md, but the offer named
# who-i-am.md whatever the fact was.
out=$(printf '{"user_prompt":"I always sharpen my chisels before a glue-up"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")
ok    "a habit at their own craft is pointed at my-setup.md" "$out" "How I do things"
no    "and the offer does not name who-i-am.md for everything" "$out" "save that to me/who-i-am.md"
# /setup asks "what makes you stop reading?", and "too much text" was taken
# as pushback on an answer Claude never gave.
printf '## 4\nToo much text.\n' > "$FIX/me/setup-answers.md"
quiet "an answer to a /setup question is not pushback" \
      "$(printf '{"user_prompt":"Too much text. Long paragraphs and words I have to look up."}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
rm -f "$FIX/me/setup-answers.md"
# "Remember ..." already asked for it, and the nudge had Claude ask back
# "Want me to save that?" anyway.
out=$(printf '{"user_prompt":"Remember that I work mornings only"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")
ok    "\"remember ...\" is saved straight away"      "$out" "already a yes"
no    "without asking back first"                    "$out" "Want me to save that to <that file>?"
ok    "and so is \"please remember\" with no \"that\"" \
      "$(printf '{"user_prompt":"Please remember I teach on Tuesdays"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")" "already a yes"
out=$(printf '{"user_prompt":"Too long. From now on keep it to three lines"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")
ok    "\"from now on\" after pushback is written down too" "$out" "already a yes"
no    "with no question about it"                    "$out" "Want me to write that down"
out=$(printf '{"user_prompt":"i always work in the mornings"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")
ok    "a fact only stated in passing is still asked about" "$out" "Want me to save that to"
no    "and is not taken as a yes"                    "$out" "already a yes"
quiet "\"I can't remember\" asks nothing to be kept" \
      "$(printf '{"user_prompt":"I can'"'"'t remember the name of that plant"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
ok    "/save-to-my-os takes \"remember\" as the yes too" \
      "$(cat "$REAL/.claude/skills/save-to-my-os/SKILL.md")" 'If they said "remember ..." or "from now on ...", that is the yes.'

# ---------------------------------------------------------------------------
say "Surviving a mess"
blank_folder
# ---------------------------------------------------------------------------
say "Not filing a second copy of something already here"
blank_folder
writeup memory 2026-01-01-retrieval.md '# Retrieval practice

Roediger and Karpicke measured it. After a week the recall group kept 61% and
the rereading group kept 40%. Most people pick rereading anyway.'

# The real bug, 2026-08-27: the setup filed this study twice under two names,
# with the original in the same folder. Names share nothing; the figures do.
DUP='# The testing effect

Roediger and Karpicke, 2006. Recall practice kept 61% after one week against
40% for rereading, and people still choose rereading.'
out=$(second notes/other/sources/2026-08-27-testing-effect.md "$DUP")
ok "a second write-up of the same study is spotted" "$out" "already be a write-up of this"

# The warning used to ride along with the write, so Claude read it after the
# second copy already existed. The first try is now turned back.
ok "and the first try is stopped, so it is read in time" "$out" '"permissionDecision":"deny"'
quiet "a second try at the same file goes through" \
   "$(second notes/other/sources/2026-08-27-testing-effect.md "$DUP")"

ok "it names the file to open"                     \
   "$(second notes/other/sources/2026-08-28-recall.md '# The testing effect

Roediger and Karpicke, 2006. Recall kept 61% against 40% for rereading.')" \
   "2026-01-01-retrieval.md"

# Years were counted as figures. The same host, the same lab and one shared
# year looked like a copy, so each new episode of a show they follow got flagged.
writeup sleep 2026-02-04-morning-light.md '# Morning light

Huberman on a 2023 Stanford study: ten minutes of light after waking helps sleep.'
quiet "the same host, lab and year on a new claim is not a copy" \
   "$(second notes/coffee/sources/2026-09-20-caffeine-timing.md '# Caffeine timing

Huberman again, citing a 2023 Stanford paper: wait before the first coffee.')"

quiet "a genuinely different source says nothing"  \
   "$(second notes/other/sources/2026-08-27-sourdough.md '# Sourdough

Warmer dough rises faster. Nobody measured it. Flour, water, salt.')"

quiet "editing an existing write-up is not a second copy" \
   "$(second notes/memory/sources/2026-01-01-retrieval.md '# Retrieval practice

Roediger and Karpicke, 61% against 40% after a week.')"

quiet "a sources README is scaffolding, not a record" \
   "$(second notes/other/sources/README.md '# Other sources

Roediger and Karpicke, 61% and 40% after a week, one per file.')"

quiet "a file outside sources/ is none of its business" \
   "$(second notes/other/what-i-think.md '# Other

Roediger and Karpicke, 61% against 40% after a week.')"

# ---------------------------------------------------------------------------
for h in session-start spot-worth-saving protect-the-record keep-tidy plain-words no-second-copy; do
  bad=0
  for junk in '' '{}' 'not json' '{"broken":' 'null' '[]'; do
    printf '%s' "$junk" | "$REAL/.claude/hooks/$h.sh" >/dev/null 2>&1 || bad=1
  done
  if [ $bad -eq 0 ]; then pass=$((pass+1)); printf '  ok    %s survives junk input\n' "$h"
  else fail=$((fail+1)); printf '  FAIL  %s fell over on junk input\n' "$h"; fi
done

blank_folder; subject s '# s

What this covers: x.
'; on_subject_map s
for gone in notes work me notes/subjects.md work/projects.md; do
  mv "$FIX/$gone" "$FIX/moved-aside" 2>/dev/null
  if printf '{}' | "$REAL/.claude/hooks/session-start.sh" >/dev/null 2>&1
    then pass=$((pass+1)); printf '  ok    survives a missing %s\n' "$gone"
    else fail=$((fail+1)); printf '  FAIL  fell over with %s missing\n' "$gone"; fi
  mv "$FIX/moved-aside" "$FIX/$gone" 2>/dev/null
done

blank_folder; project frozen; on_project_map frozen; age "$MID" "$FIX/work/frozen"
out=$(bash -c "source '$REAL/.claude/hooks/lib.sh'
               find() { case \"\$*\" in *newermt*) return 2;; esac; command find \"\$@\"; }
               stale_folders work 90" 2>&1)
quiet "a find that cannot answer says nothing, not everything" "$out"

# ---------------------------------------------------------------------------
say "Getting the words out"

# The commands in getting-the-text.md, run as written, with a stand-in yt-dlp
# and /tmp moved into the test folder. Every video went into one folder, and
# the clean-up read back every English file in it: the second video came out
# mixed with the first, and a Spanish one came out as the first one again.
# The test folder goes in quoted: a space in its name made rm -rf take the
# part before the space.
blank_folder
mkdir -p "$FIX/bin" "$FIX/tmp"
cat > "$FIX/bin/yt-dlp" <<'EOF'
#!/bin/sh
lang=en; for a; do [ "$p" = --sub-lang ] && lang=$a; p=$a; done
printf 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nwords from %s\n' "$a" > "$a.$lang.vtt"
EOF
chmod +x "$FIX/bin/yt-dlp"
video() {  # video <id> <language>
  awk '/^## /{on=/YouTube/} on && /^```$/{b=0} on && b; on && /^```bash/{b=1}' "$REAL/.claude/guides/getting-the-text.md" \
    | sed -e "s#/tmp/#\"$FIX\"/tmp/#g" -e "s#<the URL>#$1#" -e "s#--sub-lang en#--sub-lang $2#" \
    | (cd "$FIX" && PATH="$FIX/bin:$PATH" TMPDIR="$FIX/tmp" bash 2>&1)
}
video first en >/dev/null
out=$(video second en)
ok "a video's words come out"                   "$out" "words from second"
no "without the video read before it"           "$out" "words from first"
no "and without file names in front of lines"   "$out" ".vtt"
ok "a video in Spanish comes out too"           "$(video third es)" "words from third"

# Claude's file reader refuses Word and PowerPoint files, and the guide said
# only "Read it".
mkdir -p "$FIX/doc/word" "$FIX/doc/ppt/slides"
printf '<w:body><w:p><w:r><w:t>Mark in green</w:t></w:r></w:p><w:p><w:r><w:t>Never in red</w:t></w:r></w:p></w:body>' > "$FIX/doc/word/document.xml"
printf '<a:p><a:r><a:t>Slide one</a:t></a:r></a:p>' > "$FIX/doc/ppt/slides/slide1.xml"
(cd "$FIX/doc" && python3 -m zipfile -c ../policy.docx word && python3 -m zipfile -c ../talk.pptx ppt)
office() {  # office <file> <what the guide's command names inside it>
  grep -F "$2" "$REAL/.claude/guides/getting-the-text.md" | grep -F unzip | sed "s#<the file>#$1#" | bash 2>&1
}
needs unzip
out=$(office "$FIX/policy.docx" word/document.xml)
ok "a Word file's words come out"               "$(printf '%s' "$out" | grep -x 'Never in red')" "Never in red"
no "without the markup around them"             "$out" "<w:"
ok "and a PowerPoint file's"                    "$(office "$FIX/talk.pptx" ppt/slides)" "Slide one"
needs

# ---------------------------------------------------------------------------
say "The daily save"
needs git

# The message used to be built in a variable called GROUPS, which bash keeps
# for itself. Every automatic commit said "- : 20 file(s)" and nothing else.
blank_folder
mkdir -p "$FIX/.claude/scripts" "$FIX/fakehome"
cp "$REAL/.claude/scripts/daily-commit.sh" "$FIX/.claude/scripts/"
printf 'x\n' > "$FIX/notes/a.md"; printf 'x\n' > "$FIX/notes/b.md"
out=$(cd "$FIX" && git init -q && git add -A && \
      GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t \
      git commit -qm start && printf 'y\n' >> notes/a.md && printf 'y\n' > notes/c.md && \
      HOME="$FIX/fakehome" GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t \
      "$FIX/.claude/scripts/daily-commit.sh" >/dev/null 2>&1; git -C "$FIX" log -1 --format=%B 2>&1)
ok "the daily save says which room changed" "$out" "notes: 2 file(s)"
no "the daily save does not print a group number" "$out" " : "
needs

# ---------------------------------------------------------------------------
say "Getting a newer version"

# The folder this design grew out of had an upgrade that put back a skill its
# owner had deleted, and wrote over a file they had shortened by hand. These
# are the checks that it can't happen here.
UP="$FIX/up"; rm -rf "$UP"; mkdir -p "$UP"
put() { mkdir -p "$(dirname "$1")"; printf '%s\n' "$2" > "$1"; }
upgrade() { bash "$UP/mine/.claude/scripts/upgrade.sh" "$@" 2>&1; }
record() { bash "$REAL/.claude/scripts/upgrade.sh" --write-record "$1" "$2" 2>&1; }

v1="$UP/v1"; mkdir -p "$v1/.claude/scripts"
cp "$REAL/.claude/scripts/upgrade.sh" "$v1/.claude/scripts/"
put "$v1/.claude/hooks/a.sh" "old a";           put "$v1/.claude/skills/edited/SKILL.md" "old"
put "$v1/.claude/skills/deleted/SKILL.md" "x";  put "$v1/.claude/skills/dropped/SKILL.md" "d"
put "$v1/CLAUDE.md" "old rules";                put "$v1/notes/subjects.md" "blank map"
put "$v1/.claude/hooks/same.sh" "same";          put "$v1/me/who-i-am.md" "TO FILL"
put "$v1/.claude/skills/reworked/SKILL.md" "r"
record "$v1" v1 >/dev/null

v2="$UP/v2"; cp -R "$v1" "$v2"
put "$v2/.claude/hooks/a.sh" "new a";           put "$v2/.claude/skills/edited/SKILL.md" "new"
put "$v2/.claude/skills/deleted/SKILL.md" "x2"; rm -rf "$v2/.claude/skills/dropped" "$v2/.claude/skills/reworked"
put "$v2/CLAUDE.md" "new rules";                put "$v2/notes/subjects.md" "new blank map"
put "$v2/.claude/hooks/b.sh" "b"; chmod -x "$v2/.claude/hooks/b.sh"   # a zip that lost the flag
record "$v2" v2 >/dev/null

mine="$UP/mine"; cp -R "$v1" "$mine"
put "$mine/.claude/skills/edited/SKILL.md" "my own version"
rm -rf "$mine/.claude/skills/deleted"
put "$mine/notes/subjects.md" "my real subjects"
put "$mine/me/who-i-am.md" "me";                 put "$mine/me/my-setup.md" "my setup"
put "$mine/.claude/skills/reworked/SKILL.md" "my reworked one"

out=$(upgrade --preview "$v2")
ok "a preview says it is a preview" "$out" "PREVIEW"
ok "a preview lists what would be replaced" "$out" ".claude/hooks/a.sh"
ok "a preview changes nothing" "$(cat "$mine/.claude/hooks/a.sh")" "old a"
ok "the report names the folder it upgrades" "$out" "Upgrading $mine from"

# --preview after the folder used to be ignored, and the real upgrade ran.
out=$(upgrade "$v2" --preview)
ok "--preview after the folder is still a preview" "$out" "PREVIEW"
ok "and changes nothing either" "$(cat "$mine/.claude/hooks/a.sh")" "old a"

out=$(upgrade "$v2")
ok "a file never changed gets the new version" "$(cat "$mine/.claude/hooks/a.sh")" "new a"
ok "CLAUDE.md never filled in gets the new version" "$(cat "$mine/CLAUDE.md")" "new rules"
ok "and the old one is kept, just in case" "$(cat "$mine/.claude/.upgrade/before/CLAUDE.md" 2>&1)" "old rules"
ok "a skill you edited stays yours" "$(cat "$mine/.claude/skills/edited/SKILL.md")" "my own version"
ok "its new version waits beside it" "$(cat "$mine/.claude/.upgrade/new/.claude/skills/edited/SKILL.md.new" 2>&1)" "new"
ok "a filled-in map stays yours" "$(cat "$mine/notes/subjects.md")" "my real subjects"
if [ -e "$mine/.claude/skills/deleted" ]; then fail=$((fail+1)); printf '  FAIL  a skill you deleted came back\n'
else pass=$((pass+1)); printf '  ok    a skill you deleted stays deleted\n'; fi
ok "the report says it stays deleted" "$out" "stay deleted"
if [ -x "$mine/.claude/hooks/b.sh" ]; then pass=$((pass+1)); printf '  ok    a new hook arrives and can be run\n'
else fail=$((fail+1)); printf '  FAIL  a new hook arrived without the flag that lets it run\n'; fi
if [ -e "$mine/.claude/skills/dropped" ]; then fail=$((fail+1)); printf '  FAIL  a skill the template dropped is still there\n'
else pass=$((pass+1)); printf '  ok    a skill the template dropped is moved out\n'; fi
ok "and kept, not deleted" "$(ls "$mine/.claude/.upgrade/removed/.claude/skills/dropped" 2>&1)" "SKILL.md"
ok "your own files are never touched" "$(cat "$mine/me/my-setup.md")" "my setup"
# Two paths no check went through, so either could break and every check
# still passed: a form they filled in that the template left alone, and a
# dropped skill they had changed.
ok "a form you filled in stays yours" "$(cat "$mine/me/who-i-am.md")" "me"
ok "a dropped skill you changed stays yours" "$(cat "$mine/.claude/skills/reworked/SKILL.md" 2>&1)" "my reworked one"
ok "and the report says why" "$out" "you changed them, so they stay"
ok "the record says which version you are on" "$(cat "$mine/.claude/shipped.tsv")" "version	v2"

# The next version, a month later. The snag was that a deleted skill came
# back on the *next* upgrade, not the first.
v3="$UP/v3"; cp -R "$v2" "$v3"; put "$v3/.claude/skills/deleted/SKILL.md" "x3"; record "$v3" v3 >/dev/null
rm -rf "$mine/.claude/.upgrade"
out=$(upgrade "$v3")
if [ -e "$mine/.claude/skills/deleted" ]; then fail=$((fail+1)); printf '  FAIL  a deleted skill came back on the second upgrade\n'
else pass=$((pass+1)); printf '  ok    a deleted skill stays deleted on the second upgrade too\n'; fi
out=$(upgrade "$v3")
ok "running it twice finds nothing to do" "$out" "Nothing to do"

# Upgrading twice without finishing the first used to throw away what the
# first one left for you.
mkdir -p "$mine/.claude/.upgrade/new"; printf 'x\n' > "$mine/.claude/.upgrade/new/CLAUDE.md.new"
out=$(upgrade "$v3")
ok "an upgrade won't start over the top of an unfinished one" "$out" "isn't finished"
ok "and one file left is spoken of as one" "$out" "one of your own files with a newer version beside it"
ok "and what it left is still there" "$(ls "$mine/.claude/.upgrade/new")" "CLAUDE.md.new"
# It used to say to delete that folder, which lost the template's changes for
# good, or to finish with /update-os, which stopped at "Nothing to do".
ok "it sends you back to /update-os to finish" "$out" "Type /update-os"
no "and never says to delete what is still waiting" "$out" "delete"
rm "$mine/.claude/.upgrade/new/CLAUDE.md.new"
out=$(upgrade "$v3")
ok "once nothing is waiting, it says the backup is in the way" "$out" "Delete that folder"
rm -rf "$mine/.claude/.upgrade"

# A copy that failed used to be reported as done, and the record moved on
# anyway. Next time a new skill that never arrived counted as one you had
# deleted, and a file never replaced as one you had changed.
v4="$UP/v4"; cp -R "$v2" "$v4"; put "$v4/.claude/skills/fresh/SKILL.md" "the template's new skill"; record "$v4" v4 >/dev/null
stuck="$UP/stuck"; cp -R "$v1" "$stuck"
put "$stuck/.claude/skills/fresh" "a file where the new skill's folder goes"
chmod 444 "$stuck/.claude/hooks/a.sh"
out=$(bash "$stuck/.claude/scripts/upgrade.sh" "$v4" 2>&1); rc=$?
ok "a file it couldn't write is named" "$out" "Couldn't write"
ok "and the upgrade isn't called done" "exit $rc" "exit 1"
# It said the next upgrade tries them again, and the next one refused: the
# backup it had just made was in the way. Now it says so, and that works.
ok "and it says what to do before trying again" "$out" "delete .claude/.upgrade/ and run it again"
rm "$stuck/.claude/skills/fresh"; chmod 644 "$stuck/.claude/hooks/a.sh"
out=$(bash "$stuck/.claude/scripts/upgrade.sh" "$v4" 2>&1)
ok "which is what the next upgrade asks for" "$out" "Delete that folder"
rm -rf "$stuck/.claude/.upgrade"
out=$(bash "$stuck/.claude/scripts/upgrade.sh" "$v4" 2>&1)
ok "the next upgrade brings the new skill in" "$(cat "$stuck/.claude/skills/fresh/SKILL.md" 2>/dev/null)" "the template's new skill"
ok "and replaces the file it couldn't before" "$(cat "$stuck/.claude/hooks/a.sh")" "new a"

# A skill of your own that shares a name with a new one in the template used
# to count as the template's, edited, so step 4 offered to merge the two.
own="$UP/own"; cp -R "$v1" "$own"; put "$own/.claude/skills/fresh/SKILL.md" "my own routine"
out=$(bash "$own/.claude/scripts/upgrade.sh" "$v4" 2>&1)
ok "your own skill with a new skill's name is listed apart" "$out" "you already have your own file with this name"
ok "and stays yours" "$(cat "$own/.claude/skills/fresh/SKILL.md")" "my own routine"
no "and isn't recorded as the template's" "$(grep -v '^theirs:' "$own/.claude/shipped.tsv")" "skills/fresh/"
# It was put beside theirs again on every upgrade after, so "Nothing to do"
# never came, and every session said an upgrade was left unfinished.
rm -rf "$own/.claude/.upgrade"
out=$(bash "$own/.claude/scripts/upgrade.sh" "$v4" 2>&1)
ok "upgrading to the same version again finds nothing to do" "$out" "Nothing to do"
v5="$UP/v5"; cp -R "$v4" "$v5"; put "$v5/.claude/skills/fresh/SKILL.md" "the template's skill, changed"; record "$v5" v5 >/dev/null
out=$(bash "$own/.claude/scripts/upgrade.sh" "$v5" 2>&1)
ok "but a change to the template's one is shown" "$out" "you already have your own file with this name"
ok "and yours still stays yours" "$(cat "$own/.claude/skills/fresh/SKILL.md")" "my own routine"

# With no folder named, it fetches the newest version itself. A local ZIP
# stands in for GitHub here, so the checks never need the internet. These
# run from $UP: inside another OS folder, a script with no folder named
# refuses to start.
needs curl
later="$UP/later"; cp -R "$v2" "$later"
UPURL="$(printf '%s' "$UP" | sed 's/ /%20/g')"   # a space in the test folder's name breaks a URL
(cd "$UP" && rm -f v3.zip && python3 -m zipfile -c v3.zip v3)
out=$(cd "$UP" && OS_TEMPLATE_ZIP_URL="file://$UPURL/v3.zip" bash "$later/.claude/scripts/upgrade.sh" 2>&1)
ok "with no folder named, it downloads the newest version" "$out" "Downloaded the newest version"
ok "and upgrades to it" "$(cat "$later/.claude/shipped.tsv")" "version	v3"
out=$(cd "$UP" && OS_TEMPLATE_ZIP_URL="file://$UPURL/nothing-here.zip" bash "$later/.claude/scripts/upgrade.sh" --preview 2>&1)
ok "a download that fails says so plainly" "$out" "couldn't download"

# unzip isn't on every Linux. Without it, every update blamed the download.
nozip="$UP/no-unzip"; mkdir -p "$nozip"
for t in awk cat chmod cksum cp curl dirname find head mkdir mktemp mv python3 rm rmdir sed sort; do
  ln -s "$(command -v "$t")" "$nozip/$t"
done
later2="$UP/later2"; cp -R "$v2" "$later2"
out=$(cd "$UP" && PATH="$nozip" OS_TEMPLATE_ZIP_URL="file://$UPURL/v3.zip" "$BASH" "$later2/.claude/scripts/upgrade.sh" 2>&1)
ok "with no unzip, it opens the download another way" "$(cat "$later2/.claude/shipped.tsv")" "version	v3"
needs
rm "$nozip/python3"
out=$(cd "$UP" && PATH="$nozip" OS_TEMPLATE_ZIP_URL="file://$UPURL/v3.zip" "$BASH" "$later2/.claude/scripts/upgrade.sh" --preview 2>&1)
ok "and with no way at all, it says unzip is missing" "$out" "needs unzip"

# The download's own copy of this script, run from inside your folder, used
# to upgrade the download instead and say your folder already matched.
out=$(cd "$mine" && OS_TEMPLATE_ZIP_URL="file://$UPURL/v3.zip" bash "$later/.claude/scripts/upgrade.sh" --preview 2>&1)
ok "another folder's copy won't fetch from inside yours" "$out" "upgrades the folder it sits in"

# Only a fresh download. Pointing it at a folder someone has used would copy
# their files into yours.
used="$UP/used"; cp -R "$v3" "$used"; put "$used/CLAUDE.md" "somebody's own rules"
out=$(upgrade "$used")
ok "a used folder is refused" "$out" "not a fresh copy"
ok "and names what gave it away" "$out" "changed: CLAUDE.md"
ok "and nothing was copied from it" "$(cat "$mine/CLAUDE.md")" "new rules"
out=$(upgrade "$mine")
ok "pointing it at itself is refused" "$out" "that is this folder"

# A copy from before there was a record: nothing it can't tell apart gets
# replaced, even if that means more to look over.
old="$UP/old"; cp -R "$v1" "$old"; rm -f "$old/.claude/shipped.tsv"
out=$(bash "$old/.claude/scripts/upgrade.sh" "$v2" 2>&1)
ok "a copy with no record says so" "$out" "no record"
ok "and keeps anything that differs" "$(cat "$old/CLAUDE.md")" "old rules"

# An upgrade half done is easy to forget: the new versions just sit there.
blank_folder
out=$(rot)
no "no upgrade, no mention of one" "$out" "upgrade"
put "$FIX/.claude/.upgrade/new/CLAUDE.md.new" "x"
out=$(rot)
ok "a half-finished upgrade gets mentioned" "$out" "Offer /update-os"
# One file left read "1 of your own files with a newer version beside them".
ok "one file left is spoken of as one" "$out" "one of your own files with a newer version beside it."
put "$FIX/.claude/.upgrade/new/me/who-i-am.md.new" "x"
ok "and two as two" "$(rot)" "2 of your own files with a newer version beside them."
ok "and /update-os picks it up there" "$(cat "$REAL/.claude/skills/update-os/SKILL.md")" "straight to step 4"

# Nothing ever said a newer version was out, so a folder downloaded on day one
# kept day-one bugs. /catch-me-up asks GitHub, with a stand-in curl here.
blank_folder
mkdir -p "$FIX/bin"
newer() {  # newer <their version> <GitHub's tag, or "fail">
  printf '# What the template shipped\n# version\t%s\n' "$1" > "$FIX/.claude/shipped.tsv"
  if [ "$2" = fail ]; then printf '#!/bin/sh\nexit 28\n' > "$FIX/bin/curl"
  else printf '#!/bin/sh\necho "$*" > "%s/curl-args"\nprintf %s\n' "$FIX" "'{\"url\": \"x\",\n  \"tag_name\": \"v$2\",\n  \"name\": \"y\"}\\n'" > "$FIX/bin/curl"; fi
  chmod +x "$FIX/bin/curl"
  (cd "$FIX" && PATH="$FIX/bin:$PATH" bash -c "$(awk '/^# whether a newer version is out/{f=1} f&&/^$/{exit} f' "$REAL/.claude/skills/catch-me-up/SKILL.md")" 2>&1)
}
ok    "/catch-me-up says when a newer version is out" "$(newer 2026-09-27.9 2026-09-27.10)" "newer version out: 2026-09-27.10"
ok    "and sends GitHub nothing but the question" \
      "$([ "$(cat "$FIX/curl-args" 2>&1)" = "-fsS --max-time 5 https://api.github.com/repos/zidery333/os-template/releases/latest" ] && echo "nothing else")" "nothing else"
quiet "it says nothing when they have the newest" "$(newer 2026-09-27.3 2026-09-27.3)"
quiet "or something newer still"                  "$(newer 2026-09-28.1 2026-09-27.3)"
quiet "or when GitHub can't be reached"           "$(newer 2026-09-27.3 fail)"
ok    "and the catch-up tells them in one line"   "$(cat "$REAL/.claude/skills/catch-me-up/SKILL.md")" "/update-os brings it in"
# Step 4 said to take any hook whole, but the lists in hooks/ are the
# person's own. Taken whole, their answers and added words would be lost.
for f in "$REAL"/.claude/hooks/*.tsv; do
  ok "/update-os never takes $(basename "$f") whole" "$(cat "$REAL/.claude/skills/update-os/SKILL.md")" "$(basename "$f")"
done

# A download from GitHub carries a LICENSE file at the top. It belongs there.
blank_folder
printf 'MIT\n' > "$FIX/LICENSE"
no "the license file is not called loose" "$(rot)" "LICENSE"

# ---------------------------------------------------------------------------
say "The snag list"

# .claude/snags.md is the person's own list from /snag. The template never
# ships one, an upgrade never touches theirs, and no check nags about it.
SN="$FIX/snag"; rm -rf "$SN"
s1="$SN/s1"; mkdir -p "$s1/.claude/scripts"
cp "$REAL/.claude/scripts/upgrade.sh" "$s1/.claude/scripts/"
put "$s1/.claude/hooks/a.sh" "old a"; record "$s1" s1 >/dev/null
s2="$SN/s2"; cp -R "$s1" "$s2"
put "$s2/.claude/hooks/a.sh" "newer a"
put "$s2/.claude/snags.md" "a stray list in the new version"
record "$s2" s2 >/dev/null
no "a stray snag list never goes in the record" "$(cat "$s2/.claude/shipped.tsv")" "snags.md"
snagger="$SN/mine"; cp -R "$s1" "$snagger"
put "$snagger/.claude/snags.md" "- first 2026-09-01 · last 2026-09-02 · 2 times · my snag"
out=$(bash "$snagger/.claude/scripts/upgrade.sh" "$s2" 2>&1)
ok "the upgrade still happens" "$(cat "$snagger/.claude/hooks/a.sh")" "newer a"
ok "and the person's snag list survives it" "$(cat "$snagger/.claude/snags.md")" "2 times · my snag"
no "and the upgrade never mentions it" "$out" "snags.md"
no "and it is not in their record" "$(cat "$snagger/.claude/shipped.tsv")" "snags.md"

blank_folder
{ printf '# Snags\n\n## x\n\n## x\n'; seq 1 300; } > "$FIX/.claude/snags.md"
quiet "a long snag list is not a notes file"      "$(tidy "$FIX/.claude/snags.md")"
quiet "and is no record to guard"                 "$(guard Write .claude/snags.md)"
quiet "and is not a loose file"                   "$(rot)"
quiet "and a folder with one says nothing at all" "$(health)"
p4="t$RANDOM"
quiet "a snag list shown in a code block is not checked for words" \
      "$(python3 -c 'import json,sys;print(json.dumps({"last_assistant_message":"```\nSnags from my OS folder\n- first 2026-09-01 · last 2026-09-02 · 2 times · it tried to leverage a heuristic\n```","prompt_id":sys.argv[1]}))' "$p4" | "$REAL/.claude/hooks/plain-words.sh")"
quiet "a /snag message is not a fact about them" \
      "$(printf '{"user_prompt":"/snag i always have to add a new subject to the map by hand"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
quiet "text pasted after /learn is its author talking, not them" \
      "$(python3 -c 'import json;m="/learn How I cut my marking in half\n\nI use a simple grid now. I never went back.";print(json.dumps({"user_prompt":m}))' | "$REAL/.claude/hooks/spot-worth-saving.sh")"

# ---------------------------------------------------------------------------
say "Odd names, odd machines"

# A folder called "My OS". The hook paths in settings.json had no quotes, so
# every hook failed on every message, write and reply.
blank_folder
SP="$FIX/My OS"; mkdir -p "$SP"; cp -R "$REAL/.claude" "$SP/"; rm -rf "$SP/.claude/.state"
cp -R "$FIX/notes" "$FIX/work" "$FIX/me" "$FIX/CLAUDE.md" "$SP/"
bad=""; n=0
while IFS= read -r cmd; do
  n=$((n+1)); CLAUDE_PROJECT_DIR="$SP" sh -c "$cmd" </dev/null >/dev/null 2>&1
  rc=$?; [ "$rc" -lt 2 ] || bad="$bad $rc:$cmd"
done < <(python3 -c 'import json,sys
for evs in json.load(open(sys.argv[1]))["hooks"].values():
  for e in evs:
    for h in e["hooks"]: print(h["command"])' "$REAL/.claude/settings.json")
# With settings.json unreadable, there was nothing to run, and this passed.
[ "$n" -gt 0 ] || bad="no hooks found in settings.json"
quiet "every hook runs from a folder with a space in its name" "$bad"
chmod -x "$SP"/.claude/hooks/*.sh
out=$(CLAUDE_PROJECT_DIR="$SP" sh -c "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["hooks"]["SessionStart"][0]["hooks"][0]["command"])' "$REAL/.claude/settings.json")" </dev/null 2>&1; echo "rc=$?")
ok "and still runs if a download lost the run flag" "$out" "rc=0"

# The folder's name used to be read as a pattern. "OS [1]" made the guard on
# decision logs go quiet.
blank_folder
BR="$FIX/OS [1]"; mkdir -p "$BR/work/p"; printf '# p\n' > "$BR/work/p/decisions.md"
out=$(printf '{"tool_name":"Edit","tool_input":{"file_path":"%s"}}' "$BR/work/p/decisions.md" \
      | CLAUDE_PROJECT_DIR="$BR" "$REAL/.claude/hooks/protect-the-record.sh" 2>&1)
ok "a folder named with brackets still guards its decision logs" "$out" "decision log"
ok "and the warning goes to Claude, not only to the screen" "$out" "hookSpecificOutput"

# Project and subject folders with spaces in their names.
blank_folder
mkdir -p "$FIX/work/my project"; printf '# p\n**Next:** x\n' > "$FIX/work/my project/brief.md"
printf '# d\n' > "$FIX/work/my project/decisions.md"; on_project_map "my project"
age "$MID" "$FIX/work/my project"
ok "an old project with a space in its name is named whole" "$(rot)" "work/my project has not changed"
blank_folder
for n in "a one" "b two" "c three" "d four" "e five"; do project "$n"; on_project_map "$n"; done
ok "five projects with spaces in their names are counted as five" "$(rot)" "5 projects in work/"

# The file check looks at notes, not at the code inside a project.
blank_folder
mkdir -p "$FIX/work/app/src"; seq 1 400 > "$FIX/work/app/src/server.py"
quiet "a long code file inside a project is left alone" "$(CLAUDE_PROJECT_DIR="$FIX" tidy "$FIX/work/app/src/server.py")"

# Words in bullet points used to be skipped by the word check.
p3="bullet-$$"
ok "a stuffy word in a bullet point is still caught" \
   "$(printf '{"last_assistant_message":"- we should leverage this","prompt_id":"%s"}' "$p3" | "$REAL/.claude/hooks/plain-words.sh")" "leverage"

# A question is not a fact about the person.
quiet "\"how do I use git\" is a question, not something to save" \
   "$(printf '{"prompt":"how do I use git","user_prompt":"how do I use git"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
quiet "pushback words inside pasted text are not pushback" \
   "$(python3 -c 'import json;m="look at this <pasted_content id=\"x1\">it was too long and too many words</pasted_content id=\"x1\">";print(json.dumps({"prompt":m,"user_prompt":m}))' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
ok "but pushback outside the paste still counts" \
   "$(python3 -c 'import json;m="too long. <pasted_content id=\"x1\">log</pasted_content id=\"x1\">";print(json.dumps({"prompt":m,"user_prompt":m}))' | "$REAL/.claude/hooks/spot-worth-saving.sh")" "pushback"
quiet "asking for a paragraph is not pushback" \
   "$(printf '{"prompt":"write me a paragraph about my trip","user_prompt":"write me a paragraph about my trip"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
# "Too many", "too much" and "shorter" on their own fired on ordinary requests.
quiet "shortening their own letter is not pushback" \
   "$(printf '{"user_prompt":"Can you make my cover letter shorter"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
quiet "too many tomatoes is not pushback" \
   "$(printf '{"user_prompt":"I have too many tomatoes this year. What can I cook with them?"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
quiet "bread too much like a brick is not pushback" \
   "$(printf '{"user_prompt":"My sourdough came out too much like a brick"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
ok "but \"that was too much\" still is" \
   "$(printf '{"user_prompt":"that was too much"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")" "pushback"
ok "and so is \"shorter\" on its own" \
   "$(printf '{"user_prompt":"Shorter."}' | "$REAL/.claude/hooks/spot-worth-saving.sh")" "pushback"
# Narrowing that caught "it's too hot" and "will be shorter" instead, and lost
# "Too much." on its own, and a curly "That’s" with no language set.
quiet "\"it's too hot\" is about the weather, not the answer" \
   "$(printf '{"user_prompt":"It'"'"'s too hot to work in the garden today"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
quiet "\"the days will be shorter\" is not pushback" \
   "$(printf '{"user_prompt":"In winter the days will be shorter, what should I plant"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"
ok "\"Too much.\" on its own is pushback" \
   "$(printf '{"user_prompt":"Too much."}' | "$REAL/.claude/hooks/spot-worth-saving.sh")" "pushback"
ok "and so is \"That’s too much\" with no language set" \
   "$(printf '{"user_prompt":"That\\u2019s too much"}' | LC_ALL=C "$REAL/.claude/hooks/spot-worth-saving.sh")" "pushback"

# A Mac without Apple's developer tools has a python3 that only pops up an
# install box. The hooks must stay quiet, and say once what is missing.
blank_folder
out=$(OS_PRETEND_NO_PYTHON=1 "$REAL/.claude/hooks/session-start.sh" </dev/null 2>&1)
ok "no python3: the session check says what to install" "$out" "xcode-select --install"
ok "and what to do on Linux, where there is no xcode-select" "$out" "apt install python3"
ok "and what it says is still valid JSON" "$(printf '%s' "$out" | python3 -c 'import json,sys;json.load(sys.stdin);print("valid")' 2>&1)" "valid"
quiet "no python3: the other hooks stay quiet" \
   "$(printf '{"prompt":"remember that I hate emojis"}' | OS_PRETEND_NO_PYTHON=1 "$REAL/.claude/hooks/spot-worth-saving.sh" 2>&1)"

# On Windows the hooks are handed paths with backslashes, so every guard that
# goes by path matched nothing, and nothing said so.
blank_folder
out=$(bash -c "uname() { echo MINGW64_NT-10.0-19045; }; source '$REAL/.claude/hooks/lib.sh' && os_health" 2>&1)
ok "on Windows the session check says the guards are off" "$out" "doesn't support yet"
no "and on a Mac or Linux it says nothing of the sort" "$(health)" "Windows"

# /setup's own look at the machine ran git there, popping up the same box,
# and wrote the stand-in python3 down as installed. Linux has no
# xcode-select at all, and that must not read as tools missing.
blank_folder
mkdir -p "$FIX/bin"
printf '#!/bin/sh\necho "ProductVersion: 15.0"\n' > "$FIX/bin/sw_vers"
printf '#!/bin/sh\nexit 2\n'                     > "$FIX/bin/xcode-select"
printf '#!/bin/sh\necho git-was-run\n'           > "$FIX/bin/git"
chmod +x "$FIX/bin/"*
look=$(awk '/^## What to check without asking/{f=1} f&&/^```bash/{b=1;next} b&&/^```/{exit} b' "$REAL/.claude/skills/setup/SKILL.md")
out=$(cd "$FIX" && PATH="$FIX/bin:/usr/bin:/bin" bash -c "$look" 2>&1)
ok "setup on a Mac without developer tools says so"   "$out" "developer tools: missing"
no "and doesn't run git, which pops up the box"       "$out" "git-was-run"
no "and doesn't write the stand-in python3 down"      "$out" "python3: /usr/bin/python3"
printf '#!/bin/sh\nexit 1\n' > "$FIX/bin/sw_vers"
out=$(cd "$FIX" && PATH="$FIX/bin:/usr/bin:/bin" bash -c "$look" 2>&1)
no "setup on Linux doesn't ask for Apple's developer tools" "$out" "developer tools"
ok "and still looks up git"                                 "$out" "git-was-run"

# A download has no git history, and nothing ever started one: no undo, and
# /wrapup and the nightly save had nothing to add to. /setup starts it now,
# with a script, so no permission box shows the person a page of shell.
ok "setup starts the history with the script" "$(cat "$REAL/.claude/skills/setup/SKILL.md")" 'bash .claude/scripts/history.sh start "Their Name"'
ok "and the script is allowed to run without a box" "$(cat "$REAL/.claude/settings.json")" '"Bash(bash .claude/scripts/history.sh:*)"'
start_history() { (cd "$FIX" && "$@" bash .claude/scripts/history.sh start "Ana Lopez" 2>&1); }
history_script
printf '#!/bin/sh\necho "ProductVersion: 15.0"\n' > "$FIX/bin/sw_vers"
out=$(start_history env PATH="$FIX/bin:/usr/bin:/bin")
ok "on a Mac without developer tools, setup starts no history" "$out" "history: no git"
no "and doesn't run git, which pops up the box"               "$out" "git-was-run"
# /wrapup and /catch-me-up ran git there too.
out=$(cd "$FIX" && PATH="$FIX/bin:/usr/bin:/bin" bash .claude/scripts/history.sh check 2>&1)
ok "on that Mac, /wrapup offers no save"                     "$out" "save: no git"
no "and doesn't run git either"                              "$out" "git-was-run"
rm -rf "$FIX/bin"
needs git
G="GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t"
# git has no name set. On a Mac git makes one up from the computer, and that
# went in the first save instead of their name.
nameless="HOME=$FIX/fakehome XDG_CONFIG_HOME=$FIX/fakehome GIT_CONFIG_NOSYSTEM=1"
blank_folder; history_script; mkdir -p "$FIX/fakehome"
out=$(start_history env $nameless)
ok "setup starts a history in a downloaded folder"   "$out" "history: started"
ok "with everything in it"                            "$(git -C "$FIX" ls-files 2>&1)" "me/who-i-am.md"
ok "even when git has no name yet"                    "$(git -C "$FIX" log -1 --format='%s: %an <%ae>' 2>&1)" "Set up: Ana Lopez <me@localhost>"
# Its own already: nothing new is started over it.
out=$(start_history env $G)
ok "a folder with its own history keeps it"           "$out" "history: already there"
ok "and gets no second first commit"                  "$(git -C "$FIX" rev-list --count HEAD 2>&1)" "1"
# /wrapup offers a save only when there is something to save.
ok "with nothing new, /wrapup offers no save"         "$(cd "$FIX" && bash .claude/scripts/history.sh check 2>&1)" "save: nothing new"
printf 'x\n' > "$FIX/notes/new.md"
ok "with something new, /wrapup offers to save it"    "$(cd "$FIX" && bash .claude/scripts/history.sh check 2>&1)" "save: changes"
# The first save failed (here, signing is on and can't work) and nothing was
# said, leaving a history with nothing in it that every save after would hit.
blank_folder; history_script; mkdir -p "$FIX/fakehome"
printf '[user]\n\tname = t\n\temail = t@t\n[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n' > "$FIX/fakehome/.gitconfig"
out=$(start_history env $nameless)
ok "when the first save fails, setup says so"         "$out" "history: couldn't start"
ok "and leaves no half-made history behind"           "$([ -e "$FIX/.git" ] && echo there || echo gone)" "gone"
# Downloaded with git clone: the history is the template's, not theirs.
blank_folder; history_script
(cd "$FIX" && git init -q && git remote add origin https://github.com/zidery333/os-template.git && git add -A && env $G git commit -qm release)
out=$(start_history env $G)
ok "a clone of the template is spotted"               "$out" "history: the template's own"
ok "and setup adds nothing to the template's history" "$(git -C "$FIX" log -1 --format=%s 2>&1)" "release"
ok "it offers the command to start fresh"             "$(cat "$REAL/.claude/skills/setup/SKILL.md")" 'rm -rf .git && bash .claude/scripts/history.sh start'
# /wrapup offered to save their notes into it, and /catch-me-up read the
# template's own releases as this week's news.
printf 'x\n' > "$FIX/notes/mine.md"
ok "in a clone, /wrapup doesn't offer a save"         "$(cd "$FIX" && bash .claude/scripts/history.sh check 2>&1)" "save: the template's own"
ok "and gives the same command to start fresh"        "$(cat "$REAL/.claude/skills/wrapup/SKILL.md")" 'rm -rf .git && bash .claude/scripts/history.sh start'
out=$(cd "$FIX" && bash -c "$(awk '/^# what moved/{f=1} f&&/^$/{exit} f' "$REAL/.claude/skills/catch-me-up/SKILL.md")" 2>&1)
no "in a clone, /catch-me-up doesn't list the template's releases" "$out" "release"
ok "but the files they changed"                       "$out" "notes/mine.md"
# That start-fresh command failed on Linux when git had no name. The script
# carries the name fallback, so starting fresh with it works.
rm -rf "$FIX/.git"; mkdir -p "$FIX/fakehome"; rm -f "$FIX/fakehome/.gitconfig"
out=$(cd "$FIX" && env $nameless bash .claude/scripts/history.sh start 2>&1)
ok "starting fresh works with no name set"            "$out" "history: started"
ok "under the name me"                                "$(git -C "$FIX" log -1 --format='%an <%ae>' 2>&1)" "me <me@localhost>"
# Inside a bigger git folder, the folder gets a history of its own.
blank_folder
mkdir -p "$FIX/home/os/.claude/scripts"; cp "$REAL/.claude/scripts/history.sh" "$FIX/home/os/.claude/scripts/"
printf 'x\n' > "$FIX/home/taxes-2025.txt"; printf 'x\n' > "$FIX/home/os/note.md"
(cd "$FIX/home" && git init -q && env $G git commit -q --allow-empty -m start)
ok "inside a bigger git folder, /wrapup offers no save" \
   "$(cd "$FIX/home/os" && bash .claude/scripts/history.sh check 2>&1)" "save: no history of its own"
out=$(cd "$FIX/home/os" && env $G bash .claude/scripts/history.sh start 2>&1)
ok "inside a bigger git folder, setup starts one of its own" "$out" "history: started"
no "and takes nothing from around it"                 "$(git -C "$FIX/home/os" log --stat 2>&1)" "taxes-2025"
needs

# The nightly save: a folder named "keeping secrets" used to stop every save,
# and a timer that stopped working was never noticed.
needs git
blank_folder
mkdir -p "$FIX/.claude/scripts" "$FIX/notes/keeping secrets"
cp "$REAL/.claude/scripts/daily-commit.sh" "$FIX/.claude/scripts/"
printf 'x\n' > "$FIX/notes/keeping secrets/what-i-think.md"; printf 'k\n' > "$FIX/notes/api-secret.txt"
out=$(cd "$FIX" && git init -q && env $G git commit -q --allow-empty -m start && \
      env $G "$FIX/.claude/scripts/daily-commit.sh" 2>&1; git -C "$FIX" show --stat --format= HEAD 2>&1)
ok "a subject called \"keeping secrets\" gets saved" "$(git -C "$FIX" show --stat --format= HEAD 2>&1)" "keeping secrets/what-i-think.md"
no "a file that looks private is left out" "$(git -C "$FIX" show --stat --format= HEAD 2>&1)" "api-secret"
ok "and the save says which one it left out" "$out" "api-secret.txt"
touch -t "$OLD" "$FIX/.claude/.state/daily-commit.last-success" 2>/dev/null
ok "a nightly save that stopped working gets noticed" "$(rot)" "nightly save hasn't worked"

saving_folder() {  # a folder with the nightly save in it and one commit made
  blank_folder
  mkdir -p "$FIX/.claude/scripts"; cp "$REAL/.claude/scripts/daily-commit.sh" "$FIX/.claude/scripts/"
  printf '.claude/.state/\n' > "$FIX/.gitignore"
  (cd "$FIX" && git init -q && git add -A && env $G git commit -qm start)
}
nightly() { env $G "$FIX/.claude/scripts/daily-commit.sh" 2>&1; }
saved_files() { git -C "$FIX" -c core.quotePath=false ls-tree -r --name-only HEAD 2>&1; }

# A download has no git folder, and one made with git init has no commit yet.
# The save refused both, every night.
blank_folder
mkdir -p "$FIX/.claude/scripts"; cp "$REAL/.claude/scripts/daily-commit.sh" "$FIX/.claude/scripts/"
nightly >/dev/null
ok "a folder with no git history yet gets its first save" "$(git -C "$FIX" log -1 --format=%s 2>&1)" "Daily save"

# A save that failed from its first night never wrote the stamp that goes
# stale, so the session check never said a word. Here git has no name to
# commit under, which is how a fresh Linux machine starts.
blank_folder
mkdir -p "$FIX/.claude/scripts" "$FIX/fakehome"; cp "$REAL/.claude/scripts/daily-commit.sh" "$FIX/.claude/scripts/"
(cd "$FIX" && git init -q && git config user.useConfigOnly true)
HOME="$FIX/fakehome" XDG_CONFIG_HOME="$FIX/fakehome" GIT_CONFIG_NOSYSTEM=1 \
  "$FIX/.claude/scripts/daily-commit.sh" >/dev/null 2>&1
ok "a nightly save that has never worked gets noticed" "$(rot)" "nightly save has never worked"
ok "and it says what to run" "$(rot)" "git config --global user.email"

# A folder with no git history of its own, sitting inside one that has it (a
# home folder kept in git, say): the save committed everything around it.
blank_folder
mkdir -p "$FIX/home/os/.claude/scripts"; cp "$REAL/.claude/scripts/daily-commit.sh" "$FIX/home/os/.claude/scripts/"
printf 'x\n' > "$FIX/home/taxes-2025.txt"; printf 'x\n' > "$FIX/home/os/note.md"
out=$(cd "$FIX/home" && git init -q && env $G git commit -q --allow-empty -m start && \
      env $G "$FIX/home/os/.claude/scripts/daily-commit.sh" 2>&1)
no "inside a bigger git folder, the save takes nothing from around it" "$(git -C "$FIX/home" log --stat 2>&1)" "taxes-2025"
ok "and says why it stopped" "$out" "inside another git folder"
# Downloaded with git clone, the save put their notes in the template's own
# history every night and tried to push them to the template's page. The
# page here is a stand-in on disk.
saving_folder
git init -q --bare "$FIX/remote/zidery333/os-template.git"
git -C "$FIX" remote add origin "$FIX/remote/zidery333/os-template.git"
printf 'x\n' > "$FIX/notes/mine.md"
out=$(nightly)
ok "in a clone of the template, the nightly save says why it won't save" "$out" "the template's own"
ok "and saves nothing into the template's history" "$(git -C "$FIX" log -1 --format=%s 2>&1)" "start"
no "and pushes nothing to the template's page" "$(git -C "$FIX/remote/zidery333/os-template.git" log --oneline 2>&1)" "start"
# That test compared paths as typed with paths on disk. On a Mac, a folder
# called MyOS reached as myos was refused every night as inside itself.
blank_folder
mkdir -p "$FIX/MyOS/.claude/scripts"; cp "$REAL/.claude/scripts/daily-commit.sh" "$FIX/MyOS/.claude/scripts/"
printf 'x\n' > "$FIX/MyOS/note.md"; (cd "$FIX/MyOS" && git init -q)
if [ -d "$FIX/myos" ]; then
  env $G "$FIX/myos/.claude/scripts/daily-commit.sh" >/dev/null 2>&1
  ok "a folder reached with its name in other letter case still gets saved" "$(git -C "$FIX/MyOS" log -1 --format=%s 2>&1)" "Daily save"
fi

# git prints "Café" as "Caf\303\251" in quotes. The private-file filter
# unstaged that spelling, which matched no file, and the real one was saved.
saving_folder
mkdir -p "$FIX/work/Café app"; printf 'k\n' > "$FIX/work/Café app/api-secret.txt"; printf 'x\n' > "$FIX/work/Café app/menu.md"
nightly >/dev/null
no "a private-looking file in a folder with an accent is left out too" "$(saved_files)" "api-secret"
no "and the message names the room, not a quoted scrap of it" "$(git -C "$FIX" log -1 --format=%B 2>&1)" '"work'

# SSH keys and token files went in, and so did any big video. GitHub refuses a
# file over 100 MB, so after one of those every push failed.
saving_folder
mkdir -p "$FIX/notes/keys" "$FIX/work/app/config"
for f in notes/keys/id_ed25519 work/app/.netrc work/app/config/token.json work/app/config/api-keys.txt work/app/config/service-account.json; do
  printf 'k\n' > "$FIX/$f"
done
dd if=/dev/zero of="$FIX/work/app/footage.mov" bs=1048576 seek=60 count=0 2>/dev/null
out=$(nightly)
for f in id_ed25519 .netrc token.json api-keys.txt service-account.json footage.mov; do
  no "the nightly save leaves out $f" "$(saved_files)" "$f"
done
ok "and says the big one was left out" "$out" "over 50 MB: work/app/footage.mov"

# A project that is its own git folder is saved as a pointer, none of its
# files, and the log said "saved". Unsaved work inside it failed the save.
saving_folder
mkdir -p "$FIX/work/site"; printf 'x\n' > "$FIX/work/site/index.html"
(cd "$FIX/work/site" && git init -q && git add -A && env $G git commit -qm s)
ok "a project with its own git folder is named as not in the save" "$(nightly)" "work/site has its own git folder"
printf 'y\n' >> "$FIX/work/site/index.html"
no "and unsaved work inside it does not fail the save" "$(nightly)" "FAILED"

# Only a private-looking file waiting left nothing to commit. That run wrote no
# stamp, so two quiet days later the save read as broken.
saving_folder
printf 'k\n' > "$FIX/notes/api-secret.txt"
mkdir -p "$FIX/.claude/.state"; touch -t "$OLD" "$FIX/.claude/.state/daily-commit.last-success"
nightly >/dev/null
no "a night with only a private-looking file is not a broken save" "$(rot)" "nightly save hasn't worked"
needs

# /wrapup's commit had the same hole: inside a bigger git folder, git add -A
# saved all of that too. The history script's check is what rules that out.
for f in "$REAL"/.claude/skills/*/SKILL.md; do
  grep -q 'git add -A' "$f" || continue
  ok "$(basename "$(dirname "$f")") checks it is in its own git folder before committing" "$(cat "$f")" "history.sh check"
done

# ---------------------------------------------------------------------------
say "What gets shipped"

# Every hook is run by name out of settings.json. Lose the executable bit on
# one of them — a copy through a zip, a share, a cloud drive — and it stops
# firing with no message anywhere. Nothing else in this folder would notice.
for f in "$REAL"/.claude/hooks/*.sh "$REAL"/.claude/scripts/*.sh "$REAL"/.claude/tests/run.sh; do
  if [ -x "$f" ]; then pass=$((pass+1)); printf '  ok    %s can be run\n' "${f#$REAL/}"
  else fail=$((fail+1)); printf '  FAIL  %s is not executable — chmod +x it\n' "${f#$REAL/}"; fi
done

# One comma too many and settings.json can't be read, so no hook runs at all.
# Every check here that reads it found nothing to check, and passed.
ok "settings.json can be read" \
   "$(python3 -c 'import json,sys;json.load(open(sys.argv[1]))' "$REAL/.claude/settings.json" 2>&1 && echo "it can")" "it can"
# Claude Code's own memory kept "remember that..." in a folder of its own,
# outside this one, so one fact had two homes that drifted apart.
ok "Claude Code's own memory is off, so facts go in me/" \
   "$(python3 -c 'import json,sys;print("off" if json.load(open(sys.argv[1])).get("autoMemoryEnabled") is False else "on")' "$REAL/.claude/settings.json" 2>&1)" "off"

# Every hook settings.json names has to be on disk. A renamed file leaves a
# hook silently dead, which is how one of them sat broken for a month.
for rel in $(grep -o '\.claude/[a-z/-]*\.sh' "$REAL/.claude/settings.json" | sort -u); do
  if [ -f "$REAL/$rel" ]; then pass=$((pass+1)); printf '  ok    settings.json points at a real %s\n' "$(basename "$rel")"
  else fail=$((fail+1)); printf '  FAIL  settings.json names %s, which does not exist\n' "$rel"; fi
done

# The skill checks only ever ran on skills made up for them. A shipped
# /update-os that Claude Code couldn't load passed every one of them.
# Only the ones the template shipped: this runs in filled-in folders too, and
# a long skill of the person's own failed it, so no upgrade was called done.
blank_folder
if [ -f "$REAL/.claude/shipped.tsv" ]; then
  awk -F'\t' '$1 !~ /^(#|gone|theirs:)/ { print $2 }' "$REAL/.claude/shipped.tsv" | grep '^\.claude/skills/' |
    while IFS= read -r p; do [ -f "$REAL/$p" ] && mkdir -p "$FIX/${p%/*}" && cp "$REAL/$p" "$FIX/$p"; done
else cp -R "$REAL/.claude/skills" "$FIX/.claude/"; fi
quiet "every skill the template shipped can be loaded" "$(rot)"

# The front page linked a product page, never said how to get Claude Code or
# open Terminal, and gave Linux nothing. Newcomers stalled before /setup.
start=$(sed -n '/^## Start/,/^1\./p' "$REAL/README.md")
ok "the front page says how to open Terminal first" "$start" "Cmd+Space"
ok "and how to install Claude Code"                 "$(cat "$REAL/README.md")" "claude.ai/install.sh"
ok "and what Ubuntu needs"                          "$(cat "$REAL/README.md")" "apt install git curl"
# The installer doesn't put claude on the PATH of a stock Mac or Ubuntu, and
# the step that moved the folder also ran claude, so pasting it again after
# "command not found" stopped at the move.
ok "it says what to do when claude isn't found"     "$(cat "$REAL/README.md")" ".local/bin"
no "and moving the folder is a step of its own"     "$(grep -F 'mv ~/Downloads' "$REAL/README.md")" "claude"

# Only in the workshop, where release.sh sits beside the machinery: its dry
# run cut the upgrade report at 25 lines, so the end of it was never seen.
if [ -f "$REAL/../release.sh" ]; then
  no "the release shows the whole upgrade report" "$(grep -F 'upgrade.txt"' "$REAL/../release.sh")" "| head"
fi

# An allow rule matches the command's text and nothing more. "git diff:*" let
# `git diff --output=<file>` overwrite a decision log with no prompt, and
# awk and yt-dlp had the same hole. This matches rules the way Claude Code's
# permissions page says it does: deny, then ask, then allow; * is any text.
rule() { python3 - "$REAL/.claude/settings.json" "$1" <<'PY'
import json, re, sys
perms, cmd = json.load(open(sys.argv[1]))["permissions"], sys.argv[2]
def hit(r):
    if not (r.startswith("Bash(") and r.endswith(")")): return False
    r = r[5:-1]
    if r.endswith(":*"): r = r[:-2] + " *"
    if r.endswith(" *") and r.count("*") == 1 and cmd == r[:-2]: return True
    return re.fullmatch(".*".join(map(re.escape, r.split("*"))), cmd, re.S) is not None
print(next((k for k in ("deny", "ask", "allow") if any(map(hit, perms.get(k, [])))), "none"))
PY
}
no "git diff --output can't overwrite a file unasked" "$(rule 'git diff --output=work/Garden/decisions.md')" allow
no "nor can git log --output"                         "$(rule 'git log -1 --output=me/who-i-am.md --format=%s')" allow
no "nor git show --output"                            "$(rule 'git show --output=notes/thrown-away.md -s HEAD')" allow
ok "an awk that prints into a file asks first"        "$(rule "awk 'BEGIN{printf \"\" > \"CLAUDE.md\"}'")" ask
ok "with or without the space"                        "$(rule "awk '{print >\"me/who-i-am.md\"}' x")" ask
ok "yt-dlp is never let run another command"          "$(rule "yt-dlp --exec 'rm -rf ~' https://youtu.be/x")" deny
ok "not even through --netrc-cmd"                     "$(rule "yt-dlp --netrc-cmd 'rm -rf ~' https://youtu.be/x")" deny
ok "or write into a file of theirs"                   "$(rule 'yt-dlp --print-to-file title work/p/decisions.md https://youtu.be/x')" deny
ok "the subtitle line /learn uses still runs unasked" \
   "$(rule "yt-dlp --skip-download --write-auto-subs --sub-lang en --sub-format vtt -o '%(id)s' \"https://youtu.be/x\"")" allow
ok "and so does the awk that cleans the subtitles"    "$(rule "awk 'NF && \$0 != prev { print; prev = \$0 }'")" allow
ok "and the /tidy-up tally, arrows and all" \
   "$(rule "awk -F'\t' '{ k[\$1]++ } END { for (s in k) printf \"%d looked at -> %d kept\t%s\n\", k[s], k[s], s }'")" allow

# ---------------------------------------------------------------------------
more=""; [ "$skipped" -eq 0 ] || more=", $skipped skipped"
printf '\n\033[1m%s passed, %s failed%s\033[0m\n' "$pass" "$fail" "$more"
[ "$fail" -eq 0 ] || exit 1
