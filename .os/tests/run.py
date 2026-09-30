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
            (self.root / "Notes" / name).write_text(body, encoding="utf-8")
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
    essay = root / "Notes" / "Long essay.md"
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

    codes = {i["code"] for i in t.box.json("check", expect=1)["issues"]}
    for expected in ("duplicate-id", "skill-name-clash", "skill-no-description"):
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
    t.ok("domain: unsorted" in readme.read_text(), "nothing matched, so it is unsorted")
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
        (t.box.root / "Notes" / f"same-{i}.md").write_text(
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
         "outside git, a cache folder is still reported")
    if not shutil.which("git"):
        return          # the rest needs git; a computer without it isn't broken
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    t.ok(not any("__pycache__" in p for p in codes()),
         "once git ignores it, it is the project's own business")


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
    an error on the report, and both are still on disk afterwards."""
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
    codes = {i["code"] for i in t.box.json("check", expect=1)["issues"]}
    t.ok("duplicate-id" in codes, "the clash is reported as an error, not swallowed")

    # --fix repairs the mechanical; which of two things keeps a name is not that
    t.box.run("check", "--fix", expect=1)
    t.ok((clash / "README.md").exists(), "the copy is still there — nothing was deleted")
    t.ok(origin_spine.exists(), "and so is the original")
    codes = {i["code"] for i in t.box.json("check", expect=1)["issues"]}
    t.ok("duplicate-id" in codes, "--fix does not paper over a judgement call")

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
    party = t.box.root / "Notes" / "party-ideas.md"
    t.ok(party.exists(), "a loose file is named after its file name, not its first line")

    t.box.run("undo")
    t.eq(files(shop), theirs[shop], "undo leaves the code project as it was")
    t.ok(not card.exists() and not (speech / "README.md").exists(),
         "and takes away what sort added")
    t.eq(files(speech), theirs[speech], "and the speech is still word for word")


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
    t.ok((t.box.root / "Notes" / "shopping-list.md").exists(), "into the folder it belongs in")
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
    for n, name in enumerate(("one.md", "two.md", "three.md")):
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
    # a plain typo
    hits = found("accountnt")
    t.ok(any("accountant" in (h["title"] + h["snippet"]).lower() for h in hits),
         "a misspelling still finds the note")
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
    item = next(i for i in t.box.items() if "zorblat" in i["path"].lower()
                or "Zorblat" in (i["title"] or ""))
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
    """A template built one day and opened another must not arrive pre-dated."""
    import datetime
    today = datetime.date.today().strftime("%Y-%m-%d")

    # pretend this is a freshly downloaded copy carrying last year's dates
    state = t.box.root / ".os" / "state.json"
    data = json.loads(state.read_text())
    data["fresh"] = True
    state.write_text(json.dumps(data, indent=2))

    seeded = t.box.root / "Work" / "seeded-area"
    seeded.mkdir(parents=True, exist_ok=True)
    (seeded / "README.md").write_text(engine.compose(
        {"title": "Seeded area", "type": "area", "status": "active",
         "domain": "operations", "tags": ["seed"], "created": "2020-01-01",
         "updated": "2020-01-01"}, "# Seeded area\n\nShipped with the template.\n"))
    seeded_note = t.box.root / "Notes" / "seeded-note.md"
    seeded_note.write_text(engine.compose(
        {"title": "Seeded note", "type": "note", "status": "—",
         "domain": "operations", "tags": ["seed"], "created": "2020-01-01",
         "updated": "2020-01-01"}, "# Seeded note\n\nShipped with the template.\n"))

    proc = t.box.run("status")
    t.ok("dated today" in proc.stdout or "Welcome" in proc.stdout,
         "the first command announces that the copy was initialised")

    seeded_item = next(i for i in t.box.items() if i["id"] == "seeded-area")
    t.eq(seeded_item["status"], "holding",
         "a pre-merge `type: area` item is read as held, not put on the go")

    meta, _ = engine.parse_frontmatter((seeded / "README.md").read_text())
    t.eq(meta["created"], today, "seeded content is re-dated to today")
    t.eq(meta["updated"], today, "and its updated stamp too")
    note_meta, _ = engine.parse_frontmatter(seeded_note.read_text())
    t.eq(note_meta["created"], today, "a seeded note is re-dated too")
    t.ok(not json.loads(state.read_text()).get("fresh"), "the fresh flag was cleared")

    # and it does not fire twice
    second = t.box.run("status")
    t.ok("dated today" not in second.stdout, "initialisation happens exactly once")

    # setup is explicit, idempotent, and can name an owner
    t.box.run("setup", "--owner", "Sam", "--name", "Studio")
    config = json.loads((t.box.root / ".os" / "config.json").read_text())
    t.eq(config["owner"], "Sam", "setup records the owner")
    t.eq(config["name"], "Studio", "setup renames the system")
    t.box.run("setup")
    t.eq(json.loads((t.box.root / ".os" / "config.json").read_text())["owner"], "Sam",
         "running setup again keeps what was already set")


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
    t.ok("cafe-resume" in names, "accents transliterate to clean ASCII")
    t.eq([p for p in t.box.root.rglob("*untitled*")], [], "nothing collapsed to 'untitled'")
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
    t.eq(meta.get("domain"), "unsorted", "and the injected subject did not take")
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
