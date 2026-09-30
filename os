#!/usr/bin/env bash
# Zenith — the one command.
#   ./os              where everything stands
#   ./os help         everything you can type
#
# Works from anywhere: it finds its own location, so you can symlink this file
# into ~/bin and still drive the folder it came from.
set -euo pipefail

# resolve this script through any number of symlinks
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ $src != /* ]] && src="$dir/$src"
done
HERE="$(cd -P "$(dirname "$src")" && pwd)"

ENGINE="$HERE/.os/engine.py"
if [ ! -s "$ENGINE" ]; then
  # Missing, or empty: an update stopped while it was replacing the program.
  # The copy it made first is the way back.
  kept=""
  for copy in "$HERE"/.os/backups/before-upgrade-*/.os/engine.py; do
    [ -s "$copy" ] && kept="$copy"
  done
  printf '\033[38;5;167m  ✖ something is missing or empty: %s\033[0m\n' "$ENGINE" >&2
  if [ -n "$kept" ]; then
    printf '   The last update kept a copy from before it. To put it back:\n' >&2
    printf '     cp "%s" "%s"\n' "$kept" "$ENGINE" >&2
    printf '   Then ./os update brings in the newest again.\n' >&2
  else
    printf '   This file has to sit in the top of a Zenith folder to work.\n' >&2
  fi
  exit 1
fi

PY="${ZENITH_PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in python3 python3.13 python3.12 python3.11 /usr/bin/python3; do
    found="$(command -v "$candidate" 2>/dev/null)" || continue
    # On a Mac without Apple's developer tools, /usr/bin/python3 is only a
    # stand-in that pops up an install box each time it runs, and the hooks
    # run ./os at every start and after every reply. Look; don't run it.
    if [ "$found" = /usr/bin/python3 ] && [ "$(uname -s)" = Darwin ] \
       && ! xcode-select -p >/dev/null 2>&1; then
      continue
    fi
    PY="$found"; break
  done
fi
if [ -z "$PY" ]; then
  printf '\033[38;5;167m  ✖ Python is not installed\033[0m\n' >&2
  printf '   Zenith needs Python 3.9 or newer.\n' >&2
  printf '   On a Mac, run:  xcode-select --install\n' >&2
  printf '   Otherwise, get it from python.org\n' >&2
  exit 1
fi

# Always this folder, never one inherited from the environment. A ZENITH_HOME
# left over from another folder — exported in a shell profile, or by a hook —
# would otherwise make ./os here quietly drive somewhere else entirely.
export ZENITH_HOME="$HERE"
exec "$PY" "$ENGINE" "$@"
