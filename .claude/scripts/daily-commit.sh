#!/bin/bash
# Optional: commits this folder once a day so you don't lose it, and pushes
# it too if you have set up a remote. If the folder has no git history yet,
# the first run starts one.
#
# It runs with no one watching. Read it before you turn it on.
#
# To turn it on, run one of these once in your terminal:
#
#   Timer (cron), 9:30pm every day:
#     (crontab -l 2>/dev/null; echo "30 21 * * * '$(pwd)/.claude/scripts/daily-commit.sh' >> /tmp/os-commit.log 2>&1") | crontab -
#
#   macOS note: if this folder lives in ~/Downloads, ~/Documents or ~/Desktop,
#   macOS blocks timed jobs from reading it and this fails silently every day.
#   Keep the folder somewhere plain, like ~/os.
#
# To turn it off: crontab -e, and delete the line.
set -uo pipefail

# Work out where this folder is from where this script is, so the script keeps
# working after you move or copy the folder.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Written after every good run. The session check reads it, so a timer that
# quietly stopped working gets noticed within a couple of days.
STAMP_OK="$REPO_DIR/.claude/.state/daily-commit.last-success"
# Written with the reason when a run fails. A save that never once worked has
# no stamp above to go stale, so this is how the session check hears of it.
STAMP_FAIL="$REPO_DIR/.claude/.state/daily-commit.last-failure"
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

log() { echo "$LOG_PREFIX $*"; }
die() {
  log "FAILED: $*"
  mkdir -p "$(dirname "$STAMP_FAIL")" 2>/dev/null && echo "$(date '+%Y-%m-%d') $*" > "$STAMP_FAIL" 2>/dev/null
  exit 1
}
worked() { mkdir -p "$(dirname "$STAMP_OK")"; date > "$STAMP_OK"; rm -f "$STAMP_FAIL"; }
# Names are read with -z. Without it git prints "Café" as "Caf\303\251" in
# quotes, and a name spelled that way matches no file.
staged() { git diff --cached --name-only -z | tr '\0' '\n'; }

cd "$REPO_DIR" 2>/dev/null || die "cannot open $REPO_DIR (blocked folder?)"
# Start a git folder only when there is none here or above. Inside a bigger
# one, like a home folder kept in git, git finds that one instead and
# git add -A would save everything in it, so that is refused.
[ -e .git ] || git rev-parse --git-dir >/dev/null 2>&1 || git init -q || die "git init failed"
# Asked of git, not by comparing paths: a Mac folder called MyOS reached as
# myos would read as inside some other one.
UNDER=$(git rev-parse --show-prefix 2>/dev/null) || die "git can't read the git folder in $REPO_DIR"
[ -z "$UNDER" ] || die "this folder is inside another git folder, $(git rev-parse --show-toplevel), so a save would take all of that too. To save just this one: git init $(printf %q "$REPO_DIR")"
# Downloaded with git clone, the history is the template's own: a save would
# put their notes in it and then try to push them to the template's page.
case "$(git remote get-url origin 2>/dev/null)" in
  *zidery333/os-template*) die "this folder's history is the template's own, from git clone, so a save would put your notes in it. To start your own: rm -rf .git && bash .claude/scripts/history.sh start" ;;
esac
# A new Linux machine often has no name and email set for git, and then every
# commit fails.
git var GIT_COMMITTER_IDENT >/dev/null 2>&1 || \
  die "git doesn't know your name and email yet. Run: git config --global user.name \"Your Name\" && git config --global user.email you@example.com"

if [ -z "$(git status --porcelain)" ]; then
  log "nothing changed, skipping"
  worked
  exit 0
fi

# Push whatever branch is actually checked out. An earlier version always said
# 'main', so committing from any other branch pushed a main that didn't have
# the commit in it, and the two drifted apart without anyone noticing.
# symbolic-ref, because before the first commit rev-parse has no name to give.
BRANCH=$(git symbolic-ref --short -q HEAD) || die "no branch checked out, refusing to commit"

