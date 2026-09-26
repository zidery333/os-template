#!/usr/bin/env bash
# Shared bits for the hooks. Every hook sources this first.
#
# Hooks run with a stripped-down set of program locations, so find the usual
# install spots ourselves rather than trusting what we were handed.
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$HOME/.local/bin:$PATH"
export PATH

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
STATE="$ROOT/.claude/.state"
mkdir -p "$STATE" 2>/dev/null

# python3 reads the JSON Claude Code hands each hook. On a Mac without Apple's
# developer tools, /usr/bin/python3 is only a stand-in: it fails, and pops up
# an "install" box every time it runs — which here would be every message and
# every reply. So look before calling it. Without it the hooks stay quiet, and
# the session check says once what to install.
has_python() {
  [ -z "${OS_PRETEND_NO_PYTHON:-}" ] || return 1
  command -v python3 >/dev/null 2>&1 || return 1
  if [ "$(uname -s)" = Darwin ] && [ "$(command -v python3)" = /usr/bin/python3 ] &&
     ! xcode-select -p >/dev/null 2>&1; then
    return 1
  fi
  return 0
}
need_python() { has_python || exit 0; }

# Turn any text into a safe JSON string. Plain awk, so that the one message
# saying python3 is missing can still get through without it.
json_str() {
  LC_ALL=C awk 'BEGIN { printf "\"" }
    { if (NR > 1) printf "\\n"
      s = $0; out = ""
      for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "\\") out = out "\\\\"
        else if (c == "\"") out = out "\\\""
        else if (c == "\t") out = out "\\t"
        else if (c == "\r") out = out "\\r"
        else if (c < " ") out = out " "
        else out = out c
      }
      printf "%s", out }
    END { printf "\"\n" }'
}

# Print JSON that adds text to what Claude can see. Silent if there is nothing
# to say — a hook that always speaks gets ignored, which defeats the point.
say_to_claude() {
  local text="$1"
  [ -n "$text" ] || return 0
  local esc; esc=$(printf '%s' "$text" | json_str)
  printf '{"additionalContext":%s,"hookSpecificOutput":{"hookEventName":"%s","additionalContext":%s}}\n' \
    "$esc" "${2:-SessionStart}" "$esc"
}

# How big a notes file has to get before it needs splitting or trimming.
BIG_FILE_LINES="${OS_BIG_FILE_LINES:-250}"

# A decision log is meant to grow forever, so it gets its own, much larger
# limit. Splitting one the way you split a notes file would be wrong.
BIG_LOG_LINES="${OS_BIG_LOG_LINES:-600}"

# How many projects can be active before most of them are really ideas.
MAX_PROJECTS="${OS_MAX_PROJECTS:-4}"

# ---------------------------------------------------------------------------
# Things with no end.
#
# A folder like this started out assuming everything finishes: a project has a
# "done", a subject gets learned. That is a workplace, and it makes the checks
# below nag at exactly the things people keep for love — the language nobody
# finishes, the garden, the subject read for the pleasure of it.
#
# So a brief or a subject file may carry this line:
#
#     **This one has no end.**
#
# and every check that asks "isn't this finished yet?" reads straight past it.
# Nothing else changes: it still gets a brief, still gets a decision log, still
# shows up on the map. It just stops being asked when it will be done.
NO_END_MARK='**This one has no end.**'
has_no_end() { [ -f "$1" ] && grep -qF "$NO_END_MARK" "$1" 2>/dev/null; }

# ---------------------------------------------------------------------------
# Copies of an OS folder kept inside this one — a template being worked on, an
# example, someone else's folder pulled in to read. Their blanks are not your
# blanks. Put a file called .not-my-os at the top of one and the checks below
# read straight past everything under it.
#
# Without this, a copy of the template inside work/ reports every deliberate
# blank in it as a blank of yours, at the start of every session, forever.
# ---------------------------------------------------------------------------

