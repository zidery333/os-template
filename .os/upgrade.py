#!/usr/bin/env python3
"""
Zenith — upgrade an older folder to this template
=================================================

    python3 <this template>/.os/upgrade.py <the folder to upgrade>
    python3 <this template>/.os/upgrade.py <folder> --dry-run     say what would change
    python3 <this template>/.os/upgrade.py <folder> --anyway      replace a program changed there too

Brings the machinery up to date and leaves the person's things alone:

    replaced   os · .os/engine.py · .os/learn.py · .os/upgrade.py · .os/templates/
               .os/tests/ · .os/CHANGES.md (what changed, in words: the
               entries newer than their version are printed at the end)
               · the shipped skills, helpers and hooks in .claude/ · AGENTS.md
               — but a shipped file THEY edited is kept, and the new version set
               aside in .os/upgrades/<stamp>/ for them (or their AI) to merge. One
               they deleted stays deleted. A program file changed there and never
               published stops it, unless told --anyway.
               .claude/settings.json, and each subject's keywords and extensions
               and the words that tell a note from work in .os/words.json, and
               the line under the folder's name in .os/config.json, when still
               as released
    merged     .os/config.json and .os/words.json (new settings added, theirs kept)
               .claude/settings.json they changed (new template hooks and permission
               rules added, a shipped hook as an older release wrote it renewed, theirs
               kept) · .gitignore they changed (new lines added; one still as
               released is replaced)
    kept       Work/ Notes/ Archive/ · state, snags, undo history · every skill,
               helper or hook that is theirs · CLAUDE.md and GEMINI.md (only made to
               point at AGENTS.md; either one missing is added, one that is a link
               is left alone)
    migrated   in a folder from before releases only: W.04_ / N.03_ tags out of names
               and headers, then `./os sort` gives every renamed folder its Title Case

Everything replaced is copied first into <folder>/.os/backups/before-upgrade-<stamp>/,
so nothing is lost even if the new version is not wanted. The folder's
.os/shipped.json is written last, so an update that stopped partway is
finished by running it again.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # <template>/.os
TEMPLATE = HERE.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import engine  # noqa: E402  — the template's own, for names spelled its way

#: What the template owns outright and nobody edits: replaced every time.
#: `.os/shipped.json` is not in it: it says which version the folder has, so it
#: is written last, once everything else is in.
MACHINERY = [
    "os",
    ".os/engine.py", ".os/learn.py", ".os/upgrade.py",
    ".os/templates", ".os/tests",
    # The change note: what each release changed, in plain words. Theirs to
    # read, not to edit, so the newest always replaces it.
    ".os/CHANGES.md",
]
#: Shipped, but the kind of file a person reasonably edits. Replaced only when
#: the copy on disk still matches a version the template once released —
#: see `.os/shipped.json`. An edited one is kept and the new version set aside.
GUARDED = ["AGENTS.md"]
#: Fingerprinted the same way, but one they changed is merged, not set aside.
SETTINGS = ".claude/settings.json"
SHIPPED_SKILLS = ("catchup", "commands", "decide", "find", "handoff", "learn",
                  "make-skill", "save", "tidy", "wrapup")
SHIPPED_AGENTS = ("builder", "researcher", "reviewer", "student")
SHIPPED_HOOKS = ("keep-the-record.sh", "mark-dirty.sh", "session-start.sh", "settle.sh")

#: The program itself: replaced every time, but recorded too, so `./os update`
#: can tell a copy changed here and never published from an older release.
PROGRAM = ["os", ".os/engine.py", ".os/learn.py", ".os/upgrade.py"]
#: AGENTS.md carries the folder's own name in its first lines. Two copies that
#: differ only by that name are the same version. The name runs up to the
#: sentence after it, so "St. Ives" is read whole, not as "St.".
NAMED = re.compile(r"This folder is called ([^\n]+?)\. (?=It holds )|This folder is called ([^\n]+?)\. ")

TAG = re.compile(r"^([A-Za-z]\.\d{2,4}|\d{1,2}\.\d{2,4})[_ -]+(.*)$")
TEXT = {".md", ".txt", ".markdown"}


def sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def folder_name_in(path: Path) -> str:
    hit = NAMED.search(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else None
    return (hit.group(1) or hit.group(2)) if hit else ""


def named(text: str, name: str) -> str:
    return NAMED.sub(lambda _: f"This folder is called {name}. ", text, count=1)


def sha1_unnamed(path: Path) -> str:
    """AGENTS.md's fingerprint with the folder's name taken out."""
    if path.name != "AGENTS.md":
        return sha1(path)
    text = named(path.read_text(encoding="utf-8", errors="replace"), "Zenith")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def keywords_sha(words: list) -> str:
    """One subject's keyword list, fingerprinted: same words, same order."""
    return hashlib.sha1(json.dumps(words, ensure_ascii=False).encode("utf-8")).hexdigest()


