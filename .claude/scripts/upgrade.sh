#!/bin/bash
# Brings this folder up to a newer version of the template, without touching
# anything you wrote or changed.
#
#   bash .claude/scripts/upgrade.sh --preview    see what the newest version would change
#   bash .claude/scripts/upgrade.sh              take it
#
# With no folder named, it downloads the newest version from the template's
# GitHub page (TEMPLATE_HOME below) into a temporary folder, and deletes that
# again afterwards. Or name a fresh download you unzipped yourself:
#
#   bash .claude/scripts/upgrade.sh [--preview] <new-copy>
#
# Easier still: type /update-os and let Claude run this and walk you through
# the rest.
#
# How it tells your changes from the template's: .claude/shipped.tsv holds a
# fingerprint of every file as the template handed it to you. For each file:
#
#   still as shipped      replaced with the new version
#   you changed it        left alone; the new version is put beside it in
#                         .claude/.upgrade/new/ for you to look at
#   you deleted it        stays deleted, this time and every time after
#   new in the template   added, or put beside yours in .claude/.upgrade/new/
#                         if you already made a file with that name
#   dropped from it       moved to .claude/.upgrade/removed/ if you never
#                         changed it, left alone if you did
#
# Every file it replaces is copied to .claude/.upgrade/before/ first, so the
# old version is always there to go back to.
#
# Anything the template never shipped — your notes, your projects, your own
# skills — is never read or touched.
#
# The whole script sits inside main() so that bash reads all of it before
# running any of it. This file can replace itself halfway through.
set -uo pipefail

# Where new versions come from. If you made your own copy of the template on
# GitHub and want updates from it instead, change this line.
TEMPLATE_HOME="https://github.com/zidery333/os-template"

# One fingerprint, used everywhere, so a record made on a Mac still matches
# on Linux. cksum is on every system and needs nothing installed.
print_hash() { cksum < "$1" | awk '{ print $1 "-" $2 }'; }

# Every file that counts as part of the template, one per line, sorted.
# .claude/snags.md is the person's own list from /snag, so it never counts,
# even in a copy that has one by mistake.
shipped_files() {
  (cd "$1" && find . -type f \
      ! -path './.git/*' ! -path './.claude/.state/*' ! -path './.claude/.upgrade/*' \
      ! -name '.DS_Store' ! -path './.claude/settings.local.json' \
      ! -path './.claude/shipped.tsv' ! -path './.claude/snags.md' | sed 's|^\./||' | LC_ALL=C sort)
}

record_top() {  # record_top <version>
  printf '# What the template shipped: one fingerprint per file. Written by\n'
  printf '# .claude/scripts/upgrade.sh. Leave it alone; it is how an upgrade\n'
  printf '# tells your changes from the template'"'"'s.\n'
  printf '# version\t%s\n' "$1"
}

# Used when a new version of the template is made, and by the tests.
write_record() {  # write_record <folder> <version>
  local dir="$1" f
  {
    record_top "$2"
    shipped_files "$dir" | while IFS= read -r f; do
      printf '%s\t%s\n' "$(print_hash "$dir/$f")" "$f"
    done
  } > "$dir/.claude/shipped.tsv"
}