# Reads paths on standard input. Prints back the ones that are yours.
os_not_mine() {
  local marked
  marked=$(find . -name '.not-my-os' 2>/dev/null | sed 's|^\./||; s|/\.not-my-os$||')
  [ -n "$marked" ] || { cat; return 0; }
  awk -v list="$marked" '
    BEGIN { n = split(list, d, "\n") }
    {
      for (i = 1; i <= n; i++)
        if (d[i] != "" && index($0, d[i] "/") == 1) next
      print
    }'
}

# ---------------------------------------------------------------------------
# The health check. Two parts, on purpose.
#
#   os_rot     — the folder wearing out. Grows with the number of subjects and
#                projects, so a big folder can produce dozens of these at once.
#                Covers notes/, work/, and the top level of the folder itself.
#   os_health  — what a session actually gets handed: everything only the
#                person can answer, then the three worst rot lines and a count
#                of the rest.
#
# Both print nothing when there is nothing to say. That silence is what stops
# this becoming background noise.
#
# The cap is the whole point. Forty lines of complaints at the top of every
# session is the same as none — it gets skimmed. Three gets read. Run
# `/catch-me-up` to see the full list.
# ---------------------------------------------------------------------------

# How many rot lines a session gets told about before they turn into a count.
ROT_SHOWN="${OS_ROT_SHOWN:-3}"

# Headings that are section labels, not ideas, so they're meant to show up in
# every subject file. Without this the folder tells you off for following its
# own advice: notes/README.md teaches "Tried and it did nothing for me" as the
# most valuable section in a subject file, and the second subject you write
# gets it flagged as a repeat. Add your own headings here if you use them
# everywhere on purpose, separated by semicolons.
# (No apostrophes in here. Bash parses quotes inside a ${VAR:-default}, so one
# stray apostrophe silently breaks the whole file.)
SHARED_HEADINGS="${OS_SHARED_HEADINGS:-## Tried and it did nothing for me;## Open questions;## What I have not worked out yet}"

# Note on the greps below: they all use -F, because the pattern is a name the
# person chose. A subject called "a.b" as a plain pattern also matches "axb",
# so without -F a folder can look listed on the map when it isn't.

# Which folders under $1 have had nothing change inside them in $2 days.
#
# Prints one folder name per line. Prints nothing — and says so by returning
# non-zero — when find can't answer, which matters: the obvious way to write
# this is `[ -z "$(find X -newermt '-90 days')" ]` per folder, and that can't
# tell "nothing changed" from "find rejected the timestamp". Some replacements
# for find do reject it. A check that shouts about every subject you own is
# far worse than one that says nothing.
#
# One find for the whole tree, not one per folder — see the note at the top of
# os_rot for why that matters.
stale_folders() {
  local root="$1" days="$2" fresh
  [ -d "$root" ] || return 1
  # Does this find understand a relative timestamp at all?
  find "$root" -maxdepth 0 -newermt "-${days} days" >/dev/null 2>&1 || return 1
  # Three tagged streams into one awk. Tagging is what lets this stay a fixed
  # number of processes instead of one grep per folder.
  #
  #   F  folder holding a file touched inside the window
  #   N  folder holding any file at all — an empty folder is not stale, it is
  #      empty, usually one made a minute ago. It gets told about that
  #      separately, and calling it abandoned as well is wrong and confusing.
  #   D  a folder to judge
  {
    find "$root" -mindepth 2 -newermt "-${days} days" 2>/dev/null | awk -F/ '{print "F\t" $2}'
    find "$root" -mindepth 2 -type f 2>/dev/null | awk -F/ '{print "N\t" $2}'
    find "$root" -mindepth 1 -maxdepth 1 -type d ! -name archive 2>/dev/null | awk '{print "D\t" $0}'
  } | awk -F'\t' '
      $1 == "F" { isfresh[$2] = 1; next }
      $1 == "N" { hasfiles[$2] = 1; next }
      $1 == "D" { n = split($2, p, "/"); name = p[n]
                  if (hasfiles[name] && !isfresh[name]) print $2 }'
}