#: The lists in each subject of words.json that an update replaces whole while
#: they are still as a release wrote them. Extensions joined when Writing lost
#: .md and .txt: every note had been filed as writing on its file type alone,
#: and a folder that updated would have gone on doing it.
RENEWED_LISTS = ("keywords", "extensions")
#: The same for each block under "intent", the words that tell a note from
#: work, recorded in shipped.json as intent_keywords and intent_patterns.
#: Only subjects were renewed, so "recipe", "ingredients" and the kitchen
#: amounts never reached a folder that updated, and a lentil soup recipe was
#: still filed there as work being pushed.
INTENT_LISTS = ("keywords", "patterns")
#: The line the first release put under the folder's name in ./os help. Each
#: release since records its own in shipped.json as "taglines": this list
#: alone missed the one that replaced it, which no later release could then
#: have changed (review, 2026-09-30).
RELEASED_TAGLINES = ("One folder for your work. Any AI can use it.",)


def released_taglines(root: Path | None = None) -> set:
    """Every line a release has put under the folder's name in ./os help."""
    lines = set(RELEASED_TAGLINES)
    for path in (HERE / "shipped.json", (root / ".os" / "shipped.json") if root else None):
        if path is not None and path.is_file():
            said = read_shipped(path).get("taglines")
            lines |= {t for t in (said if isinstance(said, list) else []) if isinstance(t, str)}
    return lines


def _record_lists(data: dict, blocks, lists: tuple, prefix: str = "") -> int:
    """Fingerprint each list in each block into data[prefix + list]."""
    added = 0
    for key in lists:
        known_lists = data.setdefault(prefix + key, {})
        for name, block in (blocks.items() if isinstance(blocks, dict) else []):
            if isinstance(block, dict) and isinstance(block.get(key), list):
                digest = keywords_sha(block[key])
                known = known_lists.setdefault(name, [])
                if digest not in known:
                    known.append(digest)
                    added += 1
    return added


