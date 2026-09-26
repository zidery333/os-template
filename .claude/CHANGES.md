# What changed in each version

Newest at the top. `/update-os` reads this to know what to carry into files
you've made your own, so each entry says exactly what moved, file by file.

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
