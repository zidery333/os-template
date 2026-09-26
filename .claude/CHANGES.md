# What changed in each version

Newest at the top. `/update-os` reads this to know what to carry into files
you've made your own, so each entry says exactly what moved, file by file.

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