version_of() { awk -F'\t' '$1 == "# version" { print $2; exit }' "$1" 2>/dev/null; }
# The path goes in through the environment, not -v, which would eat backslashes.
recorded()   { P="$2" awk '{ t = index($0, "\t") }
                 t && substr($0, 1, 1) != "#" && substr($0, t + 1) == ENVIRON["P"] { print substr($0, 1, t - 1); exit }' "$1" 2>/dev/null; }
die()        { echo "Stopped: $*" >&2; exit 1; }

main() {
  if [ "${1:-}" = "--write-record" ]; then
    [ -d "${2:-}/.claude" ] && [ -n "${3:-}" ] || die "usage: upgrade.sh --write-record <folder> <version>"
    write_record "$2" "$3"; return 0
  fi

  local preview=0 src="" a
  for a in "$@"; do
    case "$a" in
      --preview) preview=1 ;;
      -*) die "usage: upgrade.sh [--preview] [<new-copy>]" ;;
      *) [ -z "$src" ] || die "usage: upgrade.sh [--preview] [<new-copy>]"; src="$a" ;;
    esac
  done

  local here new
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  if [ -z "$src" ]; then
    # A download's copy of this script, run from inside your own folder,
    # would upgrade the download and say it already matches.
    if [ -f "$PWD/.claude/shipped.tsv" ] && [ "$(pwd -P)" != "$(cd "$here" && pwd -P)" ]; then
      die "this script upgrades the folder it sits in ($here), not the one you're in. In your own folder run: bash .claude/scripts/upgrade.sh"
    fi
    # unzip is missing on some Linux machines; python3 can open a ZIP too.
    if command -v unzip >/dev/null 2>&1; then unpack() { unzip -q "$1" -d "$2"; }
    elif command -v python3 >/dev/null 2>&1; then unpack() { python3 -m zipfile -e "$1" "$2"; }
    else die "this needs unzip, which isn't on this computer. On Ubuntu or Debian: sudo apt install unzip"; fi
    # No folder named: fetch the newest version. The ZIP from GitHub is a
    # fresh copy by definition, so it passes the check below like any other.
    local url="${OS_TEMPLATE_ZIP_URL:-$TEMPLATE_HOME/archive/refs/heads/main.zip}"
    FETCHED="$(mktemp -d)" || die "couldn't make a temporary folder."
    trap 'rm -rf "$FETCHED"' EXIT
    curl -sfL -o "$FETCHED/new.zip" "$url" || die "couldn't download $url. Are you online?"
    unpack "$FETCHED/new.zip" "$FETCHED/unzipped" 2>/dev/null || die "the download from $url isn't a ZIP file."
    src="$(find "$FETCHED/unzipped" -mindepth 1 -maxdepth 1 -type d | head -1)"
    [ -n "$src" ] || die "the download from $url was empty."
    echo "Downloaded the newest version from $TEMPLATE_HOME"
  fi
  new="$(cd "$src" 2>/dev/null && pwd)" || die "can't open $src"
  [ "$new" != "$here" ] || die "that is this folder. Point at the new copy you downloaded."
  local newrec="$new/.claude/shipped.tsv" oldrec="$here/.claude/shipped.tsv"
  [ -f "$newrec" ] || die "$new has no .claude/shipped.tsv, so it isn't a copy of the template this script can read."

  # Only a fresh download. A folder somebody has filled in is theirs, and this
  # folder never reads anyone else's.
  local n path bad=""
  while IFS=$'\t' read -r n path; do
    case "$n" in \#*|"") continue ;; esac
    if [ ! -f "$new/$path" ]; then bad="$bad
  missing: $path"
    elif [ "$(print_hash "$new/$path")" != "$n" ]; then bad="$bad
  changed: $path"; fi
  done < "$newrec"
  [ -z "$bad" ] || die "$new is not a fresh copy. Download it again and point at that.$bad"

  local from to
  from="$(version_of "$oldrec")"; to="$(version_of "$newrec")"
  local norecord=0
  [ -f "$oldrec" ] || norecord=1

  local out="$here/.claude/.upgrade" rec_tmp
  rec_tmp="$(mktemp)"
  local added="" updated="" kept="" taken="" gone="" removed="" orphaned="" failed=""
  # Never start over the top of the last upgrade. What it left — your files
  # with a new version beside them, the old versions it kept — would be lost,
  # and the record has already moved on, so none of it would be offered again.
  if [ "$preview" -eq 0 ] && [ -e "$out" ]; then
    n=$(find "$out/new" -type f -name '*.new' 2>/dev/null | wc -l | tr -d ' ')
    them="$n of your own files with a newer version beside them"
    [ "$n" -eq 1 ] && them="one of your own files with a newer version beside it"
    [ "$n" -eq 0 ] || die "an earlier upgrade isn't finished. It left $them in .claude/.upgrade/new/. Type /update-os and it picks up there."
    die "the last upgrade's backup is still in .claude/.upgrade/. Delete that folder if you're done with it, then run this again."
  fi
  [ "$preview" -eq 1 ] || mkdir -p "$out"

  # Each of these fails if a copy does, so nothing is reported done that isn't.
  bring_in() {  # bring_in <path>: copy the new version over this folder's one
    # Keep the old one first, just in case. Cheap, and it makes undoing the
    # whole upgrade a copy back rather than a hope.
    if [ -f "$here/$1" ]; then
      mkdir -p "$(dirname "$out/before/$1")" && cp -p "$here/$1" "$out/before/$1" || return 1
    fi
    mkdir -p "$(dirname "$here/$1")" && cp "$new/$1" "$here/$1" || return 1
    case "$1" in *.sh) chmod +x "$here/$1" ;; esac
  }
  put_beside() {  # put_beside <path>: leave theirs, put the new one in .upgrade/new
    mkdir -p "$(dirname "$out/new/$1")" && cp "$new/$1" "$out/new/$1.new"
  }
  # A file it couldn't write keeps its old line in the record, so the next
  # upgrade tries it again instead of taking it for one of yours.
  couldnt() {  # couldnt <path> <old fingerprint>
    failed="$failed
  $1"
    [ -z "$2" ] || printf '%s\t%s\n' "$2" "$1" >> "$rec_tmp"
  }

  # Everything the new version ships.
  local o c theirs
  while IFS=$'\t' read -r n path; do
    case "$n" in \#*|"") continue ;; esac
    o="$(recorded "$oldrec" "$path")"
    # theirs:<fingerprint> is a file of their own that shares a name with one
    # of the template's, and which template version they were last shown.
    theirs=""; case "$o" in theirs:*) theirs="${o#theirs:}"; o="" ;; esac
    c=""; [ -f "$here/$path" ] && c="$(print_hash "$here/$path")"

    if [ -z "$c" ] && [ -n "$o" ]; then
      # Shipped before, not here now: they deleted it. It stays deleted.
      gone="$gone
  $path"
      printf 'gone\t%s\n' "$path" >> "$rec_tmp"; continue
    fi

    if [ -z "$c" ]; then
      [ "$preview" -eq 1 ] || bring_in "$path" || { couldnt "$path" ""; continue; }
      added="$added
  $path"
    elif [ "$c" = "$n" ]; then
      :  # already the same as the new version
    elif [ -n "$o" ] && [ "$o" != "gone" ] && [ "$c" = "$o" ]; then
      [ "$preview" -eq 1 ] || bring_in "$path" || { couldnt "$path" "$o"; continue; }
      updated="$updated
  $path"
    elif [ -n "$o" ] && [ "$o" = "$n" ]; then
      :  # they changed it, the template didn't; nothing new to offer
    elif [ -z "$o" ] && [ "$norecord" -eq 0 ]; then
      # New in the template, but they already made a file of their own with
      # this name. Not an edit of the template's, so it is recorded as theirs,
      # and shown again only when the template's one changes.
      if [ "$theirs" != "$n" ]; then
        [ "$preview" -eq 1 ] || put_beside "$path" || { couldnt "$path" ""; continue; }
        taken="$taken
  $path"
      fi
      printf 'theirs:%s\t%s\n' "$n" "$path" >> "$rec_tmp"; continue
    else
      [ "$preview" -eq 1 ] || put_beside "$path" || { couldnt "$path" "$o"; continue; }
      kept="$kept
  $path"
    fi
    printf '%s\t%s\n' "$n" "$path" >> "$rec_tmp"
  done < "$newrec"

  # Everything the old version shipped that the new one doesn't.
  if [ "$norecord" -eq 0 ]; then
    while IFS=$'\t' read -r o path; do
      case "$o" in \#*|""|theirs:*) continue ;; esac
      [ -n "$(recorded "$newrec" "$path")" ] && continue
      [ -f "$here/$path" ] || continue
      if [ "$o" != "gone" ] && [ "$(print_hash "$here/$path")" = "$o" ]; then
        if [ "$preview" -eq 0 ]; then
          mkdir -p "$(dirname "$out/removed/$path")" && mv "$here/$path" "$out/removed/$path" \
            || { couldnt "$path" "$o"; continue; }
          # Tidy away the folder it leaves empty, like a skill's own folder.
          local d; d="$(dirname "$here/$path")"
          while [ "$d" != "$here" ] && rmdir "$d" 2>/dev/null; do d="$(dirname "$d")"; done
        fi
        removed="$removed
  $path"
      else
        orphaned="$orphaned
  $path"
      fi
    done < "$oldrec"
  fi

  # The report. Plain lines, the same on screen and on disk.
  local report
  report="$(
    if [ "$preview" -eq 1 ]; then echo "PREVIEW — nothing has been changed yet."; fi
    echo "Upgrading $here from ${from:-an unrecorded version} to ${to:-an unnamed version}."
    if [ "$norecord" -eq 1 ]; then
      echo "This folder has no record of what it was shipped with, so every file"
      echo "that differs is treated as one you changed. Nothing of yours is lost;"
      echo "there is just more to look over by hand this once."
    fi
    [ -z "$updated" ]  || echo "Replaced with the new version (you never changed these). The old ones are kept in .claude/.upgrade/before/:$updated"
    [ -z "$added" ]    || echo "New in this version:$added"
    [ -z "$kept" ]     || echo "Yours, left as they are. The new version is beside each in .claude/.upgrade/new/:$kept"
    [ -z "$taken" ]    || echo "New in the template, but you already have your own file with this name. The template's one is beside it in .claude/.upgrade/new/:$taken"
    [ -z "$gone" ]     || echo "You deleted these, so they stay deleted:$gone"
    [ -z "$removed" ]  || echo "Dropped from the template. Moved to .claude/.upgrade/removed/:$removed"
    [ -z "$orphaned" ] || echo "Dropped from the template, but you changed them, so they stay:$orphaned"
    [ -z "$failed" ]   || echo "Couldn't write these. The old version of any it had started to replace is in .claude/.upgrade/before/. Once you've fixed what stopped them and nothing is left in .claude/.upgrade/new/, delete .claude/.upgrade/ and run it again. It tries these again:$failed"
    if [ -z "$updated$added$kept$taken$removed$failed" ]; then echo "Nothing to do. This folder already matches."; fi
  )"
  echo "$report"

  if [ "$preview" -eq 1 ]; then rm -f "$rec_tmp"; return 0; fi

  # Write the new record last, so a run that stops halfway leaves the old one
  # in place and can simply be run again.
  {
    record_top "$to"
    cat "$rec_tmp"
  } > "$oldrec"
  rm -f "$rec_tmp"
  echo "$report" > "$out/report.txt"
  [ -n "$kept$taken$removed$updated$failed" ] || { rm -rf "$out"; }
  [ -z "$failed" ]
}

main "$@"; exit $?
