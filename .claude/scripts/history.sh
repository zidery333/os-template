#!/bin/bash
# The folder's history: the saves /wrapup and the nightly save add to, so a
# saved change can be taken back. Three jobs, each run by a skill:
#
#   history.sh start "Their Name"   /setup: start one if there is none
#   history.sh check                /wrapup: is there anything to save?
#   history.sh moved                /catch-me-up: what changed this week
#
# It is a script, not lines in the skills, so it can run without a
# permission box showing the person a page of shell.
set -u

# Work from this folder wherever it was called from.
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1

# A Mac without Apple's developer tools has only a stand-in git, and running
# it pops up an install box. The same test /setup and the session check use.
no_git() {
  command -v git >/dev/null 2>&1 || return 0
  sw_vers >/dev/null 2>&1 && ! xcode-select -p >/dev/null 2>&1
}

# Downloaded with git clone, the history is the template's own: their notes
# would go into it, and a push would try to send them to the template's page.
the_templates() {
  case "$(git remote get-url origin 2>/dev/null)" in
    *zidery333/os-template*) return 0 ;;
  esac
  return 1
}

# The files changed this week, for a folder with no history. A download keeps
# the dates in the zip, so for a week after a release every blank form reads
# as new; one still as shipped is left out.
changed_files() {
  find notes work me CLAUDE.md -type f -mtime -7 ! -name README.md 2>/dev/null |
    while IFS= read -r f; do
      h=$(cksum < "$f" | awk '{ print $1 "-" $2 }')
      grep -qxF "$(printf '%s\t%s' "$h" "$f")" .claude/shipped.tsv 2>/dev/null || echo "$f"
    done
}

case "${1:-}" in
  start)
    # Only a .git here counts. Inside a bigger git folder, like a home folder
    # kept in git, a save there would take everything around this one too.
    if no_git; then echo "history: no git"
    elif [ -e .git ]; then
      if the_templates; then echo "history: the template's own"
      else echo "history: already there"; fi
    else
      # On a Mac git makes up a name and email from the computer when none is
      # set, so ask for the ones set by hand, not whatever git would use.
      # Asked after git init, so a bigger git folder around this one has no say.
      if err=$(git init -q 2>&1 && git add -A 2>&1 &&
               if git config user.name >/dev/null && git config user.email >/dev/null
               then git commit -qm "Set up" 2>&1
               else git -c user.name="${2:-me}" -c user.email=me@localhost commit -qm "Set up" 2>&1; fi)
      then echo "history: started"
      else
        # Half made, it would sit there with nothing in it and every later
        # save would fail the same way. Take back the .git just made.
        rm -rf .git
        echo "history: couldn't start: $(printf '%s\n' "$err" | grep . | tail -n 1)"
      fi
    fi ;;
  check)
    if no_git; then echo "save: no git"
    elif [ ! -e .git ]; then echo "save: no history of its own"
    elif the_templates; then echo "save: the template's own"
    elif [ -n "$(git status --porcelain 2>/dev/null)" ]; then echo "save: changes"
    else echo "save: nothing new"; fi ;;
  moved)
    if no_git || the_templates ||
       ! git log --oneline --since="7 days ago" -- notes/ work/ me/ CLAUDE.md 2>/dev/null
    then changed_files; fi ;;
  *)
    echo "usage: history.sh start \"Their Name\" | check | moved" >&2
    exit 2 ;;
esac
