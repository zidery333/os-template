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

pass=0; fail=0
say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { if printf '%s' "$2" | grep -qF "$3"; then pass=$((pass+1)); printf '  ok    %s\n' "$1"
         else fail=$((fail+1)); printf '  FAIL  %s\n        wanted to see: %s\n        got: %s\n' "$1" "$3" "${2:-<nothing>}"; fi; }
no()   { if printf '%s' "$2" | grep -qF "$3"; then fail=$((fail+1)); printf '  FAIL  %s\n        should not have said: %s\n' "$1" "$3"
         else pass=$((pass+1)); printf '  ok    %s\n' "$1"; fi; }
quiet(){ if [ -z "$(printf '%s' "$2" | tr -d '[:space:]')" ]; then pass=$((pass+1)); printf '  ok    %s\n' "$1"
         else fail=$((fail+1)); printf '  FAIL  %s\n        should have said nothing, said: %s\n' "$1" "$2"; fi; }

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
health(){ bash -c "source '$REAL/.claude/hooks/lib.sh' && os_health" 2>&1; }
second() {  # second <relative-path> <content-about-to-be-written>
  python3 -c "import json,sys;print(json.dumps({'tool_name':'Write','tool_input':{'file_path':'$FIX/$1','content':sys.argv[1]}}))" "$2" \
    | "$REAL/.claude/hooks/no-second-copy.sh" 2>&1
}
tidy() { printf '{"tool_input":{"file_path":"%s"}}' "$1" | "$REAL/.claude/hooks/keep-tidy.sh" 2>&1; }

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

blank_folder
mkdir -p "$FIX/work/archive/old"; { printf '# o\n'; seq 1 300; } > "$FIX/work/archive/old/brief.md"
quiet "archived files are not checked" "$(tidy "$FIX/work/archive/old/brief.md")"

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

# ---------------------------------------------------------------------------
say "Speaking plainly"
blank_folder
p1="t$RANDOM"; p2="t$RANDOM"
ok    "a stuffy word sends the reply back" \
      "$(printf '{"last_assistant_message":"we should leverage this","prompt_id":"%s"}' "$p1" | "$REAL/.claude/hooks/plain-words.sh")" \
      "leverage"
quiet "the same reply is only checked once" \
      "$(printf '{"last_assistant_message":"we should leverage this","prompt_id":"%s"}' "$p1" | "$REAL/.claude/hooks/plain-words.sh")"
quiet "a word inside backticks is being named, not used" \
      "$(printf '{"last_assistant_message":"the list has `leverage` in it","prompt_id":"%s"}' "$p2" | "$REAL/.claude/hooks/plain-words.sh")"

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
ok "a second write-up of the same study is spotted" \
   "$(second notes/other/sources/2026-08-27-testing-effect.md '# The testing effect

Roediger and Karpicke, 2006. Recall practice kept 61% after one week against
40% for rereading, and people still choose rereading.')" \
   "already be a write-up of this"

ok "it names the file to open"                     \
   "$(second notes/other/sources/2026-08-27-testing-effect.md '# The testing effect

Roediger and Karpicke, 2006. Recall kept 61% against 40% for rereading.')" \
   "2026-01-01-retrieval.md"

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
say "The daily save"

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
put "$v1/.claude/hooks/same.sh" "same"
record "$v1" v1 >/dev/null

v2="$UP/v2"; cp -R "$v1" "$v2"
put "$v2/.claude/hooks/a.sh" "new a";           put "$v2/.claude/skills/edited/SKILL.md" "new"
put "$v2/.claude/skills/deleted/SKILL.md" "x2"; rm -rf "$v2/.claude/skills/dropped"
put "$v2/CLAUDE.md" "new rules";                put "$v2/notes/subjects.md" "new blank map"
put "$v2/.claude/hooks/b.sh" "b"; chmod -x "$v2/.claude/hooks/b.sh"   # a zip that lost the flag
record "$v2" v2 >/dev/null

mine="$UP/mine"; cp -R "$v1" "$mine"
put "$mine/.claude/skills/edited/SKILL.md" "my own version"
rm -rf "$mine/.claude/skills/deleted"
put "$mine/notes/subjects.md" "my real subjects"
put "$mine/me/who-i-am.md" "me"

out=$(upgrade --preview "$v2")
ok "a preview says it is a preview" "$out" "PREVIEW"
ok "a preview lists what would be replaced" "$out" ".claude/hooks/a.sh"
ok "a preview changes nothing" "$(cat "$mine/.claude/hooks/a.sh")" "old a"

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
ok "your own files are never touched" "$(cat "$mine/me/who-i-am.md")" "me"
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
ok "an upgrade won't start over the top of an unfinished one" "$out" "the last upgrade is still"
ok "and what it left is still there" "$(ls "$mine/.claude/.upgrade/new")" "CLAUDE.md.new"
rm -rf "$mine/.claude/.upgrade"

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