def read_shipped(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def shipped_hashes(root: Path | None = None, key: str = "files") -> dict:
    """Every version of each shipped file ever released — the template's list,
    plus the list the folder itself was installed with, if it has one. With
    key="keywords", the same for each subject's keyword list in words.json."""
    known: dict = {}
    for path in (HERE / "shipped.json", (root / ".os" / "shipped.json") if root else None):
        if path is None or not path.is_file():
            continue
        files = read_shipped(path).get(key)
        for rel, hashes in (files.items() if isinstance(files, dict) else []):
            known.setdefault(rel, [])
            known[rel] += [h for h in hashes if h not in known[rel]]
    return known


def record() -> int:
    """Template maintainers: remember the current shipped files as released."""
    path = HERE / "shipped.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {"files": {}}
    files = data.setdefault("files", {})
    added = 0
    # Every file in a shipped skill, not just SKILL.md: one left out was
    # called theirs when the template changed it, and never updated again.
    skill_files = sorted(str(f.relative_to(TEMPLATE)) for s in SHIPPED_SKILLS
                         for f in (TEMPLATE / ".claude" / "skills" / s).rglob("*")
                         if f.is_file() and "__pycache__" not in f.parts)
    for rel in PROGRAM + GUARDED + [SETTINGS, ".gitignore"] + skill_files \
            + [f".claude/agents/{a}.md" for a in SHIPPED_AGENTS] \
            + [f".claude/hooks/{h}" for h in SHIPPED_HOOKS]:
        src = TEMPLATE / rel
        if src.is_file():
            digest = sha1_unnamed(src)
            known = files.setdefault(rel, [])
            if digest not in known:
                known.append(digest)
                added += 1
    # Each subject's keywords too, so an update can tell a list nobody
    # changed (it gets the new words) from one they did (it stays theirs).
    # And its file extensions, the same way.
    # And what tells a note from work, the same way.
    words = read_shipped(TEMPLATE / ".os" / "words.json")
    added += _record_lists(data, words.get("domains"), RENEWED_LISTS)
    added += _record_lists(data, words.get("intent"), INTENT_LISTS, "intent_")
    # And the line under its name in ./os help, so an update can tell one
    # still as a release wrote it (it gets the new line) from theirs.
    line = read_shipped(TEMPLATE / ".os" / "config.json").get("tagline")
    taglines = data.setdefault("taglines", [])
    if isinstance(line, str) and line and line not in taglines:
        taglines.append(line)
        added += 1
    # And every permission rule ever released, so an update can tell a new
    # one (added) from one they took out (left out).
    data["rules"] = sorted(set(data.get("rules") or []) | set(released_rules(TEMPLATE / SETTINGS)))
    release = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--release=")), "")
    if release:
        data["release"] = release
    path.write_text(json.dumps(data, indent=2) + "\n")
    say(f"  recorded {added} new version(s) into .os/shipped.json")
    return 0


def say(line: str = "") -> None:
    print(line)


def whats_new(release: str, most: int = 3) -> list[str]:
    """What the change note says is new since `release`, the version this
    folder had, as lines to print: `most` releases, newest first. Read from
    the new version's own .os/CHANGES.md, so an update from a version that
    never had one says it too."""
    try:
        text = (HERE / engine.CHANGES_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    entries = engine.changes_since(text, release)
    if not entries:
        return []
    lines = ["  WHAT'S NEW" + (f" since {release}" if release else "")]
    for head, body in entries[:most]:
        lines.append("    " + ("this version" if head == engine.UNRELEASED else head))
        lines += ["      " + line.rstrip() for line in body.splitlines() if line.strip()]
    if len(entries) > most:
        lines.append(f"    and {len(entries) - most} older, in .os/CHANGES.md")
    return lines


def die(msg: str) -> None:
    """Stop before anything has changed. Exit 2 tells ./os update so."""
    sys.stdout.flush()
    print(f"  ✖ {msg}", file=sys.stderr)
    sys.exit(2)


def copy(src: Path, dst: Path, text: str | None = None) -> None:
    """Copy src (or `text`, in place of src's words) over dst. The new copy
    goes in beside dst and is swapped in whole, so an update that is stopped
    never leaves a file half-written — an empty engine.py stops ./os dead."""
    if src.is_dir():
        new, old = dst.with_name(dst.name + ".new"), dst.with_name(dst.name + ".old")
        for leftover in (new, old):
            if leftover.exists():
                shutil.rmtree(leftover)
        shutil.copytree(src, new, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if dst.exists():
            dst.rename(old)
        new.rename(dst)
        if old.exists():
            shutil.rmtree(old)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    new = dst.with_name(dst.name + ".new")
    if text is None:
        shutil.copy2(src, new)
    else:
        new.write_text(text, encoding="utf-8")
        shutil.copymode(src, new)
    os.replace(new, dst)


def read_theirs(target: Path) -> dict | None:
    """Their copy of a settings file: {} when there is none, None when it
    won't read — a stray comma, usually — so that nothing is written over it."""
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def merge_json(target: Path, source: Path, skip: tuple = ()) -> list[str] | None:
    """Add keys the template has and the folder lacks. Theirs always win.
    None, and nothing written, when theirs can't be read."""
    theirs = read_theirs(target)
    if theirs is None:
        return None
    ours = json.loads(source.read_text())
    added: list[str] = []

    def walk(a: dict, b: dict, path: str) -> None:
        for k, v in b.items():
            if k in skip:
                continue
            if k not in a:
                a[k] = v
                added.append(f"{path}{k}")
            elif isinstance(a[k], dict) and isinstance(v, dict):
                walk(a[k], v, f"{path}{k}.")

    if isinstance(ours, dict):
        walk(theirs, ours, "")
        target.write_text(json.dumps(theirs, indent=2, ensure_ascii=False) + "\n")
    return added


def _renew_lists(mine, new, released: dict) -> list[str]:
    """Give each block in `mine` the new version of any list still exactly as
    a release wrote it (`released`: list name → block name → fingerprints).
    The names of the blocks that changed."""
    if not isinstance(mine, dict) or not isinstance(new, dict):
        return []
    renewed: list[str] = []
    for name, block in new.items():
        have = mine.get(name)
        if not isinstance(block, dict) or not isinstance(have, dict):
            continue
        for key, known in released.items():
            words, old = block.get(key), have.get(key)
            if isinstance(words, list) and isinstance(old, list) and words != old \
                    and keywords_sha(old) in known.get(name, []):
                have[key] = words
                if name not in renewed:
                    renewed.append(name)
    return renewed


def renew_keywords(target: Path, source: Path, known: dict,
                   extensions: dict | None = None,
                   intent: dict | None = None) -> list[str]:
    """A subject whose keywords are still a list the template released gets
    the new list, and the same for its extensions. One they changed stays
    theirs; `learned` is never touched. The intent lists, what tells a note
    from work, are renewed the same way when `intent` gives their released
    fingerprints ({"keywords": ..., "patterns": ...}); a block renewed there
    is named as "intent:<name>"."""
    theirs, ours = read_theirs(target), read_theirs(source)
    renewed = _renew_lists((theirs or {}).get("domains"), (ours or {}).get("domains"),
                           {"keywords": known, "extensions": extensions or {}})
    renewed += [f"intent:{name}" for name in _renew_lists(
        (theirs or {}).get("intent"), (ours or {}).get("intent"), intent or {})]
    if renewed:
        target.write_text(json.dumps(theirs, indent=2, ensure_ascii=False) + "\n")
    return renewed


def link_again(root: Path, name: str) -> str:
    """A CLAUDE.md or GEMINI.md made with `ln` from AGENTS.md, linked to the
    AGENTS.md there now, and what to say about it: "" when it already is."""
    pointer_md = root / name
    try:
        if pointer_md.samefile(root / "AGENTS.md"):
            return ""
        new = pointer_md.with_name(name + ".new")
        new.unlink(missing_ok=True)
        os.link(root / "AGENTS.md", new)
        os.replace(new, pointer_md)
        return f"  kept      {name} — it's a link to AGENTS.md, and now to the new one"
    except OSError:
        # Can't link again: the pointer does the same job.
        pointer_md.write_text("@AGENTS.md\n", encoding="utf-8")
        return f"  fixed     {name} now points at AGENTS.md (it was a link to the old one)"


def hook_script(command: str) -> str:
    """The script a hook command runs, however it is quoted."""
    hit = re.search(r"\.claude/hooks/([^/\"'\s]+)", command)
    return hit.group(1) if hit else Path(command.strip().strip("\"'")).name


def released_rules(settings: Path) -> list:
    """The permission rules in a settings file, as `kind:rule`."""
    perms = (read_theirs(settings) or {}).get("permissions")
    return sorted(f"{k}:{r}" for k, rules in (perms.items() if isinstance(perms, dict) else [])
                  if isinstance(rules, list) for r in rules if isinstance(r, str))


def merge_settings(target: Path, source: Path, given: dict | None = None,
                   rules_given: set | None = None) -> list[str] | None:
    """Template hooks added where missing; every hook of theirs kept. A hook
    this folder was given before and no longer has was taken out on purpose,
    and stays out. None, and nothing written, when theirs can't be read."""
    ours = json.loads(source.read_text())
    theirs = read_theirs(target)
    if theirs is None or not isinstance(theirs.get("hooks", {}), dict):
        return None
    added: list[str] = []
    hooks = theirs.setdefault("hooks", {})
    # A shipped hook still as an older release wrote it, plain and unquoted,
    # takes the new one: kept, it failed in any folder with a space in its path.
    newer = {hook_script(str(h.get("command", ""))): h for g in (ours.get("hooks") or {}).values()
             for grp in g if isinstance(grp, dict) for h in grp.get("hooks") or [] if isinstance(h, dict)}
    for groups in hooks.values():
        for grp in groups if isinstance(groups, list) else []:
            for n, h in enumerate(grp.get("hooks") or [] if isinstance(grp, dict) else []):
                script = hook_script(str(h.get("command", ""))) if isinstance(h, dict) else ""
                if script in SHIPPED_HOOKS and script in newer and h != newer[script] \
                        and str(h.get("command", "")).strip() == f"${{CLAUDE_PROJECT_DIR}}/.claude/hooks/{script}":
                    grp["hooks"][n] = dict(newer[script])
                    added.append(f"the new way of running {script}")
    # New permission rules are added. Theirs stay, and so does taking out one
    # this folder was given before: `rules_given` is every rule released to it.
    for kind, rules in ((ours.get("permissions") or {}).items()
                        if isinstance(ours.get("permissions"), dict) else []):
        have = theirs.get("permissions")
        if not isinstance(rules, list) or not isinstance(have, dict) \
                or not isinstance(have.get(kind, []), list):
            continue
        for rule in rules:
            if rule not in have.setdefault(kind, []) and f"{kind}:{rule}" not in (rules_given or set()):
                have[kind].append(rule)
                added.append(rule)
    for event, groups in (ours.get("hooks") or {}).items():
        have = {hook_script(str(h.get("command", ""))) for g in hooks.get(event) or []
                if isinstance(g, dict) for h in g.get("hooks") or [] if isinstance(h, dict)}
        for group in groups:
            for hook in group.get("hooks", []):
                # `bash "${CLAUDE_PROJECT_DIR}/.claude/hooks/settle.sh"`: the
                # quotes must not stop the name matching, or it is added again.
                script = hook_script(hook.get("command", ""))
                if not script or script in have:
                    continue
                if given and f".claude/hooks/{script}" in given:
                    continue
                hooks.setdefault(event, []).append(group)
                added.append(f"{event}: {script}")
                break
    for k, v in ours.items():
        if k != "hooks" and k not in theirs:
            theirs[k] = v
            added.append(k)
    target.write_text(json.dumps(theirs, indent=2, ensure_ascii=False) + "\n")
    return added


def _title_of(folder: Path) -> str:
    for name in ("README.md", "index.md"):
        spine = folder / name
        if spine.exists():
            try:
                meta, _ = engine.parse_frontmatter(spine.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            title = str(meta.get("title") or "").strip()
            if title:
                return title
    return ""


def strip_ids(root: Path, buckets: list[str], dry: bool) -> int:
    """Take `id:` out of every header, and the tag off the front of every name."""
    changed = 0
    for bucket in buckets:
        base = root / bucket
        if not base.exists():
            continue
        # deepest first, so a renamed parent does not invalidate a child's path
        for path in sorted(base.rglob("*"), key=lambda p: -len(p.parts)):
            if path.is_symlink():
                continue
            if path.is_file() and path.suffix.lower() in TEXT:
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if text.startswith("---\n") and re.search(r"^id:\s*\S", text, re.M):
                    head, _, rest = text[4:].partition("\n---")
                    head = re.sub(r"^id:.*\n?", "", head, flags=re.M)
                    if not dry:
                        path.write_text("---\n" + head + "\n---" + rest, encoding="utf-8")
                    changed += 1
            hit = TAG.match(path.name)
            if hit:
                rest = hit.group(2).strip()
                if rest.endswith(".card.md"):
                    inner = TAG.match(rest[: -len(".card.md")])
                    rest = (inner.group(2) if inner else rest[: -len(".card.md")]) + ".card.md"
                if path.is_dir():
                    # A folder is read as a name, so it is spelled like one:
                    # `W.04_ship-the-rewrite` becomes `Ship the Rewrite`, from
                    # the title in its README where there is one.
                    rest = engine.folder_name(_title_of(path), rest)
                target = path.with_name(rest)
                n = 2
                while target.exists() and target != path:
                    stem, suf = (rest, "") if path.is_dir() else (Path(rest).stem, Path(rest).suffix)
                    target = path.with_name(f"{stem}-{n}{suf}")
                    n += 1
                if target != path:
                    if not dry:
                        path.rename(target)
                    changed += 1
    return changed


def loose_in(root: Path) -> int:
    """Files dropped in by hand and not filed yet, counted the engine's way."""
    try:
        folder = engine.Zenith(root)
        return sum(1 for i in engine.Scanner(folder).scan() if engine.Sorter.unmanaged(i))
    except (Exception, SystemExit):
        return 0


def change_note(argv: list[str]) -> int:
    """Template maintainers: print the entry the next release goes out with,
    or stop when there isn't a new one. release-os.sh asks this before it
    builds anything, so no release goes out without saying what changed.

        python3 .os/upgrade.py --change-note [--published=<its CHANGES.md>]

    The new entry is the one at the top of .os/CHANGES.md headed
    `## Next release`. `--published` is the change note of the version out
    now: an entry that is already in it is not new, whatever its heading."""
    path = HERE / engine.CHANGES_FILE
    head = f"## {engine.UNRELEASED}"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        die(f"there's no .os/{engine.CHANGES_FILE}. Make one, with a '{head}' entry at the top "
            "saying in plain words what people will notice.")
    entries = engine.change_entries(text)
    if not entries or entries[0][0] != engine.UNRELEASED:
        die(f"there's no new entry in .os/{engine.CHANGES_FILE}. Add one at the top, headed "
            f"'{head}', saying in plain words what people will notice.")
    body = entries[0][1]
    if not re.search(r"^\s*- +\S", body, re.M):
        die(f"the '{head}' entry in .os/{engine.CHANGES_FILE} has nothing in it. "
            "Write a line for each thing people will notice, starting with '- '.")
    published = next((a.split("=", 1)[1] for a in argv if a.startswith("--published=")), "")
    if published and Path(published).is_file():
        out = engine.change_entries(Path(published).read_text(encoding="utf-8", errors="replace"))
        same = next((h for h, b in out if b == body), "")
        if same:
            die(f"the '{head}' entry is already out, as {same}. Change its heading to "
                f"'## {same}', and write a new one above it.")
    print(body)
    return 0


def main(argv: list[str]) -> int:
    if "--record" in argv:
        return record()
    if "--change-note" in argv:
        return change_note(argv)
    dry = "--dry-run" in argv
    anyway = "--anyway" in argv
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        die("which folder?   python3 .os/upgrade.py /path/to/your/folder")
    root = Path(args[0]).expanduser().resolve()
    if root == TEMPLATE:
        die("that is the template itself — point this at the folder you work in")
    if not (root / ".os" / "config.json").exists():
        die(f"{root} is not a Zenith folder (no .os/config.json)")
    try:
        old_cfg = json.loads((root / ".os" / "config.json").read_text())
        new_cfg = json.loads((TEMPLATE / ".os" / "config.json").read_text())
    except (OSError, ValueError) as exc:
        die(f"could not read a config.json: {exc}")
    buckets = list((old_cfg.get("buckets") or new_cfg.get("buckets") or {}).keys())

    # What this folder was given before, read before anything here changes:
    # its own list, and every version the template has ever released.
    before = read_shipped(root / ".os" / "shipped.json")
    given = before.get("files") if isinstance(before.get("files"), dict) else {}
    own_before = set(before.get("theirs") or [])
    known = shipped_hashes(root)
    # A CLAUDE.md or GEMINI.md made with `ln` and no -s is AGENTS.md under a
    # second name, but only until AGENTS.md is swapped for the new one. Then it
    # held the old rules, got "@AGENTS.md" put on top, and Claude Code read the
    # new rules and a stale copy of the old ones. So it's noted now, before
    # AGENTS.md changes, and linked to the new one at the end.
    hard_linked = set()
    for name in ("CLAUDE.md", "GEMINI.md"):
        try:
            if not (root / name).is_symlink() and (root / name).samefile(root / "AGENTS.md"):
                hard_linked.add(name)
        except OSError:
            pass

    # The program here was changed and never published: replacing it would
    # lose that, so it takes a deliberate --anyway.
    again = next((a.split("=", 1)[1] for a in argv if a.startswith("--again=")), "") \
        or f"python3 {shlex.quote(str(HERE / 'upgrade.py'))} {shlex.quote(str(root))}"
    changed_here = [rel for rel in PROGRAM
                    if (root / rel).is_file() and (TEMPLATE / rel).is_file()
                    and sha1(root / rel) != sha1(TEMPLATE / rel)
                    and known.get(rel) and sha1(root / rel) not in known[rel]]
    if changed_here and not anyway and not dry:
        die("this folder's own copy of " + ", ".join(changed_here) + " was changed here "
            "and never published.\n"
            "     An update would replace it. A copy goes into .os/backups/ either way.\n"
            f"     {again} --anyway   to go ahead\n"
            f"     {again} --dry-run  to see what would change")

    say()
    say(f"  UPGRADE  {root}")
    say(f"  {'a preview — nothing will change' if dry else 'from ' + str(TEMPLATE)}")
    say("  ──────────")
    if changed_here and dry and not anyway:
        say(f"  ▲ this folder's own {', '.join(changed_here)} was changed here and never published: "
            "the real run stops there unless told --anyway")

    # A rerun in the same second must not write over the first run's backup:
    # after an update that stopped partway, that one holds the originals.
    stamp = base = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    n = 1
    while (root / ".os" / "backups" / f"before-upgrade-{stamp}").exists() \
            or (root / ".os" / "upgrades" / stamp).exists():
        n += 1
        stamp = f"{base}-{n}"
    backup = root / ".os" / "backups" / f"before-upgrade-{stamp}"
    to_back = MACHINERY + GUARDED + [".os/shipped.json", ".claude/skills", ".claude/agents", ".claude/hooks",
                                     SETTINGS, ".os/config.json", ".os/words.json", "CLAUDE.md", "GEMINI.md",
                                     ".gitignore"]
    if not dry:
        for rel in to_back:
            src = root / rel
            if src.exists():
                copy(src, backup / rel)
        say(f"  ✓ backed up what gets touched → {backup.relative_to(root)}")

    # 1. machinery
    for rel in MACHINERY:
        src = TEMPLATE / rel
        if not src.exists():
            continue
        if not dry:
            copy(src, root / rel)
            if rel == "os":
                (root / rel).chmod(0o755)
        say(f"  {'would replace' if dry else 'replaced'}  {rel}")

    # 2. shipped files a person may have edited: replaced only if still as
    #    released. Anything else is theirs now; the new version goes beside it.
    #    One given before and gone now was deleted on purpose, and stays out.
    aside = root / ".os" / "upgrades" / stamp
    kept: list[str] = []
    fresh: list[str] = []
    left_out: list[str] = []
    own_now: list[str] = []
    failed: list[str] = []
    set_aside: dict = {}        # rel -> where an earlier, unfinished run put it

    def changed(dst: Path, rel: str) -> bool:
        return sha1_unnamed(dst) not in known.get(rel, []) and sha1(dst) not in known.get(rel, [])

    def theirs_already(rel: str) -> bool:
        """A file of their own that shares a name with one the template ships
        now: never given to this folder, or given while theirs was already there."""
        dst = root / rel
        return bool(given) and dst.is_file() and changed(dst, rel) \
            and (rel in own_before or rel not in given)

    def guarded(rel: str, own: bool | None = None) -> None:
        src, dst = TEMPLATE / rel, root / rel
        if not src.is_file():
            return
        # AGENTS.md says the folder's own name; the new one keeps it.
        name = folder_name_in(dst) if rel == "AGENTS.md" else ""
        text = named(src.read_text(encoding="utf-8"), name) if name else None
        try:
            if dst.is_file() and ((text is not None and dst.read_text(encoding="utf-8", errors="replace") == text)
                                  or sha1(dst) == sha1(src)):
                return
            own = theirs_already(rel) if own is None else own
            if not dst.exists() and not own:
                # Deleted stays deleted — but not AGENTS.md, without which
                # nothing here works, and no one takes it out on purpose.
                if rel in given and rel not in GUARDED:
                    left_out.append(rel)
                    return
            elif own or changed(dst, rel):
                if sha1_unnamed(src) in given.get(rel, []):
                    return          # they have had this version already: nothing new to merge
                (own_now if own else kept).append(rel)
                # An update that stopped partway already set this one aside.
                new_words = text.encode("utf-8") if text is not None else src.read_bytes()
                waiting = next((w for w in sorted((root / ".os" / "upgrades").glob(f"*/{rel}"))
                                if w.is_file() and w.read_bytes() == new_words), None)
                if waiting is not None:
                    set_aside[rel] = waiting
                elif not dry:
                    copy(src, aside / rel, text)
                return
            fresh.append(rel)
            if not dry:
                copy(src, dst, text)
                if dst.suffix == ".sh" or rel == "os":
                    dst.chmod(0o755)
        except OSError as exc:
            failed.append(f"{rel} ({exc.strerror or exc})")

    for rel in GUARDED:
        guarded(rel)
    for name in SHIPPED_SKILLS:
        folder = TEMPLATE / ".claude" / "skills" / name
        spine = f".claude/skills/{name}/SKILL.md"
        if spine in given and not (root / spine).exists():
            left_out.append(f".claude/skills/{name}/")
            continue
        own = theirs_already(spine) or None
        for f in sorted(folder.rglob("*")) if folder.exists() else []:
            if f.is_file():
                guarded(str(f.relative_to(TEMPLATE)), own)
    for name in SHIPPED_AGENTS:
        guarded(f".claude/agents/{name}.md")
    for name in SHIPPED_HOOKS:
        guarded(f".claude/hooks/{name}")
    say(f"  {'would replace' if dry else 'replaced'}  {len(fresh)} shipped file(s) still as released "
        f"(the {len(SHIPPED_SKILLS)} skills, {len(SHIPPED_AGENTS)} helpers, {len(SHIPPED_HOOKS)} hooks, AGENTS.md); "
        "anything of your own is untouched")
    # A hook script is taken out by taking it out of settings.json. One still
    # run from there was deleted by accident, and is put back after step 3.
    hooks_gone = [rel for rel in left_out if rel.startswith(".claude/hooks/")]
    for rel in left_out:
        if rel not in hooks_gone or dry:
            say(f"  {'would leave out' if dry else 'left out'}  {rel} — you deleted it, so it stays deleted")

    def aside_at(rel: str) -> str:
        return str((set_aside.get(rel) or aside / rel).relative_to(root))
    for rel in kept:
        say(f"  kept      {rel} — you edited it; the new version is in {aside_at(rel)}" if not dry else
            f"  would keep {rel} — you edited it; the new version would be set aside")
    for rel in own_now:
        say(f"  kept      {rel} — yours has the same name as a new one from the template, which is in "
            f"{aside_at(rel)}. They are different things: don't merge them" if not dry else
            f"  would keep {rel} — yours has the same name as a new one from the template")
    if failed:
        for line in failed:
            say(f"  ✖ couldn't write {line}")
        # AGENTS.md may be the new one already, and a CLAUDE.md made with
        # `ln` still the old one. Run again, as it says to, it was no longer
        # the same file, so it got "@AGENTS.md" put on top of the old rules
        # (review, 2026-09-30). So it's linked to the new one now.
        for name in sorted(hard_linked):
            said = link_again(root, name)
            if said:
                say(said)
        sys.stdout.flush()
        print("  ✖ the update stopped partway. Put right what's above, then run it again to finish; "
              f"what it replaced is in {backup.relative_to(root)}", file=sys.stderr)
        return 1

    # 3. settings: .claude/settings.json and each subject's keywords are
    #    replaced while still as released. What they changed is merged.
    typo: list[str] = []

    def unreadable(rel: str) -> None:
        typo.append(rel)
        copy(TEMPLATE / rel, aside / rel)
        say(f"  kept      {rel} — it has a typo, so nothing was merged; the new one is beside it "
            f"in {aside.relative_to(root)}/{rel}")

    if not dry:
        added = merge_json(root / ".os" / "config.json", TEMPLATE / ".os" / "config.json",
                           skip=("owner", "review"))
        cfg = json.loads((root / ".os" / "config.json").read_text())
        # The line under the name in ./os help, still as a release wrote it,
        # takes the new one, as a keyword list does. "Any AI can use it" read
        # as if the ChatGPT app would do, and theirs always won, so no update
        # could ever take it back.
        line = new_cfg.get("tagline")
        renewed = cfg.get("tagline") in released_taglines(root) and bool(line) \
            and line != cfg.get("tagline")
        if renewed:
            cfg["tagline"] = line
        (root / ".os" / "config.json").write_text(json.dumps(
            {**cfg, "version": new_cfg.get("version", old_cfg.get("version"))},
            indent=2, ensure_ascii=False) + "\n")
        say("  merged    .os/config.json" + (f" — added {', '.join(added)}" if added else "")
            + ("; a new line under its name in ./os help" if renewed else ""))
        words = root / ".os" / "words.json"
        added = merge_json(words, TEMPLATE / ".os" / "words.json")
        if added is None:
            unreadable(".os/words.json")
        else:
            renewed = renew_keywords(words, TEMPLATE / ".os" / "words.json", shipped_hashes(root, "keywords"),
                                     shipped_hashes(root, "extensions"),
                                     {key: shipped_hashes(root, f"intent_{key}") for key in INTENT_LISTS})
            subjects = [name for name in renewed if not name.startswith("intent:")]
            say("  merged    .os/words.json — your words kept" + (f", added {', '.join(added[:6])}" if added else "")
                + (f"; new words for {', '.join(subjects)}" if subjects else "")
                + ("; better at telling a note from work" if len(subjects) < len(renewed) else ""))
        src, dst = TEMPLATE / SETTINGS, root / SETTINGS
        if not src.is_file() or (dst.is_file() and sha1(dst) == sha1(src)):
            pass
        elif not dst.exists():
            if SETTINGS in given:
                say(f"  left out  {SETTINGS} — you deleted it, so it stays deleted")
            else:
                copy(src, dst)
                say(f"  added     {SETTINGS}")
        elif sha1(dst) in known.get(SETTINGS, []):
            copy(src, dst)
            say(f"  replaced  {SETTINGS} — it was still as released")
        elif sha1(src) in given.get(SETTINGS, []):
            say(f"  kept      {SETTINGS} (yours; the template's has nothing new)")
        else:
            added = merge_settings(dst, src, given, set(before.get("rules") or []))
            if added is None:
                unreadable(SETTINGS)
            else:
                say(f"  merged    {SETTINGS}" + (f" — added {', '.join(added)}" if added else ""))
        runs = read_theirs(dst) if dst.is_file() else None
        runs = json.dumps((runs or {}).get("hooks") or {})
        for rel in hooks_gone:
            if f"hooks/{Path(rel).name}" in runs:
                copy(TEMPLATE / rel, root / rel)
                (root / rel).chmod(0o755)
                say(f"  put back  {rel} — .claude/settings.json still runs it; "
                    "to take a hook out, take it out of there")
            else:
                say(f"  left out  {rel} — you deleted it, so it stays deleted")
        # .gitignore: replaced while still as released; otherwise the lines
        # the template's has and theirs doesn't are added.
        ignore, theirs_ignore = TEMPLATE / ".gitignore", root / ".gitignore"
        if ignore.is_file() and theirs_ignore.is_file() and sha1(ignore) != sha1(theirs_ignore) \
                and sha1(theirs_ignore) in known.get(".gitignore", []):
            copy(ignore, theirs_ignore)
            say("  replaced  .gitignore — it was still as released")
        elif ignore.is_file() and theirs_ignore.is_file():
            have = set(theirs_ignore.read_text(encoding="utf-8", errors="replace").splitlines())
            new_lines = [ln for ln in ignore.read_text(encoding="utf-8").splitlines()
                         if ln.strip() and not ln.startswith("#") and ln not in have]
            if new_lines:
                text = theirs_ignore.read_text(encoding="utf-8", errors="replace")
                theirs_ignore.write_text(text + ("" if text.endswith("\n") or not text else "\n")
                                         + "\n# Added by ./os update\n" + "\n".join(new_lines) + "\n",
                                         encoding="utf-8")
                say(f"  merged    .gitignore — added {', '.join(new_lines[:4])}"
                    + (" …" if len(new_lines) > 4 else ""))
    else:
        say("  would merge  .os/config.json · .os/words.json · .claude/settings.json (yours win)")

    # CLAUDE.md and GEMINI.md, each only made to point at AGENTS.md. Out here,
    # not with the merges above, so a preview says it too: it named neither,
    # and the real run then added GEMINI.md.
    # A CLAUDE.md or GEMINI.md that is a link is theirs, and is left as
    # it is. Written through, "@AGENTS.md" landed on top of whatever the
    # link led to: on AGENTS.md itself, the usual one, which then brought
    # itself in and matched no release again, so every update after it
    # set the new rules aside for a merge by hand.
    # Gemini CLI reads GEMINI.md and not AGENTS.md, so a Gemini user got
    # none of the rules. Made to point at them the way CLAUDE.md is: one of
    # their own keeps its lines, under the pointer.
    for name, why in (("CLAUDE.md", ""), ("GEMINI.md", ", so Gemini CLI reads AGENTS.md too")):
        pointer_md = root / name
        if pointer_md.is_symlink():
            say(f"  {'would keep' if dry else 'kept     '} {name} — it's a link, so it's left as it is")
        elif name in hard_linked and dry:
            # The real run links it to the new AGENTS.md; the preview said
            # it would be left as it was (review, 2026-09-30).
            say(f"  would keep {name} — it's a link to AGENTS.md, so it would be linked to the new one")
        elif name in hard_linked:
            say(link_again(root, name) or f"  kept      {name} — it's a link, so it's left as it is")
        elif not pointer_md.exists():
            if not dry:
                if name == "CLAUDE.md" and (TEMPLATE / name).is_file():
                    copy(TEMPLATE / name, pointer_md)
                else:
                    pointer_md.write_text("@AGENTS.md\n", encoding="utf-8")
            say(f"  {'would add ' if dry else 'added    '} {name}{why}")
        elif "AGENTS.md" not in pointer_md.read_text(encoding="utf-8", errors="replace")[:400]:
            if not dry:
                pointer_md.write_text("@AGENTS.md\n\n" + pointer_md.read_text(encoding="utf-8", errors="replace"),
                                      encoding="utf-8")
            say(f"  would point {name} at AGENTS.md; your rules below it would be kept" if dry else
                f"  fixed     {name} now points at AGENTS.md; your rules below it are kept")
        elif name == "CLAUDE.md":
            say(f"  {'would keep' if dry else 'kept     '} CLAUDE.md (yours)")

    # 4. numbers out — only a folder from before releases can still have them.
    #    Anywhere else, a name like `1.10 meeting with Sam.md` is theirs.
    n = 0
    if not before.get("release"):
        n = strip_ids(root, buckets, dry)
        say(f"  {'would take' if dry else 'took'} the W.xx / N.xx tags out of {n} name(s) and header(s)")

    # 5. the new engine names what step 4 renamed, and rebuilds the list.
    #    Anything else they dropped in by hand waits until they say so.
    if not dry:
        for cmd in ([["sort"]] if n else []) + [["index", "--quiet"]]:
            proc = subprocess.run([str(root / "os"), *cmd], cwd=str(root),
                                  capture_output=True, text=True)
            if proc.returncode not in (0, 1):
                say(f"  ▲ ./os {' '.join(cmd)} exited {proc.returncode}: {proc.stderr.strip()[:200]}")
        say("  ✓ ./os sort gave every renamed folder its plain name; INDEX.md rebuilt" if n
            else "  ✓ INDEX.md rebuilt")
        waiting = loose_in(root)
        if waiting:
            say(f"  {waiting} thing you dropped in by hand is waiting — ./os sort files it" if waiting == 1 else
                f"  {waiting} things you dropped in by hand are waiting — ./os sort files them")

    # 6. only now does the folder say it has this version, so an update that
    #    stopped before here is finished by running it again.
    if not dry and (TEMPLATE / ".os" / "shipped.json").is_file():
        data = read_shipped(TEMPLATE / ".os" / "shipped.json")
        data.pop("theirs", None)
        theirs = sorted(r for r in own_before | set(own_now) if (root / r).exists())
        if theirs:
            data["theirs"] = theirs
        new = root / ".os" / "shipped.json.new"
        new.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(new, root / ".os" / "shipped.json")
    say()
    # What changed, in words. The lines above say which files; this says
    # what they'll notice.
    news = whats_new(str(before.get("release") or ""))
    for line in news:
        say(line)
    if news:
        say()
    if (kept or typo) and not dry:
        say("  merge:  ask your AI to fold each set-aside file into yours, or diff them by hand")
    say("  next:  cd " + str(root) + "   ·   ./os check   ·   ./os")
    say("         happy with it? delete " + str(backup.relative_to(root)) if not dry else
        "         run it without --dry-run to do it")
    say()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
