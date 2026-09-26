#!/bin/bash
# Optional: commits this folder once a day so you don't lose it, and pushes
# it too if you have set up a remote.
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
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

log() { echo "$LOG_PREFIX $*"; }
die() { log "FAILED: $*"; exit 1; }

cd "$REPO_DIR" 2>/dev/null || die "cannot open $REPO_DIR (blocked folder?)"
git rev-parse --git-dir >/dev/null 2>&1 || die "$REPO_DIR is not a git folder"

if [ -z "$(git status --porcelain)" ]; then
  log "nothing changed, skipping"
  mkdir -p "$(dirname "$STAMP_OK")"; date > "$STAMP_OK"
  exit 0
fi

# Push whatever branch is actually checked out. An earlier version always said
# 'main', so committing from any other branch pushed a main that didn't have
# the commit in it, and the two drifted apart without anyone noticing.
BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$BRANCH" != "HEAD" ] || die "no branch checked out, refusing to commit"

git add -A || die "git add failed"

# Cheap insurance: never let a password or key ride along in a commit nobody
# is watching, even though .gitignore should already have caught it.
# Only the file's own name is looked at: a folder called "keeping secrets" is
# a subject, not a leak, and it used to stop every save. A suspect file is left
# out of the save and named in the log; everything else still gets saved.
SUSPECT=$(git diff --cached --name-only | awk -F/ '
  { n = tolower($NF) }
  n ~ /^\.env/ || n ~ /\.(pem|p12)$/ || n ~ /_rsa$/ || n ~ /secret|credential/ { print }')
if [ -n "$SUSPECT" ]; then
  printf '%s\n' "$SUSPECT" | while IFS= read -r f; do
    git reset -q -- "$f" 2>/dev/null || git rm -q --cached -- "$f"   # the second is for a folder with no commits yet
  done
  log "left these out, they look private: $(printf '%s' "$SUSPECT" | tr '\n' ' ')"
  if git diff --cached --quiet; then
    log "nothing else changed, skipping"
    exit 0
  fi
fi

FILES=$(git diff --cached --name-only)
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
  mkdir -p "$(dirname "$STAMP_OK")"; date > "$STAMP_OK"
  exit 0
fi

git push origin "$BRANCH" || die "git push origin $BRANCH failed"

log "saved and pushed ${COUNT} file(s) on ${BRANCH}"
mkdir -p "$(dirname "$STAMP_OK")"; date > "$STAMP_OK"
