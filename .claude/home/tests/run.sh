#!/usr/bin/env bash
# Checks for ./home. Builds throwaway folders in a temp dir; never touches the
# real ones. Run: ./tests/run.sh
set -uo pipefail
REAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SLEEPER=0; pass=0; fail=0
T="$(mktemp -d)"; trap '[ "$SLEEPER" -gt 0 ] && kill "$SLEEPER" 2>/dev/null; rm -rf "$T"' EXIT
# A stranger's machine may have no git name set. The checks need one.
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
ok()  { pass=$((pass+1)); }
bad() { fail=$((fail+1)); echo "FAIL: $1"; }
has() { grep -q -- "$2" <<<"$1" && ok || bad "$3 (wanted: $2)"; }
cd "$T"

# A home folder with its own copy of the script and a tiny master.
H="$T/home"; mkdir -p "$H/master/new/.os" "$H/master/new/skills/shared" "$H/master/template/skills/shared"
cp "$REAL/home" "$H/home"
printf '#!/bin/sh\necho check ran\n' > "$H/master/new/os"; chmod +x "$H/master/new/os"
echo "engine v2" > "$H/master/new/.os/engine.py"
echo "shared v2" > "$H/master/new/skills/shared/SKILL.md"
echo "shared v2" > "$H/master/template/skills/shared/SKILL.md"

newfolder() {  # a new-version folder in git, with a business skill and a Work item
  mkdir -p "$1/.os" "$1/.claude/skills/shared" "$1/.claude/skills/business" "$1/Work/Job"
  printf '#!/bin/sh\necho all good\n' > "$1/os"; chmod +x "$1/os"
  echo "engine v1" > "$1/.os/engine.py"
  echo "state" > "$1/.os/state.json"
  echo "shared v1" > "$1/.claude/skills/shared/SKILL.md"
  echo "mine" > "$1/.claude/skills/business/SKILL.md"
  printf -- '---\ntitle: The job\nstatus: pushing\n---\n\n## Next action\n- [ ] ring the client\n' > "$1/Work/Job/README.md"
  git -C "$1" init -q && git -C "$1" add -A && git -C "$1" -c user.name=t -c user.email=t@t commit -qm start
}
fingerprint() { (cd "$1" && find . -path ./.git -prune -o -type f -print0 | sort -z | xargs -0 cksum); }

A="$T/FolderA"; newfolder "$A"
B="$T/FolderB"; mkdir -p "$B/me" "$B/notes" "$B/work"
C="$T/FolderC"; newfolder "$C"; rm -rf "$C/.git"

out=$("$H/home" add "$A" "first one"); has "$out" "Added" "add a new folder"
has "$(cat "$H/folders.tsv")" "new" "new folder detected as new"
out=$("$H/home" add "$B" "old one" --as old); has "$out" "as old" "add --as old"
out=$("$H/home" add "$C" "no git"); has "$out" "Added" "add a folder with no git"
out=$("$H/home" add "$T" "parent" 2>&1); has "$out" "Refusing" "refuse a parent of home"
out=$("$H/home" add "$A" "twice" 2>&1); has "$out" "already listed" "refuse the same folder twice"

out=$("$H/home" status); has "$out" "ring the client" "status shows the next action"
has "$out" "old version, upgrade pending" "status marks old folders"

out=$("$H/home" check); has "$out" "all good" "check runs the folder's own ./os check"
has "$out" "no check yet" "check skips old folders"

before=$(fingerprint "$A")
out=$("$H/home" sync); has "$out" "change .os/engine.py" "dry run lists changed files"
has "$out" "sync skips it" "dry run skips old folders"
[ "$before" = "$(fingerprint "$A")" ] && ok || bad "dry run wrote something"
grep -q business <<<"$out" && bad "sync looked at a business skill" || ok

out=$("$H/home" sync --apply 2>&1); has "$out" "needs one folder name" "--apply needs a name"
out=$("$H/home" sync FolderC --apply 2>&1); has "$out" "isn't a git folder" "refuse a folder with no git"

echo "my edit" >> "$A/.claude/skills/shared/SKILL.md"
out=$("$H/home" sync FolderA --apply 2>&1); has "$out" "unsaved changes" "refuse when a target file is unsaved"
git -C "$A" checkout -q -- .
echo "loose" > "$A/Work/Job/draft.md"   # unsaved, but not a file sync writes

(cd "$A" && exec sleep 60) & SLEEPER=$!
sleep 1
out=$("$H/home" sync FolderA --apply 2>&1); has "$out" "something is running" "refuse when something runs in the target"
kill $SLEEPER 2>/dev/null; wait $SLEEPER 2>/dev/null; SLEEPER=0