# A download from GitHub carries a LICENSE file at the top. It belongs there.
blank_folder
printf 'MIT\n' > "$FIX/LICENSE"
no "the license file is not called loose" "$(rot)" "LICENSE"

# ---------------------------------------------------------------------------
say "Odd names, odd machines"

# A folder called "My OS". The hook paths in settings.json had no quotes, so
# every hook failed on every message, write and reply.
blank_folder
SP="$FIX/My OS"; mkdir -p "$SP"; cp -R "$REAL/.claude" "$SP/"; rm -rf "$SP/.claude/.state"
cp -R "$FIX/notes" "$FIX/work" "$FIX/me" "$FIX/CLAUDE.md" "$SP/"
bad=""
while IFS= read -r cmd; do
  CLAUDE_PROJECT_DIR="$SP" sh -c "$cmd" </dev/null >/dev/null 2>&1
  rc=$?; [ "$rc" -lt 2 ] || bad="$bad $rc:$cmd"
done < <(python3 -c 'import json,sys
for evs in json.load(open(sys.argv[1]))["hooks"].values():
  for e in evs:
    for h in e["hooks"]: print(h["command"])' "$REAL/.claude/settings.json")
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
quiet "asking for a paragraph is not pushback" \
   "$(printf '{"prompt":"write me a paragraph about my trip","user_prompt":"write me a paragraph about my trip"}' | "$REAL/.claude/hooks/spot-worth-saving.sh")"

# A Mac without Apple's developer tools has a python3 that only pops up an
# install box. The hooks must stay quiet, and say once what is missing.
blank_folder
out=$(OS_PRETEND_NO_PYTHON=1 "$REAL/.claude/hooks/session-start.sh" </dev/null 2>&1)
ok "no python3: the session check says what to install" "$out" "xcode-select --install"
ok "and what it says is still valid JSON" "$(printf '%s' "$out" | python3 -c 'import json,sys;json.load(sys.stdin);print("valid")' 2>&1)" "valid"
quiet "no python3: the other hooks stay quiet" \
   "$(printf '{"prompt":"remember that I hate emojis"}' | OS_PRETEND_NO_PYTHON=1 "$REAL/.claude/hooks/spot-worth-saving.sh" 2>&1)"

# The nightly save: a folder named "keeping secrets" used to stop every save,
# and a timer that stopped working was never noticed.
blank_folder
mkdir -p "$FIX/.claude/scripts" "$FIX/notes/keeping secrets"
cp "$REAL/.claude/scripts/daily-commit.sh" "$FIX/.claude/scripts/"
printf 'x\n' > "$FIX/notes/keeping secrets/what-i-think.md"; printf 'k\n' > "$FIX/notes/api-secret.txt"
G="GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t"
out=$(cd "$FIX" && git init -q && env $G git commit -q --allow-empty -m start && \
      env $G "$FIX/.claude/scripts/daily-commit.sh" 2>&1; git -C "$FIX" show --stat --format= HEAD 2>&1)
ok "a subject called \"keeping secrets\" gets saved" "$(git -C "$FIX" show --stat --format= HEAD 2>&1)" "keeping secrets/what-i-think.md"
no "a file that looks private is left out" "$(git -C "$FIX" show --stat --format= HEAD 2>&1)" "api-secret"
ok "and the save says which one it left out" "$out" "api-secret.txt"
touch -t "$OLD" "$FIX/.claude/.state/daily-commit.last-success" 2>/dev/null
ok "a nightly save that stopped working gets noticed" "$(rot)" "nightly save hasn't worked"

# ---------------------------------------------------------------------------
say "What gets shipped"

# Every hook is run by name out of settings.json. Lose the executable bit on
# one of them — a copy through a zip, a share, a cloud drive — and it stops
# firing with no message anywhere. Nothing else in this folder would notice.
for f in "$REAL"/.claude/hooks/*.sh "$REAL"/.claude/scripts/*.sh "$REAL"/.claude/tests/run.sh; do
  if [ -x "$f" ]; then pass=$((pass+1)); printf '  ok    %s can be run\n' "${f#$REAL/}"
  else fail=$((fail+1)); printf '  FAIL  %s is not executable — chmod +x it\n' "${f#$REAL/}"; fi
done

# Every hook settings.json names has to be on disk. A renamed file leaves a
# hook silently dead, which is how one of them sat broken for a month.
for rel in $(grep -o '\.claude/[a-z/-]*\.sh' "$REAL/.claude/settings.json" | sort -u); do
  if [ -f "$REAL/$rel" ]; then pass=$((pass+1)); printf '  ok    settings.json points at a real %s\n' "$(basename "$rel")"
  else fail=$((fail+1)); printf '  FAIL  settings.json names %s, which does not exist\n' "$rel"; fi
done

# ---------------------------------------------------------------------------
printf '\n\033[1m%s passed, %s failed\033[0m\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
