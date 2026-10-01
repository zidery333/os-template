#!/usr/bin/env python3
"""
Zenith — test suite
========================
Builds a throwaway copy of the real folder in a temp directory, throws a
realistic mess at it, and checks that every promise the product makes holds.

    python3 .os/tests/run.py            run everything
    python3 .os/tests/run.py -v         show each assertion
    python3 .os/tests/run.py -k scale   run matching tests only
    python3 .os/tests/run.py --keep     leave the sandbox on disk for inspection
    python3 .os/tests/run.py -j 1       one test at a time (default: one per core)

Nothing here touches the folder it is run from.
"""

from __future__ import annotations

import ast
import json
import multiprocessing
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

# The suite imports the engine out of the template itself. Left alone that drops
# __pycache__/ into the folder being shipped, so every `./os test` dirties it.
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SOURCE / ".os"))

import fixtures  # noqa: E402


def pathlib_stem(name: str) -> str:
    return Path(name).stem
import engine  # noqa: E402
import learn  # noqa: E402

VERBOSE = False
G, R, Y, B, D, X = "\033[38;5;72m", "\033[38;5;167m", "\033[38;5;215m", "\033[1m", "\033[38;5;240m", "\033[0m"
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    G = R = Y = B = D = X = ""


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

class Failure(AssertionError):
    pass


class Case:
    def __init__(self, box: "Sandbox"):
        self.box = box
        self.checks = 0

    def ok(self, condition, message: str) -> None:
        self.checks += 1
        if not condition:
            raise Failure(message)
        if VERBOSE:
            print(f"      {G}·{X} {D}{message}{X}")

    def eq(self, got, want, message: str) -> None:
        self.ok(got == want, f"{message} (got {got!r}, want {want!r})")

    def gte(self, got, want, message: str) -> None:
        self.ok(got >= want, f"{message} (got {got!r}, need >= {want!r})")

    def lte(self, got, want, message: str) -> None:
        self.ok(got <= want, f"{message} (got {got!r}, need <= {want!r})")


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


class Sandbox:
    """A complete, isolated copy of the OS."""

    SKIP = {"Work", "Notes", "Archive",
            "backups", "cache", "transcripts", "__pycache__",
            "registry.json", "state.json", "INDEX.md", ".git"}

    def __init__(self, name: str = "zenith-test"):
        self.tmp = Path(tempfile.mkdtemp(prefix=name + "-"))
        self.root = self.tmp / "Zenith"
        self._build()

    def _build(self) -> None:
        def ignore(directory, names):
            drop = set()
            for n in names:
                if n in self.SKIP or n.endswith(".zip") or n.endswith(".tmp~"):
                    drop.add(n)
                # What an update set aside for them to merge is theirs, and a
                # test counting what waits to be merged found it.
                if n == "upgrades" and Path(directory).name == ".os":
                    drop.add(n)
            return drop

        shutil.copytree(SOURCE, self.root, ignore=ignore, symlinks=True)
        for bucket in ("Work", "Notes", "Archive"):
            (self.root / bucket).mkdir(parents=True, exist_ok=True)
        (self.root / ".os" / "backups").mkdir(parents=True, exist_ok=True)
        (self.root / ".os" / "cache").mkdir(parents=True, exist_ok=True)
        state = self.root / ".os" / "state.json"
        state.write_text(json.dumps(
            {"counters": {}, "undo": [], "history": [], "created": "test"}, indent=2))
        # Snags are the folder's own bug list, and `./os test` is offered to
        # whoever is using it — so the suite has to start with none of theirs.
        # Copied in, two snags they had already recorded made the test that
        # counts them fail, in their folder, about nothing they had done.
        (self.root / ".os" / "snags.json").write_text("[]\n")

    # -- driving the real CLI ------------------------------------------------

    def run(self, *args: str, expect: int | None = 0) -> subprocess.CompletedProcess:
        env = dict(os.environ, ZENITH_HOME=str(self.root), NO_COLOR="1")
        proc = subprocess.run(
            [str(self.root / "os"), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(self.root), env=env, timeout=180,
        )
        if expect is not None and proc.returncode != expect:
            raise Failure(
                f"`os {' '.join(args)}` exited {proc.returncode}, expected {expect}\n"
                f"--- stdout ---\n{proc.stdout[-2500:]}\n--- stderr ---\n{proc.stderr[-2500:]}"
            )
        return proc

    def json(self, *args: str, expect: int | None = 0) -> object:
        proc = self.run(*args, "--json", expect=expect)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise Failure(f"`os {' '.join(args)} --json` did not emit JSON: {exc}\n{proc.stdout[:800]}")

    # -- inspection ----------------------------------------------------------

    GENERATED_FILES = {"INDEX.md", "_index.md", "CATALOG.md"}

    def fill_inbox(self, n: int) -> list[tuple[str, str, str]]:
        """Drop n unfiled items straight into the folders, the way a person
        dragging a pile in from Finder would. Returns (marker, content, bucket).

        There is no inbox any more: `os save` files as it writes, and anything
        put in by hand is adopted where it lies by `os sort`. Everything lands
        in Notes here — deciding it is work is the sorter's job, not the
        fixture's, and that decision is what half these tests are checking."""
        items = fixtures.bulk(n)
        for marker, body, _ in items:
            name = marker.split("::", 1)[1] if "::" in marker else f"{marker.lower()}.md"
            # A file dropped in keeps its name, so a second pile can hold the
            # same names: kept beside it, the way Finder's Keep Both does.
            path, n = self.root / "Notes" / name, 2
            while path.exists():
                path, n = path.with_name(f"{Path(name).stem} {n}{Path(name).suffix}"), n + 1
            path.write_text(body, encoding="utf-8")
        return items

    def locate(self, marker: str) -> Path | None:
        """Where did the item carrying this marker end up?"""
        needle = marker.split("::", 1)[1] if "::" in marker else marker
        for p in self.root.rglob("*"):
            if not p.is_file() or p.is_symlink():
                continue
            if "::" in marker:
                if engine.slugify(pathlib_stem(needle)) in engine.slugify(p.name):
                    return p
                continue
            if p.suffix.lower() not in engine.TEXT_SUFFIXES:
                continue
            try:
                if needle in p.read_text(encoding="utf-8", errors="replace"):
                    return p
            except OSError:
                continue
        return None

    def name_clashes(self) -> list:
        """Things that would answer to one name inside one folder.

        There should never be any. A name is the only handle an item has, so
        two of them under one name in one place means one has been written
        over the other, or that neither can be reached."""
        seen, clashes = set(), []
        for item in self.items():
            if item["kind"] not in ("project", "note", "asset") or not item["path"]:
                continue
            key = (str(Path(item["path"]).parent), item["id"])
            if key in seen:
                clashes.append(key)
            seen.add(key)
        return clashes

    def carrying(self, phrase: str) -> dict:
        """The indexed item whose own words contain `phrase`.

        Nothing is numbered, and a test cannot say "the first project" and mean
        the one it just made — the folder it runs against already has projects
        of its own. So a test names the thing by something it actually said."""
        for item in self.items():
            if not item["path"]:
                continue
            path = self.root / item["path"]
            spine = None
            if path.is_dir():
                spine = next((path / n for n in ("README.md", "index.md", "SKILL.md")
                              if (path / n).exists()), None)
            elif path.suffix.lower() in engine.TEXT_SUFFIXES:
                spine = path
            if spine and phrase in spine.read_text(encoding="utf-8", errors="replace"):
                return item
        raise Failure(f"nothing in the index carries {phrase!r}")

    def bucket_of(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).parts[0]
        except (ValueError, IndexError):
            return ""

    def registry(self) -> dict:
        return json.loads((self.root / ".os" / "registry.json").read_text())

    def items(self) -> list[dict]:
        return self.registry()["items"]

    def tree(self) -> dict[str, str]:
        """path -> content hash, for real content only.

        Generated maps (INDEX.md, _index.md, CATALOG.md) are derived state and
        are rebuilt on every index, so they are excluded — undo restores where
        things live, not files the OS regenerates from what it finds."""
        out = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(self.root)
            if rel.parts and rel.parts[0] == ".os":
                continue
            if p.name in self.GENERATED_FILES:
                continue
            out[str(rel)] = engine.digest(p)
        return out

    def dirs(self) -> set:
        """Every directory holding real content. `tree()` hashes files only, so
        an empty folder left behind by a bad undo is invisible to it."""
        out = set()
        for p in self.root.rglob("*"):
            if not p.is_dir() or p.is_symlink():
                continue
            rel = p.relative_to(self.root)
            if rel.parts and rel.parts[0] in (".os", ".git"):
                continue
            out.add(str(rel))
        return out

    def inbox_count(self) -> int:
        """How many things are sitting in a folder that the OS has not filed."""
        os_ = engine.Zenith(self.root)
        return len([i for i in engine.Scanner(os_).scan()
                    if engine.Sorter.unmanaged(i)])

    def destroy(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# the tests
# ---------------------------------------------------------------------------



def named_for(item: dict) -> bool:
    """The name on disk is the title: kebab-case for a note file, Title Case
    With Spaces for a project folder. Both slug down to the same handle, which
    is what `./os show <name>` matches on."""
    slug = engine.slugify(item["title"], 44)
    got = engine.slugify(item["id"], 44)
    return got == slug or got.startswith(slug + "-")


def as_downloaded() -> bool:
    """Is the folder under test still as it was downloaded, never used?

    A few checks judge what ships: how long AGENTS.md is, which skills come
    with it, what the settings allow. In a folder someone lives in those are
    their own choices, and failing `./os test` over them read as though the
    last update had broken something. So they run in a copy nobody has opened
    yet, which is where every release is checked."""
    try:
        return bool(json.loads((SOURCE / ".os" / "state.json").read_text()).get("fresh"))
    except (OSError, ValueError):
        return False


def in_their_words(doc: Path, box: "Sandbox") -> bool:
    """Has the person rewritten this shipped file in their own words?

    A few checks read what AGENTS.md or a skill says. In a folder someone
    lives in, those are theirs to edit, and a check failing over their wording
    read as the last update having broken something. That folder runs a
    released program; the workshop's program is unreleased, so there every
    wording is checked."""
    import upgrade
    known = upgrade.shipped_hashes(SOURCE)
    rel = doc.relative_to(box.root).as_posix()
    released = upgrade.sha1(SOURCE / ".os" / "engine.py") in known.get(".os/engine.py", [])
    return released and upgrade.sha1_unnamed(SOURCE / rel) not in known.get(rel, [])


@test
def test_fresh_folder_is_healthy(t: Case) -> None:
    """A brand new Zenith passes its own doctor."""
    # What ships has no Work/, Notes/ or Archive/: git cannot hold an empty folder.
    for bucket in ("Work", "Notes", "Archive"):
        shutil.rmtree(t.box.root / bucket)
    result = t.box.json("doctor")
    errors = [i for i in result["issues"] if i["level"] == "error"]
    t.eq(errors, [], "a fresh folder reports no errors")
    t.gte(result["score"], 90, "fresh health score")
    t.ok((t.box.root / "AGENTS.md").exists(), "AGENTS.md ships with the folder")
    claude_md = t.box.root / "CLAUDE.md"
    t.ok(claude_md.exists(), "CLAUDE.md sits alongside AGENTS.md, where Claude Code looks")
    t.ok("@AGENTS.md" in claude_md.read_text(),
         "it imports AGENTS.md rather than repeating the rules")
    t.lte(len(claude_md.read_text().split("\n")), 20,
          "and stays a pointer, not a second copy of the rules")
    if as_downloaded():
        t.lte(len((t.box.root / "AGENTS.md").read_text().split("\n")), 160,
              "AGENTS.md stays short enough to load every turn")
    # The shipped AGENTS.md sat one line under the cap, so two lines of the
    # person's own rules were a warning after every update. Blank lines are
    # not what an AI reads each turn; the cap counts lines with words in them.
    agents = t.box.root / "AGENTS.md"
    agents.write_text(agents.read_text() + "\n## Our own rules\n\n" + "".join(
        f"- Rule {n} of ours, about how we like things done here.\n\n" for n in range(6)))
    t.eq([i["message"] for i in t.box.json("doctor")["issues"] if i["code"] == "rules-bloat"], [],
         "a few rules of their own are room they have")


@test
def test_a_fresh_download_is_not_broken(t: Case) -> None:
    """The first `./os` in a download says hello, not that things need fixing.

    The download has no Work/, Notes/ or Archive/, and the check called each
    one missing an error: every stranger's first screen said "3 things need
    fixing", and after their first save the AI was told the folder was broken."""
    for bucket in ("Work", "Notes", "Archive"):
        shutil.rmtree(t.box.root / bucket)
    state = t.box.root / ".os" / "state.json"
    state.write_text(json.dumps({"fresh": True, "counters": {}, "undo": [], "history": []}))
    first = t.box.run().stdout
    t.ok("Welcome" in first, "the first ./os welcomes them")
    t.ok("fixing" not in first, "and says nothing needs fixing")
    t.box.run("check")
    t.box.run("save", "Remember to call the plumber about the leak")
    t.ok("broken" not in t.box.run("brief").stdout, "after a first save the AI hears nothing is broken")
    t.box.run("check", "--fix")
    t.ok(not (t.box.root / "Archive").exists(),
         "and nothing makes an empty folder before something goes in it")


@test
def test_shipped_extras_are_valid(t: Case) -> None:
    """Every skill and agent that ships is well-formed and safely named."""
    t.box.run("index")
    items = t.box.items()
    skills = [i for i in items if i["kind"] == "skill"]
    agents = [i for i in items if i["kind"] == "agent"]
    t.gte(len(skills), 5, "shipped skills")
    t.gte(len(agents), 3, "shipped helpers")
    # Ten, and one of them is the menu that lists the other nine. Counted from
    # the list that ships, not the folder: a skill someone made for themselves
    # is not the template growing.
    import upgrade
    t.lte(len(upgrade.SHIPPED_SKILLS), 10, "shipped skills stay few enough to remember")
    if as_downloaded():
        here = {Path(s["path"]).name for s in skills}
        t.eq(sorted(set(upgrade.SHIPPED_SKILLS) - here), [], "every skill that ships is in it")

    for skill in skills:
        path = t.box.root / skill["path"] / "SKILL.md"
        t.ok(path.exists(), f"{skill['path']} has a SKILL.md")
        meta, body = engine.parse_frontmatter(path.read_text())
        t.ok(bool(meta.get("description")), f"{skill['path']} declares a description")
        t.gte(len(str(meta["description"])), 40, f"{skill['path']} description is substantive")
        name = Path(skill["path"]).name
        t.ok(name not in engine.RESERVED_COMMANDS,
             f"skill '{name}' does not collide with a built-in command")
        t.ok(name == engine.slugify(name), f"skill '{name}' is lowercase-with-hyphens")
        t.ok(len(body.split("\n")) <= 120, f"skill '{name}' body stays under 120 lines")

    for agent in agents:
        meta, _ = engine.parse_frontmatter((t.box.root / agent["path"]).read_text())
        for field in ("name", "description"):
            t.ok(bool(meta.get(field)), f"{agent['path']} declares {field}")
        t.eq(engine.slugify(str(meta["name"])), Path(agent["path"]).stem,
             f"{agent['path']} name matches its filename")
        if meta.get("model"):
            t.ok(str(meta["model"]) in ("sonnet", "opus", "haiku", "fable", "inherit"),
                 f"{agent['path']} declares a real model")


@test
def test_a_handoff_gives_held_work_no_next_action(t: Case) -> None:
    """Held work has a standard and no next action; that is what holding
    means, and its template has no such section. /handoff wrote one into
    every item all the same, and @builder then went and did it."""
    skill = t.box.root / ".claude" / "skills" / "handoff" / "SKILL.md"
    if not skill.exists():
        return
    steps = skill.read_text()
    t.ok("Pushing: `## Next action`" in steps, "a next action is for pushed work")
    t.ok("Holding: no next action" in steps and "## Keeps coming back" in steps,
         "held work gets what keeps coming back instead")
    held = (t.box.root / ".os" / "templates" / "holding.md").read_text()
    t.ok("## Keeps coming back" in held and "## Next action" not in held,
         "which is the section held work actually has")


@test
def test_settings_and_hooks_are_wired(t: Case) -> None:
    """settings.json is valid, points at hooks that exist and behave."""
    settings = json.loads((t.box.root / ".claude" / "settings.json").read_text())
    events = settings["hooks"]
    for event in ("SessionStart", "PreToolUse", "PostToolUse", "Stop"):
        t.ok(event in events, f"{event} hook is configured")
    # What Claude learns about them goes into this folder, where they and any
    # other AI can read it — not into Claude Code's own memory, outside it.
    t.eq(settings.get("autoMemoryEnabled"), False, "Claude Code's own memory is off here")

    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(t.box.root))
    hooks = t.box.root / ".claude" / "hooks"
    commands = []
    for spec in events.values():
        for group in spec:
            for hook in group["hooks"]:
                commands.append(hook["command"])
                path = hooks / re.search(r"[\w.-]+\.sh", hook["command"]).group(0)
                t.ok(path.exists(), f"hook {path.name} exists")
                t.ok(os.access(path, os.X_OK), f"hook {path.name} is executable")

    # Claude Code hands each command to a shell. In a folder whose path has a
    # space — iCloud Drive's "Mobile Documents", "My Drive" — an unquoted path
    # split in two, and every hook failed at every start and after every edit.
    spaced = t.box.tmp / "My Work" / "Zenith"
    shutil.copytree(t.box.root, spaced, symlinks=True)
    for command in commands:
        proc = subprocess.run(["sh", "-c", command], input="{}", capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60,
                              env=dict(os.environ, CLAUDE_PROJECT_DIR=str(spaced), NO_COLOR="1"))
        t.eq(proc.returncode, 0, f"`{command}` runs from a path with a space\n{proc.stderr[-300:]}")
        if "session-start" in command:
            t.ok("additionalContext" in proc.stdout, "and the session hook still hands over the brief")

    payloads = {
        "session-start.sh": '{"hook_event_name":"SessionStart"}',
        "keep-the-record.sh": "{}",
        "mark-dirty.sh": "{}",
        "settle.sh": "{}",
    }
    for name, payload in payloads.items():
        proc = subprocess.run([str(hooks / name)], input=payload, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        t.eq(proc.returncode, 0, f"hook {name} exits 0")
        if proc.stdout.strip():
            json.loads(proc.stdout)
            t.ok(True, f"hook {name} emits valid JSON")

    # a hook must survive garbage without failing the session
    for name in payloads:
        proc = subprocess.run([str(hooks / name)], input="}{ not json", capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        t.eq(proc.returncode, 0, f"hook {name} survives malformed input")

    brief = json.loads(subprocess.run([str(hooks / "session-start.sh")], input="{}",
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", env=env).stdout)
    t.ok("additionalContext" in brief["hookSpecificOutput"],
         "the session hook hands the AI something to read")

    # When ./os can't run, the hook used to swallow it: the AI started knowing
    # nothing, and nothing said why.
    (t.box.root / ".os" / "config.json").write_text('{"name": "x",,}', encoding="utf-8")
    proc = subprocess.run([str(hooks / "session-start.sh")], input="{}", capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
    t.eq(proc.returncode, 0, "a folder ./os can't read still starts a session")
    told = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    t.ok("./os could not run" in told, f"and the AI is told so, in one line: {told}")


@test
def test_replacing_a_record_whole_asks_first(t: Case) -> None:
    """## Decisions and ## Log only ever grow, but that was only words.

    A Write that replaced a whole README went through with no question, and
    neither `./os check` nor `./os undo` could bring the lost lines back. Now a
    Write that would drop dated lines from them asks the person first, naming
    the item and how many lines. An Edit, the way lines get added, never asks."""
    root = t.box.root
    t.box.run("new", "work", "Garden Plan")
    t.box.run("decide", "garden-plan", "Raised beds, not ground planting — rules out digging the lawn")
    readme = root / "Work" / "Garden Plan" / "README.md"
    readme.write_text(readme.read_text().rstrip() + "\n- 2026-09-28 — measured the plot\n")
    record = readme.read_text()
    hook = root / ".claude" / "hooks" / "keep-the-record.sh"
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))

    def guard(tool: str, path: Path, content: str) -> str:
        call = {"tool_name": tool, "tool_input": {"file_path": str(path), "content": content}}
        proc = subprocess.run([str(hook)], input=json.dumps(call), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=30)
        t.eq(proc.returncode, 0, f"the guard never fails a {tool}")
        return proc.stdout.strip()

    head = record.split("\n# ", 1)[0]
    said = guard("Write", readme, head + "\n# Garden Plan\n\n## Next action\n- [ ] order timber\n")
    t.ok(said != "", "replacing the whole README gets a word in")
    out = json.loads(said)["hookSpecificOutput"]
    t.eq(out["hookEventName"], "PreToolUse", "as a PreToolUse answer")
    t.eq(out["permissionDecision"], "ask", "which asks the person rather than blocking")
    reason = out["permissionDecisionReason"]
    t.ok("Garden Plan" in reason, f"naming the item: {reason}")
    t.ok("1 decision and 1 log line" in reason, f"and how many lines would go: {reason}")
    t.ok("additionalContext" in out, "and tells the AI why, in case they say no")

    t.eq(guard("Write", readme, record + "- 2026-09-30 — bought the soil\n"), "",
         "a Write that keeps every dated line says nothing")
    typo = json.loads(guard("Write", readme, record.replace("measured the plot", "measured the plott")))
    t.ok("or changed" in typo["hookSpecificOutput"]["permissionDecisionReason"],
         "a line only changed isn't called gone")
    t.eq(guard("Edit", readme, ""), "", "an Edit is never asked about")
    t.eq(guard("Write", root / "Notes" / "brand-new.md", "# New\n"), "",
         "nor is making a new file")

    settings = json.loads((root / ".claude" / "settings.json").read_text())
    groups = [g for g in settings["hooks"].get("PreToolUse", [])
              if "keep-the-record.sh" in json.dumps(g)]
    t.eq([g.get("matcher") for g in groups], ["Write"], "it is wired to Write alone")

    # It ships, and an updated folder gets it too.
    lists = [re.search(r"^SHIPPED_HOOKS = \((.*?)\)",
                       (root / ".os" / "upgrade.py").read_text(), re.M).group(1)]
    # release-os.sh reads upgrade.py's list; one of its own would have to agree.
    # Found without its folder's name, which the release's own scan stops on.
    release = next(iter(sorted((SOURCE / "Work").glob("*/release-os.sh"))), None)
    own = re.search(r'^HOOKS="(.*)"', release.read_text(), re.M) if release else None
    if own:
        lists.append(own.group(1))
    for listed in lists:
        t.ok("keep-the-record.sh" in listed, f"it is on the list of hooks that ship: {listed}")
    import upgrade
    older = t.box.tmp / "older-settings.json"
    before = dict(settings, hooks={k: v for k, v in settings["hooks"].items() if k != "PreToolUse"})
    before.pop("autoMemoryEnabled", None)
    older.write_text(json.dumps(before))
    upgrade.merge_settings(older, root / ".claude" / "settings.json")
    merged = json.loads(older.read_text())
    t.ok("keep-the-record.sh" in json.dumps(merged["hooks"].get("PreToolUse")),
         "an update adds it to an older folder's settings")
    t.eq(merged.get("autoMemoryEnabled"), False, "and turns Claude Code's own memory off there too")


@test
def test_save_files_in_one_step(t: Case) -> None:
    """`os save` never asks a question, and never leaves anything pending.

    There is no drop folder to leave it in: saving and filing are one step, so
    "I saved it" and "it is filed" cannot come apart."""
    result = t.box.json("save", "Fix the auth token refresh bug, it bites every Friday "
                                "and has to be sorted before the release")
    t.ok(result["filed"], "save filed it rather than parking it")
    t.eq(t.box.inbox_count(), 0, "nothing was left unfiled")
    t.ok(bool(result["id"]), "it came back with a name")
    t.ok((t.box.root / result["saved"]).exists(), "the reported path is real")

    # nothing is left lying in the staging area either
    stage = t.box.root / ".os" / "cache" / engine.STAGING
    t.eq([p.name for p in stage.iterdir()] if stage.exists() else [], [],
         "and the staging file it wrote through is gone")

    # --hold went with the inbox: there is nowhere to hold something any more,
    # and an option that no longer exists says so rather than being swallowed
    held = t.box.run("save", "--hold", "deal with this later", expect=2)
    t.ok("--hold" not in held.stdout, "--hold is not offered back to the user")
    t.ok("doesn't understand" in held.stderr, "it is refused in words")
    t.eq(t.box.inbox_count(), 0, "and nothing was half-saved on the way out")

    # undo takes the save back out of the folders without destroying the words
    before = len([i for i in t.box.items() if i["kind"] in ("project", "note")])
    t.box.run("undo")
    after = len([i for i in t.box.items() if i["kind"] in ("project", "note")])
    t.lte(after, before, "undo removes what the save filed")


@test
def test_a_link_can_be_saved_on_its_own(t: Case) -> None:
    """A pasted link was refused as a missing file, and a link with words after
    it was named `https-www-bbc-co-uk-news-articles-c4g0000-in`."""
    bare = t.box.json("save", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    t.ok(bare["filed"], "a link on its own is saved")
    t.ok(not bare["id"].startswith("http"), f"under a name, not the link spelled out ({bare['id']})")
    worded = t.box.json("save", "https://www.bbc.co.uk/news/articles/c4g0000 interesting piece on housing")
    t.eq(worded["title"], "interesting piece on housing", "words beside a link name it")
    t.box.run("save", "report.pdf", expect=1)           # a mistyped file is still refused


@test
def test_nothing_written_down_goes_unnoticed(t: Case) -> None:
    """A capture that never reached a folder has to be visible and has to stay.

    `os save` stages and files in one breath, so anything left in the staging
    area is a run that died in between. It used to be invisible — `os` said
    "nothing started yet", `check` said "all good" — and then deleted on a
    seven-day timer, which is the one thing this folder promises never to do."""
    stage = t.box.root / ".os" / "cache" / engine.STAGING
    stage.mkdir(parents=True, exist_ok=True)
    orphan = stage / "20200101-000000-ring-the-accountant.md"
    orphan.write_text("---\nsaved: 2020-01-01T00:00:00\n---\n\n"
                      "ring the accountant about the VAT thing\n", encoding="utf-8")
    old = time.time() - (engine.STALE_STAGE_DAYS + 30) * 86_400
    os.utime(orphan, (old, old))

    seen = t.box.run("status")
    t.ok("never got filed" in seen.stdout, "the first screen says it is there")
    t.ok("./os sort" in seen.stdout, "and what to type about it")

    health = t.box.json("check", expect=None)
    found = [i for i in health["issues"] if i["code"] == "unfiled-capture"]
    t.eq(len(found), 1, "check reports it as a finding of its own")
    t.eq(found[0]["level"], "error", "one left this long is an error, not a nag")
    t.ok("accountant" in found[0]["message"], "quoting the words, so it is recognisable")
    t.eq(found[0]["fix"], "./os sort", "with the command that rescues it")

    # nothing sweeps it, however old it is: another save must leave it alone
    t.box.run("save", "something else entirely")
    t.ok(orphan.exists(), "a later save does not delete what an earlier one left")

    t.box.run("sort")
    t.ok(not orphan.exists(), "and sort is what actually files it")
    filed = [i for i in t.box.items() if "accountant" in (i["title"] or "").lower()]
    t.eq(len(filed), 1, "landing as a real filed thing")
    t.ok(bool(filed[0]["id"]), "with a name of its own")

    after = t.box.json("check", expect=None)
    t.eq([i for i in after["issues"] if i["code"] == "unfiled-capture"], [],
         "and the finding clears once it is filed")


@test
def test_words_taken_back_are_not_nagged_about(t: Case) -> None:
    """Undoing a save is a choice, not a failure to file.

    The words go back to where `./os save` first wrote them, and sort leaves
    them there on purpose. But `./os` said "1 thing you saved never got filed"
    on every run after, and `./os sort` refused them and exited 1, so the only
    way to make it stop was to save them again: the opposite of what was asked."""
    t.box.run("save", "Ring the plumber about the boiler before the winter")
    t.box.run("undo")
    stage = t.box.root / ".os" / "cache" / engine.STAGING
    staged = [p for p in stage.iterdir() if "plumber" in p.read_text(encoding="utf-8")]
    t.eq(len(staged), 1, "the words are kept where save first wrote them")

    seen = t.box.run("status")
    t.ok("never got filed" not in seen.stdout, "the first screen does not nag about them")
    proc = t.box.run("sort")
    t.ok(staged[0].exists(), "sort leaves them where they are")
    t.ok("./os save" in proc.stdout, "and says how to file them after all")
    t.eq(t.box.run("sort", "--json").returncode, 0, "which is not a failure, as JSON either")

    health = t.box.json("check", expect=None)
    t.eq([i["level"] for i in health["issues"] if i["code"] == "taken-back-capture"],
         ["hint"], "check still lists them, as something to know rather than fix")


@test
def test_it_speaks_where_the_console_cannot(t: Case) -> None:
    """A console that cannot spell a box-drawing rule still gets an answer.

    A narrow locale — `LANG=C`, a stripped container, a cron job — encodes
    stdout as ASCII, and every screen this program prints starts with a rule.
    It died on its own heading: a traceback in place of the answer, before it
    had said anything at all."""
    narrow = dict(os.environ, ZENITH_HOME=str(t.box.root), PYTHONIOENCODING="cp1252")
    for args in (["status"], ["check"], ["save", "the boiler service is due in March"],
                 ["find", "boiler"], ["words"], ["nonsense-command"]):
        proc = subprocess.run(
            [str(t.box.root / "os"), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=str(t.box.root), env=narrow,
            timeout=180)
        spoke = proc.stdout + proc.stderr
        t.ok("UnicodeEncodeError" not in spoke,
             f"`os {' '.join(args)}` survives a cp1252 console")
        t.ok("Traceback" not in spoke, f"`os {' '.join(args)}` says no traceback")
        t.ok(spoke.strip() != "", f"`os {' '.join(args)}` still answers")


@test
def test_the_folder_records_what_is_wrong_with_itself(t: Case) -> None:
    """The person using a template is the only one who learns what is wrong
    with it, and they learn it mid-sentence while doing something else — which
    is exactly when nobody stops to write a bug report. So the AI writes it,
    in one command, and the repeats are counted: the same snag six times is a
    different job from one seen once, and that count is the one piece of
    evidence whoever maintains the template cannot get any other way."""
    t.eq(t.box.json("snag")["snags"], [], "the suite starts with none of the folder's own")
    quiet = t.box.run("snag", "sort filed a photo as a project, not a note")
    t.ok("noted" in quiet.stdout, "it says one short line and gets out of the way")

    # the same complaint, said the way a person actually says it twice
    t.box.run("snag", "Sort  filed a photo as a PROJECT, not a note.")
    t.box.run("snag", "learn should remember the channels I already trust")
    listed = t.box.json("snag")["snags"]
    t.eq(len(listed), 2, "near-identical wording is one snag, not two")
    t.eq(listed[0]["times"], 2, "counted, because how often is the whole point")
    t.eq(listed[1]["times"], 1, "and a different complaint stays its own")
    t.ok(listed[0]["first"] and listed[0]["version"],
         "each carries the date and the engine it happened on")

    # it is about the machinery, so it never turns up in their own work
    t.box.run("index")
    t.eq([i for i in t.box.items() if "photo as a project" in (i["title"] or "")], [],
         "a snag is never filed as one of their notes")
    # nothing matches, which is the point — `find` exits 1 when it finds nothing
    hunted = t.box.run("find", "photo", expect=None)
    t.ok("photo as a project" not in hunted.stdout,
         "and searching their folder does not surface it")

    out = t.box.json("snag", "--export")
    page = t.box.root / out["wrote"]
    t.ok(page.exists(), "--export writes a page they can hand over")
    text = page.read_text()
    t.ok(text.index("photo as a project") < text.index("channels I already trust"),
         "most-repeated first, because that is the order to fix them in")
    t.ok("2x" in text, "with the count on the page, not just in the store")

    # cleared only once it has been written out, never before
    t.box.json("snag", "--export", "--clear")
    t.eq(t.box.json("snag")["snags"], [], "--clear empties the pile")
    t.ok(page.exists(), "and leaves the exported page behind as the record")

    # a store somebody has hand-edited into nonsense must not kill a command
    (t.box.root / ".os" / "snags.json").write_text("}{ not json", encoding="utf-8")
    t.box.run("snag", "and it still takes a new one")
    t.eq(len(t.box.json("snag")["snags"]), 1, "an unreadable store starts over, quietly")


@test
def test_a_snag_says_which_release_it_happened_on(t: Case) -> None:
    """The engine's number stays the same from one release to the next, so a
    snag list sent in could not say whether it came from before a fix or
    after it. The dated release does say."""
    subprocess.run([sys.executable, str(t.box.root / ".os" / "upgrade.py"), "--record",
                    "--release=2026-09-30.1"], capture_output=True, text=True, check=True)
    t.ok("release 2026-09-30.1" in t.box.run("--version").stdout, "--version names the release")
    t.box.run("snag", "save put my recipe in the wrong place")
    t.eq(t.box.json("snag")["snags"][0].get("release"), "2026-09-30.1", "each snag keeps its release")
    page = (t.box.root / t.box.json("snag", "--export")["wrote"]).read_text()
    t.ok(page.count("release 2026-09-30.1") >= 2, f"and the page that gets sent says it\n{page}")


@test
def test_every_command_the_docs_tell_you_to_type_is_real(t: Case) -> None:
    """A skill that tells somebody to type a command that does not exist.

    The skills and AGENTS.md are read by an AI that will do what they say, and
    a wrong flag in one of them is a bug in the folder as surely as a wrong line
    in the engine — it just fails in somebody's chat instead of in a test. Now
    it fails here. `os sort --dry` refuses since options are checked, so a stale
    flag in a skill is a dead end rather than a silent wrong turn; either way it
    should never have shipped."""
    root = t.box.root
    takes = {name: engine.FLAGS.get(fn.__name__, set()) or set()
             for name, fn in engine.COMMANDS.items() if name}
    everywhere = {"--json", "--no-color", "--plain", "--quiet", "-q",
                  "--root", "--version", "-V"}

    def typed(text: str):
        """Only what a person is being told to type: fences and code spans."""
        for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
            for line in block.split("\n"):
                yield line
        for span in re.findall(r"`([^`\n]+)`", text):
            yield span

    docs = (sorted((root / ".claude").rglob("*.md"))
            + sorted((root / ".os" / "templates").rglob("*.md"))
            + [root / "AGENTS.md", root / "CLAUDE.md"])
    t.gte(len(docs), 12, "there are docs to check")
    wrong = []
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for line in typed(text):
            # `./os              where things stand` is a table, not an argument
            head = re.split(r"\s{2,}", line.strip())[0]
            m = re.match(r"\.?/?os\s+([a-z][a-z-]*)\b(.*)", head)
            if not m:
                continue
            name, rest = m.group(1), m.group(2)
            if name not in takes:
                wrong.append(f"{doc.name}: `os {name}` is not a command")
                continue
            for flag in re.findall(r"(?<!\S)(--?[a-z][\w-]*)", rest):
                if flag not in takes[name] and flag not in everywhere:
                    wrong.append(f"{doc.name}: `os {name}` does not take {flag}")
        for named in re.findall(r"`([\w./-]+\.(?:md|py|json|sh))`", text):
            if named.startswith((".os/", ".claude/")) and not (root / named).exists():
                wrong.append(f"{doc.name}: names {named}, which is not there")
    t.eq(wrong, [], f"every command and file the docs name is real: {wrong[:4]}")


@test
def test_no_option_is_silently_ignored(t: Case) -> None:
    """An option the code does not know stops the run instead of being dropped.

    `os sort --dry` is somebody asking for a preview. Silently ignoring the
    misspelling and doing the real thing is the exact mistake a dry-run flag
    exists to prevent, and it is worse for being invisible."""
    refused = t.box.run("sort", "--dry", expect=2)
    t.ok("--dry" in refused.stderr, "it names the option it did not understand")
    t.ok("--dry-run" in refused.stderr, "and suggests the one that was meant")
    t.eq(refused.stdout, "", "nothing is reported as though it had run")

    none_taken = t.box.run("backup", "--oops", expect=2)
    t.ok("no options" in none_taken.stderr, "a command with none says so plainly")

    # words of theirs are not options, whatever they start with
    t.box.run("save", "-3 degrees and the heating is off")
    t.ok(any("heating" in (i["title"] or "").lower() for i in t.box.items()),
         "a thought starting with a dash is still a thought")
    t.box.run("save", "--", "--sorting out the garage")
    t.ok(any("garage" in (i["title"] or "").lower() for i in t.box.items()),
         "and a bare -- keeps anything at all")

    # the table has to stay in step with the code, or it starts refusing
    # options that work and waving through ones that do not
    source = (t.box.root / ".os" / "engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name, declared in engine.FLAGS.items():
        if declared is None:
            continue
        node = funcs.get(name)
        t.ok(node is not None, f"{name} in the option table is a real command")
        used = set()
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            which = getattr(call.func, "id", "")
            if which not in ("_flag", "_opt", "_count"):
                continue
            for arg in (call.args[1:] if which == "_flag" else call.args[1:2]):
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    used.add(arg.value)
        t.eq(sorted(declared), sorted(used),
             f"{name} declares exactly the options it reads")
    handlers = {h.__name__ for h in engine.COMMANDS.values()}
    t.eq(sorted(handlers - set(engine.FLAGS)), [], "every command is in the table")


@test
def test_a_command_word_does_what_it_says(t: Case) -> None:
    """`./os file invoice.pdf` said everything was filed and brought nothing
    in, and `./os fix` only looked, then said to run check --fix. Both words
    were already commands (review, 2026-09-29)."""
    outside = t.box.tmp / "plumber-invoice.pdf"
    outside.write_bytes(b"%PDF-1.4 the plumber's invoice\n")
    t.box.run("file", str(outside))
    t.ok(any(p.name == outside.name for p in (t.box.root / "Notes").rglob("*")),
         "file brings in the file it is given")
    t.ok(outside.exists(), "and leaves the original where it was")
    t.ok("nothing waiting" in t.box.run("file").stdout, "on its own, it files what was dropped in")
    dropped = t.box.root / "Notes" / "Budget 2026.csv"
    dropped.write_text("month,total\nJan,1\n")
    t.box.run("file", str(dropped))
    t.eq(t.box.inbox_count(), 0, "given a file dropped in by hand, it files it, as it did before")

    (t.box.root / "Notes" / ".DS_Store").write_text("")
    t.box.run("fix", expect=None)
    t.ok(not (t.box.root / "Notes" / ".DS_Store").exists(), "fix fixes, as check --fix does")


@test
def test_new_creates_every_kind(t: Case) -> None:
    """Each blueprint produces a valid, identified, indexed item."""
    made = {
        "project": t.box.run("new", "project", "Ship the redesign", "--domain", "design"),
        "ongoing": t.box.run("new", "ongoing", "Codebase health", "--domain", "engineering"),
        "note": t.box.run("new", "note", "Postgres index types", "--domain", "engineering"),
        "skill": t.box.run("new", "skill", "Weekly Report"),
        "agent": t.box.run("new", "agent", "Proofreader"),
    }
    t.eq(len(made), 5, "five kinds created")
    t.box.run("index")
    items = t.box.items()
    kinds = {i["kind"] for i in items}
    for kind in ("project", "note", "skill", "agent"):
        t.ok(kind in kinds, f"a {kind} exists after creation")
    # There is no journal kind any more: what happened goes in the item it
    # happened to. Asking for one has to be refused, not quietly filed.
    t.box.run("new", "log", "Kickoff meeting", expect=1)
    phases = {i["title"]: i.get("status") for i in items}
    t.eq(phases.get("Ship the redesign"), "pushing", "`new work` starts it being pushed")
    t.eq(phases.get("Codebase health"), "holding", "`new ongoing` starts it being held")

    # Nothing is numbered: the handle is the name on disk, and it is the title
    # slugified. That is the whole promise `./os show <name>` rests on.
    fresh = {"Ship the redesign", "Codebase health", "Postgres index types"}
    for item in items:
        if item["title"] in fresh:
            t.ok(bool(item["id"]), f"{item['title']} has a name on disk")
            t.ok(named_for(item),
                 f"{item['title']} is named after its title (got {item['id']!r})")
            t.ok(item["id"] in item["path"],
                 f"{item['title']} is found on disk under that name")

    # a reserved name must be refused, not silently accepted
    proc = t.box.run("new", "skill", "doctor", expect=1)
    t.ok("built-in" in (proc.stderr + proc.stdout), "reserved skill names are refused")


@test
def test_sorts_one_hundred_and_thirty_items(t: Case) -> None:
    """The headline promise: throw 130 mixed things in, get a sorted folder."""
    planted = t.box.fill_inbox(130)
    t.eq(t.box.inbox_count(), 130, "130 items planted in the inbox")

    started = time.time()
    t.box.run("sort")
    elapsed = time.time() - started

    t.eq(t.box.inbox_count(), 0, "the inbox is empty afterwards")
    t.ok(elapsed < 120, f"sorting 130 items took {elapsed:.1f}s")

    items = t.box.items()
    filed = [i for i in items if i["bucket"] != engine.TOOLKIT]
    t.gte(len(filed), 130, "every planted item is in the index")

    # no data loss
    t.gte(len(list((t.box.root).rglob("*.md"))), 130, "no markdown was lost")

    # categories appeared
    categorised = [i for i in filed if i["trail"]]
    t.gte(len(categorised), 60, "most items ended up inside a category")

    # nothing is buried
    for item in filed:
        t.ok(len(item["trail"]) <= 2, f"{item['path']} is at most 2 levels below its bucket")

    # every category is a real, marked category. Item folders and grouping
    # folders read the same from the outside now that nothing is tagged, so
    # the spine is what tells them apart: an item carries one, a category does not.
    item_paths = {i["path"] for i in filed}
    for bucket in ("Work", "Notes"):
        base = t.box.root / bucket
        for child in base.iterdir():
            if not child.is_dir() or engine.ignored(child):
                continue
            if str(child.relative_to(t.box.root)) in item_paths:
                continue
            t.ok((child / ".category").exists(),
                 f"{bucket}/{child.name} is a marked category, not a stray folder")

    # every planted item is traceable, and lands where its label says
    hits, lost, wrong = 0, [], []
    for marker, _, expected in planted:
        where = t.box.locate(marker)
        if where is None:
            lost.append(marker)
            continue
        if t.box.bucket_of(where) == expected:
            hits += 1
        else:
            wrong.append((marker, expected, t.box.bucket_of(where)))
    t.eq(lost, [], f"every planted item is still findable ({len(lost)} lost)")
    accuracy = hits / len(planted)
    t.gte(round(accuracy, 3), 0.80,
          f"classification accuracy {accuracy:.0%} against labelled fixtures; "
          f"misplaced: {wrong[:6]}")


@test
def test_names_are_earned_and_permanent(t: Case) -> None:
    """A thing keeps the name it was filed under, sort after sort.

    The name on disk is the only handle there is, so two things must never end
    up in one folder under one name, and a second sort must never rename
    something that was already right."""
    def names() -> set:
        return {i["id"] for i in t.box.items()
                if i["kind"] in ("project", "note", "asset") and i["id"]}

    t.box.fill_inbox(60)
    t.box.run("sort")
    first = names()
    t.eq(t.box.inbox_count(), 0, "everything planted came out with a name")
    t.eq(t.box.name_clashes(), [], "no two things share one name in one folder")

    # the name is the title, slugified — that is the whole addressing scheme
    for i in t.box.items():
        if i["kind"] in ("project", "note") and i["title"]:
            t.ok(named_for(i),
                 f"{i['title']!r} is named for its title (got {i['id']!r})")

    t.box.fill_inbox(40)
    t.box.run("sort")
    t.eq(t.box.name_clashes(), [], "still no clash after a second batch")
    t.eq(sorted(first - names()), [],
         "and the second sort renamed nothing that was already filed")

    # and none of it depends on the state file
    (t.box.root / ".os" / "state.json").write_text(
        json.dumps({"counters": {}, "undo": [], "history": []}))
    t.box.fill_inbox(10)
    t.box.run("sort")
    t.eq(t.box.name_clashes(), [], "no clash after state.json is destroyed")
    t.eq(sorted(first - names()), [],
         "and every earlier name survived state.json being destroyed")


@test
def test_sorting_is_idempotent(t: Case) -> None:
    """Running sort twice changes nothing the second time."""
    t.box.fill_inbox(80)
    t.box.run("sort")
    before = t.box.tree()
    result = t.box.json("sort")
    t.eq(result["moves"], [], "a second sort moves nothing")
    after = t.box.tree()
    t.eq(set(after) - set(before), set(), "no files appeared")
    t.eq(set(before) - set(after), set(), "no files vanished")


@test
def test_categories_appear_and_collapse(t: Case) -> None:
    """Structure is earned, not imposed — and it is given back."""
    for i in range(6):
        (t.box.root / "Notes" / f"note-{i}.md").write_text(
            f"# Postgres index note {i}\n\nReference cheat sheet on database schema and indexes.\n")
    t.box.run("sort")
    flat = [i for i in t.box.items() if i["bucket"] == "Notes"]
    t.ok(all(not i["trail"] for i in flat), "6 items stay flat — no premature folders")

    items = fixtures.bulk(60)
    for name, body, expected in items:
        if expected == "Notes":
            (t.box.root / "Notes" / name).write_text(body)
    t.box.run("sort")
    library = [i for i in t.box.items() if i["bucket"] == "Notes"]
    t.gte(len(library), 13, "the library is now crowded")
    t.ok(any(i["trail"] for i in library), "categories appeared once it was crowded")

    # take almost everything away again
    keep = 3
    survivors = sorted(library, key=lambda i: i["id"])[:keep]
    keep_paths = {i["path"] for i in survivors}
    for item in library:
        if item["path"] not in keep_paths:
            target = t.box.root / item["path"]
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
    t.box.run("sort")
    library = [i for i in t.box.items() if i["bucket"] == "Notes"]
    t.ok(all(not i["trail"] for i in library),
         "categories collapsed back once they were no longer needed")
    leftovers = [p for p in (t.box.root / "Notes").iterdir()
                 if p.is_dir() and (p / ".category").exists()]
    t.eq(leftovers, [], "empty category folders were removed")


@test
def test_undo_restores_the_tree_exactly(t: Case) -> None:
    """Every sort is reversible, byte for byte."""
    t.box.fill_inbox(45)
    before = t.box.tree()
    t.box.run("sort")
    mid = t.box.tree()
    t.ok(mid != before, "the sort actually changed the tree")

    t.box.run("undo")
    after = t.box.tree()

    lost = {p for p in before if p not in after}
    t.eq(lost, set(), "undo lost no files")
    for path, checksum in before.items():
        t.eq(after.get(path), checksum, f"{path} came back unchanged")

    t.eq(t.box.inbox_count(), 45, "all 45 items are unfiled again")


@test
def test_undo_leaves_nothing_behind(t: Case) -> None:
    """Undo removes what the run created, not only what it moved."""
    inbox = t.box.root / "Notes"
    (inbox / "numbers.csv").write_text("date,revenue\n2026-01-01,12000\n")
    (inbox / "diagram.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (inbox / "ship-it.md").write_text(
        "# Ship the rewrite\n\nDeadline Friday. Migrate the service.\n\n- [ ] cut over\n")
    before = t.box.tree()

    t.box.run("sort")
    cards = list(t.box.root.rglob("*.card.md"))
    t.gte(len(cards), 2, "asset cards were generated")

    t.box.run("undo")
    after = t.box.tree()

    t.eq(list(t.box.root.rglob("*.card.md")), [], "no orphan asset cards survived the undo")
    t.eq(set(after) - set(before), set(), "undo left no file behind")
    t.eq(set(before) - set(after), set(), "undo lost no file")
    t.eq(t.box.inbox_count(), 3, "all three items are back in the inbox")

    # The machine's own record lives in .os/state.json, where it cannot bury
    # the one thing a person would go back for.
    history = json.loads((t.box.root / ".os" / "state.json").read_text())["history"]
    t.gte(len(history), 1, "but every operation is still recorded, in state.json")
    t.ok(any("sort" in h["label"] for h in history), "including the sort")


@test
def test_undo_walks_back_several_runs(t: Case) -> None:
    """The undo stack is a stack, not a single slot."""
    t.box.fill_inbox(12)
    t.box.run("sort")
    first = t.box.inbox_count()
    t.box.fill_inbox(8)
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "inbox clear after two sorts")
    t.box.run("undo")
    t.eq(t.box.inbox_count(), 8, "one undo returns the second batch only")
    t.box.run("undo")
    t.eq(t.box.inbox_count(), 20, "a second undo returns the first batch too")


@test
def test_archive_and_restore_round_trip(t: Case) -> None:
    """Archiving is about attention, not deletion."""
    t.box.run("new", "project", "Retire this thing", "--domain", "engineering")
    t.box.run("index")
    project = next(i for i in t.box.items() if i["title"] == "Retire this thing")
    ident = project["id"]

    t.box.run("archive", ident)
    items = t.box.items()
    archived = next(i for i in items if i["id"] == ident)
    t.eq(archived["bucket"], "Archive", "the project moved to the archive")
    t.ok((t.box.root / archived["path"]).exists(), "the files are really there")

    hits = t.box.json("find", "Retire this thing")
    t.gte(len(hits), 1, "archived items are still findable")

    t.box.run("restore", ident)
    restored = next(i for i in t.box.items() if i["id"] == ident)
    t.eq(restored["bucket"], "Work", "restore puts it back where it came from")
    t.eq(restored["id"], ident, "and it is called what it was called")


@test
def test_undo_of_a_close_puts_the_header_back_too(t: Case) -> None:
    """Undoing a close has to give the item its own state back.

    `os close` writes `status: archived` into the header and *then* moves the
    folder. Snapshotting during the move caught the rewritten header, so undo
    put the item back in Work still calling itself archived — and status is
    load-bearing here, so the item silently stopped counting as on the go."""
    t.box.run("new", "work", "Undo me completely", "--domain", "engineering")
    t.box.run("index")
    item = next(i for i in t.box.items() if i["title"] == "Undo me completely")
    ident, spine = item["id"], t.box.root / item["path"] / "README.md"
    before = spine.read_text()

    t.box.run("close", ident)
    t.box.run("undo")
    back = next(i for i in t.box.items() if i["id"] == ident)
    t.eq(back["bucket"], "Work", "the folder came back out of the archive")
    t.eq(back["status"], "pushing", "and is being pushed again, not still archived")
    meta, _ = engine.parse_frontmatter((t.box.root / back["path"] / "README.md").read_text())
    t.ok("archived" not in meta, "no leftover archived: stamp")
    t.ok("origin" not in meta, "no leftover origin: stamp")
    t.eq((t.box.root / back["path"] / "README.md").read_text(), before,
         "the file says exactly what it said before the close")


@test
def test_a_flip_between_phases_can_be_taken_back(t: Case) -> None:
    """`os undo` takes back the *last* thing, including a hold or a push.

    Flipping a phase used to journal nothing at all, so undo reached past it
    and reversed whatever came before — the one behaviour undo must never have."""
    t.box.run("new", "work", "Flip me back and forth", "--domain", "engineering")
    t.box.run("index")
    ident = next(i["id"] for i in t.box.items() if i["title"] == "Flip me back and forth")

    t.box.run("hold", ident)
    held = next(i for i in t.box.items() if i["id"] == ident)
    t.eq(held["status"], "holding", "it is being held")

    proc = t.box.run("undo")
    t.ok(ident in proc.stdout, "undo names the flip it reversed")
    after = next(i for i in t.box.items() if i["id"] == ident)
    t.eq(after["status"], "pushing", "and the phase went back to what it was")
    t.ok((t.box.root / after["path"]).exists(), "nothing else moved")

    # The other way round: push is the flip AGENTS.md says gets used most.
    t.box.run("hold", ident)
    t.box.run("push", ident)
    pushed = next(i for i in t.box.items() if i["id"] == ident)
    t.eq(pushed["status"], "pushing", "push puts held work back on the go")
    t.box.run("undo")
    held = next(i for i in t.box.items() if i["id"] == ident)
    t.eq(held["status"], "holding", "and undo takes the push back too")


@test
def test_undo_never_throws_away_words_written_after_it(t: Case) -> None:
    """Undo put back what a file said before the step, and deleted what the
    step made, without looking at whether anybody had written in it since. A
    Next action added after `./os decide`, a line added to a saved note, a
    project started inside a new folder: all gone, and no copy anywhere. The
    step is still reversed; what was written since is kept, and undo says where."""
    root = t.box.root
    kept_in = root / ".os" / "cache" / "undo-kept"

    def kept(phrase: str) -> list:
        return [p for p in kept_in.rglob("*") if p.is_file()
                and phrase in p.read_text(encoding="utf-8", errors="replace")] \
            if kept_in.exists() else []

    def said(proc, copies: list) -> bool:
        return any(str(p.relative_to(root)) in proc.stdout for p in copies)

    # a decision, then a line written by hand, then the decision taken back
    t.box.run("new", "work", "Garden Shed")
    readme = root / "Work" / "Garden Shed" / "README.md"
    t.box.run("decide", "Garden Shed", "Timber, not metal - rules out the cheap kit")
    with readme.open("a", encoding="utf-8") as fh:
        fh.write("- Order the timber from Jewson by Friday\n")
    # It stops first and names the file: put back as it was, the lines written
    # since go too, and in a real folder that was five decisions.
    was = readme.read_text()
    stopped = t.box.run("undo", expect=1)
    t.ok("Work/Garden Shed/README.md" in stopped.stdout and "--anyway" in stopped.stdout,
         f"undo stops and says which file was written in since\n{stopped.stdout}")
    t.eq(readme.read_text(), was, "and changes nothing")
    proc = t.box.run("undo", "--anyway")
    t.ok("Timber, not metal" not in readme.read_text(), "told to, the decision is taken back")
    t.ok(kept("Jewson"), "the line written after it is kept")
    t.ok(said(proc, kept("Jewson")), f"and undo says where:\n{proc.stdout[-600:]}")

    # a save, then more written into the note, then the save taken back
    t.box.run("save", "Meeting notes with the architect about the extension")
    note = root / t.box.carrying("architect about the extension")["path"]
    with note.open("a", encoding="utf-8") as fh:
        fh.write("- She said the wall is load-bearing; it needs a steel beam\n")
    t.box.run("undo", expect=1)
    t.ok(note.exists(), "a note written in since isn't taken back without asking")
    proc = t.box.run("undo", "--anyway")
    t.ok(kept("steel beam"), "a line added to a saved note is kept")
    t.ok(said(proc, kept("steel beam")), "and undo says where")

    # a new folder, a project started inside it, then the new taken back
    t.box.run("new", "work", "Website Rebuild")
    site = root / "Work" / "Website Rebuild"
    (site / ".git").mkdir()
    (site / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (site / "CLAUDE.md").write_text("Use the house style.\n")
    with (site / "README.md").open("a", encoding="utf-8") as fh:
        fh.write("- Counted the old site: 41 pages\n")
    t.box.run("undo", "--anyway")
    t.ok((site / ".git" / "HEAD").exists(), "what was put in the folder since stays")
    t.ok((site / "CLAUDE.md").exists(), "CLAUDE.md included")
    t.ok(kept("41 pages"), "and what was written into its README is kept")

    # a file the step carried along but never changed: written in since, it
    # has nothing of the step's in it to take back, so it is left as it is
    t.box.run("new", "work", "Kitchen Plan")
    (root / "Work" / "Kitchen Plan" / "measurements.md").write_text("Wall: 3.2 m\n")
    t.box.run("rename", "Kitchen Plan", "Kitchen Refit")
    with (root / "Work" / "Kitchen Refit" / "measurements.md").open("a") as fh:
        fh.write("Window: 1.1 m\n")
    t.box.run("undo")
    t.eq((root / "Work" / "Kitchen Plan" / "measurements.md").read_text(),
         "Wall: 3.2 m\nWindow: 1.1 m\n", "a file the step only moved keeps what was written since")

    # nothing written since: nothing kept, and nothing said about it
    t.box.run("hold", "Garden Shed")
    before = len(kept(""))
    proc = t.box.run("undo")
    t.eq(len(kept("")), before, "a plain undo keeps no copies")
    t.ok("undo-kept" not in proc.stdout, "and says nothing about any")

    # Nothing written since, either, in a note past a megabyte that sort named,
    # or in work whose name only changed its capitals: undo said both had been
    # written in, and kept a copy of each.
    essay = root / "Notes" / "Untitled.md"      # named by the computer, so sort names it
    essay.write_text("# Long essay\n\n" + "A sentence about the sea and the harbour.\n" * 36_000)
    t.box.run("sort")
    t.ok(not essay.exists(), "sort gave the long note its name")
    proc = t.box.run("undo")
    t.ok(essay.exists() and "undo-kept" not in proc.stdout,
         f"undoing that says nothing was written in it since\n{proc.stdout[-400:]}")
    t.box.run("new", "work", "Tool Shed")
    t.box.run("rename", "Tool Shed", "TOOL Shed")
    proc = t.box.run("undo")
    t.ok("undo-kept" not in proc.stdout, f"nor for a name that only changed its capitals\n{proc.stdout[-400:]}")
    t.eq(len(kept("")), before, "and keeps no copies")

    # A step an older ./os wrote down has no fingerprints; the file's time says
    # it was written in since.
    t.box.run("decide", "Garden Shed", "Cedar cladding - rules out paint")
    state = root / ".os" / "state.json"
    data = json.loads(state.read_text())
    data["undo"][-1].pop("after", None)
    data["undo"][-1]["at"] = "2026-01-01T10:00:00"
    state.write_text(json.dumps(data))
    t.ok("Garden Shed" in t.box.run("undo", expect=1).stdout, "an older step stops too")
    t.box.run("undo", "--anyway")
    t.ok("Cedar cladding" not in readme.read_text(), "and goes when told")


@test
def test_two_runs_at_once_keep_each_others_undo(t: Case) -> None:
    """state.json was read when a command started and written back whole when
    it ended. A slower run overlapping a quicker one (a big save, or the index
    the settling hook runs after every turn) wrote its old copy over the
    other's, so the quicker one's undo step vanished and `./os undo` then
    reversed the wrong thing."""
    root = t.box.root
    t.box.run("new", "work", "Garden Plan")
    slow = engine.Zenith(root)                  # a run that started now
    t.box.run("hold", "Garden Plan")            # while another one finished

    def labels() -> list:
        state = json.loads((root / ".os" / "state.json").read_text())
        return [e["label"] for e in state["undo"]]

    engine.Indexer(slow).build()                # saves with no lock, as ./os index does
    t.ok(any("holding" in label for label in labels()),
         f"a rebuild keeps the other run's undo step: {labels()}")

    with engine.Lock(slow, "test"):
        slow.make_dir(root / "Notes" / "Scratch Pad")
        slow.commit("scratch")
    t.ok("holding" in labels()[-2] and labels()[-1] == "scratch",
         f"a commit adds its step after the other's, not over it: {labels()}")

    t.box.run("undo")
    t.ok(not (root / "Notes" / "Scratch Pad").exists(), "undo takes back the newest")
    t.box.run("undo")
    item = next(i for i in t.box.items() if i["title"] == "Garden Plan")
    t.eq(item["status"], "pushing", "and then the one before it")


@test
def test_a_sort_stopped_partway_can_still_be_undone(t: Case) -> None:
    """Ctrl-C during a sort said "./os undo still works", but the moves made
    before it were only written down when the run finished. Undo then reversed
    the command before, and the half-finished sort stayed where it was."""
    root = t.box.root
    t.box.fill_inbox(12)
    t.box.run("new", "work", "Something Done Before")
    before = t.box.tree()
    stopper = ("import sys\n"
               "sys.path.insert(0, '.os')\n"
               "import engine\n"
               "real = engine.Zenith.move\n"
               "done = []\n"
               "def move(self, src, dst):\n"
               "    if len(done) == 5:\n"
               "        raise KeyboardInterrupt\n"
               "    done.append(src)\n"
               "    return real(self, src, dst)\n"
               "engine.Zenith.move = move\n"
               "sys.exit(engine.main(['sort']))\n")
    env = dict(os.environ, ZENITH_HOME=str(root), NO_COLOR="1")
    proc = subprocess.run([sys.executable, "-c", stopper], cwd=str(root), env=env,
                          capture_output=True, text=True, timeout=180)
    t.eq(proc.returncode, 130, f"the sort was stopped:\n{proc.stdout[-400:]}{proc.stderr[-400:]}")
    t.ok(t.box.tree() != before, "after it had already moved some things")
    t.ok(not (root / ".os" / ".lock").exists(), "and it let go of the folder")

    t.box.run("undo")
    t.eq(t.box.tree(), before, "undo puts back what the stopped sort had done")
    t.ok((root / "Work" / "Something Done Before" / "README.md").exists(),
         "and not the command before it")


@test
def test_a_note_that_is_really_work_can_be_pushed(t: Case) -> None:
    """A to-do filed as a note ("Buy a birthday present for Sarah") had no way
    to become work: push refused a note, `new work` refused the near-duplicate,
    and moving it by hand breaks undo. Its "wasn't sure" flag was never
    cleared by anything either, so tidy listed it for ever."""
    t.box.run("save", "qwerty zxcvb")                  # nothing to go on: a note, flagged
    note = next(i for i in t.box.items() if "needs-review" in (i["flags"] or []))
    t.eq(note["kind"], "note", "it was filed as a note")
    t.box.run("push", note["id"])
    work = next(i for i in t.box.items() if i["title"] == note["title"])
    t.eq((work["kind"], work["status"], work["bucket"]), ("project", "pushing", "Work"),
         "push makes it work, in Work")
    readme = t.box.root / work["path"] / "README.md"
    t.ok(readme.exists() and "qwerty zxcvb" in readme.read_text(),
         "with its own words kept as the README")
    t.ok("## Next action" in readme.read_text(), "and the shape work has")
    t.eq(t.box.json("tidy")["unsure"], [], "and tidy stops saying it wasn't sure about it")
    t.box.run("hold", work["id"])
    said = t.box.run("push", work["id"]).stdout
    t.ok("back the other way?  ./os hold" in said, "work pushed back has hold as its way back")
    for _ in range(3):              # the push, the hold, and the push that made it work
        t.box.run("undo")
    note = next(i for i in t.box.items() if i["title"] == note["title"])
    said = t.box.run("push", note["id"]).stdout
    t.ok("./os hold" not in said and "./os undo" in said,
         "a note pushed is offered undo to go back, not hold, which keeps it work")
    t.box.run("undo")
    back = next(i for i in t.box.items() if i["title"] == note["title"])
    t.eq((back["kind"], back["path"]), ("note", note["path"]), "undo makes it the note it was")


def as_chat(label: str) -> None:
    """Pretend to be a particular chat. A claim is only worth anything if two
    sessions in the same folder read as two different people. It is Claude
    Code's own variable: the chat running these tests has that one set too,
    and it is read first."""
    os.environ["CLAUDE_CODE_SESSION_ID"] = label


@test
def test_the_same_thought_twice_is_still_one_thing(t: Case) -> None:
    """Three snags, all the same shape: one thought, several items.

    A thought reached `os save` three times in one 2026-09-05 session and left
    two duplicate projects behind; a list of channels was read as work twice;
    and wrap-up decisions saved this way became a project one day and a note
    called "Decided 2026-09-07" the next."""
    first = t.box.run("save", "the billing token dies every Friday night")
    t.ok("saved" in first.stdout.lower(), "the first one is filed as always")
    again = t.box.run("save", "the  Billing token dies every friday   night")
    t.ok("already written down" in again.stdout.lower(),
         "the same words again are not a second thing")
    t.box.run("index")
    t.eq(len([i for i in t.box.items()
              if "billing token" in i["title"].lower()]), 1, "so there is one item, not two")

    t.box.run("save", "channels whose edits are the standard: "
                      "https://youtube.com/@one https://youtube.com/@two "
                      "https://youtube.com/@three")
    t.box.run("index")
    listed = t.box.carrying("channels whose edits are the standard")
    t.eq(listed["kind"], "note", "a list of links is something to look up, not work")

    t.box.run("new", "work", "Ship the redesign", "--domain", "design")
    t.box.run("index")
    ident = next(i["id"] for i in t.box.items() if i["title"] == "Ship the redesign")
    before = len(t.box.items())
    decided = t.box.run("save", "Decided: Ship the redesign goes Meta first, "
                                "rules out YouTube")
    t.ok("Ship the redesign" in decided.stdout, "a decision says which item it went into")
    t.box.run("index")
    t.eq(len(t.box.items()), before, "and makes nothing new — no 'Decided <date>' item")
    spine = t.box.root / next(i["path"] for i in t.box.items()
                              if i["id"] == ident) / "README.md"
    body = spine.read_text()
    line = [l for l in body.split("\n") if "Meta first" in l]
    t.eq(len(line), 1, "one line, once")
    t.ok(line[0].startswith("- " + engine.today()), "dated, in the folder's own shape")
    t.ok(body.index("## Decisions") < body.index("Meta first"),
         "and it sits under ## Decisions")

    t.box.run("undo")
    t.ok("Meta first" not in spine.read_text(), "which undo takes back like anything else")


@test
def test_decide_writes_one_line_into_the_thing_it_is_about(t: Case) -> None:
    """`os save` places a decision only when the words name the item.

    Three notes called "Decided <date>" and "Open from tidy" piled up in Notes/
    by 2026-09-08 because the words never named anything. `os decide` is the
    same line said by hand, and `os save` now refuses rather than filing one."""
    t.box.run("new", "work", "Ship the redesign", "--domain", "design")
    t.box.run("index")
    ident = next(i["id"] for i in t.box.items() if i["title"] == "Ship the redesign")
    before = len(t.box.items())

    out = t.box.run("decide", ident, "three tiers, not four — rules out a free tier")
    t.ok("Wrote that under Decisions" in out.stdout,
         "it says where the line went, in those words")
    t.box.run("index")
    t.eq(len(t.box.items()), before, "and makes nothing new")

    spine = t.box.root / next(i["path"] for i in t.box.items()
                              if i["id"] == ident) / "README.md"
    body = spine.read_text()
    line = [l for l in body.split("\n") if "three tiers" in l]
    t.eq(len(line), 1, "one line, once")
    t.ok(line[0].startswith("- " + engine.today()), "dated, in the folder's own shape")
    t.ok(body.index("## Decisions") < body.index("three tiers"),
         "and it sits under ## Decisions")

    t.box.run("undo")
    t.ok("three tiers" not in spine.read_text(), "and undo takes it back")

    stray = t.box.run("save", "Decided: nothing in this sentence names anything held",
                      expect=1)
    t.ok("./os decide" in stray.stdout + stray.stderr,
         "a decision that names nothing is refused, and told which command to use")
    t.box.run("index")
    t.eq(len(t.box.items()), before, "so no 'Decided <date>' note is left behind")

    # Appended, as AGENTS.md, /decide, /wrapup and /find all say: the newest
    # is last. It used to go straight under the heading, newest first, and
    # /find's "the last entry wins" then picked the oldest.
    t.box.run("decide", ident, "Decided to use cedar — rules out pine")
    t.box.run("decide", ident, "Decision number 5 — rules out number 4")
    body = spine.read_text()
    t.ok("Decided to use cedar — rules out pine" in body,
         "their words are kept whole, even when they start with 'Decided'")
    t.ok("Decision number 5" in body, "and when they start with 'Decision'")
    t.ok(body.index("never rewrite it") < body.index("use cedar") < body.index("number 5")
         < body.index("## Log"),
         "each one goes at the end of ## Decisions, under the blueprint's own note")
    shown = t.box.run("show", ident).stdout
    t.ok("number 5" in shown, "and show lists the newest")

    # "We decided …" is a decision too, when it names the thing.
    t.box.run("new", "work", "Kitchen renovation", "--domain", "personal")
    kitchen = t.box.root / next(i["path"] for i in t.box.items()
                                if i["title"] == "Kitchen renovation") / "README.md"
    count = len(t.box.items())
    said = t.box.run("save", "We decided the Kitchen renovation tiles will be white, not green")
    t.ok("Kitchen renovation" in said.stdout, "it says which item it went into")
    t.ok("We decided the Kitchen renovation tiles will be white" in kitchen.read_text(),
         "'We decided …' goes into the ## Decisions of the item it names")
    t.box.run("index")
    t.eq(len(t.box.items()), count, "and makes nothing new")
    t.box.run("save", "I decided to learn to sail next summer")
    t.box.run("index")
    t.eq(len(t.box.items()), count + 1,
         "'I decided …' that names nothing held is still written down, never refused")
    # A pasted page that opens with "We decided" is a page, not one decision:
    # all of it, other notes and all, went into the append-only ## Decisions.
    pasted = ("We decided a few things in the call today.\n- Kitchen renovation starts in May\n"
              "- Budget is 20k\n\nOther notes: Sam is away in June, and the plumber wants cash.")
    t.box.run("save", pasted)
    t.ok("plumber wants cash" not in kitchen.read_text(), "a pasted page doesn't go into ## Decisions")
    t.ok(t.box.carrying("plumber wants cash"), "it is saved like anything else")


@test
def test_a_claim_names_a_chat_readably(t: Case) -> None:
    """A claim made inside Claude Code was shown as its whole session id."""
    said = engine.claim_words("3f2a7c1e-9b4d-4c21-8e0f-5a6b7c8d9e0f 2026-09-29T10:00:00")
    t.ok(said.startswith("claimed by chat 3f2a7c1e ") and "9b4d" not in said, f"short enough to read: {said}")


@test
def test_a_claim_says_which_chat_is_holding_something(t: Case) -> None:
    """Two AI sessions in one folder, and the second is told before it writes.

    Two chats built the same thing at the same path on 2026-09-08 and one
    overwrote the other, silently. `os claim` cannot stop that — nothing here
    locks a file — but it puts the fact in the header both of them read."""
    was = os.environ.get("CLAUDE_CODE_SESSION_ID")
    try:
        as_chat("chat-one")
        t.box.run("new", "work", "Rebuild the onboarding flow", "--domain", "engineering")
        t.box.run("index")
        ident = next(i["id"] for i in t.box.items()
                     if i["title"] == "Rebuild the onboarding flow")
        spine = t.box.root / next(i["path"] for i in t.box.items()
                                  if i["id"] == ident) / "README.md"
        before = engine.parse_frontmatter(spine.read_text())[0]

        t.box.run("claim", ident, "--as", "the welcome email")
        t.ok("it was claimed" not in t.box.run("release", ident).stdout,
             "the chat that claimed it, note and all, is not told it was somebody else's")
        t.box.run("claim", ident, "--as", "the welcome email")
        meta = engine.parse_frontmatter(spine.read_text())[0]
        label, when = engine.read_claim(meta.get("claimed"))
        t.eq(label, "chat-one (the welcome email)", "the header says who, and what for")
        t.ok(when.startswith(engine.today()), "and when, to the second")
        t.eq(meta.get("updated"), before.get("updated"),
             "claiming is not a change to the work, so the date is left alone")

        t.ok("claimed by chat-one" in t.box.run().stdout, "./os says who is holding it")
        t.ok("claimed by chat-one" in t.box.run("show", ident).stdout, "and so does show")

        as_chat("chat-two")
        refused = t.box.run("claim", ident, expect=1)
        t.ok("chat-one" in refused.stdout, "the second chat is told who has it")
        t.ok("release" in refused.stdout, "and how to take it anyway")

        for command in ("hold", "edit"):
            proc = t.box.run(command, ident)
            t.ok("claimed by chat-one" in proc.stdout,
                 f"`os {command}` warns before it changes a claimed item")
            t.ok(f"./os release {engine.handle(ident)}" in proc.stdout, "naming the way out")
        t.eq(next(i for i in t.box.items() if i["id"] == ident)["status"], "holding",
             "warned, not refused — the hold still happened")

        t.box.run("release", ident)
        t.ok("claimed" not in engine.parse_frontmatter(spine.read_text())[0],
             "release takes the line out, whoever put it there")
        t.ok("claimed by" not in t.box.run().stdout, "and ./os stops mentioning it")
        t.ok("nothing was holding" in t.box.run("release", ident).stdout,
             "releasing an unclaimed thing says so instead of failing")
    finally:
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        if was is not None:
            os.environ["CLAUDE_CODE_SESSION_ID"] = was


@test
def test_a_stale_claim_never_locks_anything_away(t: Case) -> None:
    """Chats close without saying goodbye, so a claim has to expire.

    Twelve hours old and it is shown as stale and can be claimed straight over
    — otherwise one crashed session would fence a thing off for good. And like
    every other change, a claim can be taken back."""
    was = os.environ.get("CLAUDE_CODE_SESSION_ID")
    try:
        as_chat("chat-one")
        t.box.run("new", "work", "Cut the drift shorts", "--domain", "design")
        t.box.run("index")
        ident = next(i["id"] for i in t.box.items() if i["title"] == "Cut the drift shorts")
        spine = t.box.root / next(i["path"] for i in t.box.items()
                                  if i["id"] == ident) / "README.md"

        t.box.run("claim", ident)
        t.box.run("undo")
        t.ok("claimed" not in engine.parse_frontmatter(spine.read_text())[0],
             "undo takes a claim back like anything else")

        old = (engine._dt.datetime.now() - engine._dt.timedelta(hours=30)).replace(
            microsecond=0).isoformat()
        meta, body = engine.parse_frontmatter(spine.read_text())
        meta["claimed"] = f"chat-gone {old}"
        spine.write_text(engine.compose(meta, body), encoding="utf-8")
        t.ok("stale" in t.box.run().stdout, "an old claim is shown as stale")

        as_chat("chat-two")
        taken = t.box.run("claim", ident)
        t.ok("taken over" in taken.stdout, "and can be claimed straight over")
        t.eq(engine.read_claim(engine.parse_frontmatter(spine.read_text())[0]["claimed"])[0],
             "chat-two", "the new chat holds it now")
    finally:
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        if was is not None:
            os.environ["CLAUDE_CODE_SESSION_ID"] = was


@test
def test_a_claim_made_in_claude_code_is_that_chat_s(t: Case) -> None:
    """Claude Code sets CLAUDE_CODE_SESSION_ID, not CLAUDE_SESSION_ID.

    Read for CLAUDE_SESSION_ID only, a claim fell back to the id of the shell
    that ran the command, which is gone by the next tool call: the chat's own
    claim read "that chat is gone" straight away, and any other chat could
    take it over without a word (review, 2026-09-29)."""
    was = {v: os.environ.pop(v, None)
           for v in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "TERM_SESSION_ID")}
    try:
        os.environ["CLAUDE_CODE_SESSION_ID"] = "chat-in-claude-code"
        t.box.run("new", "work", "Tile the bathroom", "--domain", "design")
        t.box.run("claim", "tile-the-bathroom")
        t.box.run("index")
        spine = t.box.root / next(i["path"] for i in t.box.items()
                                  if i["title"] == "Tile the bathroom") / "README.md"
        label, _ = engine.read_claim(engine.parse_frontmatter(spine.read_text())[0]["claimed"])
        t.eq(label, "chat-in-claude-code", "the claim names the chat, not a passing shell")
    finally:
        for var, value in was.items():
            os.environ.pop(var, None)
            if value is not None:
                os.environ[var] = value


# ---------------------------------------------------------------------------
# learning from source material  (.os/learn.py — the one part that goes online)
# ---------------------------------------------------------------------------

#: YouTube's automatic captions roll: every cue repeats the tail of the one
#: before it so the words appear to scroll up the screen. Read literally, this
#: says "the first thing" three times and "he said" twice.
ROLLING_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
the first thing

00:00:02.000 --> 00:00:04.000
the first thing
you do is flatten

00:00:04.000 --> 00:00:06.500
you do is flatten
<c>the back</c> &amp; hone it

00:00:35.000 --> 00:00:38.000
he said

00:00:38.000 --> 00:00:41.000
he said
that's the whole trick
"""


@test
def test_captions_become_something_worth_reading(t: Case) -> None:
    """Rolling captions must collapse, and keep a timestamp you can cite.

    Left rolling, a transcript triples in size and every line appears twice —
    unquotable, and three times the context for whatever reads it. The
    timestamps are the other half: a claim in a note is only checkable if you
    can say where in the video it came from."""
    vtt = t.box.tmp / "rolling.vtt"
    vtt.write_text(ROLLING_VTT, encoding="utf-8")
    text = learn.clean_vtt(vtt)

    t.eq(text.count("the first thing"), 1, "a repeated cue line appears once")
    t.eq(text.count("he said"), 1, "and so does the second one")
    t.ok("you do is flatten" in text and "that's the whole trick" in text,
         "while every new line survives")
    t.ok("<c>" not in text and "</c>" not in text, "caption markup is stripped")
    t.ok("&amp;" not in text and "& hone it" in text, "and entities are decoded")
    for junk in ("WEBVTT", "Kind:", "Language:", "-->"):
        t.ok(junk not in text, f"the file's own scaffolding is dropped ({junk})")

    stamps = re.findall(r"\[(\d\d):(\d\d)\]", text)
    t.gte(len(stamps), 2, "there is a timestamp to cite")
    t.eq(stamps[0], ("00", "00"), "the first is where the video starts")
    t.eq(stamps[1], ("00", "35"), "and the next follows the cue it belongs to")

    # order is meaning: a method read out of sequence is not a method
    t.ok(text.index("the first thing") < text.index("that's the whole trick"),
         "and the words stay in the order they were said")


@test
def test_a_link_is_read_however_it_was_pasted(t: Case) -> None:
    """People paste whatever the share button gave them."""
    for link in ("https://www.youtube.com/watch?v=wt4p2oalmRY",
                 "https://youtu.be/wt4p2oalmRY",
                 "https://www.youtube.com/shorts/wt4p2oalmRY",
                 "https://www.youtube.com/embed/wt4p2oalmRY?start=90",
                 "https://m.youtube.com/watch?v=wt4p2oalmRY&t=42s",
                 "wt4p2oalmRY"):
        t.eq(learn.video_id(link), "wt4p2oalmRY", f"{link} names the video")
    for not_a_video in ("https://example.com/article", "", "watch?v=too-short"):
        t.eq(learn.video_id(not_a_video), None, f"{not_a_video!r} is not a video")

    # a channel link has to be pointed at the uploads, not the trailer
    t.eq(learn.as_listing("https://www.youtube.com/@someone"),
         "https://www.youtube.com/@someone/videos", "a channel means its videos")
    t.eq(learn.as_listing("https://www.youtube.com/@someone/streams"),
         "https://www.youtube.com/@someone/videos", "and so does its streams tab")
    for left_alone in ("https://www.youtube.com/playlist?list=PL1",
                       "https://www.youtube.com/@someone/videos"):
        t.eq(learn.as_listing(left_alone), left_alone, f"{left_alone} is already right")


@test
def test_learning_says_what_went_wrong_in_words(t: Case) -> None:
    """It is driven by an AI, so every answer — including failure — is JSON.

    A traceback, or a bare non-zero exit, tells whatever is reading it nothing
    it can act on or repeat to a person."""
    def run(*args, expect: int = 0):
        proc = subprocess.run([sys.executable, ".os/learn.py", *args],
                              cwd=str(t.box.root), capture_output=True, text=True, encoding="utf-8", errors="replace")
        t.eq(proc.returncode, expect, f"`learn.py {' '.join(args)}` exits {expect}")
        t.ok("Traceback" not in proc.stderr,
             f"and never with a traceback:\n{proc.stderr[-400:]}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            t.ok(False, f"`learn.py {' '.join(args)}` did not print JSON: {proc.stdout[:200]!r}")
            return {}

    t.ok("commands" in run("help"), "help lists what it can do")
    t.ok("list" in run("help")["commands"] or
         any(k.startswith("list") for k in run("help")["commands"]),
         "including how to see what a channel has")

    # A refusal exits non-zero as well as saying so, because a caller reading
    # only the status and a caller reading only the JSON must not come away
    # with different stories. This used to be a flat exit 0 for everything.
    unknown = run("teleport", expect=1)
    t.eq(unknown["ok"], False, "an unknown command is a refusal, not a crash")
    t.ok("teleport" in unknown["why"], "and it says which word it did not know")

    t.eq(run("list", expect=1)["ok"], False, "asking for a listing with no link is refused")
    t.eq(run("get", expect=1)["ok"], False, "and so is asking for a transcript with no link")

    bad = run("get", "https://example.com/not-a-video", expect=1)
    t.eq(bad["ok"], False, "a batch where nothing came back does not report success")
    t.eq(bad["results"][0]["ok"], False, "a link that is not a video is reported per link")
    t.ok("video" in bad["results"][0]["why"], "in words a person could act on")

    empty = run("have")
    t.eq(empty["cached"], [], "nothing is cached in a fresh folder")

    # the cache is real, keyed by video, and forgettable
    learn.cache_dir(t.box.root).joinpath("wt4p2oalmRY.txt").write_text(
        "[00:00] flatten the back first\n", encoding="utf-8")
    t.eq(len(run("have")["cached"]), 1, "a cached transcript is listed")
    t.eq(run("have")["cached"][0]["words"], 5, "with how much is in it")
    t.eq(run("forget", "wt4p2oalmRY")["removed"], 1, "and can be dropped again")
    t.eq(run("have")["cached"], [], "leaving nothing behind")


@test
def test_forgetting_a_source_never_reaches_outside_the_cache(t: Case) -> None:
    """`--forget` takes an id and deletes that transcript, with no undo.

    The id was joined straight onto the cache folder, so `../../Notes/diary`
    or a whole path outside the folder deleted somebody's own .txt file."""
    diary = t.box.root / "Notes" / "diary.txt"
    diary.write_text("Tuesday. Rained all day.\n", encoding="utf-8")
    outside = t.box.tmp / "shopping.txt"
    outside.write_text("milk, eggs\n", encoding="utf-8")
    kept = learn.cache_dir(t.box.root) / "wt4p2oalmRY.txt"
    kept.write_text("[00:00] flatten the back first\n", encoding="utf-8")

    t.box.run("learn", "--forget", "../../Notes/diary", str(t.box.tmp / "shopping"))
    t.ok(diary.exists(), "a note reached with ../ is not deleted")
    t.ok(outside.exists(), "nor is a file named by its whole path")
    t.ok(kept.exists(), "and a cached transcript nobody named is still there")
    t.box.run("learn", "--forget", "wt4p2oalmRY")
    t.ok(not kept.exists(), "while forgetting a real one still works")


@test
def test_a_subject_teaches_the_folder_its_words(t: Case) -> None:
    """What `/learn` studies has to change where the next thought lands.

    A subject arrives with vocabulary the folder has never seen — "roas", "ad
    set" — and filing is done by matching words. Learning something and then
    still misfiling every passing note about it is the loop left open."""
    def run(*args: str, expect: int = 0) -> dict:
        proc = subprocess.run([sys.executable, ".os/learn.py", "words", *args],
                              cwd=str(t.box.root), capture_output=True, text=True, encoding="utf-8", errors="replace")
        t.eq(proc.returncode, expect, f"`learn.py words {' '.join(args)}` exits {expect}")
        return json.loads(proc.stdout)

    listed = run()
    t.ok(listed["ok"], "with no arguments it says what domains exist")
    t.ok(any(d["domain"] == "marketing" for d in listed["domains"]),
         "naming each one, so nothing has to be guessed at")

    before = t.box.root / ".os" / "words.json"
    mine = json.loads(before.read_text())["domains"]["marketing"]["keywords"]

    # invented words, so the assertion holds whatever vocabulary ships
    added = run("marketing", "zorblat", "flimbus rate", "quibbing phase")
    t.eq(added["added"], ["zorblat", "flimbus rate", "quibbing phase"], "all three taken")
    again = run("marketing", "zorblat", "ZORBLAT ", "grelling")
    t.eq(again["added"], ["grelling"], "only what is genuinely new is added")
    t.ok("zorblat" in again["already_known"], "a repeat is reported, not duplicated")

    tax = json.loads(before.read_text())
    t.eq(tax["domains"]["marketing"]["keywords"], mine,
         "their own keywords are never written to")
    t.eq(tax["domains"]["marketing"]["learned"][-4:],
         ["zorblat", "flimbus rate", "quibbing phase", "grelling"],
         "what was learned is kept separate, and in order")

    # the payoff: the classifier scores learned words exactly like their own
    box = engine.Zenith(t.box.root)
    dom, _, _ = engine.Classifier(box).score_domain(
        "zorblat fell off once the quibbing phase reset", "", "")
    t.eq(dom, "marketing", "so a passing thought about it now files itself")

    missing = run("nosuchdomain", "roas", expect=1)
    t.eq(missing["ok"], False, "an unknown domain is refused")
    t.ok("marketing" in missing["domains"], "and the real ones are offered back")


@test
def test_a_new_subject_gets_a_folder_name_that_is_safe(t: Case) -> None:
    """The name after `./os words --new` became the folder its things are
    grouped in, exactly as typed. `content` hid every video note in
    Work/Content, which ./os never looks in, and `wine~` hid them in a folder
    ./os skips as a leftover; `Food/drink` made a folder in a
    folder; `../outside` moved the notes out of this folder altogether. And
    `./os words Garden "dahlias"` was refused for a subject that was there."""
    words = t.box.root / ".os" / "words.json"
    before = words.read_bytes()
    t.box.run("new", "work", "Wedding")
    for name, why in (("content", "where big files go"), ("Notes", "uses that name"),
                      ("archive", "uses that name"), ("wedding", "Work/Wedding"),
                      ("wine~", "leftover file"), ("wine.swp", "leftover file"),
                      ("wine.card.md", "leftover file")):
        said = t.box.run("words", "--new", name, "hive", expect=1).stderr
        t.ok(why in said and "Pick another name" in said,
             f"--new {name} is refused, saying why in plain words:\n{said}")
    t.eq(words.read_bytes(), before, "and nothing was written")

    # A group sort made itself can be shared, like one left in Notes by a
    # subject taken back. Asking stopped with a traceback.
    cellar = t.box.root / "Notes" / "Cellar"
    cellar.mkdir(parents=True)
    (cellar / ".category").write_text(json.dumps(
        {"name": "Cellar", "trail": ["Cellar"], "auto": True}) + "\n")
    kept = t.box.json("words", "--new", "cellar", "claret")
    t.eq((kept["domain"], kept["made"]), ("cellar", True),
         "a group sort made doesn't stop a subject of that name")

    t.box.run("words", "--new", "../outside", "wine")
    t.box.run("words", "--new", "Food/drink", "cocktail")
    made = json.loads(words.read_text())["domains"]
    labels = (made["outside"]["label"], made["food-drink"]["label"])
    t.ok(all("/" not in label and "." not in label for label in labels),
         f"a slash or dots never get into the folder name: {labels}")
    ai = t.box.json("words", "--new", "AI & agents", "robot")
    t.eq((ai["domain"], ai["made"]), ("ai", False),
         "a name some subject already goes by is that subject")
    t.eq(t.box.json("words", "Garden", "dahlias")["domain"], "garden",
         "and a subject is found whatever its capitals")
    t.box.run("words", "--new", "Bee keeping", "hive")
    t.eq(t.box.json("words", "Bee keeping", "queen")["domain"], "bee-keeping",
         "or its spaces")

    # the payoff: once Notes fills up, the group stays inside Notes. Sort
    # names each note as it files it, so `wine 0.md` is wine-0.md by then.
    for n in range(13):
        (t.box.root / "Notes" / f"wine {n}.md").write_text(
            f"A good wine, number {n}: red, from the cellar.\n", encoding="utf-8")
    t.box.run("sort")
    t.ok(not (t.box.tmp / "outside").exists() and not (t.box.root / "outside").exists(),
         "nothing is moved out of Notes, or out of this folder")
    grouped = sorted(p.name for p in (t.box.root / "Notes" / "Outside").rglob("wine*.md"))
    t.eq(len(grouped), 13, "every wine note is grouped under Notes/Outside")
    t.eq(len(t.box.json("find", "cellar")), 13, "and found")


@test
def test_an_odd_textedit_file_never_stops_search(t: Case) -> None:
    """One .rtf with `\\u99999999` in it made every ./os find stop with a
    traceback, whatever was searched for, and every ./os sort on Linux. A
    bare `\\u`, or `\\'00`, put an invisible NUL character onto the card,
    which then counted as a binary file."""
    b = "\\"
    for odd in ("u99999999 here", "u-99999 here", "u here", "u0 here", "'00 here",
                "'07 here", "u7 here"):
        said = engine.rtf_words(b.join(["{", "rtf1", "ansi Odd file ", odd + "}"]))
        t.ok(said.startswith("Odd file") and not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", said),
             f"{odd!r} is skipped, not a crash or a NUL: {said!r}")
    (t.box.root / "Notes" / "Broken.rtf").write_text(
        b.join(["{", "rtf1", "ansi Odd file ", "u99999999 here about potash}"]), encoding="ascii")
    t.box.run("sort")
    for word in ("potash", "garden"):
        found = t.box.run("find", word, expect=None)
        t.ok("Traceback" not in found.stderr, f"./os find {word} still works:\n{found.stderr[-600:]}")


@test
def test_words_added_to_a_textedit_card_are_found(t: Case) -> None:
    """Search left out everything on an RTF's card from `## What it says`
    down, not just the copied words. The sentence of what it is that
    AGENTS.md asks for, added at the end of the card, was never found."""
    b = "\\"
    rtf = b.join(["{", "rtf1", "ansi{", "fonttbl", "f0 Helvetica;}\n",
                  "f0", "fs24 Seed order: tomatoes and potash.", "\n}"])
    kept = t.box.root / "Notes" / "seed-order.rtf"
    kept.write_text(rtf, encoding="ascii")
    t.box.run("sort")
    card = kept.with_name(kept.name + ".card.md")
    with card.open("a", encoding="utf-8") as fh:
        fh.write("\nWhat it is: Mum's order from the Suttons catalogue.\n")
    item = next(i for i in t.box.items() if (i["path"] or "").endswith(".rtf"))
    t.eq([r["id"] for r in t.box.json("find", "suttons")], [item["id"]],
         "a sentence added at the end of the card is found")
    kept.write_text(rtf.replace("potash", "rhubarb"), encoding="ascii")
    t.eq(t.box.json("find", "potash"), [], "while the card's copy of the file is still left out")
    t.eq(len(t.box.json("find", "rhubarb")), 1, "and the file is read as it is now")


@test
def test_an_update_teaches_a_folder_that_a_recipe_is_a_note(t: Case) -> None:
    """"recipe", "ingredients" and the kitchen amounts went into the words
    that tell a note from work, but an update renewed only the subjects. A
    folder that updated went on filing a lentil soup recipe as work."""
    import upgrade
    root = t.box.root
    new = json.loads((root / ".os" / "words.json").read_text())["intent"]
    # The note lists exactly as 2026-09-29.1 shipped them. Written out here,
    # not worked out from today's words.json, so adding a note word later
    # doesn't make this check fail for no reason and stop a release.
    first = {"keywords": [
        "notes", "notes on", "remind me", "reminder", "don't forget", "summary", "reference",
        "definition", "explained", "overview", "cheat sheet", "cheatsheet", "tldr", "tl;dr",
        "what is", "key ideas", "takeaways", "excerpt", "quote", "source:", "found this",
        "worth knowing", "worth remembering", "remember that", "for reference", "turns out",
        "the difference between", "how it works", "the trick is", "apparently", "according to",
        "read that", "saw that", "idea:", "thought:", "interesting", "idea for", "what if",
        "might be worth", "someday", "maybe", "note to self", "session log", "decision log",
        "changelog", "retro", "retrospective", "standup", "quote:", "he said", "she said",
        "they said", "someone said", "fyi"],
        "patterns": [r"^#+\s*(what|how|why)\b", "https?://", r"^#+\s*\d{4}-\d{2}-\d{2}",
                     "^\\s*[\\\"\u201c\u2018']", r"^\s*(good |great |favourite |favorite )?quote\b"]}
    t.eq({key: upgrade.keywords_sha(first[key]) for key in first},
         {"keywords": "04ba4a019f7770089b0de5e630fd3eff68414206",
          "patterns": "4cf5311d959fae8949918d7b1257767b2d178f75"},
         "(the lists above are the ones that release shipped)")

    def old(w: dict) -> None:
        w["intent"]["note"].update(keywords=list(first["keywords"]),
                                   patterns=list(first["patterns"]))
    _edit_json(root / ".os" / "words.json", old)
    _release(root, "2026-01-01.1")
    published = _publish(t, "2026-02-01.1", lambda out: _edit_json(
        out / ".os" / "words.json",
        lambda w: w["intent"]["note"].update(keywords=new["note"]["keywords"],
                                             patterns=new["note"]["patterns"])))
    _edit_json(root / ".os" / "words.json",
               lambda w: w["intent"]["pushing"]["keywords"].append("get round to"))

    done = t.box.run("update", "--from", str(published))
    intent = json.loads((root / ".os" / "words.json").read_text())["intent"]
    t.eq((intent["note"]["keywords"], intent["note"]["patterns"]),
         (new["note"]["keywords"], new["note"]["patterns"]),
         f"lists nobody changed are the new ones\n{done.stdout}")
    t.ok("get round to" in intent["pushing"]["keywords"], "one they changed stays theirs")
    t.ok("telling a note from work" in done.stdout, "and it says so")
    t.box.run("save", "Aunt May's lentil soup recipe. Ingredients: 250g red lentils, 1 onion. "
              "Method: soften the onion, add lentils and stock, simmer 25 minutes.")
    t.eq(t.box.carrying("Aunt May's lentil soup")["kind"], "note", "so a recipe is a note there too")
    for key in upgrade.INTENT_LISTS:
        t.ok(upgrade.keywords_sha(first[key])
             in upgrade.shipped_hashes(SOURCE, f"intent_{key}").get("note", []),
             f"and the note {key} the first release shipped are known as released")


@test
def test_find_reads_the_notes_inside_a_piece_of_work(t: Case) -> None:
    """Search read a folder's page and nothing else, so a quote kept in a
    note inside a piece of work was never found. What programs keep in there
    (node_modules, .git) is still never read, and only so much of it. But a
    folder called Content inside is theirs, not Work/Content; a second word
    in the work's name kept the file's words from showing; and one folder
    full of files used up the cap before the next folder was reached."""
    t.box.run("new", "work", "Kitchen refit")
    work = t.box.root / "Work" / "Kitchen Refit"
    (work / "quotes").mkdir()
    (work / "quotes" / "removals.md").write_text(
        "Removals quote from Bristow and Sons: 450 pounds.\n", encoding="utf-8")
    (work / "suppliers").mkdir()
    (work / "suppliers" / "plumber.md").write_text("Plumber: Quarmby, Tuesdays.\n",
                                                   encoding="utf-8")
    (work / "Content").mkdir()
    (work / "Content" / "tiler.txt").write_text("Tiler: Gumbleton\n", encoding="utf-8")
    b = "\\"
    (work / "tiles.rtf").write_text(b.join(["{", "rtf1", "ansi Tiles from Mandarin Stone.}"]),
                                    encoding="ascii")
    for skipped in ("node_modules/x", ".git", "venv/lib"):
        (work / skipped).mkdir(parents=True)
        (work / skipped / "notes.md").write_text("zorblax\n", encoding="utf-8")
    hits = t.box.json("find", "bristow")
    t.eq([(h["id"], h.get("file")) for h in hits],
         [("Kitchen Refit", "Work/Kitchen Refit/quotes/removals.md")],
         "a note inside is found, as the work it is in and the file it is in")
    t.ok("in quotes/removals.md" in t.box.run("find", "bristow").stdout,
         "and ./os find says which file")
    t.eq([h["id"] for h in t.box.json("find", "mandarin")], ["Kitchen Refit"],
         "a TextEdit note inside too")
    t.eq(t.box.json("find", "zorblax"), [], "never what programs keep there")
    t.eq([(h["id"], h.get("file")) for h in t.box.json("find", "gumbleton")],
         [("Kitchen Refit", "Work/Kitchen Refit/Content/tiler.txt")],
         "a folder of theirs called Content is read, as Work/Content is not")
    both = t.box.json("find", "bristow kitchen")
    t.eq([(h["id"], h.get("file"), "Bristow" in h["snippet"]) for h in both],
         [("Kitchen Refit", "Work/Kitchen Refit/quotes/removals.md", True)],
         "a word of its name as well still shows the words found and their file")
    for n in range(60):
        (work / "quotes" / f"quote {n:02}.md").write_text("A quote.\n", encoding="utf-8")
    t.eq([h.get("file") for h in t.box.json("find", "quarmby")],
         ["Work/Kitchen Refit/suppliers/plumber.md"],
         "a full folder doesn't use up what is read before the next one")
    os_ = engine.Zenith(t.box.root)
    finder = engine.Finder(os_)
    item = finder.by_id("kitchen-refit")
    t.eq(len(finder._inside(item)), engine.Finder.INSIDE_FILES, "and only so many files")


@test
def test_the_examples_are_from_home_everywhere(t: Case) -> None:
    """Help and "which one?" still said q3-okr-review and `marketing "ad
    set"`; the demo showed the person's own boiler note after "find one
    again"; its last line said any AI would do; and check named an RTF
    taken back with undo by its first line of code."""
    job = re.compile(r"okr|ad set|learning phase|marketing", re.I)
    for command in ("hold", "show", "open", "edit", "rename", "close", "words"):
        said = t.box.run("help", command).stdout
        t.eq(job.findall(said), [], f"`./os help {command}` has home examples")
    for args in (["show"], ["close"], ["hold"], ["rename"], ["words", "garden"]):
        said = t.box.run(*args, expect=None).stderr
        t.eq(job.findall(said), [], f"`./os {' '.join(args)}` asks with a home example")
    import shlex
    t.box.run("new", "work", "Fix the boiler")
    for example in (engine.DETAIL["hold"][2] + engine.DETAIL["show"][2]
                    + engine.DETAIL["close"][2]):
        t.box.run(*shlex.split(example)[1:])

    (t.box.root / "Notes" / "boiler.md").write_text(
        "Boiler model: Worcester. Boiler pressure 1.5 bar.\n", encoding="utf-8")
    t.box.run("sort")
    demo = t.box.run("demo").stdout.split("\n")
    found = demo[next(n for n, line in enumerate(demo) if "./os find boiler" in line) + 1]
    t.ok("The boiler keeps cutting out" in found, f"the demo finds its own: {found!r}")
    t.ok(not any("any AI" in line for line in demo), "and doesn't say any AI will do")

    b = "\\"
    outside = t.box.tmp / "pond.rtf"
    outside.write_text(b.join(["{", "rtf1", "ansi", "ansicpg1252{", "fonttbl", "f0 Helvetica;}\n",
                               "f0 Plants for the pond.}"]), encoding="ascii")
    t.box.run("save", str(outside))
    t.box.run("undo")
    said = [i["message"] for i in t.box.json("check", expect=None)["issues"]
            if i["code"] == "taken-back-capture"]
    t.ok(said and "Plants for the pond" in said[0] and "rtf1" not in said[0],
         f"check names it by what it says: {said}")


@test
def test_the_demo_in_a_full_folder_says_where_things_went(t: Case) -> None:
    """With more than a dozen things in Work and Notes, sort groups the
    demo's three by subject straight after filing them. Step 2 printed those
    second moves as bare "sort →" lines, and called the kept-up garden work
    to push, having read its phase off the path from before the move."""
    for n in range(13):
        t.box.run("new", "work", f"Paint room {n}")
        (t.box.root / "Notes" / f"paint colour {n}.md").write_text(
            f"Room {n} paint: Farrow and Ball, eggshell.\n", encoding="utf-8")
    t.box.run("sort")
    shown = t.box.run("demo", "--keep").stdout
    lines = [line.strip() for line in
             shown.split("Watch where they go.")[1].split("Find one again")[0].split("\n")
             if "→" in line]
    t.eq(len(lines), 3, f"one line for each of the three:\n{shown}")
    t.ok(not any(line.startswith("sort") for line in lines), "and none says just sort")
    for line in lines:
        where = line.split("→", 1)[1].strip()
        t.ok((t.box.root / where).exists(), f"each says where it is now: {line!r}")
    garden = next(line for line in lines if "Garden" in line)
    t.ok(garden.startswith("work you keep up"), f"the garden is kept up: {garden!r}")


def _unsure(box: "Sandbox", words: str) -> bool:
    """Save `words`; was it flagged as something ./os wasn't sure about?"""
    said = box.run("save", words).stdout
    item = box.carrying(words)
    flagged = "needs-review" in (item["flags"] or [])
    if flagged != ("wasn't sure" in said):
        raise Failure(f"the flag and what save said disagree for {words!r}:\n{said}")
    return flagged


@test
def test_a_plain_fact_is_not_called_a_guess(t: Case) -> None:
    """Nearly every plain fact came back "I wasn't sure what this one was".

    Only the note-or-work score decided it, and a fact has no cue either way,
    so "Q3 revenue was 1.2m" was flagged with its subject plain to see, and
    tidy's "I wasn't sure" list filled with everything a person wrote down.
    Unsure is kept for when there is nothing to go on at all."""
    t.box.run("words", "marketing", "zorblat")
    t.ok(not _unsure(t.box, "The zorblat was blue on Tuesday"),
         "a fact whose words name a subject is not a guess")
    t.ok(not _unsure(t.box, "Q3 revenue was 1.2m, up 8 percent on Q2"),
         "a work fact is not a guess either")
    t.ok(not _unsure(t.box, "Turns out a zebra's stripes confuse the flies"),
         "nor is one that reads as a note, whatever it is about")
    t.ok(_unsure(t.box, "qwerty zxcvb"), "nothing to go on at all is still flagged")

    # A folder still on an older words.json gives every .md to Writing on its
    # file type alone. That is not a subject matching, so it stays a guess.
    words = t.box.root / ".os" / "words.json"
    spec = json.loads(words.read_text(encoding="utf-8"))
    spec["domains"]["writing"].setdefault("extensions", []).extend([".md", ".txt"])
    words.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    t.ok(_unsure(t.box, "asdfgh jklpoi"), "a file type is not something to go on")
    t.eq(len(t.box.json("tidy")["unsure"]), 2, "and tidy lists just those two")


@test
def test_a_note_no_subject_fits_is_filed_quietly(t: Case) -> None:
    """Home notes all went under Writing, and a person could not add a subject.

    Writing claimed every .md and .txt on its file type, so a shoe size and a
    wifi password were both "writing", and `./os words garden "bulbs"` was
    refused as "no domain called 'garden'". Taking the file types away left
    `./os check` with one "has no subject set" line per note instead."""
    t.box.run("save", "Sam's shoe size is 8")
    t.box.run("new", "note", "Odds and ends")
    for words in ("Sam's shoe size is 8", "Odds and ends"):
        t.eq(t.box.carrying(words)["domain"], "general",
             f"{words!r} goes under general, not writing")
    quiet = [i for i in t.box.json("check", expect=None)["issues"] if i["code"] == "no-domain"]
    t.eq(quiet, [], "and check has nothing to say about either")

    # a subject of their own: refused without --new, so a typo can't make one
    refused = t.box.run("words", "knitting", "purl", expect=1)
    t.ok("--new" in refused.stderr, f"a refusal says how to make one:\n{refused.stderr}")
    made = t.box.json("words", "--new", "Knitting", "purl", "cast on")
    t.eq((made["domain"], made["made"], made["added"]), ("knitting", True, ["purl", "cast on"]),
         "--new makes the subject and takes its words")
    block = json.loads((t.box.root / ".os" / "words.json").read_text())["domains"]["knitting"]
    t.eq((block["label"], block["keywords"], block["learned"]), ("Knitting", [], ["purl", "cast on"]),
         "its words go where ./os words always puts them, never into a keywords list")
    again = t.box.json("words", "--new", "knitting", "yarn")
    t.eq((again["made"], again["added"]), (False, ["yarn"]), "made twice, it just takes the words")
    t.box.run("save", "Cast on forty stitches then purl the second row")
    t.eq(t.box.carrying("Cast on forty stitches")["domain"], "knitting",
         "and what is saved about it files itself there")
    t.box.run("new", "note", "Sock pattern", "--domain", "knitting")
    t.box.run("words", "--new", "!!!", expect=1)


@test
def test_a_recipe_is_a_note_to_keep_not_work_to_push(t: Case) -> None:
    """A lentil soup recipe was filed as work being pushed, then listed as
    "not touched in a while": ", add lentils" read as an instruction to do."""
    t.box.run("save", "Grandma's lentil soup recipe. Ingredients: 250g red lentils, 1 onion, "
              "2 carrots, 1.2 litres stock. Method: soften the onion, add lentils and "
              "stock, simmer 25 minutes, blend half.")
    item = t.box.carrying("Grandma's lentil soup")
    t.eq((item["kind"], item["bucket"]), ("note", "Notes"), "a recipe is a note")
    # One that never says "recipe": its weights and spoons say it instead
    t.box.run("save", "Banana bread: 3 ripe bananas, 75g melted butter, 1 egg, 150g sugar, "
              "190g flour. Mash the bananas, stir in the butter, add the rest, bake 60 min.")
    item = t.box.carrying("75g melted butter")
    t.eq((item["kind"], item["bucket"]), ("note", "Notes"), "so is one that doesn't say so")
    t.eq(t.box.json("tidy")["active"], [], "and nothing is on the go because of either")
    t.box.run("save", "Order 5kg of seed potatoes and 2kg onion sets by Friday")
    t.eq(t.box.carrying("seed potatoes")["kind"], "project",
         "while a thing to do that has weights in it is still work")


@test
def test_words_in_a_textedit_file_can_be_found(t: Case) -> None:
    """TextEdit saves in RTF unless told not to, and search couldn't read it.

    `./os find tomatoes` said nothing matched for a note about tomatoes: the
    file is kept as it is with a card, and the card said only "Asset card for
    garden-notes.rtf". Its words are read now, onto the card and at search."""
    b = "\\"
    rtf = (b.join(["{", "rtf1", "ansi", "ansicpg1252{", "fonttbl", "f0", "fswiss Helvetica;}\n"
                   "{", "colortbl;", "red255", "green255", "blue255;}\n{", "*", "expandedcolortbl;;}\n",
                   "f0", "fs24 ", "cf0 Tomatoes need pinching out every week.", "\n"
                   "The caf", "'e9 by the allotment sells potash.", "\n}"]))
    note = t.box.root / "Notes" / "Garden notes.rtf"
    note.write_text(rtf, encoding="ascii")
    t.box.run("sort")
    kept = next(p for p in (t.box.root / "Notes").rglob("*.rtf"))
    t.eq(kept.read_text(encoding="ascii"), rtf, "the file itself is kept exactly as it was")
    card = kept.with_name(kept.name + ".card.md").read_text(encoding="utf-8")
    t.ok("Tomatoes need pinching out" in card, f"its card carries its words:\n{card}")
    item = next(i for i in t.box.items() if (i["path"] or "").endswith(".rtf"))
    t.eq((item["title"], item["domain"]), ("Garden notes", "garden"),
         "it keeps the name it was saved under, and is filed by what it says")
    hits = [r["id"] for r in t.box.json("find", "tomatoes")]
    t.ok(item["id"] in hits, f"./os find reads it: {hits}")

    # TextEdit goes on saving into the same file; search reads what it says now
    kept.write_text(rtf.replace("sells potash", "sells rhubarb crowns"), encoding="ascii")
    hits = [r["id"] for r in t.box.json("find", "rhubarb")]
    t.eq(hits, [item["id"]], "a word written in after it was filed is found too")
    t.eq(t.box.json("find", "potash"), [], "and one taken out is not")
    # what reads it wherever textutil, the Mac's own reader, is not there
    t.eq(engine.rtf_words(rtf),
         "Tomatoes need pinching out every week.\nThe café by the allotment sells potash.",
         "the formatting codes come out, and the words stay")
    # An emoji is two \\u codes, one half each. Kept as two halves, the card
    # could not be written, and sort stopped with a traceback every time.
    said = engine.rtf_words(b.join(["{", "rtf1", "ansi{", "fonttbl", "f0 Helvetica;}\n",
                                    "f0 Seed order: zucchini ", "uc0", "u55356 ", "u57157  x2 ",
                                    "u-10179 lone", "\n}"]))
    t.eq(said, "Seed order: zucchini \U0001F345 x2 \ufffdlone",
         "an emoji comes back whole, and half of one as a stand-in")
    t.ok(said.encode("utf-8"), "so it can be written onto a card")


@test
def test_a_textedit_note_with_a_picture_can_be_found(t: Case) -> None:
    """With a photo pasted in, TextEdit saves a folder, `Name.rtfd`, with the
    words in a TXT.rtf inside it. It was kept as "a folder of files" with a
    card that said nothing, so `./os find rhubarb` missed it."""
    b = "\\"
    kept = t.box.root / "Notes" / "Garden with picture.rtfd"
    kept.mkdir(parents=True)
    (kept / "TXT.rtf").write_text(b.join(["{", "rtf1", "ansi{", "fonttbl", "f0 Helvetica;}\n",
                                          "f0", "fs24 Rhubarb crowns go in by the shed.",
                                          "\n}"]), encoding="ascii")
    (kept / "shed.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    t.box.run("sort")
    card = kept.with_name(kept.name + ".card.md")
    t.ok(card.is_file() and "Rhubarb crowns" in card.read_text(encoding="utf-8"),
         "its card carries the words from inside it")
    t.ok((kept / "TXT.rtf").is_file(), "it keeps its name, .rtfd and all, so TextEdit still opens it")
    item = next(i for i in t.box.items() if (i["path"] or "").endswith(".rtfd"))
    t.eq([r["id"] for r in t.box.json("find", "rhubarb")], [item["id"]], "./os find reads it")
    (kept / "TXT.rtf").write_text((kept / "TXT.rtf").read_text().replace("Rhubarb", "Leeks"))
    t.eq([r["id"] for r in t.box.json("find", "leeks")], [item["id"]],
         "and what it says now, not only what it said when it was filed")


@test
def test_the_first_things_shown_are_from_home_not_a_job(t: Case) -> None:
    """The demo, help and the kept-up blueprint spoke like a software team:
    a billing token refresh, a codebase kept green, "Keep the tests passing",
    "every release". A nurse with a garden read it as a tool for coders."""
    job = re.compile(r"token|codebase|staging|lint|billing|postgres|tests passing|"
                     r"redesign|every release", re.I)
    demo = t.box.run("demo").stdout
    t.eq(job.findall(demo), [], "the demo is about home things")
    t.ok("./os find boiler" in demo and "The boiler keeps cutting out" in demo,
         "and it finds the one it says it will")
    for command in ("new", "save", "find", "claim", "decide", "words"):
        said = t.box.run("help", command).stdout
        t.eq(job.findall(said), [], f"`./os help {command}` has home examples")
    holding = (t.box.root / ".os" / "templates" / "holding.md").read_text(encoding="utf-8")
    t.eq(job.findall(holding), [], "and so does the blueprint for something kept up")


@test
def test_the_help_examples_work_one_after_another(t: Case) -> None:
    """`./os help new` made "Fix the boiler before winter", and then `./os
    help claim` and `./os help decide` said `fix-the-boiler`, which is not its
    name: typed in order, every one after the first said "nothing here is
    called fix-the-boiler"."""
    import shlex
    started = next(e for e in engine.DETAIL["new"][2] if e.startswith("os new work "))
    t.box.run(*shlex.split(started)[1:])
    for example in engine.DETAIL["claim"][2] + engine.DETAIL["decide"][2]:
        said = t.box.run(*shlex.split(example)[1:], expect=None)
        t.eq(said.returncode, 0, f"`./{example}` works after `./{started}`:\n{said.stderr}")


@test
def test_a_subject_of_their_own_can_be_taken_back(t: Case) -> None:
    """`./os undo` straight after `./os words --new beekeeping` left
    beekeeping where it was, and quietly took back the save before it. And a
    long name ran into its count in the list: "a-very-long-subject-name0 words"."""
    t.box.run("save", "Took Rex out, forgot the poo bags again")
    before = (t.box.root / ".os" / "words.json").read_bytes()
    t.box.run("words", "--new", "a very long subject name that goes on and on", "hive", "queen bee")
    listed = t.box.run("words").stdout
    t.ok(re.search(r"a-very-long-subject-name\s+0 words\s+\+2 learned", listed),
         f"a long name has room in the list:\n{listed}")
    undone = t.box.run("undo").stdout
    t.ok("'words'" in undone, f"undo says what it took back:\n{undone}")
    t.eq((t.box.root / ".os" / "words.json").read_bytes(), before,
         "the new subject and its words are gone, and nothing else changed")
    t.ok(t.box.carrying("Took Rex out"), "and the save before it is still there")
    t.box.run("words", "garden", "dahlias")
    t.box.run("words", "garden", "dahlias")      # nothing new: not a step of its own
    t.box.run("undo")
    t.eq((t.box.root / ".os" / "words.json").read_bytes(), before,
         "one that added nothing is skipped over, not undone in place of the last")


@test
def test_an_update_takes_writing_off_every_note(t: Case) -> None:
    """Writing gave up .md and .txt, but an update only ever renewed a
    subject's keywords. A folder that updated would have gone on filing every
    note as writing on its file type, so the fix reached nobody who already
    had the folder. File types are renewed the same way now: while still as
    released."""
    import upgrade
    root = t.box.root
    first = [".md", ".txt", ".rtf", ".docx"]       # what the first ./os release shipped
    _edit_json(root / ".os" / "words.json",
               lambda w: w["domains"]["writing"].update(extensions=list(first)))
    _release(root, "2026-01-01.1")

    def change(out: Path) -> None:
        def words(w: dict) -> None:
            w["domains"]["writing"]["extensions"] = [".docx"]
            w["domains"]["design"]["extensions"].append(".xd")
        _edit_json(out / ".os" / "words.json", words)
    published = _publish(t, "2026-02-01.1", change)
    _edit_json(root / ".os" / "words.json",
               lambda w: w["domains"]["design"]["extensions"].append(".afdesign"))

    done = t.box.run("update", "--from", str(published))
    words = json.loads((root / ".os" / "words.json").read_text())["domains"]
    t.eq(words["writing"]["extensions"], [".docx"],
         f"a list of file types nobody changed is the new one\n{done.stdout}")
    t.ok(".afdesign" in words["design"]["extensions"] and ".xd" not in words["design"]["extensions"],
         "one they changed stays theirs")
    t.ok("new words for writing" in done.stdout, "and it says which subjects it renewed")
    t.ok(upgrade.keywords_sha(first) in upgrade.shipped_hashes(SOURCE, "extensions").get("writing", []),
         "and the list the first release shipped is known as released, so it is renewed too")


@test
def test_the_vocabulary_is_a_command_like_any_other(t: Case) -> None:
    """Teaching the folder words has to be reachable without invoking python.

    `python3 .os/learn.py` is not a thing anybody should have to type, and every
    other capability here is a plain `os` verb. The classifier is only as good
    as the words it has, so this is the one that compounds."""
    listed = t.box.json("words")
    t.ok(listed["ok"], "with no arguments it lists the domains")
    t.ok(any(d["domain"] == "marketing" for d in listed["domains"]), "by name")

    added = t.box.json("words", "marketing", "zorblat", "flimbus rate")
    t.eq(added["added"], ["zorblat", "flimbus rate"], "and takes new ones")
    plain = t.box.run("words", "marketing", "zorblat", "grelling")
    t.ok("1 added" in plain.stdout, "saying in words what it did")
    t.ok("zorblat" not in plain.stdout.split("added")[1][:40],
         "and not re-listing what it already knew as new")

    t.box.run("words", "nosuchdomain", "zorblat", expect=1)
    t.box.run("words", "marketing", expect=1)

    dom, _, _ = engine.Classifier(engine.Zenith(t.box.root)).score_domain(
        "the zorblat flimbus rate is falling", "", "")
    t.eq(dom, "marketing", "and what it learned is what filing then uses")

    # a video id that starts with a dash is an argument, not a mistyped option
    t.box.run("learn", "--cached")
    fetched = t.box.run("learn", "--", "-Xf12o4jt4x", expect=None)
    t.ok("doesn't understand" not in fetched.stderr,
         "a bare -- hands the id through untouched")


@test
def test_a_missing_downloader_is_not_a_dead_end(t: Case) -> None:
    """yt-dlp is the one thing Zenith wants installed, and it is optional.

    Everything else works without it, so a missing one has to say the single
    line to type on *this* machine rather than failing at somebody."""
    stripped = dict(os.environ, PATH="/nonexistent")
    # An empty PATH isn't enough on a machine that has it in /opt/homebrew,
    # which learn.py also looks in. So it is told there is none at all.
    missing = ("import sys; sys.path.insert(0, '.os'); import learn; "
               "learn.ytdlp = lambda: None; raise SystemExit(learn.main(sys.argv[1:]))")
    proc = subprocess.run(
        [sys.executable, "-c", missing, "list", "https://www.youtube.com/@someone"],
        cwd=str(t.box.root), capture_output=True, text=True, encoding="utf-8", errors="replace", env=stripped)
    t.eq(proc.returncode, 1, "a missing downloader is a refusal, not a raise")
    t.ok("Traceback" not in proc.stderr, "and never a traceback")
    t.ok("Traceback" not in proc.stderr, "with no traceback")
    payload = json.loads(proc.stdout)
    t.eq(payload["ok"], False, "it says plainly that it could not do it")
    t.ok("yt-dlp" in payload["why"], "and names what is missing")
    t.ok(payload.get("fix"), "with the one line to type")
    t.ok("yt-dlp" in payload["fix"], "which installs the thing it just named")
    t.ok("without it" in payload.get("note", ""),
         "and says the rest of the folder still works")

    # transcripts live in .os/, never in a bucket the sorter would adopt
    t.ok(learn.cache_dir(t.box.root).is_relative_to(t.box.root / ".os"),
         "the cache sits under .os/, out of the sorter's way")
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "so six transcripts never look like unfiled work")


@test
def test_a_name_says_what_a_thing_is_not_where_it_sits(t: Case) -> None:
    """A thing is called what it is called, in Work and in Archive alike.

    The old prefix (`W.04`) said which folder something belonged to, which was
    already a lie the moment anything was closed. There is no prefix now — the
    name on disk is the whole handle, and closing must not change it."""
    t.box.run("new", "work", "Ship the rewrite")
    t.box.run("new", "note", "Postgres index types")
    t.box.run("index")
    by_title = {i["title"]: i for i in t.box.items()}
    work, note = by_title["Ship the rewrite"], by_title["Postgres index types"]
    t.eq(work["id"], "Ship the Rewrite", "work is named after its title")
    t.eq(note["id"], "postgres-index-types", "and so is a note")
    t.ok(work["id"] in work["path"], "the name is what is actually on disk")

    t.box.run("close", work["id"])
    closed = next(i for i in t.box.items() if i["id"] == work["id"])
    t.eq(closed["bucket"], "Archive", "closing moves it to the Archive")
    t.eq(closed["id"], "Ship the Rewrite", "and it is called the same thing in there")

    # nothing carries an `id:` line any more, anywhere
    for folder in ("Notes", "Work", "Archive"):
        for path in (t.box.root / folder).rglob("*.md"):
            meta, _ = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            t.ok("id" not in meta, f"{path.name} carries no id: line")

    t.box.run("show", "no-such-thing-here", expect=1)   # a name that isn't there errors
    shown = t.box.run("show", note["id"])
    t.ok("postgres-index-types" in shown.stdout,
         "and ./os show <name> opens the right thing")


@test
def test_undo_of_a_sort_does_not_duplicate(t: Case) -> None:
    """Undoing a sort must leave one copy of each thing, not two.

    A single sort moves the same item twice — the inbox pass files it, the
    balance pass tucks it into a category — and each move snapshots its content
    under the path it had at the time. Writing every snapshot back after the
    moves were reversed re-created the file at the intermediate path, so the
    item ended up sitting in the inbox *and* filed in Notes."""
    inbox = t.box.root / "Notes"
    for i in range(16):
        (inbox / f"n{i}.md").write_text(
            f"Reference note {i} on postgres index types. B-tree is the default.\n"
            "Cheat sheet, for reference. Overview of when each applies.\n")
    before = t.box.tree()

    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "the sort filed them")
    t.ok(any(p.is_dir() and (p / ".category").exists()
             for p in (t.box.root / "Notes").iterdir()),
         "and enough of them to force a category folder, which moves them twice")

    t.box.run("undo")
    t.eq(t.box.inbox_count(), 16, "all sixteen are unfiled again")
    survivors = sorted(p.name for p in (t.box.root / "Notes").rglob("*")
                       if p.is_file() and not p.name.startswith("."))
    t.eq(survivors, sorted(f"n{i}.md" for i in range(16)),
         "under their original names, with not one stray copy left beside them")
    t.eq(set(t.box.tree()) - set(before), set(), "the tree is exactly as it was")
    t.eq(set(before) - set(t.box.tree()), set(), "with nothing lost either")


@test
def test_sorting_the_same_thing_twice_settles(t: Case) -> None:
    """A second sort must find nothing left to do, whatever was dropped in.

    Anything the sorter cannot write a header into has to get a card instead,
    or it carries nothing saying it is ours and every later run adopts it
    again — a file with no extension came out renamed a second time. A
    shortcut hit the same wall for the opposite reason: it has no spine on
    purpose, so its card has to be what says it is ours."""
    notes = t.box.root / "Notes"
    (notes / "no-extension").write_text("# No extension\n\nStill prose, though.\n")
    (notes / "plain.md").write_text("# Plain\n\nReference notes on index types.\n")
    outside = t.box.tmp / "elsewhere.md"
    outside.write_text("# Outside\n\nBelongs to somebody else.\n")
    (notes / "shortcut.md").symlink_to(outside)

    t.box.run("sort")
    first = sorted(p.name for p in notes.iterdir() if p.name != ".gitkeep")
    t.eq(t.box.inbox_count(), 0, "one sort files everything, headers and all")

    for run in range(2, 4):
        proc = t.box.run("sort")
        t.ok("nothing waiting" in proc.stdout, f"sort #{run} finds nothing left to do")
        t.eq(sorted(p.name for p in notes.iterdir() if p.name != ".gitkeep"), first,
             f"sort #{run} renamed nothing")

    doubled = [n for n in first if engine.ID_RE.match(n) or re.search(r"-\d+-\d+", n)]
    t.eq(doubled, [], "and nothing came out wearing a tag or a second collision suffix")


@test
def test_a_file_adopted_where_it_lies_stays_put(t: Case) -> None:
    """Adopting a file where it already lies is not a move.

    The sorter handed back the file's own path to mean "leave it", and the
    move then made that `name-2`: every file dropped in by hand went to -2
    and back, and a shortcut, which nothing renames afterwards, kept the -2."""
    notes = t.box.root / "Notes"
    (notes / "recipe.md").write_text("# Recipe\n\nFlour, water, salt. Reference for the bread.\n")
    try:
        (notes / "recipe-link.md").symlink_to("recipe.md")
    except OSError:
        return
    proc = t.box.run("sort")
    t.ok(not re.search(r"-2(\.|\s|$)", proc.stdout),
         f"nothing was sent to -2 and back:\n{proc.stdout[-600:]}")
    t.ok((notes / "recipe-link.md").is_symlink(), "the shortcut keeps its own name")
    t.ok(not (notes / "recipe-link-2.md").exists(), "with no -2 beside it")
    t.ok((notes / "recipe.md").is_file(), "and the note is where it was dropped")


@test
def test_undo_follows_a_file_it_had_to_rename(t: Case) -> None:
    """Undo two runs whose sources shared filenames, and both must come back.

    Putting something back can find its old name taken — by the batch the
    previous undo already restored — so it lands beside it instead. Its saved
    contents have to follow it there. They did not: they were written to the
    old name, overwriting the other file, and leaving this one still carrying
    the header the sort had stamped on it."""
    notes = t.box.root / "Notes"
    body = ("Reference note on postgres index types. B-tree is the default.\n"
            "Cheat sheet, for reference. Overview of when each applies.\n")
    for batch in range(2):
        for i in range(6):
            # Named by nobody, so each is named from its words, and they collide.
            (notes / f"untitled-{i}.md").write_text(f"{body}\nBatch {batch}.\n")
        t.box.run("sort")
        t.eq(t.box.inbox_count(), 0, f"batch {batch} filed")

    t.box.run("undo")
    t.eq(t.box.inbox_count(), 6, "one undo brings back the second batch")
    t.box.run("undo")
    t.eq(t.box.inbox_count(), 12,
         "and the second brings back the first, renamed around the collision")

    for path in notes.rglob("*.md"):
        if path.name.startswith("."):
            continue
        meta, _ = engine.parse_frontmatter(path.read_text())
        t.ok(not str(meta.get("id") or "").strip(),
             f"{path.name} came back as it was written, with no stamped id left on it")


@test
def test_restore_does_not_turn_a_note_into_work(t: Case) -> None:
    """Coming out of the archive gives something its own status back.

    Everything used to come back as `pushing`, so a filed PDF or a note would
    reappear as work with a next action — and then start being nagged for going
    quiet, which is a thing it never had."""
    (t.box.root / "Notes" / "figures.csv").write_text("quarter,total\nQ1,100\n")
    t.box.run("save", "Notes on Postgres index types: btree is the default")
    t.box.run("sort")

    for kind in ("asset", "note"):
        item = next((i for i in t.box.items() if i["kind"] == kind), None)
        if item is None:
            continue
        t.box.run("close", item["id"])
        t.box.run("back", item["id"])
        again = next(i for i in t.box.items() if i["id"] == item["id"])
        t.ok(again["status"] in ("", "—"),
             f"a {kind} comes back as itself, not as work ({again['status']!r})")
        meta, _ = engine.parse_frontmatter(
            (t.box.root / (again["path"] + ".card.md")).read_text()
            if kind == "asset" else (t.box.root / again["path"]).read_text())
        t.ok("archived" not in meta, f"the archive stamp is cleared off the {kind}")
        t.ok("origin" not in meta, f"and so is the note of where it used to live")

    # work, on the other hand, does go back to being pushed
    t.box.run("new", "project", "Ship the rewrite")
    work = next(i for i in t.box.items() if i["title"] == "Ship the rewrite")
    t.box.run("close", work["id"])
    t.box.run("back", work["id"])
    back = next(i for i in t.box.items() if i["id"] == work["id"])
    t.eq(back["status"], "pushing", "work comes back on the go")


@test
def test_a_backup_never_silently_skips_your_writing(t: Case) -> None:
    """The zip excluded any folder *named* cache or backups, at any depth.

    Somebody's own `Notes/cache/` was quietly left out of every backup — the
    worst possible failure for the one command whose whole job is not losing
    things."""
    import zipfile
    for folder in ("cache", "backups"):
        (t.box.root / "Notes" / folder).mkdir(parents=True, exist_ok=True)
        (t.box.root / "Notes" / folder / "mine.md").write_text(
            f"# Mine\n\nA real note that happens to sit in a folder called {folder}.\n")
    t.box.run("backup")

    zips = sorted((t.box.root / ".os" / "backups").glob("*.zip"))
    t.eq(len(zips), 1, "a backup was written")
    inside = set(zipfile.ZipFile(zips[0]).namelist())
    for folder in ("cache", "backups"):
        t.ok(f"Notes/{folder}/mine.md" in inside,
             f"a note in a folder called {folder} is in the backup")
    t.ok(not any(n.startswith(".os/backups/") for n in inside),
         "while .os/backups is still left out, so backups do not nest")
    t.ok(not any(n.startswith(".os/cache/") for n in inside),
         "and .os/cache too, since it is all rebuildable")


@test
def test_search_finds_things_by_every_handle(t: Case) -> None:
    """ID, title, tag, body — all of them work."""
    t.box.fill_inbox(50)
    t.box.run("sort")
    items = [i for i in t.box.items() if i["kind"] in ("project", "note")]
    sample = items[len(items) // 2]

    by_title = t.box.json("find", sample["title"][:28])
    t.gte(len(by_title), 1, f"found '{sample['title'][:28]}' by title")
    t.ok(any(h["id"] == sample["id"] for h in by_title), "the right item ranks in the results")

    by_id = t.box.json("find", sample["id"])
    t.gte(len(by_id), 1, "found it by ID")

    proc = t.box.run("open", sample["id"])
    t.ok(sample["id"] in proc.stdout or Path(proc.stdout.strip()).exists(),
         "`os open <id>` resolves to a real path")

    empty = t.box.json("find", "zzzz-nothing-matches-this-zzzz")
    t.eq(empty, [], "a query with no matches returns nothing, cleanly")


@test
def test_front_matter_survives_a_round_trip(t: Case) -> None:
    """The parser and the writer agree, including the awkward cases."""
    cases = [
        {"title": "Plain", "tags": ["a", "b"], "status": "active"},
        # a value that only looks numeric: quoted, or it round-trips as 10.1
        {"version": "10.01", "title": "Looks like a number", "tags": [], "status": "active"},
        {"title": "Colons: everywhere: here", "tags": [], "status": "—"},
        {"title": 'Quotes "inside" it', "tags": ["one-tag"], "count": 42},
        {"title": "Unicode — em dash, curly ‘quotes’, emoji 🎯", "tags": ["ünïcode"]},
        {"title": "Hash # not a comment", "flag": True, "other": False, "nothing": None},
        {"title": "Leading spaces preserved", "tags": ["a", "b", "c", "d", "e"]},
    ]
    for meta in cases:
        text = engine.compose(dict(meta), "# Body\n\nSome content.\n")
        parsed, body = engine.parse_frontmatter(text)
        for key, value in meta.items():
            t.eq(parsed.get(key), value, f"'{key}' survived the round trip")
        t.ok("Some content." in body, "the body survived")

    # malformed front matter must never throw
    for broken in ("---\nnot: [closed\n---\nbody", "---\n\n\n---\n", "---", "no front matter at all",
                   "---\n: bad key\n---\nbody"):
        meta, body = engine.parse_frontmatter(broken)
        t.ok(isinstance(meta, dict), "malformed front matter degrades to a dict")


@test
def test_a_divider_at_the_top_is_not_a_header(t: Case) -> None:
    """Any block between two `---` lines was read as a header, and the lines
    in it that were not `key: value` were dropped when the header was written
    back. A song opening with a divider lost its whole first verse to a sort."""
    for text in ("---\nThe whole first verse of my song lives here\nand the second line of it too\n"
                 "---\nwritten on the train, March\n",
                 "---\nNote: buy milk\nand eggs\n---\nthen the shopping\n"):
        meta, body = engine.parse_frontmatter(text)
        t.eq(meta, {}, "a block with a line of prose in it is not a header")
        t.eq(body, text, "so all of it is the note itself")

    notes = t.box.root / "Notes"
    (notes / "song.md").write_text(
        "---\nThe whole first verse of my song lives here\nand the second line of it too\n"
        "---\nwritten on the train, March\n")
    t.box.run("sort")
    filed = [p for p in notes.rglob("*.md") if "on the train" in p.read_text()]
    t.eq(len(filed), 1, "the song was filed")
    t.ok(filed and "The whole first verse of my song lives here\nand the second line of it too\n"
         in filed[0].read_text(), "with its first verse still in it")


@test
def test_a_header_keeps_the_lines_it_does_not_understand(t: Case) -> None:
    """hold, push, claim, release, decide, rename, close and back each wrote
    the whole header again from what the reader understood. A comment, the
    second line of a wrapped value and a nested field (a contact's phone
    number) were gone after one of them.

    And a file brought in with its own header lost every key of it."""
    t.box.run("new", "work", "Garden Shed")
    t.box.run("index")
    item = next(i for i in t.box.items() if i["title"] == "Garden Shed")
    spine = t.box.root / item["path"] / "README.md"
    extra = ("# owner is my manager, ask before changing\n"
             "summary: a long thought that got\n  wrapped onto a second line\n"
             "contacts:\n  - name: Ana\n    phone: 555-0100\n")
    spine.write_text(spine.read_text().replace("---\n", "---\n" + extra, 1))

    def spine_now() -> Path:
        t.box.run("index")
        found = next(i for i in t.box.items() if i["title"].startswith("Garden Shed"))
        return t.box.root / found["path"] / "README.md"

    for command in (["hold", "garden-shed"], ["push", "garden-shed"],
                    ["claim", "garden-shed"], ["release", "garden-shed"],
                    ["decide", "garden-shed", "Timber, not metal - rules out the cheap kit"],
                    ["rename", "garden-shed", "Garden Shed Two"],
                    ["close", "garden-shed-two"], ["back", "garden-shed-two"]):
        t.box.run(*command)
        t.ok(extra in spine_now().read_text(),
             f"`os {command[0]}` left the lines it did not write alone")
    meta, body = engine.parse_frontmatter(spine_now().read_text())
    t.eq(meta.get("status"), "pushing", "while the fields it did write are right")
    t.eq(meta.get("title"), "Garden Shed Two", "the new name among them")
    t.ok("Timber, not metal" in body, "and the decision went in below")

    outside = t.box.tmp / "acme.md"
    outside.write_text("---\nclient: ACME Corp\ndue: 2026-10-15\nbudget: 12000\n---\n\n"
                       "Need to finish the website redesign for ACME, build the landing page "
                       "and ship it by Friday. Next action: draft the wireframes.\n")
    t.box.run("save", str(outside))
    landed = [p for bucket in ("Work", "Notes") for p in (t.box.root / bucket).rglob("*.md")
              if p.is_file() and "website redesign for ACME" in p.read_text()]
    t.eq(len(landed), 1, "the file was brought in")
    if landed:
        meta, _ = engine.parse_frontmatter(landed[0].read_text())
        t.eq((meta.get("client"), meta.get("due"), meta.get("budget")),
             ("ACME Corp", "2026-10-15", 12000), "and its own header came with it")


@test
def test_check_finds_real_problems(t: Case) -> None:
    """Every check the doctor advertises actually fires."""
    t.box.fill_inbox(20)
    t.box.run("sort")

    # plant two things sharing one name, which is the only handle there is
    library = t.box.root / "Notes"
    victims = sorted(p for p in library.rglob("*.md")
                     if not p.name.endswith(".card.md")
                     and str(engine.parse_frontmatter(p.read_text())[0].get("type", "")) == "note")
    t.gte(len(victims), 2, "enough library notes to break")
    clash = library / "duplicates"
    clash.mkdir(exist_ok=True)
    (clash / ".category").write_text("")
    meta, _ = engine.parse_frontmatter(victims[0].read_text())
    (clash / victims[0].name).write_text(
        engine.compose(dict(meta), "# Stolen name\n\nDifferent words, same name.\n"))

    # plant a colliding skill name and a skill with no description
    (t.box.root / ".claude" / "skills" / "review").mkdir(parents=True, exist_ok=True)
    (t.box.root / ".claude" / "skills" / "review" / "SKILL.md").write_text(
        "---\nname: review\n---\n\nThis collides with a built-in and has no description.\n")

    # One name in two folders is said (a hint: show asks which one is meant).
    codes = {i["code"] for i in t.box.json("check", expect=1)["issues"]}
    for expected in ("same-name", "skill-name-clash", "skill-no-description"):
        t.ok(expected in codes, f"check detects {expected}")

    # --fix repairs the mechanical things without touching the judgement calls
    (t.box.root / "Notes").rename(t.box.root / "Notes_moved")
    for hook in (t.box.root / ".claude" / "hooks").iterdir():
        hook.chmod(0o644)

    t.box.run("check", "--fix", expect=1)   # still 1: the planted errors need judgement
    t.ok(not (t.box.root / "Notes").exists(),
         "--fix makes no empty folder: Notes/ comes back when something goes in it")
    for hook in (t.box.root / ".claude" / "hooks").iterdir():
        t.ok(os.access(hook, os.X_OK), f"--fix made {hook.name} executable again")


@test
def test_check_only_offers_the_fix_it_can_make(t: Case) -> None:
    """'Kitchen Refit' was filed with `domain: unsorted`. check then said to add
    the domain: line that was already there, and pointed at a --fix that
    could not fix it."""
    t.box.run("new", "work", "Kitchen refit")
    readme = next((t.box.root / "Work").rglob("README.md"))
    # Nothing ./os makes says `unsorted` any more — a thing no subject fits
    # goes under general — but an item made before then still does.
    readme.write_text(re.sub(r"^domain: .*$", "domain: unsorted", readme.read_text(),
                             count=1, flags=re.M))
    t.ok("domain: unsorted" in readme.read_text(), "an item from before says it is unsorted")
    hint = next(i for i in t.box.json("check", expect=None)["issues"]
                if i["code"] == "no-domain")
    t.ok("add a `domain:` line" not in hint["fix"], "it doesn't ask for a line that is there")
    t.ok("personal" in hint["fix"], "it names the subjects there are to pick from")
    plain = t.box.run("check", expect=None).stdout
    t.ok("--fix" not in plain, f"no --fix is offered when it would fix nothing:\n{plain}")
    (t.box.root / "Notes" / ".DS_Store").write_text("")
    t.ok("fixes everything that is safe to fix" in t.box.run("check", expect=None).stdout,
         "and it is offered when something it lists can be fixed that way")


@test
def test_index_matches_the_disk(t: Case) -> None:
    """The map is the territory."""
    t.box.fill_inbox(70)
    t.box.run("sort")
    registry = t.box.registry()

    for item in registry["items"]:
        t.ok((t.box.root / item["path"]).exists(),
             f"indexed item {item['path']} exists on disk")

    index_md = (t.box.root / "INDEX.md").read_text()
    sample = [i for i in registry["items"] if i["id"]][:25]
    for item in sample:
        # A project folder's name has spaces in it, so INDEX links to it escaped.
        t.ok(item["id"] in index_md or engine.md_link(item["id"]) in index_md,
             f"INDEX.md lists {item['id']}")

    catalog = (t.box.root / ".claude" / "CATALOG.md").read_text()
    for skill in [i for i in registry["items"] if i["kind"] == "skill"]:
        t.ok(f"/{Path(skill['path']).name}" in catalog,
             f"CATALOG.md lists /{Path(skill['path']).name}")

    t.eq(list(t.box.root.rglob("_index.md")), [],
         "no per-folder index files clutter the buckets")


@test
def test_the_surface_stays_small(t: Case) -> None:
    """Things stripped for the user's sake stay stripped."""
    for gone in ("dash", "stats", "guide", "link"):
        proc = t.box.run(gone, expect=1)
        t.ok("there is no" in proc.stderr, f"`os {gone}` is gone and says so plainly")
        t.ok("Did you mean" in proc.stderr or "lists everything" in proc.stderr,
             f"`os {gone}` points somewhere useful")
    for gone in ("GUIDE.pdf", ".os/guide.py", ".os/dashboard.html", "50_Toolkit",
                 "1_Inbox", "2_Work", "3_Areas", "4_Notes", "5_Files", "6_Journal",
                 "9_Archive", ".os/templates/log.md"):
        t.ok(not (t.box.root / gone).exists(), f"{gone} does not ship")
    for bucket in ("Work", "Notes", "Archive"):
        t.ok(not (t.box.root / bucket / "CLAUDE.md").exists(),
             f"{bucket} has no rules file of its own — they all live in AGENTS.md")


@test
def test_every_json_output_is_valid(t: Case) -> None:
    """Anything a script might parse, parses."""
    t.box.fill_inbox(15)
    t.box.run("sort")
    for args in (["tidy"], ["status"], ["index"], ["sort"], ["find", "note"],
                 ["brief"], ["check"], ["save", "a passing thought"]):
        payload = t.box.json(*args)
        t.ok(payload is not None, f"`os {' '.join(args)} --json` is valid JSON")
    # doctor exits 1 when it finds errors, so accept either outcome and just
    # insist the payload parses
    t.ok(t.box.json("doctor", expect=None) is not None, "`os doctor --json` is valid JSON")
    brief = t.box.json("brief")
    t.ok("hookSpecificOutput" in brief, "brief emits a hook-shaped payload")
    t.eq(brief["hookSpecificOutput"]["hookEventName"], "SessionStart",
         "brief declares the right hook event")


@test
def test_backup_captures_everything(t: Case) -> None:
    """A snapshot you could actually restore from."""
    import zipfile
    t.box.fill_inbox(20)
    t.box.run("sort")
    t.box.run("backup")
    zips = sorted((t.box.root / ".os" / "backups").glob("*.zip"))
    t.eq(len(zips), 1, "one snapshot was written")
    with zipfile.ZipFile(zips[0]) as zf:
        names = set(zf.namelist())
        t.ok("AGENTS.md" in names, "the rules are in the backup")
        t.ok("CLAUDE.md" in names, "so is Claude Code's pointer to them")
        t.ok(any(n.startswith("Notes/") for n in names), "content is in the backup")
        t.ok(any(n.startswith(".claude/skills/") for n in names), "the toolkit is in the backup")
        t.eq(zf.testzip(), None, "the archive is not corrupt")


@test
def test_the_folder_can_be_renamed_and_moved(t: Case) -> None:
    """Rename-safety: the engine finds its own root."""
    moved = t.box.tmp / "Somewhere Else"
    shutil.move(str(t.box.root), str(moved))
    env = dict(os.environ, NO_COLOR="1")
    env.pop("ZENITH_HOME", None)
    proc = subprocess.run([str(moved / "os"), "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(moved), env=env, timeout=120)
    t.eq(proc.returncode, 0, "the CLI still runs after the folder is renamed")
    t.ok(str(moved) in proc.stdout, "it reports its new location")

    deep = moved / "Notes"
    proc = subprocess.run([str(moved / "os"), "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(deep), env=env, timeout=120)
    t.eq(proc.returncode, 0, "it works from a subdirectory too")
    shutil.move(str(moved), str(t.box.root))


@test
def test_it_refuses_to_lose_data(t: Case) -> None:
    """Name collisions, weird filenames, and unreadable bytes."""
    inbox = t.box.root / "Notes"
    for i in range(4):
        (inbox / f"same-{i}.md").write_text("# Identical Title\n\nSame reference note every time.\n")
    (inbox / "spaces and (parens) & symbols!.md").write_text("# Odd name\n\nA note.\n")
    (inbox / "no-extension").write_text("# No extension\n\nStill a note.\n")
    (inbox / "binary.bin").write_bytes(bytes(range(256)))
    (inbox / "empty.md").write_text("")
    nested = inbox / "a folder" / "deeper"
    nested.mkdir(parents=True)
    (nested / "buried.md").write_text("# Buried note\n\nInside two folders.\n")

    planted = t.box.inbox_count()
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 1, "everything got filed but the empty file written seconds ago")
    old = time.time() - 120
    os.utime(inbox / "empty.md", (old, old))
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "and once it has sat there a while, that too")

    def files_containing(needle: str) -> list[Path]:
        found = []
        for p in t.box.root.rglob("*"):
            if not p.is_file() or p.is_symlink() or p.name in t.box.GENERATED_FILES:
                continue
            if p.suffix.lower() not in engine.TEXT_SUFFIXES:
                continue
            try:
                if needle in p.read_text(encoding="utf-8", errors="replace"):
                    found.append(p)
            except OSError:
                pass
        return found

    same = files_containing("Same reference note every time.")
    t.eq(len(same), 4, "all four identical notes survived, as four distinct files")
    t.eq(len({p.name for p in same}), 4, "each got a distinct filename")
    t.gte(len(files_containing("Buried note")), 1, "a nested folder was kept whole")
    t.ok(any(p.name.endswith(".bin") for p in t.box.root.rglob("*.bin")),
         "binary files are preserved, not parsed")


@test
def test_a_big_file_is_filed_whole(t: Case) -> None:
    """Filing or renaming a file writes its header back — and wrote back only
    the first 400,000 characters of it. A 3 MB thesis came out 400 KB.

    Files over 512 KB were not copied aside either, so undo put it back
    under its old name still cut short."""
    notes = t.box.root / "Notes"
    thesis = notes / "thesis-draft.md"
    thesis.write_text("# Thesis draft\n\n" + "The chapter on soil moisture goes on. " * 80_000
                      + "\nTHE VERY LAST LINE\n", encoding="utf-8")
    original = thesis.read_bytes()

    def the_thesis() -> list:
        return [p for p in notes.rglob("*.md") if b"soil moisture" in p.read_bytes()]

    t.box.run("sort")
    filed = the_thesis()
    t.eq(len(filed), 1, "the thesis was filed")
    t.ok(filed[0].read_bytes().endswith(b"THE VERY LAST LINE\n"),
         "and it still ends where it ended")
    t.gte(filed[0].stat().st_size, len(original), "nothing was cut off it")

    t.box.run("undo")
    t.eq(thesis.read_bytes() if thesis.exists() else b"", original,
         "undoing the sort puts back every byte of it")

    t.box.run("sort")
    ident = the_thesis()[0].stem
    t.box.run("rename", ident, "My thesis")
    renamed = the_thesis()
    t.ok(renamed and renamed[0].read_bytes().endswith(b"THE VERY LAST LINE\n"),
         "a rename keeps all of it too")
    t.box.run("undo")
    back = notes / f"{ident}.md"
    t.ok(back.exists() and back.read_bytes().endswith(b"THE VERY LAST LINE\n"),
         "and so does undoing the rename")

    # Past engine.PROSE_CAP it is not a note at all. A 468 MB server log given
    # a header took 4 GB of memory, and a second copy of it sat in the undo cache.
    log = t.box.root / "server.txt"
    line = b"2026-09-29 12:00:01 GET /health 200\n"
    log.write_bytes(line * (engine.PROSE_CAP // len(line) + 10))
    size = log.stat().st_size
    t.box.run("sort")
    kept = [p for p in t.box.root.rglob("server*.txt")]
    t.ok(kept and kept[0].stat().st_size == size and kept[0].read_bytes()[:len(line)] == line,
         f"a log too long to be a note is kept byte for byte ({kept})")
    t.ok(kept and kept[0].with_name(kept[0].name + ".card.md").exists(), "with a card beside it")
    copies = [p for p in (t.box.root / ".os" / "cache").rglob("*")
              if p.is_file() and p.stat().st_size >= size]
    t.eq(copies, [], "and no copy of it is kept for undo")


@test
def test_tidy_reports_decay(t: Case) -> None:
    """The anti-decay pass sees what is rotting."""
    t.box.run("new", "project", "Ancient forgotten project", "--domain", "engineering")
    t.box.run("index")
    project = next(i for i in t.box.items() if i["kind"] == "project")
    spine = t.box.root / project["path"] / "README.md"
    meta, body = engine.parse_frontmatter(spine.read_text())
    meta["updated"] = "2020-01-01"
    meta["created"] = "2020-01-01"
    spine.write_text(engine.compose(meta, body))

    (t.box.root / "Notes" / "waiting.md").write_text("# Something waiting\n\nUnfiled.\n")

    report = t.box.json("review")
    t.gte(len(report["archive_candidates"]), 1, "a long-dead project is proposed for archiving")
    t.ok(any(c["id"] == project["id"] for c in report["archive_candidates"]),
         "the right project was flagged")
    t.gte(len(report["unfiled"]), 1, "the unfiled item dropped in by hand is reported")
    t.ok("score" in report, "the review carries a health score")


@test
def test_a_log_line_written_by_hand_counts_as_a_touch(t: Case) -> None:
    """AGENTS.md and /wrapup have the Log line written by hand, and only ./os
    commands change `updated:`. Work done today was still called quiet."""
    t.box.run("new", "work", "Build the garden shed", "--domain", "personal")
    t.box.run("index")
    project = next(i for i in t.box.items() if i["title"] == "Build the garden shed")
    spine = t.box.root / project["path"] / "README.md"
    meta, body = engine.parse_frontmatter(spine.read_text())
    meta["updated"] = meta["created"] = "2020-01-01"
    spine.write_text(engine.compose(meta, body).rstrip("\n")
                     + "\n- 2020-01-01 — cleared the ground\n")

    def quiet() -> bool:
        report = t.box.json("review")
        return any(c["id"] == project["id"]
                   for c in report["stale"] + report["archive_candidates"])

    t.ok(quiet(), "nothing since 2020 is quiet")
    spine.write_text(spine.read_text().rstrip("\n")
                     + f"\n- {engine.today()} — ordered the timber\n")
    t.ok(not quiet(), "a Log line dated today means it was touched today")
    t.ok("haven't touched" not in t.box.run("status").stdout, "so ./os doesn't call it quiet")
    t.ok("today" in t.box.run("show", project["id"]).stdout, "and show says it was touched today")


@test
def test_it_repairs_itself_after_a_bad_unzip(t: Case) -> None:
    """A zip extracted on Windows arrives with no executable bit.

    `./os` then fails with a bare "permission denied" and the hooks die
    silently, which is the worst possible first five seconds. Running it by any
    other route has to put that right permanently."""
    launcher = t.box.root / "os"
    hooks = sorted((t.box.root / ".claude" / "hooks").iterdir())
    for f in [launcher, *hooks]:
        f.chmod(0o644)
    t.ok(not os.access(launcher, os.X_OK), "the executable bit really is gone")

    def direct(*args: str) -> subprocess.CompletedProcess:
        """Run it the way somebody would have to: through the interpreter."""
        return subprocess.run([sys.executable, str(t.box.root / ".os" / "engine.py"), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(t.box.root),
                              env=dict(os.environ, ZENITH_HOME=str(t.box.root), NO_COLOR="1"))

    t.eq(direct("status").returncode, 0, "it still runs when it cannot be executed directly")

    t.ok(os.access(launcher, os.X_OK), "and it put ./os back to runnable")
    for hook in hooks:
        t.ok(os.access(hook, os.X_OK), f"and {hook.name} too, so the AI still gets its brief")

    t.box.run("status")   # the normal path works again

    # every route in repairs it, including `bash os`
    for route in (["bash", str(launcher), "status"],
                  [sys.executable, str(t.box.root / ".os" / "engine.py"), "status"]):
        for f in [launcher, *hooks]:
            f.chmod(0o644)
        subprocess.run(route, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(t.box.root),
                       env=dict(os.environ, ZENITH_HOME=str(t.box.root), NO_COLOR="1"))
        t.ok(os.access(launcher, os.X_OK), f"`{route[0].split('/')[-1]} …` repairs it too")

    # and the health check still carries the finding, for a copy it cannot write to
    doctor_codes = set(engine.Doctor.__dict__)   # the check exists as a named finding
    t.ok("run" in doctor_codes, "the health check is intact")
    t.ok("os-not-executable" in (t.box.root / ".os" / "engine.py").read_text(),
         "and still reports the case where the bit cannot be restored")

    # and `os help` tells someone who cannot run anything at all
    hint = t.box.run("help").stdout
    t.ok("bash os" in hint, "help gives a way out that needs no executable bit")


@test
def test_a_shortcut_cannot_break_the_folder(t: Case) -> None:
    """Someone drags an alias in. Two ways that used to end badly.

    Following a link back into the folder it sat in had the sorter write a
    README.md *through* it, leaving a file the scanner then ignored forever.
    And a link pointing outside the folder crashed every command that rebuilt
    the index, because resolving it produced a path that is not under the root."""
    inbox = t.box.root / "Notes"

    # 1. a link that points back at the folder it lives in
    (inbox / "loop").symlink_to("../Notes")
    t.box.run("sort")
    link = next((p for p in inbox.iterdir() if p.is_symlink()), None)
    t.ok(link is not None, "the link is still a link, not a copy of what it points at")
    t.ok(link.with_name(link.name + ".card.md").exists() if link else False,
         "and it was filed like anything else — named, with a card")
    t.ok(not (inbox / "README.md").exists(),
         "no stray README.md is written back through it")

    # 2. a link that points somewhere outside the folder entirely
    outside = t.box.tmp / "somewhere-else.md"
    outside.write_text("# Outside\n\nContent about the deadline.\n")
    (inbox / "shortcut.md").symlink_to(outside)
    t.box.run("sort")

    for command in (["status"], ["index"], ["check"], ["find", "shortcut"], ["tidy"]):
        t.box.run(*command, expect=None)     # any crash fails the run outright
    t.box.run("index")
    for item in t.box.items():
        t.ok(not item["path"].startswith("/"),
             f"every recorded path stays inside the folder ({item['path']})")

    t.ok(outside.exists() and "Outside" in outside.read_text(),
         "and the thing the link points at is left alone")

    # filing is still settled: a second pass moves nothing
    t.ok("nothing waiting" in t.box.run("sort").stdout, "and sorting is stable")


@test
def test_a_shortcut_is_never_walked_through(t: Case) -> None:
    """A link is one item. What it points at belongs to whoever put it there.

    `is_dir()` and `is_file()` both follow a link, so the walker used to step
    straight through one: a shortcut to a folder carrying a `.category` marker
    was descended into, and `os sort` then *moved the files out of the person's
    own directory* into this one. A shortcut to a file was read as if it were
    that file, so the item showed up as a duplicate of itself — and the next
    sort would have rewritten the original's front matter through the link."""
    outside = t.box.tmp / "not-ours"
    (outside / "deep").mkdir(parents=True)
    (outside / ".category").write_text("")                 # looks like a group folder
    (outside / "invoice.md").write_text("# Invoice\n\nBudget and revenue for Q3.\n")

    (t.box.root / "Notes" / "linked").symlink_to(outside)
    t.box.run("sort")

    t.ok((outside / "invoice.md").exists(),
         "a file behind a shortcut is left in the folder it actually lives in")
    t.ok(not any(p.name == "invoice.md" for p in (t.box.root / "Notes").rglob("*")),
         "and is never adopted as an item of ours")

    # 2. a shortcut that points at a sibling is not a copy of it
    inbox = t.box.root / "Notes"
    (inbox / "real.md").write_text("# Real\n\nNotes on the database schema.\n")
    (inbox / "alias").symlink_to("../Notes")
    report = t.box.json("check")
    dupes = [i for i in report["issues"] if i["code"] == "duplicate-content"]
    t.eq(dupes, [], "a shortcut is not reported as a duplicate of what it points at")


@test
def test_a_saved_folder_keeps_its_shortcuts_as_shortcuts(t: Case) -> None:
    """`./os save <folder>` copied what every shortcut inside pointed at.

    A shortcut back to the folder itself, common in code projects, was copied
    into itself over and over, into .os/cache, until the disk filled up."""
    looped = t.box.tmp / "looped"
    looped.mkdir()
    (looped / "README.md").write_text("# Looped\n\nNotes on the build setup.\n")
    (looped / "self").symlink_to(".")
    started = time.time()
    t.box.run("save", str(looped))
    t.lte(time.time() - started, 60, "saving it finishes, and quickly")
    kept = [p for p in t.box.root.rglob("self") if p.is_symlink()]
    t.eq(len(kept), 1, "the shortcut came in as one shortcut, not as copies")
    copies = [p for p in t.box.root.rglob("README.md") if "Looped" in p.read_text()]
    t.eq(len(copies), 1, "and the folder it points at was brought in once")
    for command in (["status"], ["check"], ["sort"], ["backup"]):
        proc = t.box.run(*command, expect=None)
        t.ok("Traceback" not in proc.stderr,
             f"`os {command[0]}` copes with it:\n{proc.stderr[-400:]}")

    # A shortcut to something outside the saved folder came in pointing at
    # nothing, or back at the original: the words weren't in the folder, and
    # deleting the original lost them. What it points at comes in instead.
    book = t.box.tmp / "Book Draft"
    shared = t.box.tmp / "shared"
    book.mkdir()
    shared.mkdir()
    (shared / "chapter1.md").write_text("# Chapter one\n\nThe lighthouse keeper's daughter.\n")
    (shared / "maps").mkdir()
    (shared / "maps" / "harbour.md").write_text("The harbour map, drawn by hand.\n")
    (book / "README.md").write_text("# Book draft\n\nNotes for the novel.\n")
    (book / "chapter1.md").symlink_to("../shared/chapter1.md")
    (book / "maps").symlink_to(shared / "maps")
    (book / "cover.md").symlink_to("README.md")
    said = t.box.run("save", str(book)).stdout
    shutil.rmtree(shared)
    came = next(p for p in t.box.root.rglob("chapter1.md") if "book" in str(p).lower())
    t.ok(not came.is_symlink() and "lighthouse keeper" in came.read_text(),
         f"a shortcut to a file outside comes in as the file\n{said}")
    maps = came.parent / "maps" / "harbour.md"
    t.ok(maps.is_file() and not (came.parent / "maps").is_symlink() and "drawn by hand" in maps.read_text(),
         "and one to a folder outside as the folder")
    cover = came.parent / "cover.md"
    t.ok(cover.is_symlink() and "Notes for the novel" in cover.read_text(),
         "one to something inside it stays a shortcut, and still works")


@test
def test_a_blank_name_matches_nothing(t: Case) -> None:
    """`./os done ""` must not archive whatever the scanner reached first.

    An empty name once compared equal to the first thing with no name of its
    own — and the argument guard only checked that *an* argument was there,
    not that it said anything."""
    t.box.run("save", "Ship the billing rewrite by Friday, migrate the schema")
    before = sorted(p.name for p in (t.box.root / ".claude" / "skills").iterdir())

    for command in ("show", "open", "edit", "done", "back"):
        proc = t.box.run(command, "", expect=1)
        t.ok("which one" in (proc.stdout + proc.stderr).lower(),
             f"`os {command} \"\"` asks which one instead of picking for you")
        proc = t.box.run(command, "   ", expect=1)
        t.ok("which one" in (proc.stdout + proc.stderr).lower(),
             f"`os {command} \"   \"` too")

    t.eq(sorted(p.name for p in (t.box.root / ".claude" / "skills").iterdir()), before,
         "and nothing was archived out from under the toolkit")


@test
def test_a_name_collision_keeps_its_name(t: Case) -> None:
    """A name is the only handle there is, so a collision must not garble it.

    `unique_path` splits a file on its last dot to add a suffix. A directory
    has no extension, whatever dots are in its name, so splitting there would
    rewrite the middle of an item folder's name instead of the end of it.
    Archiving onto an occupied spot has to land beside it, still readable."""
    t.box.run("new", "project", "Ship the billing rewrite")
    t.box.run("sort")
    item = next(i for i in t.box.items() if i["title"] == "Ship the billing rewrite")
    folder = t.box.root / item["path"]
    t.ok(folder.is_dir(), "the project is a folder")

    # something is already sitting where the archive wants to put it
    landing = t.box.root / "Archive" / engine.today()[:4] / "Work" / folder.name
    landing.mkdir(parents=True)
    (landing / "keep.txt").write_text("someone else's")

    t.box.run("done", item["id"])
    moved = [p for p in landing.parent.iterdir() if p.name != landing.name]
    t.eq(len(moved), 1, "the archived copy lands beside it rather than on top of it")
    # Project folders are spelled as names ("Ship the Billing Rewrite"), so the
    # readable check is on the slug of what landed, not on the raw folder name.
    t.ok(engine.slugify(moved[0].name).startswith("ship-the-billing-rewrite"),
         f"and still starts with its own name (got {moved[0].name!r})")
    t.ok((landing / "keep.txt").exists(), "nothing was overwritten")


@test
def test_undo_puts_back_a_shortcut_that_dangles(t: Case) -> None:
    """`exists()` follows a link, so a shortcut whose target is gone reads as
    "not there" — and undo used to leave it behind in whichever bucket the sort
    had put it in, reporting that it could not be put back."""
    # A shortcut is filed in Notes, so one in Work moves; and named so the sort
    # has to rename it too: one already called what the sort would call it is
    # filed where it lies, and nothing moves.
    inbox = t.box.root / "Work"
    (inbox / "Old Alias").symlink_to(t.box.tmp / "deleted-long-ago")
    t.ok(not (inbox / "Old Alias").exists() and (inbox / "Old Alias").is_symlink(),
         "the shortcut dangles, as an old alias does")

    t.box.run("sort")
    t.ok(not (inbox / "Old Alias").is_symlink(), "sort files it")

    proc = t.box.run("undo")
    t.ok((inbox / "Old Alias").is_symlink(), "undo puts the shortcut back in the inbox")
    t.ok("couldn't put this one back" not in proc.stdout,
         "and does not report it as lost")


@test
def test_a_guess_it_was_unsure_about_comes_back(t: Case) -> None:
    """The sorter flags what it could not place confidently.

    That flag was only ever written onto folder-shaped items, and nothing
    surfaced it afterwards — so "I wasn't sure about this" was said once and
    then lost. It has to survive into the file and reappear in `os tidy`."""
    t.box.run("save", "qwerty zxcvb")                      # nothing to go on
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")

    unsure = [i for i in t.box.items() if "needs-review" in (i["flags"] or [])]
    t.gte(len(unsure), 1, "the low-confidence item carries the flag on disk")
    confident = [i for i in t.box.items()
                 if i["kind"] == "project" and "needs-review" not in (i["flags"] or [])]
    t.gte(len(confident), 1, "and a clear one does not")

    report = t.box.json("tidy")
    t.gte(len(report["unsure"]), 1, "`os tidy` reports what it was unsure about")
    t.ok(any(u["id"] == unsure[0]["id"] for u in report["unsure"]),
         "naming the same item")
    t.ok("sure" in t.box.run("tidy").stdout.lower(),
         "and says so in the report a person reads")

    # the capture timestamp is scaffolding and must not survive filing
    for item in t.box.items():
        path = t.box.root / item["path"]
        spine = path / "README.md" if path.is_dir() else path
        if spine.exists() and spine.suffix == ".md":
            meta, _ = engine.parse_frontmatter(spine.read_text())
            t.ok("saved" not in meta, f"{item['path']} kept a `saved:` timestamp")


@test
def test_it_does_not_quietly_eat_the_disk(t: Case) -> None:
    """Three things here grow on their own. All three have to stay bounded."""
    # 1. the undo cache keeps content aside so undo can restore what a file said
    t.box.fill_inbox(40)
    t.box.run("sort")
    for i in range(25):
        t.box.run("save", f"a passing thought number {i} about the deadline")
    runs = list((t.box.root / ".os" / "cache" / "undo").glob("*"))
    keep = json.loads((t.box.root / ".os" / "config.json").read_text())["behaviour"]["keep_undo_steps"]
    t.ok(len(runs) <= keep + 1, f"the undo cache is pruned to ~{keep} runs (found {len(runs)})")

    # 2. backups are full copies, so a low ceiling matters more than a long history
    behaviour = json.loads((t.box.root / ".os" / "config.json").read_text())["behaviour"]
    t.ok(behaviour["keep_backups"] <= 3,
         f"backups keep a short history by default (found {behaviour['keep_backups']})")
    for _ in range(6):
        t.box.run("backup")     # fast enough that the second-resolution stamps collide
    zips = list((t.box.root / ".os" / "backups").glob("*.zip"))
    t.eq(len(zips), behaviour["keep_backups"], "and older ones really are dropped")
    t.ok(len({z.name for z in zips}) == len(zips),
         "backups made in the same second do not overwrite each other")
    newest = max(z.stat().st_mtime for z in zips)
    t.ok(all(newest - z.stat().st_mtime < 60 for z in zips),
         "the ones kept are the newest, whatever they are named")
    said = t.box.run("backup").stdout
    t.ok("in all" in said, "`os backup` says what the whole set costs, not just the new one")

    # 3. the index is derived and rebuilt, so what matters is that its cost per
    #    item is small and flat — not that it is smaller than the content, which
    #    it never will be for a folder full of one-line notes
    items = len([i for i in t.box.items() if i["bucket"] != engine.TOOLKIT])
    index = ((t.box.root / "INDEX.md").stat().st_size
             + (t.box.root / ".os" / "registry.json").stat().st_size)
    per_item = index / max(items, 1)
    t.ok(per_item < 1_500,
         f"the index costs {per_item:.0f} bytes an item, so 1,000 items is under 1.5 MB")


@test
def test_every_kind_of_finding_stays_visible(t: Case) -> None:
    """Forty near-duplicates must not push a one-off warning off the screen."""
    for i in range(30):
        (t.box.root / "Notes" / f"Untitled {i}.md").write_text(
            "# Client relationships\n\nReference material about the client.\n")
    t.box.run("sort")
    (t.box.root / "Notes" / ".DS_Store").write_text("")

    shown = t.box.run("check", expect=None).stdout
    codes = {i["code"] for i in t.box.json("check", expect=None)["issues"]}
    t.gte(len(codes), 2, "the folder has several different kinds of finding")
    for code in codes:
        sample = next(i for i in t.box.json("check", expect=None)["issues"]
                      if i["code"] == code)
        t.ok(sample["message"][:40] in shown,
             f"'{code}' is visible in the report, not crowded out")
    repeated = [c for c in codes
                if len([i for i in t.box.json("check", expect=None)["issues"]
                        if i["code"] == c]) > 4]
    if repeated:
        t.ok("more like" in shown, f"repeats of {repeated[0]} are rolled up by kind")


@test
def test_ignored_code_caches_are_not_clutter(t: Case) -> None:
    """A code project makes __pycache__ every run. One project's check listed
    eighteen of them as junk although its .gitignore already covered them."""
    root = t.box.root
    cache = root / "Work" / "Bot" / "src" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "x.pyc").write_text("")
    codes = lambda: [i["path"] for i in t.box.json("check", expect=None)["issues"]
                     if i["code"] == "clutter"]
    t.ok(any("__pycache__" in p for p in codes()),
         "outside a code project, a cache folder is still reported")
    # The project's own .gitignore, not the folder's: every folder now has a
    # history whose .gitignore lists __pycache__/ (review, 2026-09-30).
    (root / "Work" / "Bot" / ".gitignore").write_text("__pycache__/\n")
    t.ok(not any("__pycache__" in p for p in codes()),
         "once the project ignores it, it is the project's own business")


@test
def test_bookkeeping_never_becomes_content(t: Case) -> None:
    """A folder full of "save — 1 change" buries the work it is supposed to hold.

    Every operation is already in state.json. None of it may leak into anything
    a person opens — not into a note, not into a project's README."""
    for what in ("Redesign the pricing page before the launch on the 14th",
                 "Move the billing job off the old queue",
                 "Write up how the release actually goes"):
        t.box.run("save", what)
    t.box.run("new", "project", "Ship the mobile app")
    t.box.run("sort")
    t.box.run("undo")

    history = json.loads((t.box.root / ".os" / "state.json").read_text())["history"]
    t.gte(len(history), 4, "state.json keeps the full operational record")

    written = [p for bucket in ("Work", "Notes")
               for p in (t.box.root / bucket).rglob("*.md")]
    t.ok(written, "and the person's own files are there to check")
    for path in written:
        text = path.read_text()
        for machine in ("change(s)", "step(s)", "— 1 change", "reverted"):
            t.ok(machine not in text,
                 f"no machine chatter reached {path.name} ({machine!r})")


@test
def test_everything_new_can_be_taken_back(t: Case) -> None:
    """`os undo` promises to reverse the last thing it did. `os new` did not.

    Worse, undo reported "0 change(s) reversed" as a success, so the folder
    claimed to have undone something while the thing sat there untouched."""
    cases = [("work", "Ship the mobile app", "Work"),
             ("ongoing", "Keep the tests green", "Work"),
             ("note", "A hand made note", "Notes"),
             ("skill", "Weekly digest", ".claude/skills/weekly-digest"),
             ("agent", "Summariser", ".claude/agents/summariser.md")]

    def contents(where: str):
        target = t.box.root / where
        if where.startswith(".claude"):
            return [where] if target.exists() else []
        return [p.name for p in target.iterdir() if p.name != ".gitkeep"]

    for kind, title, where in cases:
        t.box.run("new", kind, title)
        t.ok(contents(where), f"`os new {kind}` made something")
        t.box.run("undo")
        t.eq(contents(where), [], f"`os undo` takes back `os new {kind}`")

    t.eq(list((t.box.root / "Work").glob("*/")), [],
         "no empty folder is left behind")

    # and when there is genuinely nothing left, it must not claim success
    t.box.run("new", "project", "Temporary thing")
    made = next(i for i in t.box.items() if i["kind"] == "project")
    shutil.rmtree(t.box.root / made["path"])
    proc = t.box.run("undo", expect=1)
    t.ok("nothing left to reverse" in proc.stdout,
         "undo says so instead of reporting a success it did not have")


@test
def test_a_folder_of_photos_is_not_a_note(t: Case) -> None:
    """What a folder holds decides what it is, not the fact it is a folder."""
    photos = t.box.tmp / "holiday-photos" / "2026"
    photos.mkdir(parents=True, exist_ok=True)
    (photos / "a.jpg").write_bytes(b"\xff\xd8\xff")
    (photos.parent / "b.jpg").write_bytes(b"\xff\xd8\xff")

    docs = t.box.tmp / "reference-docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "a.md").write_text("Reference material on typography and layout. A cheat sheet.\n")

    mixed = t.box.tmp / "mixed-bag"
    mixed.mkdir(parents=True, exist_ok=True)
    (mixed / "chart.png").write_bytes(b"\x89PNG")
    (mixed / "notes.md").write_text("Notes about the quarter and the revenue numbers.\n")

    for folder in (photos.parent, docs, mixed):
        t.box.run("save", str(folder))

    placed = {i["title"]: i for i in t.box.items() if i["kind"] in ("asset", "note")}
    photos_item = next(i for k, i in placed.items() if "holiday" in k.lower())
    t.eq(photos_item["kind"], "asset", "a folder with nothing readable in it is files")
    t.eq(photos_item["bucket"], "Notes",
         "and it shelves with everything else you look up later")
    for key in ("reference", "notes about", "mixed"):
        item = next((i for k, i in placed.items() if key in k.lower()), None)
        if item:
            t.eq(item["kind"], "note", f"a folder with prose in it is still a note ({key})")


@test
def test_two_things_cannot_quietly_share_one_name(t: Case) -> None:
    """A name is a handle only while it points at one thing.

    Copies, restores from a backup and hand edits can all leave two things
    called the same. Nothing here may pick one of them silently: the clash is
    on the report, a command given the name asks which one, and both are
    still on disk afterwards. It was an error whose fix, ./os sort, fixed
    nothing, so ./os said "needs fixing" for good; the same name in two
    folders is fine now (settled 2026-09-30), and said as a hint."""
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    t.box.run("save", "Reference notes on postgres indexes")
    original = next(i for i in t.box.items() if i["kind"] == "project")
    origin_spine = t.box.root / original["path"] / "README.md"

    shelf = t.box.root / "Work" / "duplicates"
    shelf.mkdir(parents=True, exist_ok=True)
    (shelf / ".category").write_text("")
    clash = shelf / Path(original["path"]).name
    clash.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origin_spine, clash / "README.md")

    t.box.run("index")
    issues = t.box.json("check", expect=None)["issues"]
    same = [i for i in issues if i["code"] == "same-name"]
    t.ok(same and same[0]["level"] == "hint", "the clash is reported, not swallowed")
    shown = t.box.run("show", Path(original["path"]).name, expect=2)
    t.ok(original["path"] in shown.stderr and "duplicates/" in shown.stderr,
         f"and a command given the name asks which one:\n{shown.stderr}")

    # --fix repairs the mechanical; which of two things keeps a name is not that
    t.box.run("check", "--fix", expect=None)
    t.ok((clash / "README.md").exists(), "the copy is still there — nothing was deleted")
    t.ok(origin_spine.exists(), "and so is the original")
    codes = {i["code"] for i in t.box.json("check", expect=None)["issues"]}
    t.ok("same-name" in codes, "--fix does not paper over a judgement call")

    # and no file anywhere went back to claiming a number of its own
    for folder in ("Work", "Notes", "Archive"):
        for path in (t.box.root / folder).rglob("*.md"):
            meta, _ = engine.parse_frontmatter(path.read_text(encoding="utf-8"))
            t.ok("id" not in meta, f"{path.name} carries no id: line")


@test
def test_a_dropped_in_skill_keeps_its_name(t: Case) -> None:
    """Skills and helpers are found by name, so the name must survive filing."""
    folder = t.box.root / "Notes" / "some-folder-name"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        "---\nname: weekly-thing\ndescription: Does the weekly thing. Use when the "
        "user says do the weekly thing.\n---\n\n# Weekly thing\n\n## Do this\n1. it\n")
    (t.box.root / "Notes" / "whatever.md").write_text(
        "---\nname: summariser\ndescription: Summarises long documents. Use when the "
        "user asks for a summary.\ntools: Read, Grep\n---\n\nYou summarise things.\n")
    t.box.run("sort")

    t.ok((t.box.root / ".claude" / "agents" / "summariser.md").exists(),
         "a helper is filed under its `name:`, not its opening sentence")
    skills = [p.name for p in (t.box.root / ".claude" / "skills").iterdir() if p.is_dir()]
    t.ok("weekly-thing" in skills,
         f"a skill keeps the name people will type ({skills})")
    t.eq([i for i in t.box.json("check", expect=None)["issues"]
          if i["level"] == "error"], [], "and both are valid")


@test
def test_search_returns_your_work_not_the_scaffolding(t: Case) -> None:
    """Two ways search used to hand back things nobody was looking for."""
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    t.box.run("new", "project", "Ship the mobile app")

    def numbered(*args: str) -> list:
        return [h for h in t.box.json("find", *args) if h["id"]]

    # 1. every project carries the same blueprint prompts, in HTML comments.
    #    Searching their words matched every project in the folder.
    for phrase in ("specific", "argue", "blueprint"):
        for hit in numbered(phrase):
            body = (t.box.root / hit["path"]).read_text() if (t.box.root / hit["path"]).is_file() \
                else (t.box.root / hit["path"] / "README.md").read_text()
            visible = re.sub(r"<!--.*?-->", "", body, flags=re.S)
            t.ok(phrase in visible.lower(),
                 f"'{phrase}' matched {hit['id']} only inside a template comment")

    # 2. skills and helpers are the machinery, not the person's work
    for hit in numbered("the"):
        t.ok(hit["kind"] not in ("skill", "agent", "hook"),
             f"search returned {hit['kind']} '{hit['title']}' unasked")
    everything = t.box.json("find", "the")
    t.ok(not any(h["kind"] in ("skill", "agent", "hook") for h in everything),
         "no skills or helpers in a plain search")
    # ...but they are reachable on purpose
    skills = t.box.json("find", "tidy", "--kind", "skill")
    t.ok(any(h["kind"] == "skill" for h in skills), "--kind skill still finds them")

    # 3. and a snippet never shows template scaffolding
    for hit in t.box.json("find", "pricing"):
        t.ok("<!--" not in hit["snippet"] and "-->" not in hit["snippet"],
             f"snippet leaked a comment: {hit['snippet'][:50]!r}")


@test
def test_the_index_stays_in_a_findable_order(t: Case) -> None:
    """Nothing is numbered, so the list is alphabetical — and has to really be.

    A list of a hundred and forty things is only usable if you can run a finger
    down it. Past a dozen per folder the sorter makes category sections, and
    each section is its own table, so check each table rather than the file."""
    for junk in ("—", "", "abc", "2.", ".4", None, "W.04"):
        engine.id_order(junk)   # the leftover sort key must never raise

    t.box.fill_inbox(140)
    t.box.run("sort")

    tables, current = [], []
    for line in (t.box.root / "INDEX.md").read_text().split("\n"):
        row = re.match(r"^\| \[(.+?)\]\([^)]*\) \|", line)
        if row:
            current.append(row.group(1).lower())
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)

    t.gte(sum(len(x) for x in tables), 100, "enough items listed to be worth checking")
    t.gte(len(tables), 1, "INDEX.md has tables in it")
    out_of_order = [table[:6] for table in tables if table != sorted(table)]
    t.eq(out_of_order, [], "every table in INDEX.md is in alphabetical order")


@test
def test_one_thing_is_only_made_once(t: Case) -> None:
    """The reported bug: `save` files it, then `new project` makes a second one.

    `save` decides for itself that a sentence reading like work is a project.
    Anything that would create a second copy has to say so first."""
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    projects = [i for i in t.box.items() if i["kind"] == "project"]
    t.eq(len(projects), 1, "the save made exactly one project")

    # the shorter title sits inside the longer one — lengths differ a lot, and a
    # plain similarity ratio would never catch it
    refused = t.box.run("new", "project", "Redesign the pricing page", expect=1)
    t.ok("you already have" in refused.stderr, "`os new` refuses the near-duplicate")
    t.ok(projects[0]["id"] in refused.stderr, "and names the one that already exists")
    t.ok("--anyway" in refused.stderr, "and says how to override it")
    t.eq(len([i for i in t.box.items() if i["kind"] == "project"]), 1, "nothing was created")

    # saying you mean it works, and does not eat the title
    t.box.run("new", "project", "Redesign the pricing page", "--anyway")
    made = [i for i in t.box.items() if i["kind"] == "project"]
    t.eq(len(made), 2, "--anyway lets a deliberate second one through")
    t.ok(not any("anyway" in i["title"].lower() for i in made),
         f"and --anyway is not swallowed into the title ({[i['title'] for i in made]})")

    # different work is never blocked
    for title in ("Ship the mobile app", "Hire a designer", "Fix the CI pipeline"):
        t.box.run("new", "project", title)
    t.eq(len([i for i in t.box.items() if i["kind"] == "project"]), 5,
         "genuinely different things go straight through")

    # save warns but must never refuse — losing a thought is worse than a double
    saved = t.box.run("save", "Ship the mobile app soon")
    t.ok("looks like the same thing" in saved.stdout, "a near-duplicate save warns")
    t.ok("Ship the mobile app" in saved.stdout, "and names what it clashes with")
    t.eq(t.box.inbox_count(), 0, "but it still saved it — capture never refuses")


@test
def test_saved_work_that_names_work_is_its_next_step(t: Case) -> None:
    """Saving "Kitchen renovation: call the plumber on Monday" made a second
    item, `Kitchen Renovation-2`, under a name one already had."""
    t.box.run("new", "work", "Kitchen Renovation")
    before = sorted(p.name for p in (t.box.root / "Work").iterdir())
    said = t.box.run("save", "Kitchen renovation: call the plumber on Monday about moving the sink")
    t.eq(sorted(p.name for p in (t.box.root / "Work").iterdir()), before, "no second item is made")
    t.ok("Kitchen Renovation" in said.stdout, "it says which work the words went to")
    readme = (t.box.root / "Work" / "Kitchen Renovation" / "README.md").read_text()
    t.ok("- [ ] Call the plumber on Monday about moving the sink" in readme,
         "they are its next action, without the name in front")
    t.eq(t.box.inbox_count(), 0, "and nothing is left unfiled")
    t.box.run("undo")
    t.ok("plumber" not in (t.box.root / "Work" / "Kitchen Renovation" / "README.md").read_text(),
         "undo takes the line back out")


@test
def test_everyday_ways_of_naming_work_reach_it(t: Case) -> None:
    """Only "Name: step" reached the work. "Name - step", "Name, step", the
    bare name, a plural and a name typed without its accent each made a second
    item called the same thing, because the words read as a note."""
    for kind, name in (("work", "Kitchen Renovation"), ("work", "Café Plans"),
                       ("ongoing", "Garden Upkeep")):
        t.box.run("new", kind, name)
    before = sorted(p.name for p in (t.box.root / "Work").iterdir())
    for words, where, step in (
            ("Kitchen Renovation - call the electrician", "Kitchen Renovation", "Call the electrician"),
            ("Kitchen renovation, buy paint", "Kitchen Renovation", "Buy paint"),
            ("Kitchen renovations: fix the sink", "Kitchen Renovation", "Fix the sink"),
            ("Cafe Plans: ring the supplier", "Café Plans", "Ring the supplier"),
            ("Garden Upkeep: mow the lawn", "Garden Upkeep", "Mow the lawn")):
        said = t.box.run("save", words)
        t.ok(where in said.stdout, f"{words!r} says it went to {where}")
        t.ok(f"- [ ] {step}\n" in (t.box.root / "Work" / where / "README.md").read_text(),
             f"{words!r} is a next action there")
    bare = t.box.run("save", "Kitchen Renovation")
    t.ok("you already have Kitchen Renovation" in bare.stdout, "the bare name finds the one it names")
    t.eq(sorted(p.name for p in (t.box.root / "Work").iterdir()), before, "no second item is made")
    t.eq(sorted(p.name for p in (t.box.root / "Notes").iterdir())
         if (t.box.root / "Notes").exists() else [], [], "and no note under the same name")
    t.box.run("save", "Kitchen renovation: notes from the architect\n- the wall is load-bearing\n"
                      "- she wants a steel beam, about 4k\n- next visit in May")
    t.eq(sorted(p.name for p in (t.box.root / "Work").iterdir()), before,
         "a longer paste about it still makes no second item")


@test
def test_a_different_number_is_a_different_thing(t: Case) -> None:
    """Two tax years are two things, not one thing said twice.

    "Tax return 2026" was refused because "Tax return 2025" was there, and
    once forced through, check and tidy offered to merge the two years."""
    for kind, first, second in (("work", "Tax return 2025", "Tax return 2026"),
                                ("note", "Huberman episode 112 on sleep",
                                 "Huberman episode 113 on sleep")):
        t.box.run("new", kind, first)
        t.box.run("new", kind, second)
    near = [i["message"] for i in t.box.json("check", expect=None)["issues"]
            if i["code"] == "near-duplicate"]
    t.eq(near, [], "check does not call two years, or two episodes, the same thing")
    refused = t.box.run("new", "work", "Tax return 2025 paperwork", expect=1)
    t.ok("Tax return 2025" in refused.stderr, "the same number is still the same thing")
    t.ok("./os show tax-return-2025\n" in refused.stderr,
         "and the name it says to look at is one show takes")


@test
def test_a_saved_project_has_a_shape(t: Case) -> None:
    """Most projects are born from `save`, not `new`. They need the same bones.

    A project with no Next action and no Log is one the rest of the system —
    and any AI following AGENTS.md — cannot actually help with."""
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    project = t.box.carrying("Redesign the pricing page")
    text = (t.box.root / project["path"] / "README.md").read_text()
    for heading in ("## What good looks like", "## Next action", "## Decisions", "## Log"):
        t.ok(heading in text, f"saved work has {heading}")
    t.ok("Redesign the pricing page" in text, "and every word the person wrote survives")
    t.eq(project.get("status"), "pushing", "work with a next action is filed as pushing")

    t.box.run("save", "Keep the tests green, checked every week, this never ends")
    ongoing = t.box.carrying("Keep the tests green")
    t.eq(ongoing.get("status"), "holding", "a standard with no next action is filed as holding")
    text = (t.box.root / ongoing["path"] / "README.md").read_text()
    t.ok("## How often" in text, "something held gets the cadence half of the blueprint")
    t.ok("## What good looks like" in text, "and asks the same first question")

    # something that already has a shape is never rewritten
    folder = t.box.root / "Notes" / "mine"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text(
        "---\ntype: project\n---\n\n# My own project\n\n"
        "## My own heading\nThis must survive exactly.\n")
    t.box.run("sort")
    mine = t.box.carrying("This must survive exactly.")
    kept = (t.box.root / mine["path"] / "README.md").read_text()
    t.eq(mine["bucket"], "Work", "a folder declaring type: project lands in Work")
    t.ok("## My own heading" in kept, "its own headings survive")
    t.ok("## What good looks like" not in kept, "and no blueprint is forced on top of them")

    # undo still puts everything back
    before = t.box.inbox_count()
    t.box.run("undo")
    t.gte(t.box.inbox_count(), before, "undo returns what it filed")


@test
def test_saved_work_arrives_with_its_next_action(t: Case) -> None:
    """8 of 12 everyday saves came back as work with `- [ ] ` left empty, even
    when the words said what the first step was."""
    for words, step in (
            ("Renew my passport before the trip in March. First step is booking "
             "the photo appointment.", "- [ ] Booking the photo appointment"),
            ("Book the dentist before the end of the month", "- [ ] Book the dentist")):
        saved = t.box.json("save", words)
        t.eq(saved["kind"], "project", f"{words[:20]}… is work")
        readme = (t.box.root / saved["saved"] / "README.md").read_text()
        t.ok(step in readme.split("## Next action", 1)[1], f"with its next action filled in: {step}")
    # "Dr." and "Mrs." end no sentence: the step was "Call Dr" and "Book Mrs".
    for words, step in (
            ("Call Dr. Patel about the test results before Friday",
             "Call Dr. Patel about the test results before Friday"),
            ("Book Mrs. Jones for the kitchen quote next week",
             "Book Mrs. Jones for the kitchen quote next week"),
            ("The next step is clear: hire someone", "Hire someone")):
        t.eq(engine.first_step(words), step, f"the step in {words!r}")


@test
def test_a_dropped_folder_is_read_not_guessed(t: Case) -> None:
    """README.md is hidden from the scanner; whoever classifies must still read it.

    A folder somebody made keeps the name they gave it and stays where they put
    it, so what its README says shows in the subject it is filed under."""
    notes = t.box.root / "Notes" / "design-notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "README.md").write_text(
        "# Typography reference\n\nNotes on the modular scale. Reference material "
        "about spacing, typography and layout defaults. Cheat sheet only.\n")
    code = t.box.root / "Notes" / "codebase"
    code.mkdir(parents=True, exist_ok=True)
    (code / "package.json").write_text("{}")
    t.box.run("sort")

    typo = next((i for i in t.box.items() if i["title"] == "Design Notes"), None)
    t.ok(typo is not None, "the folder keeps the name it was given")
    t.eq(typo["domain"], "design", "and is filed on what its README says")
    t.eq(typo["bucket"], "Notes", "where it was put")
    repo = next((i for i in t.box.items() if i["title"].lower() == "codebase"), None)
    t.ok(repo is not None and repo["bucket"] == "Notes",
         "a code folder dropped into Notes stays there too")


@test
def test_sort_adopts_things_where_they_lie(t: Case) -> None:
    """A folder made by hand keeps its name and its place, and nothing in it is
    rewritten. `Work/Wedding Speech` was moved to Notes and renamed after the
    first line of the notes inside it, with a header and five template sections
    written into the speech; a code project's own README.md was rewritten, so
    git showed it changed and the next push would have published it."""
    speech = t.box.root / "Work" / "Wedding Speech"
    speech.mkdir()
    (speech / "speech.md").write_text("Thank you all for coming tonight.\n")
    (speech / "notes.txt").write_text("venue: the old barn\n")
    shop = t.box.root / "Work" / "Shop Site"
    for part in (".git/refs/heads", ".git/info", "src"):
        (shop / part).mkdir(parents=True)
    for rel, words in ((".git/HEAD", "ref: refs/heads/main\n"), (".git/refs/heads/main", "0\n"),
                       (".git/index", "0\n"), (".git/info/exclude", "0\n"),
                       ("README.md", "# Shop Site\n\nThe shop front end.\n"),
                       ("package.json", "{}\n"), ("src/app.js", "console.log('hi')\n")):
        (shop / rel).write_text(words)
    (t.box.root / "Notes" / "party ideas.txt").write_text(
        "Ideas for the kids birthday party\n- bouncy castle\n")

    def files(folder: Path) -> dict:
        return {str(p.relative_to(folder)): p.read_bytes()
                for p in sorted(folder.rglob("*")) if p.is_file()}

    theirs = {speech: files(speech), shop: files(shop)}
    t.box.run("sort")
    t.ok(speech.is_dir() and shop.is_dir(), "both folders stay where they were put")
    t.eq({k: v for k, v in files(speech).items() if k != "README.md"}, theirs[speech],
         "nothing the person wrote is rewritten")
    t.eq(files(shop), theirs[shop], "a code project is left exactly as it was: no file changed or added")
    card = t.box.root / "Work" / "Shop Site.card.md"
    t.ok(card.exists(), "its header is on a card beside it instead")
    wedding = next((i for i in t.box.items() if i["title"] == "Wedding Speech"), None)
    t.ok(wedding is not None and wedding["kind"] == "project", "a folder in Work is work, under its own name")
    site = next((i for i in t.box.items() if i["title"] == "Shop Site"), None)
    t.ok(site is not None and site["status"] == "pushing", "the card makes the code project work too")
    t.ok(not {"head", "heads", "index", "info"} & set(site["tags"]),
         f"and its tags are not read out of .git ({site['tags']})")
    t.ok("Shop Site" in t.box.run("show", "Shop Site").stdout, "it answers to its name")
    party = t.box.root / "Notes" / "party ideas.txt"
    t.ok(party.exists(), "a loose file keeps its own name and ending, not its first line")

    t.box.run("undo")
    t.eq(files(shop), theirs[shop], "undo leaves the code project as it was")
    t.ok(not card.exists() and not (speech / "README.md").exists(),
         "and takes away what sort added")
    t.eq(files(speech), theirs[speech], "and the speech is still word for word")


@test
def test_what_an_older_version_filed_stays_filed(t: Case) -> None:
    """What the released ./os filed is left exactly as it was after an update.

    A redesign of sort read the owner's own folder, filed long before, as
    things waiting: a Notes topic with its sources/, and the piece of work
    holding a copy of the template, with a README in every folder of its
    machinery/ and template/. Here is a folder
    made by hand the way the released ./os leaves one, subjects grouped and
    all. ./os, check and the brief say nothing is waiting or broken, sort
    changes no byte, and find reaches what it did. A folder an older sort
    named after a long title of its note keeps that title, and the words
    only in it are still found (gate on a released folder, 2026-09-30)."""
    root = t.box.root

    def put(rel: str, text: str | bytes) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")

    def head(title: str, kind: str, domain: str, status: str = "", more: str = "") -> str:
        line = f"status: {status}\n" if status else ""
        return (f"---\ntitle: {title}\ntype: {kind}\n{line}domain: {domain}\ntags: []\n"
                f"created: 2026-08-27\nupdated: 2026-09-27\n{more}---\n\n")

    def group(rel: str) -> None:
        put(f"{rel}/.category", json.dumps({"name": Path(rel).name, "trail": [Path(rel).name],
                                            "auto": True, "created": "2026-09-01",
                                            "engine": "3.0.0"}, indent=2) + "\n")

    # A piece of work that is a copy of another folder: every folder in it
    # has a README of its own, and none is a piece of work or a note of ours.
    work = "Work/starterkit"
    put(f"{work}/README.md", head("starterkit", "work", "product", "pushing",
                                  "summary: The starter folder other people download.\n")
        + "# starterkit\n\n## Next action\n- [ ] Run the stranger test\n\n## Decisions\n"
          "- 2026-09-01 · ship the os branch, not main\n\n## Log\n")
    put(f"{work}/decisions.md", "# Decisions\n\n- 2026-09-01 · one folder, any AI\n")
    put(f"{work}/.not-my-os", "This folder holds a copy of an OS folder.\n")
    put(f"{work}/release.sh", "#!/bin/bash\necho release\n")
    for part, words in (("machinery", "# The old layout\n\nWhat the template is built from.\n"),
                        ("machinery/me", "# me/\n\nWho you are. Claude reads this first.\n"),
                        ("machinery/notes", "# notes/\n\nWhat you look up later.\n"),
                        ("machinery/notes/where-i-learn", "# where-i-learn/\n\nWho to trust.\n"),
                        ("machinery/work/archive", "# archive/\n\nFinished projects.\n"),
                        ("template", "# OS template\n\nDownload it and open it in Claude.\n"),
                        ("template/work", "# work/\n\nOne folder per project.\n")):
        put(f"{work}/{part}/README.md", words)
    put(f"{work}/machinery/.claude/skills/learn/SKILL.md",
        "---\nname: learn\ndescription: Pulls something in and files it.\n---\n\n# Learn\n")
    put(f"{work}/template/me/who-i-am.md", "# Who I am\n\n<!-- fill this in -->\n")
    # Work that grew folders of its own, one with a note's header of ours.
    room = "Work/Redecorate the Spare Room"
    put(f"{room}/README.md", head("Redecorate the spare room", "work", "home", "pushing")
        + "# Redecorate the spare room\n\n## Decisions\n- 2026-09-01 · sage green, not grey\n")
    put(f"{room}/Paint Samples/README.md", "# Paint samples\n\nFarrow & Ball tester pots.\n")
    put(f"{room}/Paint Samples/sizes.txt", "Wall is 3.2m by 2.4m.\n")
    put(f"{room}/Furniture/README.md", head("Furniture", "note", "home", "—")
        + "# Furniture\n\nA single bed and a narrow wardrobe.\n")

    # Notes, grouped by subject the way sort grouped them once there were
    # more than twelve; the ones alone in their subject are in General.
    for name in ("Product", "Finance", "Personal", "General"):
        group(f"Notes/{name}")
    for topic, claim in (("handing-it-over", "Diátaxis: four kinds of documentation"),
                         ("os-folder-design", "the collector's fallacy")):
        title = topic.replace("-", " ").capitalize()
        put(f"Notes/Product/{topic}/what-i-think.md",
            f"---\ntitle: {title}\ntype: note\ndomain: product\ntags: []\n"
            f"created: 2026-08-27\nupdated: 2026-08-27\n---\n\n# {title}\n\n- {claim}.\n")
        put(f"Notes/Product/{topic}/sources/README.md",
            f"# {title} sources\n\nOne file per thing read. Don't rewrite what a source claimed.\n")
        put(f"Notes/Product/{topic}/sources/2026-08-26-first.md",
            f"# {claim} — https://example.org/{topic}\n\nDate:      2026-08-26\n"
            f"From:      a write-up\nSubject:   {topic}\n\nThe claim:  {claim}.\n")
        put(f"Notes/Product/{topic}/sources/2026-08-27-yt-second.md",
            "---\ntitle: Onboarding with AI\nsource: video\ndate: 2026-08-27\n---\n\n"
            "The claim: a newcomer reads the first screen and nothing else.\n")
    put("Notes/Finance/council-tax-bill-2026.pdf", b"%PDF-1.4\n\x00\x01council\n")
    put("Notes/Finance/council-tax-bill-2026.pdf.card.md",
        head("Council Tax Bill 2026", "file", "finance", "—",
             "source: council-tax-bill-2026.pdf\n") + "# Council Tax Bill 2026\n")
    put("Notes/Finance/council-tax-is-band-c.md", head("Council tax is band C", "note", "finance", "—")
        + "Council tax is band C, paid by direct debit on the 1st.\n")
    put("Notes/Personal/About me/README.md",
        "---\ntitle: About me\ntype: note\ndomain: personal\ntags: []\ncreated: 2026-08-27\n"
        "updated: 2026-09-26\n---\n\n# About me\n\nSee [who-i-am.md](who-i-am.md).\n")
    put("Notes/Personal/About me/who-i-am.md", "# Who I am\n\nI make the template.\n")
    put("Notes/Personal/recipe-lemon-drizzle-cake.md", head("Recipe: lemon drizzle cake", "note",
                                                            "personal", "—") + "225g butter.\n")
    put("Notes/Personal/Holiday Snaps/IMG_4414.png", b"\x89PNG\r\n\x1a\n" + bytes(40))
    put("Notes/Personal/Holiday Snaps.card.md", head("Holiday Snaps", "file", "personal", "—",
                                                     "source: Holiday Snaps\n"))
    # A folder of their notes that an older sort named after the long title
    # of the first note in it, as it named every such folder.
    long_title = ("★★★★★ (5/5) — “the” best — pizza — dough — I — have — ever — made — "
                  "honestly — 72h cold ferment")
    named = engine.folder_name(long_title, engine.slugify(long_title, 44))
    put(f"Notes/Personal/{named}/best-dough.md",
        head(f'"{long_title}"', "note", "personal") + "72 hours in the fridge.\n")
    put(f"Notes/Personal/{named}/flour.md", "Tipo 00, 12% protein.\n")
    put("Notes/General/where-i-learn/who-to-trust.md",
        head("Where I learn", "note", "research") + "# Where I learn\n\nWho to trust, and why.\n")
    put("Notes/General/boiler-manual.pdf", b"%PDF-1.4\n\x00\x01boiler\n")
    put("Notes/General/boiler-manual.pdf.card.md",
        head("Boiler manual", "file", "engineering", "—", "source: boiler-manual.pdf\n"))
    put("Notes/General/thrown-away.md",
        "---\ntitle: Thrown away\ntype: note\ndomain: learning\ntags: []\ncreated: 2026-08-27\n"
        "updated: 2026-08-27\n---\n\n# Thrown away\n\nIdeas tried and dropped.\n")
    put("Notes/General/Garden Notes/README.md", head("Garden Notes", "note", "garden", "—")
        + "# Garden Notes\n")
    put("Notes/General/Garden Notes/roses.txt", "Roses: prune in February.\n")

    # Put away: by year, as close left it, and a file from the old layout.
    put("Archive/2026/old-layout/projects.md", "# Projects\n\nEvery project in `work/`.\n")
    put("Archive/2026/Work/Plan the Lisbon Trip/README.md",
        head("Plan the Lisbon trip", "work", "personal", "archived",
             "archived: 2026-09-01\nwas: pushing\norigin: Work/Plan the Lisbon Trip\n")
        + "# Plan the Lisbon trip\n\n## Decisions\n- 2026-08-30 · fly from Bristol\n")
    put("Archive/2026/Notes/bin-day-is-tuesday.md",
        head("Bin day is Tuesday", "note", "writing", "archived",
             "archived: 2026-09-01\nwas: —\norigin: Notes/Writing/bin-day-is-tuesday.md\n")
        + "Bin day is Tuesday.\n")

    def disk() -> dict:
        return {str(p.relative_to(root)): p.read_bytes() for bucket in ("Work", "Notes", "Archive")
                for p in sorted((root / bucket).rglob("*")) if p.is_file()}

    before = disk()
    status = t.box.run().stdout
    for said in ("dropped in", "not filed", "fixing", "waiting"):
        t.ok(said not in status, f"./os doesn't say {said!r}:\n{status}")
    t.eq(t.box.json().get("unfiled"), [], "nothing is counted as waiting to be filed")
    brief = t.box.run("brief").stdout
    t.ok("broken" not in brief and "dropped in" not in brief, f"nor does the brief:\n{brief}")
    issues = t.box.json("check", expect=None)["issues"]
    loud = [f"{i['level']} {i['code']} {i['path']}" for i in issues if i["level"] != "hint"]
    t.eq(loud, [], "check finds nothing wrong or out of reach")
    t.ok("nothing waiting" in t.box.run("sort", "--dry-run").stdout, "sort --dry-run has nothing to do")
    for n in (1, 2):
        t.ok("nothing waiting" in t.box.run("sort").stdout, f"sort {n} has nothing to do")
    after = disk()
    t.eq(sorted(set(before) ^ set(after)), [], "sort moved, added and took away nothing")
    t.eq([p for p in before if before[p] != after.get(p, before[p])], [],
         "and changed no byte, in sources/ or anywhere else")

    for words, where in (("diataxis", "Notes/Product/handing-it-over"),
                         ("collector", "Notes/Product/os-folder-design"),
                         ("who to trust", "Notes/General/where-i-learn"),
                         ("council tax", "Notes/Finance/council-tax-is-band-c.md"),
                         ("sage green", room),
                         ("ship the os branch", work),
                         ("cold ferment", f"Notes/Personal/{named}"),
                         ("honestly", f"Notes/Personal/{named}")):
        hits = [h["path"] for h in t.box.json("find", words, expect=None) or []]
        t.ok(where in hits, f"find {words!r} still reaches {where} ({hits})")
    items = {i["path"]: i for i in t.box.items()}
    t.eq(items.get(f"Notes/Personal/{named}", {}).get("title"), long_title,
         "the folder named after its note's title is still called that")
    t.eq(items.get("Notes/General/where-i-learn", {}).get("title"), "Where I learn",
         "as is a folder whose name is the title")
    t.eq(items.get(work, {}).get("kind"), "project", "the copy of the template is one piece of work")
    t.ok(not any(p.startswith(f"{work}/") for p in items),
         "and nothing inside it is listed as a thing of its own")


@test
def test_one_item_can_be_looked_at(t: Case) -> None:
    """`os show <name>` — by the name on disk or by the title — without
    opening a file."""
    t.box.run("save", "Redesign the pricing page before the launch on the 14th")
    project = t.box.carrying("Redesign the pricing page")
    ident = project["id"]

    readme = t.box.root / project["path"] / "README.md"
    text = readme.read_text()
    text = text.replace("## Next action\n- [ ] ", "## Next action\n- [ ] Get the layout in front of Sam")
    text = text.replace("## Decisions", "## Decisions\n- 2026-08-20 - three tiers, not four")
    readme.write_text(text)
    t.box.run("index")

    # both handles resolve: the name on disk, and the title said out loud
    for handle in (ident, project["title"]):
        out = t.box.run("show", handle).stdout
        t.ok(ident in out, f"`os show {handle}` names the thing it opened")
        t.ok("Get the layout in front of Sam" in out, f"`os show {handle}` shows the next action")
        t.ok("three tiers" in out, f"`os show {handle}` shows what was decided")
        for jargon in ("front matter", "taxonomy", "spine", "bucket"):
            t.ok(jargon not in out.lower(), f"`os show` never says '{jargon}'")

    t.box.run("show", "nothing-is-called-this", expect=1)


@test
def test_show_gives_the_newest_log_and_only_open_questions(t: Case) -> None:
    """Log lines are added at the bottom, and show took the top three, so
    "lately" was the oldest sessions. Answered questions lost their tick and
    pushed the one real question off the list."""
    t.box.run("new", "work", "Build the garden shed")
    readme = next((t.box.root / "Work").rglob("README.md"))
    text = readme.read_text()
    text = text.replace("## Next action\n- [ ] ",
                        "## Next action\n- [x] Measure the plot\n- [ ] Order the timber")
    text = text.replace("## Open questions\n- ",
                        "## Open questions\n- [x] Which wood? Answered: cedar.\n"
                        "- [x] Who builds it? Answered: me.\n"
                        "- [x] Budget? Answered: 800.\n- Where does it go?")
    log = "".join(f"- 2026-09-{day:02d} — session {day}\n" for day in range(10, 20))
    readme.write_text(text.rstrip("\n") + "\n" + log)

    out = t.box.run("show", "build-the-garden-shed").stdout
    t.ok("session 19" in out and "session 17" in out, "lately is the newest Log lines")
    t.ok("session 10" not in out, "not the oldest")
    t.ok("Where does it go?" in out, "the open question is there")
    t.ok("Which wood" not in out, "and the answered ones are not")
    t.ok("Order the timber" in out, "the next action is there")
    t.ok("Measure the plot" not in out, "and one already ticked off is not")


@test
def test_the_names_it_lists_are_names_that_work(t: Case) -> None:
    """Every "which one?" says "run ./os to see the names", and ./os listed
    none. The brief cut long titles short, and show refused the cut name."""
    long_title = "Renew my passport before the Lisbon trip in May next year"
    t.box.run("new", "work", long_title)
    t.box.run("new", "ongoing", "Keep the garden watered")
    status = t.box.run("status").stdout
    for title in (long_title, "Keep the garden watered"):
        item = next(i for i in t.box.items() if i["title"] == title)
        t.ok(f"./os show {engine.handle(item['id'])}" in status,
             f"./os gives the name to type for {title!r}")
    brief = t.box.run("brief").stdout
    listed = [ln for ln in brief.split("\n") if "On the go:" in ln or "Being kept up" in ln]
    names = re.findall(r"\[([^\]\s]+)\]", " ".join(listed))
    t.eq(len(names), 2, "the brief gives a name for each thing it lists")
    for name in names:
        t.box.run("show", name)


@test
def test_files_dropped_in_by_hand_get_noticed(t: Case) -> None:
    """Drag files into the inbox and nothing should silently swallow them."""
    quiet = t.box.run("index", "--notify")
    t.eq(quiet.stdout.strip(), "", "an empty inbox says nothing")

    (t.box.root / "Notes" / "budget.csv").write_text("q,total\nQ3,1\n")
    (t.box.root / "Notes" / "scratch.md").write_text("# scratch\n")
    said = json.loads(t.box.run("index", "--notify").stdout)
    message = said["systemMessage"]
    t.ok("budget.csv" in message and "scratch.md" in message, "it names the files")
    t.ok("./os sort" in message, "it says how to file them")
    t.ok("./os undo" in message, "and how to take that back")
    t.ok("without asking" not in message,
         "and never contradicts AGENTS.md, /wrapup and /tidy, which say to sort")
    t.eq(t.box.inbox_count(), 2, "and noticing them does not move them")
    stop = json.loads((t.box.root / ".claude" / "settings.json").read_text())["hooks"]["Stop"]
    t.ok("Filing" not in json.dumps(stop), "the hook that only rebuilds the list doesn't say it is filing")


@test
def test_a_file_at_the_top_of_the_folder_gets_filed(t: Case) -> None:
    """The top of the folder is where a file gets dropped first, and nothing
    looked there: `./os` didn't mention it, sort said everything was filed,
    and `./os save` refused it as already in this folder."""
    (t.box.root / "shopping list.md").write_text("eggs\nmilk\nbread\n")
    (t.box.root / "Receipt.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    # A Mac folder's custom icon, and Windows' picture previews: not theirs to file.
    (t.box.root / "Icon\r").write_bytes(b"")
    (t.box.root / "Thumbs.db").write_bytes(b"\0" * 16)
    t.ok("2 things dropped in but not filed" in t.box.run().stdout, "./os says they are there")
    said = json.loads(t.box.run("index", "--notify").stdout)["systemMessage"]
    t.ok("shopping list.md" in said and "Receipt.png" in said, "and so does the hook")
    t.box.run("save", "./Receipt.png")
    t.ok(not (t.box.root / "Receipt.png").exists(), "save files one from where it lies")
    t.box.run("sort")
    t.ok(not (t.box.root / "shopping list.md").exists(), "and sort files the rest")
    t.ok((t.box.root / "Notes" / "shopping list.md").exists(),
         "into the folder it belongs in, under its own name")
    for name in ("AGENTS.md", "CLAUDE.md", "os", "Icon\r", "Thumbs.db"):
        t.ok((t.box.root / name).exists(), f"what belongs at the top stays there ({name!r})")
    t.box.run("undo")
    t.ok((t.box.root / "shopping list.md").exists(), "and undo puts it back")


@test
def test_tags_are_earned_not_invented(t: Case) -> None:
    """Junk tags on every short note make the whole folder look stupid."""
    t.box.run("save", "The billing token refresh dies every Friday night, before the release")
    short = next(i for i in t.box.items() if i["kind"] == "project")
    t.eq(short["tags"], [], "a one-line thought gets no invented tags")

    (t.box.root / "Notes" / "long.md").write_text(
        "# Migration plan\n\nThe billing service runs on the legacy schema. Migrating "
        "the billing tables is the work. Each billing record needs backfilling, and the "
        "billing job keeps running through the migration. Migration order: billing first.\n")
    t.box.run("sort")
    long_one = next(i for i in t.box.items()
                    if i["kind"] in ("project", "note") and "igration" in i["title"])
    t.ok("billing" in long_one["tags"],
         f"a word used over and over does earn a tag (got {long_one['tags']})")


@test
def test_a_number_for_a_tag_does_not_stop_the_folder(t: Case) -> None:
    """`tags: 2026` reads as a number and `tags: yes` as true, and the scan
    tried to walk through either one as a list. Every command that looks at
    the folder died with a traceback, and no command could get round it."""
    for name, tags in (("tax-return", "2026"), ("garden-plan", "yes"), ("reading", "1.5")):
        (t.box.root / "Notes" / f"{name}.md").write_text(
            f"---\ntitle: {name.replace('-', ' ').title()}\ntype: note\nstatus: —\n"
            f"domain: home\ntags: {tags}\n---\n\n# {name}\n\nSomething to look up later.\n")
    for command in ([], ["check"], ["check", "--fix"], ["find", "tax"],
                    ["show", "tax-return"], ["sort"], ["brief"]):
        proc = t.box.run(*command, expect=None)
        t.ok("Traceback" not in proc.stderr,
             f"`os {' '.join(command)}` still works:\n{proc.stderr[-300:]}")
    t.box.run("index")
    item = next(i for i in t.box.items() if i["title"] == "Tax Return")
    t.eq(item["tags"], ["2026"], "and the number is kept as a tag")


@test
def test_the_name_is_the_same_everywhere(t: Case) -> None:
    """A half-finished rename is the easiest way to ship something embarrassing."""
    name = json.loads((t.box.root / ".os" / "config.json").read_text())["name"]
    t.ok(name.strip() and name[0].isupper(), f"the folder declares a real name ({name!r})")

    # the suite itself has to name old names in order to check for them
    skip = {".git", "backups", "cache", "__pycache__", ".DS_Store", "tests"}
    stale = []
    for path in sorted(t.box.root.rglob("*")):
        if not path.is_file() or any(part in skip for part in path.parts):
            continue
        if path.suffix in (".zip", ".pyc"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for other in ("meridian",):          # every name this has ever had
            if other in text.lower() and other != name.lower():
                stale.append(f"{path.relative_to(t.box.root)} still says {other!r}")
    t.eq(stale, [], f"no file mentions an old name: {stale[:3]}")

    # and a new name reaches the places a person, or their AI, actually looks.
    # Renamed first, whatever this copy is called: checked only with the name
    # it shipped with, AGENTS.md went on saying "Zenith" after every rename.
    name = "Harbour Studio"
    t.box.run("name", "--name", name)
    t.ok(name in t.box.run("help").stdout, "`os help` shows the name")
    t.ok(f"This folder is called {name}. " in (t.box.root / "AGENTS.md").read_text(),
         "AGENTS.md shows the name")
    t.ok(t.box.run("brief").stdout.strip().startswith(name.upper()),
         "and so does what the AI is handed first")
    t.box.run("index")
    t.ok(name in (t.box.root / "INDEX.md").read_text(), "INDEX.md shows the name")
    t.box.run("backup")
    zips = list((t.box.root / ".os" / "backups").glob("*.zip"))
    t.ok(zips and zips[0].name.startswith(engine.slugify(name) + "-"),
         f"backups are named after it ({[z.name for z in zips]})")
    t.box.run("setup", "--name", "Garden Stall", "--quiet-welcome")
    t.ok("This folder is called Garden Stall. " in (t.box.root / "AGENTS.md").read_text(),
         "naming it at setup reaches AGENTS.md too")
    # A full stop in the name: the fingerprint stopped at "St." and took the
    # AGENTS.md it had just written for one the person edited.
    import upgrade
    t.box.run("name", "--name", "St. Ives")
    agents = t.box.root / "AGENTS.md"
    blank = t.box.tmp / "AGENTS.md"
    blank.write_text(agents.read_text().replace("St. Ives", "Zenith", 1))
    t.eq(engine._sha1(agents), engine._sha1(blank), "a name with a full stop in it is still just a name")
    t.eq(upgrade.sha1_unnamed(agents), upgrade.sha1_unnamed(blank), "to the update too")
    t.eq(upgrade.folder_name_in(agents), "St. Ives", "which reads the whole name")
    t.box.run("name", "--name", "Penzance")
    t.ok("This folder is called Penzance. It" in agents.read_text(), "and renaming it again replaces all of it")


@test
def test_it_survives_being_used_wrongly(t: Case) -> None:
    """Every one of these was a real bug found by trying to break it."""
    # `Path("")` is the current directory: an empty argument once tried to copy
    # the whole folder into its own inbox, and hung
    # Nothing typed and nothing *filable* typed are different mistakes, and
    # answering "tell me what to save" to somebody who plainly did reads as
    # the folder not listening. Emoji count as punctuation here.
    for junk in ("", "     "):
        proc = t.box.run("save", junk, expect=1)
        t.ok("tell me what to save" in proc.stderr, f"`save {junk!r}` is refused, kindly")
    for wordless in (".", "...!!!", "\U0001f389\U0001f389"):
        proc = t.box.run("save", wordless, expect=1)
        t.ok("no words in that" in proc.stderr,
             f"`save {wordless!r}` says which of the two it was")
    t.eq(t.box.inbox_count(), 0, "nothing junk reached the inbox")

    # a path that is not there must not be filed as if it were prose
    for missing in ("/no/such/file.txt", "./notes/thing.md", "report.pdf"):
        proc = t.box.run("save", missing, expect=1)
        t.ok("there is no file at" in proc.stderr, f"`save {missing}` says the file is missing")
    # ...but a real sentence containing a dot is still a sentence
    t.box.run("save", "Ship v2.0 before Friday")
    t.gte(len([i for i in t.box.items() if i["kind"] == "project"]), 1,
          "a sentence with a version number is still a sentence")

    # the folder must refuse to swallow itself
    for suicidal in (str(t.box.root), str(t.box.root / "Notes"), ".."):
        t.box.run("save", suicidal, expect=1)
    t.eq(t.box.inbox_count(), 0, "no self-copy reached the inbox")


@test
def test_files_never_lose_their_card(t: Case) -> None:
    """A file on the Notes shelf carries a card; separating them orphans both."""
    for i in range(16):
        (t.box.root / "Notes" / f"figures-{i}.csv").write_text(f"quarter,total\nQ{i},{i}00\n")
    t.box.run("sort")   # enough of them to force category folders, which moves them

    def orphans() -> tuple:
        root = t.box.root / "Notes"
        cards, lonely = [], []
        for card in root.rglob("*.card.md"):
            if not card.with_name(card.name[: -len(".card.md")]).exists():
                cards.append(str(card))
        for f in root.rglob("*"):
            if f.is_file() and not f.name.endswith(".card.md") and not f.name.startswith("."):
                if not f.with_name(f.name + ".card.md").exists():
                    lonely.append(str(f))
        return cards, lonely

    t.eq(orphans(), ([], []), "re-shelving keeps every file with its card")

    asset = next(i for i in t.box.items() if i["kind"] == "asset")
    t.box.run("done", asset["id"])
    t.eq(orphans(), ([], []), "archiving takes the card along")
    t.box.run("back", asset["id"])
    t.eq(orphans(), ([], []), "restoring brings the card back")
    t.box.run("undo")
    t.eq(orphans(), ([], []), "undo keeps them together too")

    # and `open` points at the file, not at the card describing it
    path = t.box.run("open", asset["id"]).stdout.strip().split("\n")[0]
    t.ok(not path.endswith(".card.md"), "`os open` gives the file itself")


@test
def test_edit_opens_nothing_when_no_one_is_at_a_terminal(t: Case) -> None:
    """`./os edit` handed the file to the Mac's `open`, or to $EDITOR,
    whoever ran it. An AI has no screen, so the window landed on the
    person's desk — and this suite runs `edit` too, so every `./os test`
    opened a TextEdit window. With no terminal it now says where the file
    is and opens nothing, the way `./os open` already did."""
    t.box.run("new", "work", "Rebuild the onboarding flow", "--domain", "engineering")
    ident = next(i["id"] for i in t.box.items() if i["title"] == "Rebuild the onboarding flow")

    fake = t.box.tmp / "fake-bin"
    fake.mkdir()
    opened = t.box.tmp / "something-opened"
    for name in ("open", "xdg-open", "fake-editor"):
        (fake / name).write_text(f'#!/bin/sh\necho "$0 $*" >> "{opened}"\n')
        (fake / name).chmod(0o755)
    env = dict(os.environ, ZENITH_HOME=str(t.box.root), NO_COLOR="1",
               PATH=f"{fake}{os.pathsep}{os.environ.get('PATH', '')}")

    for editor, how in (("", "no editor set"), (str(fake / "fake-editor"), "an editor set")):
        env["EDITOR"] = env["VISUAL"] = editor
        proc = subprocess.run([str(t.box.root / "os"), "edit", ident], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              cwd=str(t.box.root), env=env, timeout=60)
        t.eq(proc.returncode, 0, f"`os edit` with {how} succeeds")
        t.ok(not opened.exists(), f"`os edit` with {how} opens nothing when no one is at a terminal")
        said = proc.stdout.strip().split("\n")[-1] if proc.stdout.strip() else ""
        t.ok(said.endswith(".md") and Path(said).is_file(), f"and with {how} it says where the file is")


@test
def test_a_scanned_file_is_found_by_what_it_is(t: Case) -> None:
    """A card held only the file's name, so scan_0012.pdf, a tenancy
    agreement, was never found by "tenancy". Search reads the card, so the
    save skill and AGENTS.md have the AI write what the file is into it, and
    rename a camera's or scanner's name."""
    for doc in (t.box.root / "AGENTS.md", t.box.root / ".claude" / "skills" / "save" / "SKILL.md"):
        if not doc.is_file() or in_their_words(doc, t.box):
            continue
        text = doc.read_text()
        t.ok("3–5 tags" in text and "./os rename" in text,
             f"{doc.name} says to fill the card in and rename a scanner's name")

    scan = t.box.tmp / "scan_0012.pdf"
    scan.write_bytes(b"%PDF-1.4\nTenancy agreement between the landlord and the tenant.\n")
    t.box.run("save", str(scan))
    card = next((t.box.root / "Notes").glob("scan*0012.pdf.card.md"))
    t.ok("nothing matched" in t.box.run("find", "tenancy", expect=None).stdout,
         "a card with only the name is not found by what the file is")
    card.write_text(card.read_text().replace("tags: []", "tags: [tenancy, landlord, flat]")
                    + "\nThe tenancy agreement for the flat, signed with the landlord.\n")
    t.ok("scan" in t.box.run("find", "tenancy").stdout.lower(), "filled in, it is")
    t.box.run("rename", "scan-0012", "Tenancy Agreement")
    t.ok((t.box.root / "Notes" / "tenancy-agreement.pdf.card.md").exists()
         and (t.box.root / "Notes" / "tenancy-agreement.pdf").exists(),
         "and a rename takes the card along")


def _unreachable(box: "Sandbox") -> dict:
    """What ./os check says nothing can reach, by its code."""
    out: dict = {}
    for issue in box.json("check", expect=None)["issues"]:
        if issue["code"] in ("left-at-top", "passed-over", "work-inside-work", "too-far-in",
                             "card-left-behind"):
            out.setdefault(issue["code"], []).append(issue)
    return out


@test
def test_every_file_in_a_folder_is_found_by_its_name(t: Case) -> None:
    """Search read the words of text files and nothing else, so a folder of
    PDFs, Notes/Taxes, or of Word files, Notes/School, was one thing whose
    files no search found, the ones added later too, while sort said nothing
    was waiting and check said all good (review, 2026-09-30)."""
    notes = t.box.root / "Notes"
    taxes, school = notes / "Taxes", notes / "School"
    for folder, names in ((taxes, ("Self assessment 2024.pdf", "P60 2024.pdf")),
                          (school, ("History essay.docx", "MathsNotes_week1.docx"))):
        folder.mkdir(parents=True)
        for name in names:
            (folder / name).write_bytes(b"%PDF-1.4\n\x00\x01binary\n")
    t.box.run("sort")
    (taxes / "Council tax bill 2026.pdf").write_bytes(b"%PDF-1.4\n\x00\x01\n")
    (school / "Biology").mkdir()
    (school / "Biology" / "Cells diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(40))
    t.ok("nothing waiting" in t.box.run("sort").stdout, "files added later are not waiting")

    for words, folder, name in (("self assessment", taxes, "Self assessment 2024.pdf"),
                                ("council", taxes, "Council tax bill 2026.pdf"),
                                ("history", school, "History essay.docx"),
                                ("maths notes", school, "MathsNotes_week1.docx"),
                                ("cells", school, "Biology/Cells diagram.png")):
        hits = t.box.json("find", words)
        hit = next((h for h in hits if h["path"] == f"Notes/{folder.name}"), None)
        t.ok(hit is not None, f"find {words!r} reaches Notes/{folder.name} ({hits})")
        t.eq(hit and hit.get("file"), f"Notes/{folder.name}/{name}",
             f"and says which file in it matched {words!r}")
    shown = t.box.run("find", "council").stdout
    t.ok("in Council tax bill 2026.pdf" in shown, f"the hit shows the file:\n{shown}")
    t.eq(_unreachable(t.box), {}, "and check has nothing to say about any of it")

    # Never the names inside a code project, nor in Work/Content.
    app = t.box.root / "Work" / "Budget App"
    (app / "src").mkdir(parents=True)
    (app / "package.json").write_text("{}\n")
    (app / "src" / "zebracrossing.js").write_text("export {}\n")
    content = t.box.root / "Work" / "Content"
    content.mkdir()
    (content / "okapi footage.mov").write_bytes(b"\x00" * 64)
    t.box.run("sort")
    for word in ("zebracrossing", "okapi"):
        t.ok(not any(h.get("file") for h in t.box.json("find", word, expect=None) or []),
             f"{word!r} is not matched inside a code project or Work/Content")
    t.eq(_unreachable(t.box), {}, "and check is still quiet")


@test
def test_check_names_what_nothing_can_reach(t: Case) -> None:
    """One rule behind a run of holes each found alone: anything in Work or
    Notes that neither ./os find nor the list reaches is named by ./os check,
    with where it is and the command that fixes it (review, 2026-09-30)."""
    work = t.box.root / "Work"
    # Work moved into another piece of work's folder dropped off the list,
    # while sort said nothing was waiting and check said all good.
    t.box.run("new", "work", "Wedding")
    t.box.run("new", "work", "Book the venue")
    (work / "Wedding" / "Venue").mkdir()
    (work / "Book the Venue").rename(work / "Wedding" / "Venue" / "Book the Venue")
    t.ok("nothing waiting" in t.box.run("sort").stdout, "sort doesn't see it")
    t.box.run("show", "book-the-venue", expect=1)
    found = _unreachable(t.box).get("work-inside-work", [])
    t.eq(len(found), 1, f"check names the hidden work ({found})")
    t.ok(found and "Book the venue" in found[0]["message"] and "Wedding" in found[0]["message"]
         and found[0]["path"] == "Work/Wedding/Venue/Book the Venue"
         and found[0]["fix"].startswith("mv -n "),
         f"naming both, where it is, and the command that brings it out ({found})")
    # A piece of work's own folder is theirs to arrange, and work kept in
    # another under the released ./os was called broken after an update,
    # and moved out by --fix (review, 2026-09-30). Said, never moved.
    t.ok(found and found[0]["level"] == "hint" and "fixing" not in t.box.run().stdout,
         "said by check, and ./os doesn't call it broken")
    t.box.run("check", "--fix", expect=None)
    t.ok((work / "Wedding" / "Venue" / "Book the Venue" / "README.md").is_file()
         and not (work / "Book the Venue").exists(), "--fix leaves it where they put it")
    subprocess.run(["bash", "-c", found[0]["fix"]], cwd=str(t.box.root), check=True)
    t.ok((work / "Book the Venue" / "README.md").is_file(), "the command puts it back at the top of Work")
    t.ok("book the venue" in t.box.run("show", "book-the-venue").stdout.lower(), "show reaches it")
    t.ok("Book the venue" in t.box.run().stdout, "and ./os lists it again")

    # An older sort gave a Notes folder of typed notes a README of ours, so
    # it is one thing. What goes in it later is found all the same.
    recipes = t.box.root / "Notes" / "My Recipes"
    recipes.mkdir(parents=True)
    (recipes / "README.md").write_text(
        "---\ntitle: My Recipes\ntype: note\nstatus: —\ndomain: food\ntags: [pizza]\n"
        "created: 2026-09-01\nupdated: 2026-09-01\n---\n\n# My Recipes\n\n## In one line\n")
    (recipes / "Pizza.md").write_text("500g flour, semolina\n")
    t.box.run("sort")
    (recipes / "Focaccia.md").write_text("rosemary and olive oil\n")
    (recipes / "Oven manual.pdf").write_bytes(b"%PDF-1.4\n\x00\x01\n")
    for word in ("rosemary", "focaccia", "oven", "semolina"):
        t.ok(any(h["path"] == "Notes/My Recipes" for h in t.box.json("find", word)),
             f"find {word} reaches what was added to it")

    # Further in than search looks is named, with the one it's like.
    photos = t.box.root / "Notes" / "Photos"
    deep = photos.joinpath(*[f"level {n}" for n in range(1, 14)])
    deep.mkdir(parents=True)
    (deep / "Lost lighthouse.jpg").write_bytes(b"\xff\xd8\xff" + bytes(40))
    (photos / "level 1" / "Harbour.jpg").write_bytes(b"\xff\xd8\xff" + bytes(40))
    t.box.run("sort")
    t.ok(any(h.get("file", "").endswith("Harbour.jpg") for h in t.box.json("find", "harbour")),
         "a photo a folder down is found by its name")
    far = _unreachable(t.box).get("too-far-in", [])
    t.ok(len(far) == 1 and "Lost lighthouse.jpg" in far[0]["message"]
         and far[0]["path"] == "Notes/Photos" and far[0]["fix"].startswith("mv "),
         f"one thirteen folders down is named, with the command to bring it up ({far})")
    # The command never lands on a file of the same name already there: a
    # plain mv put the deep one over it, for good.
    (photos / "Lost lighthouse.jpg").write_bytes(b"TOP-ONE")
    (deep / "Lost lighthouse.jpg").write_bytes(b"DEEP-ONE")
    cure = _unreachable(t.box)["too-far-in"][0]["fix"]
    subprocess.run(["bash", "-c", cure], cwd=str(t.box.root), check=True)
    t.eq((photos / "Lost lighthouse.jpg").read_bytes(), b"TOP-ONE",
         f"the one already at the top is kept ({cure})")
    t.eq((photos / "Lost lighthouse 2.jpg").read_bytes(), b"DEEP-ONE",
         "and the deep one comes up beside it")
    t.ok("too-far-in" not in _unreachable(t.box), "after which check is quiet")

    # A name ./os passes over, sitting where a thing of theirs would be. One
    # with nothing in it yet but what a Mac leaves is nothing of theirs.
    (t.box.root / "Notes" / "Content" / "Drafts").mkdir(parents=True)
    (t.box.root / "Notes" / "Content" / ".DS_Store").write_bytes(b"\0")
    t.ok("passed-over" not in _unreachable(t.box), "a Content folder with nothing in it yet is left alone")
    (t.box.root / "Notes" / "Content" / "Blog ideas.md").write_text("A post about compost.\n")
    over = sorted(i["path"] for i in _unreachable(t.box).get("passed-over", []))
    t.eq(over, ["Notes/Content"], "a Content folder in Notes")
    t.box.run("check", "--fix", expect=None)
    t.box.run("sort")
    t.ok(t.box.json("find", "compost"), "--fix gives it a name sort files, and compost is found")
    t.ok("passed-over" not in _unreachable(t.box), "and check stops saying so")
    # Named after where it was, since a bare `Content 2` says nothing.
    t.ok((t.box.root / "Notes" / "Notes Content" / "Blog ideas.md").is_file(),
         f"called after the folder it was in ({sorted(p.name for p in (t.box.root / 'Notes').iterdir())})")


@test
def test_a_folder_dropped_at_the_top_is_filed(t: Case) -> None:
    """Only files were taken from the top of the folder, so `My Recipes`
    dragged in beside Work and Notes was never mentioned, sorted or found,
    while check said all good (review, 2026-09-30)."""
    top = t.box.root / "My Recipes"
    top.mkdir()
    (top / "Pizza.md").write_text("500g flour, semolina\n")
    (top / "Chilli oil.md").write_text("Dried chillies and oil.\n")
    # Set up ahead of time, with only what a Mac leaves in it: nothing yet.
    (t.box.root / "Nothing yet" / "Later").mkdir(parents=True)
    (t.box.root / "Nothing yet" / "Later" / ".DS_Store").write_bytes(b"\0")
    t.ok("1 thing dropped in but not filed" in t.box.run().stdout, "./os says it's waiting")
    left = _unreachable(t.box).get("left-at-top", [])
    t.ok([i["path"] for i in left] == ["My Recipes"], f"check says where it is ({left})")
    said = t.box.run("save", str(top), expect=1)
    t.ok("./os sort files it" in said.stderr, f"save points at sort:\n{said.stderr}")
    preview = t.box.run("sort", "--dry-run").stdout
    t.ok("My Recipes" in preview and "Notes/My Recipes" in preview and top.is_dir(),
         f"a preview says where it goes, and moves nothing:\n{preview}")
    t.ok("✓" not in preview and "would" in preview,
         f"and doesn't say it's done ({preview})")

    t.box.run("sort")
    kept = t.box.root / "Notes" / "My Recipes"
    t.ok((kept / "Pizza.md").is_file() and not top.exists(), "into Notes under its own name")
    paths = {i["path"] for i in t.box.items()}
    t.ok("Notes/My Recipes" in paths, f"and filed there, as one thing ({sorted(paths)})")
    t.ok(any(h["path"] == "Notes/My Recipes" for h in t.box.json("find", "semolina")),
         "found by what it says")
    t.ok("left-at-top" not in _unreachable(t.box), "check is quiet about it now")
    t.ok((t.box.root / "Nothing yet" / "Later").is_dir(),
         "a folder with nothing in it yet is left alone")
    t.box.run("undo")
    t.ok((top / "Pizza.md").is_file(), "and ./os undo puts it back at the top")

    # One called Content too: only Work/Content is left alone, and one at the
    # top was never mentioned, sorted or found.
    shutil.rmtree(top)
    (t.box.root / "Content").mkdir()
    (t.box.root / "Content" / "compost-post.md").write_text("A post about compost.\n")
    t.ok("1 thing dropped in but not filed" in t.box.run().stdout, "./os says it's waiting")
    t.eq([i["path"] for i in _unreachable(t.box).get("left-at-top", [])], ["Content"],
         "check says where it is")
    t.box.run("sort")
    # Notes/Content is a name ./os passes over, so it goes in beside it.
    t.ok(not (t.box.root / "Content").exists()
         and any(h["path"] == "Notes/Content 2" for h in t.box.json("find", "compost")),
         "sort files it and find reaches it")


@test
def test_a_folder_they_made_keeps_its_name_and_every_note_in_it_is_found(t: Case) -> None:
    """Two saved notes moved by hand into `mkdir Notes/Recipes`: sort renamed
    the folder after the chilli oil note, ./os find pizza found nothing, and
    check said all good. In Work the folder went to Notes, as the note in it
    said `type: note` (stranger test, 2026-09-30). A folder they named keeps
    the name, stays where they put it, and every note in it is found."""
    root = t.box.root
    notes = {}
    for bucket, folder, said in (
            ("Notes", "Recipes", ("Pizza dough: 500g flour, 325ml water, 10g salt, rest 24 hours",
                                  "Chilli oil: warm 200ml oil and pour over chilli flakes")),
            ("Work", "Kitchen", ("Quote from Harlow Joinery for the worktops: 2400 pounds",
                                 "The tiler Gumbleton can start on the 14th"))):
        (root / bucket / folder).mkdir(parents=True, exist_ok=True)
        for words in said:
            t.box.run("save", words)
            saved = root / t.box.carrying(words.split(":")[0])["path"]
            moved = root / bucket / folder / saved.name
            saved.rename(moved)
            notes[moved] = moved.read_bytes()
    # One written by hand with a short header, first in the folder: its
    # header is its own, and nothing is added to it.
    focaccia = root / "Notes" / "Recipes" / "a-focaccia.md"
    focaccia.write_text("---\ntitle: Focaccia\ndomain: food\n---\n\nOlive oil and rosemary.\n")
    notes[focaccia] = focaccia.read_bytes()
    t.box.run("sort")
    t.ok(all(p.is_file() for p in notes), f"both folders keep their names and places "
         f"({sorted(str(p.relative_to(root)) for b in ('Work', 'Notes') for p in (root / b).rglob('*'))})")
    t.eq({p: p.read_bytes() for p in notes}, notes, "and not a note in them is rewritten")
    for word, where in (("pizza", "Notes/Recipes"), ("chilli", "Notes/Recipes"),
                        ("harlow", "Work/Kitchen"), ("gumbleton", "Work/Kitchen")):
        hits = [h["path"] for h in t.box.json("find", word)]
        t.eq(hits[:1], [where], f"./os find {word} finds it")
    kitchen = next(i for i in t.box.items() if i["path"] == "Work/Kitchen")
    t.eq((kitchen["title"], kitchen["kind"], kitchen["status"]), ("Kitchen", "project", "pushing"),
         "the one in Work is work, under its own name")
    t.ok("Kitchen" in t.box.run().stdout, "and on the list")
    t.ok("nothing waiting" in t.box.run("sort").stdout, "sort has nothing more to do")
    issues = t.box.json("check", expect=None)["issues"]
    t.eq([i for i in issues if "Recipes" in i["path"] or "Kitchen" in i["path"]], [],
         "and check has nothing to say about either")
    # Renamed, the folder's name changes, and the notes in it keep theirs.
    t.box.run("rename", "Recipes", "Family Recipes")
    t.eq(sorted(p.read_bytes() for p in (root / "Notes" / "Family Recipes").glob("*.md")),
         sorted(v for p, v in notes.items() if p.parent.name == "Recipes"),
         "renamed, not a note in it is rewritten")
    t.box.run("undo")

    # Further in, the folder still keeps its name: the note inside
    # Italian/ gave Cooking its title, and sort renamed it `risotto`.
    t.box.run("save", "Risotto: toast the arborio rice, then add warm stock slowly")
    saved = root / t.box.carrying("Risotto")["path"]
    (root / "Notes" / "Cooking" / "Italian").mkdir(parents=True)
    saved.rename(root / "Notes" / "Cooking" / "Italian" / saved.name)
    t.box.run("sort")
    t.ok((root / "Notes" / "Cooking" / "Italian" / saved.name).is_file(),
         f"a folder with the note further in keeps its name too "
         f"({sorted(p.name for p in (root / 'Notes').iterdir())})")
    t.eq([h["path"] for h in t.box.json("find", "arborio")][:1], ["Notes/Cooking"],
         "and the note is found")

    # One an older sort filed, named after the note it reads through, with
    # sources in it: nothing about it changes, and it keeps its title.
    older = root / "Notes" / "handing-it-over"
    (older / "sources").mkdir(parents=True)
    (older / "what-i-think.md").write_text(
        "---\ntitle: Handing it over\ntype: note\ndomain: product\ntags: []\n"
        "created: 2026-08-27\nupdated: 2026-08-27\n---\n\n# Handing it over\n\nKeep it short.\n")
    (older / "sources" / "talk.md").write_text("What a stranger keeps.\n")
    before = {str(p.relative_to(older)): p.read_bytes() for p in older.rglob("*") if p.is_file()}
    t.box.run("sort")
    t.eq({str(p.relative_to(older)): p.read_bytes() for p in older.rglob("*") if p.is_file()},
         before, "a folder filed before is left exactly as it was")
    item = next(i for i in t.box.items() if i["path"] == "Notes/handing-it-over")
    t.eq(item["title"], "Handing it over", "and keeps its title")

    # Made by ./os new, a folder still follows its title when that changes.
    t.box.run("new", "work", "Fix the shed door")
    readme = root / "Work" / "Fix the Shed Door" / "README.md"
    readme.write_text(readme.read_text().replace("title: Fix the shed door",
                                                 "title: Mend the shed door"))
    t.box.run("sort")
    t.ok((root / "Work" / "Mend the Shed Door" / "README.md").is_file(),
         "a folder ./os made is renamed when its title changes")


HOME_FACTS = (
    "Boiler pressure should sit at 1.5 bar when cold",
    "The car's tyre pressure is 32 psi front and back",
    "Lemon cake: 200g butter, 200g sugar, 4 eggs, zest of two lemons",
    "Tomatoes need staking once they reach 30cm",
    "Library card number is on the fridge",
    "Bin day is Tuesday, recycling every other week",
    "Doctor said take vitamin D through winter",
    "The garden tap washer is 1/2 inch",
    "Sourdough starter: feed it 1:1:1 every 12 hours",
    "Mum's birthday is 14 March",
    "Wifi router lives behind the TV",
    "Broadband contract ends in June",
    "Paint for the hallway is Farrow and Ball Elephant's Breath",
)


@test
def test_a_folder_they_made_is_never_moved_into_a_group(t: Case) -> None:
    """Once Notes passed 12 things, sort moved `mkdir Notes/Recipes` into
    Notes/Food/Recipes (stranger test, 2026-09-30). A folder somebody made
    stays where they put it however big the folder gets, in Work too, and
    one already in a group stays in it (decided 2026-10-01). The notes sort
    filed itself, and the work ./os new made, are still grouped."""
    root = t.box.root
    recipes = root / "Notes" / "Recipes"
    recipes.mkdir(parents=True)
    for words in ("Pizza dough: 500g flour, 325ml water, 10g salt, rest 24 hours",
                  "Chilli oil: warm 200ml oil and pour over chilli flakes"):
        t.box.run("save", words)
        saved = root / t.box.carrying(words.split(":")[0])["path"]
        saved.rename(recipes / saved.name)
    # Made by hand in Work with only a photo in it: sort gives it a page.
    kitchen = root / "Work" / "Kitchen"
    kitchen.mkdir(parents=True)
    (kitchen / "worktop.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really a photo")
    # One a released ./os already put in a group, as it did past 12 things.
    garden = root / "Notes" / "Garden"
    (garden / "Allotment").mkdir(parents=True)
    (garden / ".category").write_text(json.dumps({"name": "Garden", "trail": ["Garden"],
                                                  "auto": True, "created": "2026-09-01",
                                                  "engine": "3.0.0"}) + "\n")
    (garden / "Allotment" / "plot.md").write_text(
        "---\ntitle: Plot 14\ntype: note\nstatus: —\ndomain: home\ntags: []\n"
        "created: 2026-09-01\nupdated: 2026-09-01\n---\n\nBeans along the north fence.\n")
    t.box.run("sort")
    theirs = {p: p.read_bytes() for d in (recipes, kitchen, garden / "Allotment")
              for p in d.iterdir() if p.is_file()}
    t.ok("made: by hand" in (kitchen / "README.md").read_text(),
         "the folder made by hand in Work says so in its header")

    for words in HOME_FACTS:
        t.box.run("save", words)
    for n in range(13):
        t.box.run("new", "work", f"Fix the {['shed', 'gate', 'fence', 'roof', 'tap'][n % 5]} "
                                 f"number {n + 1}")
    moved = t.box.run("sort").stdout
    t.eq({p: p.read_bytes() for p in theirs if p.is_file()}, theirs,
         f"Recipes, Kitchen and the Allotment stay exactly where they were\n{moved}")
    t.ok("Recipes" not in moved and "Kitchen" not in moved and "Allotment" not in moved,
         f"and sort doesn't say it moved them\n{moved}")
    grouped = {i["bucket"] for i in t.box.items() if i["trail"]}
    t.ok({"Notes", "Work"} <= grouped,
         f"the notes and work ./os filed itself are still grouped ({sorted(grouped)})")
    t.ok("nothing waiting" in t.box.run("sort").stdout, "and the next sort has nothing to do")
    for word, where in (("pizza", "Notes/Recipes"), ("beans", "Notes/Garden/Allotment")):
        t.eq([h["path"] for h in t.box.json("find", word)][:1], [where], f"./os find {word} finds it")


@test
def test_sort_never_names_two_things_in_one_bucket_alike(t: Case) -> None:
    """A second note called Pizza was named against the top of Notes only,
    while the first was in Notes/Food. The next sort put it in General, and
    two things were called pizza for good (stranger test, 2026-09-30). A new
    name is checked against every name in the bucket, its groups too."""
    root = t.box.root
    for words in HOME_FACTS + ("Pizza",):
        t.box.run("save", words)
    t.box.run("sort")
    t.ok(any(i["trail"] for i in t.box.items()), "Notes is grouped by now")
    t.box.run("save", "Pizza\n\nRecipe: 500g flour, 325ml water, 10g salt, yeast. "
                      "Ingredients for the dough, rest 24 hours")
    t.box.run("sort")
    # And one renamed by hand to a title already in another group.
    paint = root / t.box.carrying("Farrow and Ball")["path"]
    paint.write_text(paint.read_text().replace("title: Paint for the hallway", "title: Pizza", 1))
    t.box.run("sort")
    names = [engine.slugify(i["id"]) for i in t.box.items() if i["bucket"] == "Notes"]
    t.eq(sorted(n for n in set(names) if names.count(n) > 1), [],
         "no two things in Notes answer to one name")
    t.ok(not [i for i in t.box.json("check", expect=None)["issues"] if i["code"] == "same-name"],
         "and check has no name said twice")
    shown = t.box.run("show", "pizza", expect=None)
    t.ok(shown.returncode == 0 and "Notes/" in shown.stdout, "./os show pizza answers straight away")


@test
def test_a_work_folder_with_its_own_decisions_keeps_them_in_one_place(t: Case) -> None:
    """A Work folder made by hand whose README already kept its decisions got
    a card beside it, and the next decision went into the card: two files of
    decisions (stranger test, 2026-09-30). The header goes on that README,
    and every line of theirs stays."""
    root = t.box.root
    reno = root / "Work" / "Kitchen Reno"
    reno.mkdir(parents=True)
    theirs = ("# Kitchen reno\n\nNew worktops and tiles.\n\n## Next action\n- [ ] Call the tiler\n\n"
              "## Decisions\n- 2026-09-20 · oak worktops, not granite\n\n"
              "## Log\n- 2026-09-20 · measured up\n")
    (reno / "README.md").write_text(theirs)
    (reno / "quote.txt").write_text("Harlow Joinery: 2400 pounds\n")
    # Without any of those sections, the README is still theirs alone.
    plain = root / "Work" / "Bike Repair"
    plain.mkdir()
    (plain / "README.md").write_text("# Bike repair\n\nThe back brake squeals.\n")
    t.box.run("sort")
    t.ok(not (root / "Work" / "Kitchen Reno.card.md").exists(), "no card beside it")
    text = (reno / "README.md").read_text()
    meta, body = engine.parse_frontmatter(text)
    t.eq((meta.get("type"), meta.get("title")), ("work", "Kitchen Reno"), "the header is on its README")
    t.eq(body.strip(), theirs.strip(), "and every line of theirs is as it was")
    t.ok((root / "Work" / "Bike Repair.card.md").exists()
         and (plain / "README.md").read_text() == "# Bike repair\n\nThe back brake squeals.\n",
         "a README with no decisions, log or next action keeps a card beside it")
    t.box.run("decide", "Kitchen Reno", "white tiles, not green")
    shown = t.box.run("show", "Kitchen Reno").stdout
    t.ok("oak worktops" in shown and "white tiles" in shown and "Call the tiler" in shown,
         f"./os show has both decisions and the next action\n{shown}")
    t.ok("white tiles" in (reno / "README.md").read_text(), "the new decision is in the same file")
    t.box.run("undo")
    t.box.run("undo")
    t.eq((reno / "README.md").read_text(), theirs, "undo puts their README back word for word")


@test
def test_something_moved_into_archive_by_hand_is_named(t: Case) -> None:
    """A project dragged straight into Archive was gone from the list, show
    and find, and check said all good (stranger test, 2026-09-30). Check
    names anything in Archive that isn't where ./os close puts things, with
    the command that closes it properly. What ./os close filed, and a
    year's folder of older notes like this workshop's, are not named."""
    root = t.box.root
    t.box.run("new", "work", "Kitchen Reno")
    t.box.run("new", "work", "Bathroom")
    t.box.run("close", "Bathroom")
    (root / "Work" / "Kitchen Reno").rename(root / "Archive" / "Kitchen Reno")
    (root / "Archive" / "2026" / "Shed").mkdir(parents=True)
    (root / "Archive" / "2026" / "Shed" / "README.md").write_text("# Shed\n\nNew felt on the roof.\n")
    (root / "Archive" / "boiler manual.txt").write_text("Bleed the radiators each autumn.\n")
    older = root / "Archive" / "2026" / "old-layout"
    older.mkdir(parents=True)
    (older / "subjects.md").write_text("# Subjects\n\nWhat the old layout kept.\n")
    issues = [i for i in t.box.json("check", expect=None)["issues"] if i["code"] == "archived-by-hand"]
    t.eq(sorted(i["path"] for i in issues),
         ["Archive/2026/Shed", "Archive/Kitchen Reno", "Archive/boiler manual.txt"],
         "check names each thing put in Archive by hand, and nothing else")
    t.ok(all("by hand" in i["message"] and "./os close" in i["fix"] for i in issues),
         f"each says what happened and how to close it ({issues})")
    env = dict(os.environ, ZENITH_HOME=str(root), NO_COLOR="1")
    for issue in issues:
        done = subprocess.run(issue["fix"].replace("./os ", f"{shlex.quote(str(root / 'os'))} "),
                              shell=True, cwd=str(root), env=env, capture_output=True, text=True)
        t.eq(done.returncode, 0, f"`{issue['fix']}` works as given\n{done.stdout}{done.stderr}")
    t.eq([i for i in t.box.json("check", expect=None)["issues"] if i["code"] == "archived-by-hand"], [],
         "afterwards check has nothing more to say")
    for word, where in (("kitchen", "Archive/2026/Work/Kitchen Reno"),
                        ("felt", "Archive/2026/Work/Shed"),
                        ("radiators", "Archive/2026/Notes/boiler manual.txt")):
        t.eq([h["path"] for h in t.box.json("find", word)][:1], [where], f"./os find {word} reaches it")


@test
def test_a_file_dropped_in_keeps_its_own_name_and_ending(t: Case) -> None:
    """Sort renamed `Shopping List.txt` to shopping-list.md, and TextEdit,
    saving it again, made a second copy (stranger test, 2026-09-30). A file
    dropped in by hand keeps its name and its ending, and is found by what
    it says; one the computer named is named after what it says."""
    notes = t.box.root / "Notes"
    shopping = notes / "Shopping List.txt"
    shopping.write_text("eggs\nmilk\nsourdough starter\n")
    (notes / "Garden Plan.rtf").write_text(r"{\rtf1\ansi Tomatoes by the south wall}")
    lease = notes / "Lease notes.md"
    lease.write_text("# Lease\n\nThe landlord is Mr Pemberton.\n")
    (notes / "Untitled.txt").write_text("Quinces and medlars for the jelly\n")
    t.box.run("sort")
    t.ok(shopping.is_file() and lease.is_file() and (notes / "Garden Plan.rtf").is_file(),
         f"each keeps its name and ending ({sorted(p.name for p in notes.iterdir())})")
    t.ok(shopping.read_text().startswith("---\ntitle: Shopping List\n")
         and shopping.read_text().endswith("eggs\nmilk\nsourdough starter\n"),
         "a .txt takes its header inside, with every word of it kept")
    t.ok((notes / "quinces-and-medlars-for-the-jelly.txt").is_file(),
         "one the computer named is named after what it says, ending and all")
    for word, where in (("sourdough", "Notes/Shopping List.txt"), ("tomatoes", "Notes/Garden Plan.rtf"),
                        ("pemberton", "Notes/Lease notes.md")):
        t.eq([h["path"] for h in t.box.json("find", word)][:1], [where], f"./os find {word} finds it")

    # TextEdit saves it again under its name, without the header.
    shopping.write_text("eggs\nmilk\nsourdough starter\nbutter\n")
    t.box.run("sort")
    t.eq(sorted(p.name for p in notes.glob("Shopping*")), ["Shopping List.txt"],
         "saved again, it is still one file")
    t.eq([h["path"] for h in t.box.json("find", "butter")][:1], ["Notes/Shopping List.txt"],
         "with what was added found")
    t.ok("nothing waiting" in t.box.run("sort").stdout, "and sort leaves it be after")

    # One of the same name left at the top goes beside it, as Finder names it.
    (t.box.root / "Shopping List.txt").write_text("birthday candles\n")
    t.box.run("sort")
    t.eq(sorted(p.name for p in notes.glob("Shopping*")), ["Shopping List 2.txt", "Shopping List.txt"],
         "one of the same name from elsewhere is called Shopping List 2")
    t.ok("nothing waiting" in t.box.run("sort").stdout
         and (notes / "Shopping List 2.txt").is_file(), "and keeps that name")

    # Words given to ./os save still get a tidy name.
    t.box.run("save", "Remember the boiler service in October")
    t.ok((notes / "remember-the-boiler-service-in-october.md").is_file(),
         "words saved are named as they always were")


@test
def test_a_text_file_that_reads_like_work_keeps_its_name(t: Case) -> None:
    """`To Do.txt` left at the top became Work/To Do/README.md, and one in
    Notes the same, so the file TextEdit had open was gone (review,
    2026-09-30). It goes into a folder of its name in Work, as it is, with
    a page of ours beside it."""
    root = t.box.root
    words = ("Book the plumber for the boiler\nCall the council about the bins\n"
             "Finish the tax return by Friday\n")
    (root / "To Do.txt").write_text(words)
    (root / "Notes" / "Things to do.txt").write_text(
        "- [ ] Paint the fence\n- [ ] Fix the gate hinge\nNeed to get these done before the party.\n")
    t.box.run("sort")
    kept = root / "Work" / "To Do" / "To Do.txt"
    t.eq(kept.read_text() if kept.is_file() else sorted(p.name for p in (root / "Work").rglob("*")),
         words, "it keeps its name, its ending and every word, with nothing added")
    t.ok((root / "Work" / "Things to do" / "Things to do.txt").is_file(), "so does one from Notes")
    listed = t.box.run().stdout
    t.ok("To Do" in listed and "Things to do" in listed, f"each is on the list as work:\n{listed}")
    t.ok("Book the plumber for the boiler" in t.box.run("show", "To Do").stdout,
         "with its first line as the next action")
    t.eq([h["path"] for h in t.box.json("find", "hinge")][:1], ["Work/Things to do"],
         "found by what it says")
    t.ok("nothing waiting" in t.box.run("sort").stdout, "and sort leaves it be after")


@test
def test_a_programs_document_left_at_the_top_keeps_its_ending(t: Case) -> None:
    """`Garden Plan.rtfd` left at the top was filed as Notes/garden-plan-rtfd,
    which a Mac shows as a plain folder, and `Novel.scriv` got a README of
    ours inside it (review, 2026-09-30). A blank copy has no Notes folder,
    so the top is where TextEdit's Save puts a note with a picture in it."""
    root, notes = t.box.root, t.box.root / "Notes"
    b = "\\"
    rtfd = root / "Garden Plan.rtfd"
    rtfd.mkdir()
    (rtfd / "TXT.rtf").write_text(b.join(["{", "rtf1", "ansi Tomatoes by the south wall.}"]))
    (rtfd / "shed.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(40))
    (root / "Novel.scriv" / "Files").mkdir(parents=True)
    (root / "Novel.scriv" / "Files" / "version.txt").write_text("23\n")

    def inside(folder: Path) -> dict:
        return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}

    was = {name: inside(root / name) for name in ("Garden Plan.rtfd", "Novel.scriv")}
    t.box.run("sort")
    for name, files in was.items():
        t.ok((notes / name).is_dir() and (notes / f"{name}.card.md").is_file(),
             f"{name} keeps its name and ending, with a card beside it "
             f"({sorted(p.name for p in notes.iterdir())})")
        t.eq(inside(notes / name) if (notes / name).is_dir() else {}, files,
             f"and nothing in {name} is changed or added")
    t.eq([h["path"] for h in t.box.json("find", "tomatoes")][:1], ["Notes/Garden Plan.rtfd"],
         "./os find reads it")

    # Saved again under its name, it goes beside it, as Finder names it.
    rtfd.mkdir()
    (rtfd / "TXT.rtf").write_text(b.join(["{", "rtf1", "ansi Leeks.}"]))
    t.box.run("sort")
    t.ok((notes / "Garden Plan 2.rtfd" / "TXT.rtf").is_file(), "the second is Garden Plan 2.rtfd")
    t.ok("nothing waiting" in t.box.run("sort").stdout, "and sort leaves them be after")

    # Dropped into Work, it is kept whole too: no page of ours goes in it.
    (root / "Work" / "Song.band" / "Media").mkdir(parents=True)
    (root / "Work" / "Song.band" / "Media" / "take1.aif").write_bytes(b"FORM" + bytes(40))
    t.box.run("sort")
    homes = [p for p in (root / "Work" / "Song.band", notes / "Song.band") if p.is_dir()]
    t.ok(len(homes) == 1 and inside(homes[0]).keys() == {"Media/take1.aif"},
         f"a GarageBand song gets nothing of ours inside it ({homes})")


@test
def test_a_folder_named_like_a_group_is_never_made_one(t: Case) -> None:
    """Notes/Money, made by hand with two money notes in it: once Notes
    grouped by subject, sort stopped on a disk error, moving Money into
    itself, and left a group mark in it; the next sort filled it with their
    other money notes as a group of its own (review, 2026-09-30). The
    released ./os never hit it: it renamed such folders after a note."""
    notes = t.box.root / "Notes"
    t.box.run("save", "Council tax is 1840 a year")
    t.box.run("save", "ISA allowance is 20k")
    money = notes / "Money"
    money.mkdir()
    for p in list(notes.glob("council-*.md")) + list(notes.glob("isa-*.md")):
        p.rename(money / p.name)
    t.box.run("sort")
    theirs = sorted(p.name for p in money.iterdir())
    for words in ("Pension transfer value is 31k", "Mortgage fix ends in March 2027",
                  "Energy bill: switched to Octopus, 120 a month",
                  "Savings: emergency fund target 6 months", "Budget: groceries 400 a month",
                  "Car insurance renewal is 540 this year",
                  "Dentist: Dr Okafor, check-up every 6 months", "Running: 5k in 27 minutes",
                  "Poem draft: the heron on the canal", "Novel chapter 3: lighthouse keeper",
                  "Wifi password is on the router", "Houseplant care: water the fig weekly",
                  "Tax code 1257L on my payslip"):
        t.box.run("save", words)
    got = t.box.run("sort", expect=None)
    t.eq(got.returncode, 0, f"sort finishes\n{got.stdout}{got.stderr}")
    t.ok(any((p / ".category").is_file() for p in notes.iterdir()), "Notes is grouped by subject")
    t.ok(not (money / ".category").exists(), "their folder is never marked as a group of sort's")
    t.eq(sorted(p.name for p in money.iterdir()), theirs, "and nothing of sort's goes in it")
    t.ok("Notes/Money" in {i["path"] for i in t.box.items()}, "it stays where they made it")
    t.ok("nothing waiting" in t.box.run("sort").stdout and not (money / ".category").exists(),
         "and the next sort leaves it so")
    t.ok(any(h["path"] == "Notes/Money" for h in t.box.json("find", "council")), "found as before")


@test
def test_a_readme_saying_what_a_folder_is_for_is_left_alone(t: Case) -> None:
    """A README.md at the top of Notes or Work says what the folder is for,
    like the old layout's notes/README.md. After an update ./os said it
    needed fixing, --fix renamed it, and sort filed "# My notes" as work on
    the go (review, 2026-09-30)."""
    root = t.box.root
    said = {root / "Notes" / "README.md": "# My notes\n\nEverything I want to look up later lives here.\n",
            root / "Work" / "README.md": "# Work\n\nWhat I'm carrying right now.\n"}
    for path, text in said.items():
        path.write_text(text)
    t.box.run("save", "The plumber's number is 0161 555 0100")
    t.ok("fixing" not in t.box.run().stdout, "./os doesn't call either broken")
    t.eq(_unreachable(t.box).get("passed-over", []), [], "check says nothing about them")
    t.box.run("check", "--fix", expect=None)
    t.box.run("sort")
    t.eq({str(p): p.read_text() if p.is_file() else None for p in said},
         {str(p): text for p, text in said.items()}, "--fix and sort leave both as they are")
    titles = {i["title"] for i in t.box.items()}
    t.ok("My notes" not in titles and "Work README" not in titles and "Notes README" not in titles,
         f"and neither is filed as a thing of its own ({sorted(titles)})")


@test
def test_a_card_in_a_sources_folder_is_never_moved(t: Case) -> None:
    """check --fix put a card left behind back beside its file even when
    the card was in a sources/ folder, which nothing may change, and left
    that folder empty (review, 2026-09-30)."""
    notes = t.box.root / "Notes"
    (notes / "Sleep" / "sources").mkdir(parents=True)
    (notes / "Sleep" / "what-i-think.md").write_text(
        "---\ntitle: Sleep\ntype: note\nstatus: —\ndomain: health\ntags: []\n"
        "created: 2026-09-01\nupdated: 2026-09-01\n---\n\n# Sleep\n\nMorning light matters.\n")
    card = notes / "Sleep" / "sources" / "walker.pdf.card.md"
    text = ("---\ntitle: Walker paper\ntype: file\nstatus: —\ndomain: health\ntags: []\n"
            "created: 2026-09-01\nsource: walker.pdf\n---\n\n# Walker paper\n\nAbout REM.\n")
    card.write_text(text)
    (notes / "Papers").mkdir()
    (notes / "Papers" / "walker.pdf").write_bytes(b"%PDF-1.4\n\x00\x01\n")
    t.box.run("sort")
    left = _unreachable(t.box).get("card-left-behind", [])
    t.ok(left and all(i["level"] == "hint" for i in left) and left[0]["fix"].startswith("cp -n "),
         f"check says so, with a command that copies it, not one that moves it ({left})")
    t.box.run("check", "--fix", expect=None)
    t.eq(card.read_text() if card.is_file() else None, text, "--fix leaves it as it is, where it is")
    subprocess.run(["bash", "-c", left[0]["fix"]], cwd=str(t.box.root), check=True)
    t.ok((notes / "Papers" / "walker.pdf.card.md").is_file() and card.read_text() == text,
         "the command puts a copy beside the paper and keeps the one in sources")
    (card.parent / "gone.pdf.card.md").write_text(text.replace("walker", "gone"))
    t.eq(_unreachable(t.box).get("card-left-behind", []), [],
         "one whose file is nowhere is never offered for removal")


@test
def test_sort_says_where_things_land_and_a_preview_counts_the_same(t: Case) -> None:
    """A preview said "1 folder brought in · 2 filed" where sort then said
    "3 filed", and regrouping printed two notes of one name as landing on
    the same Notes/Food/Shopping List.txt, when the second was given
    another name, which the next sort made shopping-list.txt (review,
    2026-09-30)."""
    root, notes = t.box.root, t.box.root / "Notes"
    top = root / "My Recipes"
    top.mkdir()
    (top / "Pizza.md").write_text("500g flour, semolina\n")
    (top / "Chilli oil.md").write_text("Dried chillies and oil.\n")
    (root / "Seed list.txt").write_text("Carrots, parsnips and beetroot for the spring.\n")
    filed = [re.search(r"(\d+) filed", t.box.run("sort", *how).stdout) for how in (["--dry-run"], [])]
    t.ok(all(filed) and filed[0].group(1) == filed[1].group(1),
         f"the preview counts what sort then files ({[m and m.group(0) for m in filed]})")

    for words in ("Pension transfer value is 31k", "Mortgage fix ends in March 2027",
                  "Dentist: Dr Okafor, check-up every 6 months", "Running: 5k in 27 minutes",
                  "Poem draft: the heron on the canal", "Novel chapter 3: lighthouse keeper",
                  "Wifi password is on the router", "Houseplant care: water the fig weekly",
                  "Sourdough starter: feed it every morning", "Roast chicken takes 90 minutes",
                  "Tax code 1257L on my payslip", "Energy bill is 120 a month"):
        t.box.run("save", words)
    (notes / "Shopping List.txt").write_text("eggs\nmilk\nsourdough starter\nflour for bread\n")
    t.box.run("sort")
    first = next((p for p in notes.rglob("Shopping List.txt")), None)
    t.ok(first is not None and first.parent != notes, f"it went into a group ({first})")
    (notes / "Shopping List.txt").write_text("eggs\nmilk\nsourdough starter\nflour for bread\nbutter\n")
    moves = t.box.json("sort")["moves"]
    now = next(p for p in notes.rglob("Shopping*.txt") if "butter" in p.read_text())
    t.ok(now.parent == first.parent and now != first, f"the second went beside it ({now})")
    landed = [dst for what, src, dst in moves if what == "sort" and src == "Notes/Shopping List.txt"]
    t.eq(landed, [str(now.relative_to(root))], "and sort says where it really went")
    t.box.run("sort")
    t.ok(now.is_file(), "the next sort keeps the name it was given there, not a lower-case one "
                        f"({sorted(p.name for p in now.parent.iterdir())})")


@test
def test_one_name_in_two_folders_asks_which(t: Case) -> None:
    """Pizza in Work and pizza.md in Notes: an error for good, whose fix,
    ./os sort, changed nothing, and ./os show reached only the first. Now it
    is a hint at most, and a command given the name asks which one, with
    the command for each (settled 2026-09-30)."""
    t.box.run("new", "work", "Pizza")
    (t.box.root / "Notes" / "pizza.md").write_text(
        "---\ntitle: Pizza\ntype: note\nstatus: —\ndomain: food\ntags: []\n"
        "created: 2026-09-30\nupdated: 2026-09-30\n---\n\n# Pizza\n\nthin crust, semolina\n")
    t.box.run("sort")
    result = t.box.json("check", expect=0)
    codes = {i["code"]: i for i in result["issues"]}
    t.ok("duplicate-id" not in codes and "same-name" in codes
         and codes["same-name"]["level"] == "hint", f"a hint, not an error ({codes})")
    t.ok("fixing" not in t.box.run().stdout, "./os doesn't say it needs fixing")
    t.ok("--fix" not in t.box.run("check").stdout, "and check doesn't offer --fix for it")

    for verb in ("show", "open", "edit", "close", "hold", "rename"):
        asked = t.box.run(verb, "pizza", *(["Pie"] if verb == "rename" else []), expect=2)
        t.ok("2 things here are called pizza" in asked.stderr
             and "Work/Pizza" in asked.stderr
             and "Notes/pizza.md" in asked.stderr, f"{verb} asks which:\n{asked.stderr}")
    t.ok((t.box.root / "Work" / "Pizza" / "README.md").exists()
         and (t.box.root / "Notes" / "pizza.md").exists(), "nothing was changed")

    asked = t.box.run("show", "pizza", expect=2).stderr
    for line in asked.splitlines()[1:]:
        command, where = line.strip().rsplit("   ", 1)
        said = command.split(" ", 2)[2].strip().strip('"')
        t.ok(where.strip() in t.box.run("show", said).stdout, f"{command} reaches {where.strip()}")
    t.box.run("close", "Work/Pizza")
    t.ok(not (t.box.root / "Work" / "Pizza").exists()
         and (t.box.root / "Notes" / "pizza.md").exists(),
         "the folder with the name picks the right one")
    t.ok("Notes/pizza.md" in t.box.run("show", "pizza").stdout,
         "and the name is one thing's again")


@test
def test_a_shared_name_is_printed_so_it_reaches_one_thing(t: Case) -> None:
    """The front screen and the brief printed `./os show garden` beside the
    Garden in Work while Notes had a Garden too, and that stopped with "which
    one?". After a close, the `./os back website` printed brought out nothing
    while a live Website was there, and `./os back Archive/Website` found
    nothing either (review, 2026-09-30)."""
    t.box.run("new", "work", "Garden")
    (t.box.root / "Notes" / "Garden.md").write_text(
        "---\ntitle: Garden\ntype: note\nstatus: —\ndomain: home\ntags: []\n"
        "created: 2026-09-30\nupdated: 2026-09-30\n---\n\n# Garden\n\ntulips\n")
    front = t.box.run().stdout
    t.ok("./os show Work/Garden" in front and "./os show garden" not in front,
         f"the front screen gives the name with its folder:\n{front}")
    brief = t.box.run("brief").stdout
    t.ok("[Work/Garden]" in brief and "[garden]" not in brief, f"and so does the brief:\n{brief}")
    t.ok("GARDEN" in t.box.run("show", "Work/Garden").stdout.upper(), "which reaches it")

    t.box.run("new", "work", "Website")
    closed = t.box.run("close", "website").stdout
    t.ok("./os back website" in closed, f"close says how to bring it back:\n{closed}")
    t.box.run("new", "work", "Website")
    t.box.run("back", "website")
    t.ok(not any((t.box.root / "Archive").rglob("README.md"))
         and (t.box.root / "Work" / "Website").is_dir(),
         "and that brings out the one put away, not the live one")
    t.box.run("close", "website")
    t.box.run("back", "Archive/Website")
    t.ok(not any((t.box.root / "Archive").rglob("README.md")),
         "the folders it is in, in order, reach it too")

    # Two dozen of one name: the first dozen, and how the rest are told apart.
    # Sort never gives two things in one folder the same name, so these are
    # a note called Ideas in each of 14 groups, marked the way sort marks one.
    for n in range(14):
        group = t.box.root / "Notes" / f"Client {n:02}"
        group.mkdir()
        (group / ".category").write_text(json.dumps(
            {"name": group.name, "trail": [group.name], "auto": True}) + "\n")
        (group / "ideas.md").write_text(
            "---\ntitle: Ideas\ntype: note\nstatus: —\ndomain: business\ntags: []\n"
            f"created: 2026-09-30\nupdated: 2026-09-30\n---\n\n# Ideas\n\nclient {n}\n")
    asked = t.box.run("show", "ideas", expect=2).stderr
    t.ok("14 things here are called ideas" in asked and asked.count("./os show") == 12
         and "and 2 more" in asked, f"a dozen, and how many more:\n{asked}")
    # The hint in check works out the commands for the three it shows, not
    # for every copy: 200 of them made ./os take three seconds.
    os_ = engine.Zenith(t.box.root)
    calls = []
    was = engine.Finder.qualified
    engine.Finder.qualified = lambda self, *a, **k: calls.append(1) or was(self, *a, **k)
    try:
        engine.Doctor(os_).run()
    finally:
        engine.Finder.qualified = was
    t.ok(len(calls) <= 3 * 3, f"check names three of each shared name ({len(calls)} worked out)")


@test
def test_notes_of_their_own_are_never_taken_for_hidden_work(t: Case) -> None:
    """An Obsidian vault in Notes, one thing by its README, holding PARA
    notes that say `type: area` / `status: active`: check called each one
    work hidden in a folder, ./os said "2 things need fixing" on every run,
    and check --fix moved them out of the vault into Work (review,
    2026-09-30). Only work filed by ./os, inside a piece of work, is that."""
    vault = t.box.root / "Notes" / "Second Brain"
    (vault / "Areas").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "app.json").write_text("{}\n")
    (vault / "README.md").write_text("# My second brain\n")
    for area in ("Health", "Finances"):
        (vault / "Areas" / f"{area}.md").write_text(
            f"---\ntype: area\nstatus: active\ncreated: 2024-03-01\n---\n\n# {area}\n")
    t.box.run("sort")
    # The same notes inside a piece of work of ours: still theirs.
    t.box.run("new", "work", "Wedding")
    shutil.copytree(vault / "Areas", t.box.root / "Work" / "Wedding" / "Areas")
    t.eq(_unreachable(t.box).get("work-inside-work", []), [], "check says nothing about them")
    t.ok("fixing" not in t.box.run().stdout, "nor does ./os")
    t.box.run("check", "--fix", expect=None)
    t.ok((vault / "Areas" / "Health.md").is_file()
         and (t.box.root / "Work" / "Wedding" / "Areas" / "Health.md").is_file()
         and not (t.box.root / "Work" / "Health.md").exists(),
         "and check --fix leaves them where they are")


@test
def test_a_huge_folder_is_pointed_at_work_content_safely(t: Case) -> None:
    """For a folder of more files than search matches the names of, check
    said `mv "Notes/Phone Export" "Work/Content/"`. With no Work/Content yet,
    that renamed the folder to Work/Content, its name gone, and left its card
    behind in Notes (review, 2026-09-30)."""
    export = t.box.root / "Notes" / "Phone Export"
    for year in ("2020", "2021"):
        (export / year).mkdir(parents=True)
        for n in range(30):
            (export / year / f"IMG_{n:04}.HEIC").write_bytes(b"\x00" * 16)
    t.box.run("sort")
    t.ok((t.box.root / "Notes" / "Phone Export.card.md").is_file(), "one thing, with a card")
    os_ = engine.Zenith(t.box.root)
    was = engine.Finder.NAMES_LOOKED
    engine.Finder.NAMES_LOOKED = 20        # as if it held tens of thousands
    try:
        issues = engine.Doctor(os_).run()["issues"]
    finally:
        engine.Finder.NAMES_LOOKED = was
    far = [i for i in issues if i["code"] == "too-far-in"]
    t.ok(len(far) == 1 and far[0]["level"] == "hint", f"said, not counted as broken ({far})")
    t.ok(not (t.box.root / "Work" / "Content").exists(), "there is no Work/Content yet")
    cure = far[0]["fix"]          # the whole line, pasted as it is
    subprocess.run(["bash", "-c", cure], cwd=str(t.box.root), check=True)
    moved = t.box.root / "Work" / "Content" / "Phone Export"
    t.ok((moved / "2021" / "IMG_0001.HEIC").is_file(), f"it keeps its name ({cure})")
    t.ok((t.box.root / "Work" / "Content" / "Phone Export.card.md").is_file()
         and not (t.box.root / "Notes" / "Phone Export.card.md").exists(),
         "and its card goes with it")


@test
def test_a_big_piece_of_work_is_never_pointed_at_work_content(t: Case) -> None:
    """For a piece of work with more files than search matches the names of,
    check said to move the whole of it into Work/Content. Done, it was off
    the list, and show and find lost its next action, log and notes, while
    check said all good (review, 2026-09-30). It points at the big folder
    inside it instead, or at nothing."""
    t.box.run("new", "work", "Wedding video")
    work = t.box.root / "Work" / "Wedding Video"
    (work / "shot list.md").write_text("First dance, then the speeches.\n")
    for folder in ("Frames", "Frames B"):
        (work / "Exports" / folder).mkdir(parents=True)
        for n in range(30):
            (work / "Exports" / folder / f"frame_{n:04}.png").write_bytes(b"\x89PNG")
    os_ = engine.Zenith(t.box.root)
    was = engine.Finder.NAMES_LOOKED
    engine.Finder.NAMES_LOOKED = 20        # as if it held tens of thousands
    try:
        issues = engine.Doctor(os_).run()["issues"]
    finally:
        engine.Finder.NAMES_LOOKED = was
    far = [i for i in issues if i["code"] == "too-far-in"]
    t.ok(len(far) == 1 and far[0]["level"] == "hint", f"said, not counted as broken ({far})")
    cure = far[0]["fix"]
    t.ok('"Work/Wedding Video"' not in cure and "Work/Wedding Video/Exports" in cure,
         f"it points at the big folder in it, not the piece of work ({cure})")
    subprocess.run(["bash", "-c", cure], cwd=str(t.box.root), check=True)
    t.ok((work / "README.md").is_file() and (work / "shot list.md").is_file(),
         "the piece of work stays where it is")
    t.ok((t.box.root / "Work" / "Content" / "Wedding Video" / "Exports" / "Frames B"
          / "frame_0001.png").is_file(), "and its exports go to Work/Content")
    t.ok("Wedding video" in t.box.run().stdout, "still on the list")
    t.eq([h["path"] for h in t.box.json("find", "speeches")][:1], ["Work/Wedding Video"],
         "and found")


@test
def test_an_older_sorts_page_never_hides_notes(t: Case) -> None:
    """Sort gave Notes/My Recipes a README of ours, so it is one thing, and
    the words of only the first 40 notes in it are read: `spice45` was
    "corrected" to spice35 and showed the wrong note, while check said
    nothing (review, 2026-09-30). check says so. It is how sort filed it, so
    it is said, not counted as broken, and nothing is moved or renamed."""
    recipes = t.box.root / "Notes" / "My Recipes"
    recipes.mkdir(parents=True)
    page = ("---\ntitle: My Recipes\ntype: note\nstatus: —\ndomain: food\ntags: []\n"
            "created: 2026-09-01\nupdated: 2026-09-01\nsummary: thin crust\n---\n\n"
            "# My Recipes\n\nthin crust\n\n## In one line\n\n## What it says\n")
    (recipes / "README.md").write_text(page)
    for n in range(1, 45):
        (recipes / f"Recipe {n:02}.md").write_text(f"spice{n:02} and more words\n")
    (recipes / "Zucchini fritters.md").write_text("spice45 and more words\n")
    t.box.run("sort")
    before = sorted(str(p.relative_to(t.box.root)) for p in (t.box.root / "Notes").rglob("*"))
    far = [i for i in _unreachable(t.box).get("too-far-in", []) if i["path"] == "Notes/My Recipes"]
    t.ok(len(far) == 1 and far[0]["level"] == "hint" and "past the 40" in far[0]["message"]
         and "Notes/My Recipes/Recipe" in far[0]["message"], f"check says so ({far})")
    t.ok("fixing" not in t.box.run().stdout, "and ./os doesn't count it as broken")
    t.box.run("check", "--fix", expect=None)
    t.box.run("sort")
    t.eq(sorted(str(p.relative_to(t.box.root)) for p in (t.box.root / "Notes").rglob("*")),
         before, "nothing is moved or renamed")
    t.eq((recipes / "README.md").read_text(), page, "and its page is as it was")
    hits = t.box.json("find", "zucchini")
    t.eq([(h["path"], h.get("file")) for h in hits][:1],
         [("Notes/My Recipes", "Notes/My Recipes/Zucchini fritters.md")],
         "each note in it is still found by its name, the last one too")


@test
def test_a_card_left_behind_goes_back_beside_its_file(t: Case) -> None:
    """A saved PDF dragged into a folder in Finder leaves its card behind,
    and the card holds the only words search reads for it: `worcester`
    found nothing, while check said all good (review, 2026-09-30)."""
    outside = t.box.tmp / "scan0042.pdf"
    outside.write_bytes(b"%PDF-1.4\n\x00\x01\n")
    t.box.run("save", str(outside))
    notes = t.box.root / "Notes"
    with (notes / "scan0042.pdf.card.md").open("a") as fh:
        fh.write("\nBoiler warranty certificate from Worcester Bosch\n")
    t.ok(t.box.json("find", "worcester"), "found by its card")
    (notes / "Taxes").mkdir()
    for name in ("P60 2024.pdf", "Self assessment.pdf"):
        (notes / "Taxes" / name).write_bytes(b"%PDF-1.4\n\x00\n")
    t.box.run("sort")
    (notes / "scan0042.pdf").rename(notes / "Taxes" / "scan0042.pdf")
    t.eq(t.box.json("find", "worcester", expect=None), [], "moved, what its card says is lost")
    left = _unreachable(t.box).get("card-left-behind", [])
    t.ok(len(left) == 1 and left[0]["path"] == "Notes/scan0042.pdf.card.md"
         and "Notes/Taxes" in left[0]["message"] and left[0]["fix"].startswith("./os check --fix"),
         f"check says where it went, and the fix ({left})")
    t.ok("1 thing needs fixing" in t.box.run().stdout, "./os counts it")
    t.box.run("check", "--fix", expect=None)
    t.ok((notes / "Taxes" / "scan0042.pdf.card.md").is_file(), "--fix puts it beside its file")
    hits = t.box.json("find", "worcester")
    t.ok(hits and hits[0].get("file", hits[0]["path"]).endswith("Taxes/scan0042.pdf"),
         f"and it is found again, as that file ({hits})")
    t.box.run("undo")
    t.ok((notes / "scan0042.pdf.card.md").is_file(), "./os undo puts it back")

    # Into a Notes folder sort filed as one thing: the card is read in there.
    t.box.run("check", "--fix", expect=None)
    (notes / "Recipes").mkdir()
    (notes / "Recipes" / "Pizza.md").write_text("flour\n")
    (notes / "Recipes" / "Soup.md").write_text("leeks\n")
    t.box.run("sort")
    manual = t.box.tmp / "manual.pdf"
    manual.write_bytes(b"%PDF-1.4\n\x00\n")
    t.box.run("save", str(manual))
    with (notes / "manual.pdf.card.md").open("a") as fh:
        fh.write("\nOven manual from Neff\n")
    (notes / "manual.pdf").rename(notes / "Recipes" / "manual.pdf")
    t.box.run("check", "--fix", expect=None)
    hits = t.box.json("find", "neff")
    t.ok([(h["path"], h.get("file")) for h in hits] == [("Notes/Recipes", "Notes/Recipes/manual.pdf")],
         f"a card put back beside its file is found again, as that file ({hits})")

    # One whose file is gone for good is said, and nothing is moved.
    (notes / "Taxes" / "scan0042.pdf").unlink()
    gone = _unreachable(t.box).get("card-left-behind", [])
    t.ok(len(gone) == 1 and gone[0]["level"] == "hint", f"a card with no file is a hint ({gone})")
    t.ok("fixing" not in t.box.run().stdout, "not something to fix")


@test
def test_a_broken_settings_file_says_which_one(t: Case) -> None:
    """These are files people hand-edit, so a typo must name itself."""
    for name in ("words.json", "config.json"):
        path = t.box.root / ".os" / name
        good = path.read_text()
        path.write_text('{ "oops": ')
        proc = t.box.run("status", expect=1)
        t.ok(f".os/{name}" in proc.stderr, f"a broken {name} names {name}")
        t.ok("comma" in proc.stderr or "quote" in proc.stderr,
             f"a broken {name} suggests what to look for")
        path.write_text(good)

    # a missing state file is rebuilt rather than fatal
    (t.box.root / ".os" / "state.json").unlink()
    t.box.run("sort")
    t.ok((t.box.root / ".os" / "state.json").exists(), "a lost state file is rebuilt")


@test
def test_two_things_can_share_a_title(t: Case) -> None:
    """Identical titles once crashed the check that exists to find them.

    Different words under one title, so this is the near-duplicate case and not
    the plain duplicate-content one: three files saying exactly the same thing
    are a different finding, reported elsewhere."""
    for n, name in enumerate(("Untitled.md", "Untitled 2.md", "Untitled 3.md")):
        (t.box.root / "Notes" / name).write_text(
            f"# Same Title Here\n\nReference material, written out {n} times.\n")
    t.box.run("sort")
    result = t.box.json("check", expect=None)
    t.ok(any(i["code"] == "near-duplicate" for i in result["issues"]),
         "three items with one title are reported, not fatal")
    names = sorted(i["id"] for i in t.box.items() if i["title"] == "Same Title Here")
    t.eq(len(set(names)), 3, f"and each got a name of its own ({names})")


@test
def test_data_named_as_prose_is_still_data(t: Case) -> None:
    """A .md file full of bytes must not be read as prose."""
    (t.box.root / "Notes" / "screenshot.md").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(400))
    (t.box.root / "Notes" / "real.md").write_text("# Real note\n\nAbout the billing deadline.\n")
    t.box.run("sort")
    placed = {Path(i["path"]).name: i for i in t.box.items() if i["kind"] in ("asset", "note")}
    binary = next((i for n, i in placed.items() if "screenshot" in n), None)
    t.ok(binary is not None, "the binary file was filed somewhere")
    t.eq(binary["kind"], "asset", "a .md full of bytes is kept as a file, not read as text")
    t.ok("screenshot" in binary["title"].lower(),
         f"it keeps a sane name instead of noise (got {binary['title']!r})")


@test
def test_text_in_an_older_encoding_keeps_its_accents(t: Case) -> None:
    """A Latin-1 or old Windows text file is not UTF-8, and writing a header
    into it turned é, à and € into '?' boxes in the file itself.

    It is kept byte for byte, with a card beside it like any other file."""
    raw = b"Caf\xe9 menu \xe0 la carte\n\nSoup 12\x80 and bread, all week\n"
    (t.box.root / "Notes" / "latin1 menu.txt").write_bytes(raw)
    outside = t.box.tmp / "old price list.txt"
    outside.write_bytes(raw.replace(b"Soup", b"Stew"))
    t.box.run("sort")
    t.box.run("save", str(outside))

    for word, how in ((b"Soup", "dropped in and sorted"), (b"Stew", "brought in with ./os save")):
        kept = [p for bucket in ("Notes", "Work", "Archive")
                for p in (t.box.root / bucket).rglob("*")
                if p.is_file() and word + b" 12\x80" in p.read_bytes()]
        t.eq(len(kept), 1, f"the file {how} is still there, accents and all")
        if kept:
            t.eq(kept[0].read_bytes(), raw if word == b"Soup" else raw.replace(b"Soup", b"Stew"),
                 f"and not one byte of the file {how} changed")
            t.ok(kept[0].with_name(kept[0].name + ".card.md").exists(),
                 f"the file {how} is filed with a card beside it")
    t.ok("nothing waiting" in t.box.run("sort").stdout, "and it stays filed")

    # work whose own page was saved that way is refused, not spoiled
    t.box.run("new", "work", "Old Project")
    readme = t.box.root / "Work" / "Old Project" / "README.md"
    readme.write_bytes(readme.read_text().replace("## Next action", "## Next action\n- résumé")
                       .encode("cp1252", errors="replace"))
    before = readme.read_bytes()
    proc = t.box.run("hold", "old-project", expect=1)
    t.ok("older text format" in proc.stderr, f"hold says why it stopped: {proc.stderr[-200:]}")
    t.eq(readme.read_bytes(), before, "and leaves the page exactly as it was")


@test
def test_search_forgives_how_people_type(t: Case) -> None:
    """"I know I wrote it down somewhere" is exactly when you misspell it."""
    for text in (
        "The billing token refresh dies every Friday night, before the release on the 14th",
        "Notes on Postgres index types: btree is the default, gin is for arrays",
        "Meeting with the accountant about quarterly filing",
        "How to set up a mobile dev environment on a new laptop",
        "Debugging the jubilee campaign landing page",
    ):
        t.box.run("save", text)

    def found(*args: str) -> list:
        return t.box.json("find", *args)

    # the word as typed
    t.ok(any("billing" in h["title"].lower() for h in found("billing")),
         "an exact word is found")
    # plurals and other endings, both directions
    for query, want in (("refreshes", "refresh"), ("meetings", "meeting"),
                        ("postgress", "postgres"), ("types", "type")):
        hits = found(query)
        t.ok(any(want in (h["title"] + h["snippet"]).lower() for h in hits),
             f"'{query}' finds '{want}'")
    # a plain typo is offered as a search to type, not searched for on its own
    t.eq(found("accountnt"), [], "a misspelling is not swapped for another word")
    said = t.box.run("find", "accountnt", expect=1).stdout
    t.ok("./os find accountant" in said, f"but the right spelling is offered to type\n{said}")
    # ...but a stem must not match in the middle of an unrelated word
    for hit in found("biling"):
        blob = (hit["title"] + hit["snippet"]).lower()
        t.ok("mobile" not in blob and "jubilee" not in blob,
             f"'biling' must not drag in {hit['title']!r}")
    # exact matching is unchanged: a substring anywhere still counts
    t.ok(any("debugging" in h["title"].lower() for h in found("bug")),
         "an exact substring still matches inside a longer word")
    # and nonsense is still nonsense
    t.eq(found("zzzzqqqxx"), [], "a word that is in nothing finds nothing")


@test
def test_search_never_swaps_a_real_word_for_another(t: Case) -> None:
    """When nothing matched, search looked for the nearest word it knew and
    showed those notes: bike found bake, cage cake, june jungle, winter wine
    and valve valet, and winner showed the winter note with no word about it
    (review, 2026-09-30). Now it says nothing matched and offers the near
    word as a command to type. Plurals and endings are still forgiven."""
    for text in (
        "Notes on Gran's lemon cake recipe: 200g butter, 200g sugar, four eggs",
        "The boiler keeps cutting out at night, worth knowing the reset button",
        "Weeding the garden is easiest on a dry morning, notes on what grows",
        "Bake bread on Sunday mornings, the sourdough wants a long rise",
        "The jungle gym in the back yard has two loose bolts, a note for later",
        "Wine for the party: one red, two whites, notes from the shop",
        "Valet parking at the hotel was twenty pounds, a note for next time",
    ):
        t.box.run("save", text)

    def plain(query: str) -> str:
        return t.box.run("find", query, expect=None).stdout

    for typed, near in (("bike", "bake"), ("cage", "cake"), ("june", "jungle"),
                        ("winter", "wine"), ("valve", "valet")):
        t.eq(t.box.json("find", typed, expect=None), [], f"'{typed}' finds nothing, not the {near} note")
        said = plain(typed)
        t.ok("nothing matched" in said and f"./os find {near}" in said,
             f"'{typed}' says nothing matched and offers ./os find {near}\n{said}")
        t.ok("searched for" not in said, f"and never says it searched for something else\n{said}")

    # Plurals and word endings are still the same word.
    for typed, want in (("lemons", "lemon"), ("boilers", "boiler"), ("gardening", "garden"),
                        ("cakes", "cake"), ("baking", "bake")):
        hits = t.box.json("find", typed)
        t.ok(any(want in (h["title"] + h["snippet"]).lower() for h in hits),
             f"'{typed}' still finds the {want} note ({[h['title'] for h in hits]})")

    # Real typos: offered, not searched for.
    for typed, near in (("lemmon", "lemon"), ("recipie", "recipe")):
        t.eq(t.box.json("find", typed, expect=None), [], f"'{typed}' isn't searched for as another word")
        t.ok(f"./os find {near}" in plain(typed), f"'{typed}' offers ./os find {near}")
    # The rest of a longer search is kept in what is offered.
    t.ok("./os find lemon recipe" in plain("lemmon recipie"), "every word is offered, put right")

    # An accent typed or not is the same word. Found before only because the
    # near-word guess, "taxis", happened to be part of it.
    t.box.run("save", "Diátaxis: four kinds of documentation, a note for later")
    for typed in ("diataxis", "diátaxis", "DIATAXIS"):
        t.ok(any("taxis" in h["title"].lower() for h in t.box.json("find", typed)),
             f"'{typed}' finds the Diátaxis note")

    # winner's root is win, and win is the start of winter: not the same word.
    t.box.run("save", "Winter tyres go on before the first frost, a note for the car")
    t.ok(any("winter" in h["title"].lower() for h in t.box.json("find", "winter")), "winter finds winter")
    t.eq(t.box.json("find", "winner", expect=None), [], "winner doesn't show the winter note")
    said = plain("winner")
    t.ok("nothing matched" in said and "./os find winter" in said,
         f"it says nothing matched, and offers winter to type\n{said}")


@test
def test_a_root_is_found_only_with_its_own_endings(t: Case) -> None:
    """A root matched the start of any word: "winner" found "winter"."""
    for typed, hay, want in (
        ("winner", "winter tyres", False), ("winner", "the winning goal", True),
        ("winner", "red wine", False), ("baking", "bake bread", True),
        ("gardening", "garden notes", True), ("gardening", "gardener's diary", True),
        ("gardening", "a gardenia", False), ("berries", "a berry tart", True),
        ("biling", "mobile billing", True), ("biling", "mobile phone", False),
        ("types", "type of index", True), ("meetings", "meet the team", True),
    ):
        t.eq(engine.Term(typed).weight(hay) > 0, want, f"'{typed}' in '{hay}'")


@test
def test_it_stays_quick_as_it_grows(t: Case) -> None:
    """Speed is a feature here: `brief` runs on every single session start.

    The checks are shaped so cost grows with the folder, not with the square of
    it. A regression shows up as one of these blowing past its budget."""
    t.box.fill_inbox(220)
    t.box.run("sort")

    for command, budget in (("brief", 6.0), ("status", 6.0), ("check", 6.0)):
        start = time.time()
        t.box.run(command)
        spent = time.time() - start
        t.ok(spent < budget, f"`os {command}` took {spent:.1f}s on 220 items (budget {budget}s)")

    # duplicates are reported as groups, not as every pair inside a group
    dupes = [i for i in t.box.json("check", expect=None)["issues"]
             if i["code"] == "near-duplicate"]
    t.ok(len(dupes) < 40, f"duplicates come back grouped, not pair by pair ({len(dupes)})")


@test
def test_it_reads_ordinary_english(t: Case) -> None:
    """The filing has to work on how people actually write, not on keywords.

    This is the whole promise: you type a sentence, it lands somewhere sensible.
    If this test sags, the system is guessing and the user has to file by hand."""
    cases = [
        ("pushing", "Fix the login page, it 500s on Safari. Needs to be out before Monday."),
        ("pushing", "Write the grant application. Due the 30th."),
        ("pushing", "Plan Mia's birthday party for the 12th"),
        ("pushing", "Set up the new laptop this weekend"),
        ("pushing", "We need to move the database off Heroku before the bill renews"),
        ("pushing", "I need to write the Q3 report by Friday."),
        ("pushing", "Redesign the onboarding flow so new users get their first win fast"),
        ("pushing", "The billing token refresh dies every Friday night"),
        ("pushing", "The export button is broken on mobile"),
        ("pushing", "Login keeps failing for new accounts"),
        ("holding", "Ongoing: reply to every customer email within a day. That's the standard."),
        ("holding", "Go for a run every morning. No end date, just something I hold to."),
        ("holding", "Keep on top of the invoices. Check it every Friday."),
        ("holding", "Keep the codebase green: no failing tests, no lint errors, every week."),
        ("note", "TIL: you can use --json on every command here"),
        ("note", "The difference between GIN and GiST indexes, for reference"),
        ("note", "Idea for a podcast about small software businesses"),
        ("note", "Quote worth keeping: 'the plural of anecdote is not data'"),
        ("note", "Notes on Postgres index types: btree is the default, gin is for arrays"),
        ("note", "Standup notes: blocked on the API key, Sam is chasing it"),
        ("note", "Met with the accountant today. We agreed to switch to quarterly filing."),

        # The admin half of running anything, which is most of what gets typed.
        # Every one of these was read wrong before the imperative rule existed.
        ("pushing", "call the accountant about the VAT return"),
        ("pushing", "kevin from northwind wants the deck by friday"),
        ("pushing", "AWS bill jumped 40% last month, find out why"),
        ("pushing", "chase the overdue invoice from acme"),
        ("pushing", "email the landlord about the lease renewal"),
        ("pushing", "book the flights for the conference"),
        ("pushing", "draft the onboarding email sequence"),
        ("pushing", "renew the domain before it expires"),
        ("pushing", "reply to the recruiter"),
        ("pushing", "sort out the broken CI pipeline"),
        ("pushing", "ask sarah for the updated logo files"),
        ("pushing", "cancel the old hosting plan"),
        ("pushing", "look into why churn went up in march"),
        ("holding", "keep the staging environment matching prod"),
        ("holding", "keep the tests passing"),
        ("holding", "stay on top of the support inbox"),
        ("holding", "maintain the relationship with northwind"),
        ("holding", "keep the runway above six months"),
        ("note", "good quote: constraints are what make a thing itself"),
        ("note", "meta ads: the learning phase needs about 50 conversions"),
        ("note", "the auth token expires on the 14th"),
        ("note", "kevin's number is 07700 900123"),
        ("note", "stripe takes 1.5% plus 20p on UK cards"),

        # And the trap either way: notes that *talk about* doing things. A
        # bigram like "make a" fires in the middle of ordinary English, which
        # is how "what make a thing itself" was once filed as a project.
        ("note", "the trick is to write the hook before the body"),
        ("note", "how to make a landing page that converts"),
        ("note", "review: the new macbook is not worth it"),
        ("note", "what good onboarding looks like, from three teams"),
        ("note", "he said the deal only works above 40% margin"),
        ("note", "pricing psychology: anchor high, then remove features"),
        ("note", "the meeting is on the 14th at 3pm"),
    ]
    classifier = engine.Classifier(engine.Zenith(t.box.root))
    wrong = []
    for want, text in cases:
        got, _score, _all = classifier.score_intent(text[:60], text)
        if got != want:
            wrong.append(f"{text[:44]!r} read as {got}, not {want}")
    t.eq(wrong, [], f"{len(wrong)} of {len(cases)} ordinary sentences were misread")

    # short keywords must match whole words: "ci" must not fire inside "pricing"
    for text, forbidden in (("Redesign the pricing page", "engineering"),
                            ("Already read the header", "marketing"),
                            ("I had a bad idea", "engineering")):
        got, _s, scores = classifier.score_domain(text, text, "")
        t.ok(forbidden not in scores,
             f"{text!r} must not score as {forbidden} on a fragment match")


@test
def test_taxonomy_is_teachable(t: Case) -> None:
    """Adding your own vocabulary changes where things land."""
    tax_path = t.box.root / ".os" / "words.json"
    tax = json.loads(tax_path.read_text())
    tax["domains"]["northwind"] = {
        "label": "Northwind",
        "keywords": ["northwind", "zorblat", "flimbus"],
        "extensions": [],
    }
    tax_path.write_text(json.dumps(tax, indent=2))

    (t.box.root / "Notes" / "mystery.md").write_text(
        "# Zorblat rollout\n\nNotes on the flimbus configuration for northwind. Reference material.\n")
    t.box.run("sort")
    item = next(i for i in t.box.items() if i["path"] == "Notes/mystery.md")
    t.eq(item["domain"], "northwind", "the new domain was learned and applied")


@test
def test_holds_up_at_three_hundred_items(t: Case) -> None:
    """Well past the promise: 300 mixed items, still shallow, still stable."""
    planted = t.box.fill_inbox(300)
    started = time.time()
    t.box.run("sort")
    elapsed = time.time() - started
    t.ok(elapsed < 180, f"300 items sorted in {elapsed:.1f}s")
    t.eq(t.box.inbox_count(), 0, "the inbox emptied completely")

    filed = [i for i in t.box.items() if i["bucket"] != engine.TOOLKIT]
    t.gte(len(filed), 300, "every item is individually indexed, none swallowed by a folder")

    # nothing lost
    lost = [m for m, _, _ in planted if t.box.locate(m) is None]
    t.eq(lost, [], f"nothing was lost ({len(lost)} missing)")

    # every category at every level is properly marked
    # names carry no number any more, so item folders are known by where the
    # scanner says the items are, not by how they are spelled
    item_dirs = {(t.box.root / i["path"]).resolve() for i in filed}
    for bucket in ("Work", "Notes"):
        base = t.box.root / bucket
        if not base.exists():
            continue
        for node in base.rglob("*"):
            if not node.is_dir() or engine.ignored(node):
                continue
            rel = node.relative_to(base)
            # anything at or below an item folder is item content, not structure
            if any(p in item_dirs for p in (node.resolve(), *node.resolve().parents)):
                continue
            t.ok((node / ".category").exists(),
                 f"{bucket}/{rel} is a marked category at every level")

    # shallow, always
    t.ok(max(len(i["trail"]) for i in filed) <= 2, "nothing is more than 2 levels below its bucket")

    # one name, one thing, at scale
    t.eq(t.box.name_clashes(), [], "no two things share a name at 300 items")

    # stable
    t.eq(t.box.json("sort")["moves"], [], "a second sort at scale moves nothing")
    t.eq(t.box.json("sort")["moves"], [], "and a third moves nothing either")

    # a big healthy folder is not reported as a sick one
    health = t.box.json("doctor", expect=None)
    t.eq([i for i in health["issues"] if i["level"] == "error"], [], "no errors at scale")
    t.gte(health["score"], 70, "a large, healthy folder still scores well")
    t.ok(len(health["issues"]) < 200, "the report stays readable — findings are rolled up")

    # search stays fast
    t0 = time.time()
    t.box.json("find", "postgres index")
    t.ok(time.time() - t0 < 15, "search stays responsive at 300 items")


@test
def test_a_fresh_copy_dates_itself(t: Case) -> None:
    """The first command in a new copy makes it theirs, and leaves their dates alone.

    It used to re-date every file in Work/, Notes/ and Archive/ to today. The
    blank folder ships none, so the only files it ever touched were the
    person's own: a note from 2019 dropped in before the first ./os came out
    saying today, and undo couldn't put it back (stranger test, 2026-09-30).
    `./os setup` in a used folder did the same, and emptied ./os last."""
    import datetime
    today = datetime.date.today().strftime("%Y-%m-%d")

    # a copy nobody has opened yet, with somebody's old notes already in it
    state = t.box.root / ".os" / "state.json"
    data = json.loads(state.read_text())
    data["fresh"] = True
    state.write_text(json.dumps(data, indent=2))

    seeded = t.box.root / "Work" / "seeded-area"
    seeded.mkdir(parents=True, exist_ok=True)
    (seeded / "README.md").write_text(engine.compose(
        {"title": "Seeded area", "type": "area", "status": "active",
         "domain": "operations", "tags": ["seed"], "created": "2020-01-01",
         "updated": "2020-01-01"}, "# Seeded area\n\nBrought in by hand.\n"))
    old_note = t.box.root / "Notes" / "From Obsidian" / "starlings.md"
    old_note.parent.mkdir(parents=True)
    old_note.write_text("---\ntitle: Starling murmurations\ncreated: 2019-03-04\n"
                        "updated: 2021-11-20\ntags: [birds]\n---\n\n"
                        "Seen over Brighton pier, 2019.\n")
    before = old_note.read_text()

    proc = t.box.run("status")
    t.ok("Welcome" in proc.stdout, "the first command says the copy is theirs now")
    t.eq(old_note.read_text(), before, "a note they brought keeps its own dates")
    meta, _ = engine.parse_frontmatter((seeded / "README.md").read_text())
    t.eq((meta["created"], meta["updated"]), ("2020-01-01", "2020-01-01"),
         "and so does work they brought")

    seeded_item = next(i for i in t.box.items() if i["id"] == "seeded-area")
    t.eq(seeded_item["status"], "holding",
         "a pre-merge `type: area` item is read as held, not put on the go")
    after = json.loads(state.read_text())
    t.ok(not after.get("fresh"), "the fresh flag was cleared")
    t.eq(after.get("installed"), today, "and the day it was installed is written down")

    # and it does not fire twice
    second = t.box.run("status")
    t.ok("Welcome" not in second.stdout, "initialisation happens exactly once")

    # setup is explicit, idempotent, and can name an owner
    t.box.run("save", "Remember to call the plumber about the leak")
    t.box.run("setup", "--owner", "Sam", "--name", "Studio")
    config = json.loads((t.box.root / ".os" / "config.json").read_text())
    t.eq(config["owner"], "Sam", "setup records the owner")
    t.eq(config["name"], "Studio", "setup renames the system")
    t.box.run("setup")
    t.eq(json.loads((t.box.root / ".os" / "config.json").read_text())["owner"], "Sam",
         "running setup again keeps what was already set")
    t.eq(old_note.read_text(), before, "setup in a used folder re-dates nothing")
    later = json.loads(state.read_text())
    t.ok(later.get("history"), "and keeps what ./os last reads")
    t.eq(later.get("installed"), today, "and the day it was installed")


@test
def test_a_textedit_card_made_on_a_mac_holds_no_control_characters(t: Case) -> None:
    """On a Mac, sort copies an RTF's words onto its card from textutil,
    which turns `\\u0` into a NUL and `\\'07` into a bell. The card then
    counted as a binary file (`file` said data, grep said "Binary file
    matches"), where the same RTF on Linux gave a clean one (review,
    2026-09-30). A page break stays a break."""
    b = "\\"
    rtf = t.box.root / "Notes" / "Jam.rtf"
    rtf.write_text(b.join(["{", "rtf1", "ansi Plum jam: ", "u0 ?sugar and ", "'07pectin, ",
                           "u12 ?boil ", "u27 ?hard.", "page Pour into jars.}"]), encoding="ascii")
    said = engine.rtf_text(rtf)
    t.ok(not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", said),
         f"the words read out of it hold no control character: {said!r}")
    t.ok(re.search(r"hard\.\s*\n\s*Pour into jars", said),
         f"and the words either side of a page break don't run together: {said!r}")
    t.box.run("sort")
    card = rtf.with_name(rtf.name + ".card.md").read_bytes()
    t.ok(not re.search(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", card),
         f"so its card is text, not a binary file: {card[-160:]!r}")
    t.ok(b"pectin" in card and b"Pour into jars" in card, "and the words are on it")


@test
def test_a_subject_written_into_a_header_never_hides_what_it_groups(t: Case) -> None:
    """`./os words --new` refuses a subject whose folder ./os would skip, but
    a header written by hand never asked. 14 video projects marked `domain:
    content` were grouped into Work/Content, where ./os never looks: ./os
    said nothing was started, find found nothing, and check said all good
    (review, 2026-09-30). `food/drink` made a folder inside a folder. And
    `--new wine.rtfd` grouped notes in what a Mac opens as one TextEdit file."""
    root = t.box.root
    for n in range(14):
        folder = root / "Work" / f"Film Video {n}"
        folder.mkdir(parents=True)
        (folder / "README.md").write_text(engine.compose(
            {"title": f"Film video {n}", "type": "work", "status": "pushing",
             "domain": "content" if n < 7 else "food/drink", "tags": ["video"],
             "created": "2026-09-01", "updated": "2026-09-01"},
            f"# Film video {n}\n\nShoot and cut the clip about plum jam, part {n}.\n"))
    t.box.run("sort")
    hidden = list((root / "Work" / "Content").rglob("README.md"))
    t.eq(hidden, [], "nothing is grouped into Work/Content, which ./os never looks in")
    works = [i for i in t.box.items() if i["path"].startswith("Work/")]
    t.eq(len(works), 14, "every one is still on the list")
    t.eq(len(t.box.json("find", "plum jam")), 14, "and found")
    t.ok(not (root / "Work" / "Food").exists(), "a slash makes no folder inside a folder")
    for name in ("wine.rtfd", "wine.rtf"):
        said = t.box.run("words", "--new", name, "merlot", expect=1).stderr
        t.ok("one document" in said and "Pick another name" in said,
             f"--new {name} is refused, saying why:\n{said}")


@test
def test_a_tag_never_names_a_folder_that_hides_what_is_in_it(t: Case) -> None:
    """The group inside a crowded subject is named after a tag, and nothing
    asked whether ./os would skip it. 14 video projects tagged `content`
    went into Work/General/Content, where ./os never looks; the same sort
    then took General away, with every one of them in it, as empty. Undo
    couldn't bring them back and check said all good (review, 2026-09-30)."""
    root = t.box.root
    for n in range(14):
        folder = root / "Work" / f"Film Video {n}"
        folder.mkdir(parents=True)
        (folder / "README.md").write_text(engine.compose(
            {"title": f"Film video {n}", "type": "work", "status": "pushing",
             "domain": "content", "tags": ["content", "video"],
             "created": "2026-09-01", "updated": "2026-09-01"},
            f"# Film video {n}\n\nShoot and cut the clip about plum jam, part {n}.\n"))
    t.box.run("sort")
    left = sorted(p.parent.name for p in (root / "Work").rglob("README.md"))
    t.eq(len(left), 14, f"all 14 are still on disk: {left}")
    t.eq(list((root / "Work").rglob("Content")), [], "none went into a Content folder")
    t.eq(len([i for i in t.box.items() if i["path"].startswith("Work/")]), 14,
         "and every one is still on the list")
    t.eq(len(t.box.json("find", "plum jam")), 14, "and found")


@test
def test_sort_never_takes_away_a_group_with_anything_still_in_it(t: Case) -> None:
    """A group sort made was taken away with rmtree once `ignored()` saw
    nothing in it. A folder ./os skips counts as nothing to that, so a group
    holding only a Content folder went, and all that was in it (review,
    2026-09-30). Now only its own mark and what a computer leaves go."""
    root = t.box.root
    group = root / "Notes" / "Garden"
    (group / "Content").mkdir(parents=True)
    (group / engine.CATEGORY_MARKER).write_text(json.dumps(
        {"name": "Garden", "trail": ["Garden"], "auto": True}))
    (group / "Content" / "tomato footage.mp4").write_bytes(b"\0" * 64)
    (group / "old plan.md~").write_text("the plan before the frost\n")
    empty = root / "Notes" / "Kitchen"
    empty.mkdir(parents=True)
    (empty / engine.CATEGORY_MARKER).write_text(json.dumps(
        {"name": "Kitchen", "trail": ["Kitchen"], "auto": True}))
    (empty / ".DS_Store").write_bytes(b"\0\0\0\1Bud1")
    t.box.run("sort")
    t.ok((group / "Content" / "tomato footage.mp4").is_file(), "what ./os skips is still there")
    t.ok((group / "old plan.md~").is_file(), "and so is a file named like a leftover")
    t.ok(not empty.exists(), "a group with nothing but its mark in it still goes")


@test
def test_a_save_a_crash_left_empty_is_put_back_by_check_fix(t: Case) -> None:
    """git doesn't make sure its newest files reach the disk, so a crash in a
    checkpoint most often leaves the save's own file empty, and the files
    of what it took in. The checkpoint and every brief said the history had
    lost track of its last save and to run ./os check --fix; but check only
    read the save's name, said all good, and --fix did nothing, so no
    checkpoint was ever kept again (review, 2026-09-30)."""
    if engine.History.no_git():
        return
    _as_downloaded(t.box)
    root, env = t.box.root, _nameless(t.box)
    _run_with(t.box, env)
    t.box.run("save", "Remember to service the boiler in October")
    _run_with(t.box, env, "checkpoint", "the boiler")
    before = _git_in(root, "rev-parse", "HEAD~1").stdout.strip()
    head = _git_in(root, "rev-parse", "HEAD").stdout.strip()
    tree = _git_in(root, "rev-parse", "HEAD^{tree}").stdout.strip()
    rel = next(n for n in _git_in(root, "diff-tree", "--no-commit-id", "--name-only", "-r",
                                  "HEAD").stdout.split() if not n.startswith(".os/"))
    blob = _git_in(root, "rev-parse", f"HEAD:{rel}").stdout.strip()
    theirs = {p: p.read_bytes() for bucket in ("Work", "Notes")
              for p in (root / bucket).rglob("*") if p.is_file()}
    for sha in (head, tree, blob):                   # what the crash left
        part = root / ".git" / "objects" / sha[:2] / sha[2:]
        part.chmod(0o644)
        part.write_bytes(b"")

    status = t.box.run().stdout
    t.ok("needs fixing" in status and "./os check --fix" in status,
         f"./os says something needs fixing\n{status}")
    check = t.box.run("check", expect=1).stdout
    t.ok("lost track of its last save" in check, f"and ./os check says what\n{check}")
    fixed = t.box.run("check", "--fix", expect=None).stdout
    t.ok("history back on its last save" in fixed, f"--fix puts it back\n{fixed}")
    t.eq(_git_in(root, "rev-parse", "HEAD").stdout.strip(), before,
         "on the last save git's own record names that can still be read")
    t.eq({p: p.read_bytes() for p in theirs}, theirs, "and no file of theirs changed")
    kept = _run_with(t.box, env, "checkpoint", "after the crash")
    t.ok("kept" in kept.stdout, f"once put back, checkpoints carry on\n{kept.stdout}")
    t.eq(_git_in(root, "show", f"HEAD:{rel}").stdout, (root / rel).read_text(),
         "with every file in it whole")
    t.ok("lost track" not in t.box.run("brief").stdout, "and the brief stops saying it")
    tidy = _git_in(root, "gc", "-q")
    t.eq(tidy.returncode, 0, f"and git can still tidy it up\n{tidy.stderr}")


def _crashed_mid_checkpoint(t: Case, emptied: str) -> tuple:
    """A folder with two saves and a note, then a crash in a checkpoint that
    left one of git's files empty: `emptied` picks which, from what the
    last save holds (`tree`), or from what a checkpoint cut off had taken
    in (`taken in`). Gives the root, the environment and the note."""
    _as_downloaded(t.box)
    root, env = t.box.root, _nameless(t.box)
    _run_with(t.box, env)
    t.box.run("save", "Remember to service the boiler in October")
    _run_with(t.box, env, "checkpoint", "the boiler")
    rel = next(n for n in _git_in(root, "diff-tree", "--no-commit-id", "--name-only", "-r",
                                  "HEAD").stdout.splitlines() if n.startswith("Notes/"))
    with (root / rel).open("a") as note:
        note.write("\nThe engineer's number is on the fridge.\n")
    if emptied == "tree":
        sha = _git_in(root, "rev-parse", "HEAD^{tree}").stdout.strip()
    else:
        _git_in(root, "add", "-A")
        sha = _git_in(root, "rev-parse", f":{rel}").stdout.strip()
    part = root / ".git" / "objects" / sha[:2] / sha[2:]
    part.chmod(0o644)
    part.write_bytes(b"")                            # what the crash left
    return root, env, rel


@test
def test_a_crash_that_empties_what_the_last_save_holds_is_put_right(t: Case) -> None:
    """A crash can leave the last save whole and the list of what it holds
    empty. Every checkpoint after said "nothing has changed since the last
    one" and kept nothing, even after an edit, while ./os check and --fix
    both said all good (review, 2026-09-30)."""
    if engine.History.no_git():
        return
    root, env, rel = _crashed_mid_checkpoint(t, "tree")
    after = _run_with(t.box, env, "checkpoint", "after the crash", expect=1).stdout
    t.ok("nothing has changed" not in after and "./os check --fix" in after,
         f"the checkpoint doesn't say nothing changed, and says what puts it right\n{after}")
    check = t.box.run("check", expect=1).stdout
    t.ok("lost track of its last save" in check, f"./os check says so\n{check}")
    t.ok("./os check --fix" in t.box.run("brief").stdout, "and so does the brief")
    t.box.run("check", "--fix", expect=None)
    kept = _run_with(t.box, env, "checkpoint", "after the fix").stdout
    t.ok("kept" in kept, f"once put right, the edit is kept\n{kept}")
    t.eq(_git_in(root, "show", f"HEAD:{rel}").stdout, (root / rel).read_text(),
         "and can be read back")
    tidy = _git_in(root, "gc", "-q")
    t.eq(tidy.returncode, 0, f"and git can still tidy it up\n{tidy.stderr}")


@test
def test_a_crash_while_a_checkpoint_took_files_in_is_put_right(t: Case) -> None:
    """A crash while a checkpoint took the files in can leave git's copies of
    them empty, and its list naming them. The next checkpoint took them as
    written, and kept a save that could never be read back, while ./os
    check said all good (review, 2026-09-30)."""
    if engine.History.no_git():
        return
    root, env, rel = _crashed_mid_checkpoint(t, "taken in")
    status = t.box.run().stdout
    t.ok("./os check --fix" in status, f"./os says something needs fixing\n{status}")
    after = _run_with(t.box, env, "checkpoint", "after the crash", expect=1).stdout
    t.ok("crash" in after and "./os check --fix" in after,
         f"the checkpoint keeps nothing it couldn't read back, and says why\n{after}")
    check = t.box.run("check", expect=1).stdout
    t.ok("history empty" in check, f"./os check says so\n{check}")
    fixed = t.box.run("check", "--fix", expect=None).stdout
    t.ok("cleared what a crash left empty" in fixed, f"--fix puts it right\n{fixed}")
    t.eq(_git_in(root, "log", "--format=%s", "-1").stdout.strip(), "the boiler",
         "keeping the last save, which was whole")
    kept = _run_with(t.box, env, "checkpoint", "after the fix").stdout
    t.ok("kept" in kept, f"then the edit is kept\n{kept}")
    t.eq(_git_in(root, "show", f"HEAD:{rel}").stdout, (root / rel).read_text(),
         "and can be read back")
    t.ok("crash" not in t.box.run("brief").stdout, "and the brief stops saying it")


@test
def test_a_crash_during_the_first_save_is_finished_by_check_fix(t: Case) -> None:
    """A crash during the very first save, which takes in the whole folder,
    left no save to go back to. With the branch never written, ./os and
    check said all good, and every checkpoint failed on git's words about an
    empty file; with it emptied, --fix could only point at git fsck, which
    found nothing (review, 2026-09-30). There is nothing in it to lose, so
    check --fix makes that save again, and no file of theirs changes."""
    if engine.History.no_git():
        return
    for branch in ("never written", "emptied"):
        _as_downloaded(t.box)
        shutil.rmtree(t.box.root / ".git", ignore_errors=True)
        root, env = t.box.root, _nameless(t.box)
        # git's own tidy-up packs things away after a save, in the background.
        Path(env["GIT_CONFIG_GLOBAL"]).write_text("[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n")
        _run_with(t.box, env)
        (root / "Notes").mkdir(exist_ok=True)
        (root / "Notes" / "boiler.md").write_text(
            "---\ntitle: Boiler\ntype: note\nstatus: —\ndomain: home\ntags: []\n"
            "created: 2026-09-30\nupdated: 2026-09-30\n---\n\nThe engineer is Dave.\n")
        git = root / ".git"
        for part in (git / "objects").glob("??/*"):          # what the crash left
            part.chmod(0o644)
            part.write_bytes(b"")
        (git / "index").write_bytes(b"")
        ref = git / (git / "HEAD").read_text().split(":", 1)[1].strip()
        if branch == "never written":
            ref.unlink()
            shutil.rmtree(git / "logs")
        else:
            ref.write_bytes(b"")
            for record in (git / "logs").rglob("*"):
                if record.is_file():
                    record.write_bytes(b"")
        theirs = {p: p.read_bytes() for bucket in ("Work", "Notes") if (root / bucket).is_dir()
                  for p in (root / bucket).rglob("*") if p.is_file()}

        status = t.box.run().stdout
        t.ok("./os check --fix" in status, f"{branch}: ./os says something needs fixing\n{status}")
        check = t.box.run("check", expect=1).stdout
        t.ok("first save" in check, f"{branch}: ./os check says what\n{check}")
        after = _run_with(t.box, env, "checkpoint", expect=1).stdout
        t.ok("./os check --fix" in after, f"{branch}: so does a checkpoint\n{after}")
        fixed = t.box.run("check", "--fix", expect=None).stdout
        t.ok("finished this folder's first history save" in fixed, f"{branch}: --fix finishes it\n{fixed}")
        t.eq(_git_in(root, "show", "HEAD:Notes/boiler.md").stdout,
             (root / "Notes" / "boiler.md").read_text(), f"{branch}: with their files in it")
        t.eq({p: p.read_bytes() for p in theirs}, theirs, f"{branch}: and no file of theirs changed")
        brief = t.box.run("brief").stdout
        t.ok("crash" not in brief and "check --fix" not in brief,
             f"{branch}: the brief stops saying it\n{brief}")
        (root / "Notes" / "boiler.md").write_text("The engineer is Dave, 0161 555 0101.\n")
        kept = _run_with(t.box, env, "checkpoint", "after the fix").stdout
        t.ok("kept" in kept, f"{branch}: and checkpoints carry on\n{kept}")
        t.eq(_git_in(root, "fsck").returncode, 0, f"{branch}: with nothing in it broken")
        for part in git.rglob("*"):
            part.chmod(0o755 if part.is_dir() else 0o644)


@test
def test_a_garbled_list_of_what_git_took_in_is_made_again(t: Case) -> None:
    """A crash can garble .git/index. Every checkpoint after it failed with
    "bad signature 0x00000000", and every brief said so, while ./os check
    said all good (review, 2026-09-30). It holds nothing that isn't in the
    files and the last save, so the checkpoint makes it again."""
    if engine.History.no_git():
        return
    _as_downloaded(t.box)
    root, env = t.box.root, _nameless(t.box)
    _run_with(t.box, env)
    (root / ".git" / "index").write_bytes(bytes(100))       # what the crash left
    (root / "Notes").mkdir(exist_ok=True)
    (root / "Notes" / "boiler.md").write_text("# Boiler\n\nService it in October.\n")
    kept = _run_with(t.box, env, "checkpoint", "after the crash")
    t.ok("kept" in kept.stdout, f"the checkpoint is kept\n{kept.stdout}")
    t.eq(_git_in(root, "show", "HEAD:Notes/boiler.md").stdout, "# Boiler\n\nService it in October.\n",
         "with what changed in it")
    brief = t.box.run("brief").stdout
    t.ok("didn't work" not in brief and "signature" not in brief, f"and the brief is clear\n{brief}")


@test
def test_saves_on_a_branch_of_their_own_make_a_clone_theirs(t: Case) -> None:
    """A git clone of the template was counted theirs only by the saves on the
    branch open at the time. With their saves on a branch of their own, every
    brief and checkpoint still said  rm -rf .git , which would throw those
    saves away (review, 2026-09-30)."""
    if engine.History.no_git():
        return
    _as_downloaded(t.box)
    root = t.box.root
    _cloned(root)
    t.box.run()
    _git_in(root, "checkout", "-q", "-b", "mine")
    (root / "Notes").mkdir(exist_ok=True)
    (root / "Notes" / "boiler.md").write_text("# Boiler\n\nService it in October.\n")
    _git_in(root, "add", "-A")
    _git_in(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "mine")
    _git_in(root, "checkout", "-q", "-")
    brief = t.box.run("brief").stdout
    t.ok("rm -rf" not in brief, f"saves on another branch make it theirs\n{brief}")
    said = t.box.run("checkpoint", expect=1).stdout
    t.ok("rm -rf" not in said, f"and a checkpoint doesn't offer to throw them away\n{said}")


@test
def test_a_checkpoint_waits_for_the_first_run_to_finish(t: Case) -> None:
    """A checkpoint made while the first run was still taking everything in
    took git's lock away from under it, failed with git's own words, and
    those words sat in every brief after (review, 2026-09-30). The first run
    holds ./os's lock now, so the checkpoint waits its turn."""
    if engine.History.no_git():
        return
    _as_downloaded(t.box)
    root, env = t.box.root, _nameless(t.box)
    (root / "Notes").mkdir()
    (root / "Notes" / "boiler.md").write_text("# Boiler\n\nService it in October.\n")
    slow = t.box.tmp / "slow-git"
    slow.write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = add ] && sleep 3; done\n'
                    'exec git "$@"\n')
    slow.chmod(0o755)
    first = subprocess.Popen(
        [str(root / "os")], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(os.environ, ZENITH_HOME=str(root), NO_COLOR="1", ZENITH_GIT=str(slow),
                 ZENITH_HISTORY_WAIT="30", **env))
    try:
        deadline = time.time() + 20
        while time.time() < deadline and _git_in(
                root, "config", "--get", engine.History.MARK).stdout.strip() != "true":
            time.sleep(0.05)
        during = _run_with(t.box, env, "checkpoint", "at the same time", expect=None)
    finally:
        first.communicate(timeout=60)
    t.eq(during.returncode, 0, f"the checkpoint works\n{during.stdout}\n{during.stderr}")
    saves = _git_in(root, "log", "--reverse", "--format=%s").stdout.splitlines()
    t.eq(saves[:1], ["The folder as it was first opened"],
         "after the first run has made the first save")
    t.ok("didn't work" not in t.box.run("brief").stdout, "and the brief has nothing to say about it")


@test
def test_a_git_of_their_own_still_decides_about_its_caches(t: Case) -> None:
    """The owner settled on 2026-09-29 that ./os check leaves __pycache__
    alone when git ignores it: a code project's check had listed eighteen as
    junk. Made to report strays in the history ./os keeps, it listed them
    again in a folder that is a code project with a git of its own (review,
    2026-09-30). Their git says what's theirs; the .gitignore of the history
    ./os keeps was written by ./os, so there a project file says it."""
    if engine.History.no_git():
        return
    root = t.box.root
    _git_in(root, "init", "-q")
    for cache in (root / "src" / "__pycache__", root / "tests" / "__pycache__",
                  root / "Work" / "Bot" / "src" / "__pycache__"):
        cache.mkdir(parents=True)
        (cache / "x.pyc").write_text("")
    (root / "pyproject.toml").write_text("[project]\nname = 'bot'\n")

    def caches() -> list:
        return [i["path"] for i in t.box.json("check", expect=None)["issues"]
                if i["code"] == "clutter" and "__pycache__" in i["path"]]
    t.eq(caches(), [], "a code project with a git of its own that ignores them: none reported")
    (root / "pyproject.toml").unlink()
    t.eq(caches(), [], "nor with no project file, while their own git ignores them")
    _git_in(root, "config", engine.History.MARK, "true")
    t.eq(sorted(caches()), ["Work/Bot/src/__pycache__", "src/__pycache__", "tests/__pycache__"],
         "in the history ./os keeps, one outside a code project is reported")
    (root / "pyproject.toml").write_text("[project]\nname = 'bot'\n")
    t.eq(caches(), [], "and this folder with a project file of its own is a code project")


@test
def test_the_setup_screen_the_demo_and_the_download_name_an_ai_that_runs_commands(t: Case) -> None:
    """The first screen stopped saying any AI would do, but `./os setup` still
    said "claude — or any AI", and the line under the name in `./os help`,
    which the first screen points at, said "Any AI can use it." A chat-only
    app like ChatGPT can't run ./os (review, 2026-09-30). The demo's last
    line was 86 wide, and wrapped in a Terminal window as it opens. And
    `./os decide` asked with "we ship Meta first", a job's example."""
    setup = " ".join(t.box.run("setup").stdout.split())
    t.ok("any AI" not in setup and "or another AI that can run commands here" in setup,
         f"setup names an AI that can run commands\n{setup[-300:]}")
    demo = t.box.run("demo").stdout.splitlines()
    t.eq([line for line in demo if len(line) > 80], [], "no line of the demo is wider than 80")
    t.ok(any("another AI that can run commands here" in line for line in demo),
         "and its last line says it the same way")
    t.box.run("new", "work", "Fix the boiler")
    said = t.box.run("decide", "fix-the-boiler", expect=1).stderr
    t.ok("Meta" not in said and "not another repair" in said,
         f"decide asks with an example from home\n{said}")


@test
def test_the_download_ships_no_subject_of_this_folders_own(t: Case) -> None:
    """A subject made here with `./os words --new` has no words but the ones
    it learned, which never ship: it went out as a name alone, one of this
    folder's own. And the download said "Any AI can use it." under its name
    in `./os help`. Only the workshop has the script, so a copy skips this."""
    script = next(iter(sorted((SOURCE / "Work").glob("*/release-os.sh"))), None)
    if script is None:
        return
    root = t.box.root
    ours = root / "Work" / script.parent.name / script.name
    ours.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(script, ours)
    t.box.run("words", "--new", "wine", "merlot", "claret")
    out = t.box.tmp / "blank"
    proc = subprocess.run(["bash", str(ours), "build", str(out)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=dict(os.environ, NO_COLOR="1"),
                          timeout=300)
    t.eq(proc.returncode, 0, f"it builds\n{proc.stdout[-600:]}\n{proc.stderr[-600:]}")
    shipped = json.loads((out / ".os" / "words.json").read_text())["domains"]
    t.ok("wine" not in shipped, "a subject made here with --new doesn't ship")
    t.ok("general" in shipped and "garden" in shipped,
         "the subjects that come with it do, general too, which has no words on purpose")
    line = json.loads((out / ".os" / "config.json").read_text()).get("tagline", "")
    t.ok(line and "any ai" not in line.lower(), f"the line under its name in ./os help: {line!r}")


@test
def test_an_update_renews_the_line_under_the_name_still_as_released(t: Case) -> None:
    """The first release put "Any AI can use it." under the folder's name in
    `./os help`, and an update kept theirs whatever it said, so no release
    could ever take it back. One still as released takes the new one; one
    they wrote themselves stays."""
    root = t.box.root
    config = root / ".os" / "config.json"
    _edit_json(config, lambda c: c.update(tagline="One folder for your work. Any AI can use it."))
    _release(root, "2026-01-01.1")
    published = _publish(t, "2026-02-01.1", lambda out: _edit_json(
        out / ".os" / "config.json", lambda c: c.update(tagline="One folder for your notes.")))
    done = t.box.run("update", "--from", str(published))
    t.eq(json.loads(config.read_text())["tagline"], "One folder for your notes.",
         f"a line still as released is the new one\n{done.stdout}")
    t.ok("a new line under its name" in done.stdout, "and the update says so")
    _edit_json(config, lambda c: c.update(tagline="Sam's things, kept tidy."))
    newer = _publish(t, "2026-03-01.1", lambda out: _edit_json(
        out / ".os" / "config.json", lambda c: c.update(tagline="Something newer.")),
        base=published)
    t.box.run("update", "--from", str(newer))
    t.eq(json.loads(config.read_text())["tagline"], "Sam's things, kept tidy.",
         "one they wrote themselves stays theirs")


@test
def test_a_release_remembers_the_line_under_the_name(t: Case) -> None:
    """An update renews the line under the folder's name only while it is
    one some release wrote, and only the first release's was listed. So a
    folder that took this release's line kept it for good, whatever a later
    release said (review, 2026-09-30). Each release records its own."""
    root = t.box.root
    config = root / ".os" / "config.json"
    line = "One folder for your notes, plans and files."
    _edit_json(config, lambda c: c.update(tagline=line))
    _release(root, "2026-01-01.1")
    shipped = json.loads((root / ".os" / "shipped.json").read_text())
    t.ok(line in shipped.get("taglines", []), "the line it shipped is recorded")
    published = _publish(t, "2026-02-01.1", lambda out: _edit_json(
        out / ".os" / "config.json", lambda c: c.update(tagline="Something newer.")))
    done = t.box.run("update", "--from", str(published))
    t.eq(json.loads(config.read_text())["tagline"], "Something newer.",
         f"so the next release can change it\n{done.stdout}")


@test
def test_an_update_that_stops_partway_keeps_a_hard_linked_claude_md_linked(t: Case) -> None:
    """A CLAUDE.md made with `ln` from AGENTS.md was linked to the new
    AGENTS.md only at the very end of an update. One that stopped partway had
    swapped AGENTS.md already, and run again, as it says to, CLAUDE.md was no
    longer the same file: it got "@AGENTS.md" put on top of the old rules,
    and check was quiet. The preview said it would be left as it was, and
    the real run linked it again (review, 2026-09-30)."""
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        return          # nothing is read-only to the computer's owner
    import hashlib
    root = t.box.root
    shipped = json.loads((root / ".os" / "shipped.json").read_text())
    old_rules = "# How to work in this folder\n\nThe old rules, from an older release.\n"
    skill = root / ".claude" / "skills" / "save" / "SKILL.md"
    old_skill = "---\nname: save\ndescription: Save it.\n---\n\nAn older release's save.\n"
    for rel, text in (("AGENTS.md", old_rules), (".claude/skills/save/SKILL.md", old_skill)):
        (root / rel).write_text(text)
        shipped["files"].setdefault(rel, []).append(hashlib.sha1(text.encode()).hexdigest())
    (root / ".os" / "shipped.json").write_text(json.dumps(shipped))
    (root / "CLAUDE.md").unlink(missing_ok=True)
    os.link(root / "AGENTS.md", root / "CLAUDE.md")

    def update(*more: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SOURCE / ".os" / "upgrade.py"), str(root), *more],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=180)
    preview = update("--dry-run")
    t.ok("would keep CLAUDE.md — it's a link to AGENTS.md, so it would be linked to the new one"
         in preview.stdout, f"the preview says what the real run does\n{preview.stdout[-800:]}")
    skill.parent.chmod(0o555)
    try:
        stopped = update()
    finally:
        skill.parent.chmod(0o755)
    t.eq(stopped.returncode, 1, f"the update stops partway\n{stopped.stdout[-800:]}")
    t.ok("The old rules" not in (root / "AGENTS.md").read_text(), "after AGENTS.md was the new one")
    t.ok((root / "CLAUDE.md").samefile(root / "AGENTS.md"),
         "and CLAUDE.md is still the same file as it, even so")
    finished = update()
    t.eq(finished.returncode, 0, f"run again, it finishes\n{finished.stdout[-800:]}")
    claude = (root / "CLAUDE.md").read_text()
    t.ok((root / "CLAUDE.md").samefile(root / "AGENTS.md") and "@AGENTS.md" not in claude
         and "The old rules" not in claude,
         f"with CLAUDE.md still AGENTS.md, and no stale copy under a pointer\n{claude[:300]}")


@test
def test_a_release_remembers_the_words_that_tell_a_note_from_work(t: Case) -> None:
    """An update renews the words that tell a note from work only while a
    folder's are a list some release wrote, so each release has to record
    its own. Nothing checked that it did: without it, a release's new note
    words, like "recipe", would never reach anyone who updated after the
    next one (review, 2026-09-30)."""
    import upgrade
    root = t.box.root
    words = root / ".os" / "words.json"
    _edit_json(words, lambda w: w["intent"]["note"]["keywords"].append("jam jar"))
    _release(root, "2026-01-01.1")
    note = json.loads(words.read_text())["intent"]["note"]
    shipped = json.loads((root / ".os" / "shipped.json").read_text())
    t.ok(upgrade.keywords_sha(note["keywords"]) in shipped["intent_keywords"]["note"],
         "the note words it shipped are recorded")
    t.ok(upgrade.keywords_sha(note["patterns"]) in shipped["intent_patterns"]["note"],
         "and so are the patterns")


@test
def test_a_history_that_lost_its_last_save_is_never_deleted(t: Case) -> None:
    """A crash while saving can leave the file naming the last save empty. A
    checkpoint read that as "nothing saved yet", deleted the whole history and
    started again: every earlier checkpoint gone, with the record that could
    have found them, and it said "started this folder's history" (review,
    2026-09-30)."""
    if engine.History.no_git():
        return
    _as_downloaded(t.box)
    root, env = t.box.root, _nameless(t.box)
    _run_with(t.box, env)
    t.box.run("save", "Remember to service the boiler in October")
    _run_with(t.box, env, "checkpoint", "the boiler")
    last = _git_in(root, "rev-parse", "HEAD").stdout.strip()
    branch = _git_in(root, "symbolic-ref", "HEAD").stdout.strip()
    t.ok(len(last) in (40, 64) and branch.startswith("refs/heads/"), "two saves to lose")

    (root / ".git" / branch).write_text("")          # what the crash left
    (root / "Notes").mkdir(exist_ok=True)
    (root / "Notes" / "after.md").write_text("# After\n\nWritten after the crash.\n")
    lost = _run_with(t.box, env, "checkpoint", "after the crash", expect=1)
    t.ok("lost track of its last save" in lost.stdout and "started" not in lost.stdout,
         f"it says what happened, and starts nothing\n{lost.stdout}")
    t.ok("./os check --fix" in lost.stdout,
         f"with the one line that puts it back\n{lost.stdout}")
    t.ok(_git_in(root, "cat-file", "-e", last + "^{commit}").returncode == 0,
         "every save is still in the history")
    t.ok(len((root / ".git" / "logs" / "HEAD").read_text().splitlines()) == 2,
         "and so is git's record of them")
    t.ok("lost track of its last save" in t.box.run("brief").stdout,
         "the brief says so too, for as long as it's true")
    # Only Claude Code reads the brief: ./os said nothing, and ./os check
    # said "all good" (review, 2026-09-30).
    status = t.box.run().stdout
    t.ok("needs fixing" in status and "./os check --fix" in status,
         f"./os says something needs fixing\n{status}")
    check = t.box.run("check", expect=1).stdout
    t.ok("lost track of its last save" in check, f"and ./os check says what\n{check}")

    # the line it gave, and the checkpoint after it follows on
    fixed = t.box.run("check", "--fix", expect=None).stdout
    t.ok("history back on its last save" in fixed, f"--fix puts it back\n{fixed}")
    t.eq((root / ".git" / branch).read_text().strip(), last, "on the save it last had")
    kept = _run_with(t.box, env, "checkpoint", "after the crash")
    t.ok("kept" in kept.stdout, f"once put back, checkpoints carry on\n{kept.stdout}")
    t.eq(_git_in(root, "rev-list", "--count", "HEAD").stdout.strip(), "3",
         "from the save before the crash")
    t.ok("lost track" not in t.box.run("brief").stdout, "and the brief stops saying it")

    # the file gone altogether, while git's record of the branch has its saves
    last = _git_in(root, "rev-parse", "HEAD").stdout.strip()
    (root / ".git" / branch).unlink()
    (root / ".git" / "packed-refs").unlink(missing_ok=True)
    gone = _run_with(t.box, env, "checkpoint", expect=1)
    t.ok("lost track" in gone.stdout and "./os check --fix" in gone.stdout,
         f"is lost too, not a history to start again\n{gone.stdout}")
    t.ok(_git_in(root, "cat-file", "-e", last + "^{commit}").returncode == 0,
         "and everything in it is still there")
    t.box.run("check", "--fix", expect=None)
    t.eq(_git_in(root, "rev-parse", "HEAD").stdout.strip(), last, "and --fix puts that back too")
    t.ok("lost track" not in t.box.run("check", expect=None).stdout, "after which all is well")


@test
def test_checkpoint_is_listed_and_the_brief_says_why_there_is_no_history(t: Case) -> None:
    """./os checkpoint wasn't in the main help, and `./os commit` and `./os
    history` pointed at a list without it. In Claude Code the first run is the
    start-of-chat check's `./os brief --json`, which prints nothing else, so
    nobody heard there was no git, a git clone, or a bigger history around
    the folder (review, 2026-09-30)."""
    listed = t.box.run("help").stdout
    t.ok("os checkpoint" in listed and "hand edits" in listed,
         f"the main help lists checkpoint, and what it's for\n{listed}")
    # Kept to 42 lines by making two of them 109 and 105 long, which an
    # 80-wide terminal broke in the middle (review, 2026-09-30).
    widest = max(listed.splitlines(), key=len)
    t.lte(len(widest), 90, f"and no line of it is wider than before\n{widest}")
    for guess in ("commit", "history"):
        said = t.box.run(guess, expect=1).stderr
        t.ok("./os checkpoint" in said, f"./os {guess} points at it\n{said}")
        t.ok("help checkpoint" in t.box.run("help", guess, expect=1).stdout,
             f"and so does ./os help {guess}")

    def told(extra: dict | None = None) -> str:
        proc = _run_with(t.box, extra or {}, "brief", "--json")
        return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]

    _as_downloaded(t.box)
    brief = told({"ZENITH_GIT": str(t.box.tmp / "no-git-here")})
    t.ok("first visit" in brief.lower() and "no git" in brief,
         f"no git: the first brief says so\n{brief}")
    if engine.History.no_git():
        return
    root = t.box.root

    # the template's own history, from a git clone
    _as_downloaded(t.box)
    _cloned(root)
    brief = told()
    t.ok("came with the download" in brief and "rm -rf .git && ./os checkpoint" in brief,
         f"a git clone: the first brief gives the way to start their own\n{brief}")
    shutil.rmtree(root / ".git")

    # a bigger history around the folder, in an everyday brief
    _as_downloaded(t.box)
    _git_in(t.box.tmp, "init", "-q")
    first = t.box.run().stdout
    t.ok("Welcome" in first and "./os checkpoint --here" in first,
         f"a bigger history: the first run gives the next step\n{first}")
    t.box.run("save", "Remember to service the boiler in October")
    brief = told()
    t.ok("On the go" in brief or "Nothing being pushed" in brief, "the everyday brief")
    t.ok("bigger folder's history" in brief and "./os checkpoint --here" in brief,
         f"a bigger history: the brief gives the next step\n{brief}")
    around = t.box.run("checkpoint", expect=1).stdout
    t.ok("./os checkpoint --here" in around, f"and so does a checkpoint\n{around}")
    here = _run_with(t.box, _nameless(t.box), "checkpoint", "--here")
    t.ok("started" in here.stdout and (root / ".git").is_dir(),
         f"--here starts one for this folder alone\n{here.stdout}")
    t.ok(_git_in(t.box.tmp, "rev-parse", "HEAD").returncode != 0,
         "and nothing went into the bigger one")
    t.ok("nothing has changed" in t.box.run("checkpoint").stdout,
         "after that, checkpoints are kept there without asking again")
    t.ok("checkpoint --here" not in told(), "and the brief stops saying it")


@test
def test_two_words_to_decide_or_rename_ask_without_offering_the_same_command(t: Case) -> None:
    """With Van and Van Insurance both here, `./os rename van "Insurance"` was
    refused, and the fix it offered for Van was that same command. Made to
    never ask, `./os decide Kitchen Renovation`, its decision forgotten,
    wrote "Renovation" under Kitchen. A bare -- was written into the
    decision or the new name, and `./os close -- garden` was told to try
    `./os close "-- garden"` (review, 2026-09-30)."""
    for name in ("Van", "Van Insurance", "Kitchen", "Kitchen Renovation", "Garden", "Shed"):
        t.box.run("new", "work", name)
    root = t.box.root
    readme = lambda name: (root / "Work" / name / "README.md").read_text()
    before = t.box.tree()
    said = t.box.run("decide", "Kitchen", "Renovation", expect=2).stderr
    t.ok('./os decide "Kitchen Renovation" "what was decided"' in said,
         f"two words that make another name still ask\n{said}")
    said = t.box.run("rename", "van", "Insurance", expect=2).stderr
    offered = './os rename van -- "Insurance"'
    t.ok('./os rename "van Insurance" "the new name"' in said and offered in said,
         f"and the way offered for the first isn't the same command\n{said}")
    t.eq(t.box.tree(), before, "nothing was written or renamed")

    t.box.run("rename", "van", "--", "Insurance")
    t.ok((root / "Work" / "Insurance").is_dir() and (root / "Work" / "Van Insurance").is_dir(),
         "the way it offers works, instead of asking again")
    t.box.run("decide", "kitchen", "--", "Renovation")
    t.ok("· Renovation" in readme("Kitchen") and "· --" not in readme("Kitchen"),
         "and a -- is where the name ends, never part of the decision")
    t.box.run("decide", "Kitchen Renovation", "--", "we", "tile", "the", "floor")
    t.ok("· we tile the floor" in readme("Kitchen Renovation"),
         f"whatever follows it\n{readme('Kitchen Renovation')}")
    t.box.run("decide", "Kitchen", "Renovation", "--", "we", "grout", "it")
    t.ok("· we grout it" in readme("Kitchen Renovation"), "and the words before it are the name")
    t.box.run("decide", "--", "shed", "-3 degrees is too cold for paint")
    t.ok("· -3 degrees is too cold for paint" in readme("Shed"), "a -- before the name too")
    t.box.run("rename", "garden", "--", "Back Garden")
    t.ok("title: Back Garden\n" in readme("Back Garden"), "or the new name")

    t.box.run("close", "--", "back-garden")
    t.ok(not (root / "Work" / "Back Garden").exists(), "a name after -- is the name")
    said = t.box.run("close", "--", "insurance", "now", expect=2).stderr
    t.ok('"-- ' not in said and "./os close insurance" in said,
         f"and words after it are refused without the --\n{said}")


@test
def test_stray_caches_are_still_litter_in_a_folder_with_a_history(t: Case) -> None:
    """The check left __pycache__ alone whenever git ignored it, meaning a
    code project's own. Every folder now has a history whose .gitignore lists
    __pycache__/, so none was ever reported or swept again (review,
    2026-09-30). A code project's own is still its business."""
    if engine.History.no_git():
        return
    root = t.box.root
    _git_in(root, "init", "-q")
    # The history ./os keeps, whose .gitignore is ours. One they keep
    # themselves ignoring it is their choice (see the check after dates_itself).
    _git_in(root, "config", engine.History.MARK, "true")
    stray = root / "Notes" / "__pycache__"
    stray.mkdir(parents=True)
    (stray / "a.pyc").write_text("")
    bot = root / "Work" / "Bot"
    (bot / "src" / "__pycache__").mkdir(parents=True)
    (bot / "src" / "__pycache__" / "x.pyc").write_text("")
    (bot / "pyproject.toml").write_text("[project]\nname = 'bot'\n")
    found = [i["path"] for i in t.box.json("check", expect=None)["issues"]
             if i["code"] == "clutter"]
    t.ok(any("Notes/__pycache__" in p for p in found),
         f"a stray one is reported, history or not\n{found}")
    t.ok(not any("Bot" in p for p in found), "a code project's own isn't")
    t.box.run("check", "--fix", expect=None)
    t.ok(not stray.exists() and (bot / "src" / "__pycache__").exists(),
         "--fix sweeps the stray one and leaves the project's")


@test
def test_a_file_named_like_a_password_stays_out_of_the_history(t: Case) -> None:
    """`./os help checkpoint` said files named like a password stay out, and
    Notes/passwords.txt went into the history, where it stays even after the
    file is deleted (review, 2026-09-30). A note named after its title still
    goes in."""
    if engine.History.no_git():
        return
    _as_downloaded(t.box)
    root, env = t.box.root, _nameless(t.box)
    _run_with(t.box, env)
    (root / "Notes").mkdir(exist_ok=True)
    (root / "Notes" / "passwords.txt").write_text("wifi: hunter2\n")
    (root / "Notes" / "Router Password.pdf").write_bytes(b"%PDF-1.4\n")
    (root / "Notes" / "how-to-change-a-password.md").write_text("# How\n\nSettings.\n")
    proc = _run_with(t.box, env, "checkpoint")
    kept = _git_in(root, "ls-files").stdout.split("\n")
    t.ok("Notes/passwords.txt" not in kept and "Notes/Router Password.pdf" not in kept,
         f"files named like a password are left out\n{kept}")
    t.ok("Notes/how-to-change-a-password.md" in kept, "a note about one isn't")
    t.ok("passwords.txt" in proc.stdout, f"and it says what it left out\n{proc.stdout}")


@test
def test_the_checks_ignore_a_history_around_the_temp_folder(t: Case) -> None:
    """Someone whose temp folder sits inside a git history (a home folder kept
    in git) had three history checks fail in ./os test, about nothing they had
    done: git looked above each check's folder and found theirs (review,
    2026-09-30)."""
    if engine.History.no_git():
        return
    home = t.box.tmp / "home"
    (home / "tmp").mkdir(parents=True)
    _git_in(home, "init", "-q")
    env = {k: v for k, v in os.environ.items() if k != "GIT_CEILING_DIRECTORIES"}
    env.update(TMPDIR=str(home / "tmp"), NO_COLOR="1")
    proc = subprocess.run([sys.executable, str(t.box.root / ".os" / "tests" / "run.py"),
                           "-k", "names_like_keys", "-j", "1"], cwd=str(t.box.root), env=env,
                          capture_output=True, text=True, timeout=300)
    t.ok(proc.returncode == 0 and "1/1 passed" in proc.stdout,
         f"a history check passes there\n{proc.stdout[-2000:]}{proc.stderr[-1000:]}")


@test
def test_a_git_clone_they_have_saved_in_is_theirs(t: Case) -> None:
    """Every brief in a git clone of the template said their changes weren't
    going in its history, and to  rm -rf .git  to keep their own, even after
    they had saved their own work in it: the command would have thrown those
    saves away (review, 2026-09-30). Once it holds anything of theirs, it's
    theirs."""
    if engine.History.no_git():
        return
    _as_downloaded(t.box)
    root = t.box.root
    _cloned(root)
    t.box.run()
    t.ok("rm -rf .git" in t.box.run("brief").stdout, "a clone as downloaded still says it")
    t.box.run("save", "Remember to service the boiler in October")
    _git_in(root, "add", "-A")
    _git_in(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "mine")
    brief = t.box.run("brief").stdout
    t.ok("rm -rf" not in brief and "download" not in brief,
         f"once they've saved in it, the brief says nothing of the kind\n{brief}")
    said = t.box.run("checkpoint", expect=1).stdout
    t.ok("didn't start" in said and "rm -rf" not in said,
         f"and a checkpoint leaves it to them, like any history of theirs\n{said}")
    t.eq(_git_in(root, "rev-list", "--count", "HEAD").stdout.strip(), "2", "and adds nothing")


@test
def test_a_first_start_that_failed_is_not_promised_again(t: Case) -> None:
    """A first start that failed left the brief saying "the first ./os
    checkpoint starts it", and every checkpoint then failed the same way,
    with git's "fatal: adding files failed", which names no file (review,
    2026-09-30). Now each says why, naming the file, until a save works."""
    if engine.History.no_git() or not hasattr(os, "geteuid") or os.geteuid() == 0:
        return          # nothing is unreadable to the computer's owner
    _as_downloaded(t.box)
    root, env = t.box.root, _nameless(t.box)
    locked = root / "Notes" / "locked.txt"
    locked.parent.mkdir(exist_ok=True)
    locked.write_text("mine\n")
    locked.chmod(0)
    try:
        first = _run_with(t.box, env)
        t.ok("couldn't start" in first.stdout and "Notes/locked.txt" in first.stdout,
             f"the first run names the file\n{first.stdout}")
        brief = t.box.run("brief").stdout
        t.ok("ouldn't start" in brief and "Notes/locked.txt" in brief
             and "checkpoint  starts it" not in brief,
             f"the brief says why, and promises nothing\n{brief}")
        again = _run_with(t.box, env, "checkpoint", expect=1)
        t.ok("Notes/locked.txt" in again.stdout, f"so does a checkpoint\n{again.stdout}")
        t.ok("lost track" not in t.box.run("check", expect=None).stdout,
             "a history with nothing in it yet hasn't lost anything")
    finally:
        locked.chmod(0o644)
    t.ok("started" in _run_with(t.box, env, "checkpoint").stdout, "once it can be read")
    t.ok("ouldn't" not in t.box.run("brief").stdout, "and the brief stops saying it")

    # a later checkpoint that fails is said too, until one works
    locked.write_text("mine, changed\n")
    locked.chmod(0)
    try:
        _run_with(t.box, env, "checkpoint", expect=1)
        brief = t.box.run("brief").stdout
        t.ok("didn't work" in brief and "Notes/locked.txt" in brief,
             f"the brief says the last one didn't work\n{brief}")
    finally:
        locked.chmod(0o644)
    _run_with(t.box, env, "checkpoint")
    t.ok("didn't work" not in t.box.run("brief").stdout, "and stops once one does")


@test
def test_the_history_line_is_said_once_and_only_while_it_is_true(t: Case) -> None:
    """The brief ended its history line with a full stop, so `./os checkpoint
    --here.`, copied as it stood, was refused; a first `./os brief` in a
    terminal said the line twice; and a bigger history that already keeps
    this folder's files was told every session that it kept none, and
    offered --here, which would have hidden them from it (review,
    2026-09-30)."""
    _as_downloaded(t.box)
    brief = _run_with(t.box, {"ZENITH_GIT": str(t.box.tmp / "no-git-here")}, "brief").stdout
    t.eq(brief.count("no git"), 1, f"a first brief says it once\n{brief}")
    if engine.History.no_git():
        return
    root = t.box.root
    _as_downloaded(t.box)
    _git_in(t.box.tmp, "init", "-q")
    t.box.run()
    brief = t.box.run("brief").stdout
    t.ok("./os checkpoint --here" in brief and "--here." not in brief,
         f"a command ends the line, with nothing after it\n{brief}")

    who = ["-c", "user.name=t", "-c", "user.email=t@t"]
    _git_in(t.box.tmp, "add", "-A")
    _git_in(t.box.tmp, *who, "commit", "-q", "-m", "my home, kept by me")
    brief = t.box.run("brief").stdout
    t.ok("bigger folder" not in brief and "--here" not in brief,
         f"a bigger history that keeps these files is somebody's plan\n{brief}")
    said = t.box.run("checkpoint", expect=1).stdout
    t.ok("kept in the history of a bigger folder" in said and "--here" not in said,
         f"a checkpoint says to save there\n{said}")
    t.ok(not (root / ".git").exists(), "and starts nothing of its own")


def _run_with(box: "Sandbox", extra: dict, *args: str, expect: int | None = 0) -> subprocess.CompletedProcess:
    """`box.run`, with more set in the environment."""
    env = dict(os.environ, ZENITH_HOME=str(box.root), NO_COLOR="1", **extra)
    proc = subprocess.run([str(box.root / "os"), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(box.root), env=env,
                          timeout=180)
    if expect is not None and proc.returncode != expect:
        raise Failure(f"`os {' '.join(args)}` exited {proc.returncode}, expected {expect}\n"
                      f"--- stdout ---\n{proc.stdout[-2500:]}\n--- stderr ---\n{proc.stderr[-2500:]}")
    return proc


def _git_in(where: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True)


def _cloned(root: Path) -> None:
    """Make the folder look like a fresh `git clone` of the template: its
    files saved once, and a copy of that on the template's own page."""
    who = ["-c", "user.name=t", "-c", "user.email=t@t"]
    _git_in(root, "init", "-q")
    _git_in(root, "add", "-A")
    _git_in(root, *who, "commit", "-q", "-m", "the template")
    _git_in(root, "remote", "add", "origin", "https://github.com/zidery333/os-template.git")
    _git_in(root, "update-ref", "refs/remotes/origin/main", "HEAD")


# Every check's folder is in the temp folder. Someone whose temp folder sits
# inside a git history (a home folder kept in git) had history checks fail in
# ./os test, about nothing they had done: git looked above the check's own
# folder and found theirs (review, 2026-09-30). So git looks no higher than
# the temp folder, in the checks and in every ./os they run.
_CEILING = str(Path(tempfile.gettempdir()).resolve())
if _CEILING not in os.environ.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep):
    os.environ["GIT_CEILING_DIRECTORIES"] = os.pathsep.join(
        [_CEILING, *filter(None, os.environ.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep))])


def _as_downloaded(box: "Sandbox") -> None:
    """Make the box a copy nobody has opened: no folders yet, and fresh."""
    for bucket in ("Work", "Notes", "Archive"):
        shutil.rmtree(box.root / bucket, ignore_errors=True)
    (box.root / ".os" / "state.json").write_text(
        json.dumps({"fresh": True, "counters": {}, "undo": [], "history": []}))


@test
def test_the_first_run_starts_a_history_hand_edits_can_go_back_to(t: Case) -> None:
    """./os undo only reverses what ./os did. A line deleted from About me by
    hand was gone for good: there was no history, /wrapup never saved, and
    ./os help said every change could be undone (stranger test, 2026-09-30)."""
    said = t.box.run("help").stdout
    t.ok("every change can be undone" not in said, "help no longer promises too much")
    t.ok("every move ./os makes can be undone" in said, "it promises what's true")
    wrapup = t.box.root / ".claude" / "skills" / "wrapup" / "SKILL.md"
    if not in_their_words(wrapup, t.box):
        text = wrapup.read_text()
        t.ok("./os checkpoint" in text and "safe to close" in text,
             "/wrapup keeps a checkpoint and says when it's safe to close")
    if engine.History.no_git():
        return          # the rest needs git; a computer without it isn't broken

    _as_downloaded(t.box)
    root = t.box.root
    (root / "Work" / "Content").mkdir(parents=True)
    (root / "Work" / "Content" / "holiday.mov").write_bytes(b"\0" * 2048)
    (root / ".env").write_text("SECRET=hunter2\n")
    # No git name set, as on a new computer: the save must not fail for it.
    blank = t.box.tmp / "no-name.gitconfig"
    blank.write_text("")
    nameless = {"GIT_CONFIG_GLOBAL": str(blank), "GIT_CONFIG_NOSYSTEM": "1"}
    _run_with(t.box, nameless)
    t.ok((root / ".git").is_dir(), "the first ./os starts the folder's history")
    t.ok(_git_in(root, "rev-parse", "HEAD").returncode == 0, "with a first save in it")
    t.ok("@localhost" in _git_in(root, "log", "-1", "--format=%ae").stdout,
         "made with a stand-in name when git has none")

    t.box.run("save", "About me: nurse on night shifts, likes short answers")
    about = next(p for bucket in ("Notes", "Work") for p in (root / bucket).rglob("*.md")
                 if "night shifts" in p.read_text())
    proc = _run_with(t.box, nameless, "checkpoint", "the first day")
    t.ok("kept" in proc.stdout, f"a checkpoint says it kept them\n{proc.stdout}")
    kept = _git_in(root, "ls-files").stdout
    t.ok(about.relative_to(root).as_posix() in kept, "what they saved is in the history")
    t.ok("holiday.mov" not in kept, "footage in Work/Content is left out")
    t.ok(".env" not in kept.split("\n"), "and so is a file of secrets")

    # the stranger's step: a line taken out by hand, which undo can't reach
    about.write_text(about.read_text().replace("night shifts", "day shifts"))
    t.ok(_git_in(root, "restore", "--", about.relative_to(root).as_posix()).returncode == 0
         and "night shifts" in about.read_text(),
         "a hand edit after a checkpoint can be taken back to it")
    t.ok("nothing has changed" in t.box.run("checkpoint").stdout,
         "with nothing new, a checkpoint says so")


@test
def test_a_history_is_only_started_and_kept_where_it_belongs(t: Case) -> None:
    """No git is no reason to stop, and a history ./os didn't start is not its
    to write in: the template's own from a git clone, one the person keeps,
    or a bigger one around this folder, which every save would take whole."""
    _as_downloaded(t.box)
    root = t.box.root
    first = _run_with(t.box, {"ZENITH_GIT": str(t.box.tmp / "no-git-here")})
    t.ok("Welcome" in first.stdout and "no git" in first.stdout,
         f"with no git the first run goes on, and says so in one line\n{first.stdout}")
    t.ok(not (root / ".git").exists(), "and starts nothing")
    none = _run_with(t.box, {"ZENITH_GIT": str(t.box.tmp / "no-git-here")},
                     "checkpoint", expect=1)
    t.ok("no git" in none.stdout, "a checkpoint says why it can't")
    if engine.History.no_git():
        return

    # a bigger history around this folder
    _as_downloaded(t.box)
    _git_in(t.box.tmp, "init", "-q")
    t.box.run()
    t.ok(not (root / ".git").exists(), "no history is started inside another one")
    around = t.box.run("checkpoint", expect=1)
    t.ok("bigger folder" in around.stdout, f"a checkpoint leaves it alone\n{around.stdout}")
    t.ok(_git_in(t.box.tmp, "rev-parse", "HEAD").returncode != 0,
         "and nothing was saved into it")
    shutil.rmtree(t.box.tmp / ".git")

    # one the person keeps themselves, and the template's own from a git clone
    _git_in(root, "init", "-q")
    theirs = t.box.run("checkpoint", expect=1)
    t.ok("didn't start" in theirs.stdout, f"theirs is left alone\n{theirs.stdout}")
    t.ok(_git_in(root, "rev-parse", "HEAD").returncode != 0, "nothing was saved into it")
    shutil.rmtree(root / ".git")
    _cloned(root)
    cloned = t.box.run("checkpoint", expect=1)
    t.ok("came with the download" in cloned.stdout and "rm -rf .git" in cloned.stdout,
         f"the template's own is left alone, with the way to start their own\n{cloned.stdout}")
    t.eq(_git_in(root, "rev-list", "--count", "HEAD").stdout.strip(), "1",
         "nothing was saved into that either")


@test
def test_words_after_a_name_are_refused(t: Case) -> None:
    """`./os done shop "sold out, finished"` put Shop away and the words went
    nowhere; `./os close Kitchen Renovation`, unquoted, put away Kitchen
    (stranger test, 2026-09-30). Words after the name now stop the run, the
    way an option it doesn't know does, and it shows the right form."""
    for name in ("Kitchen", "Kitchen Renovation", "Garden"):
        t.box.run("new", "work", name)
    before = t.box.tree()

    wrong = t.box.run("close", "Kitchen", "Renovation", expect=2)
    t.ok('./os close "Kitchen Renovation"' in wrong.stderr,
         f"it shows the name in quotes\n{wrong.stderr}")
    t.eq(wrong.stdout, "", "nothing is reported as though it had run")
    said = t.box.run("close", "Garden", "it", "is", "done", "now", expect=2).stderr
    t.ok("one name" in said and "./os close garden" in said,
         f"words that aren't a name: it shows just the name\n{said}")
    for verb in ("hold", "push", "show", "open", "edit", "claim", "release", "back"):
        t.box.run(verb, "kitchen", "check the seals every spring", expect=2)
    t.eq(t.box.tree(), before, "nothing was put away, flipped or written")

    t.box.run("close", "Kitchen Renovation")
    live = {i["title"] for i in t.box.items() if i["bucket"] == "Work"}
    t.ok("Kitchen" in live and "Kitchen Renovation" not in live,
         "in quotes, the one meant is put away")
    t.box.run("claim", "kitchen", "--as", "the tiling")
    t.box.run("decide", "kitchen", "we tile the floor")
    t.ok("we tile the floor" in (t.box.root / "Work" / "Kitchen" / "README.md").read_text(),
         "decide still takes its words after the name")


@test
def test_decide_and_rename_ask_where_an_unquoted_name_ends(t: Case) -> None:
    """decide and rename take words after the name, so they can't refuse them;
    but `./os decide Kitchen Renovation "we tile the floor"` wrote "Renovation
    we tile the floor" under Kitchen, and rename called Kitchen "Renovation
    Kitchen Refit" (review, 2026-09-30)."""
    for name in ("Kitchen", "Kitchen Renovation"):
        t.box.run("new", "work", name)
    before = t.box.tree()
    said = t.box.run("decide", "Kitchen", "Renovation", "we tile the floor", expect=2).stderr
    t.ok('./os decide "Kitchen Renovation" "we tile the floor"' in said,
         f"it shows the name in quotes\n{said}")
    t.ok('./os decide kitchen "Renovation we tile the floor"' in said,
         f"and the other way to read it\n{said}")
    said = t.box.run("rename", "Kitchen", "Renovation", "Kitchen Refit", expect=2).stderr
    t.ok('./os rename "Kitchen Renovation" "Kitchen Refit"' in said, said)
    t.box.run("rename", "Kitchen", "Renovation", expect=2)
    t.eq(t.box.tree(), before, "nothing was written or renamed")

    t.box.run("decide", "Kitchen Renovation", "we tile the floor")
    t.box.run("decide", "kitchen", "we", "keep", "the", "old", "sink")
    t.ok("we tile the floor" in (t.box.root / "Work" / "Kitchen Renovation" / "README.md")
         .read_text(), "quoted, it goes to the one meant")
    t.ok("we keep the old sink" in (t.box.root / "Work" / "Kitchen" / "README.md").read_text(),
         "and words that make no other name are still taken as they are")
    t.box.run("rename", "Kitchen Renovation", "Kitchen Refit")
    t.ok((t.box.root / "Work" / "Kitchen Refit").is_dir(), "rename in quotes works")


def _nameless(box: "Sandbox") -> dict:
    """The environment of a computer where git has no name set."""
    blank = box.tmp / "no-name.gitconfig"
    blank.write_text("")
    return {"GIT_CONFIG_GLOBAL": str(blank), "GIT_CONFIG_NOSYSTEM": "1"}


@test
def test_a_code_project_with_its_own_git_leaves_the_history_working(t: Case) -> None:
    """A code project in Work set up with its own git, nothing saved in it yet,
    made `git add` fail for the whole folder: no history on the first run, and
    every checkpoint after said git's own words and kept nothing (review,
    2026-09-30). Its files are in its own history, so they're left out here."""
    if engine.History.no_git():
        return
    _as_downloaded(t.box)
    root, env = t.box.root, _nameless(t.box)
    shop = root / "Work" / "Shop Site"
    shop.mkdir(parents=True)
    _git_in(shop, "init", "-q")
    (shop / "index.html").write_text("<h1>Shop</h1>\n")
    _run_with(t.box, env)
    t.ok(_git_in(root, "rev-parse", "HEAD").returncode == 0,
         "the first run starts the history all the same")
    blog = root / "Work" / "Blog"
    (blog / "src").mkdir(parents=True)
    (blog / "src" / "app.js").write_text("console.log('hi')\n")
    _git_in(blog, "init", "-q")
    _git_in(blog, "add", "-A")
    _git_in(blog, "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-qm", "first")
    t.box.run("save", "Remember to renew the shop's web address in March")
    proc = _run_with(t.box, env, "checkpoint", "the shop")
    t.ok("kept" in proc.stdout and "Work/Shop Site has a git history of its own" in proc.stdout
         and "Work/Blog" in proc.stdout, f"a checkpoint keeps the rest, and says so\n{proc.stdout}")
    kept = _git_in(root, "ls-files", "-s").stdout
    t.ok("Shop Site" not in kept and "Blog" not in kept,
         f"neither project's files go into this history\n{kept}")
    saved = next(p for bucket in ("Notes", "Work") for p in (root / bucket).rglob("*.md")
                 if "renew the shop" in p.read_text(errors="replace"))
    t.ok(saved.relative_to(root).as_posix() in kept, "what they saved is in it")


@test
def test_names_like_keys_stay_out_of_the_history(t: Case) -> None:
    """Only .gitignore kept secrets out: service-account.json, api-keys.txt and
    credentials.json went into the history, and stay there after the file is
    deleted (review, 2026-09-30). Main's nightly save leaves those names out."""
    if engine.History.no_git():
        return
    _as_downloaded(t.box)
    root, env = t.box.root, _nameless(t.box)
    _run_with(t.box, env)
    site = root / "Work" / "Shop Site"
    site.mkdir(parents=True)
    for name in ("service-account.json", "api-keys.txt", "credentials.json", "id_ed25519",
                 "client_secret_123.json"):
        (site / name).write_text("hunter2\n")
    (root / "Notes").mkdir(exist_ok=True)
    (root / "Notes" / "Secret Santa.md").write_text("# Secret Santa\n\nNames in a hat.\n")
    proc = _run_with(t.box, env, "checkpoint")
    kept = _git_in(root, "ls-files").stdout.split("\n")
    for name in ("service-account.json", "api-keys.txt", "credentials.json", "id_ed25519",
                 "client_secret_123.json"):
        t.ok(f"Work/Shop Site/{name}" not in kept, f"{name} is left out")
    t.ok("Notes/Secret Santa.md" in kept, "a note that only mentions a secret is kept")
    t.ok("look like keys or passwords" in proc.stdout and "api-keys.txt" in proc.stdout,
         f"and it says what it left out\n{proc.stdout}")


@test
def test_a_first_run_cut_off_is_finished_by_the_next_checkpoint(t: Case) -> None:
    """The start-of-chat check stops everything at 15 seconds. A first run
    killed while git took in what was there left git's lock behind, and every
    checkpoint failed on it; stopped more gently, the next one said "67 files
    changed since the last one" when there was none (review, 2026-09-30)."""
    if engine.History.no_git():
        return
    root, env = t.box.root, _nameless(t.box)
    (root / "Notes" / "boiler.md").write_text("# Boiler\n\nService it in October.\n")

    # killed hard: marked as ./os's, nothing saved, the lock left behind
    _git_in(root, "init", "-q")
    _git_in(root, "config", "zenith.history", "true")
    (root / ".git" / "index.lock").write_text("")
    proc = _run_with(t.box, env, "checkpoint")
    t.ok("started this folder's history" in proc.stdout, f"it starts again\n{proc.stdout}")
    t.ok(_git_in(root, "rev-parse", "HEAD").returncode == 0, "and saves")
    t.ok(not (root / ".git" / "index.lock").exists(), "with the lock gone")

    # stopped more gently: everything taken in, nothing saved
    shutil.rmtree(root / ".git")
    _git_in(root, "init", "-q")
    _git_in(root, "config", "zenith.history", "true")
    _git_in(root, "add", "-A")
    proc = _run_with(t.box, env, "checkpoint")
    t.ok("since the last one" not in proc.stdout and "started" in proc.stdout,
         f"no talk of a last one when there wasn't one\n{proc.stdout}")

    # a lock left by a checkpoint stopped partway, in a history with saves in it
    lock = root / ".git" / "index.lock"
    lock.write_text("")
    (root / "Notes" / "boiler.md").write_text("# Boiler\n\nService it in November.\n")
    busy = _run_with(t.box, env, "checkpoint", expect=1)
    t.ok("busy" in busy.stdout and "Another git" not in busy.stdout,
         f"a new one: it says so plainly, and leaves it\n{busy.stdout}")
    t.ok(lock.exists(), "a lock that may be in use is left alone")
    old = time.time() - engine.Lock.STALE_AFTER - 60
    os.utime(lock, (old, old))
    t.ok("kept" in _run_with(t.box, env, "checkpoint").stdout,
         "one older than any run of ./os is cleared, and the checkpoint kept")

    # a first run given more than it can take in, in time
    shutil.rmtree(root / ".git")
    _as_downloaded(t.box)
    slow = t.box.tmp / "slow-git"
    slow.write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = add ] && exec sleep 30; done\n'
                    'exec git "$@"\n')
    slow.chmod(0o755)
    started = time.time()
    first = _run_with(t.box, {**env, "ZENITH_GIT": str(slow), "ZENITH_HISTORY_WAIT": "1"})
    t.ok(time.time() - started < 12, "the first run doesn't wait on git past its time")
    t.ok("Welcome" in first.stdout and "first  ./os checkpoint" in first.stdout,
         f"it says the history starts at the first checkpoint\n{first.stdout}")
    # Left as it is, not deleted: ./os never deletes a .git (review, 2026-09-30).
    t.ok(_git_in(root, "rev-parse", "HEAD").returncode != 0, "and saves nothing yet")
    t.ok("started" in _run_with(t.box, env, "checkpoint").stdout,
         "which then starts it")


@test
def test_the_demo_leaves_no_trace(t: Case) -> None:
    """The demo must be safe to run on a folder that already has real work in it."""
    before, before_dirs = t.box.tree(), t.box.dirs()
    proc = t.box.run("demo")
    for beat in ("Write three things down", "Watch where they go",
                 "Find one again", "Change your mind"):
        t.ok(beat in proc.stdout, f"the demo shows the '{beat}' step")
    t.ok("work you're pushing" in proc.stdout and "a note" in proc.stdout
         and "work you keep up" in proc.stdout,
         "the demo tells the two phases of work apart, and a note from both")
    for jargon in ("capture", "taxonomy", "front matter", "bucket"):
        t.ok(jargon not in proc.stdout.lower(),
             f"the demo never says '{jargon}' at the user")

    after = t.box.tree()
    t.eq(set(after) - set(before), set(), "the demo added nothing")
    t.eq(set(before) - set(after), set(), "the demo removed nothing")
    t.eq(t.box.dirs() - before_dirs, set(),
         "the demo left no empty folders behind either")
    t.eq(t.box.inbox_count(), 0, "the inbox is empty again")

    # --keep leaves the three items filed
    t.box.run("demo", "--keep")
    filed = [i for i in t.box.items() if i["bucket"] in ("Work", "Notes")]
    t.gte(len(filed), 3, "--keep leaves the demo items in place")

    # and it refuses to sweep up real work
    (t.box.root / "Notes" / "mine.md").write_text("# Something real\n\nDo not touch.\n")
    refused = t.box.run("demo", expect=1)
    said = refused.stderr + refused.stdout
    t.ok("not filed" in said and "./os sort" in said,
         "the demo refuses to run over unfiled work and says exactly how to fix it")


@test
def test_help_exists_for_every_command(t: Case) -> None:
    """No command is undocumented, and no documented command is missing."""
    listed = t.box.run("help").stdout
    for command in ("save", "find", "open", "undo", "new", "close", "hold", "back",
                    "sort", "check", "tidy", "backup", "edit", "demo", "name"):
        t.ok(command in listed, f"`os {command}` appears in the main help")
    t.lte(len(listed.strip().split("\n")), 42,
          "the main help fits on one screen")
    for jargon in ("taxonomy", "front matter", "idempotent", "bucket", "anti-decay"):
        t.ok(jargon not in listed.lower(), f"the help never says '{jargon}'")

    for topic in sorted(engine.DETAIL):
        proc = t.box.run("help", topic)
        t.ok(len(proc.stdout.strip()) > 60, f"`os help {topic}` explains something")

    real = {name for name in engine.COMMANDS if name and not name.startswith("-")}
    documented = set(engine.DETAIL) | {"help"}
    aliases = {"st", "s", "add", "n", "start", "f", "o", "e", "file", "reindex",
               "search", "snapshot", "selftest", "init", "oops", "finish",
               "unarchive", "fix", "cleanup", "view", "look"}
    missing = real - documented - aliases
    t.eq(missing, set(), f"every command has help: missing {missing}")

    unknown = t.box.run("help", "definitely-not-a-command", expect=1)
    t.ok("nothing called" in unknown.stdout, "an unknown topic says so cleanly")

    # a near miss teaches instead of scolding
    for typo, want in (("delete", "close"), ("remember", "save"), ("organise", "sort")):
        proc = t.box.run(typo, expect=1)
        t.ok(want in proc.stderr, f"`os {typo}` points at `os {want}`")


@test
def test_a_claude_or_gemini_md_linked_to_agents_md_passes_the_check(t: Case) -> None:
    """A CLAUDE.md made as a link to AGENTS.md holds the same rules word for
    word, and `./os check` called it out of step. Its fix, "put `@AGENTS.md`
    on its first line", done through the link, wrote onto AGENTS.md itself:
    the harm an update avoids by leaving a link alone. GEMINI.md wasn't
    looked at at all."""
    root = t.box.root

    def drift() -> list:
        return [i for i in t.box.json("doctor")["issues"] if i["code"] == "rules-drift"]

    for name in ("CLAUDE.md", "GEMINI.md"):
        (root / name).unlink(missing_ok=True)
        (root / name).symlink_to("AGENTS.md")
    t.eq(drift(), [], "a link to AGENTS.md is not called out of step")
    # Made with `ln` and no -s it's the same file too, and was still told to
    # put the pointer on its first line: into AGENTS.md itself.
    (root / "CLAUDE.md").unlink()
    os.link(root / "AGENTS.md", root / "CLAUDE.md")
    t.eq(drift(), [], "and nor is a hard link to it")

    elsewhere = t.box.tmp / "rules-from-somewhere-else.md"
    elsewhere.write_text("Be brief.\n")
    (root / "GEMINI.md").unlink()
    (root / "GEMINI.md").symlink_to(elsewhere)
    found = drift()
    t.eq([i["path"] for i in found], ["GEMINI.md"], "a link that leads somewhere else is")
    t.ok(bool(found) and "Gemini CLI" in found[0]["message"] and "don't write into it" in found[0]["fix"],
         f"and the fix doesn't say to write into the link\n{found}")
    t.eq(elsewhere.read_text(), "Be brief.\n", "the check itself changes nothing")
    (root / "GEMINI.md").unlink()
    os.link(elsewhere, root / "GEMINI.md")
    found = drift()
    t.ok(bool(found) and "don't write into it" in found[0]["fix"],
         f"nor for a hard link to somewhere else\n{found}")

    (root / "GEMINI.md").unlink()
    (root / "GEMINI.md").write_text("Answer me in Spanish.\n")
    found = drift()
    t.eq([i["path"] for i in found], ["GEMINI.md"], "a GEMINI.md of their own without the pointer is too")
    t.ok(bool(found) and "put `@AGENTS.md` on its first line" in found[0]["fix"],
         f"and there the pointer goes on its first line\n{found}")


@test
def test_an_update_preview_says_what_it_does_to_claude_and_gemini_md(t: Case) -> None:
    """`./os update --dry-run` said nothing about GEMINI.md, and the real run
    then added one. A CLAUDE.md of their own was the same: the preview left
    out the pointer the real run put on top of it."""
    root = t.box.root

    def update(*extra: str) -> list:
        done = subprocess.run([sys.executable, str(SOURCE / ".os" / "upgrade.py"), str(root), *extra],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        t.eq(done.returncode, 0, f"the update runs\n{done.stdout[-1200:]}{done.stderr[-800:]}")
        return done.stdout.splitlines()

    (root / "GEMINI.md").unlink(missing_ok=True)
    (root / "CLAUDE.md").write_text("Answer me in Spanish.\n")
    preview = update("--dry-run")
    t.ok(any("would add" in ln and "GEMINI.md" in ln for ln in preview),
         "the preview says GEMINI.md would be added\n" + "\n".join(preview))
    t.ok(any("would point CLAUDE.md at AGENTS.md" in ln for ln in preview),
         "and that CLAUDE.md would be pointed at the rules")
    t.ok(not (root / "GEMINI.md").exists() and (root / "CLAUDE.md").read_text() == "Answer me in Spanish.\n",
         "and changes neither")
    done = update()
    t.ok(any("added" in ln and "GEMINI.md" in ln for ln in done), "the real run does what the preview said")
    t.ok(any("fixed" in ln and "CLAUDE.md" in ln for ln in done), "to both")

    (root / "GEMINI.md").unlink()
    (root / "GEMINI.md").symlink_to("AGENTS.md")
    t.ok(any("would keep GEMINI.md — it's a link" in ln for ln in update("--dry-run")),
         "a link is named in the preview too")


@test
def test_the_first_screen_and_the_skill_list_name_an_ai_that_runs_commands(t: Case) -> None:
    """After setup, the first screen ended "open this folder in any AI and just
    talk", the misreading the README had fixed: a chat-only app like ChatGPT
    can't run ./os. And the list of skills opened "Extras for Claude Code",
    which told every other AI the skills weren't for it."""
    said = " ".join(t.box.run().stdout.split())
    t.ok("any AI" not in said, f"the first screen doesn't say any AI will do\n{said[-400:]}")
    t.ok("claude or another AI that can run commands here" in said,
         f"it names Claude Code, or an AI that can run commands\n{said[-400:]}")
    t.box.run("index", "--quiet")
    catalog = (t.box.root / ".claude" / "CATALOG.md").read_text()
    t.ok("Extras for Claude Code" not in catalog and "Skills any AI here can use." in catalog,
         f"the list of skills says any AI here can use them\n{catalog[:400]}")


@test
def test_a_job_something_already_does_is_not_called_done_by_hand(t: Case) -> None:
    """A note saying "a scheduled task on the Mac does this every week on its
    own now", with its three steps, was listed by `./os tidy` as done by hand
    every time, and a skill was offered for it. Then "my scheduled tasks do
    this" and "this is automated now" still were, and one "automatically"
    inside a step hid a job they said they do by hand."""
    notes = t.box.root / "Notes"
    steps = "\n1. Plug in the drive\n2. Copy the photo library over\n3. Eject the drive\n"
    done_for_them = {
        "Photo backup": "A scheduled task on the Mac does this every week on its own now; I never touch it.",
        "Film backup": "My Scheduled Tasks do this every week now; I never touch it.",
        "Game backup": "This is automated now, every week. I never touch it.",
    }
    for title, said in done_for_them.items():
        (notes / f"{title}.md").write_text(f"# {title}\n\n{said}\n" + steps)
    (notes / "Music backup.md").write_text(
        "# Music backup\n\nEvery week I do this myself.\n" + steps.replace("photo", "music"))
    (notes / "Weekly invoices.md").write_text(
        "# Weekly invoices\n\nEvery week I do this myself.\n\n1. Export the invoices from the bank\n"
        "2. Upload them to the app, which emails the client automatically\n3. Tick them off in the sheet\n")
    t.box.run("sort")
    flagged = {r["title"] for r in t.box.json("tidy")["routines"]}
    for title in done_for_them:
        t.ok(title not in flagged, f"{title}, which something already does, is not listed ({flagged})")
    t.ok("Music backup" in flagged, f"the same steps done by hand every week are still spotted ({flagged})")
    t.ok("Weekly invoices" in flagged, f"and so is one whose step says the app does a part of it ({flagged})")
    said = t.box.run("tidy").stdout
    t.ok("DONE BY HAND EVERY TIME?" in said, f"and the list asks, since words can't tell\n{said}")


@test
def test_agents_md_says_to_keep_a_checkpoint(t: Case) -> None:
    """`./os checkpoint` keeps the folder as it is, so a hand edit can be taken
    back, and AGENTS.md never named it: an AI ending a session didn't keep
    one, and one told "undo can't recover hand edits" gave up there."""
    agents = t.box.root / "AGENTS.md"
    if in_their_words(agents, t.box):
        return
    text = agents.read_text()
    rules = " ".join(text.split())
    t.ok("A hand edit can go back to the last `./os checkpoint`" in rules,
         "rule 1 says a hand edit can go back to the last checkpoint")
    t.ok('Then `./os sort`. Then `./os checkpoint "<what changed>"`.' in rules,
         "a session ends with one, after ./os sort")
    t.ok('\n./os checkpoint "..."' in text, "and it's in the list of commands")
    t.ok("checkpoint" in engine.COMMANDS, "which is a real command")


@test
def test_a_first_visit_asks_what_they_are_into(t: Case) -> None:
    """The first chat asked "what are you working on", the question taken out
    of setup on 2026-08-27: asked first, work shapes everything after it like a
    workplace, and this folder is meant as a home. Nothing asked how they like
    their answers either, so every session after it guessed."""
    brief = t.box.run("brief").stdout
    said = " ".join(brief.split())
    t.ok("first visit" in said.lower(), f"this is the first-visit brief\n{brief}")
    t.ok("working on" not in said, f"it doesn't ask what they're working on\n{brief}")
    t.ok("on their mind" in said and "they're into" in said,
         "it asks what's on their mind, or what they're into")
    t.ok('"Nothing yet" is a fine answer' in said, "and nothing yet is a fine answer")
    t.ok("how they like their answers" in said.lower()
         and './os new note "About me" --domain personal' in said,
         "how they like answers is asked once, and it goes in About me with their name")
    agents = t.box.root / "AGENTS.md"
    if not in_their_words(agents, t.box):
        rules = " ".join(agents.read_text().split())
        t.ok("what they're working on" not in rules and "what they're into" in rules,
             "AGENTS.md asks the same first question")
        t.ok('"nothing yet" is fine' in rules, "and says nothing yet is fine there too")


@test
def test_the_hooks_still_run_when_os_has_lost_its_run_permission(t: Case) -> None:
    """A copy whose ./os lost its run permission (a cloud drive, some unzippers)
    made the session hook stop before its own "could not run" line: Claude
    started knowing nothing, and nothing said why. The hooks run ./os through
    bash now, which also puts the permission back."""
    root = t.box.root
    hooks = root / ".claude" / "hooks"
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), NO_COLOR="1")

    def hook(name: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(hooks / name)], input="{}", capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=120)

    (root / "os").chmod(0o644)
    started = hook("session-start.sh")
    t.eq(started.returncode, 0, "the session starts")
    t.ok(started.stdout.strip(), "and the session hook is not silent")
    told = json.loads(started.stdout)["hookSpecificOutput"]["additionalContext"]
    t.ok("first visit" in told.lower(), f"the AI is handed the real brief\n{told}")
    t.ok(os.access(root / "os", os.X_OK), "and ./os can be run again afterwards")

    (root / "os").chmod(0o644)
    dirty = root / ".os" / ".dirty"
    dirty.write_text("")
    t.eq(hook("settle.sh").returncode, 0, "the hook after a reply runs too")
    t.ok(not dirty.exists(), "and rebuilds the list instead of leaving it for later")


@test
def test_gemini_is_pointed_at_the_same_rules(t: Case) -> None:
    """Gemini CLI reads GEMINI.md and not AGENTS.md, so someone using it got
    none of the rules. A download has one line pointing at AGENTS.md, as
    CLAUDE.md does, and an update gives that line to a folder without it."""
    root = t.box.root
    gemini = root / "GEMINI.md"
    if as_downloaded():
        t.ok(gemini.is_file() and gemini.read_text().startswith("@AGENTS.md"),
             "a download has a GEMINI.md that brings in AGENTS.md")

    def update() -> subprocess.CompletedProcess:
        done = subprocess.run([sys.executable, str(SOURCE / ".os" / "upgrade.py"), str(root)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        t.eq(done.returncode, 0, f"the update runs\n{done.stdout[-1200:]}{done.stderr[-800:]}")
        return done

    gemini.unlink(missing_ok=True)
    said = update().stdout
    t.eq(gemini.read_text() if gemini.is_file() else "", "@AGENTS.md\n",
         "a folder with no GEMINI.md is given one")
    t.ok("GEMINI.md" in said, f"and the update says so\n{said}")
    gemini.write_text("Answer me in Spanish.\n")
    update()
    update()
    text = gemini.read_text()
    t.ok(text.startswith("@AGENTS.md") and "Answer me in Spanish." in text,
         f"one of their own points at the rules and keeps their line\n{text}")
    t.eq(text.count("@AGENTS.md"), 1, "once, however many updates run")


@test
def test_an_update_leaves_a_linked_claude_or_gemini_md_alone(t: Case) -> None:
    """A GEMINI.md made as a link to AGENTS.md, a usual way to point Gemini CLI
    at the rules, was written through by an update: "@AGENTS.md" went on top
    of AGENTS.md itself, which then brought itself in and matched no release,
    so every later update set the new rules aside for a merge by hand. And the
    update still said "your rules below it are kept". A link is theirs now,
    and is left as it is."""
    root = t.box.root
    for name in ("GEMINI.md", "CLAUDE.md"):
        (root / name).unlink(missing_ok=True)
        (root / name).symlink_to("AGENTS.md")
    done = subprocess.run([sys.executable, str(SOURCE / ".os" / "upgrade.py"), str(root)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    t.eq(done.returncode, 0, f"the update runs\n{done.stdout[-1200:]}{done.stderr[-800:]}")
    rules = (root / "AGENTS.md").read_text()
    t.ok(rules.startswith("# "), f"AGENTS.md still starts with its own heading\n{rules[:200]}")
    t.eq(rules.count("@AGENTS.md"), 0, "and never brings itself in")
    for name in ("GEMINI.md", "CLAUDE.md"):
        t.ok((root / name).is_symlink(), f"{name} is still their link")
        t.ok(f"{name} — it's a link" in done.stdout, f"and the update says it left {name} alone")

    # Made with `ln` and no -s, CLAUDE.md is AGENTS.md under a second name. The
    # update swapped AGENTS.md for the new one, so CLAUDE.md kept the old rules,
    # got "@AGENTS.md" on top, and Claude Code read both the new rules and a
    # stale copy of the old. Here AGENTS.md is an older release's, so it's replaced.
    import hashlib
    old_rules = "# How to work in this folder\n\nThe old rules, from an older release.\n"
    (root / "AGENTS.md").write_text(old_rules)
    shipped = json.loads((root / ".os" / "shipped.json").read_text())
    shipped["files"].setdefault("AGENTS.md", []).append(hashlib.sha1(old_rules.encode()).hexdigest())
    (root / ".os" / "shipped.json").write_text(json.dumps(shipped))
    (root / "CLAUDE.md").unlink()
    os.link(root / "AGENTS.md", root / "CLAUDE.md")
    preview = subprocess.run([sys.executable, str(SOURCE / ".os" / "upgrade.py"), str(root), "--dry-run"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    t.ok("would keep CLAUDE.md — it's a link" in preview.stdout,
         f"a preview says a hard-linked CLAUDE.md is kept\n{preview.stdout[-1200:]}")
    done = subprocess.run([sys.executable, str(SOURCE / ".os" / "upgrade.py"), str(root)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    t.eq(done.returncode, 0, f"the update runs\n{done.stdout[-1200:]}{done.stderr[-800:]}")
    rules = (root / "AGENTS.md").read_text()
    t.ok("The old rules" not in rules and rules.startswith("# "), "AGENTS.md is the new one")
    t.ok((root / "CLAUDE.md").samefile(root / "AGENTS.md"),
         f"and CLAUDE.md is still the same file as it, not a stale copy\n{(root / 'CLAUDE.md').read_text()[:300]}")
    t.ok("CLAUDE.md — it's a link to AGENTS.md, and now to the new one" in done.stdout,
         f"and the update says so\n{done.stdout[-1200:]}")


@test
def test_every_ai_is_told_where_the_skills_are(t: Case) -> None:
    """AGENTS.md told every AI to write skills and never said where they live,
    and `./os new skill` said "type /name", which only Claude Code knows. Any
    other AI never used one, however well it fitted what was asked."""
    root = t.box.root
    agents = root / "AGENTS.md"
    if not in_their_words(agents, t.box):
        rules = agents.read_text()
        t.ok("`.claude/skills/<name>/SKILL.md`" in rules and "`.claude/CATALOG.md`" in rules,
             "AGENTS.md says where a skill lives and where they are all listed")
        # The list itself opens "Extras for Claude Code", which told any other
        # AI the skills weren't for it.
        t.ok("any AI can use one" in " ".join(rules.split()), "and that any AI can use them")
    made = t.box.run("new", "skill", "Weekly Plant Watering").stdout
    t.ok((root / ".claude" / "skills" / "weekly-plant-watering" / "SKILL.md").is_file(),
         "a new skill is where AGENTS.md says")
    t.ok("/weekly-plant-watering" in (root / ".claude" / "CATALOG.md").read_text(), "and it is listed")
    t.ok("ask your AI for it by name" in " ".join(made.split()),
         f"and any AI can be asked for it, not only Claude Code\n{made}")


@test
def test_the_feedback_page_is_read_before_it_goes(t: Case) -> None:
    """Each snag is kept word for word, and `./os snag --export` put one naming
    a doctor and a court date into the page to send, then said only "hand that
    file to whoever maintains this template". Now it says to read it first,
    and AGENTS.md has the AI leave anything personal out of a snag."""
    t.box.run("snag", "sort filed a shopping list in Work instead of Notes")
    out = " ".join(t.box.run("snag", "--export").stdout.split())
    t.ok("read it before you send it" in out, f"the export ends by saying to read it first\n{out}")
    agents = t.box.root / "AGENTS.md"
    if not in_their_words(agents, t.box):
        t.ok("nothing personal: no names, subjects or note text" in " ".join(agents.read_text().split()),
             "and AGENTS.md keeps snags free of anything personal")


@test
def test_a_preference_is_kept_and_a_hobby_is_not_homework(t: Case) -> None:
    """"From now on…" and "I prefer…" were not among the words that bring in
    the save skill, so a preference said once could go unwritten. And /learn
    asked everyone what they'd do with a subject, even one they only like."""
    root = t.box.root
    save = root / ".claude" / "skills" / "save" / "SKILL.md"
    if save.is_file() and not in_their_words(save, t.box):
        meta, body = engine.parse_frontmatter(save.read_text())
        for words in ("from now on", "I prefer", "always", "stop doing"):
            t.ok(words in str(meta.get("description")), f"the save skill is used on \"{words}\"")
        t.ok("straight into About me" in " ".join(body.split()), "and sends those to About me")
    learn = root / ".claude" / "skills" / "learn" / "SKILL.md"
    if learn.is_file() and not in_their_words(learn, t.box):
        t.ok("for using, or for liking?" in learn.read_text(), "/learn asks which kind it is first")
    shape = (root / ".os" / "templates" / "learning.md").read_text()
    for heading in ("## Why I'm learning this", "## Practice"):
        t.ok("For using only" in shape.split(heading, 1)[1][:80],
             f"a learning note's {heading} is only for something to use")


@test
def test_a_list_survives_being_written_back(t: Case) -> None:
    """Front matter is read and rewritten on every filing pass, so anything it
    cannot round-trip is lost a little more each time.

    Lists were written bare: a tag reading "billing, urgent" came back as two
    tags, and one holding a `]` truncated every tag after it."""
    awkward = ["billing, urgent", 'a"b', "c]d", "plain", " lead", "trail ", "[x]"]
    for value in awkward:
        rendered = engine.render_frontmatter({"tags": [value, "after"]})
        back, _ = engine.parse_frontmatter(rendered + "\n\nbody\n")
        t.eq(back["tags"], [value, "after"],
             f"a tag of {value!r} survives being written and read again")

    # and through the real thing, twice, since filing rewrites the header
    (t.box.root / "Notes" / "tagged.md").write_text(
        '---\ntitle: Tag torture\ntype: note\ndomain: engineering\n'
        'tags: ["billing, urgent", "postgres"]\n---\n\n'
        '# Tag torture\n\nNotes on the database schema and index types.\n')
    t.box.run("sort")
    t.box.run("sort")
    item = next(i for i in t.box.items() if i["title"] == "Tag torture")
    # tags are stored as slugs, so the text is normalised — but "billing,
    # urgent" is still *one* tag, and the tag written after it is still there
    t.eq(len(item["tags"]), 2, f"two tags in, two tags out (got {item['tags']!r})")
    t.ok("postgres" in item["tags"], "and nothing after the awkward one was lost")
    t.ok("urgent" not in item["tags"], "the comma inside a tag did not split it")


@test
def test_what_could_not_be_filed_is_said_out_loud(t: Case) -> None:
    """"Nothing waiting" while something is still waiting is a lie.

    One item the sorter cannot move must not stop the other forty-nine — and
    must not be quietly counted as filed either."""
    # Dropped into Work, but they are a file and a note, so the sorter has to
    # move them to Notes — which is exactly the folder it cannot write to.
    work = t.box.root / "Work"
    (work / "blocked.csv").write_text("quarter,total\nQ3,1200\n")
    (work / "ship-it.md").write_text(
        "# Ship the rewrite\n\nDeadline Friday. Migrate the service.\n\n- [ ] cut over\n")

    notes = t.box.root / "Notes"
    notes.chmod(0o555)                       # nothing new may be written there
    try:
        proc = t.box.run("sort", expect=None)
        said = proc.stdout + proc.stderr
        t.ok("Traceback" not in said, f"a destination it cannot write to is not a crash:\n{said[-600:]}")
        t.ok("nothing waiting" not in said,
             "and it never claims everything is filed while something is stuck")
        t.ok("couldn't file" in said, f"it names what it could not move:\n{said[-600:]}")
        t.eq(proc.returncode, 1, "and says so with its exit code")
        report = t.box.json("sort", expect=None)
        t.gte(len(report["skipped"]), 1, "the JSON report carries it too")
    finally:
        notes.chmod(0o755)

    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "once the folder is writable again, everything files")


@test
def test_the_disk_saying_no_is_not_a_crash(t: Case) -> None:
    """A read-only folder is an ordinary thing to run into, and a Python
    traceback is the least useful way to describe one. Only OSError is caught
    here — a real defect still raises, because the trace is what makes it
    reportable."""
    t.box.run("new", "project", "Ship the billing rewrite")
    t.box.run("sort")
    name = t.box.carrying("Ship the billing rewrite")["id"]
    archive = t.box.root / "Archive"
    archive.chmod(0o555)
    try:
        proc = t.box.run("done", name, expect=1)
        said = proc.stdout + proc.stderr
        t.ok("Traceback" not in said, f"no traceback:\n{said[-600:]}")
        t.ok("would not let me finish" in said, f"it says what happened:\n{said[-400:]}")
        t.ok("Archive" in said, "and names the folder it could not write to")
    finally:
        archive.chmod(0o755)

    t.ok(any(i["id"] == name and i["path"].startswith("Work")
             for i in t.box.items()),
         "the project is still where it was")
    t.box.run("done", name)            # and the same command works once it can


@test
def test_a_failure_before_setup_still_respects_no_color(t: Case) -> None:
    """Finding the root and reading the settings both happen before styling was
    switched on, so the two errors most likely to be read out of a redirected
    log were the two that dumped raw escape codes into it."""
    config = t.box.root / ".os" / "config.json"
    keep = config.read_text()
    config.write_text("{ not json")
    try:
        proc = t.box.run("status", expect=1)
        t.ok("\033[" not in (proc.stdout + proc.stderr),
             f"no escape codes when NO_COLOR is set: {(proc.stdout + proc.stderr)!r}")
        t.ok("config.json" in proc.stderr, "and the message still names the file")
    finally:
        config.write_text(keep)


@test
def test_a_routine_done_by_hand_gets_noticed(t: Case) -> None:
    """Nothing in the terminal ever mentioned skills, so somebody who never
    opens the chat could use this folder for a year without learning they
    exist. `os tidy` now says so — but only when the person has written down,
    in their own words, a job they do the same way every time.

    The bar has to stay high: a nudge that fires on the wrong note is worse
    than one that never fires."""
    inbox = t.box.root / "Notes"
    # a cadence AND a fixed procedure — this is a skill waiting to happen
    (inbox / "invoice.md").write_text(
        "# Draft my weekly invoice\n\nEvery Friday I do the same steps: open the "
        "timesheet, total the hours, apply the rate, draft the email.\n"
        "Ongoing, recurring, no end date.\n")
    # a cadence and three steps counts too
    (inbox / "standup.md").write_text(
        "# Monday standup prep\n\nEvery monday, ongoing recurring responsibility, "
        "no deadline.\n- pull the open PRs\n- check what shipped\n- write three bullets\n")
    # bait: a cadence with no procedure is just an ordinary ongoing thing
    (inbox / "gym.md").write_text(
        "# Fitness\n\nOngoing. Three workouts every week, sleep before midnight. "
        "Recurring, no end date.\n")
    # bait: a procedure with no cadence is a runbook, not a routine
    (inbox / "restore.md").write_text(
        "# Restoring from backup\n\nReference runbook. Checklist for the on-call "
        "engineer.\n- stop the writer\n- restore the snapshot\n- verify checksums\n")
    t.box.run("sort")

    flagged = {r["title"] for r in t.box.json("tidy")["routines"]}
    t.ok("Draft my weekly invoice" in flagged, f"a cadence plus fixed steps is spotted ({flagged})")
    t.ok("Monday standup prep" in flagged, f"a cadence plus a step list too ({flagged})")
    t.ok("Fitness" not in flagged, "a cadence on its own is not a routine")
    t.ok("Restoring from backup" not in flagged, "and neither is a checklist that runs once")

    said = t.box.run("tidy").stdout
    t.ok("by hand" in said.lower(), "the report a person reads says so")
    t.ok("os new skill" in said, "and shows the command that fixes it")

    # once it is automated, it must stop nagging
    t.box.run("new", "skill", "Draft my weekly invoice")
    after = {r["title"] for r in t.box.json("tidy")["routines"]}
    t.ok("Draft my weekly invoice" not in after,
         "something already made into a skill is never mentioned again")

    # and the vocabulary is the user's to change, like the rest of words.json
    words = t.box.root / ".os" / "words.json"
    spec = json.loads(words.read_text())
    t.ok("routine" in spec, "the phrases live in the file people are told to edit")
    spec["routine"]["cadence"] = []
    words.write_text(json.dumps(spec, indent=2))
    t.eq(t.box.json("tidy")["routines"], [], "emptying it turns the nudge off entirely")


@test
def test_the_terminal_says_skills_exist(t: Case) -> None:
    """`os help` listed project, ongoing and note and stopped there, so the
    whole idea of a skill was reachable only from the chat."""
    top = t.box.run("help").stdout
    t.ok("os new skill" in top, "the main help lists it beside project and ongoing")

    detail = t.box.run("help", "new").stdout
    t.ok("skill" in detail and "same way every time" in detail,
         "and `os help new` says what a skill actually is, not just its syntax")
    t.ok("helper" in detail, "and points at helpers for the other kind of job")


def lock_wait(seconds: str):
    """How long a run waits for a busy folder, for the length of one test.

    The tests that check a busy folder turns a run away would otherwise sit
    out the whole wait, every time the suite runs."""
    class _Wait:
        def __enter__(self):
            self.was = os.environ.get("ZENITH_LOCK_WAIT")
            os.environ["ZENITH_LOCK_WAIT"] = seconds

        def __exit__(self, *exc):
            if self.was is None:
                os.environ.pop("ZENITH_LOCK_WAIT", None)
            else:
                os.environ["ZENITH_LOCK_WAIT"] = self.was
            return False
    return _Wait()


@test
def test_a_busy_folder_waits_its_turn(t: Case) -> None:
    """An AI often runs several commands at once. The lock turned every one
    after the first away on the spot: of twelve saves at once, eleven were told
    the folder was busy, their words sat unfiled, and an AI seeing that may
    well save them again. A run now waits a few seconds for its turn."""
    lock = t.box.root / ".os" / ".lock"
    other_run = ("import json, os, sys, time\n"
                 "lock = sys.argv[1]\n"
                 "with open(lock, 'w') as fh:\n"
                 "    fh.write(json.dumps({'pid': os.getpid(), 'at': time.time(), 'label': 'sort'}))\n"
                 "time.sleep(1.5)\n"
                 "os.remove(lock)\n")
    holder = subprocess.Popen([sys.executable, "-c", other_run, str(lock)])
    try:
        for _ in range(600):
            if lock.exists():
                break
            time.sleep(0.05)
        proc = t.box.run("save", "Paint the garden fence before the rain comes", expect=None)
        t.eq(proc.returncode, 0, f"a save that finds the folder busy waits, then files:\n{proc.stderr}")
    finally:
        holder.kill()
        holder.wait(timeout=30)
    t.ok(t.box.locate("Paint the garden fence") is not None, "the words were filed")
    t.eq(list((t.box.root / ".os" / "cache" / engine.STAGING).glob("*")), [],
         "and none were left waiting")


@test
def test_one_run_at_a_time(t: Case) -> None:
    """Two sessions in one folder must not re-shelve it simultaneously."""
    with lock_wait("0"):
        _one_run_at_a_time(t)


def _one_run_at_a_time(t: Case) -> None:
    lock = t.box.root / ".os" / ".lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "at": time.time(), "label": "sort"}))

    t.box.fill_inbox(4)
    blocked = t.box.run("sort", expect=1)
    t.ok("already working here" in (blocked.stderr + blocked.stdout),
         "a live lock blocks a second sort")
    t.eq(t.box.inbox_count(), 4, "and nothing was moved while blocked")

    # a read-only command is never blocked
    t.box.run("status")
    t.box.run("find", "note")

    # a stale lock is stepped over rather than wedging the folder forever
    lock.write_text(json.dumps({"pid": os.getpid(), "at": time.time() - 4000, "label": "sort"}))
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "a stale lock does not block work")
    t.ok(not lock.exists(), "the lock is released afterwards")

    # a lock nobody can read is debris, not a holder
    lock.write_text("{{{ not json")
    t.box.fill_inbox(2)
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "an unreadable lock does not wedge the folder either")

    # And the case the check-then-write version could not cover: runs that
    # start together. Both used to look, both saw no lock, and both went
    # ahead — after which one moved a file the other was midway through
    # moving, and died with a Python traceback instead of the message above.
    # Losing this race needs both runs past the check before either writes,
    # which is a narrow window — so try it several times rather than once.
    env = dict(os.environ, ZENITH_HOME=str(t.box.root), NO_COLOR="1")
    for burst in range(4):
        t.box.fill_inbox(16)
        racers = [subprocess.Popen([str(t.box.root / "os"), "sort"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace",
                                   cwd=str(t.box.root), env=env)
                  for _ in range(6)]
        said = [proc.communicate(timeout=180)[0] for proc in racers]
        for out in said:
            t.ok("Traceback" not in out,
                 f"no run crashes (burst {burst}); one said:\n{out[-600:]}")
            # Every run did one of two things and nothing else. How *many* were
            # turned away is the machine's business, not the folder's: on a slow
            # one all six contend, on a fast one an early winner has finished and
            # let go before the last starts, and that run is entitled to the
            # folder. Asserting a count here is asserting the speed of the box.
            t.ok("already working here" in out or "filed" in out,
                 f"a run either did the work or was told who has it (burst {burst}); "
                 f"it said:\n{out[-400:]}")
        t.eq(t.box.inbox_count(), 0, f"all sixteen were filed (burst {burst})")
        t.eq(t.box.name_clashes(), [],
             f"and no two things were given one name (burst {burst})")
        t.eq(t.box.inbox_count(), 0, f"and the winner still files everything (burst {burst})")


@test
def test_names_in_any_language_survive(t: Case) -> None:
    """Transliteration must never silently delete somebody's filename."""
    cases = {
        "設計ノート.md": "# 設計ノート\n\nReference notes on design.\n",
        "проект-2026.md": "# Проект 2026\n\nReference material for the year.\n",
        "café-résumé.md": "# Café résumé\n\nReference notes.\n",
        "emoji-🎯-target.md": "# Target notes\n\nReference material.\n",
    }
    for name, body in cases.items():
        (t.box.root / "Notes" / name).write_text(body, encoding="utf-8")
    t.box.run("sort")
    t.eq(t.box.inbox_count(), 0, "every name was filed")

    names = " ".join(p.name for p in t.box.root.rglob("*") if p.is_file())
    t.ok("設計" in names, "Japanese characters were kept in the filename")
    t.ok("проект" in names or "2026" in names, "Cyrillic was kept or sensibly transliterated")
    t.ok("café-résumé.md" in engine.nfc(names), "accents are kept in a name somebody gave")
    t.eq([p for p in t.box.root.rglob("*untitled*")], [], "nothing collapsed to 'untitled'")
    # Words given to ./os save are named by this folder, in plain letters.
    t.box.run("save", "Crème brûlée for the café: burn the sugar at the table")
    t.ok(any(p.name.startswith("creme-brulee-for-the-cafe")
             for p in t.box.root.rglob("*.md")), "accents transliterate to clean ASCII")
    t.box.run("check", expect=None)


@test
def test_a_name_is_one_name_however_its_letters_are_stored(t: Case) -> None:
    """A Mac often hands over a name decomposed, 한 as three code points, and a
    keyboard types it composed, as one. Made one way and typed the other,
    show, hold and find all said nothing was there (review, 2026-09-29)."""
    import unicodedata
    apart = unicodedata.normalize("NFD", "한국어 메모")
    together = unicodedata.normalize("NFC", "한국어 메모")
    t.ok(apart != together, "the two spellings are different text")
    t.box.run("new", "work", apart, "--domain", "writing")
    t.box.run("show", together)
    t.box.run("hold", together)
    found = t.box.run("find", unicodedata.normalize("NFC", "메모")).stdout
    t.ok("Work/" in found, f"and one word of it finds it\n{found}")


@test
def test_the_template_is_shippable(t: Case) -> None:
    """The things a stranger downloading this folder needs to find."""
    root = t.box.root
    for name in ("AGENTS.md", "CLAUDE.md", ".gitignore", "os"):
        t.ok((root / name).exists(), f"{name} ships with the template")
    visible = sorted(p.name for p in root.iterdir() if not p.name.startswith("."))
    t.lte(len([v for v in visible if v.endswith(".md")]), 4,
          f"the top level stays uncluttered (found {visible})")
    t.ok("AGENTS.md" in (root / "CLAUDE.md").read_text(),
         "CLAUDE.md points at AGENTS.md rather than repeating it")
    t.ok(os.access(root / "os", os.X_OK), "./os is executable")

    settings = json.loads((root / ".claude" / "settings.json").read_text())
    # What ships. A rule they added themselves is theirs (see as_downloaded).
    shipped = as_downloaded()
    rules = settings["permissions"]["allow"] + settings["permissions"]["deny"] if shipped else []
    for rule in rules:
        t.ok("//" not in rule and "~" not in rule,
             f"no permission rule reaches outside the folder: {rule}")
        # Claude Code fills in no ${...} in a rule, so one matched a folder of
        # that name; and it never reads a Write(path) rule, but warns about it
        # at every start.
        t.ok("${" not in rule and not rule.startswith("Write("),
             f"a rule Claude Code actually reads: {rule}")
    if shipped:
        t.ok("Read(/.os/backups/**)" in settings["permissions"]["deny"],
             "the backups are kept out of reach, from the folder's own top")

    ignored_paths = (root / ".gitignore").read_text()
    for pattern in (".os/backups/", ".os/cache/", ".os/upgrades/", ".DS_Store", "registry.json"):
        t.ok(pattern in ignored_paths, f".gitignore covers {pattern}")
    # An older .gitignore the person kept, or changed, stays theirs; an update
    # only adds the lines it lacks.
    for gone in (() if in_their_words(root / ".gitignore", t.box) else
                 ("commit script", "notes/", ".claude/.upgrade/")):
        t.ok(gone not in ignored_paths, f".gitignore says nothing of {gone}, which this folder doesn't have")

    # Finder recreates .DS_Store constantly, so the guarantee that matters is
    # that the doctor sweeps it, not that it never appears
    (root / ".claude" / ".DS_Store").write_text("")
    (root / "Notes" / ".DS_Store").write_text("")
    codes = {i["code"] for i in t.box.json("check", expect=None)["issues"]}
    t.ok("clutter" in codes, "check notices desktop litter")
    t.box.run("check", "--fix", expect=None)
    t.eq(list(root.rglob(".DS_Store")), [], "--fix sweeps macOS clutter")
    t.eq(list(root.rglob("__pycache__")), [], "--fix sweeps bytecode")
    t.eq(list(root.rglob("*.tmp~")), [], "no temp files survive")

    # the command reference lives in `./os help` and in the manual, not in a
    # separate file that can drift out of sync with the code
    listed = t.box.run("help").stdout
    for command in ("save", "sort", "undo", "check", "demo"):
        t.ok(command in listed, f"`os help` lists {command}")
    for gone in ("CHEATSHEET.md", "GUIDE.pdf", "GUIDE.md"):
        t.ok(not (root / gone).exists(), f"no {gone} to fall out of date")


@test
def test_the_release_builds_a_blank_folder(t: Case) -> None:
    """What a stranger downloads is built from the workshop by release-os.sh.

    It shipped the owner's /learn words, which filed a stranger's Spanish
    homework under finance. Its scan skipped the checks and never looked for
    an email. A skill missing here still built, and every check passed. Only
    the workshop has the script, so a downloaded copy skips this.

    Nothing here spells out a name the build's own scan stops on, or every
    real release would stop on this file."""
    script = next(iter(sorted((SOURCE / "Work").glob("*/release-os.sh"))), None)
    if script is None:
        return
    import upgrade
    root = t.box.root
    ours = root / "Work" / script.parent.name / script.name
    ours.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(script, ours)
    words = json.loads((root / ".os" / "words.json").read_text())
    words["domains"]["finance"]["learned"] = ["es", "portfolio"]
    (root / ".os" / "words.json").write_text(json.dumps(words, indent=2))

    def build(name: str) -> tuple:
        out = t.box.tmp / name
        proc = subprocess.run(["bash", str(ours), "build", str(out)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=dict(os.environ, NO_COLOR="1"), timeout=300)
        return out, proc

    out, proc = build("clean")
    t.eq(proc.returncode, 0, f"it builds\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}")
    shipped = json.loads((out / ".os" / "words.json").read_text())
    t.eq([d for d, spec in shipped["domains"].items() if "learned" in spec], [],
         "none of the words /learn picked up here ship")
    t.ok("learned" in json.loads((root / ".os" / "words.json").read_text())["domains"]["finance"],
         "and this folder keeps its own")
    for name in upgrade.SHIPPED_SKILLS:
        t.ok((out / ".claude" / "skills" / name / "SKILL.md").exists(), f"the {name} skill ships")
    for name in upgrade.SHIPPED_AGENTS:
        t.ok((out / ".claude" / "agents" / f"{name}.md").exists(), f"the {name} helper ships")
    readme = (out / "README.md").read_text()
    t.ok("./os snag --export" in readme and "Discord" in readme,
         "the README says where to send what went wrong")
    t.ok("xcode-select --install" in readme, "and what a Mac needs first")
    # The number of checks is counted by the build and put in place of a
    # marker. Nothing read it: with that step broken, the download said
    # "passes all @CHECKS@ of its own checks" and every check still passed.
    t.ok("@CHECKS@" not in readme, "the README has no marker left in it")
    t.ok(f"passes all {len(TESTS)} of its own checks" in readme,
         f"and gives the real number of checks, {len(TESTS)}")
    t.ok("bash os" in readme and "cd ~/os && claude" in readme,
         "it says what to type when ./os won't run, and next time")
    # mv into a ~/os that's already there puts the download inside it, and
    # the next step finds no ./os. Said after the mv, the warning came too late.
    warn, move = readme.find("Already have a `~/os`?"), readme.find("mv ~/Downloads/os-template-os ~/os")
    t.ok(0 <= warn < move, "the README warns about an existing ~/os before the mv, not after")
    t.ok("can run it too" not in readme and "nobody has tried them yet" in readme,
         "and doesn't claim Codex or Gemini CLI were tried")

    # A shipped skill missing here stops the build; it used to go out without it.
    decide = root / ".claude" / "skills" / "decide"
    shutil.rmtree(decide)
    _, proc = build("missing")
    t.eq(proc.returncode, 1, "a build missing a shipped skill stops")
    t.ok("decide" in proc.stderr, f"and names it\n{proc.stderr[-400:]}")
    shutil.copytree(SOURCE / ".claude" / "skills" / "decide", decide)

    # The lists of what ships live in .os/upgrade.py only, so the download and
    # every update carry the same skills.
    (root / ".claude" / "skills" / "plan").mkdir()
    (root / ".claude" / "skills" / "plan" / "SKILL.md").write_text(
        "---\nname: plan\ndescription: Plan the week. Use when they say plan my week.\n---\n\nPlan it.\n")
    code = (root / ".os" / "upgrade.py").read_text()
    listed = code.replace('"wrapup")', '"wrapup", "plan")', 1)
    t.ok(listed != code, "the shipped list in .os/upgrade.py can take one more")
    (root / ".os" / "upgrade.py").write_text(listed)
    out, proc = build("listed-once")
    t.eq(proc.returncode, 0, f"a skill added to the shipped list builds\n{proc.stderr[-400:]}")
    t.ok((out / ".claude" / "skills" / "plan" / "SKILL.md").exists(), "and goes into the download")
    (root / ".os" / "upgrade.py").write_text(code)

    # Something from this folder, even in the checks, stops the build. Spelled
    # in pieces here, or this file would stop every real release.
    leak = root / ".os" / "tests" / "leak.txt"
    leak.write_text("made in Os" + "TradingBot\n")
    _, proc = build("leak-in-tests")
    t.eq(proc.returncode, 1, "a name from this folder in the checks stops the build")
    t.ok("leak.txt" in proc.stdout, "and says which file")
    leak.unlink()
    save = root / ".claude" / "skills" / "save" / "SKILL.md"
    save.write_text(save.read_text() + "\nWrite to someone@" + "gmail.com\n")
    _, proc = build("email")
    t.eq(proc.returncode, 1, "so does an email address")


@test
def test_a_hand_edited_settings_file_never_crashes(t: Case) -> None:
    """`.os/config.json` and `.os/words.json` are files people are invited to
    edit. A line deleted out of one of them has to produce a sentence, not a
    Python traceback — the folder is somebody's filing cabinet, not a dev tool."""
    config = t.box.root / ".os" / "config.json"
    words = t.box.root / ".os" / "words.json"
    state = t.box.root / ".os" / "state.json"
    keep = {p: p.read_text() for p in (config, words, state)}
    try:
        # one setting gone
        data = json.loads(keep[config])
        del data["thresholds"]["stale_project_days"]
        config.write_text(json.dumps(data, indent=2))
        proc = t.box.run("status")
        t.ok("Traceback" not in proc.stderr, "a missing threshold falls back to the default")

        # the whole block gone
        data.pop("thresholds", None)
        data.pop("behaviour", None)
        config.write_text(json.dumps(data, indent=2))
        proc = t.box.run("status")
        t.ok("Traceback" not in proc.stderr, "a missing settings block is survivable too")

        # no folders at all: that one cannot be defaulted, so it must be said
        config.write_text(json.dumps({"name": "Zenith"}, indent=2))
        broken = t.box.run("status", expect=1)
        t.ok("buckets" in broken.stderr, "a config with no folders says which block is missing")
        t.ok("Traceback" not in broken.stderr, "and says it as a sentence")
        config.write_text(keep[config])

        # a vocabulary edited down to nothing is reported, not fatal
        words.write_text(json.dumps({"$schema": "zenith/words/2"}, indent=2))
        proc = t.box.run("status")
        t.ok("Traceback" not in proc.stderr, "an empty vocabulary still runs")
        codes = {i["code"] for i in t.box.json("check", expect=None)["issues"]}
        t.ok("no-vocabulary" in codes, "and check says the vocabulary is empty")
        words.write_text(keep[words])

        # state edited into something that is not state at all
        state.write_text("[1, 2, 3]")
        proc = t.box.run("status")
        t.ok("Traceback" not in proc.stderr, "unreadable state is treated as no state")

        # A setting that is present and is the wrong *type*. The first pass here
        # made a missing line safe; a line somebody actually wrote was still a
        # traceback, and it is by far the likelier of the two.
        data = json.loads(keep[config])
        data["behaviour"] = {"keep_undo_steps": "lots", "keep_backups": -5}
        data["thresholds"] = {"category_split": "many", "duplicate_similarity": "high"}
        config.write_text(json.dumps(data, indent=2))
        for words_of_command in (("save", "a note about the billing rewrite"),
                                 ("sort",), ("tidy",), ("check",)):
            proc = t.box.run(*words_of_command, expect=None)
            t.ok("Traceback" not in proc.stderr,
                 f"a setting written in words does not stop `{words_of_command[0]}`")
        data["thresholds"] = "not even a block"
        config.write_text(json.dumps(data, indent=2))
        proc = t.box.run("status")
        t.ok("Traceback" not in proc.stderr, "nor does a settings block that is a string")
        config.write_text(keep[config])

        # state.json with every field present and every one the wrong shape
        state.write_text(json.dumps({"counters": "x", "undo": "y", "history": 3}))
        for words_of_command in (("status",), ("save", "another billing note"), ("undo",)):
            proc = t.box.run(*words_of_command, expect=None)
            t.ok("Traceback" not in proc.stderr,
                 f"`{words_of_command[0]}` survives state of the wrong shape")
        state.write_text(keep[state])

        # words.json: the file people are told to open, in every half-edited
        # shape that used to be an AttributeError on the next save
        for broken, why in (({"domains": "notadict"}, "domains written as a string"),
                            ({"domains": {"x": []}}, "a subject written as a list"),
                            ({"domains": {"x": {"label": "X"}}}, "a subject with no keywords"),
                            ({"domains": {"x": {"keywords": ["q"]}}}, "a subject with no label"),
                            ({"domains": {}, "stopwords": "abc"}, "stopwords written as a string"),
                            ({"domains": {}, "intent": {"o": {"patterns": ["[unclosed"]}}},
                             "a pattern that will not compile")):
            words.write_text(json.dumps(broken, indent=2))
            for verb in ("status", "check", "words", "save"):
                args = (verb, "a thought about marketing") if verb == "save" else (verb,)
                proc = t.box.run(*args, expect=None)
                t.ok("Traceback" not in (proc.stderr + proc.stdout),
                     f"`{verb}` says something about {why}, rather than raising")
        words.write_text(keep[words])
    finally:
        for path, text in keep.items():
            path.write_text(text)
        t.box.run("index")


@test
def test_check_names_the_part_of_the_vocabulary_it_could_not_read(t: Case) -> None:
    """Reading around a broken entry is right; doing it silently is not.

    A subject that quietly stops matching anything is exactly the kind of wrong
    `./os check` exists to make loud, and it is invisible everywhere else."""
    words = t.box.root / ".os" / "words.json"
    keep = words.read_text()
    try:
        data = json.loads(keep)
        data["domains"]["broken"] = ["not", "a", "block"]
        data.setdefault("intent", {})["oops"] = {"patterns": ["[unclosed"]}
        data["stopwords"] = "abc"
        words.write_text(json.dumps(data, indent=2))
        report = t.box.json("check", expect=None)
        said = [i for i in report["issues"] if i["code"] == "words-unreadable"]
        t.gte(len(said), 3, "every unreadable entry is named")
        blob = " ".join(i["message"] for i in said)
        t.ok("broken" in blob, "including which subject")
        t.ok("oops" in blob, "including which intent")
        t.ok("stopwords" in blob, "including a list that is not one")
    finally:
        words.write_text(keep)
        t.box.run("index")


@test
def test_a_number_option_wants_a_number(t: Case) -> None:
    """`--limit all` is a guess at the spelling, not a reason to show a stack."""
    proc = t.box.run("find", "anything", "--limit", "all", expect=1)
    t.ok("Traceback" not in proc.stderr, "no traceback")
    t.ok("--limit" in proc.stderr, "it names the option")
    t.box.run("find", "anything", "--limit", "3", expect=None)


@test
def test_the_launcher_drives_the_folder_it_sits_in(t: Case) -> None:
    """A ZENITH_HOME left over from another folder must not redirect this one.

    Exported once in a shell profile — or by a hook — it used to make every
    other copy of `./os` quietly file into somewhere else entirely."""
    elsewhere = t.box.tmp / "other-zenith"
    shutil.copytree(t.box.root, elsewhere, symlinks=True)
    env = dict(os.environ, ZENITH_HOME=str(elsewhere), NO_COLOR="1")
    proc = subprocess.run([str(t.box.root / "os"), "status"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          cwd=str(t.box.root), env=env, timeout=120)
    t.eq(proc.returncode, 0, "it runs")
    t.ok(str(t.box.root) in proc.stdout, "and reports the folder it actually sits in")
    t.ok(str(elsewhere) not in proc.stdout, "not the one the environment named")
    shutil.rmtree(elsewhere, ignore_errors=True)


@test
def test_a_python_that_only_offers_to_install_itself_is_never_run(t: Case) -> None:
    """On a Mac without Apple's developer tools, /usr/bin/python3 is a stand-in
    that pops up an install box each time it runs. ./os ran it anyway — the
    hooks at every start and after every reply — and its own "install
    Python" help never showed."""
    others = [Path(d, n) for d in ("/usr/bin", "/bin") for n in ("python3.13", "python3.12", "python3.11")]
    if not Path("/usr/bin/python3").exists() or any(p.exists() for p in others):
        return
    fake = t.box.tmp / "fakebin"
    fake.mkdir()
    (fake / "uname").write_text("#!/bin/sh\necho Darwin\n")
    (fake / "xcode-select").write_text("#!/bin/sh\nexit 2\n")        # no developer tools
    for f in fake.iterdir():
        f.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "ZENITH_PYTHON"}
    env.update(PATH=f"{fake}:/usr/bin:/bin", NO_COLOR="1", CLAUDE_PROJECT_DIR=str(t.box.root))
    run = lambda *cmd: subprocess.run(list(cmd), input="{}", capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", env=env, timeout=120)

    proc = run(str(t.box.root / "os"), "--version")
    t.eq(proc.returncode, 1, "./os stops")
    t.ok("Zenith" not in proc.stdout, "without running the stand-in")
    t.ok("xcode-select --install" in proc.stderr, f"and says what to install\n{proc.stderr}")
    hook = run("bash", str(t.box.root / ".claude" / "hooks" / "session-start.sh"))
    t.eq(hook.returncode, 0, "the session still starts")
    t.ok("./os could not run" in hook.stdout, "and the AI is told ./os can't run here")

    (fake / "xcode-select").write_text("#!/bin/sh\necho /Library/Developer/CommandLineTools\n")
    proc = run(str(t.box.root / "os"), "--version")
    t.ok(proc.returncode == 0 and "Zenith" in proc.stdout,
         f"with the developer tools, that same python3 is used\n{proc.stderr}")


@test
def test_punctuation_in_a_title_cannot_break_the_index(t: Case) -> None:
    """A pipe in a title is ordinary English and used to be a broken table row."""
    t.box.run("new", "note", "Bench | results [draft]")
    t.box.run("index")
    rows = [ln for ln in (t.box.root / "INDEX.md").read_text().split("\n")
            if "Bench" in ln]
    t.eq(len(rows), 1, "the note is listed once")
    t.eq(len(re.findall(r"(?<!\\)\|", rows[0])), 4,
         "and it is still a three-column row")
    t.ok("\\|" in rows[0] and "\\[" in rows[0], "the punctuation is escaped, not dropped")


@test
def test_shell_completion_is_usable(t: Case) -> None:
    """Tab completion is how most people discover the rest of a CLI."""
    zsh = t.box.run("completion", "zsh").stdout
    t.ok(zsh.startswith("#compdef os"), "the zsh script declares itself")
    for command in ("save", "sort", "check", "demo", "undo", "find", "close", "hold"):
        t.ok(f"'{command}:" in zsh, f"zsh completion offers {command}")

    bash = t.box.run("completion", "bash").stdout
    t.ok("complete -F _os_complete os" in bash, "the bash script registers itself")
    script = t.box.tmp / "completion.bash"
    script.write_text(bash)
    check = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    t.eq(check.returncode, 0, f"the bash completion parses: {check.stderr}")


@test
def test_empty_buckets_stay_quiet(t: Case) -> None:
    """An empty folder should look empty, not broken."""
    t.box.run("index")
    for bucket in ("Work", "Notes"):
        visible = [p.name for p in (t.box.root / bucket).iterdir()
                   if not p.name.startswith(".")]
        t.eq(visible, [], f"{bucket} is genuinely empty, not full of scaffolding")

    plain = t.box.run("status").stdout
    for jargon in ("taxonomy", "front matter", "idempotent", "/100"):
        t.ok(jargon not in plain.lower(), f"an empty folder never says '{jargon}'")
    t.ok("save" in plain, "an empty folder tells you the one thing to try")

    brief = t.box.run("brief").stdout
    t.ok("first visit" in brief.lower(),
         "a brand-new folder tells the AI this person has never seen it")
    t.ok("Commands, folder names" in brief and "wait until" in brief,
         "and that a wall of commands is not how a first visit opens")


@test
def test_the_brief_is_written_as_facts(t: Case) -> None:
    """What the hooks hand the AI says how things are, not what to do.

    Claude Code's hooks docs warn that text framed as out-of-band system
    commands can trip the AI's prompt-injection defences, and then it is shown
    to the person instead of read — on the first visit, of all sessions."""
    orders = ("Do NOT", "Do not", "Your first message", "Talk to them", "Never say",
              "Offer to", "Say \"")
    first = t.box.run("brief").stdout
    t.box.run("save", "Ring the plumber about the boiler before Friday")
    later = t.box.run("brief").stdout
    (t.box.root / "Notes" / "scratch.md").write_text("# scratch\n")
    settled = json.loads(t.box.run("index", "--notify").stdout)["systemMessage"]
    for name, text in (("the first-visit brief", first), ("the everyday brief", later),
                       ("the settling note", settled)):
        said = [o for o in orders if o in text]
        t.eq(said, [], f"{name} gives no orders")
    t.ok("AGENTS.md" in first and "AGENTS.md" in later,
         "and says where the folder's own conventions are written")


@test
def test_what_they_said_about_themselves_is_in_every_brief(t: Case) -> None:
    """"From now on call me Sam" was filed as an ordinary note and never read
    again, so the next session had forgotten it. Facts about them live in one
    About me note — a single note, or a folder of them — and its first lines
    are in every brief. With no such note, the brief says nothing about it."""
    root = t.box.root
    t.box.run("save", "Ring the plumber about the boiler before Friday")
    t.ok("About them" not in t.box.run("brief").stdout, "no note, no lines about them")

    t.box.run("new", "note", "About me", "--domain", "personal")
    note = root / "Notes" / "about-me.md"
    t.ok(note.exists(), "`./os new note \"About me\"` makes it where the brief looks")
    note.write_text(note.read_text().replace(
        "## What it says\n", "## What it says\n- Call me Sam.\n- Answers under 100 words.\n", 1))
    brief = t.box.run("brief").stdout
    t.ok("About them (Notes/about-me.md)" in brief, "the brief says where they are")
    t.ok("- Call me Sam." in brief and "- Answers under 100 words." in brief,
         "and what they said, word for word")
    t.ok("## What it says" not in brief and "<!--" not in brief,
         "without the note's headings or its prompts")
    t.ok("Call me Sam" in t.box.json("brief")["hookSpecificOutput"]["additionalContext"],
         "and the AI is handed them at the start of a session")

    # This folder's own way: a folder called About me, its README pointing on.
    t.box.run("close", "about-me")
    folder = root / "Notes" / "About me"
    folder.mkdir()
    (folder / "README.md").write_text(
        "---\ntitle: About me\ntype: note\ndomain: personal\ntags: []\n"
        "created: 2026-09-29\nupdated: 2026-09-29\n---\n\n# About me\n\n"
        "Who I am: [who-i-am.md](who-i-am.md). Read it before doing anything for me.\n")
    (folder / "who-i-am.md").write_text("# Who I am\n\n- Plain words, bad news first.\n")
    brief = t.box.run("brief").stdout
    t.ok("About them (Notes/About me/README.md)" in brief, f"a folder works too\n{brief}")
    t.ok("Read it before doing anything for me." in brief, "with the lines of its README")
    t.ok("More in Notes/About me/: who-i-am.md" in brief, "and the files beside it named")
    t.ok("Call me Sam" not in brief, "a closed About me is not read any more")

    for doc in (root / "AGENTS.md", root / ".claude" / "skills" / "save" / "SKILL.md"):
        if doc.is_file() and not in_their_words(doc, t.box):
            t.ok('./os new note "About me"' in doc.read_text(), f"{doc.name} says where such facts go")


@test
def test_a_title_cannot_write_its_own_front_matter(t: Case) -> None:
    """A newline in a title used to become a second front-matter *key*.

        os new note "Harmless
        type: work"

    wrote a note on disk that told everything reading it it was work: it was
    listed in the wrong place, and `./os hold` would have taken it as something
    it could change the phase of. Titles arrive from the clipboard as often as
    the keyboard, so this is one paste away."""
    t.box.run("new", "note", "Harmless\ndomain: engineering\ntype: work")
    hit = next(p for p in (t.box.root / "Notes").glob("*.md") if "harmless" in p.name)
    meta, _ = engine.parse_frontmatter(hit.read_text(encoding="utf-8"))
    t.eq(meta.get("type"), "note",
         f"the file is what it was made as (got {meta.get('type')!r})")
    t.eq(meta.get("domain"), engine.catch_all(engine.Zenith(t.box.root).taxonomy),
         "and the injected subject did not take: it is where nothing matched")
    t.ok("\n" not in str(meta.get("title", "")), "the title is one line")
    t.ok("type: work" in str(meta.get("title", "")), "with every word of it kept")

    # and the same through the other door: a value stamped into an existing file
    stamped = engine.compose({"type": "note", "title": "One\nTwo: three"}, "body")
    again, _ = engine.parse_frontmatter(stamped)
    t.eq(again.get("type"), "note", "a stamped value cannot be displaced either")
    t.ok("\n" not in str(again.get("title", "")), "and is written on one line")


@test
def test_a_name_can_be_typed_the_way_it_is_said(t: Case) -> None:
    """A handle is only worth having if typing it the obvious way works.

    Things are addressed by name, and a name is said out loud far more often
    than it is copied — so the title spoken plainly has to find the same item
    as the hyphenated name sitting on disk, in whatever case it is typed."""
    t.box.run("save", "The billing rewrite has to ship before the audit")
    item = t.box.carrying("billing rewrite")
    name, title = item["id"], item["title"]
    t.ok(name, "something was filed and named")
    for spelling in (name, name.upper(), title, title.lower(), title.upper()):
        proc = t.box.run("show", spelling)
        t.ok(name in proc.stdout, f"`./os show {spelling!r}` finds {name}")
    t.box.run("show", "nothing-is-called-this-at-all", expect=1)


@test
def test_a_name_reaches_the_thing_it_names(t: Case) -> None:
    """The name on disk beats a title two things share, and every command ./os
    prints for a name works pasted as it stands.

    `./os close same-title-here` put away same-title-here-2.md; the handle
    `resume-review-2` that ./os had just printed found nothing; and hints
    like `./os push Garden Shed` read as `push Garden` (review, 2026-09-29)."""
    def hinted(output: str, marker: str) -> list:
        """The first command printed after `marker`, as pasted: split on spaces
        the way a shell does, and read as all these commands read it, the verb
        and one word."""
        after = output[output.index(marker):]
        return after[after.index("./os ") + len("./os "):].split()[:2]

    t.box.run("new", "note", "Same Title Here", "--domain", "writing")
    t.box.run("new", "note", "Same Title Here", "--domain", "writing", "--anyway")
    t.box.run("close", "same-title-here")
    left = sorted(p.name for p in (t.box.root / "Notes").rglob("same-title-here*"))
    t.eq(left, ["same-title-here-2.md"], "close put away the file with that name")

    first = t.box.run("new", "work", "Resume review", "--domain", "writing").stdout
    second = t.box.run("new", "work", "Resume review", "--domain", "writing", "--anyway").stdout
    flip = hinted(second, "./os hold")
    t.ok(flip != hinted(first, "./os hold"), f"the second one has a name of its own ({flip})")
    t.box.run(*flip)
    held = [Path(i["path"]).name for i in t.box.items() if i["status"] == "holding"]
    t.ok(len(held) == 1 and engine.handle(held[0]) == flip[1],
         f"and that name reaches it, not the first ({held})")

    t.box.run("new", "work", "Garden Shed", "--domain", "writing")
    twice = t.box.run("new", "work", "Garden Shed", "--domain", "writing", expect=1)
    t.box.run(*hinted(twice.stderr, "Look at it:"))
    t.box.run(*hinted(t.box.run("hold", "Garden Shed").stdout, "back the other way?"))
    closed = t.box.run("close", "Garden Shed").stdout
    shown = t.box.run("show", "garden-shed").stdout
    t.ok("./os back garden-shed" in shown and "./os close" not in shown,
         "something put away is offered back, not put away again")
    t.box.run(*hinted(closed, "changed your mind?"))
    t.ok(any(i["path"] == "Work/Garden Shed" for i in t.box.items()),
         "and the hint close printed brings it back")


@test
def test_the_index_survives_a_filename_written_across_two_lines(t: Case) -> None:
    """A newline in a name split one table row in half and took the next one
    with it. Pipes and brackets were already handled; this was not."""
    awkward = t.box.root / "Notes" / "two\nlines (and parens).md"
    try:
        awkward.write_text("# Two lines\n\nSomething to look up later.\n", encoding="utf-8")
    except OSError:
        return                      # a filesystem that will not take it at all
    t.box.run("index")
    lines = (t.box.root / "INDEX.md").read_text(encoding="utf-8").split("\n")
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        t.ok(line.rstrip().endswith("|"),
             f"no row stops halfway through: {line[:90]!r}")
        t.ok(line.count("[") == line.count("]") and line.count("(") == line.count(")"),
             f"and no link is left open at the end of one: {line[:90]!r} (line {i})")
    items = [line for line in lines if line.startswith("| [")]
    for row in items:
        t.eq(row.count("|") - row.count("\\|"), 4,
             f"every item row still has its three columns: {row[:90]!r}")
    t.ok(any("%0A" in row for row in items), "and the awkward name is still linked")


@test
def test_two_runs_writing_at_once_do_not_crash_each_other(t: Case) -> None:
    """`settle.sh` rebuilds the index in the background while the chat may be
    running `./os save` in front of it, and both write registry.json.

    Sharing one temp name, the first to finish replaced it out from under the
    second, which then died on `os.replace` with a FileNotFoundError — about a
    file it had itself written correctly."""
    target = t.box.root / ".os" / "cache" / "contended.json"
    payloads = [json.dumps({"who": who, "pad": [who] * 400}) for who in ("a", "b", "c", "d")]
    script = (
        "import json,sys\n"
        "sys.dont_write_bytecode = True\n"
        f"sys.path.insert(0, {str(SOURCE / '.os')!r})\n"
        "import engine\n"
        "from pathlib import Path\n"
        "for _ in range(150):\n"
        f"    engine.write_text(Path({str(target)!r}), sys.argv[1])\n")
    runners = [subprocess.Popen([sys.executable, "-c", script, body],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
               for body in payloads]
    torn = 0
    for _ in range(400):
        try:
            json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (ValueError, OSError):
            torn += 1
    for runner in runners:
        _, err = runner.communicate(timeout=120)
        t.eq(runner.returncode, 0, f"a writer that lost the race still finished:\n{err[-400:]}")
        t.ok("Traceback" not in err, "and never with a traceback")
    t.eq(torn, 0, "and nobody ever read half a file")
    t.eq(list((t.box.root / ".os" / "cache").glob("*.tmp~")), [],
         "no temp file is left behind")


@test
def test_undoing_a_sort_does_not_claim_a_link_was_lost(t: Case) -> None:
    """Snapshots used to read and write *through* a symlink, so undoing a sort
    that had adopted one reported a permission error about /etc — on a link it
    had in fact put back perfectly."""
    link = t.box.root / "Notes" / "pointer.md"
    try:
        link.symlink_to(Path("/etc/hosts"))
    except OSError:
        return
    t.box.run("sort")
    proc = t.box.run("undo")
    t.ok("couldn't put this one back" not in proc.stdout,
         f"undo does not report a failure it did not have:\n{proc.stdout[-400:]}")
    t.ok(link.is_symlink(), "and the link is back where it was")
    link.unlink()
    t.box.run("index")


@test
def test_a_busy_folder_says_the_words_are_not_lost(t: Case) -> None:
    """`./os save` writes what was typed down *before* it takes the lock, so a
    run turned away has already kept it. Being told only that the folder is
    busy reads as "your words are gone", and they never are."""
    lock = t.box.root / ".os" / ".lock"
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock.write_text(json.dumps({"pid": holder.pid, "at": time.time(), "label": "sort"}))
        with lock_wait("0"):
            proc = t.box.run("save", "Chase the Northwind contract before Friday", expect=1)
        t.ok("already working here" in proc.stderr, "it says the folder is busy")
        t.ok("safe" in proc.stderr, "and that what was typed is not lost")
        t.ok("./os sort" in proc.stderr, "and how to finish filing it")
        t.ok("Traceback" not in proc.stderr, "as a sentence")
    finally:
        holder.kill()
        holder.wait(timeout=30)
        if lock.exists():
            lock.unlink()
    staged = list((t.box.root / ".os" / "cache" / "incoming").glob("*"))
    t.ok(staged, "and the words really are on disk")
    t.box.run("sort")
    t.ok(t.box.locate("Northwind contract before Friday") is not None,
         "where ./os sort picks them up")


@test
def test_the_settling_hook_does_not_drop_what_it_could_not_file(t: Case) -> None:
    """The mark that something changed was cleared before the rebuild, not
    after. A rebuild that lost a race — or hit a disk that said no — took the
    folder's only record that anything had changed with it."""
    hook = t.box.root / ".claude" / "hooks" / "settle.sh"
    if not hook.exists():
        return
    dirty = t.box.root / ".os" / ".dirty"
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(t.box.root), NO_COLOR="1")

    dirty.write_text("")
    proc = subprocess.run(["bash", str(hook)], capture_output=True, text=True,
                          env=env, timeout=120)
    t.eq(proc.returncode, 0, "it never fails a session")
    t.ok(not dirty.exists(), "a rebuild that worked clears the mark")

    # now make the rebuild impossible and check the mark comes back
    launcher = t.box.root / "os"
    keep = launcher.read_text(encoding="utf-8")
    try:
        launcher.write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8")
        dirty.write_text("")
        proc = subprocess.run(["bash", str(hook)], capture_output=True, text=True,
                              env=env, timeout=120)
        t.eq(proc.returncode, 0, "and still never fails a session")
        t.ok(dirty.exists(), "a rebuild that did not happen keeps the mark for next time")
    finally:
        launcher.write_text(keep, encoding="utf-8")
        launcher.chmod(0o755)
        if dirty.exists():
            dirty.unlink()


@test
def test_check_names_the_part_of_the_vocabulary_it_could_not_read(t: Case) -> None:
    """Reading around a broken entry is right; doing it silently is not.

    A subject that quietly stops matching anything is exactly the kind of wrong
    `./os check` exists to make loud, and it is invisible everywhere else."""
    words = t.box.root / ".os" / "words.json"
    keep = words.read_text()
    try:
        data = json.loads(keep)
        data["domains"]["broken"] = ["not", "a", "block"]
        data.setdefault("intent", {})["oops"] = {"patterns": ["[unclosed"]}
        data["stopwords"] = "abc"
        words.write_text(json.dumps(data, indent=2))
        report = t.box.json("check", expect=None)
        said = [i for i in report["issues"] if i["code"] == "words-unreadable"]
        t.gte(len(said), 3, "every unreadable entry is named")
        blob = " ".join(i["message"] for i in said)
        t.ok("broken" in blob, "including which subject")
        t.ok("oops" in blob, "including which intent")
        t.ok("stopwords" in blob, "including a list that is not one")
    finally:
        words.write_text(keep)
        t.box.run("index")


@test
def test_a_refusal_from_the_fetcher_says_which_refusal(t: Case) -> None:
    """Every failure used to come back as "no captions on this one" — a deleted
    video, a rate-limit and a dropped connection all said the one thing they
    were not. Only one of those is a reason to go and find another source, so
    three times in four the honest response to it was wasted work."""
    class Said:
        def __init__(self, err: str, code: int = 1):
            self.stderr, self.returncode, self.stdout = err, code, ""

    for err, expected in (
            ("ERROR: [youtube] xx: Video unavailable", "available"),
            ("ERROR: [youtube] xx: Private video. Sign in if you've been granted access", "private"),
            ("ERROR: unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests",
             "rate-limiting"),
            ("ERROR: [generic] x: Unable to download webpage: Failed to resolve 'nope.invalid'",
             "connection"),
            ("ERROR: [youtube] xx: Sign in to confirm your age", "age-restricted")):
        why = learn.why_empty(Said(err))
        t.ok(expected in why, f"{expected!r} is said for {err[:44]!r} (got {why!r})")
        t.ok("Traceback" not in why and len(why) < 160, "in one plain sentence")
        t.ok("caused by" not in why, "with none of yt-dlp's own plumbing in it")

    t.eq(learn.why_empty(Said("WARNING: [youtube] no supported JavaScript runtime\n"
                              "ERROR: [youtube] xx: There are no subtitles for the requested languages")),
         "no captions on this one",
         "and a video that genuinely has none still says so")
    t.eq(learn.why_empty(Said("", code=0)), "no captions on this one",
         "as does one that failed at nothing")


@test
def test_a_source_in_another_language_is_still_a_source(t: Case) -> None:
    """Asking only for `en.*` meant a video spoken in anything else reported
    "no captions on this one" while sitting on a full set of them. YouTube
    always names the spoken track `<lang>-orig`, so one extra pattern reaches
    it — and never a hundred files."""
    t.ok("en" in learn.SUB_LANGS and "-orig" in learn.SUB_LANGS,
         "English first, then whatever it was spoken in")
    t.eq(learn._track_lang(Path("vid.en.vtt")), "en", "the language comes out of the name")
    t.eq(learn._track_lang(Path("vid.ja-orig.vtt")), "ja",
         "and `-orig` is a fact about the track, not the language")
    ranked = sorted([Path("v.ja-orig.vtt"), Path("v.en.vtt"), Path("v.de.vtt")],
                    key=learn._track_rank)
    t.eq(ranked[0].name, "v.en.vtt", "English wins where it is on offer")


@test
def test_a_phrase_is_found_however_its_words_are_joined(t: Case) -> None:
    """The title is worth 3.0 and a mention in the body 0.6 — and for anything
    dropped in or captured, the title *is* the filename, hyphens and all.

    A phrase written with a space could never match one, so every multi-word
    term in words.json was invisible in the one place that counted most. That
    is most of what a subject ever teaches the folder: "ad set", "learning
    phase", "cash flow" — not nouns."""
    match = engine.Classifier._matcher
    rx = match("poke test")
    for joined in ("the poke test sprang", "a poke-test sprang", "poke_test today"):
        t.ok(rx.findall(joined), f"{joined!r} is the same phrase")
    t.eq(rx.findall("pokes test"), [], "but a longer word is still not a match")

    t.eq(match("ci").findall("pricing broke"), [],
         "and a short word still does not fire inside another one")

    # the whole point, through the real command
    t.box.run("words", "personal", "poke test")
    box = engine.Zenith(t.box.root)
    _, score, _ = engine.Classifier(box).score_domain("poke-test-sprang-back", "", "")
    t.gte(score, 3.0, "a phrase in a hyphenated filename scores like a title")

    # and one term written two ways is still one term, not two
    both = engine.Classifier._terms(["to-do", "to do", "to  do", "roas"])
    t.eq([kw for kw, _ in both], ["to-do", "roas"],
         "so 'Nothing to do' cannot score the same term twice")


@test
def test_a_tie_between_subjects_goes_to_the_longer_word(t: Case) -> None:
    """A dead heat used to go to whichever subject came first in the alphabet.

    Teaching the folder "poke test" — the whole point of `./os words` — drew
    3.0 against `engineering`, which owns the word "test", and lost to it for
    beginning with an e. The longer match is the better evidence: a two-word
    phrase is a subject saying its own name."""
    t.box.run("words", "personal", "poke test")
    box = engine.Zenith(t.box.root)
    domain, _, scores = engine.Classifier(box).score_domain(
        "the poke test sprang back too fast this morning", "", "")
    t.eq(scores.get("personal"), scores.get("engineering"),
         f"the two really are tied ({scores})")
    t.eq(domain, "personal", "and the phrase wins over the single generic word")

    # a tie with nothing to separate them still has to land somewhere, and
    # land in the same place every time
    twice = [engine.Classifier(engine.Zenith(t.box.root)).score_domain(
        "the poke test sprang back too fast this morning", "", "")[0] for _ in range(3)]
    t.eq(set(twice), {"personal"}, "and it is the same answer every time")


@test
def test_a_learning_note_comes_out_in_the_right_shape(t: Case) -> None:
    """`.os/templates/learning.md` was the documented shape of a /learn note and
    nothing could produce it: the only route was an ordinary note with its
    headings retyped by hand, which is a step that gets skipped, and did."""
    t.box.run("new", "learning", "How sourdough is actually made")
    hit = next(p for p in (t.box.root / "Notes").glob("*.md") if "sourdough" in p.name)
    body = hit.read_text(encoding="utf-8")
    meta, _ = engine.parse_frontmatter(body)
    t.eq(meta.get("type"), "note", "it is still a note, in Notes, named like one")
    t.eq(hit.stem, "how-sourdough-is-actually-made", "under its own title, slugified")
    for heading in ("## The method", "## Where they disagree", "## What goes wrong",
                    "## Practice", "## Sources"):
        t.ok(heading in body, f"and carries {heading}")
    t.ok("## What it says" not in body, "and not the plain note's headings")

    plain = t.box.run("new", "note", "How Postgres indexes work")
    t.ok("Traceback" not in plain.stderr, "an ordinary note is untouched")
    other = next(p for p in (t.box.root / "Notes").glob("*.md") if "postgres" in p.name)
    t.ok("## What it says" in other.read_text(encoding="utf-8"),
         "and still gets the ordinary shape")


@test
def test_machine_output_is_only_ever_the_object(t: Case) -> None:
    """The very first command in a folder nobody has opened prints a welcome,
    and it printed it above the JSON — so the first `./os brief --json` any AI
    ran in any fresh folder came back unparseable. Which is every folder, once."""
    state = t.box.root / ".os" / "state.json"
    keep = state.read_text()
    try:
        data = json.loads(keep)
        data["fresh"] = True
        state.write_text(json.dumps(data, indent=2))
        proc = t.box.run("brief", "--json")
        json.loads(proc.stdout)          # raises, and fails the test, if it is not
        t.ok("Welcome" not in proc.stdout, "no greeting on the machine's channel")

        # and the greeting is not simply gone: a person still gets it
        data["fresh"] = True
        state.write_text(json.dumps(data, indent=2))
        t.ok("Welcome" in t.box.run("status").stdout, "a person opening it is still greeted")
    finally:
        state.write_text(keep)
        t.box.run("index")


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


@test
def test_rename_moves_the_name_and_the_title_together(t: Case) -> None:
    """The name is the handle, so it must never disagree with the title.

    Renaming by hand left a folder saying one thing and a header saying
    another, and `./os show` finding neither (snag, 2026-09-05)."""
    t.box.run("new", "work", "Q3 OKR review", "--domain", "writing")
    t.box.run("new", "note", "How to run a retro", "--domain", "design")
    t.box.run("index")
    out = t.box.run("rename", "q3-okr-review", "Q4 OKR review")
    t.ok("renamed" in out.stdout.lower(), "it says so")
    t.box.run("index")
    item = next(i for i in t.box.items() if i["title"] == "Q4 OKR review")
    t.eq(item["path"].split("/")[-1], "Q4 OKR Review", "the folder is Title Case With Spaces")
    readme = (t.box.root / item["path"] / "README.md").read_text()
    t.ok("# Q4 OKR review" in readme and "# Q3 OKR review" not in readme,
         "the heading in the file follows the title")
    t.ok(not (t.box.root / "Work" / "Q3 OKR Review").exists(), "and the old one is gone")
    shown = t.box.run("show", "q4-okr-review").stdout
    t.ok("Work/Q4 OKR Review" in shown, "the new name finds it")
    t.ok("./os edit q4-okr-review" in shown, "and the hint under it is one word, pasteable")
    t.ok("Work/Q4 OKR Review" in t.box.run("show", "Q4 OKR review").stdout,
         "however it is typed")

    t.box.run("rename", "how-to-run-a-retro", "Running a retro")
    t.box.run("index")
    note = next(i for i in t.box.items() if i["title"] == "Running a retro")
    t.ok(note["path"].endswith("running-a-retro.md"), "a note stays a kebab-case file")

    t.box.run("undo")
    t.box.run("index")
    t.ok(any(i["title"] == "How to run a retro" for i in t.box.items()),
         "undo puts the old name back")
    t.box.run("rename", "nothing-called-this", "Whatever", expect=1)
    t.box.run("new", "work", "Ship the redesign", "--domain", "design")
    taken = t.box.run("rename", "q4-okr-review", "Ship the redesign", expect=1)
    t.ok("already" in taken.stdout + taken.stderr, "a name in use is refused, not overwritten")


@test
def test_a_rename_takes_the_links_to_it_along(t: Case) -> None:
    """`./os rename` says "everywhere at once", yet links to the old name were
    left pointing at nothing, and `./os check` then reported the broken link
    the rename itself had made (review, 2026-09-29)."""
    for kind, title in (("note", "Sourdough guide link"), ("work", "Garden Shed"),
                        ("note", "How to bake sourdough")):
        t.box.run("new", kind, title, "--domain", "writing")
    t.box.run("index")
    where = {i["title"]: t.box.root / i["path"] for i in t.box.items()}
    guide = where["How to bake sourdough"]

    def link(to: Path) -> str:
        return Path(os.path.relpath(to, guide.parent)).as_posix().replace(" ", "%20")

    guide.write_text(guide.read_text()
                     + f"\n- [the guide]({link(where['Sourdough guide link'])})"
                     + f"\n- [the shed]({link(where['Garden Shed'] / 'README.md')})"
                     + "\n- [online](https://example.com/sourdough-guide-link.md)\n")
    t.box.run("rename", "sourdough-guide-link", "Serious Eats sourdough")
    said = t.box.run("rename", "garden-shed", "Garden Cabin").stdout
    text = guide.read_text()
    t.ok("serious-eats-sourdough.md)" in text, f"a link to a renamed note follows it\n{text}")
    t.ok("Garden%20Cabin/README.md)" in text, "and so does one into a renamed folder")
    t.ok("(https://example.com/sourdough-guide-link.md)" in text, "a web address is left alone")
    t.ok("links to it" in said, "the rename says so")
    broken = [i for i in t.box.json("check", expect=None)["issues"] if i["code"] == "broken-link"]
    t.eq(broken, [], "and check finds nothing the rename broke")
    t.box.run("undo")
    t.ok("Garden%20Shed/README.md)" in guide.read_text(), "undo puts the links back with the name")


@test
def test_a_rename_can_change_only_the_capitals(t: Case) -> None:
    """`Q3 okr review` is filed as `Q3 Okr Review`, and the obvious repair was
    refused on a Mac as a clash with itself: that disk doesn't tell capitals
    apart, so the new name looked taken (review, 2026-09-29)."""
    t.box.run("new", "work", "Q3 okr review", "--domain", "writing")
    folders = lambda: sorted(p.name for p in (t.box.root / "Work").iterdir() if p.is_dir())
    was = folders()
    t.box.run("rename", "q3-okr-review", "Q3 OKR Review")
    t.eq(folders(), ["Q3 OKR Review"], "one folder, spelled the new way, and no -2")
    t.box.run("undo")
    t.eq(folders(), was, "and undo puts the old spelling back")


@test
def test_a_subject_new_does_not_know_is_refused(t: Case) -> None:
    """`new` and `words` agree on what a subject is.

    `./os new learning --domain trading` wrote `trading` into a header when no
    such subject existed, and `./os words trading` then said there was no
    domain called that (snag, 2026-08-30)."""
    proc = t.box.run("new", "note", "Order flow basics", "--domain", "tradng", expect=1)
    said = proc.stdout + proc.stderr
    t.ok("no subject called" in said, "it is refused")
    t.ok("engineering" in said, "and the subjects it does know are listed")
    t.box.run("index")
    t.ok(not any(i["title"] == "Order flow basics" for i in t.box.items()), "nothing was made")
    known = next(iter(engine.Zenith(t.box.root).taxonomy["domains"]))
    t.box.run("new", "note", "Order flow basics", "--domain", known)
    t.box.run("index")
    t.ok(any(i["title"] == "Order flow basics" for i in t.box.items()), "a known one goes through")


@test
def test_a_multiline_description_reaches_the_catalog(t: Case) -> None:
    """A skill written with `description: |` is still described.

    The catalog rendered one as "/" because the header reader only took
    single-line values (snag, 2026-08-30)."""
    folder = t.box.root / ".claude" / "skills" / "weekly-brief"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: weekly-brief\ndescription: |\n  Writes the Monday brief from what changed.\n"
        "  Use when they ask for the brief.\n---\n\nDo the brief.\n")
    meta, _ = engine.parse_frontmatter((folder / "SKILL.md").read_text())
    t.eq(meta["description"], "Writes the Monday brief from what changed.\nUse when they ask for the brief.",
         "the block is read whole")
    folded, _ = engine.parse_frontmatter("---\ntitle: x\nsummary: >\n  one\n  two\ntags: [a]\n---\nbody\n")
    t.eq(folded["summary"], "one two", "a folded block joins with spaces")
    t.eq(folded["tags"], ["a"], "and the key after it is still read")
    t.box.run("index")
    catalog = (t.box.root / ".claude" / "CATALOG.md").read_text()
    t.ok("Writes the Monday brief from what changed" in catalog, "the catalog shows it")
    t.ok("| `/weekly-brief` | / |" not in catalog, "not a bare slash")

    (folder / "SKILL.md").write_text(
        "---\nname: weekly-brief\ndescription: Writes the Friday brief. Use when asked.\n---\n\nDo it.\n")
    t.box.run("check", "--fix")
    t.ok("Writes the Friday brief" in (t.box.root / ".claude" / "CATALOG.md").read_text(),
         "an edited description reaches the catalog on the next check --fix")


@test
def test_an_account_of_finished_work_is_a_note(t: Case) -> None:
    """A wrap-up saying what shipped has nothing left to push on.

    "Folder improvements shipped 2026-09-08: ..." became a Work item because it
    said *shipped* (snag, 2026-09-08)."""
    t.box.run("save", "Folder improvements shipped this week: claim and release so two "
                      "chats never build the same thing; three save bugs fixed; skills "
                      "split under the cap. Open: two tests fail on limits the folder outgrew.")
    t.box.run("index")
    listed = t.box.carrying("Folder improvements shipped this week")
    t.eq(listed["kind"], "note", "an account of finished work is a note")
    t.box.run("save", "Ship the billing rewrite by Friday: migrate the schema, "
                      "then cut over. Next step is reproducing the bug on staging.")
    t.box.run("index")
    t.eq(t.box.carrying("Ship the billing rewrite")["kind"], "project",
         "work with a next action is still work")


@test
def test_a_researched_answer_stays_a_note(t: Case) -> None:
    """@researcher's note ends in "What this means for the decision", and
    `./os save` read that as work: it came back as something on the go, and
    later as going cold. It makes the note with `./os new note` instead and
    writes into the file that made."""
    helper = t.box.root / ".claude" / "agents" / "researcher.md"
    if not helper.exists():
        return
    steps = helper.read_text()
    t.ok('./os save "<the note>"' not in steps, "the helper no longer saves its note")
    t.ok("./os new note" in steps, "it makes the note's file with ./os new note")

    t.box.run("new", "note", "Which password manager should we switch to?")
    note = next((t.box.root / "Notes").glob("which-password-manager*.md"))
    head = note.read_text().split("\n# ", 1)[0]
    note.write_text(head + "\n# Which password manager should we switch to?\n\n"
                    "## In one line\nBitwarden: audited, and cheapest for a family.\n\n"
                    "## What this means for the decision\n"
                    "Switch to Bitwarden this weekend and move the shared vault first.\n")
    t.box.run("sort")
    t.eq(t.box.carrying("Switch to Bitwarden this weekend")["kind"], "note",
         "the answer stays a note")
    t.ok("Which password manager" not in t.box.run("brief").stdout,
         "and is not listed as something on the go")


@test
def test_check_and_test_leave_a_shipped_folder_fresh(t: Case) -> None:
    """Running the maintainer's commands inside the template must not adopt it.
    Before this, one `./os check` in the shipped copy flipped state.json from
    fresh to installed and left the template dirty in git."""
    for bucket in ("Work", "Notes", "Archive"):
        shutil.rmtree(t.box.root / bucket)       # the shipped copy has none
    state = t.box.root / ".os" / "state.json"
    state.write_text(json.dumps(
        {"counters": {}, "undo": [], "history": [], "created": "template", "fresh": True},
        indent=2))
    t.box.run("check")
    t.eq(json.loads(state.read_text()).get("fresh"), True,
         "check leaves the folder fresh")
    t.box.run("help")
    t.eq(json.loads(state.read_text()).get("fresh"), True,
         "help leaves the folder fresh")
    t.box.run("status")
    t.eq(json.loads(state.read_text()).get("fresh"), False,
         "a real command still adopts it")


@test
def test_last_says_what_happened_last_time(t: Case) -> None:
    """`./os last` is read from the items' own ## Log lines, so any AI can pick
    up cold. Nothing is remembered outside the folder."""
    empty = t.box.json("last")
    t.eq(empty["day"], "", "nothing logged yet means no day")
    t.box.run("new", "work", "Ship the redesign")
    readme = next((t.box.root / "Work").rglob("README.md"))
    text = readme.read_text()
    t.ok("## Log" in text, "a new item carries a ## Log")
    day = engine.today()
    readme.write_text(text.rstrip("\n") + "\n- 2026-01-05 — cut the hero copy in half\n"
                      f"- {day} — sent the draft to Sam for review\n")
    result = t.box.json("last")
    t.eq(result["day"], day, "the most recent day wins, filing included")
    t.eq(len(result["touched"]), 1, "one item was touched that day")
    t.gte(len(result["filed"]), 1, "and what ./os filed that day is listed too")
    t.eq(result["touched"][0]["log"], "sent the draft to Sam for review",
         "the log line comes back without its date")
    plain = t.box.run("last").stdout
    t.ok("Ship the redesign" in plain and "sent the draft" in plain,
         "the plain report names the item and the line")


@test
def test_the_newest_lines_of_a_long_file_are_still_read(t: Case) -> None:
    """## Log is added to at the bottom and nothing trims it. Search, ./os last
    and ./os show read only the top of a file, so in a long README the newest
    lines were the ones nothing could find."""
    t.box.run("new", "work", "Run the trading bot")
    readme = next((t.box.root / "Work").rglob("README.md"))
    old = "".join(f"- 2026-01-{1 + n % 28:02d} — ran the backtest again and wrote "
                  f"down every number it printed, run {n}\n" for n in range(3000))
    readme.write_text(readme.read_text().rstrip("\n") + "\n" + old
                      + f"- {engine.today()} — switched the broker to Zorblax, "
                        "the fees were lower\n")
    t.gte(readme.stat().st_size, 200_000, "the README is long")
    t.box.run("index")

    found = t.box.json("find", "zorblax", expect=None)
    t.ok(any(f["title"] == "Run the trading bot" for f in found),
         "search finds a word in the newest Log line")
    last = t.box.json("last")
    t.eq(last["day"], engine.today(), "./os last sees the newest day")
    t.ok(any("Zorblax" in x["log"] for x in last["touched"]), "and the newest line")
    t.ok("Zorblax" in t.box.run("show", "run-the-trading-bot").stdout,
         "show gives it under lately")

    # Size is counted in bytes and the start was read in characters: in a file
    # of mostly Chinese, both ends were the whole file, and its end came twice.
    wide = t.box.tmp / "wide.md"
    wide.write_text("".join(f"- 第{n}行：今天把所有的数字都记下来了\n" for n in range(2600))
                    + "- THE LAST LINE\n", encoding="utf-8")
    t.ok(120_000 < wide.stat().st_size and len(wide.read_text(encoding="utf-8")) < 120_000,
         "more bytes than the two ends, fewer characters")
    t.eq(engine.read_ends(wide).count("THE LAST LINE"), 1, "the last line is read once")


@test
def test_a_folder_named_in_ignore_is_left_alone(t: Case) -> None:
    """Big media lives in Work/Content/ and `./os` never files, nags or renames it."""
    media = t.box.root / "Work" / "Content" / "Footage" / "Some Shoot"
    media.mkdir(parents=True)
    (media / "raw-clip.mp4").write_bytes(b"\x00" * 64)
    (media / "notes.md").write_text("# loose prose\n\nShip the deck by Friday.\n")
    t.box.run("sort")
    t.box.run("index")
    t.ok((media / "raw-clip.mp4").exists() and (media / "notes.md").exists(),
         "nothing in it moved")
    t.ok(not any("Content" in i["path"] for i in t.box.items()), "nothing in it is an item")
    t.ok("not filed" not in t.box.run().stdout, "and nothing in it is nagged about")
    t.ok(not (t.box.root / "Notes" / "loose-prose.md").exists(), "prose in it was not adopted")


@test
def test_saved_footage_goes_to_the_media_folder(t: Case) -> None:
    """AGENTS.md says big media lives in Work/Content, but `./os save` put 300 MB
    of video in Notes beside the prose."""
    clip = t.box.tmp / "GX010042.MP4"
    clip.write_bytes(b"\x00" * 1024)
    said = t.box.run("save", str(clip))
    t.ok((t.box.root / "Work" / "Content" / "GX010042.MP4").exists(), "footage goes to Work/Content")
    t.ok("leaves them alone" in said.stdout, "and it says why")
    t.ok(not list((t.box.root / "Notes").glob("gx010042*")), "not into Notes")
    big = t.box.tmp / "export.zip"
    big.write_bytes(b"\x00" * 2048)
    config = json.loads((t.box.root / ".os" / "config.json").read_text())
    config.setdefault("thresholds", {})["big_file_mb"] = 0
    (t.box.root / ".os" / "config.json").write_text(json.dumps(config))
    t.box.run("save", str(big))
    t.ok((t.box.root / "Work" / "Content" / "export.zip").exists(),
         "so does anything bigger than big_file_mb")


@test
def test_work_is_never_made_where_nothing_looks(t: Case) -> None:
    """`./os new work "Content"` was reported as started inside the media
    folder, and then show, find and close all said it did not exist."""
    t.box.run("new", "work", "Content")
    made = next((i for i in t.box.items() if i["title"] == "Content"), None)
    t.ok(made is not None, "the item can be seen")
    t.ok("Content" in t.box.run("show", "content").stdout, "and found by its name")
    (t.box.root / "Notes" / "shoot.md").write_text(
        "# Content\n\nDeadline Friday.\n\n- [ ] book the van\n- [ ] call the crew\n")
    t.box.run("sort")
    filed = [i for i in t.box.items() if i["title"] == "Content"]
    t.eq(len(filed), 2, "sort files work called Content where it can be seen too")


@test
def test_an_empty_file_seconds_old_waits_for_the_next_sort(t: Case) -> None:
    """A sort adopted the shell of a note another session was still typing,
    and filed it twice (snag, 2026-08-31). Empty and seconds old means wait;
    anything with words in it is filed at once, however new."""
    fresh = t.box.root / "Notes" / "half-written.md"
    fresh.write_text("# Half written\n\n")
    out = t.box.run("sort")
    t.ok("left Notes/half-written.md for now" in out.stdout, "sort says it left it, and why")
    t.ok(fresh.exists(), "the file is where it was")
    t.ok(not fresh.read_text().startswith("---"), "and has not been given a header")

    fresh.write_text("# Half written\n\nThe first line of something worth keeping.\n")
    t.box.run("sort")
    t.box.run("index")
    t.ok(any("half-written" in i["path"] for i in t.box.items()),
         "with words in it, it is filed straight away")

    shell = t.box.root / "Notes" / "old-shell.md"
    shell.write_text("# Old shell\n")
    old = time.time() - 120
    os.utime(shell, (old, old))
    t.box.run("sort")
    t.box.run("index")
    t.ok(any("old-shell" in i["path"] for i in t.box.items()),
         "an empty file that has sat there a while is filed like anything else")


@test
def test_an_older_folder_upgrades_without_losing_anything(t: Case) -> None:
    """`.os/upgrade.py` brings the machinery up to date and keeps what is theirs.

    An old folder carries W.04_ names, `id:` headers, a skill of its own and a
    settings file with its own hook. After the upgrade the names are plain, the
    ids are gone, the skill and the hook are still there, and a backup holds
    every file that was replaced."""
    old = t.box.root
    work = old / "Work" / "W.01_ship-the-rewrite"
    work.mkdir(parents=True)
    (work / "README.md").write_text(
        "---\nid: W.01\ntitle: Ship the API rewrite\ntype: work\nstatus: pushing\n"
        "domain: engineering\ntags: []\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
        "# Ship the API rewrite\n\n## Next action\n- [ ] migrate\n")
    (old / "Notes" / "N.03_how-to-run-a-retro.md").write_text(
        "---\nid: N.03\ntitle: How to run a retro\ntype: note\ndomain: operations\n"
        "tags: []\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n\n# How to run a retro\n\nKeep it short.\n")
    mine = old / ".claude" / "skills" / "my-own-thing"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: my-own-thing\ndescription: Mine. Use when I say so.\n---\n\nDo it.\n")
    settings = old / ".claude" / "settings.json"
    conf = json.loads(settings.read_text())
    conf["hooks"]["UserPromptSubmit"] = [{"hooks": [{"type": "command", "command": "echo mine"}]}]
    settings.write_text(json.dumps(conf, indent=2))
    (old / "CLAUDE.md").write_text("@AGENTS.md\n\n## My rules\n\nAlways ask twice.\n")
    (old / ".os" / "engine.py").write_text("# an older engine\n")
    # A shipped skill they edited, and one they left alone.
    wrap = old / ".claude" / "skills" / "wrapup" / "SKILL.md"
    wrap.write_text(wrap.read_text() + "\nThen run my own to-do sync.\n")
    (old / ".claude" / "skills" / "find" / "SKILL.md").write_text("# an older find skill\n")
    # ...which the folder's own shipped list says was a released version, as
    # was its older engine. It was installed before today's wrapup existed,
    # and before releases had stamps: only such a folder has W.01_ names.
    import hashlib
    shipped = json.loads((old / ".os" / "shipped.json").read_text())
    shipped.pop("release", None)
    shipped["files"].setdefault(".claude/skills/find/SKILL.md", []).append(
        hashlib.sha1(b"# an older find skill\n").hexdigest())
    shipped["files"].setdefault(".os/engine.py", []).append(
        hashlib.sha1(b"# an older engine\n").hexdigest())
    today_wrap = hashlib.sha1((SOURCE / ".claude" / "skills" / "wrapup" / "SKILL.md").read_bytes()).hexdigest()
    shipped["files"][".claude/skills/wrapup/SKILL.md"] = [
        h for h in shipped["files"].get(".claude/skills/wrapup/SKILL.md", []) if h != today_wrap]
    (old / ".os" / "shipped.json").write_text(json.dumps(shipped))
    cfg = json.loads((old / ".os" / "config.json").read_text())
    cfg.pop("ignore", None); cfg["owner"] = "Sam"
    (old / ".os" / "config.json").write_text(json.dumps(cfg, indent=2))

    preview = subprocess.run([sys.executable, str(SOURCE / ".os" / "upgrade.py"), str(old), "--dry-run"],
                             capture_output=True, text=True)
    t.eq(preview.returncode, 0, "a dry run succeeds")
    t.ok("would" in preview.stdout and work.exists(), "and changes nothing")
    t.ok("# an older engine" in (old / ".os" / "engine.py").read_text(), "not even the engine")

    proc = subprocess.run([sys.executable, str(SOURCE / ".os" / "upgrade.py"), str(old)],
                          capture_output=True, text=True)
    t.eq(proc.returncode, 0, f"the upgrade succeeds\n{proc.stdout}\n{proc.stderr}")
    t.ok("# an older engine" not in (old / ".os" / "engine.py").read_text(), "the engine is the new one")
    t.ok((old / "Work" / "Ship the API Rewrite").exists(), "the work folder is its plain Title Case name")
    t.ok(not work.exists(), "and the W.01_ one is gone")
    t.ok((old / "Notes" / "how-to-run-a-retro.md").exists(), "the note is its plain name")
    readme = (old / "Work" / "Ship the API Rewrite" / "README.md").read_text()
    t.ok("id:" not in readme.split("---")[1], "the id line is out of the header")
    t.ok("Ship the API rewrite" in readme, "and the title is intact")
    t.ok((mine / "SKILL.md").exists(), "their own skill is untouched")
    t.ok("my own to-do sync" in wrap.read_text(), "a shipped skill they edited is kept as theirs")
    aside = list((old / ".os" / "upgrades").glob("*/.claude/skills/wrapup/SKILL.md"))
    t.eq(len(aside), 1, "and the new version is set aside for them")
    t.ok("my own to-do sync" not in aside[0].read_text(), "as the template's copy")
    t.ok("# an older find skill" not in (old / ".claude" / "skills" / "find" / "SKILL.md").read_text(),
         "a shipped skill still as released is replaced")
    t.ok("edited it" in proc.stdout and "wrapup" in proc.stdout, "and the upgrade says which was which")
    conf = json.loads(settings.read_text())
    t.ok(any("echo mine" in json.dumps(g) for g in conf["hooks"]["UserPromptSubmit"]), "their own hook is kept")
    t.ok(any("session-start.sh" in json.dumps(g) for g in conf["hooks"]["SessionStart"]), "the template hooks are there")
    cfg = json.loads((old / ".os" / "config.json").read_text())
    t.eq(cfg.get("owner"), "Sam", "their settings win")
    t.ok("ignore" in cfg, "new settings are added")
    t.ok("Always ask twice" in (old / "CLAUDE.md").read_text(), "their CLAUDE.md rules are kept")
    backups = list((old / ".os" / "backups").glob("before-upgrade-*"))
    t.eq(len(backups), 1, "one backup was made")
    t.ok("# an older engine" in (backups[0] / ".os" / "engine.py").read_text(), "holding the old engine")
    t.box.run("check")
    t.ok("Work/Ship the API Rewrite" in t.box.run("show", "ship-the-api-rewrite").stdout,
         "and the new engine finds things by name")


def _release(where: Path, stamp: str) -> None:
    """Record `where` as a published version, the way release-os.sh does."""
    subprocess.run([sys.executable, str(where / ".os" / "upgrade.py"), "--record", f"--release={stamp}"],
                   capture_output=True, text=True, check=True)


def _publish(t: Case, stamp: str, change=None, base: Path | None = None) -> Path:
    """A fresh download of the next version: this folder's program with no
    work in it, a little newer, changed by `change`, recorded as `stamp`."""
    out = t.box.tmp / f"published-{stamp}"
    shutil.copytree(base or t.box.root, out, ignore=shutil.ignore_patterns(
        "Work", "Notes", "Archive", "backups", "upgrades", "cache", "registry.json", "INDEX.md"))
    program = out / ".os" / "engine.py"
    program.write_text(program.read_text() + f"\n# {stamp}\n")
    if change:
        change(out)
    _release(out, stamp)
    return out


def _edit_json(path: Path, change) -> None:
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _release_of(root: Path) -> str:
    return json.loads((root / ".os" / "shipped.json").read_text()).get("release", "")


@test
def test_update_renews_settings_and_keywords_nobody_changed(t: Case) -> None:
    """A settings file or a subject's keywords nobody changed take the new
    version whole. Ones they changed stay theirs, and `learned` is never
    touched. Before, every hook command, permission rule and keyword that
    shipped once was in every folder for good."""
    root = t.box.root
    _release(root, "2026-01-01.1")

    def change(out: Path) -> None:
        def settings(conf: dict) -> None:
            conf["permissions"]["allow"].append("Bash(./os *)")
            conf["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 90
        _edit_json(out / ".claude" / "settings.json", settings)

        def words(w: dict) -> None:
            w["domains"]["personal"]["keywords"].append("allotment")
            w["domains"]["engineering"]["keywords"].append("zig")
        _edit_json(out / ".os" / "words.json", words)

    def change_again(out: Path) -> None:
        _edit_json(out / ".claude" / "settings.json", lambda conf: conf["hooks"].setdefault(
            "Notification", []).append({"hooks": [{"type": "command",
                                                    "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/notify.sh"}]}))
        _edit_json(out / ".claude" / "settings.json",
                   lambda conf: conf["permissions"]["allow"].append("Bash(./os find:*)"))

    published = _publish(t, "2026-02-01.1", change)
    newer = _publish(t, "2026-03-01.1", change_again, base=published)

    def theirs(w: dict) -> None:
        w["domains"]["engineering"]["keywords"].append("my-own-word")
        w["domains"]["personal"]["learned"] = ["sourdough"]
    _edit_json(root / ".os" / "words.json", theirs)

    settings = root / ".claude" / "settings.json"
    done = t.box.run("update", "--from", str(published))
    t.eq(settings.read_text(), (published / ".claude" / "settings.json").read_text(),
         f"a settings file nobody changed is the new one, whole\n{done.stdout}")
    words = json.loads((root / ".os" / "words.json").read_text())["domains"]
    t.ok("allotment" in words["personal"]["keywords"], "a keyword list nobody changed gets the new words")
    t.eq(words["personal"].get("learned"), ["sourdough"], "and what was learned stays as it was")
    t.ok("my-own-word" in words["engineering"]["keywords"] and "zig" not in words["engineering"]["keywords"],
         "a keyword list they changed stays theirs")

    _edit_json(settings, lambda conf: conf["hooks"].setdefault("UserPromptSubmit", []).append(
        {"hooks": [{"type": "command", "command": "echo mine"}]}))
    # How an older release wrote a hook: unquoted, it fails in a folder whose
    # path has a space in it. Merged, theirs was kept, and broke every session.
    _edit_json(settings, lambda conf: conf["hooks"]["SessionStart"][0]["hooks"][0].update(
        command="${CLAUDE_PROJECT_DIR}/.claude/hooks/session-start.sh"))
    ignore = root / ".gitignore"
    ignore.write_text("my-private-stuff/\n.DS_Store\n")
    t.box.run("update", "--from", str(newer))
    conf = json.loads(settings.read_text())
    t.ok("echo mine" in json.dumps(conf["hooks"].get("UserPromptSubmit")),
         "a settings file they changed keeps what they put in")
    t.ok("notify.sh" in json.dumps(conf["hooks"].get("Notification")), "and takes the new hook beside it")
    t.eq(conf["hooks"]["SessionStart"], json.loads((newer / ".claude" / "settings.json").read_text())
         ["hooks"]["SessionStart"], "a hook still as an older release wrote it takes the new one")
    t.ok("Bash(./os find:*)" in conf["permissions"]["allow"], "and a new permission rule is added")
    kept = ignore.read_text()
    t.ok("my-private-stuff/" in kept and ".os/upgrades/" in kept,
         f".gitignore keeps their lines and takes the new ones\n{kept}")
    (newer / ".gitignore").write_text((newer / ".gitignore").read_text() + "*.bak\n")
    _release(newer, "2026-03-01.1")
    shutil.copy2(newer / ".gitignore", ignore)
    newest = _publish(t, "2026-04-01.1", lambda out: (out / ".gitignore").write_text(
        "# only the new one\n.os/upgrades/\n.os/backups/\n"), base=newer)
    t.box.run("update", "--from", str(newest))
    t.eq(ignore.read_text(), (newest / ".gitignore").read_text(), "one still as released is replaced whole")


@test
def test_update_leaves_a_settings_file_with_a_typo_alone(t: Case) -> None:
    """A trailing comma made the update read their settings as empty and write
    the template's over them — their permissions and hooks gone, and the
    update calling it a merge."""
    root = t.box.root
    _release(root, "2026-01-01.1")
    published = _publish(t, "2026-02-01.1", lambda out: _edit_json(
        out / ".claude" / "settings.json", lambda c: c["hooks"]["Stop"][0]["hooks"][0].update(timeout=90)))
    settings = root / ".claude" / "settings.json"
    conf = json.loads(settings.read_text())
    conf["permissions"]["allow"].append("Bash(git status)")
    broken = json.dumps(conf, indent=2).replace('"Bash(git status)"', '"Bash(git status)",')
    settings.write_text(broken)

    done = t.box.run("update", "--from", str(published))
    t.eq(settings.read_text(), broken, f"their settings file is exactly as it was\n{done.stdout}")
    t.ok("typo" in done.stdout, "the update says why nothing was merged")
    aside = list((root / ".os" / "upgrades").glob("*/.claude/settings.json"))
    t.ok(aside and "hooks" in json.loads(aside[0].read_text()), "and puts the new one beside it")

    import upgrade
    words = t.box.tmp / "words.json"
    words.write_text('{"domains": {"personal": {"keywords": ["garden",]}}}')
    t.ok(upgrade.merge_json(words, root / ".os" / "words.json") is None
         and words.read_text().endswith('["garden",]}}}'), "a words file with a typo is not written over either")


@test
def test_update_leaves_their_own_files_as_they_are(t: Case) -> None:
    """Every update ran ./os sort, which renamed and moved anything dropped in
    by hand, and took '1.10 ' off the front of names anywhere in the folders."""
    root = t.box.root
    _release(root, "2026-01-01.1")
    published = _publish(t, "2026-02-01.1")
    t.box.run("new", "work", "Launch the bakery stall")
    item = next(p for p in (root / "Work").iterdir() if p.is_dir())
    dated = item / "1.10 meeting with Sam.md"
    dated.write_text("---\nid: sam-1\ntitle: Meeting with Sam\n---\n\nPrices for the stall.\n")
    dropped = root / "Notes" / "My Draft (do not touch).md"
    dropped.write_text("# My draft\n\nHalf a chapter about the stall.\n")

    done = t.box.run("update", "--from", str(published))
    t.ok(dated.exists() and "id: sam-1" in dated.read_text(),
         f"a name starting with a number keeps it, and its header\n{done.stdout}")
    t.ok(dropped.exists() and dropped.read_text().startswith("# My draft"),
         "a file they dropped in is not moved or given a header")
    t.ok("waiting" in done.stdout and "./os sort" in done.stdout, "the update says it is waiting to be filed")


@test
def test_update_keeps_what_they_deleted_deleted(t: Case) -> None:
    """A shipped skill they deleted, or a hook they took out, came back on
    every update — the snag decisions.md says must not happen."""
    root = t.box.root
    _release(root, "2026-01-01.1")

    def change(out: Path) -> None:
        handoff = out / ".claude" / "skills" / "handoff" / "SKILL.md"
        handoff.write_text(handoff.read_text() + "\nA newer handoff step.\n")
        _edit_json(out / ".claude" / "settings.json",
                   lambda c: c["hooks"]["SessionStart"][0]["hooks"][0].update(timeout=20))
    published = _publish(t, "2026-02-01.1", change)
    shutil.rmtree(root / ".claude" / "skills" / "handoff")
    settings = root / ".claude" / "settings.json"
    _edit_json(settings, lambda c: (c["hooks"].pop("Stop"), c["hooks"].pop("PostToolUse")))
    hooks = root / ".claude" / "hooks"
    (hooks / "mark-dirty.sh").unlink()          # taken out of settings.json too: on purpose
    # Gone by accident: AGENTS.md, and a hook script settings.json still runs,
    # which then failed at every start while check said all was well.
    (root / "AGENTS.md").unlink()
    (hooks / "session-start.sh").unlink()

    done = t.box.run("update", "--from", str(published))
    t.ok(not (root / ".claude" / "skills" / "handoff").exists(), f"a skill they deleted stays deleted\n{done.stdout}")
    t.ok("handoff" in done.stdout and "deleted" in done.stdout, "and the update says so")
    t.ok("settle.sh" not in settings.read_text(), "a hook they took out stays out")
    t.ok(not (hooks / "mark-dirty.sh").exists(), "and so does its script")
    t.ok((root / "AGENTS.md").is_file(), "AGENTS.md comes back: nothing works without it")
    t.ok((hooks / "session-start.sh").is_file() and os.access(hooks / "session-start.sh", os.X_OK),
         "a hook script settings.json still runs comes back")


@test
def test_update_sets_aside_only_what_is_new_to_them(t: Case) -> None:
    """A skill they changed was set aside again on every update, even when the
    template hadn't changed it. And a new template skill sharing a name with
    one of theirs was called theirs, edited, and queued to be merged in."""
    root = t.box.root
    _release(root, "2026-01-01.1")

    def weekly(words: str):
        def change(out: Path) -> None:
            up = out / ".os" / "upgrade.py"
            up.write_text(up.read_text().replace('"tidy", "wrapup")', '"tidy", "wrapup", "weekly")', 1))
            skill = out / ".claude" / "skills" / "weekly" / "SKILL.md"
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text(f"---\nname: weekly\ndescription: The template's weekly review. {words}\n---\n\n"
                             "Review the week.\n")
        return change
    published = _publish(t, "2026-02-01.1", weekly("Use on Fridays."))
    newer = _publish(t, "2026-03-01.1", weekly("Use on Mondays."), base=published)

    wrap = root / ".claude" / "skills" / "wrapup" / "SKILL.md"
    wrap.write_text(wrap.read_text() + "\nThen my own to-do sync.\n")
    mine = root / ".claude" / "skills" / "weekly" / "SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("---\nname: weekly\ndescription: My Friday invoices run. Use on Fridays.\n---\n\n"
                    "Send the invoices.\n")

    done = t.box.run("update", "--from", str(published))
    upgrades = root / ".os" / "upgrades"
    t.ok(not list(upgrades.glob("*/.claude/skills/wrapup/SKILL.md")),
         f"a skill they changed and the template didn't is not set aside\n{done.stdout}")
    t.ok("Send the invoices" in mine.read_text(), "their own weekly skill is untouched")
    t.ok(list(upgrades.glob("*/.claude/skills/weekly/SKILL.md")), "the template's is put beside it")
    t.ok("same name" in done.stdout and "edited it" not in done.stdout and "merge:" not in done.stdout,
         "and it is not called theirs to merge")
    again = t.box.run("update", "--from", str(newer))
    t.ok("Send the invoices" in mine.read_text(), "on the next update theirs is still untouched")
    t.ok("same name" in again.stdout and "edited it" not in again.stdout,
         f"and still not called theirs to merge\n{again.stdout}")
    left = t.box.json("check")["issues"]
    t.ok(not any(i["code"] == "update-to-merge" for i in left), "nothing is waiting to be merged")


@test
def test_update_knows_every_file_a_skill_ships_with(t: Case) -> None:
    """Only SKILL.md and three other names were recorded. Any other file in a
    skill, changed by the template, was called theirs, "a different thing,
    don't merge them", and kept at the old version for good."""
    import upgrade
    root = t.box.root
    name = next((s for s in upgrade.SHIPPED_SKILLS
                 if (root / ".claude" / "skills" / s / "SKILL.md").is_file()), "")
    if not name:
        return
    extra = root / ".claude" / "skills" / name / "checklist.md"
    extra.write_text("- look at held work\n")
    _release(root, "2026-01-01.1")
    published = _publish(t, "2026-02-01.1", lambda out: (
        out / ".claude" / "skills" / name / "checklist.md").write_text("- look at held work\n- and old notes\n"))
    done = t.box.run("update", "--from", str(published))
    t.ok("and old notes" in extra.read_text(), f"a file nobody touched takes the new version\n{done.stdout}")
    t.ok("same name" not in done.stdout, "and isn't called theirs")


@test
def test_update_refuses_a_folder_somebody_has_used(t: Case) -> None:
    """`--from` took any folder. Pointed at a lived-in one, its hand-edited
    skills, words and hooks came in as if they were the template's."""
    root = t.box.root
    _release(root, "2026-01-01.1")
    used = _publish(t, "2026-02-01.1")
    save = used / ".claude" / "skills" / "save" / "SKILL.md"
    save.write_text(save.read_text() + "\nAlways ask me which folder first.\n")
    refused = t.box.run("update", "--from", str(used), expect=1)
    t.ok("someone's folder" in refused.stderr, f"it says what that folder is\n{refused.stderr}")
    t.ok("Always ask me" not in (root / ".claude" / "skills" / "save" / "SKILL.md").read_text(),
         "nothing of theirs came in")
    t.eq(list((root / ".os" / "backups").glob("before-upgrade-*")), [], "and nothing here was touched")

    lived = _publish(t, "2026-02-01.2")
    (lived / "Notes").mkdir()
    (lived / "Notes" / "sourdough.md").write_text("# Sourdough\n\n500g flour.\n")
    t.ok("someone's folder" in t.box.run("update", "--from", str(lived), expect=1).stderr,
         "a folder with notes in it is somebody's too")


@test
def test_upgrade_run_by_hand_keeps_a_program_changed_here(t: Case) -> None:
    """A folder from before ./os update takes `python3 <download>/.os/upgrade.py
    <folder>` once. That replaced a program changed there and never published
    without a word: the refusal lived only in ./os update."""
    root = t.box.root
    _release(root, "2026-01-01.1")
    published = _publish(t, "2026-02-01.1")
    engine_here = root / ".os" / "engine.py"
    engine_here.write_text(engine_here.read_text() + "\n# my own fix\n")
    by_hand = [sys.executable, str(published / ".os" / "upgrade.py"), str(root)]

    preview = subprocess.run(by_hand + ["--dry-run"], capture_output=True, text=True)
    t.eq(preview.returncode, 0, f"a dry run goes through\n{preview.stderr}")
    t.ok("engine.py" in preview.stdout and "--anyway" in preview.stdout, "and says it would stop there")
    refused = subprocess.run(by_hand, capture_output=True, text=True)
    t.ok(refused.returncode != 0 and "engine.py" in refused.stderr and "--anyway" in refused.stderr,
         f"it names the changed file and the way through\n{refused.stdout}\n{refused.stderr}")
    t.ok(str(published / ".os" / "upgrade.py") in refused.stderr, "as a command to type")
    t.ok("# my own fix" in engine_here.read_text(), "and leaves it alone")
    went = subprocess.run(by_hand + ["--anyway"], capture_output=True, text=True)
    t.eq(went.returncode, 0, f"--anyway goes ahead\n{went.stderr}")
    t.ok("# 2026-02-01.1" in engine_here.read_text(), "with the new program")


@test
def test_an_update_that_stops_partway_can_be_finished(t: Case) -> None:
    """The folder said it had the new version before any skill was copied, so
    an update that stopped partway was never finished: the next one said
    "already has the newest version"."""
    import upgrade
    root = t.box.root
    # Two shipped skills they still have: one that can't be written, and one
    # they edited, whose new version is set aside for them.
    have = [s for s in upgrade.SHIPPED_SKILLS if (root / ".claude" / "skills" / s / "SKILL.md").is_file()]
    if len(have) < 2:
        return
    locked, edited = have[-1], have[0]
    _release(root, "2026-01-01.1")

    def change(out: Path) -> None:
        for s, line in ((locked, "\nCheck held work once a month.\n"), (edited, "\nA newer step.\n")):
            skill = out / ".claude" / "skills" / s / "SKILL.md"
            skill.write_text(skill.read_text() + line)
    published = _publish(t, "2026-02-01.1", change)
    mine = root / ".claude" / "skills" / edited / "SKILL.md"
    mine.write_text(mine.read_text() + "\nMy own step.\n")
    folder = root / ".claude" / "skills" / locked
    skill = folder / "SKILL.md"
    skill.chmod(0o444)
    folder.chmod(0o555)
    try:
        if os.access(folder, os.W_OK):      # run as root: nothing can be made unwritable
            return
        stopped = t.box.run("update", "--from", str(published), expect=None)
    finally:
        folder.chmod(0o755)
        skill.chmod(0o644)
    t.ok(stopped.returncode != 0 and locked in stopped.stdout + stopped.stderr,
         f"it stops, and says what it couldn't write\n{stopped.stdout}\n{stopped.stderr}")
    t.eq(_release_of(root), "2026-01-01.1", "the folder doesn't claim a version it didn't finish")
    for kept in (root / ".os" / "backups").rglob(locked):
        kept.chmod(0o755)        # the backup copied the locked folder as it was
    t.box.run("update", "--from", str(published))
    t.ok("once a month" in skill.read_text(), "running it again finishes it")
    t.eq(_release_of(root), "2026-02-01.1", "and then it has the new version")
    aside = list((root / ".os" / "upgrades").glob(f"*/.claude/skills/{edited}/SKILL.md"))
    t.eq(len(aside), 1, "and what it set aside the first time isn't set aside again")


@test
def test_an_empty_engine_says_so(t: Case) -> None:
    """Killed while engine.py was being replaced, an update could leave it
    empty. Then ./os, ./os update and the session hook printed nothing and
    said all was well."""
    root = t.box.root
    kept = root / ".os" / "backups" / "before-upgrade-2026-01-01-120000" / ".os"
    kept.mkdir(parents=True)
    shutil.copy2(root / ".os" / "engine.py", kept / "engine.py")
    (root / ".os" / "engine.py").write_text("")
    said = t.box.run(expect=1)
    t.ok("before-upgrade-2026-01-01-120000" in said.stderr, f"it says where a good copy is\n{said.stderr}")


@test
def test_files_left_to_merge_are_mentioned_until_merged(t: Case) -> None:
    """The files an update set aside to merge were named once, in its own
    output. If the chat ended first, nothing ever mentioned them again."""
    root = t.box.root
    _release(root, "2026-01-01.1")
    published = _publish(t, "2026-02-01.1", lambda out: (out / ".claude" / "skills" / "wrapup" / "SKILL.md").write_text(
        (out / ".claude" / "skills" / "wrapup" / "SKILL.md").read_text() + "\nA newer wrapup step.\n"))
    t.box.run("new", "work", "Ship the rewrite")
    wrap = root / ".claude" / "skills" / "wrapup" / "SKILL.md"
    wrap.write_text(wrap.read_text() + "\nThen my own to-do sync.\n")
    t.box.run("update", "--from", str(published))

    t.ok(".os/upgrades/" in t.box.run().stdout, "./os says there is something to merge")
    t.ok(any(i["code"] == "update-to-merge" for i in t.box.json("check")["issues"]), "so does ./os check")
    t.ok(".os/upgrades/" in t.box.run("brief").stdout, "and so does what the AI is told first")
    shutil.rmtree(root / ".os" / "upgrades")
    t.ok(".os/upgrades/" not in t.box.run().stdout, "once merged and cleared away, nothing more is said")


@test
def test_update_check_says_when_a_newer_version_is_out(t: Case) -> None:
    """Nothing ever told a folder a newer version was out, so a folder
    downloaded on day one kept day-one bugs until somebody typed ./os update."""
    root = t.box.root
    _release(root, "2026-01-01.1")
    published = _publish(t, "2026-02-01.1")
    said = t.box.run("update", "--check", "--from", str(published))
    t.ok("2026-02-01.1" in said.stdout and "./os update" in said.stdout, f"it says so\n{said.stdout}")
    t.eq(_release_of(root), "2026-01-01.1", "and changes nothing")
    t.box.run("update", "--from", str(published))
    t.eq(t.box.run("update", "--check", "--from", str(published)).stdout.strip(), "",
         "nothing is said when this folder has the newest")
    t.eq(t.box.run("update", "--check", "--from", str(t.box.tmp / "nowhere")).stdout.strip(), "",
         "or when it can't find out")


CHANGES_THEN = """# What's new

## 2026-02-01.1

- Bread recipes are kept as notes now.
- Search reads TextEdit notes.

## 2026-01-01.1

- An older change they already have.
"""


@test
def test_an_update_says_in_words_what_changed(t: Case) -> None:
    """An update listed the files it replaced and never said what anybody
    would notice. It prints the change note's entries newer than the version
    the folder had, even from a version that had no change note at all."""
    root = t.box.root
    (root / ".os" / "CHANGES.md").unlink(missing_ok=True)     # 2026-09-30.1 had none
    _release(root, "2026-01-01.1")
    published = _publish(t, "2026-02-01.1",
                         lambda out: (out / ".os" / "CHANGES.md").write_text(CHANGES_THEN))
    preview = t.box.run("update", "--from", str(published), "--dry-run").stdout
    t.ok("WHAT'S NEW" in preview and "Bread recipes are kept as notes" in preview,
         f"a preview says what's new\n{preview}")
    done = t.box.run("update", "--from", str(published)).stdout
    t.ok("WHAT'S NEW since 2026-01-01.1" in done, f"the update says what's new since their version\n{done}")
    t.ok("2026-02-01.1" in done and "Bread recipes are kept as notes" in done
         and "Search reads TextEdit notes" in done, "every line of the new entry")
    t.ok("older change they already have" not in done, "and not the ones they already had")
    t.eq((root / ".os" / "CHANGES.md").read_text(), CHANGES_THEN, "the change note comes with the update")
    again = t.box.run("update", "--from", str(published)).stdout
    t.ok("WHAT'S NEW" not in again, "nothing new, nothing said")


@test
def test_the_change_note_is_replaced_not_kept_as_theirs(t: Case) -> None:
    """The change note is the template's, not theirs: an update replaces it
    even when they wrote in it, and sets nothing aside to merge."""
    root = t.box.root
    (root / ".os" / "CHANGES.md").write_text(CHANGES_THEN)
    _release(root, "2026-02-01.1")
    with open(root / ".os" / "CHANGES.md", "a") as note:
        note.write("\nMy own line.\n")
    newer = "# What's new\n\n## 2026-03-01.1\n\n- Held work stops nagging.\n\n" + CHANGES_THEN.split("\n", 2)[2]
    published = _publish(t, "2026-03-01.1", lambda out: (out / ".os" / "CHANGES.md").write_text(newer))
    done = t.box.run("update", "--from", str(published)).stdout
    t.eq((root / ".os" / "CHANGES.md").read_text(), newer, "it's replaced")
    t.ok("Held work stops nagging" in done and "Bread recipes" not in done,
         f"and only what's newer than their version is said\n{done}")
    t.eq(list((root / ".os" / "upgrades").rglob("CHANGES.md")), [], "nothing is set aside to merge")


@test
def test_the_change_note_is_read_by_release(t: Case) -> None:
    """Which entries are new to a folder, by the release it has."""
    text = ("# What's new\n\n## Next release\n\n- Coming.\n\n## 2026-02-01.2\n\n- Two.\n\n"
            "## 2026-02-01.1\n\n- One.\n\n## Not a release\n\n- Skipped.\n")
    heads = lambda got: [h for h, _ in got]
    t.eq(heads(engine.change_entries(text)), ["Next release", "2026-02-01.2", "2026-02-01.1"],
         "only releases, and the entry being written")
    t.eq(heads(engine.changes_since(text, "2026-02-01.1")), ["Next release", "2026-02-01.2"],
         "newer than the folder's release")
    t.eq(heads(engine.changes_since(text, "2026-02-01.2")), ["Next release"], "the one being written always")
    t.eq(len(engine.changes_since(text, "")), 3, "everything, for a folder from before releases")
    t.eq(engine.change_entries(text)[1][1], "- Two.", "with what it says")


@test
def test_no_release_without_a_new_change_note(t: Case) -> None:
    """The release script asks `upgrade.py --change-note` for the new entry
    before it builds anything, and stops without one."""
    root = t.box.root
    note = root / ".os" / "CHANGES.md"
    published = t.box.tmp / "published-CHANGES.md"
    published.write_text(CHANGES_THEN)

    def ask(*more: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(root / ".os" / "upgrade.py"), "--change-note", *more],
                              capture_output=True, text=True)

    note.write_text("# What's new\n\n## Next release\n\n- Held work stops nagging.\n\n"
                    + CHANGES_THEN.split("\n", 2)[2])
    got = ask(f"--published={published}")
    t.eq((got.returncode, got.stdout.strip()), (0, "- Held work stops nagging."), "a new entry is handed over")
    note.write_text(CHANGES_THEN)
    got = ask(f"--published={published}")
    t.ok(got.returncode != 0 and "Next release" in got.stderr, f"no new entry stops it\n{got.stderr}")
    note.write_text("# What's new\n\n## Next release\n\n" + CHANGES_THEN.split("\n", 2)[2])
    got = ask()
    t.ok(got.returncode != 0 and "nothing in it" in got.stderr, f"so does an empty one\n{got.stderr}")
    note.write_text(CHANGES_THEN.replace("## 2026-02-01.1", "## Next release"))
    got = ask(f"--published={published}")
    t.ok(got.returncode != 0 and "already out, as 2026-02-01.1" in got.stderr,
         f"and one already published under its real name\n{got.stderr}")
    note.unlink()
    got = ask()
    t.ok(got.returncode != 0 and "CHANGES.md" in got.stderr, f"and no change note at all\n{got.stderr}")


@test
def test_the_shipped_change_note_reads_right(t: Case) -> None:
    """Every entry has lines to read, the newest is on top, and only the top
    one can be the one still being written."""
    text = (SOURCE / ".os" / "CHANGES.md").read_text(encoding="utf-8")
    entries = engine.change_entries(text)
    t.ok(entries, "it has entries")
    for head, body in entries:
        t.ok(re.search(r"^- +\S", body, re.M), f"{head} says something")
    t.ok(all(h != engine.UNRELEASED for h, _ in entries[1:]), "only the top one is unreleased")
    stamps = [engine._release_key(h) for h, _ in entries if h != engine.UNRELEASED]
    t.eq(stamps, sorted(stamps, reverse=True), "newest first")


@test
def test_agents_md_says_to_look_for_a_newer_version_first(t: Case) -> None:
    """Nothing told the AI to run ./os update --check, so nobody heard a
    newer version was out unless they asked."""
    text = (t.box.root / "AGENTS.md").read_text(encoding="utf-8")
    first = text.split("## First, always", 1)[1].split("\n## ", 1)[0]
    t.ok("./os update --check" in first, "it's under 'First, always'")
    t.eq(t.box.run("update", "--check", "--from", str(t.box.tmp / "nowhere")).stdout.strip(), "",
         "and says nothing when it can't find out")


@test
def test_update_says_its_title_first(t: Case) -> None:
    """Read through a pipe, as an AI reads it, the upgrade's whole report came
    before the update's own title, and an error before the title it belongs to."""
    root = t.box.root
    _release(root, "2026-01-01.1")
    published = _publish(t, "2026-02-01.1")
    older = _publish(t, "2025-12-01.1")
    env = dict(os.environ, ZENITH_HOME=str(root), NO_COLOR="1")

    def both(*args: str) -> str:
        return subprocess.run([str(root / "os"), *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, cwd=str(root), env=env).stdout

    text = both("update", "--from", str(published), "--dry-run")
    t.ok(0 <= text.find("UPDATE") < text.find("UPGRADE"), f"the update's title comes first\n{text}")
    text = both("update", "--from", str(older))
    t.ok(0 <= text.find("UPDATE") < text.find("newer than the published"), f"and an error comes under it\n{text}")


@test
def test_a_release_of_only_words_and_settings_still_arrives(t: Case) -> None:
    """A release can change only .os/words.json or .claude/settings.json.

    No program file changed, so ./os update said the folder already had the
    newest version, the stamp stayed old, and release-os.sh then refused to
    publish it."""
    root = t.box.root
    record = lambda where, *more: subprocess.run(
        [sys.executable, str(where / ".os" / "upgrade.py"), "--record", *more],
        capture_output=True, text=True, check=True)
    record(root, "--release=2026-01-01.1")
    published = t.box.tmp / "published"
    shutil.copytree(root, published, ignore=shutil.ignore_patterns("Work", "Notes", "Archive", "backups"))
    words = json.loads((published / ".os" / "words.json").read_text())
    words["domains"]["cooking"] = {"label": "Cooking", "keywords": ["sourdough", "recipe"], "extensions": []}
    (published / ".os" / "words.json").write_text(json.dumps(words, indent=2))
    settings = json.loads((published / ".claude" / "settings.json").read_text())
    settings["hooks"]["UserPromptSubmit"] = [{"hooks": [
        {"type": "command", "command": 'bash "${CLAUDE_PROJECT_DIR}/.claude/hooks/new-one.sh"'}]}]
    (published / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2))
    record(published, "--release=2026-02-01.1")

    done = t.box.run("update", "--from", str(published))
    t.ok("already has the newest" not in done.stdout, f"a newer release is not waved through\n{done.stdout}")
    t.eq(json.loads((root / ".os" / "shipped.json").read_text()).get("release"), "2026-02-01.1",
         "the folder says it has the new release")
    t.ok("cooking" in json.loads((root / ".os" / "words.json").read_text())["domains"],
         "the new subject arrives")
    mine = (root / ".claude" / "settings.json").read_text()
    t.ok("new-one.sh" in mine, "and so does the new hook")
    for name in ("session-start.sh", "mark-dirty.sh", "settle.sh"):
        t.eq(mine.count(name), 1, f"{name} is still there once, not added again")
    again = t.box.run("update", "--from", str(published))
    t.ok("already has the newest" in again.stdout, "the same release twice is nothing to do")


@test
def test_update_uses_the_copy_it_is_pointed_at(t: Case) -> None:
    """`./os update ~/Downloads/os-template` dropped the path without a word
    and went to GitHub instead: a 404 before the first release, and after it
    another version than the one they pointed at (review, 2026-09-29)."""
    # Recorded as a release first: a copy whose files match no published
    # version is somebody's folder, and update refuses it for that instead.
    _release(t.box.root, "2026-01-01.1")
    copy = t.box.tmp / "my copy"
    shutil.copytree(t.box.root, copy,
                    ignore=shutil.ignore_patterns("Work", "Notes", "Archive", "backups"))
    same = t.box.run("update", str(copy))
    t.ok("already has the newest" in same.stdout,
         f"the copy named is the copy read\n{same.stdout}{same.stderr}")
    same = t.box.run("update", *str(copy).split(" "))
    t.ok("already has the newest" in same.stdout,
         f"even typed without quotes round its space\n{same.stdout}{same.stderr}")
    nowhere = t.box.run("update", str(t.box.tmp / "nowhere"), expect=1)
    t.ok("--from" in nowhere.stderr, f"a path with nothing at it is refused\n{nowhere.stderr}")


@test
def test_update_brings_the_newest_version_and_keeps_theirs(t: Case) -> None:
    """`./os update` fetches a newer published copy and runs its upgrade.

    What they wrote and what they changed stays theirs; the folder's name in
    AGENTS.md survives; nothing new means nothing done; and a program changed
    here and never published is not quietly replaced."""
    import hashlib
    root = t.box.root
    record = lambda where, *more: subprocess.run(
        [sys.executable, str(where / ".os" / "upgrade.py"), "--record", *more],
        capture_output=True, text=True, check=True)
    record(root, "--release=2026-01-01.1")         # this folder is a released version

    published = t.box.tmp / "published"
    shutil.copytree(root, published, ignore=shutil.ignore_patterns("Work", "Notes", "Archive", "backups"))
    (published / ".os" / "engine.py").write_text(
        (published / ".os" / "engine.py").read_text() + "\n# a newer engine\n")
    find = published / ".claude" / "skills" / "find" / "SKILL.md"
    find.write_text(find.read_text() + "\nA newer find step.\n")
    wrap_new = published / ".claude" / "skills" / "wrapup" / "SKILL.md"
    wrap_new.write_text(wrap_new.read_text() + "\nA newer wrapup step.\n")
    agents_new = published / "AGENTS.md"
    agents_new.write_text(agents_new.read_text() + "\nA newer rule.\n")
    record(published, "--release=2026-02-01.1")

    # theirs: a name, a piece of work, and a shipped skill they edited
    agents = root / "AGENTS.md"
    agents.write_text(re.sub(r"This folder is called [^\n]+?\. ", "This folder is called Sam's Place. ",
                             agents.read_text(), count=1))
    t.ok("Sam's Place" in agents.read_text(), "the folder carries its own name")
    t.box.run("new", "work", "Ship the rewrite")
    wrap = root / ".claude" / "skills" / "wrapup" / "SKILL.md"
    wrap.write_text(wrap.read_text() + "\nThen my own to-do sync.\n")

    preview = t.box.run("update", "--from", str(published), "--dry-run")
    t.ok("would" in preview.stdout, f"a dry run says what would happen\n{preview.stdout}")
    t.ok("# a newer engine" not in (root / ".os" / "engine.py").read_text(), "and changes nothing")

    # The way everyone gets it: GitHub's ZIP, with one folder inside.
    top = t.box.tmp / "zip" / "os-template-os"
    shutil.copytree(published, top, symlinks=True)
    archive = shutil.make_archive(str(t.box.tmp / "os"), "zip", root_dir=str(top.parent), base_dir=top.name)
    done = t.box.run("update", "--from", archive)
    t.ok("# a newer engine" in (root / ".os" / "engine.py").read_text(), "the engine is the new one")
    t.ok(os.access(root / "os", os.X_OK), "and ./os can still be run: unpacking the ZIP drops that")
    t.ok("A newer find step" in (root / ".claude" / "skills" / "find" / "SKILL.md").read_text(),
         "a shipped skill still as released is replaced")
    t.ok("my own to-do sync" in wrap.read_text(), "a shipped skill they edited is kept")
    aside = list((root / ".os" / "upgrades").glob("*/.claude/skills/wrapup/SKILL.md"))
    t.ok(aside and "A newer wrapup step" in aside[0].read_text(), "with the new one set aside")
    text = agents.read_text()
    t.ok("A newer rule" in text and "Sam's Place" in text,
         f"AGENTS.md is the new one and still says the folder's name\n{done.stdout}")
    t.ok(any("Ship the Rewrite" in i["path"] for i in t.box.items()), "their work is still there")
    t.eq(len(list((root / ".os" / "backups").glob("before-upgrade-*"))), 1, "a backup was made first")
    t.eq(json.loads((root / ".os" / "shipped.json").read_text()).get("release"), "2026-02-01.1",
         "the folder knows which version it has")

    again = t.box.run("update", "--from", str(published))
    t.ok("already has the newest" in again.stdout, "a second update finds nothing to do")
    t.eq(len(list((root / ".os" / "backups").glob("before-upgrade-*"))), 1, "and touches nothing")

    # a program changed here and never published is not replaced without asking
    (published / ".os" / "learn.py").write_text(
        (published / ".os" / "learn.py").read_text() + "\n# newer still\n")
    record(published, "--release=2026-03-01.1")
    engine_here = root / ".os" / "engine.py"
    engine_here.write_text(engine_here.read_text() + "\n# my own fix\n")
    refused = t.box.run("update", "--from", str(published), expect=1)
    t.ok("engine.py" in refused.stderr and "--anyway" in refused.stderr,
         f"it names the changed file and the way through\n{refused.stderr}")
    t.ok("# my own fix" in engine_here.read_text(), "and leaves it alone")
    t.box.run("update", "--from", str(published), "--anyway")
    t.ok("# newer still" in (root / ".os" / "learn.py").read_text(), "--anyway goes ahead")
    kept = list((root / ".os" / "backups").glob("before-upgrade-*/.os/engine.py"))
    t.ok(any("# my own fix" in k.read_text() for k in kept), "with their fix in the backup")

    # an older published copy never quietly replaces a newer folder
    older = t.box.tmp / "older"
    shutil.copytree(published, older)
    (older / ".os" / "learn.py").write_text("# an old learn\n")
    record(older, "--release=2025-12-01.1")
    back = t.box.run("update", "--from", str(older), expect=1)
    t.ok("newer than the published" in back.stderr, "going backwards takes --anyway")


def _run_one(name: str, keep: bool, verbose: bool) -> dict:
    """One test in one worker process. Returns only plain data — the report
    is printed by the parent, in order, so the output reads the same as -j 1."""
    global VERBOSE
    VERBOSE = False            # per-assertion chatter would interleave; keep the doc line
    fn = next(t for t in TESTS if t.__name__ == name)
    doc = (fn.__doc__ or "").strip().split("\n")[0]
    box = Sandbox()
    case = Case(box)
    t0 = time.time()
    out = {"ok": True, "checks": 0, "secs": 0.0, "error": "", "tb": "", "kept": "", "notes": []}
    try:
        fn(case)
        if verbose and doc:
            out["notes"].append(f"      {D}{doc}{X}")
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)
        out["tb"] = traceback.format_exc()
    finally:
        out["checks"] = case.checks
        out["secs"] = time.time() - t0
        if keep:
            out["kept"] = str(box.root)
        else:
            box.destroy()
    return out


def main(argv: list[str]) -> int:
    engine.speak_utf8()          # the report is box-drawing; cp1252 cannot say it
    global VERBOSE
    VERBOSE = "-v" in argv or "--verbose" in argv
    keep = "--keep" in argv
    only = ""
    if "-k" in argv:
        idx = argv.index("-k")
        only = argv[idx + 1] if idx + 1 < len(argv) else ""

    jobs = os.cpu_count() or 1
    if "-j" in argv:
        idx = argv.index("-j")
        try:
            jobs = max(1, int(argv[idx + 1]))
        except (IndexError, ValueError):
            jobs = 1
    selected = [t for t in TESTS if not only or only in t.__name__]
    print()
    print(f"  {B}Zenith — test suite{X}")
    print(f"  {D}engine {engine.ENGINE_VERSION} · python {sys.version.split()[0]} · "
          f"{len(selected)} tests{X}")
    print(f"  {D}{'─' * 64}{X}")

    passed, failed, checks = 0, [], 0
    started = time.time()

    # Every test owns its own sandbox, so they run one per core. Each runs in
    # its own process, not a thread: a few tests set CLAUDE_CODE_SESSION_ID in
    # os.environ, and Case.run copies the environment at call time.
    if jobs > 1 and len(selected) > 1:
        import concurrent.futures as cf
        ctx = multiprocessing.get_context("fork" if sys.platform != "win32" else "spawn")
        with cf.ProcessPoolExecutor(max_workers=jobs, mp_context=ctx) as pool:
            futures = [pool.submit(_run_one, fn.__name__, keep, VERBOSE) for fn in selected]
            for fn, fut in zip(selected, futures):
                label = fn.__name__.replace("test_", "").replace("_", " ")
                print(f"  {D}▸{X} {label:<44}", end="", flush=True)
                r = fut.result()
                checks += r["checks"]
                if r["ok"]:
                    passed += 1
                    print(f" {G}pass{X} {D}{r['checks']:>4} checks  {r['secs']:>5.1f}s{X}")
                    for line in r["notes"]:
                        print(line)
                else:
                    failed.append((fn.__name__, r["error"], r["tb"]))
                    print(f" {R}FAIL{X} {D}{r['checks']:>4} checks  {r['secs']:>5.1f}s{X}")
                if r["kept"]:
                    print(f"      {D}sandbox kept: {r['kept']}{X}")
    else:
      for fn in selected:
        label = fn.__name__.replace("test_", "").replace("_", " ")
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        print(f"  {D}▸{X} {label:<44}", end="", flush=True)
        box = Sandbox()
        case = Case(box)
        t0 = time.time()
        try:
            fn(case)
            checks += case.checks
            passed += 1
            print(f" {G}pass{X} {D}{case.checks:>4} checks  {time.time()-t0:>5.1f}s{X}")
            if VERBOSE and doc:
                print(f"      {D}{doc}{X}")
        except Exception as exc:
            checks += case.checks
            failed.append((fn.__name__, exc, traceback.format_exc()))
            print(f" {R}FAIL{X} {D}{case.checks:>4} checks  {time.time()-t0:>5.1f}s{X}")
        finally:
            if keep:
                print(f"      {D}sandbox kept: {box.root}{X}")
            else:
                box.destroy()

    elapsed = time.time() - started
    print(f"  {D}{'─' * 64}{X}")
    if failed:
        print()
        for name, exc, tb in failed:
            print(f"  {R}{B}FAILED{X} {B}{name}{X}")
            first = str(exc).split("\n")
            for line in first[:14]:
                print(f"    {R}{line}{X}")
            if VERBOSE:
                print(f"{D}{tb}{X}")
            print()
    verdict = f"{G}{passed}/{len(selected)} passed{X}" if not failed else \
              f"{R}{len(failed)} failed{X}, {passed} passed"
    print(f"  {verdict}  {D}· {checks} assertions · {elapsed:.1f}s{X}")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
