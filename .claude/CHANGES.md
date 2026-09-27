# What changed in each version

Newest at the top. `/update-os` reads this to know what to carry into files
you've made your own, so each entry says exactly what moved, file by file.

## 2026-09-27.2 — /snag and a one-line contact part

- **New: `.claude/skills/snag/SKILL.md`.** `/snag <what broke>` writes down
  something wrong with the folder's machinery in `.claude/snags.md`: first
  seen, last seen, how many times, one sentence. The same snag again bumps its
  count instead of adding a line. Claude also writes one on its own when the
  machinery gets in its way, and never puts your own content in it. `/snag` on
  its own shows the list, most repeated first, ready to send.
- **`.claude/snags.md` is yours.** The template never ships one, and an
  upgrade never reads, replaces or mentions it.
- **`.claude/scripts/upgrade.sh`:** in `shipped_files()`, added
  `! -path './.claude/snags.md'` after `! -path './.claude/shipped.tsv'`, so
  the list can never end up in `shipped.tsv`.
- **`.claude/hooks/spot-worth-saving.sh`:** right after the `LOWER=` line,
  added `case "$LOWER" in /snag*) exit 0 ;; esac` with a two-line comment, so
  a `/snag` message isn't offered for saving as a fact about you.
- **`.claude/tests/run.sh`:** new part, "The snag list", before "Odd names,
  odd machines": the list stays out of the record, survives an upgrade, and
  no check nags about it. 160 checks now.
- **`.claude/README.md`:** a `snags.md` row in the first table, after
  `CHANGES.md`, and a `snag` row in the skills table, after `update-os`.
- **`CLAUDE.md`:** one new last bullet under "Working rules": "**Log snags.**
  When this folder's machinery gets in your way, write it down with `/snag`
  without asking. Never about my own work." If you edited yours, add that line
  by hand.
- **`README.md`:** `/snag` added as the last line of the Commands block. The
  "Make it better" part is one line now: "Broken thing or better idea? Send
  your `/snag` list to **zidery333** on Discord." Nothing else changed.

## 2026-09-27 — a much shorter README

- **`README.md`:** cut from about 1,900 words to about 280. It keeps the
  install steps, the four rooms, five commands, two rules and what's proven,
  and points to `.claude/README.md` for everything else. New part, "Make it
  better": how to reach the author on Discord with fixes and ideas. Nothing
  else changed; if you edited your own README, keep yours.

## 2026-09-26.6 — the home folder is gone again

- **Removed: `.claude/home/`.** Its one real user found it didn't fit how
  they work: it looked after folders but couldn't work in them. If you had it,
  `/update-os` moves it to `.claude/.upgrade/removed/` rather than deleting it,
  and any home folder you already copied out keeps working on its own.
- **`README.md`:** the "Got more than one OS folder?" part is gone.
- **`.claude/README.md`:** the table no longer lists `home/`.
- **`.claude/tests/run.sh`:** no longer runs the home folder's checks.

## 2026-09-26.5 — the home folder can set up a new OS folder

- **`.claude/home/home`:** new command, `./home new <path> "what it's for"`.
  It downloads a fresh copy of the template, starts git in it with a first
  commit, and adds it to the list. It never overwrites anything, refuses to
  put one OS folder inside another, and refuses a copy someone filled in.
  Also fixed: status now finds projects inside category folders and skips
  folders a folder's own settings ignore; sync says when a folder has gone
  instead of listing every file as new. A purpose with a line break no
  longer breaks `folders.tsv`, and stray `*` marks are gone from status.
- **`.claude/home/README.md`:** says how to use `./home new`, that a shared
  skill should be changed in `master/`, not in a folder, and exactly what
  sync writes in each kind of folder.
- **`.claude/home/CLAUDE.md`, `.claude/home/master/README.md`, `README.md`:** mention it.
- **`.claude/home/tests/run.sh`:** checks for all of the above.

## 2026-09-26.4 — an optional home folder for people with several OS folders

- **New: `.claude/home/`.** An optional home folder: copy it out beside your OS
  folders and it shows what's on the go in each (`./home status`), runs their
  checks (`./home check`), and sends shared skills to all of them
  (`./home sync`, a dry run until you add `--apply`). It never writes into
  me/, notes/ or work/, never deletes, and refuses when a folder isn't in git,
  has unsaved changes to a file it would write, or has something running in it.
  `.claude/home/README.md` says how to set it up.
- **`.claude/tests/run.sh`:** also runs the home folder's own checks, and
  checks its scripts can be run.
- **`README.md`:** new part, "Got more than one OS folder?", before "Keeping it safe".
- **`.claude/README.md`:** the table lists `home/`.

## 2026-09-26.3 — /wrapup, and setup stops guessing

- **New: `.claude/skills/wrapup/SKILL.md`.** Say you're done and it makes sure
  each brief and decision log is true about today, offers to commit, and says
  whether it's safe to close. It writes no session log.
- **`CLAUDE.md`, "What runs on its own", last paragraph:** now says no session
  log is written, and that `/wrapup` only makes the folder's files true.
- **`.claude/skills/setup/SKILL.md`:** it no longer guesses a choice that is
  still open (which blockchain, which language) into `who-to-trust.md`; it
  names it as the thing to decide. Two answer numbers left over from when
  there were eight questions are fixed.
- **`.claude/hooks/spot-worth-saving.sh`:** text you paste in is no longer
  read as you pushing back, and a long message is never taken as pushback.
- **`README.md` and `.claude/README.md`:** list `/wrapup`.

## 2026-09-26.2 — /update-os fetches the newest version itself

- **`.claude/scripts/upgrade.sh`:** with no folder named, it downloads the
  newest version from the template's GitHub page (the `TEMPLATE_HOME` line
  near the top) and uses that. Naming a folder still works.
- **`.claude/skills/update-os/SKILL.md`:** no longer asks where the new
  version is. It previews, asks, runs, then helps merge.
- **`README.md`, "Getting a newer version":** just type `/update-os`. And how
  to hear about new versions: Watch, then Custom, then Releases.
- **`.claude/README.md`, "The scripts":** says where `upgrade.sh` gets new
  versions from.
- **`.claude/tests/run.sh`:** three checks for the download.

## 2026-09-26 — first public version

Everything is new.