os_rot() {
  cd "$ROOT" 2>/dev/null || return 0

  # This runs at every single session start, so everything below spawns a
  # fixed number of processes rather than one per subject. The obvious
  # per-subject version — a wc here, a grep there — took six seconds at two
  # hundred subjects against a fifteen-second timeout, and a hook that times
  # out fails silently with no clue why. That is the only reason for the awk.

  # A subject file that has grown too big to read in one sitting.
  find notes -mindepth 2 -maxdepth 2 -name 'what-i-think.md' -exec wc -l {} + 2>/dev/null \
    | awk -v lim="$BIG_FILE_LINES" '$2 != "total" && $1 > lim {
        print $2 " is " $1 " lines. Too long to stay useful — split it into two subjects, or cut the weakest half." }'

  # A write-up nothing links to. The source was saved but the lesson never
  # made it into what you believe, so it is doing no work.
  find notes -mindepth 3 -maxdepth 3 -path '*/sources/*' -name '[0-9]*.md' 2>/dev/null \
    | awk -F/ '{
        subj = $2
        belief = "notes/" subj "/what-i-think.md"
        if (!(subj in loaded)) {
          loaded[subj] = 1; text[subj] = ""
          while ((getline line < belief) > 0) text[subj] = text[subj] line "\n"
          close(belief)
        }
        if (text[subj] == "") next          # no belief file: nothing to link from
        if (index(text[subj], $NF) > 0) next
        # A write-up that says it turned out to be wrong is finished, not
        # unfinished. The rules tell you to keep exactly these — they are the
        # evidence that a source misled you, and the scorecard runs on them —
        # so nagging to link or bin one asks for the opposite of the rule.
        retracted = 0
        while ((getline line < $0) > 0)
          if (line ~ /turned out to be wrong|turned out to be false|this was wrong/) retracted = 1
        close($0)
        if (retracted) next
        print $0 " is not linked from " belief ". Either add what it taught, or throw it away."
      }'

  # The same heading in two subject files. Usually means one idea got written
  # down twice and neither copy is now trusted.
  # Only headings that show up in two *different* files. The same heading twice
  # inside one file is a different problem, and keep-tidy.sh catches that one.
  dupes=$(find notes -mindepth 2 -maxdepth 2 -name 'what-i-think.md' -exec grep -H '^## ' {} + 2>/dev/null \
    | sort -u -t: -k1,1 -k2 | cut -d: -f2- \
    | awk -v shared="$SHARED_HEADINGS" '
        BEGIN { n = split(shared, s, ";"); for (i = 1; i <= n; i++) skip[s[i]] = 1 }
        !($0 in skip)' \
    | sort | uniq -d | head -3)
  [ -n "$dupes" ] && echo "These headings appear in more than one subject file, so the same idea is probably written twice: $(echo "$dupes" | tr '\n' ';')"

  # A subject folder that is not on the map in notes/subjects.md. If it is not
  # listed, nobody will find it, and the same subject gets started again later
  # under a different name.
  if [ -f notes/subjects.md ]; then
    # Strip the commented-out example at the bottom of the file first. It names
    # sleep and photography, so without this a subject actually called sleep
    # looks like it is already on the map when nobody has listed it.
    find notes -mindepth 2 -maxdepth 2 -name 'what-i-think.md' 2>/dev/null \
      | awk -F/ -v map=notes/subjects.md '
          BEGIN {
            # Skip commented-out lines. Someone parking a subject for a while
            # comments it out, and a commented line is not on the map.
            while ((getline line < map) > 0) {
              if (line ~ /<!--/) skip = 1
              if (!skip) listed = listed line "\n"
              if (line ~ /-->/) skip = 0
            }
            close(map)
          }
          { if (index(listed, "(" $2 "/what-i-think.md)") == 0)
              print "notes/" $2 " is not listed in notes/subjects.md. Add a one-line entry so it can be found." }'
  fi

  # A subject nobody has touched in a year. Either it is finished or it is
  # dead, and dead ones should go.
  stale_folders notes 365 | while read -r d; do
    [ -f "$d/what-i-think.md" ] || continue
    case "$d" in notes/where-i-learn) continue ;; esac
    has_no_end "$d/what-i-think.md" && continue
    echo "$d has not changed in a year. Ask whether it is finished or dead."
  done

  # ---- work/ ----------------------------------------------------------------
  # Projects wear out faster than notes do, so the clock here is months, not
  # years. archive/ is skipped throughout: going stale is what it is for.

  # A project folder that is not on the map.
  if [ -f work/projects.md ]; then
    find work -mindepth 1 -maxdepth 1 -type d ! -name archive 2>/dev/null \
      | awk -F/ -v map=work/projects.md '
          BEGIN {
            while ((getline line < map) > 0) {
              if (line ~ /<!--/) skip = 1
              if (!skip) listed = listed line "\n"
              if (line ~ /-->/) skip = 0
            }
            close(map)
          }
          { if (index(listed, "(" $2 "/brief.md)") == 0)
              print "work/" $2 " is not listed in work/projects.md. Add a one-line entry so it can be found." }'
  fi

  # A project missing one of its two required files.
  find work -mindepth 1 -maxdepth 1 -type d ! -name archive 2>/dev/null | while read -r d; do
    [ -f "$d/brief.md" ] || echo "$d has no brief.md. Nobody can tell what it is or when it is done."
    [ -f "$d/decisions.md" ] || echo "$d has no decisions.md. Add it before the reasons are forgotten."
  done

  # A project nobody has touched in three months. Not a nag to work on it —
  # a question. Paused and dead are both fine answers; pretending is not.
  stale_folders work 90 | while read -r d; do
    has_no_end "$d/brief.md" && continue
    echo "$d has not changed in three months. Ask whether it is paused, done, or dead — /archive handles the last two."
  done

  # Too many at once. Everything past a few is an idea wearing a project's
  # clothes, and ideas belong on the map with no folder.
  # Only the ones with an end are counted. Four things you are trying to finish
  # is a lot; four things you simply keep is not, and telling someone their
  # hobbies are too numerous is how a folder stops feeling like theirs.
  nproj=$(find work -mindepth 2 -maxdepth 2 -name brief.md ! -path 'work/archive/*' \
          -exec grep -L -F "$NO_END_MARK" {} + 2>/dev/null | wc -l | tr -d ' ')
  [ "${nproj:-0}" -gt "$MAX_PROJECTS" ] && \
    echo "$nproj projects in work/ with an end. Past about $MAX_PROJECTS, most are ideas. Ask which ones to move to the Ideas list."

  # ---- .claude/skills/ ------------------------------------------------------
  # A skill that Claude Code will not load fails silently: you type /thing,
  # nothing happens, and nothing anywhere says why. These are the rules from
  # the Agent Skills docs, checked here so a broken one gets noticed.
  find .claude/skills -mindepth 1 -maxdepth 1 -type d 2>/dev/null | while read -r d; do
    name=$(basename "$d")
    case "$name" in README*) continue ;; esac
    if [ ! -f "$d/SKILL.md" ]; then
      echo "$d has no SKILL.md, so it is not a skill and nothing will run it."
      continue
    fi
    awk -v dir="$name" -v path="$d/SKILL.md" '
      NR == 1 && $0 != "---" { print path " does not start with a --- settings block, so Claude Code will not load it."; bad = 1; exit }
      NR > 1 && $0 == "---" { infm = 0 }
      NR == 1 { infm = 1; next }
      infm && /^name:/        { n = $2 }
      infm && /^description:/ { d = 1 }
      !infm { body++ }
      END {
        if (bad) exit
        if (n == "")        print path " has no name in its settings block."
        else if (n != dir)  print path " is named \"" n "\" but sits in a folder called \"" dir "\". Claude Code goes by the folder, so the name must match it."
        else if (n !~ /^[a-z0-9-]+$/) print path " has a name with characters that are not allowed. Lowercase letters, numbers and hyphens only."
        else if (n ~ /claude|anthropic/) print path " uses a reserved word in its name."
        if (!d) print path " has no description. That is the only thing that decides whether the skill ever starts on its own."
        if (body > 500) print path " is " body " lines. Past about five hundred, split the detail into a second file next to it and point at it."
      }' "$d/SKILL.md"
  done

  # ---- the nightly save -------------------------------------------------------
  # Only if it was ever turned on. A timer that stops working says nothing, so
  # the last good run is the only way to know.
  stamp=.claude/.state/daily-commit.last-success
  if [ -f "$stamp" ] && [ -z "$(find "$stamp" -mmin -2880 2>/dev/null)" ]; then
    echo "The nightly save hasn't worked for over two days. Its log is /tmp/os-commit.log. A common cause on a Mac is the folder sitting in Downloads, Documents or Desktop."
  fi

  # ---- a half-finished upgrade ----------------------------------------------
  # The upgrade leaves the new version of each file the person had changed in
  # .claude/.upgrade/new/. Left there, nobody ever carries the change over.
  if [ -d .claude/.upgrade/new ]; then
    n=$(find .claude/.upgrade/new -type f -name '*.new' 2>/dev/null | wc -l | tr -d ' ')
    [ "$n" -gt 0 ] && echo "An upgrade left $n of your own files with a newer version beside them. Offer /update-os to finish carrying the changes over."
  fi

  # ---- the folder itself ----------------------------------------------------
  # Stray files at the top level. This is the one that keeps the whole thing
  # readable after a few years — everything belongs in one of the four rooms.
  # Dotfiles are skipped: those are machine clutter and settings, and
  # .gitignore already decides which of them get committed. What this is
  # looking for is a document nobody gave a home to.
  find . -maxdepth 1 -type f ! -name '.*' 2>/dev/null | while read -r f; do
    case "$(basename "$f")" in
      README.md|CLAUDE.md|LICENSE) ;;
      *) echo "$f sits loose at the top level. Move it into me/, notes/, work/ or .claude/, or delete it." ;;
    esac
  done
}

