---
name: update-os
description: >-
  Brings this OS folder up to a newer version of the template — new fixes to
  the skills, hooks and rules — without losing anything the person wrote or
  changed. Use when they ask to update or upgrade the folder, ask whether
  there's a new version, or have downloaded one.
when_to_use: >-
  The user says 'update my OS', 'upgrade the folder', 'there's a new version',
  'get the latest template', or names a folder they just downloaded. Also when
  the session check says an upgrade was left half-finished.
argument-hint: "[folder with a new version — leave out to fetch the newest]"
---
Upgrade this folder from: $ARGUMENTS

A script does the safe part. You do the part that needs judgment: carrying the
template's changes into files the person has made their own.

## 1. Where the new version comes from

Usually nowhere you need to ask about: with no folder named, the script
downloads the newest version from the template's GitHub page itself. Only if
they name a folder — a fresh download they unzipped themselves — pass it on
the end of both commands below. Never point it at a folder somebody has used;
the script refuses those anyway, because this folder never reads anyone
else's.

If the folder is a git repository with uncommitted changes, offer to commit
first, in one line. It makes the whole upgrade one easy undo.

## 2. Preview, then ask

```bash
bash .claude/scripts/upgrade.sh --preview
```

"Nothing to do" means they already have the newest version. Say so and stop.

Tell them what it says in three or four lines: how many files get the new
version, which of theirs stay as they are, and anything they deleted that
stays deleted. Then ask for a yes. Don't paste the whole list unless they ask.

## 3. Run it

```bash
bash .claude/scripts/upgrade.sh
```

## 4. Carry the changes over, one file at a time

Every file in `.claude/.upgrade/new/` is one the person changed, where the
template also changed it. Theirs was left alone; the new version sits beside
it with `.new` on the end.

Read `.claude/CHANGES.md` — the new one, now in place — for every version
after the one they came from. It says what changed and why. Then for each
file:

1. Compare theirs with the `.new` one.
2. Work out which differences are the template's and which are theirs.
3. Say in a sentence or two what you would carry over. Write it on a yes.
4. Delete the `.new` file.

Rules for doing it well:

- **Their edits win.** You are adding the template's change to their file,
  never the other way round. If the two really clash, show both and ask.
- **In `me/`, `notes/`, `work/` and `CLAUDE.md`,** carry over wording,
  headings and rules only. Never touch their answers.
- **In `settings.json`,** keep their permissions and any hook they turned
  off. Add new hooks.
- **A hook, script or test they don't remember changing** — take the new one
  whole. Hand-edits there are rare; old copies without a record list every
  file this way.

## 5. Finish

- `.claude/.upgrade/before/` holds the old version of every file that was
  replaced, and `.claude/.upgrade/removed/` holds files the template dropped.
  Say so in one line: that's the way back if anything seems wrong.
- Run `./.claude/tests/run.sh`. If anything fails, say so plainly and which
  check. Don't call the upgrade done.
- Once `new/` is empty and the checks pass, ask before deleting
  `.claude/.upgrade/` — it's their backup of this upgrade.
- Offer to commit. If they unzipped a download themselves, say they can
  delete it.

## What this skill never does

**It never overwrites a file the person changed.** The script guarantees that
for the first pass; you keep it true in step 4. **It never brings back
something they deleted** — if they want one back, they can copy it from the
download themselves.