# GitHub refuses a file over 100 MB, and once one is committed every push after
# it fails until the history is rewritten. Big files belong outside this
# folder, so any over 50 MB is left out before git ever reads it.
BIG=""; set -- .
while IFS= read -r f; do
  [ -n "$f" ] && [ -n "$(git ls-files -o -m --exclude-standard -- ":(literal)$f" 2>/dev/null)" ] || continue
  BIG="$BIG$f  "; set -- "$@" ":(exclude,literal)$f"
done <<EOF
$(find . -name .git -prune -o -type f -size +50M -print 2>/dev/null | sed 's|^\./||')
EOF
git add -A -- "$@" || die "git add failed"
[ -z "$BIG" ] || log "left these out, they are over 50 MB: $BIG"

# Cheap insurance: never let a password or key ride along in a commit nobody
# is watching, even though .gitignore should already have caught it.
# Only the file's own name is looked at: a folder called "keeping secrets" is
# a subject, not a leak, and it used to stop every save. A suspect file is left
# out of the save and named in the log; everything else still gets saved.
# Key and token names are matched closely: "token" alone would catch a note
# called design-tokens.md, and "*.key" every Keynote file.
SUSPECT=$(staged | awk -F/ '
  { n = tolower($NF) }
  n ~ /^\.env/ || n ~ /\.(pem|p12|kdbx)$/ || n ~ /_rsa$/ || n ~ /secret|credential/ ||
  n ~ /^id_(dsa|ecdsa|ed25519)$/ || n == ".netrc" || n == "token.json" ||
  n ~ /^api[-_]?keys?\./ || n ~ /^service[-_]?account.*\.json$/ { print }')
if [ -n "$SUSPECT" ]; then
  printf '%s\n' "$SUSPECT" | while IFS= read -r f; do
    git reset -q -- "$f" 2>/dev/null || git rm -q --cached -- "$f"   # the second is for a folder with no commits yet
  done
  log "left these out, they look private: $(printf '%s' "$SUSPECT" | tr '\n' ' ')"
fi

# A project in here with its own git folder goes in as a pointer to it, with
# none of its files. Say so, or the log reads as if it were backed up.
git -c core.quotePath=false diff --cached --raw | awk -F'\t' '$1 ~ / 160000 / { print $2 }' |
  while IFS= read -r d; do
    log "$d has its own git folder, so this save holds none of its files. Give it its own remote to back it up."
  done

# Nothing left to commit: only something left out above changed, or only
# unsaved work inside such a project. That is a good night, not a failure.
if git diff --cached --quiet; then
  log "nothing else to save, skipping"
  worked
  exit 0
fi

FILES=$(staged)
COUNT=$(echo "$FILES" | wc -l | tr -d ' ')
# Not called GROUPS: bash keeps that name for itself and quietly ignores
# anything put in it, so every message used to read "- : 20 file(s)".
BY_ROOM=$(echo "$FILES" | awk -F'/' '{ if (NF>1) print $1; else print "(top level)" }' | sort | uniq -c | sort -rn)
BODY=$(echo "$BY_ROOM" | awk '{count=$1; $1=""; sub(/^ /,""); printf "- %s: %d file(s)\n", $0, count}')

MSG="Daily save $(date '+%Y-%m-%d'): ${COUNT} file(s) changed

${BODY}

(saved automatically, nobody wrote this message)"

git commit -m "$MSG" || die "git commit failed"

# No remote is a normal state — plenty of people keep this folder on one
# machine. Committing is the part that matters, so say so and stop rather
# than failing every night with a push error nobody reads.
if ! git remote get-url origin >/dev/null 2>&1; then
  log "saved ${COUNT} file(s) on ${BRANCH}. No remote set, so nothing was pushed."
  log "to push as well: git remote add origin <url> && git push -u origin $BRANCH"
  worked
  exit 0
fi

git push origin "$BRANCH" || die "git push origin $BRANCH failed"

log "saved and pushed ${COUNT} file(s) on ${BRANCH}"
worked
