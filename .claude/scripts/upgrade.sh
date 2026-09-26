#!/bin/bash
# Brings this folder up to a newer version of the template, without touching
# anything you wrote or changed.
#
#   bash .claude/scripts/upgrade.sh --preview <new-copy>   see what would happen
#   bash .claude/scripts/upgrade.sh <new-copy>             do it
#
# <new-copy> is a fresh download of the template, unzipped anywhere. Easier
# still: type /update-os and let Claude run this and walk you through the rest.
#
# How it tells your changes from the template's: .claude/shipped.tsv holds a
# fingerprint of every file as the template handed it to you. For each file:
#
#   still as shipped      replaced with the new version
#   you changed it        left alone; the new version is put beside it in
#                         .claude/.upgrade/new/ for you to look at
#   you deleted it        stays deleted, this time and every time after
#   new in the template   added
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

# One fingerprint, used everywhere, so a record made on a Mac still matches
# on Linux. cksum is on every system and needs nothing installed.
print_hash() { cksum < "$1" | awk '{ print $1 "-" $2 }'; }

# Every file that counts as part of the template, one per line, sorted.
shipped_files() {
  (cd "$1" && find . -type f \
      ! -path './.git/*' ! -path './.claude/.state/*' ! -path './.claude/.upgrade/*' \
      ! -name '.DS_Store' ! -path './.claude/settings.local.json' \
      ! -path './.claude/shipped.tsv' | sed 's|^\./||' | LC_ALL=C sort)
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

  local preview=0
  [ "${1:-}" = "--preview" ] && { preview=1; shift; }
  [ -n "${1:-}" ] || die "say where the new copy is: upgrade.sh [--preview] <folder>"

  local here new
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  new="$(cd "$1" 2>/dev/null && pwd)" || die "can't open $1"
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
  local added="" updated="" kept="" gone="" removed="" orphaned=""
  # Never start over the top of the last upgrade. What it left — your files
  # with a new version beside them, the old versions it kept — would be lost,
  # and the record has already moved on, so none of it would be offered again.
  if [ "$preview" -eq 0 ]; then
    [ ! -e "$out" ] || die "the last upgrade is still in .claude/.upgrade/. Finish it with /update-os, or delete that folder if you're done with it, then run this again."
    mkdir -p "$out"
  fi

  bring_in() {  # bring_in <path>: copy the new version over this folder's one
    # Keep the old one first, just in case. Cheap, and it makes undoing the
    # whole upgrade a copy back rather than a hope.
    if [ -f "$here/$1" ]; then
      mkdir -p "$(dirname "$out/before/$1")"
      cp -p "$here/$1" "$out/before/$1"
    fi
    mkdir -p "$(dirname "$here/$1")"
    cp "$new/$1" "$here/$1"
    case "$1" in *.sh) chmod +x "$here/$1" ;; esac
  }
  put_beside() {  # put_beside <path>: leave theirs, put the new one in .upgrade/new
    mkdir -p "$(dirname "$out/new/$1")"
    cp "$new/$1" "$out/new/$1.new"
  }

  # Everything the new version ships.
  local o c
  while IFS=$'\t' read -r n path; do
    case "$n" in \#*|"") continue ;; esac
    o="$(recorded "$oldrec" "$path")"
    c=""; [ -f "$here/$path" ] && c="$(print_hash "$here/$path")"

    if [ -z "$c" ] && [ -n "$o" ]; then
      # Shipped before, not here now: they deleted it. It stays deleted.
      gone="$gone
  $path"
      printf 'gone\t%s\n' "$path" >> "$rec_tmp"; continue
    fi
    printf '%s\t%s\n' "$n" "$path" >> "$rec_tmp"

    if [ -z "$c" ]; then
      added="$added
  $path"
      [ "$preview" -eq 1 ] || bring_in "$path"
    elif [ "$c" = "$n" ]; then
      :  # already the same as the new version
    elif [ -n "$o" ] && [ "$o" != "gone" ] && [ "$c" = "$o" ]; then
      updated="$updated
  $path"
      [ "$preview" -eq 1 ] || bring_in "$path"
    elif [ -n "$o" ] && [ "$o" = "$n" ]; then
      :  # they changed it, the template didn't; nothing new to offer
    else
      kept="$kept
  $path"
      [ "$preview" -eq 1 ] || put_beside "$path"
    fi
  done < "$newrec"

  # Everything the old version shipped that the new one doesn't.
  if [ "$norecord" -eq 0 ]; then
    while IFS=$'\t' read -r o path; do
      case "$o" in \#*|"") continue ;; esac
      [ -n "$(recorded "$newrec" "$path")" ] && continue
      [ -f "$here/$path" ] || continue
      if [ "$o" != "gone" ] && [ "$(print_hash "$here/$path")" = "$o" ]; then
        removed="$removed
  $path"
        if [ "$preview" -eq 0 ]; then
          mkdir -p "$(dirname "$out/removed/$path")"
          mv "$here/$path" "$out/removed/$path"
          # Tidy away the folder it leaves empty, like a skill's own folder.
          local d; d="$(dirname "$here/$path")"
          while [ "$d" != "$here" ] && rmdir "$d" 2>/dev/null; do d="$(dirname "$d")"; done
        fi
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
    echo "Upgrading from ${from:-an unrecorded version} to ${to:-an unnamed version}."
    if [ "$norecord" -eq 1 ]; then
      echo "This folder has no record of what it was shipped with, so every file"
      echo "that differs is treated as one you changed. Nothing of yours is lost;"
      echo "there is just more to look over by hand this once."
    fi
    [ -z "$updated" ]  || echo "Replaced with the new version (you never changed these). The old ones are kept in .claude/.upgrade/before/:$updated"
    [ -z "$added" ]    || echo "New in this version:$added"
    [ -z "$kept" ]     || echo "Yours, left as they are. The new version is beside each in .claude/.upgrade/new/:$kept"
    [ -z "$gone" ]     || echo "You deleted these, so they stay deleted:$gone"
    [ -z "$removed" ]  || echo "Dropped from the template. Moved to .claude/.upgrade/removed/:$removed"
    [ -z "$orphaned" ] || echo "Dropped from the template, but you changed them, so they stay:$orphaned"
    if [ -z "$updated$added$kept$removed" ]; then echo "Nothing to do. This folder already matches."; fi
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
  [ -n "$kept$removed$updated" ] || { rm -rf "$out"; }
  return 0
}

main "$@"; exit $?