os_health() {
  cd "$ROOT" 2>/dev/null || return 0

  # These are never capped. There are at most three of them, and they are
  # the only ones nobody but the person can deal with.

  has_python || echo "python3 isn't installed, so most of this folder's checks are switched off. Tell them once: on a Mac, run xcode-select --install in Terminal, then restart Claude Code."

  # Blanks nobody has filled. The highest-value lines in the folder.
  #
  # The marker means "someone has to answer this", NOT "this is empty".
  # Sections that fill in on their own over months — things I do over and
  # over, how I do things, what's actually proven — must never carry it, or a
  # correctly finished setup gets nagged about them at every session forever.
  # Don't write the words in ordinary prose either; the grep can't tell the
  # difference between using the marker and talking about it.
  #
  # Two of them blank at once means nobody has ever run /setup — a folder
  # straight off the download. That is a different situation and needs a
  # different answer: point at /setup once, don't start the interview by
  # hand one file at a time. Anything less than both is a half-finished
  # setup, and that gets the ordinary nudge below.
  if grep -q 'TO FILL' me/who-i-am.md 2>/dev/null &&
     grep -q 'TO FILL' CLAUDE.md 2>/dev/null; then
    echo "This folder has never been set up — the starting files are still blank. Offer /setup in one line, then wait. Don't run the questions yourself."
  else
    blanks=$(grep -rl 'TO FILL' me CLAUDE.md work 2>/dev/null | grep -v '^work/archive/' | os_not_mine | tr '\n' ' ' | sed 's/ *$//')
    [ -n "$blanks" ] && echo "Still blank: $blanks — ask about these when a natural moment comes up, don't interrogate."
  fi

  # Everything else is rot, and there can be a lot of it. Show the worst few.
  rot=$(os_rot)
  [ -n "$rot" ] || return 0
  n=$(printf '%s\n' "$rot" | wc -l | tr -d ' ')
  printf '%s\n' "$rot" | head -"$ROT_SHOWN"
  if [ "$n" -gt "$ROT_SHOWN" ]; then
    echo "...and $((n - ROT_SHOWN)) more like that. Don't work through them here — say so in one line and offer /tidy-up."
  fi
}