out=$("$H/home" sync FolderA --apply 2>&1); has "$out" "committed as" "apply writes and commits"
has "$(cat "$A/.os/engine.py")" "engine v2" "apply wrote the program"
has "$(cat "$A/.claude/skills/business/SKILL.md")" "mine" "business skill untouched"
has "$(cat "$A/.os/state.json")" "state" "folder state untouched"
has "$(git -C "$A" status --porcelain)" "draft.md" "the person's loose file is left unsaved and unmoved"
sha=$(git -C "$A" rev-parse --short HEAD)
git -C "$A" -c user.name=t -c user.email=t@t revert --no-edit "$sha" >/dev/null
has "$(cat "$A/.os/engine.py")" "engine v1" "git revert undoes a sync"

mkdir -p "$H/master/new/Work"; echo x > "$H/master/new/Work/evil.md"
out=$("$H/home" sync FolderA 2>&1); has "$out" "never allowed to touch" "refuse to write into Work/"
rm -r "$H/master/new/Work"
mkdir -p "$H/master/new/notes"; echo x > "$H/master/new/notes/evil.md"
out=$("$H/home" sync FolderA 2>&1); has "$out" "never allowed to touch" "refuse to write into notes/"
rm -r "$H/master/new/notes"
echo x > "$H/master/new/.os/config.json"
out=$("$H/home" sync FolderA 2>&1); has "$out" "never allowed to touch" "refuse to write a folder's own settings"
rm "$H/master/new/.os/config.json"

# A folder made from the template (me/ notes/ work/).
D="$T/FolderD"; mkdir -p "$D/me" "$D/notes" "$D/work/garden" "$D/.claude/skills/setup" "$D/.claude/tests"
printf '## Active\n\n- [garden](garden/brief.md) — veg beds\n\n## Ideas\n' > "$D/work/projects.md"
printf '# garden\n\n**Next:** sow the beans\n' > "$D/work/garden/brief.md"
echo "stock" > "$D/.claude/skills/setup/SKILL.md"
printf '#!/bin/sh\necho 5 passed, 0 failed\n' > "$D/.claude/tests/run.sh"
git -C "$D" init -q && git -C "$D" add -A && git -C "$D" -c user.name=t -c user.email=t@t commit -qm start
out=$("$H/home" add "$D" "home life"); has "$out" "as template" "a template folder is found on its own"
out=$("$H/home" status); has "$out" "sow the beans" "status reads a template folder's next step"
out=$("$H/home" check); has "$out" "5 passed" "check runs a template folder's own tests"
out=$("$H/home" sync FolderD --apply 2>&1); has "$out" "committed as" "sync writes into a template folder"
has "$(cat "$D/.claude/skills/shared/SKILL.md")" "shared v2" "the shared skill arrived"
has "$(cat "$D/.claude/skills/setup/SKILL.md")" "stock" "the folder's own skills are left alone"
[ -e "$D/os" ] && bad "a template folder got the ./os program" || ok
echo "note" > "$H/master/README.md"
out=$("$H/home" sync FolderD 2>&1); has "$out" "nothing to do" "a loose note in master/ isn't handed out"

# A linked skills folder must not carry a write into somewhere else.
E="$T/Elsewhere"; mkdir -p "$E"
rm -rf "$A/.claude/skills/shared"; ln -s "$E" "$A/.claude/skills/shared"
out=$("$H/home" sync FolderA 2>&1); has "$out" "is a link" "refuse to write through a link"
[ -z "$(ls "$E")" ] && ok || bad "a write went through a link"
rm "$A/.claude/skills/shared"; git -C "$A" checkout -q -- .

# git keeps no copy of a file it ignores, so an edit there could never come back.
I="$T/FolderI"; newfolder "$I"; "$H/home" add "$I" "ignored" >/dev/null
git -C "$I" rm -q --cached .claude/skills/shared/SKILL.md
git -C "$I" -c user.name=t -c user.email=t@t commit -qm "stop tracking"
echo ".claude/skills/shared/" >> "$I/.git/info/exclude"
echo "my own edit" > "$I/.claude/skills/shared/SKILL.md"
out=$("$H/home" sync FolderI --apply 2>&1); has "$out" "git ignores" "refuse to overwrite a file git ignores"
has "$(cat "$I/.claude/skills/shared/SKILL.md")" "my own edit" "the ignored file is left alone"

# A write that fails halfway puts the changed files back.
chmod 000 "$A/.os"
before=$(cat "$A/os")
out=$("$H/home" sync FolderA --apply 2>&1); has "$out" "Stopped" "a failed write stops cleanly"
chmod 755 "$A/.os"
has "$(cat "$A/os")" "$before" "and the file changed before it is put back"

M2="$T/Two/FolderA"; newfolder "$M2"; "$H/home" add "$M2" "same name" >/dev/null
out=$("$H/home" sync FolderA 2>&1); has "$out" "More than one folder" "a name two folders share is refused"

grep -nE 'rmtree|unlink|os\.remove|rmdir|os\.rename|"rm"|git.*"rm"|"mv"' "$REAL/home" && bad "home has a way to delete or move" || ok
grep -nE '"--fix"' "$REAL/home" && bad "home can run a fix" || ok

echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
