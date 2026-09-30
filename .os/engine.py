#!/usr/bin/env python3
"""
Zenith — engine
=================
A folder that keeps itself organised, in one file. Works with any AI, or none.

Rules this file obeys:
  1. Python 3.9+ standard library only. No installs, no network, works offline.
  2. Never destroys data. Every move is written down and reversible with `os undo`.
  3. Location independent. Rename or move the folder; it finds its own root.
  4. Deterministic. Same input tree -> same output tree. Filing is idempotent.
  5. Fails loudly and specifically, never silently.

The folders it manages (see .os/config.json):
    Work         work, in either phase        -> auto-grouped
                 (status: pushing = has a next action;
                  holding = has a standard, no next action)
    Notes        anything you look up later   -> auto-grouped
                 (prose, and files too: a PDF gets a card beside it)
    Archive      no longer live, still searchable -> filed by date

Nothing here is numbered. A thing's handle is its plain name on disk —
`ship-the-rewrite` — and `type:` in its header says what it *is*, not where it
sits: closing it moves it into Archive and it is still `ship-the-rewrite`.

Three folders, and none of them is a decision the person has to make. `os save`
writes a thing down and files it in one step, through a staging file under
.os/cache/ that exists for the length of one command — there is no drop folder
to remember, and so nothing that can sit in one being forgotten. Anything
dropped straight into a bucket by hand is adopted where it lies by `os sort`.

A file is a thing you look up later, so it is a note that happens to be bytes;
what happened and what was decided belongs in the item it happened to, under
`## Log` and `## Decisions`, findable by that item's own name.

Skills, helpers and hooks live in .claude/ and are catalogued, never moved.
"""

from __future__ import annotations

import codecs
import datetime as _dt
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path

ENGINE_VERSION = "3.0.0"
MARKER = ".os"
TOOLKIT = "_tools"   # skills/helpers/hooks: they live in .claude/, not a managed folder
#: Where `os save` puts a thing for the moment between writing it down and
#: working out where it goes. Under .os/, deliberately: a drop folder somebody
#: can see is a drop folder things get left in.
STAGING = "incoming"
#: A capture still sitting in there after this long has been missed rather than
#: only just written, so `os check` stops calling it a warning and calls it an
#: error. It is never deleted on a timer: see `staged_captures`.
STALE_STAGE_DAYS = 7
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".org"}

# ---------------------------------------------------------------------------
# terminal style
# ---------------------------------------------------------------------------


class S:
    """ANSI styling that degrades to plain text when it should."""

    enabled = True

    RESET = "\033[0m"
    B = "\033[1m"

    INK = "\033[38;5;252m"
    MUTE = "\033[38;5;245m"
    FAINT = "\033[38;5;240m"
    GOLD = "\033[38;5;179m"
    AMBER = "\033[38;5;215m"
    JADE = "\033[38;5;72m"
    SKY = "\033[38;5;110m"
    RED = "\033[38;5;167m"

    @classmethod
    def setup(cls, mode: str = "auto") -> None:
        if mode == "never" or os.environ.get("NO_COLOR"):
            cls.enabled = False
        elif mode == "always":
            cls.enabled = True
        else:
            cls.enabled = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
        if not cls.enabled:
            for k in list(vars(cls)):
                if k.isupper() and isinstance(getattr(cls, k), str):
                    setattr(cls, k, "")


def speak_utf8() -> None:
    """Make the output streams carry the characters this program actually prints.

    A terminal running under `LANG=C`, or any narrow locale, encodes stdout as
    ASCII — and a single box-drawing rule is then an unhandled exception rather
    than a line, so `os status` dies on its own heading before saying anything.
    Ask for UTF-8, and fall back to replacing whatever cannot be rendered: a
    question mark in place of a tick beats a traceback in place of the answer."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def paint(text: str, *styles: str) -> str:
    if not S.enabled or not styles:
        return text
    return "".join(styles) + text + S.RESET


def vlen(text: str) -> int:
    """Visible length: strips ANSI, counts wide glyphs as 2 columns."""
    bare = re.sub(r"\033\[[0-9;]*m", "", text)
    n = 0
    for ch in bare:
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def pad(text: str, width: int, align: str = "<") -> str:
    gap = max(0, width - vlen(text))
    if align == ">":
        return " " * gap + text
    if align == "^":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def trunc(text: str, width: int) -> str:
    if vlen(text) <= width:
        return text
    out = ""
    for ch in text:
        if vlen(out + ch) > width - 1:
            break
        out += ch
    return out + "…"


class Out:
    """Everything the CLI prints goes through here."""

    quiet = False

    @staticmethod
    def raw(line: str = "") -> None:
        if not Out.quiet:
            print(line)

    @staticmethod
    def title(text: str, sub: str = "") -> None:
        Out.raw()
        Out.raw("  " + paint(text.upper(), S.B, S.GOLD) + ("  " + paint(sub, S.FAINT) if sub else ""))
        Out.raw("  " + paint("─" * max(10, vlen(text)), S.FAINT))

    @staticmethod
    def item(bullet: str, text: str, style: str = "") -> None:
        Out.raw("  " + paint(bullet, style or S.MUTE) + " " + text)

    @staticmethod
    def ok(text: str) -> None:
        Out.item("✓", text, S.JADE)

    @staticmethod
    def warn(text: str) -> None:
        Out.item("▲", text, S.AMBER)

    @staticmethod
    def bad(text: str) -> None:
        Out.item("✖", text, S.RED)

    @staticmethod
    def info(text: str) -> None:
        Out.item("·", text, S.SKY)

    @staticmethod
    def note(text: str) -> None:
        Out.raw("    " + paint(text, S.FAINT))

    @staticmethod
    def kv(key: str, value: str, width: int = 16) -> None:
        Out.raw("  " + paint(pad(key, width), S.MUTE) + value)


def die(message: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    # Anything already said goes out first: read through a pipe, the error
    # otherwise lands above the title it belongs under.
    sys.stdout.flush()
    print("  " + paint("✖ " + message, S.RED), file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------


def today(fmt: str = "%Y-%m-%d") -> str:
    return _dt.date.today().strftime(fmt)


def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def slugify(text: str, limit: int = 56) -> str:
    """ASCII slug where possible; otherwise keep the author's own characters.

    Transliterating "設計ノート" to "untitled" would be a quiet data loss, so a
    name that carries no ASCII falls back to a filesystem-safe unicode slug."""
    raw = str(text)

    def condense(source: str, flags: int = 0) -> str:
        cleaned = re.sub(r"[^\w\s-]", " ", source, flags=flags).strip().lower()
        return re.sub(r"[\s_-]+", "-", cleaned).strip("-")

    ascii_form = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    ascii_slug = condense(ascii_form)
    native_slug = condense(raw, re.UNICODE)

    # Prefer ASCII, but only when transliteration kept everything. "Проект 2026"
    # must not become "2026" — that silently deletes the name.
    def weight(slug: str) -> int:
        return len([c for c in slug if c.isalnum()])

    slug = ascii_slug if weight(ascii_slug) >= weight(native_slug) else native_slug
    if len(slug) > limit:
        slug = slug[:limit].rstrip("-")
    return slug or "untitled"


def handle(name) -> str:
    """A name as it is typed back: one word, so it pastes without quotes.

    A folder is `Q3 OKR Review`; the hint under it says `./os edit
    q3-okr-review`, and every command finds it either way."""
    text = str(getattr(name, "ident", name) or "")
    return slugify(text) or text


def nfc(text: str) -> str:
    """Text in the one Unicode spelling that names are compared in.

    A Mac often hands over a name decomposed, one letter as several code
    points; typed on a keyboard the same name comes composed. Compared as they
    came, `./os show 한국어 메모` could not find the folder it had just made."""
    return unicodedata.normalize("NFC", str(text))


def unaccent(text: str) -> str:
    """Text with its accents taken off, for comparing what was typed without them."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(text))
                   if not unicodedata.combining(c))


def folder_name(title: str, fallback: str = "") -> str:
    """A folder spelled the way a person would write it on a label.

    Title Case With Spaces, built from the title itself so its own capitals
    survive — `Q3 OKR Review`, not the `Q3 Okr Review` that comes back
    from re-casing a slug. Anything a file system cannot take is dropped."""
    clean = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", one_line(str(title or "")))
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    if not clean:
        return titleize(fallback or "untitled")
    return titleize(clean)[:60].rstrip(" .") or titleize(fallback or "untitled")


def titleize(text: str) -> str:
    text = re.sub(r"[-_]+", " ", str(text)).strip()
    small = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "vs"}
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if w.isupper() and len(w) <= 4:
            out.append(w)
        elif i and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out) or "Untitled"


TIMESTAMP_PREFIX = re.compile(r"^\d{6,8}[-_]\d{4,6}[-_]?")


COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def gist(body: str, limit: int = 180) -> str:
    """The readable opening of a document.

    Blueprints are full of `<!-- prompts -->` telling the author what to write.
    They are instructions, not content — showing them in a search result or a
    summary just leaks the scaffolding at somebody."""
    text = COMMENT_RE.sub(" ", body)
    text = re.sub(r"^#.*$", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*]\s*(\[[ xX]\])?\s*$", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip(" -\t\n")[:limit]


def _shorten(text: str, limit: int = 62, ellipsis: str = "") -> str:
    """Trim to `limit`, but never mid-word, and never leave dangling punctuation."""
    text = text.strip().rstrip(".:;,-– ")
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    if " " in clipped:
        clipped = clipped[:clipped.rfind(" ")]
    return clipped.rstrip(".:;,-– ") + ellipsis


#: A link as it gets pasted: with its scheme, or starting www.
URL_RE = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)\S+")


def link_name(url: str) -> str:
    """A link said in words: its site, and the last part of its path."""
    m = re.match(r"(?:[A-Za-z][A-Za-z0-9+.-]*://)?(?:www\.)?([^/?#\s]+)([^?#\s]*)", url)
    if not m:
        return url
    tail = [part for part in m.group(2).split("/") if part]
    last = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", tail[-1]) if tail else ""
    return (m.group(1) + " " + re.sub(r"[-_+]+", " ", last)).strip()


def infer_title(body: str, path: Path) -> str:
    """The best short human title we can find: an H1, then the opening sentence,
    then the filename with any timestamp stripped off.

    Short matters. This becomes the folder name the person actually sees, so a
    whole paragraph is a worse title than its first clause."""
    _, body = parse_frontmatter(body) if body.lstrip().startswith("---") else ({}, body)
    h1 = re.search(r"^#\s+(.+?)\s*$", body, re.M)
    if h1 and h1.group(1).strip():
        return _shorten(h1.group(1), 72)
    for raw in body.split("\n"):
        line = raw.strip()
        if not line or line.startswith(("---", "<!--", "```", "|", ">", "#")):
            continue
        line = re.sub(r"^[-*+]\s*(\[[ xX]\]\s*)?", "", line)
        # A link says where the words came from, not what to call them: a
        # pasted article was named `https-www-bbc-co-uk-news-articles-c4g0000-in`.
        links = URL_RE.findall(line)
        if links:
            line = re.sub(r"\s+", " ", URL_RE.sub(" ", line)).strip(" -–—:|,")
            if len(line) < 3:
                line = link_name(links[0])
        line = re.sub(r"[*_`]", "", line).strip()
        if len(line) < 3:
            continue
        # first sentence, or first clause if the sentence runs long
        cut = re.split(r"(?<=[.!?])\s|\s[-–—]\s", line)[0].strip()
        if len(cut) > 62:
            head = re.split(r"[:;]\s", cut)[0].strip()
            if 12 <= len(head) < len(cut):
                cut = head
        return _shorten(cut if len(cut) >= 12 else line)
    return titleize(TIMESTAMP_PREFIX.sub("", path.stem))


#: How a next step is said inside the words themselves.
SAID_STEP = re.compile(r"\b(?:(?:first|next) (?:step|action)s?(?: is|:)|next:)\s*(?:to\s+)?"
                       r"(\S.*?)(?:[.!?](?=\s|$)|\n|$)", re.I)


#: Words shortened with a full stop that don't end a sentence: "Dr. Patel".
ABBREVIATION = re.compile(r"(?:^|\W)(?:[^\W\d_]|dr|mr|mrs|ms|mx|prof|st|mt|jr|sr|vs|"
                          r"approx|dept|etc|e\.g|i\.e)$", re.I)


def first_step(text: str) -> str:
    """The next action saved words already say, for work that arrives without one.

    8 of 12 everyday saves came back as work with `- [ ] ` left empty, even
    "… First step is booking the photo appointment." An open checkbox wins,
    then a first or next step said in so many words, then the opening
    sentence — "Call mum on Sunday" is its own next action."""
    text = COMMENT_RE.sub(" ", str(text or ""))
    box = re.search(r"^[ \t]*[-*][ \t]*\[ \][ \t]*(\S.*)$", text, re.M)
    said = box or SAID_STEP.search(text)
    if said:
        step = said.group(1)
        if not box:     # "the next step is clear: hire someone"
            step = re.sub(r"^[^\W\d_]+:\s+(?=\S)", "", step)
    else:
        lines = [ln.strip() for ln in text.split("\n")
                 if ln.strip() and not ln.lstrip().startswith(("#", "---", "|", ">", "```"))]
        step = lines[0] if lines else ""
        # The first sentence, but "Call Dr. Patel" doesn't end at "Dr.".
        for end in re.finditer(r"[.!?](?=\s)", step):
            if not ABBREVIATION.search(step[:end.start()]):
                step = step[:end.start() + 1]
                break
    step = _shorten(re.sub(r"\s+", " ", step), 100, "…")
    return step[:1].upper() + step[1:]


#: Names a computer gives a thing nobody has named yet.
UNNAMED = re.compile(r"^(?:untitled|new|folder|document|new folder|untitled folder|"
                     r"new document|untitled document|new text document)"
                     r"(?:[\s_-]*\(?\d+\)?)?$", re.I)


def given_name(name: str) -> str:
    """The name somebody gave a folder or a file, as a title.

    Empty when the computer chose it — `untitled folder`, `New Text Document`,
    a timestamp — and then the thing is named from what is in it instead.
    Their own capitals are kept; a name typed all in lowercase is title-cased."""
    stem = TIMESTAMP_PREFIX.sub("", str(name)).strip(" .-_")
    if not re.search(r"[^\W\d_]", stem) or UNNAMED.match(stem):
        return ""
    return stem if stem != stem.lower() else titleize(stem)


DIGEST_CAP = 1_000_000   # bytes read per file before we fall back to metadata
#: Text longer than this is kept as it is, with a card, like a PDF: a 468 MB
#: server log given a header took 4 GB of memory and a second copy for undo.
PROSE_CAP = 10_000_000


def _digest_file(h, path: Path) -> None:
    """Hash a file's content, or its shape when the file is large.

    Reading a 4 GB video to notice it has not changed is not insight, it is a
    stall. Past the cap we hash size and mtime instead, which is enough to spot
    a change and costs nothing."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size > DIGEST_CAP:
        h.update(f"{path.name}:{size}:{int(path.stat().st_mtime)}".encode())
        return
    try:
        h.update(path.read_bytes())
    except OSError:
        pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    try:
        if path.is_dir():
            for f in sorted(p for p in path.rglob("*") if p.is_file())[:500]:
                h.update(str(f.relative_to(path)).encode())
                _digest_file(h, f)
        else:
            _digest_file(h, path)
    except OSError:
        return ""
    return h.hexdigest()[:16]


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def content_print(path: Path) -> str:
    """What a file says, as a fingerprint that doesn't change when only its
    name does. Undo compares these: `digest` hashes the name of a file past a
    megabyte, so a note that sort had renamed always looked written in since."""
    h = hashlib.sha256()
    try:
        size = path.stat().st_size
        if size > PROSE_CAP:            # never rewritten by ./os, so never read whole
            return f"{size}:{path.stat().st_mtime_ns}"
        with path.open("rb") as fh:
            for piece in iter(lambda: fh.read(1 << 20), b""):
                h.update(piece)
    except OSError:
        return ""
    return h.hexdigest()[:16]


def is_binary(path: Path) -> bool:
    """A NUL byte in the first block means this is data, whatever it is named.

    People rename things by accident, and a .md file full of bytes should be
    kept as a file, not read as prose and given a title made of noise."""
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return False


def read_text(path: Path, limit: int = 400_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def read_all(path: Path) -> str:
    """The whole file, to read. Anything that writes it back uses `read_utf8`.

    `read_text` caps at 400 000 chars for scanning; a README past that (the
    Editing Toolbox was 405 KB on 2026-09-21) lost its tail on every
    hold/push/decide/claim/release until this went in."""
    return read_text(path, limit=-1)


def is_utf8(path: Path) -> bool:
    """Can this file be read as UTF-8, all of it? Read in pieces, so a huge
    file costs no more memory than a small one."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with path.open("rb") as fh:
            for piece in iter(lambda: fh.read(1 << 20), b""):
                decoder.decode(piece)
        decoder.decode(b"", final=True)
    except (OSError, UnicodeDecodeError):
        return False
    return True


def read_utf8(path: Path) -> str | None:
    """The whole file, for anything that writes it back — or None if it is
    not UTF-8.

    `read_text` swaps bytes it cannot read for a '?' box, which is fine for
    reading and ruinous for writing: a Latin-1 menu written back with a header
    on it had every é, à and € replaced in the file itself."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def read_to_rewrite(path: Path) -> str:
    """`read_utf8` for a command that has to change the file, or a plain stop."""
    name = f"{path.parent.name}/{path.name}"
    try:
        with path.open("r", encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError:
        die(f"{name} is saved in an older text format, and changing it here "
            "would spoil its accented letters. Open it in a text editor, save it "
            "as UTF-8, and try again.")
    except OSError as exc:
        die(f"cannot read {name}: {exc.strerror or exc}")


def read_ends(path: Path, each: int = 60_000) -> str:
    """The start of a file and its end, for anything that scans a spine.

    `## Log` grows at the bottom and nothing trims it. Reading only the first
    60 000 characters, search, `./os last` and "touched" lost a long README's
    newest lines first. The middle is the oldest history, so it is what goes."""
    try:
        if path.stat().st_size <= 2 * each:
            return read_text(path, -1)
        # Both ends in bytes, as the size is: read as characters, the start of
        # a file in Chinese already reached its end, which then came twice.
        with path.open("rb") as fh:
            head = fh.read(each).decode("utf-8", errors="replace")
            fh.seek(-each, os.SEEK_END)
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return read_text(path, each)
    head = re.sub(r"\r\n?", "\n", head)
    cut = head.rfind("\n")
    head = head[:cut + 1] if cut >= 0 else head + "\n"
    tail = re.sub(r"\r\n?", "\n", tail)
    return head + tail[tail.find("\n") + 1:]


def write_text(path: Path, text: str) -> None:
    """Atomic write: temp file in the same directory, then replace.

    The temp name carries the pid because the folder is not single-threaded.
    `settle.sh` rebuilds the index in the background while the chat may be
    running `./os save` in the foreground, and both end up writing
    registry.json. Sharing one temp name, the first to finish replaced it out
    from under the second — which then died on `os.replace` with a
    FileNotFoundError traceback, on a file it had written correctly. A name per
    process makes each writer's temp its own, so the replace always lands and
    the loser simply gets overwritten by a whole file rather than half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp~")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def human_size(n: float) -> str:
    if n < 1024:
        return f"{int(n)}B"
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        n /= 1024.0
        if n < 1024 or unit == "PB":
            return f"{n:.1f}{unit}"
    return f"{n:.1f}PB"


def days_since(stamp: str) -> int:
    if not stamp:
        return 9999
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = _dt.datetime.strptime(stamp[: len(fmt) + 2].strip(), fmt)
            return (_dt.datetime.now() - d).days
        except ValueError:
            continue
    return 9999


def unique_path(target: Path) -> Path:
    """Never overwrite. foo.md -> foo-2.md -> foo-3.md

    A directory has no extension, whatever the dots in its name say. An item
    folder called `v2.ship-the-rewrite` split on the last dot turns a collision
    into `v2-2.ship-the-rewrite` — a name that no longer reads as the thing it
    is, so the folder stops announcing what it is."""
    if not target.exists() and not target.is_symlink():
        return target
    if target.is_dir():
        stem, suffix = target.name, ""
    else:
        stem, suffix = target.stem, target.suffix
    for n in range(2, 500):
        candidate = target.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
    return target.with_name(f"{stem}-{int(time.time())}{suffix}")


# ---------------------------------------------------------------------------
# front matter  (a deliberately small, predictable YAML subset)
# ---------------------------------------------------------------------------

FIELD_ORDER = [
    "id", "title", "type", "status", "domain", "tags",
    "created", "updated", "owner", "source", "links", "summary",
]

_SCALAR_TRUE = {"true", "yes", "on"}
_SCALAR_FALSE = {"false", "no", "off"}

# Fields that are always text, however numeric they look. A version like 10.01
# must never become the float 10.01 — that would round-trip as "10.1" and lose it.
STRING_KEYS = {
    "id", "title", "status", "domain", "created", "updated", "archived", "was",
    "version", "source", "summary", "name", "description", "origin", "captured",
    "claimed",
}


def _scalar(raw: str, key: str | None = None):
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in "\"'" and raw[-1] == raw[0] and len(raw) > 1:
        inner = raw[1:-1]
        if raw[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    low = raw.lower()
    if low in _SCALAR_TRUE:
        return True
    if low in _SCALAR_FALSE:
        return False
    if low in ("null", "none", "~"):
        return None
    if key in STRING_KEYS:
        return raw
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    return raw


def _inline_list(raw: str, key: str | None = None):
    """Split `[a, "b, c", "d\\"e"]` on the commas that separate items.

    Quotes are kept on the part and handed to `_scalar`, which is the one place
    that knows how to unquote and unescape. Splitting them off here as well
    meant a `\\"` inside an item closed it early, so a tag written back out
    correctly still came apart on the way in."""
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    parts, buf, quote, escaped = [], "", "", False
    for ch in inner:
        if quote:
            buf += ch
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf += ch
        elif ch == ",":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [_scalar(p, key) for p in parts if p.strip() != ""]


#: `key:` at the start of a header line, the way the reader below takes one.
_KEY_LINE = re.compile(r"^([A-Za-z_][\w .-]*)\s*:\s*(.*)$")
#: Any line a header could hold as a field, including keys the reader skips.
_ANY_KEY = re.compile(r"^[^\s#\-][^:]*:(\s|$)")


def _header_end(lines: list) -> int | None:
    """Where the header closes, or None if the file does not open with one.

    A note can open with a `---` divider, and whatever sits between it and the
    next one is not a header just for being there. A song whose first verse
    sat between two dividers lost the verse to a sort: the lines that were not
    `key: value` were dropped when the header was written back. So a block is
    a header only if every line in it is a field, a list item or the rest of a
    field, or a comment — and at least one is a field."""
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, min(len(lines), 400)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return None
    keyed = False
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _ANY_KEY.match(line):
            keyed = True
        elif not (keyed and (line[0] in " \t" or re.match(r"^-(\s|$)", line))):
            return None
    return end if keyed else None


def parse_frontmatter(text: str):
    """Return (meta_dict, body). Tolerant: bad front matter is treated as body."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = _header_end(lines)
    if end is None:
        return {}, text

    meta, key = {}, None
    block = None      # (key, fold) while inside a `key: |` or `key: >` value
    for line in lines[1:end]:
        if block is not None:
            # An indented line continues the block; the first unindented one
            # ends it. A skill written with `description: |` used to come out
            # as an empty description, and the catalog listed it as "/".
            if line.startswith((" ", "\t")) or not line.strip():
                meta[block[0]].append(line.strip())
                continue
            joiner = " " if block[1] else "\n"
            meta[block[0]] = joiner.join(x for x in meta[block[0]] if x).strip()
            block = None
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s*-\s", line) and key is not None:
            value = _scalar(re.sub(r"^\s*-\s*", "", line), key)
            if not isinstance(meta.get(key), list):
                meta[key] = [] if meta.get(key) in ("", None) else [meta[key]]
            meta[key].append(value)
            continue
        m = _KEY_LINE.match(line)
        if not m:
            continue
        key, raw = m.group(1).strip(), m.group(2).strip()
        if raw in ("|", ">", "|-", ">-"):
            meta[key] = []
            block = (key, raw.startswith(">"))
        elif raw.startswith("[") and raw.endswith("]"):
            meta[key] = _inline_list(raw, key)
        elif raw == "":
            meta[key] = ""
        else:
            meta[key] = _scalar(raw, key)
    if block is not None:
        joiner = " " if block[1] else "\n"
        meta[block[0]] = joiner.join(x for x in meta[block[0]] if x).strip()
    body = "\n".join(lines[end + 1:])
    return meta, body.lstrip("\n")


#: Characters that end an item early when it sits inside `[a, b]`. A tag reading
#: "billing, urgent" written bare comes back as two tags, and one holding a `]`
#: truncates every tag after it — so list items get a stricter test than scalars.
_NEEDS_QUOTES_IN_LIST = re.compile(r"[,\[\]{}]|^\s|\s$")

#: A line break and everything that behaves like one. Front matter is read a
#: line at a time, so any of these inside a value is not a value any more.
_LINE_BREAK = re.compile(r"[\r\n\v\f\t\x85\u2028\u2029]+")


#: A tag as this folder used to write one: a letter, a dot, and a count. Left
#: here only so an old-style name typed out of habit is still recognisable.
_IDENT = re.compile(r"^([A-Za-z]|\d{1,2})\.(\d{1,4})$")


def canonical_id(text: str) -> str:
    """A leftover from when things here were numbered. Nothing calls it.

    Items are found by their name now — `./os show q3-okr-review` — so there
    is no spelling to normalise any more. Kept in place rather than removed so
    anything outside this file that still imports it does not break."""
    hit = _IDENT.match(str(text).strip())
    if not hit:
        return str(text).strip()
    return f"{hit.group(1).upper()}.{int(hit.group(2)):02d}"


def one_line(text: str) -> str:
    """A string with no way out of the line it is written on.

    Front matter is `key: value`, one per line, so a title carrying a newline
    does not become a two-line title — it becomes a second *key*. Pasting

        os new note "Harmless
        type: work"

    wrote a note on disk that told everything reading it it was work: the index
    listed it in the wrong place, and `./os hold` would have taken it as
    something it could change phase. Titles come from
    the clipboard as often as the keyboard, so this is one paste away rather
    than an attack, and the fix belongs here — at the point every value passes
    through — instead of at each of the places one can be typed.

    Collapses, never trims: a tag written `" lead"` is written back with its
    space, because `_emit` already quotes anything with one and somebody who
    typed it meant it."""
    return _LINE_BREAK.sub(" ", str(text))


def _emit(value, key: str | None = None, in_list: bool = False) -> str:
    if key in STRING_KEYS and isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value):
        return '"' + value + '"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_emit(v, key, in_list=True) for v in value) + "]"
    text = one_line(value)
    if text == "":
        return '""'
    if re.search(r"^[\s>|&*!%@`{\[]|:\s|#\s|\"|'|:$", text) \
            or (in_list and _NEEDS_QUOTES_IN_LIST.search(text)):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def render_frontmatter(meta: dict) -> str:
    keys = [k for k in FIELD_ORDER if k in meta]
    keys += [k for k in meta if k not in keys]
    lines = ["---"]
    for k in keys:
        lines.append(f"{k}: {_emit(meta[k], k)}")
    lines.append("---")
    return "\n".join(lines)


def compose(meta: dict, body: str) -> str:
    return render_frontmatter(meta) + "\n\n" + body.lstrip("\n").rstrip() + "\n"


def set_fields(text: str, changes: dict | None = None, drop: tuple = (),
               body: str | None = None) -> str:
    """`text` with only the named header fields changed, added or dropped.

    Every other line comes back exactly as it was: comments, the second line
    of a wrapped value, fields nested under a key, fields nothing here knows
    about. So does the body, unless a new one is given. Writing the whole
    header again from what the reader understood lost all of those on every
    hold, push, claim and close — a contact's phone number among them."""
    changes = {k: v for k, v in (changes or {}).items() if k not in drop}
    lines = text.split("\n")
    end = _header_end(lines)
    if end is None:
        rest = text if body is None else body
        return render_frontmatter(changes) + "\n\n" + rest if changes else rest

    head = lines[1:end]
    fields = []                  # [key, its first line, the line after its value]
    for i, line in enumerate(head):
        m = _KEY_LINE.match(line)
        if m:
            fields.append([m.group(1).strip(), i, i + 1])
    for field in fields:
        j = field[2]
        while j < len(head) and (not head[j].strip() or head[j][0] in " \t"
                                 or re.match(r"^-(\s|$)", head[j])):
            j += 1
        while j > field[2] and not head[j - 1].strip():
            j -= 1               # a blank line after a value belongs to what follows
        field[2] = j

    edits = {f[1]: (f[2], []) for f in fields if f[0] in drop}
    last = {f[0]: f for f in fields}      # the reader keeps the last of a repeated key
    order = {k: n for n, k in enumerate(FIELD_ORDER)}
    added = {}                   # where a new field goes -> its lines
    for k, v in changes.items():
        line = f"{k}: {_emit(v, k)}"
        if k in last:
            edits[last[k][1]] = (last[k][2], [line])
            continue
        # A field of our own goes after the ones that come before it in
        # FIELD_ORDER, so `title:` still leads; anything else goes last.
        rank = order.get(k, len(order))
        at = len(head) if rank == len(order) else \
            max([f[2] for f in fields if order.get(f[0], len(order)) < rank] or [0])
        added.setdefault(at, []).append((rank, line))
    kept, i = [], 0
    while True:
        kept += [line for _, line in sorted(added.get(i, []), key=lambda a: a[0])]
        if i >= len(head):
            break
        if i in edits:
            i, lines_in = edits[i]
            kept += lines_in
        else:
            kept.append(head[i])
            i += 1

    top = lines[:1] + kept + [lines[end]]
    if body is None:
        return "\n".join(top + lines[end + 1:])
    rest = "\n".join(lines[end + 1:])
    return "\n".join(top) + "\n" + rest[:len(rest) - len(rest.lstrip("\n"))] + body


def stamp_file(path: Path, meta_updates: dict, force: tuple = ()) -> dict:
    """Merge front matter into a markdown file on disk. Returns the merged meta.

    Existing values win by default — what somebody wrote by hand is not ours to
    overwrite. `force` names the keys where we know better, for the cases where
    the folder is more sure of a value than the file is.

    Only the fields that change are written; the rest of the file is left
    byte for byte. A file that is not UTF-8 is not written at all."""
    text = read_utf8(path)
    if text is None:
        return parse_frontmatter(read_text(path))[0]
    meta, _body = parse_frontmatter(text)
    was = dict(meta)
    for k, v in meta_updates.items():
        if v is None:
            continue
        if k in force or k not in meta or meta.get(k) in ("", [], None):
            meta[k] = v
    # A pass that looked at a file and found nothing to correct has not touched
    # it. `os sort` and `os check` re-dated ~50 untouched notes in one 2026-09-09
    # run, and `updated:` is what "gone quiet" is measured from — so a date it
    # did not earn is a lie the whole folder then reads.
    if meta == was and "updated" in was:
        return meta
    meta["updated"] = today()
    write_text(path, set_fields(text, {k: v for k, v in meta.items()
                                       if k not in was or was[k] != v}))
    return meta


def unflag(meta: dict) -> bool:
    """Drop `needs-review` from a header, once somebody has said what the thing
    is by pushing, holding, renaming or deciding about it. Nothing else cleared
    it, so `./os tidy` listed it under "I wasn't sure" for ever."""
    flags = meta.get("flags")
    flags = flags if isinstance(flags, list) else ([flags] if flags else [])
    kept = [f for f in flags if str(f).strip() != "needs-review"]
    if len(kept) == len(flags):
        return False
    if kept:
        meta["flags"] = kept
    else:
        meta.pop("flags", None)
    return True


def unflag_fields(meta: dict) -> tuple:
    """unflag() as set_fields() arguments, (changes, drop), so only the
    `flags:` line changes. Both empty when there was nothing to take off."""
    kept = dict(meta)
    if not unflag(kept):
        return {}, ()
    return ({"flags": kept["flags"]}, ()) if "flags" in kept else ({}, ("flags",))


# ---------------------------------------------------------------------------
# root discovery
# ---------------------------------------------------------------------------


def ensure_runnable(root: Path) -> list:
    """Put back the executable bit on `os` and the hooks.

    A zip extracted on Windows, or a download that drops Unix modes, arrives
    with `os` not executable — and `./os` then fails with a bare "permission
    denied" that tells a newcomer nothing. As long as this runs by any route
    (`bash os`, `python3 .os/engine.py`, one chmod), it repairs itself for good.
    Silent, and never fatal: a read-only copy is still perfectly usable."""
    healed = []
    targets = [root / "os"]
    hooks = root / ".claude" / "hooks"
    if hooks.is_dir():
        targets += [h for h in hooks.iterdir()
                    if h.is_file() and h.suffix in (".sh", ".zsh", ".bash", ".py")]
    for target in targets:
        try:
            if target.is_file() and not os.access(target, os.X_OK):
                target.chmod(target.stat().st_mode | 0o755)
                healed.append(target.name)
        except OSError:
            pass
    return healed


def relative_to_root(path: Path, root: Path) -> str:
    """Where something sits inside the folder, in POSIX form.

    Never resolves first. A symlink in Notes/ points outside the folder, and
    resolving it produces a path that is not under the root at all — which used
    to raise straight out of the indexer and leave every command broken until
    somebody found and deleted the link by hand."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        return path.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return str(path)


def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` looking for the .os marker. Rename-safe by design.

    An explicit `start` — what `--root` passes — beats everything. The launcher
    always exports ZENITH_HOME, so consulting the environment first made --root
    silently do nothing at all."""
    if start is not None:
        here = Path(start).expanduser().resolve()
        for candidate in [here, *here.parents]:
            if (candidate / MARKER / "config.json").exists():
                return candidate
        die(f"{start} is not a Zenith folder, and neither is anything above it.\n"
            "     --root wants the folder that holds AGENTS.md and .os/")

    env = os.environ.get("ZENITH_HOME")
    if env and (Path(env).expanduser() / MARKER / "config.json").exists():
        return Path(env).expanduser().resolve()
    here = Path.cwd().resolve()
    for candidate in [here, *here.parents]:
        if (candidate / MARKER / "config.json").exists():
            return candidate
    # engine.py itself lives in <root>/.os/
    guess = Path(__file__).resolve().parent.parent
    if (guess / MARKER / "config.json").exists():
        return guess
    die("no Zenith root found (looked for a .os/config.json here and in every parent)")


# ---------------------------------------------------------------------------
# the OS object: config, state, history
# ---------------------------------------------------------------------------


#: What every threshold and setting is, when the file does not say. These files
#: are edited by hand, and a missing line must not be the difference between a
#: working folder and a stack trace: whatever is there wins, and the rest of the
#: settings fall back to these.
DEFAULT_THRESHOLDS = {
    "category_split": 12, "category_max_items": 99, "max_categories_per_bucket": 9,
    "stale_project_days": 30, "dormant_project_days": 75, "rules_max_lines": 160,
    "skill_body_max_lines": 120, "duplicate_similarity": 0.86, "min_classify_score": 2.0,
    "big_file_mb": 100,
}
DEFAULT_BEHAVIOUR = {
    "keep_undo_steps": 20, "keep_backups": 3, "colour": "auto",
    "date_format": "%Y-%m-%d",
}


def settings_like(defaults: dict, given) -> dict:
    """Settings merged over their defaults, and each one the shape its default is.

    The first pass here made a missing line safe. What it did not make safe was
    a line somebody *wrote*: `"keep_undo_steps": "lots"` is a perfectly natural
    thing to type into a settings file, and it took down every command that
    changed anything — `int()` on it is a traceback, not a message. So the
    default is the type as well as the value. Anything that will not read as
    the number its default is falls back to that default, silently, because a
    number written in words is somebody experimenting and not somebody who
    needs a lecture. Settings with no default here are none of our business and
    pass through untouched."""
    out = dict(defaults)
    if not isinstance(given, dict):
        return out
    for key, value in given.items():
        want = defaults.get(key)
        if isinstance(want, bool) or not isinstance(want, (int, float)):
            out[key] = value
            continue
        try:
            out[key] = type(want)(value)
        except (TypeError, ValueError):
            pass
    return out


def whole(value, fallback: int) -> int:
    """A count read out of a file somebody edits by hand."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def clean_words(tax: dict) -> list[str]:
    """Force `.os/words.json` into the shape everything downstream reads it in.

    This is the one file the person is invited to open, so it is the one most
    likely to be half-edited — a domain written as a list, one missing its
    `keywords`, a pattern with an unclosed bracket. Every one of those was a
    traceback on the next `./os save`, which is the worst possible moment: they
    are nowhere near the file they just changed. Anything unreadable is dropped
    from the working copy on disk-read only — the file itself is never
    rewritten — and returned, so `./os check` can name it."""
    dropped: list[str] = []
    for block in ("domains", "intent"):
        if not isinstance(tax.get(block), dict):
            if tax.get(block) not in (None, {}):
                dropped.append(f"`{block}` is not a block of subjects in {{ }}")
            tax[block] = {}
    for name, spec in list(tax["domains"].items()):
        if not isinstance(spec, dict):
            tax["domains"].pop(name)
            dropped.append(f"the subject `{name}` is not a block of settings in {{ }}")
            continue
        spec["label"] = one_line(spec.get("label") or name)
        for listing in ("keywords", "learned"):
            spec[listing] = [one_line(w) for w in spec.get(listing) or []
                             if isinstance(w, (str, int, float)) and one_line(w)] \
                if isinstance(spec.get(listing), list) else []
    for name, spec in list(tax["intent"].items()):
        if not isinstance(spec, dict):
            tax["intent"].pop(name)
            dropped.append(f"the intent `{name}` is not a block of settings in {{ }}")
            continue
        spec["keywords"] = [one_line(w) for w in spec.get("keywords") or []
                            if isinstance(w, (str, int, float))] \
            if isinstance(spec.get("keywords"), list) else []
        raw = spec.get("patterns")
        good = []
        for pattern in raw if isinstance(raw, list) else []:
            try:
                re.compile(str(pattern))
            except (re.error, TypeError):
                dropped.append(f"the intent `{name}` has a pattern that is not "
                               f"a valid one: {pattern!r}")
                continue
            good.append(str(pattern))
        spec["patterns"] = good
    for listing in ("stopwords", "asset_extensions"):
        if not isinstance(tax.get(listing), list):
            if tax.get(listing) is not None:
                dropped.append(f"`{listing}` is not a list")
            tax[listing] = []
    return dropped


def merge_state(base, ours, theirs):
    """`theirs`, with this run's own changes made to it: whatever went from
    `base` to `ours`, and nothing else.

    state.json is read when a command starts and written when it ends, and
    runs overlap: the rebuild the settling hook starts after every turn, a big
    save, a second chat. Writing the whole of one run's copy back threw away
    what another had written in between, its undo step included, so the next
    `./os undo` reversed the wrong thing. Lists are the undo steps, the history
    and the like: what this run added is added, what it dropped is dropped, and
    what somebody else added stays."""
    if ours == base:
        return theirs
    if isinstance(ours, dict) and isinstance(base, dict) and isinstance(theirs, dict):
        out = dict(theirs)
        for key, value in ours.items():
            if key not in base or base[key] != value:
                out[key] = merge_state(base.get(key), value, theirs.get(key))
        for key in base:
            if key not in ours:
                out.pop(key, None)
        return out
    if isinstance(ours, list) and isinstance(base, list) and isinstance(theirs, list):
        dropped: dict = {}
        for entry in base:
            k = json.dumps(entry, sort_keys=True)
            dropped[k] = dropped.get(k, 0) + 1
        added = []
        for entry in ours:
            k = json.dumps(entry, sort_keys=True)
            if dropped.get(k):
                dropped[k] -= 1
            else:
                added.append(entry)
        kept = []
        for entry in theirs:
            k = json.dumps(entry, sort_keys=True)
            if dropped.get(k):
                dropped[k] -= 1
            else:
                kept.append(entry)
        return kept + added
    return ours


class Zenith:
    def __init__(self, root: Path):
        self.root = root
        self.dot = root / MARKER
        self.config = self._load_json(self.dot / "config.json", required=True)
        for name in (self.config.get("ignore") or []):
            if isinstance(name, str) and name.strip():
                IGNORE_FOLDERS.add(name.strip())
        # `words.json` is the one file people are told to edit, so it gets a name
        # that says what is in it. Older folders called it taxonomy.json. Only
        # fall back when it is genuinely absent — a broken one must say so by name.
        words = self.dot / "words.json"
        legacy = self.dot / "taxonomy.json"
        self.taxonomy = self._load_json(legacy if legacy.exists() and not words.exists()
                                        else words, required=True)
        if not isinstance(self.config, dict) or not isinstance(self.config.get("buckets"), dict) \
                or not self.config["buckets"]:
            die(".os/config.json has no `buckets` block, so there are no folders to file into.\n"
                "     Restore it from a fresh copy of this folder.")
        if not isinstance(self.taxonomy, dict):
            die(".os/words.json should be a block of settings in { } — restore it "
                "from a fresh copy of this folder.")
        self.words_dropped = clean_words(self.taxonomy)
        # A state file edited into something that is not a block of settings is
        # debris rather than state: nothing in it can be read, and refusing to
        # run over it would lock somebody out of their own folder.
        loaded = self._load_json(self.dot / "state.json")
        self.state = loaded if isinstance(loaded, dict) else {}
        #: state.json as this run last read or wrote it — what `save_state`
        #: compares against to tell this run's changes from another run's.
        self._state_base = json.loads(json.dumps(self.state))
        self._state_text = None   # and its text, once this run has seen it whole
        self._shape_state()
        for name, spec in self.config["buckets"].items():
            spec.setdefault("role", "note")
            spec.setdefault("label", name)
            spec.setdefault("blurb", "")
        self.thresholds = settings_like(DEFAULT_THRESHOLDS, self.config.get("thresholds"))
        self.behaviour = settings_like(DEFAULT_BEHAVIOUR, self.config.get("behaviour"))
        self.date_fmt = self.behaviour.get("date_format", "%Y-%m-%d")
        self._pending: list[dict] = []
        self._rel_cache: dict[str, str] = {}
        self._snapshots: dict[str, str] = {}
        #: how many steps had been recorded when each snapshot was taken
        self._snapped_at: dict[str, int] = {}
        self._run_dir: Path | None = None
        #: set when a run stopped partway and what it had done was kept for undo
        self.stopped_partway = ""

    def _shape_state(self) -> None:
        # `setdefault` only fills a key that is *missing*. A state file carrying
        # `"undo": "yes"` kept the string and crashed the next `./os undo` on
        # `.pop()`, and `"counters": "x"` crashed the next `./os save` — so what
        # is checked is the shape, not merely the presence.
        for key, shape in (("counters", dict), ("undo", list), ("history", list)):
            if not isinstance(self.state.get(key), shape):
                self.state[key] = shape()
        if not isinstance(self.state.get("created"), str):
            self.state["created"] = now_iso()

    # -- persistence --------------------------------------------------------

    @staticmethod
    def _load_json(path: Path, required: bool = False):
        """Read a settings file. A broken one is always reported by name: these
        are files people edit by hand, and a stray comma should say so."""
        if not path.exists():
            if required:
                die(f".os/{path.name} is missing.\n"
                    "     Restore it from a backup in .os/backups/, or copy it "
                    "from a fresh copy of this folder.")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            die(f".os/{path.name} has a typo in it and can't be read.\n"
                f"     {exc}\n"
                "     Usually a missing comma or an unclosed quote. Fix that line, "
                "or restore the file from .os/backups/.")
        except OSError as exc:
            die(f"cannot read .os/{path.name}: {exc}")

    def refresh_state(self) -> None:
        """Take in whatever another run wrote to state.json since this one read
        it, keeping this run's own changes on top. See `merge_state`."""
        try:
            text = (self.dot / "state.json").read_text(encoding="utf-8")
            if text == self._state_text:
                return   # nobody has written to it since
            theirs, base = json.loads(text), json.loads(text)
        except (OSError, ValueError):
            return
        if not isinstance(theirs, dict):
            return
        merged = merge_state(self._state_base, self.state, theirs)
        self.state.clear()
        self.state.update(merged)
        self._shape_state()
        self._state_base = base
        self._state_text = text

    def save_state(self) -> None:
        self.refresh_state()
        if {k: v for k, v in self.state.items() if k != "saved"} == \
                {k: v for k, v in self._state_base.items() if k != "saved"}:
            return   # nothing of this run's to add to what is there
        self.state["saved"] = now_iso()
        text = json.dumps(self.state, indent=2) + "\n"
        write_text(self.dot / "state.json", text)
        self._state_base = json.loads(text)
        self._state_text = text

    def save_config(self) -> None:
        write_text(self.dot / "config.json", json.dumps(self.config, indent=2) + "\n")

    def set_name(self, name: str) -> None:
        """Call the folder something else, wherever it says what it is called.

        Changed in config.json alone, the name reached ./os and INDEX.md while
        AGENTS.md, which every AI reads first, went on saying the old one.
        Updates compare AGENTS.md with its name taken out, so a renamed copy
        still counts as untouched. Saved with the next save_config()."""
        was = str(self.config.get("name") or "")
        self.config["name"] = name
        rules = self.root / "AGENTS.md"
        if not rules.is_file():
            return
        text = read_all(rules)
        said = f"This folder is called {name}. "
        # The old name exactly, when it is there: a name like "St. Ives" has a
        # full stop in it, and the pattern alone stops at that one.
        old = f"This folder is called {was}. "
        named = (text.replace(old, said, 1) if was and old in text else
                 FOLDER_CALLED.sub(lambda _: said, text, count=1))
        if named != text:
            write_text(rules, named)

    # -- paths --------------------------------------------------------------

    def taken_back(self) -> set:
        """Staged words `os undo` put back, which sort must leave where they are.

        Pruned to what is still on disk, so a re-saved thought is filed again
        like anything else and this never becomes a permanent blocklist."""
        kept = [r for r in (self.state.get("taken_back") or [])
                if (self.root / r).exists()]
        if kept != (self.state.get("taken_back") or []):
            self.state["taken_back"] = kept
            self.save_state()
        return set(kept)

    def rel(self, path: Path) -> str:
        """Path relative to the root, always with forward slashes so it reads the
        same on every machine. Memoised: resolve() is a syscall, and one
        `os check` on a big folder asks for the same paths thousands of times."""
        key = str(path)
        hit = self._rel_cache.get(key)
        if hit is None:
            hit = relative_to_root(path, self.root)
            if len(self._rel_cache) < 20_000:
                self._rel_cache[key] = hit
        return hit

    def buckets(self) -> dict:
        return self.config["buckets"]

    def bucket_for_role(self, role: str) -> str:
        for name, spec in self.buckets().items():
            if spec["role"] == role:
                return name
        die(f"no folder is set up for '{role}' — check .os/config.json")

    # -- counters ------------------------------------------------------------
    # Left over from when everything here carried a tag like W.04. Nothing is
    # tagged any more — a thing's handle is its name on disk — so the counters
    # still tick over but nothing is written from them. Kept because an older
    # state file has them, and dropping them would make it unreadable.

    def next_id(self, bucket: str) -> str:
        code = self.buckets()[bucket].get("code")
        if not code:
            die(f"the {bucket}/ folder has no `code:` in .os/config.json, so it "
                "has no counter to tick")
        counters = self.state.setdefault("counters", {})
        # A counter hand-edited to a word must not throw; starting again from
        # one is recoverable, a traceback is not, and `reserve_id_at_least`
        # pushes it past whatever is already on disk.
        n = whole(counters.get(code, 0), 0) + 1
        counters[code] = n
        return f"{code}.{n:02d}"

    def reserve_id_at_least(self, bucket: str, seen: set[str]) -> None:
        """Push the counter past any old-style tags still on disk.

        A folder that never handed out tags has no counter to repair. Nothing
        new is tagged, so this only matters for a folder carried over from
        when things here were numbered."""
        code = self.buckets()[bucket].get("code")
        if not code:
            return
        top = 0
        for ident in seen:
            if ident.startswith(code + "."):
                try:
                    top = max(top, int(ident.split(".", 1)[1]))
                except ValueError:
                    pass
        counters = self.state.setdefault("counters", {})
        counters[code] = max(whole(counters.get(code, 0), 0), top)

    # -- history / undo -----------------------------------------------------

    def record(self, action: str, src: str, dst: str = "") -> None:
        self._pending.append({"action": action, "src": src, "dst": dst})

    # -- content snapshots, so undo restores what a file said, not just where --

    def _ensure_run_dir(self) -> Path:
        if self._run_dir is None:
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self._run_dir = self.dot / "cache" / "undo" / stamp
            self._run_dir.mkdir(parents=True, exist_ok=True)
        return self._run_dir

    def snapshot(self, src: Path, cap_bytes: int = 512_000, cap_files: int = 400) -> None:
        """Copy the text content of `src` aside before anything rewrites it.

        Only text, only small files, only a bounded number of them: a snapshot
        is insurance, not a second copy of the folder. The one exception is the
        file a header is about to be written into — `src` itself, or the
        folder's own README — which is kept whatever its size. Skipping it left
        undo putting a 3 MB thesis back under its old name, still cut short.

        The *first* snapshot of a file in a run is the one that is kept. One
        command can touch the same file twice — `os close` rewrites the header
        and then moves the folder — and the second copy would be the rewritten
        one, which is exactly what undo is supposed to put back."""
        try:
            if src.is_file():
                spine, targets = src, []
            else:
                spine = Scanner.spine_of(src)
                targets = [p for p in sorted(src.rglob("*")) if p.is_file()]
        except OSError:
            return
        for f in ([spine] if spine else []) + targets[:cap_files]:
            # A symlink's content belongs to whatever it points at, which may be
            # /etc/hosts and is certainly not ours to put back. Undoing a sort
            # that had adopted one said "couldn't put this one back: permission
            # denied" about a link it had in fact restored perfectly.
            if f.is_symlink():
                continue
            if f.suffix.lower() not in TEXT_SUFFIXES or self.rel(f) in self._snapshots:
                continue
            try:
                size = f.stat().st_size
                if size > PROSE_CAP or (f != spine and size > cap_bytes):
                    continue        # past PROSE_CAP nothing rewrites it
                data = f.read_bytes()
            except OSError:
                continue
            key = self.rel(f)
            blob = hashlib.sha256(key.encode()).hexdigest()[:20] + ".bak"
            try:
                (self._ensure_run_dir() / blob).write_bytes(data)
            except OSError:
                continue
            self._snapshots[key] = blob
            self._snapped_at[key] = len(self._pending)

    def _prune_snapshots(self) -> None:
        keep = int(self.behaviour.get("keep_undo_steps", 20))
        live = {e.get("blobs") for e in self.state.get("undo", []) if e.get("blobs")}
        base = self.dot / "cache" / "undo"
        if not base.exists():
            return
        for d in sorted(base.iterdir()):
            if d.is_dir() and d.name not in live:
                shutil.rmtree(d, ignore_errors=True)
        for d in sorted(base.iterdir())[:-max(keep, 1)]:
            shutil.rmtree(d, ignore_errors=True)

    def _where_now(self, rel: str, since: int) -> str:
        """Where the thing at `rel` is after the moves recorded from step `since` on."""
        for step in self._pending[since:]:
            if step["action"] != "move":
                continue
            if rel == step["src"]:
                rel = step["dst"]
            elif rel.startswith(step["src"] + "/"):
                rel = step["dst"] + rel[len(step["src"]):]
        return rel

    def commit(self, label: str) -> int:
        if not self._pending:
            return 0
        # How each file stood when this run was done with it, so undo can tell
        # whether anybody has written in it since: see Undo.revert.
        after = {}
        for rel in self._snapshots:
            now = self.root / self._where_now(rel, self._snapped_at.get(rel, 0))
            if now.is_file() and not now.is_symlink():
                after[rel] = content_print(now)
        for n, step in enumerate(self._pending):
            if step["action"] == "create":
                now = self.root / self._where_now(step["src"], n + 1)
                if now.is_file() and not now.is_symlink():
                    step["digest"] = content_print(now)
        entry = {"at": now_iso(), "label": label, "steps": self._pending,
                 "snapshots": dict(self._snapshots), "after": after,
                 "blobs": self._run_dir.name if self._run_dir else ""}
        undo = self.state.setdefault("undo", [])
        undo.append(entry)
        keep = int(self.behaviour.get("keep_undo_steps", 20))
        self.state["undo"] = undo[-keep:]
        history = self.state.setdefault("history", [])
        history.append({"at": entry["at"], "label": label, "steps": len(self._pending)})
        self.state["history"] = history[-400:]
        count = len(self._pending)
        self._pending = []
        self._snapshots = {}
        self._snapped_at = {}
        self._run_dir = None
        self.save_state()
        self._prune_snapshots()
        # Deliberately not written into anything a person reads. Every operation
        # is already recorded in state["history"] above, and a log filled with
        # "save — 1 change" buries the one thing worth keeping: what you decided,
        # and why. That belongs in the item it happened to, under ## Decisions.
        return count

    # -- first run ----------------------------------------------------------

    def is_fresh(self) -> bool:
        return bool(self.state.get("fresh", False))

    def initialise(self, owner: str = "", name: str = "") -> dict:
        """Make a shipped template belong to whoever just opened it.

        A template is built on one day and opened on another. Left alone, every
        date in it would be a lie and every project would look stale on arrival."""
        day = today()
        restamped, moved = 0, 0   # moved: kept at 0; nothing needs migrating now

        for bucket, spec in self.buckets().items():
            base = self.root / bucket
            if not base.exists():
                continue
            for path in sorted(base.rglob("*.md")):
                # README.md is an item's spine here, so `ignored()` is the wrong
                # filter — it deliberately hides README from the *scanner*.
                if path.name in ("CLAUDE.md", "_index.md") or path.name.startswith("."):
                    continue
                text = read_utf8(path)
                meta, _body = parse_frontmatter(text or "")
                if not meta:
                    continue
                if meta.get("created") == day and meta.get("updated") == day:
                    continue
                write_text(path, set_fields(text, {"created": day, "updated": day}))
                restamped += 1

        if owner:
            self.config["owner"] = owner
        if name:
            self.set_name(name)
        self.config.setdefault("review", {})["last_run"] = None
        self.save_config()

        self.state["fresh"] = False
        self.state["installed"] = day
        self.state.setdefault("undo", [])
        self.state["history"] = []
        self.save_state()
        return {"restamped": restamped, "moved": moved, "day": day,
                "owner": self.config.get("owner", ""), "name": self.config.get("name", "")}

    # -- filesystem moves (always journalled) -------------------------------

    def move(self, src: Path, dst: Path) -> Path:
        """Move one thing, and write down that it happened.

        The source can disappear under us — someone dragged it out of the
        folder, or a second run got to it first. `shutil.move` answers that with
        a raw traceback, which tells a person nothing and looks like a crash."""
        if not src.exists() and not src.is_symlink():
            raise FileNotFoundError(f"{self.rel(src)} was moved or deleted while this ran")
        # Already where it is going: not a move. The sorter asks for exactly
        # this when it adopts a file where it lies, and `unique_path` below
        # turned it into `name-2` and back. It is still about to get a header,
        # so undo keeps what it said.
        if dst == src:
            self.snapshot(src)
            self.record("edit", self.rel(src))
            return src
        self.snapshot(src)
        dst = unique_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        self.record("move", self.rel(src), self.rel(dst))
        return dst

    def move_item(self, src: Path, dst: Path) -> Path:
        """Move something, taking its asset card with it.

        A file in Notes carries a sibling `<name>.card.md` holding everything
        searchable about it. Move one without the other and you get an orphan
        card describing a file that is not there."""
        card = src.with_name(src.name + ".card.md")
        moved = self.move(src, dst)
        if card.exists():
            self.move(card, moved.with_name(moved.name + ".card.md"))
        return moved

    def created(self, path: Path) -> None:
        """Remember a file this run brought into existence, so undo can remove it."""
        if path.exists():
            self.record("create", self.rel(path))

    def make_dir(self, path: Path) -> Path:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            self.record("mkdir", self.rel(path))
        return path


class Lock:
    """One mutating command at a time.

    Two Claude sessions in the same folder is normal; two of them re-shelving it
    simultaneously is not. Read-only commands never take the lock."""

    STALE_AFTER = 900   # seconds; a lock older than this belonged to a dead run
    #: How long a run waits its turn before giving up. An AI runs several
    #: commands at once, and a save takes a tenth of a second: turned away on
    #: the spot, eleven of twelve saves made together were told the folder was
    #: busy, and their words sat unfiled until somebody ran ./os sort.
    #: ZENITH_LOCK_WAIT overrides it, so the checks of a busy folder need not
    #: sit out the whole wait.
    WAIT = 10.0

    def __init__(self, os_: "Zenith", label: str, safe: str = ""):
        self.os = os_
        self.path = os_.dot / ".lock"
        self.label = label
        # What is already on disk and cannot be lost, if this one is turned
        # away. Being told the folder is busy reads like "your words are gone"
        # unless somebody says otherwise, and by this point they never are.
        self.safe = safe
        self.held = False

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (OSError, TypeError, ValueError):
            return False
        return True

    def _holder(self) -> dict | None:
        """Whoever is holding the lock right now, or None.

        None means the file on disk is debris rather than a holder: unreadable,
        ours already, left by a process that has since died, or simply older
        than any run could plausibly still be."""
        try:
            held = json.loads(self.path.read_text())
            pid = int(held["pid"])
            age = time.time() - float(held.get("at", 0))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None
        if pid == os.getpid() or not self._alive(pid) or age >= self.STALE_AFTER:
            return None
        return held

    def __enter__(self) -> "Lock":
        """Claim the lock, or say who has it.

        Checking `exists()` and *then* writing left a window: two runs starting
        together both looked, both saw nothing, and both went ahead. `O_EXCL`
        closed that one and opened a narrower one, which took concurrent CI to
        find: between creating the file and writing into it, the lock exists but
        is *empty*. A second run read it, could not parse it, correctly
        concluded that an unreadable lock is debris, deleted it — and took the
        lock. Both were then inside, and one moved a folder the other was
        midway through moving.

        So the payload is written first, under a name nobody looks at, and only
        then made visible in a single atomic step. `os.link` fails if the name
        is taken, which is the exclusion; and because the content is already
        there, a lock file that exists is always one somebody can read.

        Once it is ours, state.json is read again: another run may have written
        to it since this one started, and what this one writes goes on top."""
        payload = json.dumps({"pid": os.getpid(), "at": time.time(), "label": self.label})
        tmp = self.path.with_name(f".lock.{os.getpid()}")
        try:
            wait = max(0.0, float(os.environ.get("ZENITH_LOCK_WAIT", self.WAIT)))
        except ValueError:
            wait = self.WAIT
        deadline = time.monotonic() + wait
        cleared = 0
        while True:
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.link(tmp, self.path)
            except FileExistsError:
                held = self._holder()
                if held is None:
                    try:
                        self.path.unlink()   # debris from a run that is long gone
                    except OSError:
                        pass
                    cleared += 1
                    if cleared <= 2:
                        continue
                if time.monotonic() >= deadline:
                    if held is None:
                        # lost every try to a run that keeps replacing the lock
                        die("another run is already working here. Wait for it, or "
                            "remove .os/.lock if it is dead."
                            + (f"\n     {self.safe}" if self.safe else ""))
                    die(f"another run is already working here "
                        f"('{held.get('label', '?')}', pid {held.get('pid')}). "
                        "Wait for it, or remove .os/.lock if it is dead."
                        + (f"\n     {self.safe}" if self.safe else ""))
                time.sleep(0.15)
                continue
            except OSError:
                # A filesystem with no hard links, or one that will not take the
                # write at all. Neither is a reason to refuse somebody their own
                # folder: no lock is worse than a lock, and better than a wall.
                self.os.refresh_state()
                return self
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            self.held = True
            self.os.refresh_state()
            return self

    def __exit__(self, kind, error, trace) -> bool:
        # Stopped partway: Ctrl-C, a disk that said no, anything at all. What
        # it had already done is real, so it goes on the undo list like any
        # finished run, while the folder is still this run's. Only written
        # down at the end, it left moves that undo could not reverse, and
        # undo reversed the command before instead.
        if kind is not None and self.os._pending:
            try:
                self.os.commit(f"{self.label} (stopped partway)")
                self.os.stopped_partway = self.label
            except Exception:
                pass   # never hide what stopped it
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
        return False


# ---------------------------------------------------------------------------
# the item model
# ---------------------------------------------------------------------------

IGNORE_NAMES = {
    ".os", ".git", ".DS_Store", ".Trash", "node_modules", "__pycache__",
    ".venv", "venv", ".idea", ".vscode", ".category", ".gitkeep",
    "_index.md", "INDEX.md", "CATALOG.md", "CLAUDE.md", "README.md",
    "desktop.ini", ".localized", "os",
    "Icon\r", "Thumbs.db",     # a Mac folder's own icon; Windows' picture previews
}
#: Folders the person keeps but `./os` never files, nags or renames — the media
#: library, most often: footage, exports, big binaries. `Content` by default;
#: `"ignore": [...]` in .os/config.json adds to it. It sits under Work/ so the
#: person finds it with everything else, but nothing in it is an item.
MEDIA_FOLDER = "Content"
IGNORE_FOLDERS = {MEDIA_FOLDER}
#: Footage. Saved, it goes to Work/Content whatever its size; anything else
#: goes there once it is bigger than `big_file_mb` in .os/config.json.
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".mts",
                  ".m2ts", ".mxf", ".braw", ".r3d", ".3gp")
#: All of these are plain suffixes, so `str.endswith` settles them in one C-level
#: call. `Path.match` would run five glob compilations per file, and this is asked
#: about every file in the tree, on every single command.
IGNORE_SUFFIXES = (".tmp~", ".pyc", ".swp", "~", ".card.md")
CATEGORY_MARKER = ".category"

#: Files that ARE the thing they sit in. The scanner hides them so a project
#: folder counts as one item rather than two — but anything trying to work out
#: what a folder *is* has to read them, or it is guessing blind.
SPINE_NAMES = {"README.md", "index.md", "SKILL.md", "AGENT.md"}

#: Words that carry no complaint. Only used to tell one snag from another.
STOPWORDS_LITE = {
    "a", "an", "and", "as", "at", "be", "but", "by", "can", "for", "from", "get",
    "got", "has", "have", "in", "is", "it", "its", "me", "my", "no", "not", "of",
    "on", "or", "so", "that", "the", "their", "them", "then", "there", "they",
    "this", "to", "was", "were", "what", "when", "which", "with", "you", "your",
}


def ignored(path: Path) -> bool:
    name = path.name
    return (name in IGNORE_NAMES or name in IGNORE_FOLDERS
            or name.startswith("._")
            or name.endswith(IGNORE_SUFFIXES))


#: What a code project holds. Such a folder is its own repository, so nothing
#: of ours is ever written inside it.
CODE_MARKERS = ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", ".git")


def own_paths(folder: Path) -> list:
    """Everything inside a folder that somebody made, in a stable order.

    Never into `.git/` or `node_modules/`: they hold thousands of files nobody
    wrote, and reading them tagged a code project `head, heads, index, info`."""
    out = []
    for top, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not ignored(Path(d))]
        out += [Path(top) / n for n in dirs + files]
    return sorted(out)


def big_media(os_: "Zenith", path: Path) -> bool:
    """Is this footage, or too big to read? Then it belongs in Work/Content."""
    files = [path] if path.is_file() else \
        [p for p in own_paths(path) if p.is_file() and not ignored(p)]
    try:
        size = sum(p.stat().st_size for p in files)
    except OSError:
        return False
    cap = os_.thresholds.get("big_file_mb", 100) * 1_000_000
    return bool(files) and (size > cap or all(p.suffix.lower() in VIDEO_SUFFIXES
                                              for p in files))


def staged_captures(dot: Path) -> list[Path]:
    """Anything `os save` wrote down but never got as far as filing.

    Normally empty: save stages and files in one breath. What lands here is a
    run that died between the two — and it is the only place in the folder
    where something the person typed can sit where nothing is looking at it.
    So everything that reports on the folder looks here, and nothing sweeps
    it: content the person wrote is never thrown away on a timer."""
    stage = dot / "cache" / STAGING
    if not stage.exists():
        return []
    return sorted(p for p in stage.iterdir() if p.is_file() and not ignored(p))


#: What belongs at the top of the folder: this program, and the files an AI
#: reads its rules from. Any other file there was dropped in.
TOP_NAMES = {"AGENTS.md", "CLAUDE.md", "README.md", "INDEX.md", "os", "template-feedback.md",
             "CLAUDE.local.md", "AGENTS.override.md", "GEMINI.md"}


def loose_at_top(root: Path) -> list:
    """Files dropped at the top of the folder, outside every bucket.

    The most natural place to drop a file, and the one place nothing looked:
    `./os` never mentioned it, sort said everything was filed, find could not
    see it, and save refused it as already in this folder. Sort files these
    like anything else dropped in by hand."""
    try:
        return sorted(p for p in root.iterdir()
                      if p.is_file() and not p.is_symlink() and p.name not in TOP_NAMES
                      and not p.name.startswith(".") and not ignored(p))
    except OSError:
        return []


class Item:
    """One thing the OS knows about."""

    __slots__ = (
        "path", "bucket", "kind", "ident", "title", "status", "domain", "tags",
        "created", "updated", "summary", "is_dir", "words", "trail",
        "flags", "fingerprint", "spine", "blurb", "managed", "claim",
    )

    def __init__(self, path: Path, bucket: str, kind: str):
        self.path = path
        self.bucket = bucket
        self.kind = kind
        self.ident = ""
        self.title = ""
        self.status = ""
        self.domain = ""
        self.tags: list[str] = []
        self.created = ""
        self.updated = ""
        self.summary = ""
        self.blurb = ""            # a skill/helper `description:`, verbatim
        self.is_dir = path.is_dir()
        self.words = 0
        self.trail: list[str] = []      # category folders between bucket and item
        self.flags: list[str] = []
        self.fingerprint = ""
        self.spine: Path | None = None  # the markdown file that carries front matter
        self.claim = ""            # a `claimed:` header, verbatim — see cmd_claim
        #: Has the OS ever taken charge of this? True iff its own header says
        #: what it is — a `type:` or a `title:`. The *filename* proves nothing;
        #: anyone can type one. Decided in hydrate(), where the front matter is
        #: already read, so asking costs nothing: `os brief` asks it about every
        #: item, every run.
        self.managed = False

    def as_dict(self, root: Path) -> dict:
        return {
            "id": self.ident,
            "title": self.title,
            "kind": self.kind,
            "bucket": self.bucket,
            "trail": self.trail,
            "path": relative_to_root(self.path, root) if self.path.exists() else "",
            "status": self.status,
            "domain": self.domain,
            "tags": self.tags,
            "created": self.created,
            "updated": self.updated,
            "summary": self.summary,
            "words": self.words,
            "flags": self.flags,
            "fingerprint": self.fingerprint,
        }


# ---------------------------------------------------------------------------
# the classifier
# ---------------------------------------------------------------------------


#: An intent block in words.json names either a kind of thing or a phase of
#: work. Both spellings are accepted: a words.json somebody customised before
#: Projects and Ongoing merged still classifies correctly.
INTENT_RESULT = {
    "pushing": ("project", "pushing"), "project": ("project", "pushing"),
    "holding": ("project", "holding"), "area": ("project", "holding"),
    "ongoing": ("project", "holding"),
    "note": ("note", ""), "asset": ("asset", ""),
}

#: How that reads in the one line the person is shown.
INTENT_WORDS = {
    "pushing": "work with a next action", "project": "work with a next action",
    "holding": "something to keep up", "area": "something to keep up",
    "ongoing": "something to keep up",
    "note": "a note", "asset": "a file",
}


class Classifier:
    """Decides what an unfiled thing is, what phase it is in, and where it goes.

    Signals, strongest first:
        1. explicit front matter (type / domain / bucket)
        2. a filename convention  (project--x.md, note--x.md, ...)
        3. structural shape       (a folder holding SKILL.md is a skill)
        4. file extension         (media and data are assets)
        5. weighted keyword score against .os/words.json
    Anything below `min_classify_score` is filed as a note and flagged
    `needs-review`, so a low-confidence guess is visible rather than silent.
    """

    NAME_HINTS = {
        "project": "project", "proj": "project", "p": "project",
        "area": "project", "ongoing": "project", "work": "project", "w": "project",
        "note": "note", "n": "note", "ref": "note",
        "skill": "skill", "agent": "agent",
        "asset": "asset", "file": "asset",
    }

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.tax = os_.taxonomy
        self.stop = set(self.tax.get("stopwords", []))
        self.asset_ext = set(self.tax.get("asset_extensions", []))
        self._compiled = {
            intent: [re.compile(p, re.I | re.M) for p in spec.get("patterns", [])]
            for intent, spec in self.tax.get("intent", {}).items()
        }
        # `keywords` is theirs and is never written by anything but them.
        # `learned` is where /learn puts the vocabulary a subject taught the
        # folder, kept separate so it stays obvious which words came from where
        # — but scored identically, because a word only helps if it counts.
        self._words = {
            name: self._terms(list(spec["keywords"]) + list(spec.get("learned", [])))
            for name, spec in self.tax["domains"].items()
        }
        self._intent_words = {
            name: self._terms(spec.get("keywords", []))
            for name, spec in self.tax.get("intent", {}).items()
        }

    @classmethod
    def _terms(cls, keywords: list) -> list:
        """One entry per *term*, however many ways it happens to be spelled.

        A list holding both "to-do" and "to do" once held two terms, because
        the hyphen and the space matched different text. They match the same
        text now, so "Nothing to do" scored the same term twice and doubled a
        subject's score against a note that was only mentioning it."""
        out, seen = [], set()
        for kw in keywords:
            key = re.sub(r"[^a-z0-9]+", " ", str(kw).lower()).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append((str(kw).lower(), cls._matcher(kw)))
        return out

    @staticmethod
    def _matcher(keyword: str):
        """A keyword must match a whole word, not a fragment — and a phrase must
        match however the words in it happen to be joined.

        Without the first part, "ci" fires inside "pricing" and "ad" inside
        "already", and short keywords quietly poison every score. Keywords
        carrying punctuation are matched literally, since they cannot collide.

        The second part took a real filing to find. The heaviest signal by far
        is the title, worth 3.0 against 0.6 for a mention in the body — and for
        anything dropped in or captured, the title *is* the filename, hyphens
        and all. A phrase written with a space could never match one, so
        "poke test" scored nothing against `poke-test-sprang-back.md` while the
        single generic word "test" scored 3.0 for another subject entirely.
        Every multi-word term in `words.json` was invisible in the one place it
        counted most, which is most of what `./os words` is ever taught: a
        subject arrives carrying "ad set" and "learning phase", not nouns."""
        k = keyword.lower()
        if not re.fullmatch(r"[a-z0-9]+(?:[ -][a-z0-9]+)*", k):
            return None
        # A short run, not any run: "ad set" is one term where the words are
        # adjacent, and should not be found either side of a full paragraph.
        joined = r"[^a-z0-9]{1,3}".join(re.escape(w) for w in re.split(r"[ -]+", k))
        return re.compile(r"(?<![a-z0-9])" + joined + r"(?![a-z0-9])")

    @staticmethod
    def _hits(hay: str, kw: str, rx) -> int:
        if rx is None:
            return hay.count(kw)
        return len(rx.findall(hay))

    # -- reading ------------------------------------------------------------

    #: A link, or a handle written the way one is on any platform.
    LINKS = re.compile(r"https?://\S+|(?:^|\s)@[A-Za-z0-9_.]{2,}")

    #: How a wrap-up names what got done. Past tense, no next action.
    DONE_LEAD = re.compile(
        r"^\s*(?:[^\n]{0,60}\b)?(?:shipped|finished|done|completed|wrapped up|wrap-up|"
        r"recap|summary|what got done|what we did)\b", re.I)
    #: A next action, in any of the ways people write one down.
    OPEN_STEP = re.compile(r"^\s*[-*]\s*\[ \]|\bnext (?:action|step)s?\b|\btodo\b|\bto do\b", re.I | re.M)

    @classmethod
    def _finished_summary(cls, title: str, body: str) -> bool:
        """Is this an account of work already done, rather than work to do?

        "Folder improvements shipped 2026-09-08: ..." was filed as a project
        because it said *shipped* (snag, 2026-09-08). A summary of finished
        work has nothing left to push on: it is a note. Only the opening line
        decides, and any open checkbox or next step keeps it work."""
        head = (title or "").strip() or body.strip().split("\n", 1)[0]
        if not cls.DONE_LEAD.match(head[:120]):
            return False
        return not cls.OPEN_STEP.search(body)

    @classmethod
    def _reference_list(cls, body: str) -> bool:
        """Is this a list of things to look at, rather than something to do?

        "channels whose edits are the standard" — a line of context and a dozen
        links — scored as work twice and left two empty projects behind (snag,
        2026-09-05). Three links or handles is a list; a list is something you
        come back to, which is a note."""
        return len(cls.LINKS.findall(body)) >= 3

    @staticmethod
    def _sample(path: Path) -> tuple[str, str]:
        """(title-ish text, body text) for a file or a folder."""
        if path.is_dir():
            names, body = [], []
            # Read the spine first: a dropped-in folder usually says what it is
            # in its README, and the scanner's ignore list hides exactly that.
            for name in ("README.md", "index.md", "SKILL.md", "AGENT.md"):
                spine = path / name
                if spine.exists() and spine.is_file():
                    body.append(read_text(spine, 20_000))
                    break
            for child in own_paths(path)[:60]:
                if ignored(child) and child.name not in SPINE_NAMES:
                    continue
                names.append(child.name)
                if child.is_file() and child.suffix.lower() in TEXT_SUFFIXES \
                        and child.name not in SPINE_NAMES:
                    body.append(read_text(child, 20_000))
            return path.name + " " + " ".join(names), "\n".join(body)[:120_000]
        if path.suffix.lower() in TEXT_SUFFIXES:
            return path.stem, read_text(path, 120_000)
        return path.stem, ""

    # -- scoring ------------------------------------------------------------

    def score_domain(self, title: str, body: str, suffix: str) -> tuple[str, float, dict]:
        head = " ".join(body.split("\n")[:40])
        headings = " ".join(re.findall(r"^#{1,3}\s+(.+)$", body, re.M)[:20])
        hay_title = (title + " " + headings).lower()
        hay_head = head.lower()
        hay_body = body.lower()

        scores: dict[str, float] = {}
        #: The longest keyword each subject actually matched on. Only ever used
        #: to break a tie, and a tie is otherwise broken by the alphabet — which
        #: is deterministic and means nothing. "the poke test sprang back" drew
        #: 3.0 each between `engineering`, on the word "test", and `personal`,
        #: on the phrase "poke test" the person had just taught it; engineering
        #: won for beginning with an e. The longer match is the better evidence:
        #: a two-word phrase is the subject saying its own name, and a short
        #: generic word is a coincidence waiting to happen.
        sharpest: dict[str, int] = {}
        for name, spec in self.tax["domains"].items():
            total = 0.0
            longest = 0
            for kw, rx in self._words[name]:
                matched = False
                if self._hits(hay_title, kw, rx):
                    total += 3.0
                    matched = True
                if self._hits(hay_head, kw, rx):
                    total += 1.5
                    matched = True
                in_body = min(self._hits(hay_body, kw, rx), 6)
                total += in_body * 0.6
                if matched or in_body:
                    longest = max(longest, len(kw))
            if suffix and suffix in spec.get("extensions", []):
                total += 2.5
            if total:
                scores[name] = round(total, 2)
                sharpest[name] = longest
        if not scores:
            return "", 0.0, {}
        best = max(sorted(scores), key=lambda k: (scores[k], sharpest[k]))
        return best, scores[best], scores

    def score_intent(self, title: str, body: str) -> tuple[str, float, dict]:
        hay = (title + "\n" + body).lower()
        scores: dict[str, float] = {}
        for intent, spec in self.tax.get("intent", {}).items():
            total = 0.0
            weight = float(spec.get("weight", 1.0))
            for kw, rx in self._intent_words[intent]:
                hits = self._hits(hay, kw, rx)
                if hits:
                    total += weight * min(hits, 4)
            for rx in self._compiled.get(intent, []):
                total += weight * min(len(rx.findall(body)), 6)
            if total:
                scores[intent] = round(total, 2)
        if not scores:
            return "note", 0.0, {}
        best = max(scores, key=lambda k: scores[k])
        return best, scores[best], scores

    def keywords(self, title: str, body: str, limit: int = 6) -> list[str]:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9+-]{2,}", (title + " " + body).lower())
        freq: dict[str, int] = {}
        for w in words:
            if w in self.stop or len(w) > 24:
                continue
            freq[w] = freq.get(w, 0) + 1
        ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
        # A word earns a tag by recurring. With no repetition there is no signal,
        # and picking the first few words just decorates a note with noise —
        # "before, billing, dies, every, fixed" tells nobody anything.
        return [w for w, n in ranked if n > 1][:limit]

    # -- the decision -------------------------------------------------------

    def classify(self, path: Path) -> dict:
        name = path.name
        suffix = path.suffix.lower()

        # A symlink is a pointer, not content. Reading through one can walk back
        # into the folder it came from, and *writing* through one drops a file
        # somewhere nobody asked for — a link to a bucket once had the sorter
        # create a README.md inside it, which the scanner then ignored forever.
        # Keep the link itself, follow nothing.
        if path.is_symlink():
            return {"kind": "asset", "domain": "unsorted", "title": titleize(path.stem),
                    "tags": [], "confidence": 10.0, "flags": [],
                    "why": ["a shortcut to somewhere else — kept as-is, not followed"],
                    "scores": {}, "summary": "", "captured": ""}

        title_src, body = self._sample(path)
        meta, stripped = parse_frontmatter(body) if body.lstrip().startswith("---") else ({}, body)
        if path.is_file() and suffix in TEXT_SUFFIXES:
            meta2, stripped2 = parse_frontmatter(read_text(path, 120_000))
            if meta2:
                meta, stripped = meta2, stripped2
        body = stripped if stripped.strip() else body

        verdict = {
            "kind": "", "status": "", "domain": "", "title": "", "tags": [],
            "confidence": 0.0, "why": [], "flags": [],
        }

        # 1. explicit front matter wins outright
        declared = str(meta.get("type", "")).strip().lower()
        if declared in ("project", "work", "area", "ongoing", "note", "asset", "file",
                        "skill", "agent", "log", "reference", "journal"):
            verdict["kind"] = TYPE_FROM_DISK.get(declared, declared)
            verdict["confidence"] += 10
            verdict["why"].append(f"front matter says type: {declared}")
        if verdict["kind"] == "project" and (meta.get("status") or declared):
            verdict["status"] = normalize_status(meta.get("status"), "project", declared)
        if meta.get("domain"):
            verdict["domain"] = slugify(str(meta["domain"]), 24)
            verdict["confidence"] += 4
            verdict["why"].append("front matter says domain")
        if meta.get("title"):
            verdict["title"] = str(meta["title"])
            verdict["title_from_meta"] = True
        elif meta.get("name") and verdict["kind"] in ("skill", "agent"):
            # skills and helpers carry `name:`, never `title:` — and that name is
            # load-bearing: it is the word people type to invoke the thing
            verdict["title"] = titleize(str(meta["name"]))
            verdict["title_from_meta"] = True
        if meta.get("tags"):
            raw = meta["tags"]
            verdict["tags"] = [slugify(str(t), 24) for t in (raw if isinstance(raw, list) else str(raw).split(","))]

        # 2. filename convention: kind--name.ext
        if not verdict["kind"]:
            m = re.match(r"^([a-z]+)--(.+)$", name, re.I)
            if m and m.group(1).lower() in self.NAME_HINTS:
                verdict["kind"] = self.NAME_HINTS[m.group(1).lower()]
                verdict["confidence"] += 8
                verdict["why"].append(f"filename prefix '{m.group(1)}--'")
                if not verdict["title"]:
                    verdict["title"] = titleize(Path(m.group(2)).stem)

        # 3. structural shape
        if not verdict["kind"]:
            if path.is_dir() and (path / "SKILL.md").exists():
                verdict["kind"] = "skill"
                verdict["confidence"] += 10
                verdict["why"].append("folder contains SKILL.md")
            elif path.is_file() and suffix in TEXT_SUFFIXES and meta.get("name") and meta.get("description") \
                    and not meta.get("type"):
                verdict["kind"] = "agent"
                verdict["confidence"] += 6
                verdict["why"].append("agent-shaped front matter (name + description)")
            elif path.is_dir() and any((path / f).exists() for f in CODE_MARKERS):
                verdict["kind"] = "project"
                verdict["confidence"] += 7
                verdict["why"].append("folder looks like a code project")

        if not verdict.get("title_from_meta") and verdict["kind"] in ("skill", "agent") \
                and meta.get("name"):
            verdict["title"] = titleize(str(meta["name"]))
            verdict["title_from_meta"] = True

        # 4a. a folder holding nothing you would read is a folder of files
        if not verdict["kind"] and path.is_dir():
            files = [q for q in own_paths(path) if q.is_file() and not ignored(q)]
            prose = [q for q in files if q.suffix.lower() in TEXT_SUFFIXES
                     and not is_binary(q)]
            if files and not prose:
                verdict["kind"] = "asset"
                verdict["confidence"] += 6
                verdict["why"].append(f"{len(files)} file(s) and nothing to read")

        # Text that is not UTF-8 — an old Windows or Latin-1 file — cannot take
        # a header without every é, à and € in it turning into a '?' box. It is
        # kept exactly as it is, with a card, whatever it says it is; and read
        # the way Windows wrote it, so the card's title has its accents too.
        # Text too long to be a note — a server log, a data dump — is kept the
        # same way: giving it a header meant holding all of it in memory, twice.
        if path.is_file() and suffix in TEXT_SUFFIXES and not is_binary(path) \
                and _size(path) > PROSE_CAP:
            verdict["kind"] = "asset"
            verdict["confidence"] += 6
            verdict["why"].append("text too long to be a note — kept as it is, with a card")
        elif path.is_file() and suffix in TEXT_SUFFIXES and not is_binary(path) \
                and not is_utf8(path):
            verdict["kind"] = "asset"
            verdict["confidence"] += 6
            verdict["why"].append("text in an older format — kept as it is, with a card")
            try:
                with path.open("rb") as fh:
                    body = fh.read(120_000).decode("cp1252", errors="replace")
            except OSError:
                pass

        # 4. extension — and what the bytes actually say
        if not verdict["kind"] and path.is_file():
            if suffix in self.asset_ext or (suffix and suffix not in TEXT_SUFFIXES):
                verdict["kind"] = "asset"
                verdict["confidence"] += 5
                verdict["why"].append(f"'{suffix or 'no extension'}' is not prose")
            elif is_binary(path):
                verdict["kind"] = "asset"
                verdict["confidence"] += 6
                verdict["why"].append("named like text, but the contents are data")
                verdict["title"] = verdict["title"] or titleize(path.stem)
                body = ""

        # 5. content scoring
        domain, dscore, dall = self.score_domain(title_src, body, suffix)
        intent, iscore, iall = self.score_intent(title_src, body)
        if not verdict["domain"] and domain:
            verdict["domain"] = domain
            verdict["why"].append(f"reads as {self.tax['domains'][domain]['label'].lower()} ({dscore:g})")
        if not verdict["kind"]:
            kind, phase = INTENT_RESULT.get(intent, (intent, ""))
            verdict["kind"] = kind
            if phase and not verdict["status"]:
                verdict["status"] = phase
            verdict["confidence"] += iscore
            verdict["why"].append(f"content scores as {INTENT_WORDS.get(intent, intent)} ({iscore:g})")

        # Content scoring can only read the words, and a page of links about
        # editing reads exactly like a piece of work about editing. Anything
        # that *declared* itself work still is: this only overrules a guess.
        if verdict["kind"] == "project" and not declared and self._reference_list(body):
            verdict["kind"] = "note"
            verdict["status"] = ""
            verdict["why"].append("a list of links — something to look up, not to do")
        elif verdict["kind"] == "project" and not declared \
                and self._finished_summary(verdict.get("title") or title_src, body):
            verdict["kind"] = "note"
            verdict["status"] = ""
            verdict["why"].append("an account of work already done — nothing left to push on")

        floor = float(self.os.thresholds.get("min_classify_score", 2.0))
        if verdict["confidence"] < floor:
            verdict["flags"].append("needs-review")
            verdict["why"].append("not sure what this is — kept in Notes so it is easy to spot")
            if verdict["kind"] not in ("asset", "skill", "agent"):
                verdict["kind"] = "note"
                verdict["status"] = ""

        if not verdict["domain"]:
            verdict["domain"] = "unsorted"
        if not verdict["title"]:
            # What they called it, when they called it anything: a folder by
            # its own name, a file of prose by its heading or else its file
            # name. `Wedding Speech/` was renamed after the first line of the
            # notes inside it. Words typed into `os save` have no name but the
            # one this program gave the file, so those go by their first line.
            named = "" if meta.get("saved") else \
                given_name(path.name if path.is_dir() else path.stem)
            if named and (path.is_dir() or suffix in TEXT_SUFFIXES
                          and not re.search(r"^#\s+\S", body, re.M)):
                verdict["title"], verdict["title_given"] = named, True
            else:
                verdict["title"] = infer_title(body, path)
        if not verdict["tags"]:
            verdict["tags"] = self.keywords(title_src, body)
        verdict["confidence"] = round(verdict["confidence"] + dscore * 0.25, 2)
        verdict["scores"] = {"domain": dall, "intent": iall}
        verdict["summary"] = gist(body)
        verdict["captured"] = COMMENT_RE.sub("", body).strip()[:4_000]
        return verdict


# ---------------------------------------------------------------------------
# the scanner
# ---------------------------------------------------------------------------

#: `W.04_ship-the-rewrite` — how folders were named back when things here were
#: numbered. Nothing writes this any more; it is matched only so a folder left
#: over from then still opens, with the tag stripped off the front of its title.
ID_RE = re.compile(r"^([A-Za-z]\.\d{2,4}|\d{1,2}\.\d{2,4})[_ -]+(.*)$")


def id_order(ident: str) -> tuple:
    """Sort key for a leftover tag like `W.04`. Plain names sort last, together.

    Sorting tags as text put N.100 between N.10 and N.11, because "1" sorts
    before "9", so the count is compared as a number instead. Nothing is
    tagged now, so in practice everything falls into the same bucket here and
    whatever sorts alongside this key — the title — does the real work."""
    try:
        tag, item = ident.split(".", 1)
        return (0, f"{int(tag):03d}" if tag.isdigit() else tag, int(item))
    except (ValueError, AttributeError):
        return (1, "", 0)


class Scanner:
    """Walks the tree and returns every item the OS knows about."""

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.classifier = Classifier(os_)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def is_category(path: Path) -> bool:
        """Is this a grouping folder the walker should descend into?

        Never through a symlink. A link in Notes/ pointing at a folder that
        happens to hold a `.category` marker used to make the walker step
        straight out of the Zenith folder and treat somebody's real directory as
        its own — and then `os sort` *moved their files out of it*. A shortcut is
        one item, always: the thing it points at belongs to whoever put it there."""
        return path.is_dir() and not path.is_symlink() and (path / CATEGORY_MARKER).exists()

    @staticmethod
    def spine_of(path: Path) -> Path | None:
        # A shortcut is a pointer, not a document. Reading through one adopts a
        # file that belongs to another item — which then shows up as a duplicate
        # of itself, and would be rewritten in place by the next `os sort`.
        if path.is_symlink():
            return None
        # The card first, whether this is one file or a folder of them. A folder
        # of photos gets a card too, and only looking for one beside a *file*
        # meant that card was never read: the folder's title, subject and tags
        # sat in it, indexed by nothing and findable by no search.
        card = path.with_name(path.name + ".card.md")
        if card.exists():
            return card              # it was kept as-is; the card describes it
        if path.is_file():
            if path.suffix.lower() in TEXT_SUFFIXES:
                return path
            return None
        for candidate in ("README.md", "index.md", "SKILL.md", "AGENT.md"):
            if (path / candidate).exists():
                return path / candidate
        mds = sorted(p for p in path.glob("*.md") if not ignored(p))
        return mds[0] if mds else None

    def hydrate(self, item: Item) -> Item:
        m = ID_RE.match(item.path.name)
        if m:
            item.ident = m.group(1)
        item.spine = self.spine_of(item.path)
        meta: dict = {}
        body = ""
        item.blurb = ""
        if item.spine and item.spine.exists():
            meta, body = parse_frontmatter(read_ends(item.spine))
            item.words = len(body.split())
            if not item.summary:
                item.summary = gist(body)
        item.blurb = str(meta.get("description") or "").strip()
        item.claim = str(meta.get("claimed") or "").strip()
        # There are no numbers any more: filed means the header says what the
        # thing is. The name on disk is the handle every command answers to.
        item.managed = bool(str(meta.get("type") or meta.get("title") or "").strip())
        if not item.managed:
            # A shortcut has no spine on purpose — reading through one adopts a
            # file belonging to somebody else. But the card *beside* it is ours,
            # and without checking for it a filed link looks unfiled forever, so
            # every sort adopted and refiled it again.
            item.managed = item.path.with_name(item.path.name + ".card.md").exists()
        item.ident = item.path.stem if item.path.is_file() else item.path.name
        declared_name = str(meta.get("name") or "").strip()
        item.title = str(meta.get("title")
                         or (titleize(declared_name) if declared_name else "")
                         or titleize(m.group(2) if m else item.path.stem))
        # One shelf now holds prose and files alike, so what a thing *is* comes
        # from its own header; the folder only says where it lives. Without this
        # a PDF filed in Notes would call itself a note in every listing.
        if item.kind in ("note", "project"):
            declared = str(meta.get("type") or "").strip().lower()
            resolved = TYPE_FROM_DISK.get(declared, declared)
            if resolved in ("project", "note", "asset"):
                item.kind = resolved
        item.status = normalize_status(meta.get("status"), item.kind, meta.get("type"))
        raw_domain = str(meta.get("domain") or "").strip()
        item.domain = (slugify(raw_domain, 24) if raw_domain else "") or "unsorted"
        raw_tags = meta.get("tags") or []
        # `tags: 2026` reads as a number and `tags: yes` as true. Walking either
        # as a list stopped every command that scans the folder.
        if not isinstance(raw_tags, (list, str)):
            raw_tags = [] if isinstance(raw_tags, bool) else [str(raw_tags)]
        if isinstance(raw_tags, str):
            raw_tags = [t for t in re.split(r"[,\s]+", raw_tags) if t]
        item.tags = [slugify(str(t), 24) for t in raw_tags][:12]
        item.created = str(meta.get("created") or "")
        item.updated = str(meta.get("updated") or "")
        if not item.created or not item.updated:
            try:
                st = item.path.stat()
                item.created = item.created or _dt.date.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d")
                item.updated = item.updated or _dt.date.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
            except OSError:
                pass
        # A Log line is written by hand, as AGENTS.md and /wrapup ask, and only
        # ./os commands change `updated:`. Work logged today was still called
        # quiet until the newest dated Log line counted as touching it.
        logged = last_logged(body)
        if logged and days_since(logged) < days_since(item.updated):
            item.updated = logged
        item.fingerprint = digest(item.spine) if item.spine else digest(item.path)
        if meta.get("flags"):
            raw = meta["flags"]
            item.flags = list(raw) if isinstance(raw, list) else [str(raw)]
        return item

    # -- the walk -----------------------------------------------------------

    def _walk_bucket(self, bucket: str, node: Path, trail: list[str], out: list[Item], role: str) -> None:
        if not node.exists():
            return
        for child in sorted(node.iterdir()):
            if ignored(child):
                continue
            if self.is_category(child):
                self._walk_bucket(bucket, child, trail + [child.name], out, role)
                continue
            item = Item(child, bucket, role)
            item.trail = list(trail)
            out.append(self.hydrate(item))

    def scan(self) -> list[Item]:
        root = self.os.root
        items: list[Item] = []

        for bucket, spec in self.os.buckets().items():
            role = spec["role"]
            base = root / bucket
            if role == "archive":
                for year in sorted(p for p in base.glob("*") if p.is_dir()):
                    for origin in sorted(p for p in year.iterdir() if p.is_dir()):
                        for f in sorted(origin.iterdir()):
                            if ignored(f):
                                continue
                            it = Item(f, bucket, "archive")
                            it.trail = [year.name, origin.name]
                            items.append(self.hydrate(it))
                continue
            self._walk_bucket(bucket, base, [], items, role)

        items.extend(self.scan_toolkit())

        # repair the leftover counters if state.json was lost — see next_id
        by_bucket: dict[str, set] = {}
        for it in items:
            if it.ident:
                by_bucket.setdefault(it.bucket, set()).add(it.ident)
        for bucket, idents in by_bucket.items():
            if bucket in self.os.buckets():
                self.os.reserve_id_at_least(bucket, idents)
        return items

    def scan_toolkit(self) -> list[Item]:
        root, out = self.os.root, []
        skills = root / ".claude" / "skills"
        agents = root / ".claude" / "agents"
        hooks = root / ".claude" / "hooks"
        for d in sorted(p for p in skills.glob("*") if p.is_dir()) if skills.exists() else []:
            if ignored(d) or not (d / "SKILL.md").exists():
                continue
            it = Item(d, TOOLKIT, "skill")
            it.trail = ["skills"]
            it = self.hydrate(it)
            it.title = it.title or titleize(d.name)
            out.append(it)
        for f in sorted(agents.glob("*.md")) if agents.exists() else []:
            if ignored(f):
                continue
            it = Item(f, TOOLKIT, "agent")
            it.trail = ["agents"]
            out.append(self.hydrate(it))
        for f in sorted(hooks.glob("*")) if hooks.exists() else []:
            if ignored(f) or f.is_dir():
                continue
            it = Item(f, TOOLKIT, "hook")
            it.trail = ["hooks"]
            it.title = titleize(f.stem)
            it.domain = "operations"
            it.status = "—"
            out.append(it)
        return out


# ---------------------------------------------------------------------------
# the sorter — adoption, naming, self-balancing categories
# ---------------------------------------------------------------------------

#: Every kind of thing the person owns, and the folder role it lives under.
#: A file is a note with bytes instead of prose — it goes to the same shelf and
#: gets a card beside it, so it is searchable like everything else.
ROLE_FOR_KIND = {
    "project": "project", "note": "note", "asset": "note",
}

#: The two live phases of work. Whether something will ever finish is a
#: prediction, and the person is asked for it at the moment they know least —
#: so the folder does not ask. It asks what the thing needs *now*: a next
#: action (pushing), or a standard held level (holding). The same item moves
#: between the two, repeatedly, which is exactly why this is a status and not
#: a folder.
PUSHING, HOLDING = "pushing", "holding"

#: Words that used to mean these, in files written before the merge, and the
#: ones people type. Anything not here is left alone and shown as written.
STATUS_ALIASES = {
    "active": PUSHING, "in-progress": PUSHING, "in progress": PUSHING,
    "open": PUSHING, "doing": PUSHING, "started": PUSHING,
    "ongoing": HOLDING, "maintained": HOLDING, "area": HOLDING,
    "shipped": "done", "complete": "done", "completed": "done", "closed": "done",
}

#: Live work, and work that has left. `""` counts as live: an item somebody
#: hand-wrote without a status is still theirs.
LIVE_STATUSES = (PUSHING, HOLDING, "")
CLOSED_STATUSES = ("done", "archived", "dropped", "parked")


#: `type:` words from before Projects and Ongoing merged.
LEGACY_HOLDING_TYPES = {"area", "ongoing"}


def normalize_status(raw: str, kind: str, declared_type: str = "") -> str:
    """One vocabulary for work, whatever the file happens to say.

    A file written before the merge says `type: ongoing` and `status: active`,
    where "active" meant *being kept up* — the opposite of what it means now.
    Read those as held, or every ongoing thing somebody already had would come
    back as work on the go, and immediately start being nagged for going quiet."""
    word = str(raw or "").strip().lower()
    if kind != "project":
        return str(raw or "").strip() or "—"
    if str(declared_type).strip().lower() in LEGACY_HOLDING_TYPES \
            and word in ("", "active", "in-progress", "open"):
        return HOLDING
    return STATUS_ALIASES.get(word, word) or PUSHING


#: What `type:` says in a file the person might open. The engine still thinks
#: in "project" and "asset"; the folders say Work and Files, so the files do too.
TYPE_ON_DISK = {"project": "work", "asset": "file"}
#: Words older folders wrote into `type:`. `log` and `journal` had a folder of
#: their own once; they read as notes now, which is what they always were.
TYPE_FROM_DISK = {"work": "project", "ongoing": "project", "area": "project",
                  "file": "asset", "reference": "note",
                  "log": "note", "journal": "note"}


class Sorter:
    """Turns a messy folder into a sorted one, reversibly.

    Three passes:
      1. adopt   — anything the OS never filed is classified, named, stamped
                   and moved: staged captures from `os save`, and anything
                   dropped straight into a bucket by hand
      2. identify— every managed item's name on disk is made to match its
                   title, and its header restamped with what search needs
      3. balance — categories appear when a bucket earns them and collapse
                   when it no longer needs them (with hysteresis, so a bucket
                   hovering at the threshold does not thrash)
    """

    def __init__(self, os_: "Zenith", dry: bool = False):
        self.os = os_
        self.dry = dry
        self.scanner = Scanner(os_)
        self.classifier = self.scanner.classifier
        self.creator = Creator(os_)
        self.moves: list[tuple[str, str, str]] = []   # (what, from, to)
        self.notes: list[str] = []
        self.skipped: list[tuple[str, str]] = []      # (path, why it could not be filed)
        self.waiting: list[str] = []                   # empty and seconds old: left for the next run
        self.taken_back: list[str] = []                # put back by ./os undo: left alone on purpose

    # -- pass 1: staged captures, and anything dropped in by hand ------------

    def _take(self, src: Path, here: bool = False) -> Path | None:
        """Classify one unfiled thing and put it where it belongs."""
        verdict = self.classifier.classify(src)
        try:
            dest = self.place(src, verdict, here)
        except OSError as exc:
            # One thing going missing is not a reason to abandon the other
            # forty-nine — but it is never swallowed: reporting "nothing
            # waiting" while something is still waiting is a lie.
            self.skipped.append((self.os.rel(src), str(exc)))
            return None
        if dest is not None:
            why = verdict["why"][0] if verdict["why"] else "classified"
            self.moves.append((verdict["kind"], self.os.rel(src), self.os.rel(dest)))
            self.notes.append(f"{verdict['kind']}: {why}")
        return dest

    def file_staged(self) -> int:
        """File anything left in the staging area under .os/cache/.

        `os save` stages, classifies and moves in one breath, so this is
        normally empty. It exists for the run that died between the two."""
        stage = self.os.dot / "cache" / STAGING
        if not stage.exists():
            return 0
        # Undoing a save puts the words back here, where they started. The next
        # sort used to file them straight out again, so two items undone on
        # 2026-09-08 came back on their own and had to be closed to make it
        # stick. Something the person took back is not something waiting.
        refused = self.os.taken_back()
        filed = 0
        for child in sorted(stage.iterdir()):
            if ignored(child):
                continue
            # Not a failure to file, so not in `skipped`: counted there, it made
            # sort exit 1 and `./os` nag about it on every run after.
            if self.os.rel(child) in refused:
                self.taken_back.append(self.os.rel(child))
                continue
            if self._take(child) is not None:
                filed += 1
        return filed

    #: A prose file changed this recently is probably mid-write.
    SETTLE_SECONDS = 20

    @classmethod
    def _still_being_written(cls, path: Path) -> bool:
        """A text file touched seconds ago, with nothing in it yet, is mid-write.

        A sort ran while another session was still filling a fresh note, and
        adopted the shell of it: the note was filed twice, and an empty copy
        was left beside the real one (snag, 2026-08-31). A file with words in
        it is filed however new it is — people drop things in and sort at
        once — so only the empty, seconds-old case waits for the next run."""
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            return False
        try:
            if (time.time() - path.stat().st_mtime) >= cls.SETTLE_SECONDS:
                return False
            _meta, body = parse_frontmatter(read_text(path, 4_000))
        except OSError:
            return False
        # A heading alone is a name, not content.
        return not re.sub(r"^\s*#.*$", "", body, flags=re.M).strip()

    @staticmethod
    def unmanaged(it: "Item") -> bool:
        """Is this something the OS has never taken charge of?

        Ours iff its own header says what it is — the name on the file is not
        enough, because anybody can type one. A bare PDF with no card beside it
        has no header at all, and so is nobody's."""
        return it.kind in ("project", "note", "asset") and not it.managed

    def adopt(self, items: list["Item"]) -> int:
        """Take charge of anything sitting in a bucket that the OS never filed.

        With no drop folder, the natural move is to drag a PDF straight into
        Notes//. Left alone that is a file with no card — no subject, no tags,
        and nothing for a search to match on. So the sorter adopts it
        where it lies, rather than asking the person to have put it somewhere
        special first. A loose file still goes where it belongs: a PDF dropped
        into Work/ is a thing you look up later wherever you put it. A folder
        somebody made stays where they made it, under their name for it."""
        taken = 0
        todo = [(it.path, True) for it in items
                if it.bucket in self.os.buckets() and self.unmanaged(it)]
        # A file left at the top of the folder lies in no bucket at all, so it
        # goes where it belongs, the same as a file that was saved.
        todo += [(path, False) for path in loose_at_top(self.os.root)]
        for path, here in todo:
            if self._still_being_written(path):
                self.waiting.append(self.os.rel(path))
                continue
            if self.dry:
                verdict = self.classifier.classify(path)
                dest = self.place(path, verdict, here=here)
                if dest is not None:
                    self.moves.append((verdict["kind"], self.os.rel(path),
                                       self.os.rel(dest)))
                    taken += 1
                continue
            if self._take(path, here=here) is not None:
                taken += 1
        return taken

    def file_one(self, src: Path) -> tuple[Path | None, dict]:
        """Classify and place exactly one thing.

        `os save` uses this so nothing is ever left staged waiting for the
        person to remember a second command."""
        verdict = self.classifier.classify(src)
        dest = self.place(src, verdict)
        if dest is not None:
            self.moves.append((verdict["kind"], self.os.rel(src), self.os.rel(dest)))
        return dest, verdict

    def place(self, src: Path, verdict: dict, here: bool = False) -> Path | None:
        """Put one thing where it belongs. `here`: it was dropped into a
        bucket by hand, rather than saved."""
        kind = verdict["kind"]
        slug = slugify(verdict["title"] or src.stem, 44)
        root = self.os.root

        if kind == "skill":
            dest_dir = root / ".claude" / "skills" / slug
            if self.dry:
                return dest_dir
            if src.is_dir():
                moved = self.os.move(src, dest_dir)
            else:
                self.os.make_dir(dest_dir)
                moved = self.os.move(src, dest_dir / "SKILL.md")
                moved = dest_dir
            self._ensure_skill(moved, verdict)
            return moved

        if kind == "agent":
            dest = root / ".claude" / "agents" / f"{slug}.md"
            if self.dry:
                return dest
            if src.is_dir():
                inner = self.scanner.spine_of(src)
                if inner is None:
                    kind = verdict["kind"] = "asset"
                else:
                    moved = self.os.move(inner, dest)
                    self._ensure_agent(moved, verdict)
                    if not any(p for p in src.iterdir() if not ignored(p)):
                        shutil.rmtree(src, ignore_errors=True)
                    return moved
            else:
                moved = self.os.move(src, dest)
                self._ensure_agent(moved, verdict)
                return moved

        # A folder is somebody's own: its files are never rewritten (see
        # _ensure_spine). One they made by hand is also adopted where it lies —
        # the bucket they put it in says what it is, and a name they chose is
        # its name. `Work/Wedding Speech` became `Notes/venue-the-old-barn`,
        # after the first line of the notes inside it.
        theirs = src.is_dir() and not src.is_symlink()
        # Footage, and anything too big to read, lives in Work/Content, which
        # ./os never files, nags or renames. Saved, 300 MB of video went into
        # Notes beside the prose.
        if kind == "asset" and not here and not src.is_symlink() and big_media(self.os, src):
            dest = unique_path(root / self.os.bucket_for_role("project") / MEDIA_FOLDER / src.name)
            return dest if self.dry else self.os.move(src, dest)
        keep = False
        if here and theirs and kind in ROLE_FOR_KIND:
            lies = self.os.rel(src).split("/", 1)[0]
            role = self.os.buckets().get(lies, {}).get("role")
            if role == "project" and kind != "project":
                kind = verdict["kind"] = "project"
            elif role == "note" and kind == "project":
                kind = verdict["kind"] = "note"
                verdict["status"] = ""
            # Where it goes was their call, so nobody was guessing.
            verdict["flags"] = [f for f in verdict.get("flags", []) if f != "needs-review"]
            keep = bool(verdict.get("title_given"))

        bucket = self.os.bucket_for_role(ROLE_FOR_KIND.get(kind, "note"))
        ident = f"{self.os.buckets()[bucket]['code']}.??" if self.dry else self.os.next_id(bucket)
        base = root / bucket

        # Names are plain words, and the name is the handle: nothing is tagged.
        # A name ./os never looks inside (Content, README.md) is taken too: work
        # called "Content" was made inside the media folder, and vanished.
        def free(name: str) -> Path:
            cand = base / name
            # Adopting a file where it already lies is not a move — without
            # this it went to `name-2` and straight back again.
            if cand == src or not (cand.exists() or ignored(cand)):
                return cand
            suf = cand.suffix
            stem = cand.name[: -len(suf)] if suf else cand.name
            n = 2
            while (base / f"{stem}-{n}{suf}").exists():
                n += 1
            return base / f"{stem}-{n}{suf}"

        def move_to(dest: Path) -> Path:
            return src if dest == src else self.os.move(src, dest)

        if kind == "project":
            dest_dir = src if keep else free(folder_name(verdict["title"], slug))
            if self.dry:
                return dest_dir
            if src.is_dir():
                moved = move_to(dest_dir)
            else:
                self.os.make_dir(dest_dir)
                inner = dest_dir / ("README.md" if src.suffix.lower() in TEXT_SUFFIXES else src.name)
                self.os.move(src, inner)
                moved = dest_dir
            self._ensure_spine(moved, ident, verdict, kind, theirs)
            return moved

        if kind == "asset":
            suffix = src.suffix if src.is_file() else ""
            dest = src if keep else free(f"{slug}{suffix}")
            if self.dry:
                return dest
            moved = move_to(dest)
            self._sidecar(moved, ident, verdict)
            return moved

        # note
        if src.is_dir():
            dest = src if keep else free(slug)
            if self.dry:
                return dest
            moved = move_to(dest)
            self._ensure_spine(moved, ident, verdict, "note", theirs)
            return moved
        dest = free(f"{slug}.md")
        if self.dry:
            return dest
        if src.suffix.lower() not in TEXT_SUFFIXES:
            dest = free(f"{slug}{src.suffix}")
        moved = self.os.move(src, dest)
        if moved.suffix.lower() in TEXT_SUFFIXES:
            stamp_file(moved, {"title": verdict["title"], "type": "note",
                               "status": "—", "domain": verdict["domain"],
                               "tags": verdict["tags"], "created": today(),
                               "summary": verdict.get("summary", "")})
            self._finish_header(moved, verdict)
        else:
            # It reads as prose but we cannot write a header into it — a file
            # with no extension at all, most often. Give it a card, the same as
            # any other file. Without one it carries no header anywhere, so
            # every later sort saw it as unfiled and filed it again: one run
            # left `no-extension-2-2` behind, having moved it twice.
            self._sidecar(moved, ident, verdict)
        return moved

    @staticmethod
    def _finish_header(spine: Path, verdict: dict) -> None:
        """Tidy up a freshly filed item's header.

        `saved:` is a capture artefact and has no business surviving. A
        `needs-review` flag is the opposite — it is the only record that the
        sorter was guessing, and `os tidy` is where somebody goes to find it."""
        text = read_utf8(spine)
        meta, _body = parse_frontmatter(text or "")
        if not meta:
            return
        flags = {"flags": verdict["flags"]} if verdict.get("flags") else {}
        if meta.get("saved") or flags:
            write_text(spine, set_fields(text, flags, drop=("saved",)))

    # -- writing the spine --------------------------------------------------

    def _ensure_spine(self, folder: Path, ident: str, verdict: dict, kind: str,
                      theirs: bool = False) -> None:
        """Give a project or an ongoing thing the same shape `os new` gives it.

        Most of these are born from `os save`, where the captured text simply
        becomes the README. Left alone that produces a project with no next
        action, no decisions and no log — nothing the rest of the system, or an
        AI following AGENTS.md, can actually work with. So: keep every word the
        person wrote, and build the structure around it.

        `theirs`: the folder came as it is, and every file in it is somebody
        else's. The shape then goes in a README.md of our own, or on a card
        beside the folder when it has a README already or is a code project —
        a code project's README once gained a header and five empty sections,
        which git showed as changed and the next push would have published."""
        spine = self.scanner.spine_of(folder) or (folder / "README.md")
        if theirs:
            code = any((folder / f).exists() for f in CODE_MARKERS)
            spine = folder.with_name(folder.name + ".card.md") \
                if code or (folder / "README.md").exists() else folder / "README.md"
        already_there = spine.exists()
        existing, has_shape, head = "", False, ""
        if theirs:
            has_shape = already_there      # ours from before: only the header is stamped
        elif already_there:
            text = read_utf8(spine)
            if text is None:
                # Not UTF-8: see stamp_file. It is kept as it is, with a card.
                self._sidecar(folder, ident, verdict)
                return
            _, existing = parse_frontmatter(text)
            # Its own header stays on it, every line: a file saved with
            # `client:` and `due:` in it lost both when it became work.
            head = text[:len(text) - len(existing)]
            has_shape = bool(re.search(r"^##\s+", existing, re.M))
            if not has_shape:
                existing = re.sub(r"^#\s+.*$", "", existing, count=1, flags=re.M).strip()
            elif not verdict.get("title_from_meta"):
                heading = re.search(r"^#\s+(.+?)\s*$", existing, re.M)
                if heading:
                    verdict["title"] = heading.group(1).strip()[:90]

        # Something the person already shaped is theirs. Stamp the header so it
        # is findable, and touch nothing below it.
        if not has_shape:
            # Their words stay in their own files; a gist of them is enough here.
            body = existing or str(("" if theirs else verdict.get("captured"))
                                   or verdict.get("summary") or "").strip()
            _, blueprint = parse_frontmatter(
                self.creator.render(kind, verdict["title"], ident,
                                    verdict["domain"], verdict["tags"],
                                    verdict.get("status", "")))
            blueprint = blueprint.lstrip("\n")
            step = "" if theirs else first_step(body)
            if step:
                blueprint = re.sub(r"^(##\s+Next action[ \t]*\n[-*][ \t]*\[ \])[ \t]*$",
                                   lambda m: m.group(1) + " " + step, blueprint,
                                   count=1, flags=re.M)
            if body:
                title_line, _, rest = blueprint.partition("\n")
                blueprint = title_line + "\n\n" + body.strip() + "\n" + rest
            write_text(spine, head + blueprint)
            # Only a file this run brought into existence may be removed by undo.
            # Recording a create for one that was moved here makes undo delete it
            # before it can be moved back.
            if not already_there:
                self.os.created(spine)
        # No empty notes/ or assets/ scaffolding: a project's subfolders appear
        # when something goes in them, never before.
        stamp_file(spine, {
            "title": verdict["title"], "type": TYPE_ON_DISK.get(kind, kind),
            "status": verdict.get("status") or (PUSHING if kind == "project" else "—"),
            "domain": verdict["domain"], "tags": verdict["tags"],
            "created": today(), "summary": verdict.get("summary", ""),
        })
        self._finish_header(spine, verdict)

    def _sidecar(self, asset: Path, ident: str, verdict: dict) -> None:
        card = asset.with_name(asset.name + ".card.md")
        if card.exists():
            return
        write_text(card, compose({
            "title": verdict["title"], "type": "file",
            "status": "—", "domain": verdict["domain"], "tags": verdict["tags"],
            "created": today(), "source": asset.name,
        }, f"# {verdict['title']}\n\nAsset card for `{asset.name}`.\n\n{verdict.get('summary','')}\n"))
        self.os.created(card)

    def _ensure_skill(self, folder: Path, verdict: dict) -> None:
        skill = folder / "SKILL.md"
        if not skill.exists():
            write_text(skill, f"---\nname: {folder.name}\ndescription: {verdict['title']}\n---\n\n{verdict.get('summary','')}\n")
            self.os.created(skill)
            return
        self._name_it(skill, folder.name, verdict)

    def _ensure_agent(self, path: Path, verdict: dict) -> None:
        self._name_it(path, slugify(verdict["title"]), verdict)

    @staticmethod
    def _name_it(path: Path, name: str, verdict: dict) -> None:
        """Give a skill or a helper the name and description it is missing."""
        text = read_utf8(path)
        if text is None:
            return
        meta, _ = parse_frontmatter(text)
        missing = {k: v for k, v in (("name", name), ("description", verdict.get("summary")
                                                      or verdict["title"])) if k not in meta}
        if missing:
            write_text(path, set_fields(text, missing))

    # -- pass 2: identify ---------------------------------------------------

    def identify(self, items: list[Item]) -> int:
        # There are no numbers any more: a thing is known by its name. This
        # pass only makes sure the name on disk matches the title, and that the
        # header carries what search needs.
        fixed = 0
        for it in items:
            if it.kind not in ("project", "note", "asset"):
                continue
            # Anything adopt() chose to leave alone stays alone: stamping a
            # header onto it here would file it without ever classifying it.
            if not it.managed:
                continue
            # A headed file counts as filed, but its header can still disagree
            # with where it sits — a hand-dropped folder saying `type: work`
            # left in Notes is work, so it belongs in Work.
            home = self.os.bucket_for_role(ROLE_FOR_KIND.get(it.kind, "note"))
            if it.bucket != home:
                dest = self.os.root / home / it.path.name
                n = 2
                while dest.exists() or ignored(dest):
                    dest = self.os.root / home / f"{it.path.stem}-{n}{it.path.suffix if it.path.is_file() else ''}"
                    n += 1
                if self.dry:
                    self.moves.append(("id", self.os.rel(it.path), self.os.rel(dest)))
                    fixed += 1
                    continue
                new = self.os.move_item(it.path, dest)
                self.moves.append(("id", self.os.rel(it.path), self.os.rel(new)))
                it.path, it.bucket, it.trail = new, home, []
                it.spine = self.scanner.spine_of(new)
                fixed += 1
            slug = slugify(it.title or it.path.stem, 44)
            suffix = it.path.suffix if it.path.is_file() else ""
            # A folder is read as a name, so it is spelled like one — "Q3 OKR
            # Review", not q3-okr-review. Notes stay kebab-case files. Any
            # folder that already slugs down to its own title is right however
            # it is spaced or cased, so it is left exactly as it sits.
            is_dir = not it.path.is_file()
            if is_dir and slugify(it.path.name, 44) == slug:
                wanted = it.path.name
            elif is_dir:
                wanted = folder_name(it.title, slug)
            else:
                wanted = f"{slug}{suffix}"
            target = it.path.with_name(wanted)
            n = 2
            while target != it.path and (target.exists() or ignored(target)):
                target = it.path.with_name(f"{wanted}-{n}" if is_dir
                                          else f"{slug}-{n}{suffix}")
                n += 1
            if target != it.path:
                if self.dry:
                    self.moves.append(("id", self.os.rel(it.path), self.os.rel(target)))
                    fixed += 1
                    continue
                new = self.os.move_item(it.path, target)
                self.moves.append(("id", self.os.rel(it.path), self.os.rel(new)))
                it.path = new
                # the spine moved with it; stamping the old path writes nowhere
                it.spine = self.scanner.spine_of(new)
                fixed += 1
            if not self.dry and it.spine and it.spine.exists():
                stamp_file(it.spine, {"title": it.title,
                                      "type": TYPE_ON_DISK.get(it.kind, it.kind),
                                      "domain": it.domain, "tags": it.tags})
            it.ident = target.stem if it.path.is_file() else target.name
        return fixed

    # -- pass 3: balance ----------------------------------------------------

    def _cluster_key(self, group: list[Item], depth_cap: int) -> dict[str, str]:
        """Assign each item in `group` a second-level folder, deterministically."""
        tally: dict[str, int] = {}
        for it in group:
            for t in it.tags[:5]:
                if t and t != "unsorted":
                    tally[t] = tally.get(t, 0) + 1
        floor = max(2, len(group) // 8)
        ranked = [t for t, n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])) if n >= floor]
        ranked = ranked[: min(9, depth_cap)]
        mapping: dict[str, str] = {}
        for it in group:
            for t in ranked:
                if t in it.tags[:5]:
                    mapping[str(it.path)] = titleize(t)
                    break
        return mapping

    def target_trails(self, items: list[Item]) -> dict[str, list[str]]:
        """The trail every item *should* have, given the current population."""
        split = int(self.os.thresholds.get("category_split", 12))
        collapse = max(2, int(split * 0.6))
        max_cats = int(self.os.thresholds.get("max_categories_per_bucket", 9))
        labels = {k: v["label"] for k, v in self.os.taxonomy["domains"].items()}
        plan: dict[str, list[str]] = {}

        for bucket, spec in self.os.buckets().items():
            if not spec.get("categorize"):
                continue
            pool = [it for it in items if it.bucket == bucket]
            if not pool:
                continue
            currently_split = any(it.trail for it in pool)
            should_split = len(pool) > split if not currently_split else len(pool) >= collapse
            if not should_split:
                for it in pool:
                    plan[str(it.path)] = []
                continue

            groups: dict[str, list[Item]] = {}
            for it in pool:
                label = labels.get(it.domain, titleize(it.domain or "Unsorted"))
                groups.setdefault(label, []).append(it)

            # too many thin groups? keep only the biggest, pool the rest
            ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            keep = {name for name, g in ordered[:max_cats] if len(g) >= 2}
            for name, group in groups.items():
                category = name if name in keep else "General"
                for it in group:
                    plan[str(it.path)] = [category]

            # second level, only where a single category is itself crowded
            regrouped: dict[str, list[Item]] = {}
            for it in pool:
                regrouped.setdefault(plan[str(it.path)][0], []).append(it)
            for cat, group in regrouped.items():
                nested_now = any(len(it.trail) > 1 for it in group)
                crowded = len(group) > split if not nested_now else len(group) >= collapse
                if not crowded:
                    continue
                mapping = self._cluster_key(group, max_cats)
                for it in group:
                    sub = mapping.get(str(it.path))
                    if sub:
                        plan[str(it.path)] = [cat, sub]
        return plan

    def balance(self, items: list[Item]) -> int:
        plan = self.target_trails(items)
        moved = 0
        for it in items:
            want = plan.get(str(it.path))
            if want is None or want == it.trail:
                continue
            dest_dir = self.os.root / it.bucket
            for part in want:
                dest_dir = dest_dir / part
            target = dest_dir / it.path.name
            self.moves.append(("sort", self.os.rel(it.path), self.os.rel(target)))
            moved += 1
            if self.dry:
                continue
            self._make_category(dest_dir, want)
            new = self.os.move_item(it.path, target)
            it.path, it.trail = new, list(want)
        if not self.dry:
            self.prune_categories()
        return moved

    def _make_category(self, path: Path, trail: list[str]) -> None:
        """Create and mark *every* level of the trail.

        Marking only the deepest folder leaves the levels above it looking like
        ordinary items, which hides everything beneath them from the index."""
        base = path
        for _ in trail:
            base = base.parent
        for depth in range(1, len(trail) + 1):
            node = base
            for part in trail[:depth]:
                node = node / part
            self.os.make_dir(node)
            marker = node / CATEGORY_MARKER
            if marker.exists():
                continue
            write_text(marker, json.dumps({
                "name": trail[depth - 1],
                "trail": trail[:depth],
                "auto": True,
                "created": today(),
                "engine": ENGINE_VERSION,
            }, indent=2) + "\n")

    def prune_categories(self) -> int:
        removed = 0
        for bucket, spec in self.os.buckets().items():
            if not spec.get("categorize"):
                continue
            base = self.os.root / bucket
            if not base.exists():
                continue
            for path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if not Scanner.is_category(path):
                    continue
                leftovers = [p for p in path.iterdir() if not ignored(p) and p.name != CATEGORY_MARKER]
                if leftovers:
                    continue
                shutil.rmtree(path, ignore_errors=True)
                self.os.record("rmdir", self.os.rel(path))
                removed += 1
        return removed

    # -- the whole run ------------------------------------------------------

    def run(self) -> dict:
        # Read the tree first: what is already on disk decides what a new name
        # is allowed to be, so nothing can land on top of something else.
        items = self.scanner.scan()
        filed = self.file_staged() + self.adopt(items)
        items = self.scanner.scan()
        identified = self.identify(items)
        if not self.dry:
            items = self.scanner.scan()
        balanced = self.balance(items)
        pruned = 0 if self.dry else self.prune_categories()
        if not self.dry:
            self.os.save_state()
        return {"filed": filed, "identified": identified, "balanced": balanced,
                "pruned": pruned, "moves": self.moves, "notes": self.notes,
                "waiting": self.waiting, "taken_back": self.taken_back,
                "skipped": [{"path": path, "why": why} for path, why in self.skipped]}


# ---------------------------------------------------------------------------
# the indexer — registry, human maps, dashboard
# ---------------------------------------------------------------------------

GENERATED = "<!-- written by Zenith. Don't edit by hand — it gets overwritten. -->"


def md_cell(text: str, link_text: bool = False) -> str:
    """Text that will sit in a markdown table, and mean what it says.

    A title reading `Bench | results [draft]` is ordinary English and a broken
    table row: the pipe opens a column that is not there, and the brackets eat
    the link. Nobody should have to avoid punctuation to be indexed."""
    out = one_line(text).replace("|", "\\|")
    return out.replace("[", "\\[").replace("]", "\\]") if link_text else out


#: What breaks a link target once markdown has read it. The space was always
#: here. The rest arrived with a file somebody had named across two lines: the
#: newline ended the table row halfway through the link, so that row and the one
#: after it both stopped being rows. Percent-encoding is the escape markdown
#: understands, and it leaves anything not listed — accents, kanji — readable.
_LINK_ESCAPES = {" ": "%20", "\t": "%09", "\n": "%0A", "\r": "%0D",
                 "(": "%28", ")": "%29", "|": "%7C", "<": "%3C", ">": "%3E",
                 '"': "%22", "'": "%27", "`": "%60"}


def md_link(target: str) -> str:
    """A path that is still one link, pointing where it did, after markdown.

    Escaped rather than flattened: `one_line` would turn the newline in the
    name into a space and quietly aim the link at a file that does not
    exist."""
    return "".join(_LINK_ESCAPES.get(ch, ch) for ch in str(target))

HOOK_BLURBS = {
    "keep-the-record.sh": "Asks you first when a rewrite would lose decisions or log lines.",
    "session-start.sh": "Tells your AI where things stand, before you say anything.",
    "mark-dirty.sh": "Notices when a file changed.",
    "settle.sh": "Re-reads the folder when you stop typing, so search stays current.",
}


class Indexer:
    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.scanner = Scanner(os_)

    def build(self) -> dict:
        items = self.scanner.scan()
        self.os.save_state()
        registry = {
            "engine": ENGINE_VERSION,
            "name": self.os.config.get("name", "Zenith"),
            "root": str(self.os.root),
            "generated": now_iso(),
            "counts": self._counts(items),
            "buckets": {},
            "items": [it.as_dict(self.os.root) for it in items],
        }
        for bucket, spec in self.os.buckets().items():
            pool = [it for it in items if it.bucket == bucket]
            cats: dict[str, int] = {}
            for it in pool:
                key = " / ".join(it.trail) if it.trail else "—"
                cats[key] = cats.get(key, 0) + 1
            registry["buckets"][bucket] = {
                "label": spec["label"], "role": spec["role"], "blurb": spec["blurb"],
                "count": len(pool), "categories": cats,
            }
        write_text(self.os.dot / "registry.json", json.dumps(registry, indent=2) + "\n")
        self.write_index(items, registry)
        self.write_catalog(items)
        return registry

    @staticmethod
    def _counts(items: list[Item]) -> dict:
        counts: dict[str, int] = {}
        for it in items:
            counts[it.kind] = counts.get(it.kind, 0) + 1
        counts["total"] = len(items)
        return counts

    # -- INDEX.md -----------------------------------------------------------

    def write_index(self, items: list[Item], registry: dict) -> None:
        cfg = self.os.config
        yours = len([i for i in items if i.bucket != TOOLKIT])
        lines = [
            GENERATED,
            f"# {cfg.get('name', 'Zenith')}",
            "",
            "Everything in this folder, in one list. Rebuilt automatically.",
            "",
            f"{yours} thing{'' if yours == 1 else 's'} · "
            f"last updated {registry['generated'][:16].replace('T', ' ')}",
            "",
            "| Folder | What's in it | How many |",
            "| --- | --- | --- |",
        ]
        for bucket, spec in self.os.buckets().items():
            lines.append(f"| **{bucket}** | {md_cell(spec['blurb'])} | "
                         f"{registry['buckets'][bucket]['count']} |")

        for bucket, spec in self.os.buckets().items():
            pool = [it for it in items if it.bucket == bucket]
            if not pool:
                continue
            lines += ["", f"## {bucket}", "", f"_{spec['blurb']}_", ""]
            groups: dict[str, list[Item]] = {}
            for it in pool:
                groups.setdefault(" / ".join(it.trail) if it.trail else "", []).append(it)
            for cat in sorted(groups, key=lambda c: (c == "", c)):
                group = sorted(groups[cat], key=lambda i: (id_order(i.ident), i.title.lower()))
                if cat:
                    lines += [f"### {cat}", ""]
                lines += ["| Name | State | Last touched |",
                          "| --- | --- | --- |"]
                for it in group:
                    link = md_link(relative_to_root(it.path, self.os.root)
                                   if it.path.exists() else "")
                    lines.append(f"| [{md_cell(it.title, link_text=True)}]({link}) | "
                                 f"{md_cell(it.status or '—')} | {it.updated or '—'} |")
                lines.append("")

        tools = [it for it in items if it.bucket == TOOLKIT]
        if tools:
            skills = len([i for i in tools if i.kind == "skill"])
            agents = len([i for i in tools if i.kind == "agent"])
            lines += ["", "## Extras", "",
                      f"_{skills} skills and {agents} helpers live in `.claude/` — "
                      "see [CATALOG.md](.claude/CATALOG.md). Everything works without "
                      "them too, through `./os`._", ""]
        write_text(self.os.root / "INDEX.md", "\n".join(lines).rstrip() + "\n")

    # -- toolkit catalog ----------------------------------------------------

    def write_catalog(self, items: list[Item]) -> None:
        skills = [i for i in items if i.kind == "skill"]
        agents = [i for i in items if i.kind == "agent"]
        hooks = [i for i in items if i.kind == "hook"]
        lines = [
            GENERATED, "# What this folder can do", "",
            "Extras for Claude Code. Type a `/name` **in the chat** (not the terminal) "
            "to run a skill. Helpers get sent off on their own when a job suits them. "
            "None of this is required — `./os help` is the plain version, and it works "
            "in any terminal with or without an AI.", "",
        ]

        def block(title: str, pool: list[Item], mark: str, lead: str) -> list[str]:
            if not pool:
                return []
            out = [f"## {title}", "", lead, "", "| | What it does |", "| --- | --- |"]
            for it in sorted(pool, key=lambda i: i.path.name.lower()):
                name = it.path.stem if it.path.is_file() else it.path.name
                desc = (it.blurb or it.summary or it.title or "")
                # A description reads "<what it does>. Use when <trigger words>."
                # The trigger half exists to make the model fire; a person reading
                # this page only wants the first half.
                desc = re.split(r"\.\s+(?:Use|Delegate)\b", desc, maxsplit=1)[0]
                out.append(f"| `{mark}{name}` | "
                           f"{_shorten(desc.replace('|', '/'), 128, '…')} |")
            return out + [""]

        lines += block("Skills", skills, "/", "Type these in the chat, or just ask for them in your own words.")
        lines += block("Helpers", agents, "@", "Picked automatically when a job is big enough to deserve its own context.")
        if hooks:
            lines += ["## Automatic", "", "These run on their own. You never call them.",
                      "", "| File | |", "| --- | --- |"]
            for it in sorted(hooks, key=lambda i: i.path.name):
                lines.append(f"| `{it.path.name}` | {HOOK_BLURBS.get(it.path.name, '—')} |")
            lines.append("")
        write_text(self.os.root / ".claude" / "CATALOG.md", "\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# the doctor — health, and safe repair
# ---------------------------------------------------------------------------

RESERVED_COMMANDS = {
    "help", "clear", "compact", "config", "cost", "doctor", "exit", "init",
    "login", "logout", "memory", "model", "permissions", "resume", "review",
    "status", "vim", "code-review", "batch", "debug", "loop", "claude-api",
    "run", "verify", "skill-doctor", "agents", "skills", "hooks", "context",
    "rewind", "usage", "feedback", "add-dir", "mcp", "plugin", "output-style",
}

LEVELS = {"error": 3, "warn": 2, "hint": 1}


def numbered_apart(a: str, b: str) -> bool:
    """Do two titles carry different numbers? Then they are two things.

    "Tax return 2025" and "Tax return 2026" are 93% alike, and were called one
    thing twice: `new` refused the second year, and tidy offered to merge them.
    A number in only one title says nothing, and one title's numbers all
    inside the other's ("Q3 review" and "Q3 review on the 14th") is still a match."""
    na = {n.lstrip("0") for n in re.findall(r"\d+", a)}
    nb = {n.lstrip("0") for n in re.findall(r"\d+", b)}
    return bool(na and nb and not (na <= nb or nb <= na))


class Doctor:
    #: A folder with 400 items will trip the same hint hundreds of times. Report
    #: the first few of each kind in full and roll the rest into one line, so the
    #: report stays readable at any scale.
    PER_CODE_CAP = 20

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.scanner = Scanner(os_)
        self.issues: list[dict] = []
        self.tally: dict[str, int] = {}

    def flag(self, level: str, code: str, message: str, path: str = "", fix: str = "") -> None:
        self.tally[code] = self.tally.get(code, 0) + 1
        if self.tally[code] > self.PER_CODE_CAP:
            return
        self.issues.append({"level": level, "code": code, "message": message,
                            "path": path, "fix": fix})

    def _rollup(self) -> None:
        for code, total in sorted(self.tally.items()):
            if total <= self.PER_CODE_CAP:
                continue
            sample = next(i for i in self.issues if i["code"] == code)
            self.issues.append({
                "level": sample["level"], "code": code,
                "message": f"...and {total - self.PER_CODE_CAP} more '{code}' findings",
                "path": "", "fix": sample.get("fix", ""),
            })

    # -- the checks ---------------------------------------------------------

    def run(self, items: list[Item] | None = None, fix: bool = False) -> dict:
        self.issues = []
        self.tally = {}
        root = self.os.root
        repaired: list[str] = []

        # 1. skeleton. Work/, Notes/ and Archive/ are not part of it: no folder
        # exists before something goes in it, and git cannot ship an empty one
        # anyway. Every download arrived without them, so every stranger's first
        # `./os` said "3 things need fixing" and the AI was told it was broken.
        # save, new and close make each one the first time it is needed.
        want = root / ".claude"
        if not want.exists():
            if fix:
                for sub_ in ("skills", "agents", "hooks"):
                    (want / sub_).mkdir(parents=True, exist_ok=True)
                repaired.append("created .claude/")
            else:
                self.flag("hint", "no-claude-dir",
                          ".claude/ is missing — Claude Code's extras will not load "
                          "(everything else still works)", ".claude", "./os check --fix")

        # 2. the house rules every AI reads
        rules = root / "AGENTS.md"
        for claude_md, pointer in ((root / "CLAUDE.md", "@AGENTS.md"),
                                   (root / ".claude" / "CLAUDE.md", "@../AGENTS.md")):
            if claude_md.exists() and "AGENTS.md" not in read_text(claude_md, 4_000):
                self.flag("warn", "rules-drift",
                          f"{self.os.rel(claude_md)} no longer points at AGENTS.md, so "
                          "Claude Code and every other AI are reading different rules",
                          self.os.rel(claude_md), f"put `{pointer}` on its first line")
        if not rules.exists():
            self.flag("error", "no-agents-md",
                      "AGENTS.md is missing — an AI opening this folder won't know the rules",
                      "AGENTS.md")
        else:
            # Lines with words in them: blank ones cost nothing to read.
            n = sum(1 for line in read_text(rules).split("\n") if line.strip())
            cap = int(self.os.thresholds.get("rules_max_lines", 160))
            if n > cap:
                self.flag("warn", "rules-bloat",
                          f"AGENTS.md has {n} lines of text (cap {cap}) — it is read on every single turn, "
                          "so move long procedures into a skill",
                          "AGENTS.md", "move sections into .claude/skills/")

        # Filing is word matching, so an empty vocabulary is not a crash any
        # more — it is a folder that quietly stops guessing. Say so.
        if not self.os.taxonomy.get("domains"):
            self.flag("warn", "no-vocabulary",
                      ".os/words.json lists no subjects, so nothing can be grouped by "
                      "subject any more", ".os/words.json",
                      "restore it from a fresh copy of this folder")

        # Parts of words.json that could not be read at all. Reading it steps
        # over them rather than falling over, which is right — but a subject
        # silently not filing anything is exactly the kind of quiet wrong this
        # command exists to make loud.
        for said in self.os.words_dropped:
            self.flag("warn", "words-unreadable",
                      f".os/words.json is being read without part of it — {said}",
                      ".os/words.json", "fix that entry, or delete it")

        settings = root / ".claude" / "settings.json"
        if settings.exists():
            try:
                json.loads(settings.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                self.flag("error", "bad-settings", f".claude/settings.json is not valid JSON: {exc}", ".claude/settings.json")

        # Newer versions of files they changed, set aside by an update. Said
        # once in its output and never again, they would wait there for good.
        waiting, where = _left_to_merge(self.os)
        if waiting:
            self.flag("warn", "update-to-merge",
                      f"an update left {waiting} newer file{'' if waiting == 1 else 's'} "
                      f"to merge into yours in {where}",
                      where, "fold each into the file of the same name, then delete that folder")

        # 2b. words that never reached a folder. Saving files in one breath, so
        # anything here is a run that died in between. Left unsaid it is
        # invisible to every command a person would think to run — `os` says
        # "nothing started yet" and `check` says "all good" over the top of it.
        refused = self.os.taken_back()
        for path in staged_captures(self.os.dot):
            try:
                age = int((time.time() - path.stat().st_mtime) // 86_400)
            except OSError:
                age = 0
            if self.os.rel(path) in refused:
                _, body = parse_frontmatter(read_text(path, 2_000))
                words = next((trunc(ln.strip(), 44) for ln in body.split("\n") if ln.strip()),
                             path.name)
                self.flag("hint", "taken-back-capture",
                          f"'{words}' was taken back with ./os undo and is still "
                          "sitting in staging — sort leaves it alone",
                          self.os.rel(path), f'./os save "{self.os.rel(path)}"  to file it after all')
                continue
            _, body = parse_frontmatter(read_text(path, 2_000))
            words = next((trunc(ln.strip(), 44) for ln in body.split("\n") if ln.strip()),
                         path.name)
            stale = age >= STALE_STAGE_DAYS
            when = "today" if age < 1 else f"{age} days ago"
            self.flag("error" if stale else "warn", "unfiled-capture",
                      f"'{words}' was written down {when} and never filed",
                      self.os.rel(path), "./os sort")

        if items is None:
            items = self.scanner.scan()

        # 3. identity and metadata
        seen: dict[str, Item] = {}
        for it in items:
            if it.kind in ("project", "note", "asset"):
                if not it.ident:
                    self.flag("warn", "no-id", f"'{it.title}' has no name on disk yet", self.os.rel(it.path), "./os sort")
                elif it.ident in seen:
                    self.flag("error", "duplicate-id",
                              f"two things share the name {it.ident} ('{it.title}' and '{seen[it.ident].title}')",
                              self.os.rel(it.path), "./os sort")
                else:
                    seen[it.ident] = it
                if it.spine and (not it.domain or it.domain == "unsorted"):
                    # `new` writes `domain: unsorted` when nothing matched, so
                    # "add a domain: line" asked for one that was already there.
                    subjects = ", ".join(self.os.taxonomy.get("domains") or {})
                    self.flag("hint", "no-domain", f"'{it.title}' has no subject set, so it can't be grouped",
                              self.os.rel(it.path),
                              f"set `domain:` at the top of the file to one of: {subjects}"
                              if subjects else "set `domain:` at the top of the file")
            if it.spine and it.spine.exists() and it.words == 0 and it.kind != "asset":
                self.flag("hint", "empty", f"'{it.title}' is empty", self.os.rel(it.path))

        # 4. duplicates
        by_print: dict[str, list[Item]] = {}
        for it in items:
            if it.fingerprint and it.kind != "asset":
                by_print.setdefault(it.fingerprint, []).append(it)
        for group in by_print.values():
            if len(group) > 1:
                names = ", ".join(self.os.rel(g.path) for g in group[:3])
                self.flag("warn", "duplicate-content", f"the same thing appears in {len(group)} places: {names}",
                          self.os.rel(group[0].path), "./os tidy")
        # Near-duplicate titles, reported as groups rather than pairs. Fifty
        # copies of one title is 1,225 pairs and one useful sentence, so cluster
        # them: sort (which lands similar titles next to each other), then walk.
        # difflib's own cheap upper bounds skip pairs that cannot reach the bar.
        # Sort on the title alone. Without an explicit key, two items sharing a
        # title make Python fall through to comparing Item objects, which it
        # cannot do — and two items sharing a title is the exact case this
        # check exists to find.
        titles = sorted(((it.title.lower(), it) for it in items
                         if it.title and it.kind in ("note", "project")),
                        key=lambda pair: (pair[0], str(pair[1].path)))
        threshold = float(self.os.thresholds.get("duplicate_similarity", 0.86))
        matcher = difflib.SequenceMatcher(autojunk=False)

        def alike(a: str, b: str) -> bool:
            if numbered_apart(a, b):
                return False
            # ratio() <= 2*min/(la+lb), so lengths this far apart cannot match
            if 2 * min(len(a), len(b)) < threshold * (len(a) + len(b)):
                return False
            matcher.set_seq2(a)
            matcher.set_seq1(b)
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                return False
            return matcher.ratio() >= threshold

        i = 0
        while i < len(titles):
            head_title, head_item = titles[i]
            group = [head_item]
            j = i + 1
            while j < len(titles) and alike(head_title, titles[j][0]):
                group.append(titles[j][1])
                j += 1
            if len(group) > 1 and len({g.fingerprint for g in group}) > 1:
                names = ", ".join(f"'{g.title}'" for g in group[:3])
                more = f" and {len(group) - 3} more" if len(group) > 3 else ""
                self.flag("hint", "near-duplicate",
                          f"{len(group)} things look like the same thing: {names}{more}",
                          self.os.rel(group[1].path), "./os tidy")
            i = j if len(group) > 1 else i + 1

        # 5. toolkit validity
        for it in items:
            if it.kind == "skill":
                skill_md = it.path / "SKILL.md"
                meta, body = parse_frontmatter(read_text(skill_md))
                if not meta.get("description"):
                    self.flag("warn", "skill-no-description",
                              f"the skill '{it.path.name}' has no description, so it will almost never run",
                              self.os.rel(skill_md), "add a `description:` line saying when to use it")
                if it.path.name.lower() in RESERVED_COMMANDS:
                    self.flag("error", "skill-name-clash",
                              f"the skill '{it.path.name}' has the same name as a built-in command",
                              self.os.rel(it.path), "rename the folder, e.g. os-" + it.path.name)
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", it.path.name):
                    self.flag("warn", "skill-name-shape",
                              f"skill folder '{it.path.name}' should be lowercase-with-hyphens",
                              self.os.rel(it.path))
                cap = int(self.os.thresholds.get("skill_body_max_lines", 120))
                if len(body.split("\n")) > cap:
                    self.flag("hint", "skill-long",
                              f"skill '{it.path.name}' is {len(body.splitlines())} lines — rules stay in SKILL.md, "
                              "reference material moves to REFERENCE.md beside it",
                              self.os.rel(skill_md))
            if it.kind == "agent":
                meta, _ = parse_frontmatter(read_text(it.path))
                if not meta.get("name"):
                    self.flag("warn", "agent-no-name", f"the helper '{it.path.name}' has no `name:` line", self.os.rel(it.path))
                elif slugify(str(meta["name"])) != it.path.stem:
                    self.flag("hint", "agent-name-mismatch",
                              f"agent file '{it.path.name}' declares name '{meta['name']}'",
                              self.os.rel(it.path))
                if not meta.get("description"):
                    self.flag("warn", "agent-no-description",
                              f"the helper '{it.path.name}' has no description, so it will never be used",
                              self.os.rel(it.path))
            if it.kind == "hook" and it.path.suffix in (".sh", ".zsh", ".bash", ".py"):
                if not os.access(it.path, os.X_OK):
                    if fix:
                        it.path.chmod(it.path.stat().st_mode | 0o755)
                        repaired.append(f"made {it.path.name} runnable again")
                    else:
                        self.flag("warn", "hook-not-executable",
                                  f"'{it.path.name}' can't run — it needs to be executable",
                                  self.os.rel(it.path), "./os check --fix")

        launcher = root / "os"
        if launcher.is_file() and not os.access(launcher, os.X_OK):
            if fix:
                launcher.chmod(launcher.stat().st_mode | 0o755)
                repaired.append("made ./os runnable again")
            else:
                self.flag("error", "os-not-executable",
                          "./os is not executable, so typing ./os just says "
                          "'permission denied'", "os", "run:  chmod +x os")

        backups = root / MARKER / "backups"
        if backups.is_dir():
            kept = list(backups.glob("*.zip"))
            weight = sum(z.stat().st_size for z in kept)
            live = sum(f.stat().st_size for bucket in self.os.buckets()
                       for f in (root / bucket).rglob("*") if f.is_file())
            # more than twice what it is copying, and big enough to be worth
            # mentioning at all — a 3 MB folder does not need advice
            if kept and weight > live * 2 and weight > 50_000_000:
                self.flag("hint", "heavy-backups",
                          f"{len(kept)} backups are using {human_size(weight)} — more "
                          f"than twice the {human_size(live)} they are backing up",
                          ".os/backups",
                          "delete the older zips, or lower keep_backups in .os/config.json")

        # 6. clutter the desktop leaves behind
        litter = [q for q in root.rglob("*")
                  if q.name in (".DS_Store", ".localized") or q.name.endswith(".tmp~")
                  or q.name.startswith("._")]
        # A code project makes __pycache__ every run. When git already ignores
        # it, that was the project's choice, and it isn't clutter.
        caches = [q for q in root.rglob("__pycache__") if q.is_dir()]
        if caches and (root / ".git").exists():
            try:
                done = subprocess.run(
                    ["git", "-C", str(root), "check-ignore", "--stdin"],
                    input="\n".join(self.os.rel(q) for q in caches),
                    capture_output=True, text=True, timeout=10)
                ignored = set(done.stdout.splitlines())
                caches = [q for q in caches if self.os.rel(q) not in ignored]
            except (OSError, subprocess.SubprocessError):
                pass
        litter += caches
        for q in litter:
            if fix:
                try:
                    shutil.rmtree(q) if q.is_dir() else q.unlink()
                    repaired.append(f"removed {self.os.rel(q)}")
                except OSError:
                    pass
            else:
                self.flag("hint", "clutter",
                          f"{self.os.rel(q)} is junk your computer left behind",
                          self.os.rel(q), "./os check --fix")

        # 7. hygiene and decay
        stale = int(self.os.thresholds.get("stale_project_days", 30))
        dormant = int(self.os.thresholds.get("dormant_project_days", 75))
        for it in items:
            if it.kind != "project" or it.status in CLOSED_STATUSES:
                continue
            # Only work being pushed can go stale. Something you are holding is
            # *supposed* to sit still between the times you tend to it — nagging
            # about that is the system misunderstanding its own vocabulary.
            if it.status == HOLDING:
                continue
            age = days_since(it.updated)
            if age >= dormant:
                self.flag("warn", "dormant", f"'{it.title}' hasn't moved in {age} days",
                          self.os.rel(it.path),
                          f"./os hold {handle(it)}, or ./os close {handle(it)}")
            elif age >= stale:
                self.flag("hint", "stale", f"'{it.title}' hasn't moved in {age} days",
                          self.os.rel(it.path), "give it a next action, or ./os hold it")

        # 8. category pressure
        cap = int(self.os.thresholds.get("category_max_items", 99))
        pressure: dict[tuple, int] = {}
        for it in items:
            if it.trail:
                key = (it.bucket, tuple(it.trail))
                pressure[key] = pressure.get(key, 0) + 1
        for (bucket, trail), n in pressure.items():
            if n > cap:
                self.flag("warn", "category-overflow",
                          f"{bucket}/{'/'.join(trail)} holds {n} things — that is a lot for one folder",
                          f"{bucket}/{'/'.join(trail)}", "raise category_split in .os/config.json, or split it yourself")

        # 9. broken internal links
        for it in items:
            if not it.spine or not it.spine.exists() or it.spine.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = read_text(it.spine, 80_000)
            for m in re.finditer(r"\[[^\]]*\]\(([^)#:]+\.md)\)", text):
                target = (it.spine.parent / m.group(1).replace("%20", " ")).resolve()
                if not target.exists():
                    self.flag("hint", "broken-link", f"'{it.title}' links to something that isn't there: {m.group(1)}",
                              self.os.rel(it.spine))
                    break

        # Errors are always serious. Warnings matter but saturate. Hints are
        # texture — a folder with 400 items will always have some, and that is
        # not the same as being unhealthy.
        self._rollup()
        tally = {"error": 0, "warn": 0, "hint": 0}
        for issue in self.issues:
            tally[issue["level"]] += 1
        penalty = (
            tally["error"] * 15
            + min(tally["warn"] * 4, 40)
            + min(tally["hint"] * 0.5, 15)
        )
        score = int(max(0, 100 - min(penalty, 100)))
        return {"issues": self.issues, "score": score, "repaired": repaired,
                "items": len(items)}


# ---------------------------------------------------------------------------
# search, archive, undo, backup, review
# ---------------------------------------------------------------------------


#: Suffixes stripped to find a word's root, longest first. Not a full stemmer —
#: just enough that "refreshes" finds "refresh" and "meetings" finds "meeting".
_SUFFIXES = ("ingly", "edly", "ings", "ies", "ied", "ing", "ers", "est", "ed",
             "es", "er", "ly", "s")


def stem(word: str) -> str:
    """The root of a word, or the word itself when stripping would mangle it."""
    for suffix in _SUFFIXES:
        if not word.endswith(suffix):
            continue
        root = word[: -len(suffix)]
        if len(root) < 3:
            continue
        if suffix == "ies":
            return root + "y"
        # "running" -> "runn" -> "run"
        if len(root) > 3 and root[-1] == root[-2] and root[-1] not in "aeiou":
            root = root[:-1]
        return root
    return word


class Term:
    """One search word: how it was typed, and its root.

    The word as typed matches anywhere, the way it always has. The root only
    matches at the start of a word — otherwise "biling" stems to "bil" and
    starts finding every note about anything mobile."""

    __slots__ = ("word", "root", "_rx")

    def __init__(self, word: str):
        self.word = word
        root = stem(word)
        self.root = root if root != word else ""
        self._rx = re.compile(r"(?<![a-z0-9])" + re.escape(root)) if self.root else None

    def weight(self, hay: str) -> float:
        """1.0 for the word as typed, less for a root-only match, 0 for neither."""
        if self.word in hay:
            return 1.0
        return Finder.STEM_WEIGHT if self._rx and self._rx.search(hay) else 0.0

    def count(self, hay: str) -> tuple[int, float, str]:
        """(hits, how much each is worth, the form that matched)."""
        n = hay.count(self.word)
        if n:
            return n, 1.0, self.word
        if self._rx:
            found = self._rx.findall(hay)
            if found:
                return len(found), Finder.STEM_WEIGHT, self.root
        return 0, 0.0, ""


WORD_RE = re.compile(r"[a-z][a-z0-9'-]{2,}")


class Finder:
    """Ranked full-text search over the whole OS. No index server, no daemon."""

    #: how much of a term's score a root-only match earns
    STEM_WEIGHT = 0.6

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.scanner = Scanner(os_)
        self.corrected: dict[str, str] = {}   # what we searched for instead

    # -- scoring ------------------------------------------------------------

    def _pass(self, items: list[Item], terms: list["Term"],
              vocabulary: set | None) -> list[tuple[float, Item, str]]:
        results = []
        for it in items:
            hay_title = nfc(f"{it.ident} {it.title} {it.path.name}").lower()
            hay_meta = nfc(f"{it.domain} {' '.join(it.tags)} {it.status} {it.blurb}").lower()
            raw = ""
            if it.spine and it.spine.exists() and it.spine.suffix.lower() in TEXT_SUFFIXES:
                _, raw = parse_frontmatter(read_ends(it.spine))
                raw = nfc(COMMENT_RE.sub(" ", raw))
            body = raw.lower()

            # Collect the words we have already read, so a failed search can ask
            # "did you mean" without opening a single extra file.
            if vocabulary is not None and len(vocabulary) < 60_000:
                vocabulary.update(WORD_RE.findall(hay_title))
                vocabulary.update(WORD_RE.findall(hay_meta))
                vocabulary.update(WORD_RE.findall(body[:8_000]))

            score, snippet = 0.0, (it.blurb or it.summary)
            for term in terms:
                score += 10 * term.weight(hay_title)
                score += 4 * term.weight(hay_meta)
                hits, worth, form = term.count(body)
                if not hits:
                    continue
                score += min(hits, 8) * 1.2 * worth
                if not snippet or form not in (snippet or "").lower():
                    pos = body.find(form)
                    snippet = gist(raw[max(0, pos - 60): pos + 120], 200)
            if len(terms) > 1 and all(t.weight(hay_title) for t in terms):
                score += 12
            if score > 0:
                results.append((round(score, 2), it, snippet or ""))
        results.sort(key=lambda r: (-r[0], r[1].title.lower()))
        return results

    # -- the search ---------------------------------------------------------

    def search(self, query: str, limit: int = 20, kind: str = "", bucket: str = "") -> list[tuple[float, Item, str]]:
        self.corrected = {}
        terms = [t for t in re.split(r"\s+", nfc(query).lower().strip()) if t]
        if not terms:
            return []
        # Skills and helpers are the machinery, not the person's work. Searching
        # "one sentence" should not hand back nine skill files ahead of the two
        # notes they were actually looking for. Ask for them by kind to see them.
        toolkit = {"skill", "agent", "hook"}
        items = [it for it in self.scanner.scan()
                 if (kind in toolkit or it.kind not in toolkit)
                 and (not kind or it.kind == kind)
                 and (not bucket or it.bucket.lower().startswith(bucket.lower()))]

        vocabulary: set[str] = set()
        hits = self._pass(items, [Term(t) for t in terms], vocabulary)
        if hits:
            return hits[:limit]

        # Nothing matched. Before giving up, assume a typo: every word in the
        # folder is already in hand from the pass above, so this costs no reads.
        fixed = []
        for term in terms:
            if len(term) < 4 or term in vocabulary:
                fixed.append(term)
                continue
            near = difflib.get_close_matches(term, vocabulary, n=1, cutoff=0.75)
            if near and near[0] != term:
                self.corrected[term] = near[0]
                fixed.append(near[0])
            else:
                fixed.append(term)
        if not self.corrected:
            return []
        return self._pass(items, [Term(t) for t in fixed], None)[:limit]

    def like(self, title: str, kinds: tuple = (), threshold: float = 0.82) -> list:
        """Things already here that are near-identical to `title`.

        `os save` files a sentence that reads like work as a project. An AI that
        then also runs `os new project` for the same sentence lands you with
        two folders describing one thing — so both commands ask this first."""
        want = nfc(title).lower().strip()
        if not want:
            return []
        matcher = difflib.SequenceMatcher(autojunk=False)
        matcher.set_seq2(want)
        hits = []
        for it in self.scanner.scan():
            if kinds and it.kind not in kinds:
                continue
            if it.bucket == self.os.bucket_for_role("archive"):
                continue
            have = nfc(it.title).lower().strip()
            if not have or numbered_apart(have, want):
                continue
            # "Redesign the pricing page" inside "Redesign the pricing page before
            # the launch on the 14th" is the same piece of work, even though the
            # lengths are far enough apart that a ratio would never say so.
            if len(have) > 12 and (have in want or want in have):
                hits.append((1.0, it))
                continue
            if 2 * min(len(have), len(want)) < threshold * (len(have) + len(want)):
                continue
            matcher.set_seq1(have)
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                continue
            if matcher.ratio() >= threshold:
                hits.append((round(matcher.ratio(), 3), it))
        hits.sort(key=lambda h: -h[0])
        return [it for _score, it in hits]

    def by_id(self, ident: str) -> Item | None:
        """The one item carrying this name. An empty string matches nothing.

        Things are addressed by name now — the folder or file name, or the
        title said any reasonable way ("Q3 OKR review" and q3-okr-review
        are the same thing). Skills and helpers are tools, not items, so they
        are never matched here — `./os close tidy` must not archive a skill.

        The name on disk wins, then that name as its one-word handle, and only
        then the title. Two notes can share a title: taking the first title
        match the scan met, `./os close same-title-here` put away
        same-title-here-2.md, and the handle `resume-review-2` that ./os had
        just printed found nothing at all."""
        ident = nfc(ident).strip()
        if not ident:
            return None
        items = [it for it in self.scanner.scan()
                 if it.kind in ("project", "note", "asset", "archive")]
        exact, slug = ident.lower(), slugify(ident).lower()
        for same in (lambda it: nfc(it.ident).lower() == exact,
                     lambda it: slugify(nfc(it.ident)).lower() == slug,
                     lambda it: slugify(nfc(it.title)).lower() in (exact, slug)):
            for it in items:
                if same(it):
                    return it
        return None


class Archivist:
    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.finder = Finder(os_)

    def archive(self, ident: str) -> Path:
        item = self.finder.by_id(ident)
        if item is None:
            die(f"nothing here is called {ident} — try  ./os find {ident}")
        shelf = self.os.bucket_for_role("archive")
        if item.bucket == shelf:
            die(f"{ident} is already in the archive — ./os back {handle(item)} brings it out")
        dest = self.os.root / shelf / today()[:4] / item.bucket / item.path.name
        if item.spine and item.spine.exists():
            # Before the header is touched, not after the move: undo restores
            # what a file *said* as well as where it was, and a snapshot taken
            # afterwards would put the archived header back on a live item.
            text = read_to_rewrite(item.spine)
            self.os.snapshot(item.spine)
            meta, _body = parse_frontmatter(text)
            # What it was before it left, so ./os back can put it back as it was
            # rather than waking every filed note up as work with a next action.
            was = str(meta.get("status") or "").strip()
            changes = {"status": "archived", "archived": today()}
            if was:
                changes["was"] = was
            changes["origin"] = self.os.rel(item.path)
            changes["updated"] = today()
            write_text(item.spine, set_fields(text, changes))
        moved = self.os.move_item(item.path, dest)
        self.os.commit(f"archive {ident}")
        return moved

    def restore(self, ident: str) -> Path:
        item = self.finder.by_id(ident)
        if item is None:
            die(f"nothing here is called {ident} — try  ./os find {ident}")
        if item.bucket != self.os.bucket_for_role("archive"):
            die(f"{ident} is not in the archive — ./os show {handle(item)} says where it is")
        meta, text = {}, ""
        if item.spine and item.spine.exists():
            text = read_to_rewrite(item.spine)
            self.os.snapshot(item.spine)      # see archive(): before the rewrite
            meta, _body = parse_frontmatter(text)
        # Where it actually came from, recorded when it was put away. The trail
        # (<year>/<bucket>/) is the fallback, and only then a default.
        origin = str(meta.get("origin") or "")
        origin_bucket = (origin.split("/", 1)[0] if "/" in origin else "") \
            or (item.trail[1] if len(item.trail) > 1 else "") \
            or self.os.bucket_for_role("note")
        if origin_bucket not in self.os.buckets():
            origin_bucket = self.os.bucket_for_role("note")
        dest = self.os.root / origin_bucket / item.path.name
        if item.spine and item.spine.exists():
            # A note or a file is not work, and coming out of the archive must
            # not turn it into work with a next action. Only what was pushed or
            # held goes back to a live phase; everything else keeps its own word.
            was = str(meta.get("was") or "").strip()
            status = was or ("—" if str(meta.get("type", "")) != "work" else PUSHING)
            write_text(item.spine, set_fields(text, {"status": status, "updated": today()},
                                              drop=("was", "archived", "origin")))
        moved = self.os.move_item(item.path, dest)
        self.os.commit(f"restore {ident}")
        return moved


class Undo:
    def __init__(self, os_: "Zenith"):
        self.os = os_

    def rel_of(self, path: Path) -> str:
        return relative_to_root(path, self.os.root)

    def peek(self) -> dict | None:
        undo = self.os.state.get("undo") or []
        return undo[-1] if undo else None

    def written_since(self, entry: dict) -> list:
        """Files the step changed or made that have been written in since.
        Undoing it would take those later words out too, so it asks first."""
        steps = entry.get("steps") or []

        def now_at(rel: str, since: int) -> Path:
            for step in steps[since:]:
                if step.get("action") != "move":
                    continue
                if rel == step["src"]:
                    rel = step["dst"]
                elif rel.startswith(step["src"] + "/"):
                    rel = step["dst"] + rel[len(step["src"]):]
            return self.os.root / rel

        after = entry.get("after")
        try:
            then = _dt.datetime.fromisoformat(str(entry.get("at"))).timestamp() + 5
        except ValueError:
            then = None
        found = []
        for n, step in enumerate(steps):
            if step.get("action") == "create" and "digest" in step:
                path = now_at(step["src"], n + 1)
                if path.is_file() and not path.is_symlink() \
                        and content_print(path) != step["digest"]:
                    found.append(self.rel_of(path))
        blobs = self.os.dot / "cache" / "undo" / str(entry.get("blobs") or "")
        for rel, blob in (entry.get("snapshots") or {}).items():
            path = now_at(rel, 0)
            if not (blobs / blob).is_file() or not path.is_file() or path.is_symlink():
                continue
            try:
                if isinstance(after, dict) and rel in after:
                    same = hashlib.sha256((blobs / blob).read_bytes()).hexdigest()[:16] == after[rel]
                    if not same and content_print(path) != after[rel]:
                        found.append(self.rel_of(path))
                elif then is not None and path.stat().st_mtime > then:
                    # a step from before ./os wrote down how it left each file
                    found.append(self.rel_of(path))
            except OSError:
                continue
        return sorted(set(found))

    def revert(self) -> dict:
        undo = self.os.state.get("undo") or []
        if not undo:
            die("nothing to undo")
        entry = undo.pop()
        restored, failed = 0, []
        # Words written into a file after the step being undone are not the
        # step's to take back. Undo used to write the older copy over them, and
        # delete what the step had made, without looking: a Next action added
        # after `./os decide`, a line added to a saved note, a project started
        # inside a folder from `./os new`, all gone with no copy anywhere. The
        # step is still reversed; the file as it stood goes here first, and
        # undo says so.
        kept: list[tuple[str, str]] = []
        keep_dir: list[Path] = []
        after = entry.get("after")

        def keep(path: Path, rel: str) -> None:
            if not keep_dir:
                stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                keep_dir.append(unique_path(self.os.dot / "cache" / "undo-kept" / stamp))
            copy = keep_dir[0] / rel
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(path), str(copy))
            kept.append((self.rel_of(path), self.rel_of(copy)))

        #: where each thing actually came back to. A name can already be taken
        #: by the time we put something back — two runs undone in turn, the
        #: second having reused the first's filenames — and then it lands beside
        #: its old name rather than on it. Its saved content has to follow it
        #: there, or it is written over whatever now holds that name instead.
        landed: dict[str, str] = {}
        for step in reversed(entry["steps"]):
            try:
                if step["action"] == "move":
                    src = self.os.root / step["dst"]
                    dst = self.os.root / step["src"]
                    # `exists()` follows the link, so a shortcut whose target is
                    # gone reads as "not there" and undo used to abandon it in
                    # whichever bucket the sort had put it in.
                    if src.exists() or src.is_symlink():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        free = unique_path(dst)
                        shutil.move(str(src), str(free))
                        if free != dst:
                            landed[step["src"]] = self.rel_of(free)
                        restored += 1
                    else:
                        failed.append(step["dst"])
                elif step["action"] == "create":
                    path = self.os.root / step["src"]
                    if path.is_file():
                        if content_print(path) != step.get("digest"):
                            keep(path, step["src"])
                        path.unlink()
                        restored += 1
                elif step["action"] == "mkdir":
                    # Only ever an empty folder. Emptiness used to be judged by
                    # `ignored()`, which counts .git, node_modules and CLAUDE.md
                    # as not there, and the whole folder went with rmtree.
                    path = self.os.root / step["src"]
                    if path.is_dir():
                        litter = [p for p in path.iterdir() if p.is_file() and not p.is_symlink()
                                  and (p.name in (".DS_Store", CATEGORY_MARKER)
                                       or p.name.startswith("._"))]
                        if len(litter) < len(list(path.iterdir())):
                            failed.append(f"{step['src']}: other things were put in it "
                                          "since, so it stays")
                            continue
                        for p in litter:
                            p.unlink()
                        path.rmdir()
                        restored += 1
            except OSError as exc:
                failed.append(f"{step.get('dst') or step.get('src', '?')}: {exc}")

        # locations are back; now put the contents back too.
        #
        # Only into a file that is actually sitting there. One run can move the
        # same thing twice — the adopt pass files it into Notes/, the balance
        # pass tucks it into a category — and each move snapshots it under the
        # path it had at the time. Writing every snapshot back unconditionally
        # re-created the file at that intermediate path, so undoing a sort left
        # the item where it started *and* a stray copy of it in Notes.
        blobs = self.os.dot / "cache" / "undo" / str(entry.get("blobs") or "")
        # On a Mac, `Tool Shed/README.md` and `TOOL Shed/README.md` are one
        # file. The first copy of it is the oldest, and the one that goes back.
        done: set = set()
        for rel, blob in (entry.get("snapshots") or {}).items():
            source = blobs / blob
            target = self.os.root / landed.get(rel, rel)
            if not source.exists() or not target.is_file():
                continue
            try:
                same = (target.stat().st_dev, target.stat().st_ino)
                if same in done:
                    continue
                done.add(same)
                older = source.read_bytes()
                if isinstance(after, dict) and rel in after:
                    # (the fingerprint `content_print` gives a file this small)
                    if hashlib.sha256(older).hexdigest()[:16] == after[rel]:
                        continue   # the step never changed what it said: nothing to put back
                    changed = content_print(target) != after[rel]
                else:
                    # a step from before ./os wrote down how it left each file
                    changed = target.read_bytes() != older
                if changed:
                    keep(target, landed.get(rel, rel))
                target.write_bytes(older)
                restored += 1
            except OSError as exc:
                failed.append(f"{rel}: {exc}")
        # A step that copied nothing aside has no folder of its own, and `blobs`
        # is then the folder holding every other step's copies.
        if entry.get("blobs"):
            shutil.rmtree(blobs, ignore_errors=True)

        # What went back into the staging area is what the person took back.
        # `os sort` reads this and leaves it alone; see `Sorter.file_staged`.
        back = [self.rel_of(self.os.root / landed.get(st["src"], st["src"]))
                for st in entry["steps"] if st["action"] == "move"
                and str(st["src"]).startswith(f"{MARKER}/cache/{STAGING}/")]
        if back:
            self.os.state["taken_back"] = (
                (self.os.state.get("taken_back") or []) + back)[-60:]

        self.os.state["undo"] = undo
        self.os.save_state()
        # (also not journalled — see Zenith.commit)
        return {"label": entry["label"], "restored": restored, "failed": failed,
                "kept": kept}


#: What a backup is called: the folder's name, then when. Only these are ever
#: dropped as newer ones arrive; any other file in .os/backups/ is left alone.
BACKUP_NAME = re.compile(r".+-\d{8}-\d{6}(?:-\d+)?\.zip")


class Backup:
    def __init__(self, os_: "Zenith"):
        self.os = os_

    def snapshot(self) -> Path:
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        # two backups in the same second must not become one: the stamp is only
        # second-resolution, and silently overwriting a backup is the exact
        # opposite of what somebody asking for a backup wants. Named after the
        # folder, so a backup moved somewhere else still says whose it is.
        name = slugify(self.os.config.get("name") or "Zenith")
        out = self.os.dot / "backups" / f"{name}-{stamp}.zip"
        out.parent.mkdir(parents=True, exist_ok=True)
        out = unique_path(out)
        # Matched on the whole path, never on a bare folder name. Anchoring these
        # to .os/ matters: somebody's own `Notes//cache/` or a project's
        # `notes/backups/` is their writing, and a backup that quietly leaves it
        # out is worse than no backup at all.
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv"}
        # .os/transcripts is a cache too: keyed by video, re-fetchable, and
        # bigger than everything it sits beside. A backup of it is 100KB of
        # somebody else's words that yt-dlp would hand back for free.
        skip_trees = ((MARKER, "backups"), (MARKER, "cache"), (MARKER, "transcripts"))
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in self.os.root.rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                rel = path.relative_to(self.os.root)
                if any(part in skip_dirs for part in rel.parts):
                    continue
                if any(rel.parts[:len(t)] == t for t in skip_trees):
                    continue
                zf.write(path, rel.as_posix())   # zip entries are always POSIX
        # by when it was written, not by what it is called: "…-2.zip" sorts
        # before "….zip", so sorting by name can drop the newest and keep the oldest.
        # Every name the folder has had, so a rename doesn't strand the old ones.
        keep = max(1, int(self.os.behaviour.get("keep_backups", 3)))
        zips = sorted((z for z in (self.os.dot / "backups").glob("*.zip")
                       if BACKUP_NAME.fullmatch(z.name)),
                      key=lambda z: z.stat().st_mtime)
        for stale in zips[:-keep]:
            stale.unlink(missing_ok=True)
        return out


class Reviewer:
    """The anti-decay pass. A second brain dies from neglect, not from bad taxonomy."""

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.scanner = Scanner(os_)

    #: A line that is one step of a procedure: a bullet, a checkbox, or "3. do x".
    STEP_LINE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)", re.M)
    #: Inline code: a name the person is pointing at, not a claim they are making.
    CODE_SPAN = re.compile(r"`[^`\n]*`")
    #: A markdown heading — where somebody writing down a repeated job says so.
    HEADING_LINE = re.compile(r"^\s*#{1,6}\s")
    #: Short enough that no line in it is incidental.
    SHORT_NOTE = 25

    def routines(self, items: list, limit: int = 3) -> list:
        """Things being kept up by hand that an AI could just do.

        Nothing else in here ever mentions skills, so somebody who never opens
        the chat can use this folder for a year and not learn they exist. The
        moment to say so is not in a menu — it is when they have written down,
        in their own words, a job they do the same way every time.

        The bar is deliberately high: a cadence *and* a fixed procedure, because
        either alone is ordinary writing. "Every Monday I dread it" is not a
        skill, and neither is a checklist that runs once. A nudge that fires on
        the wrong note is worse than one that never fires — so when in doubt
        this says nothing. The words themselves live in .os/words.json, where
        somebody can teach it their own."""
        spec = self.os.taxonomy.get("routine") or {}
        cadence = [w.lower() for w in spec.get("cadence", [])]
        procedure = [w.lower() for w in spec.get("procedure", [])]
        if not cadence or not procedure:
            return []

        # Something already automated is not worth nagging about.
        have = {slugify(i.title) for i in items if i.kind in ("skill", "agent")}
        shelf = self.os.bucket_for_role("archive")
        out = []
        for it in items:
            if it.bucket == shelf:
                continue
            if not (it.kind == "note" or (it.kind == "project" and it.status == HOLDING)):
                continue
            if not it.spine or not it.spine.exists():
                continue
            if slugify(it.title) in have:
                continue
            _, body = parse_frontmatter(read_text(it.spine, 40_000))
            # A word inside backticks is a name — `Daily Emails/` is a folder,
            # not a cadence — so code spans come out before anything is matched.
            clean = self.CODE_SPAN.sub(" ", COMMENT_RE.sub(" ", body))
            # "every Monday I dread it" in the middle of a paragraph is writing,
            # not a routine. Three notes were flagged on 2026-09-08 for the word
            # `weekly` appearing in their prose. A cadence only counts where the
            # note is *structured* around it — the title, a heading, or a list
            # line — which is where somebody writing down a repeated job puts it.
            rows = [ln for ln in clean.split("\n") if ln.strip()]
            headings = [ln for ln in rows if self.HEADING_LINE.match(ln)]
            # Where the note is *framed* by the cadence: its own title, or a
            # heading. A three-line note is all framing — nothing in it can be
            # incidental — so its whole body counts. A 500-line research note
            # that happens to say "this field moves monthly" is not a routine,
            # and three of them were flagged that way on 2026-09-08.
            framing = ([it.title] + headings
                       + (rows if len(rows) <= self.SHORT_NOTE else []))
            hay = (it.title + "\n" + clean).lower()
            said = next((w for w in cadence
                         if w in "\n".join(framing).lower()), "")
            if not said:
                continue
            how = next((w for w in procedure if w in hay), "")
            if not how and len(self.STEP_LINE.findall(body)) < 3:
                continue
            out.append({"id": it.ident, "title": it.title,
                        "where": self.os.rel(it.path), "said": said})
        return out[:limit]

    #: Transcripts are pulled again on demand and never opened twice by most
    #: people, so they are never deleted for anybody — but they are the only
    #: thing here that grows without being asked, and nothing else in the folder
    #: would ever mention them. Past this many, the weekly pass says so once.
    STUDY_CACHE_NAG = 20

    def study_cache(self) -> dict:
        """What `./os learn` has fetched and kept, if anything.

        Not a fault and never repaired automatically: they are somebody's
        sources. This is the one place they are visible at all."""
        cache = self.os.dot / "transcripts"
        try:
            files = [p for p in cache.glob("*.txt") if p.is_file()]
            size = sum(p.stat().st_size for p in files)
        except OSError:
            return {"sources": 0, "bytes": 0}
        return {"sources": len(files), "bytes": size}

    def run(self) -> dict:
        items = self.scanner.scan()
        health = Doctor(self.os).run(items=items)
        stale_days = int(self.os.thresholds.get("stale_project_days", 30))
        dormant_days = int(self.os.thresholds.get("dormant_project_days", 75))

        shelf = self.os.bucket_for_role("archive")
        active = [i for i in items if i.kind == "project" and i.status in (PUSHING, "")]
        holding = [i for i in items if i.kind == "project" and i.status == HOLDING
                   and i.bucket != shelf]
        report = {
            "generated": now_iso(),
            "score": health["score"],
            "counts": {k: v for k, v in Indexer._counts(items).items()},
            "unfiled": [i.title for i in items if Sorter.unmanaged(i)]
                       + [f.name for f in loose_at_top(self.os.root)],
            "active": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in
                       sorted(active, key=lambda x: days_since(x.updated))],
            "stale": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in active
                      if stale_days <= days_since(i.updated) < dormant_days],
            "holding": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in
                        sorted(holding, key=lambda x: days_since(x.updated))],
            # Only work being pushed can look abandoned. Something held is quiet
            # by design, so it is never offered up for the archive on age alone.
            "archive_candidates": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in active
                                   if days_since(i.updated) >= dormant_days
                                   and i.bucket != shelf],
            "duplicates": [i for i in health["issues"] if i["code"] in ("duplicate-content", "near-duplicate")],
            "unsorted": [{"id": i.ident, "title": i.title} for i in items
                         if i.domain in ("", "unsorted") and i.kind in ("note", "project")][:20],
            "shipped": [{"id": i.ident, "title": i.title} for i in items
                        if i.status == "done" and i.bucket != shelf],
            # The classifier flags what it could not place confidently. That flag
            # is worthless if nothing ever shows it again — this is where a human
            # is meant to look.
            "unsure": [{"id": i.ident, "title": i.title, "where": i.bucket} for i in items
                       if "needs-review" in (i.flags or [])],
            # Things being kept up by hand that could be handed to an AI. This
            # is the only place the product ever mentions skills unprompted.
            "routines": self.routines(items),
            "study_cache": self.study_cache(),
            "issues": health["issues"],
        }
        self.os.config.setdefault("review", {})["last_run"] = today()
        self.os.save_config()
        write_text(self.os.dot / "cache" / "last-review.json", json.dumps(report, indent=2) + "\n")
        return report


# ---------------------------------------------------------------------------
# creating new things from blueprints
# ---------------------------------------------------------------------------

KIND_ALIASES = {
    "work": "project", "project": "project", "proj": "project", "p": "project", "w": "project",
    "ongoing": "project", "area": "project", "a": "project",
    "pushing": "project", "holding": "project", "hold": "project",
    "note": "note", "n": "note", "ref": "note", "reference": "note",
    "learning": "note", "learn": "note", "method": "note",
    "skill": "skill", "s": "skill",
    "helper": "agent", "agent": "agent", "subagent": "agent",
}


#: Some of the words above say *which phase* the new thing starts in. The rest
#: start it being pushed, because that is what somebody typing `os new` is
#: nearly always doing.
NEW_STATUS = {"ongoing": HOLDING, "area": HOLDING, "holding": HOLDING, "hold": HOLDING}

#: …and one of them says which *blueprint*, the same way `ongoing` does for
#: work. A note about how something is done wants numbered steps, the
#: disagreement, what goes wrong and a practice run; an ordinary note wants
#: none of that. `.os/templates/learning.md` has been the shape /learn is held
#: to since it was written, and until this existed the only way to reach it was
#: to make an ordinary note and retype its headings by hand — which is a thing
#: that gets skipped, and did.
NEW_SHAPE = {"learning": "learning", "learn": "learning", "method": "learning"}


class Creator:
    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.templates = os_.dot / "templates"
        self._cache: dict = {}

    #: what a template file is called, when that differs from the internal name
    TEMPLATE_NAMES = {"project": "pushing"}

    def template(self, kind: str, shape: str = "") -> str:
        """The blueprint for a kind, and — where there is more than one — which.

        Both halves of a piece of work ask *what does good look like here*.
        They differ only below that: pushing wants a next action, holding wants
        a cadence. So there are two blueprints and one kind, and the same is
        true of notes: `learning.md` is a note in the shape a method has to be
        written in to be worth anything. Cached: filing a big drop asks for the
        same handful of templates hundreds of times."""
        key = f"{kind}:{shape}"
        if key in self._cache:
            return self._cache[key]
        names = []
        if shape:
            names.append(shape)
        names += [self.TEMPLATE_NAMES.get(kind, kind), kind]
        for name in names:
            path = self.templates / f"{name}.md"
            if path.exists():
                return self._cache.setdefault(key, read_text(path))
        return ("---\ntitle: {{TITLE}}\ntype: {{KIND}}\nstatus: {{STATUS}}\n"
                "domain: {{DOMAIN}}\ntags: [{{TAGS}}]\ncreated: {{DATE}}\n"
                "updated: {{DATE}}\n---\n\n# {{TITLE}}\n")

    def render(self, kind: str, title: str, ident: str, domain: str, tags: list[str],
               status: str = "", shape: str = "") -> str:
        # Work picks its blueprint by the phase it starts in, so `status` is
        # already the answer there; a note has no phase and says which shape it
        # wants outright.
        text = self.template(kind, shape or status)
        for key, value in {
            "{{ID}}": ident, "{{TITLE}}": title, "{{SLUG}}": slugify(title),
            "{{KIND}}": TYPE_ON_DISK.get(kind, kind), "{{STATUS}}": status or "—",
            "{{DATE}}": today(), "{{DOMAIN}}": domain or "unsorted",
            "{{TAGS}}": ", ".join(tags), "{{YEAR}}": today()[:4],
            "{{OWNER}}": str(self.os.config.get("owner") or ""),
            "{{OS_NAME}}": str(self.os.config.get("name", "Zenith")),
        }.items():
            # Every blueprint puts {{TITLE}} on a front-matter line as well as
            # in a heading, so anything substituted in has to stay on the line
            # it lands on: `_emit` only guards values that go through it, and a
            # template substitution never does. See `one_line`.
            text = text.replace(key, one_line(value))
        return text

    def create(self, kind: str, title: str, domain: str = "", tags: list[str] | None = None,
               status: str = "") -> Path:
        asked = kind.lower()
        kind = KIND_ALIASES.get(asked, "")
        if not kind:
            die("that is not a kind — try one of: work, ongoing, note, learning, "
                "skill, helper")
        if kind == "project":
            status = status or NEW_STATUS.get(asked, PUSHING)
            shape = ""
        else:
            status = ""
            shape = NEW_SHAPE.get(asked, "")
        tags = tags or []
        if not domain:
            # Nobody wants to be asked for a subject. Guess it from the title,
            # the same way the sorter would, and leave it blank if we cannot.
            guess, score, _ = Classifier(self.os).score_domain(title, title, "")
            if guess and score >= 3.0:
                domain = guess
        slug = slugify(title)
        root = self.os.root

        if kind == "skill":
            if slug in RESERVED_COMMANDS:
                die(f"'{slug}' is a built-in Claude Code command — pick another name (try os-{slug})")
            folder = root / ".claude" / "skills" / slug
            if folder.exists():
                die(f"skill '{slug}' already exists at {self.os.rel(folder)}")
            self.os.record("mkdir", self.os.rel(folder))
            write_text(folder / "SKILL.md", self.render("skill", title, "", domain, tags))
            self.os.created(folder / "SKILL.md")
            self.os.commit(f"new skill /{slug}")
            return folder / "SKILL.md"

        if kind == "agent":
            path = root / ".claude" / "agents" / f"{slug}.md"
            if path.exists():
                die(f"agent '{slug}' already exists")
            write_text(path, self.render("agent", title, "", domain, tags))
            self.os.created(path)
            self.os.commit(f"new agent {slug}")
            return path

        bucket = self.os.bucket_for_role(kind if kind == "project" else "note")
        ident = self.os.next_id(bucket)
        if kind == "project":
            # The name on disk is the handle: plain words, nothing in front of them.
            # Born with the name a person would write on it.
            name = folder_name(title, slug)
            folder = root / bucket / name
            # `Content` is the media folder ./os never looks inside: work made
            # there was reported as started, and then nothing could find it.
            if folder.exists() or ignored(folder):
                n = 2
                while (root / bucket / f"{name}-{n}").exists():
                    n += 1
                folder = root / bucket / f"{name}-{n}"
            # Record the folders before the files inside them: undo replays the
            # steps backwards, so the contents have to come off first or the
            # directory is never empty enough to remove.
            self.os.record("mkdir", self.os.rel(folder))
            # No empty notes/ or assets/ scaffolding.
            write_text(folder / "README.md",
                       self.render(kind, title, ident, domain, tags, status))
            self.os.created(folder / "README.md")
            self.os.save_state()
            self.os.commit(f"new {status} — {title}")
            return folder / "README.md"

        path = root / bucket / f"{slug}.md"
        if path.exists():
            n = 2
            while (root / bucket / f"{slug}-{n}.md").exists():
                n += 1
            path = root / bucket / f"{slug}-{n}.md"
        write_text(path, self.render("note", title, ident, domain, tags, shape=shape))
        self.os.created(path)
        self.os.save_state()
        self.os.commit(f"new note — {title}")
        return path

    def stage(self) -> Path:
        """The staging area a capture passes through on its way to a folder."""
        stage = self.os.dot / "cache" / STAGING
        stage.mkdir(parents=True, exist_ok=True)
        return stage

    def capture(self, text: str, source: str = "") -> Path:
        """Write it down with no questions asked. Anything, any time.

        It lands in the staging area, not in a folder the person has. `os save`
        classifies and moves it in the same breath, so this file exists for the
        length of one command — which is the point: there is no drop folder to
        remember, and so nothing that can sit in one going stale."""
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        first = re.sub(r"\s+", " ", text.strip().split("\n")[0])[:60] or "capture"
        path = self.stage() / f"{stamp}-{slugify(first)}.md"
        header = f"---\nsaved: {now_iso()}\n"
        if source:
            header += f"source: {source}\n"
        header += "---\n\n"
        landed = unique_path(path)
        # Something taken back with ./os undo keeps its name on a list the sort
        # won't file from. Saved again in the same second, new words got that
        # same name and sat unfiled; the demo lost all three of its own that way.
        refused = self.os.taken_back()
        n = 2
        while self.os.rel(landed) in refused:
            landed = unique_path(path.with_name(f"{path.stem}-{n}{path.suffix}"))
            n += 1
        write_text(landed, header + text.rstrip() + "\n")
        return landed


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------

def wordmark(name: str = "Zenith") -> str:
    """The name, letter-spaced, with a rule exactly as wide as it is."""
    spaced = " ".join(name.upper())
    return "\n  " + spaced + "\n  " + "─" * len(spaced)


WORDMARK = wordmark()

HELP = """
  {name} — {tagline}

  {c1}THE FIVE YOU'LL ACTUALLY USE{c0}
    os                             what's going on right now
    os save "<anything>"           write it down — I file it for you
    os find <words>                search everything you've ever saved
    os show <name>                 look at one thing: state, next action, log
    os open <name>                 show me where something is on disk
    os undo                        take back the last thing Zenith did
    os decide <name> "<text>"      write a settled thing into its ## Decisions

  {c1}STARTING, AND STOPPING{c0}
    os new work "<name>"           start something you're pushing on
    os new ongoing "<name>"        start something you'll just keep up
    os new note "<name>"           write a note yourself
    os new skill "<name>"          teach your AI a job you want done the same way
    os hold <name>                 no next action, just keep it level
    os push <name>                 back on the go
    os close <name>                no longer live — put it away in Archive/
    os back <name>                 get it out of the archive again

  {c1}HOUSEKEEPING (rarely needed){c0}
    os sort                        file anything you dropped in by hand
    os check                       is anything broken?   --fix repairs it
    os tidy                        what's gone stale, doubled up or unfiled
    os backup                      zip a copy of everything · os update gets the newest version
    os edit <name>                 open it in your text editor · os rename <name> "<new>"
    os claim <name>                tell other chats you're on it · os release frees it
    os demo                        two-minute tour, then puts everything back
    os name "<your name>"          put your name on this folder

  {c1}IF YOU NEED IT{c0}
    os last                        what happened the last time anyone worked here
    os help <command>              more about any one — most take --json for scripts
    os test                        prove it still works, on a throwaway copy
    os index / os brief            rebuild the list · what your AI gets told

  Nothing is ever deleted, and every change can be undone with  os undo
  If the shell says permission denied, run  bash os  once and it fixes itself.
"""


def _theirs(argv: list[str]) -> list[str]:
    """The arguments that are the person's own words, not options.

    Once a command has taken the flags it knows, what is left is theirs — but
    dropping everything that starts with a dash silently eats `-3 degrees` and
    `-Xf12o4jt4`. A bare `--` says the rest is content whatever it looks like,
    which is the only way to name such a thing on a command line."""
    if "--" in argv:
        return argv[argv.index("--") + 1:]
    return [a for a in argv if not a.startswith("-")]


def _flag(argv: list[str], *names: str) -> bool:
    for n in names:
        if n in argv:
            argv.remove(n)
            return True
    return False


def _opt(argv: list[str], name: str, default: str = "") -> str:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            value = argv[i + 1]
            del argv[i:i + 2]
            return value
        del argv[i]
    for a in list(argv):
        if a.startswith(name + "="):
            argv.remove(a)
            return a.split("=", 1)[1]
    return default


def _count(argv: list[str], name: str, default: int) -> int:
    """A `--limit`-style option, read as a number.

    `./os find x --limit all` is somebody guessing at the spelling, and int()
    answers that with a traceback — which reads as a broken program rather than
    a mistyped word. Say what was wrong with it instead."""
    raw = _opt(argv, name).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        die(f"{name} wants a number, not {raw!r}   e.g.  {name} 20")
    return value if value > 0 else default


def cmd_status(os_: Zenith, argv: list[str]) -> int:
    """One screen. Plain sentences, not a dashboard."""
    if _flag(argv, "--json"):
        print(json.dumps(Reviewer(os_).run(), indent=2))
        return 0
    if not (os_.dot / "registry.json").exists():
        Indexer(os_).build()
    items = Scanner(os_).scan()
    health = Doctor(os_).run(items=items)

    mark = wordmark(os_.config.get("name", "Zenith"))
    Out.raw(paint(mark, S.GOLD) if S.enabled else mark)
    Out.raw("  " + paint(str(os_.root), S.FAINT))
    Out.raw()

    rows = [(b, len([i for i in items if i.bucket == b]), spec["blurb"])
            for b, spec in os_.buckets().items()]
    width = max(len(r[0]) for r in rows) + 3
    for bucket, n, blurb in rows:
        bar = paint("▍", S.GOLD) if n else " "
        Out.raw("  " + bar + paint(pad(bucket, width - 1), S.INK if n else S.FAINT)
                + paint(pad(str(n) if n else "·", 6), S.B if n else S.FAINT)
                + paint(trunc(blurb, 50), S.FAINT))
    Out.raw()

    waiting = len([i for i in items if Sorter.unmanaged(i)]) + len(loose_at_top(os_.root))
    # Words taken back with ./os undo sit in the same place on purpose, and are
    # no worry at all: counted here they were a nag with no way to stop it.
    taken = os_.taken_back()
    staged = [p for p in staged_captures(os_.dot) if os_.rel(p) not in taken]
    active = [i for i in items if i.kind == "project" and i.status == PUSHING]
    held = [i for i in items if i.kind == "project" and i.status == HOLDING
            and i.bucket != os_.bucket_for_role("archive")]
    cold = [i for i in active if days_since(i.updated) >= int(os_.thresholds["stale_project_days"])]
    errors = [i for i in health["issues"] if i["level"] == "error"]

    def plural(n, word):
        return f"{n} {word}" + ("" if n == 1 else "s")

    def names(group: list) -> None:
        # Every "which one?" says "run ./os to see the names", so here they
        # are: the newest few, each with the name a command takes.
        group = sorted(group, key=lambda i: days_since(i.updated))
        for it in group[:5]:
            Out.raw("    " + paint(pad(trunc(it.title, 40), 42), S.INK)
                    + paint(f"./os show {handle(it)}", S.FAINT))
        if len(group) > 5:
            Out.raw("    " + paint(f"and {len(group) - 5} more — every name is in INDEX.md", S.FAINT))

    if active:
        line = plural(len(active), "thing") + " on the go"
        if cold:
            line += f", {len(cold)} you haven't touched in a while"
        Out.raw("  " + paint(line + ".", S.INK))
        names(active)
    elif not held:
        Out.raw("  " + paint("Nothing started yet.", S.FAINT))
    # Held work is listed, never counted as stale: sitting quiet is what it is
    # for. Without this line it would be invisible, which is how the old
    # Ongoing folder became a place things went to be forgotten.
    if held:
        Out.raw("  " + paint(plural(len(held), "thing") + " you're just keeping level.", S.MUTE))
        names(held)
    # Who is holding what, before anything about tidiness: two chats in one
    # folder is the ordinary case now, and this is the only place either of
    # them finds out about the other.
    for it in [i for i in items if i.claim][:6]:
        mine = claim_owner(read_claim(it.claim)[0]) == claim_label()
        Out.raw("  " + paint(f"{it.title} — {claim_words(it.claim)}", S.MUTE)
                + paint("   (this chat)" if mine else f"   ./os release {handle(it)}", S.FAINT))
    if waiting:
        Out.raw("  " + paint(plural(waiting, "thing") + " dropped in but not filed.", S.AMBER)
                + paint("   ./os sort", S.FAINT))
    # Said separately from the line above, because it is a different worry: not
    # a file they dragged in and forgot, but words they typed that this program
    # accepted and then failed to put anywhere.
    if staged:
        Out.raw("  " + paint(plural(len(staged), "thing") + " you saved never got filed.", S.AMBER)
                + paint("   ./os sort", S.FAINT))
    to_merge, where = _left_to_merge(os_)
    if to_merge:
        Out.raw("  " + paint(f"An update left {plural(to_merge, 'newer file')} to merge into yours.", S.AMBER)
                + paint(f"   {where}", S.FAINT))
    if errors:
        Out.raw("  " + paint(plural(len(errors), "thing")
                             + (" needs" if len(errors) == 1 else " need") + " fixing.", S.RED)
                + paint("   ./os check --fix", S.FAINT))
    elif health["score"] < 85:
        Out.raw("  " + paint("A few small things could be tidier.", S.AMBER)
                + paint("   ./os tidy", S.FAINT))

    Out.raw()
    Out.raw("  " + paint('./os save "anything on your mind"', S.GOLD)
            + paint("   or open this folder in any AI and just talk", S.FAINT))
    Out.raw("  " + paint("./os help", S.MUTE) + paint("   everything you can type", S.FAINT))
    Out.raw()
    return 0


def cmd_sort(os_: Zenith, argv: list[str]) -> int:
    dry = _flag(argv, "--dry-run", "-n")
    as_json = _flag(argv, "--json")
    if dry:
        result = Sorter(os_, dry=True).run()
    else:
        with Lock(os_, "sort"):
            result = Sorter(os_, dry=False).run()
            os_.commit("sort")
            Indexer(os_).build()
    if as_json:
        print(json.dumps(result, indent=2))
        return 1 if result.get("skipped") else 0

    def report_skipped() -> None:
        for entry in result.get("skipped", [])[:20]:
            Out.warn(f"couldn't file {entry['path']} — {entry['why']}")
        for rel in result.get("waiting", [])[:20]:
            Out.note(f"left {rel} for now — it's empty and seconds old, so somebody "
                     "is probably still writing it")
        for rel in result.get("taken_back", [])[:20]:
            Out.note(f"left {rel} alone — you took it back with ./os undo · "
                     f'./os save "{rel}" files it after all')

    Out.title("filing", "a preview — nothing has moved" if dry else "")
    if not result["moves"]:
        if result.get("skipped") or result.get("waiting") or result.get("taken_back"):
            report_skipped()
            Out.note("everything else is filed already")
        else:
            Out.ok("nothing waiting — everything is filed already")
        Out.raw()
        return 1 if result.get("skipped") else 0
    width = max((vlen(m[1]) for m in result["moves"]), default=10)
    width = min(width, 46)
    for what, src, dst in result["moves"][:200]:
        arrow = paint("→", S.FAINT)
        Out.raw("  " + paint(pad(what, 9), S.GOLD) + pad(trunc(src, width), width + 2)
                + arrow + " " + paint(trunc(dst, 52) if dst != src
                                      else "stays where it is", S.INK))
    if len(result["moves"]) > 200:
        Out.note(f"...and {len(result['moves']) - 200} more")
    Out.raw()
    bits = []
    if result["filed"]:
        bits.append(f"{result['filed']} filed")
    if result["identified"]:
        bits.append(f"{result['identified']} named")
    if result["balanced"]:
        bits.append(f"{result['balanced']} tucked into folders")
    Out.ok(" · ".join(bits) or "nothing to do")
    report_skipped()
    if not dry:
        Out.note("didn't like any of that?  ./os undo puts it all back")
    Out.raw()
    return 1 if result.get("skipped") else 0


def cmd_index(os_: Zenith, argv: list[str]) -> int:
    as_json = _flag(argv, "--json")
    notify = _flag(argv, "--notify")
    registry = Indexer(os_).build()
    if notify:
        # Files dropped straight into a folder by hand are otherwise invisible
        # until the person happens to run ./os. The Stop hook rebuilds the index
        # anyway, so noticing costs nothing. Said as a fact, the way AGENTS.md
        # has it: there is no inbox, and sort rewrites nothing they wrote. It
        # used to add "do not file them without asking", while AGENTS.md,
        # /wrapup and /tidy all say to run `./os sort`, so the AI had to guess.
        loose = sorted(os_.rel(i.path) for i in Scanner(os_).scan() if Sorter.unmanaged(i))
        loose += [os_.rel(p) for p in loose_at_top(os_.root)]
        if loose:
            listed = ", ".join(loose[:4]) + (f" and {len(loose) - 4} more" if len(loose) > 4 else "")
            print(json.dumps({"systemMessage":
                f"{len(loose)} file(s) were added by hand and aren't filed yet: "
                f"{listed}. `./os sort` files them and `./os undo` reverses it."}))
        return 0
    if as_json:
        print(json.dumps(registry["counts"], indent=2))
        return 0
    Out.title("index")
    Out.ok(f"read {registry['counts'].get('total', 0)} things and rebuilt the list")
    Out.note("INDEX.md · .claude/CATALOG.md")
    Out.raw()
    return 0


def _looks_like_a_path(text: str) -> bool:
    """Did they mean a file that is not there, rather than a sentence?

    Getting this wrong in either direction is bad: silently filing a typo'd
    path as a note loses the file they meant, and rejecting a real sentence is
    infuriating. Spaces settle it — paths people type rarely have them, and
    sentences almost always do. A link is words to keep, never a missing file."""
    if " " in text or "\n" in text or URL_RE.match(text):
        return False
    return (text.startswith(("/", "~", "./", "../"))
            or "/" in text
            or bool(re.search(r"\.[A-Za-z0-9]{1,5}$", text)))


KIND_WORDS = {"project": "work", "note": "a note",
              "asset": "a file", "skill": "a skill", "agent": "a helper"}

#: What each kind is called out loud. "project" and "asset" are internal words.
KIND_LABEL = {"project": "work", "note": "note", "asset": "file",
              "skill": "skill", "agent": "helper",
              "hook": "automatic", "archive": "archived"}


#: `Decided: ...`, `Decision — ...`. How a wrap-up writes one down. Only the
#: word and the mark after it come off: "Decided to use cedar" is kept whole.
DECISION_LEAD = re.compile(
    r"^\s*(?:decided|decision|open from)s?\s*[:\-—,·]\s*", re.I)
#: Words that open a decision, with or without that mark.
DECISION_WORDS = re.compile(r"^\s*(?:decided|decision|open from)s?\b", re.I)
#: A decision said in a sentence: "We decided the tiles will be white".
DECISION_SAID = re.compile(r"^\s*(?:we|i)(?:['’]ve|\s+have)?\s+decided\b", re.I)


def said_before(os_: Zenith, text: str) -> Path | None:
    """The item these exact words were already filed into, if there is one.

    One thought reached `os save` three times in a single 2026-09-05 session —
    two captures and a note — and the sorter dutifully made two projects out of
    it. Words that are word-for-word what was saved before are not a second
    thought about the same thing; they are the same thing arriving twice."""
    key = re.sub(r"\s+", " ", text.strip().lower())
    for entry in reversed(os_.state.get("captures") or []):
        if not isinstance(entry, dict) or entry.get("words") != key:
            continue
        where = os_.root / str(entry.get("path") or "")
        if where.exists():
            return where
    return None


def remember_save(os_: Zenith, text: str, dest: Path) -> None:
    key = re.sub(r"\s+", " ", text.strip().lower())
    # Not `state["saved"]`: that name is already taken by the timestamp
    # `save_state` writes on every run.
    seen = [e for e in (os_.state.get("captures") or [])
            if isinstance(e, dict) and e.get("words") != key]
    seen.append({"words": key, "path": os_.rel(dest), "at": now_iso()})
    os_.state["captures"] = seen[-60:]
    os_.save_state()


def decision_home(os_: Zenith, text: str) -> Item | None:
    """The item a decision names, when it names one.

    Wrap-up decisions saved with `os save` became a holding project on
    2026-09-06 and a note called "Decided 2026-09-07" the day after — a
    decision is never a thing of its own, it is a line in the thing it is
    about. Matched on the item's own name, because that is what a decision
    written at the end of a session always says."""
    hay = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "
    best, longest = None, 0
    for it in Scanner(os_).scan():
        if it.kind not in ("project", "note") or not (it.spine and it.spine.exists()):
            continue
        if it.bucket == os_.bucket_for_role("archive"):
            continue
        for name in {it.title, it.ident}:
            needle = " " + re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip() + " "
            if len(needle) > 6 and needle in hay and len(needle) > longest:
                best, longest = it, len(needle)
    return best


def write_decision(os_: Zenith, item: Item, line: str) -> int:
    """Append one dated line to an item's ## Decisions, and nothing else.

    At the end of the section, as AGENTS.md, /decide and /wrapup all say, so
    the newest is last. It used to go straight under the heading, which put
    ./os decide's lines newest-first above hand-written ones, and /find's
    "the last entry wins" then read the oldest."""
    with Lock(os_, "decision"):
        text = read_to_rewrite(item.spine)
        _meta, body = parse_frontmatter(text)
        entry = f"- {today()} · {line.rstrip().rstrip('.')}"
        lines = body.split("\n")
        start = next((n for n, text in enumerate(lines)
                      if re.match(r"##\s+Decisions\s*$", text, re.I)), None)
        if start is not None:
            end = next((n for n in range(start + 1, len(lines))
                        if SECTION_RE.match(lines[n])), len(lines))
            while end > start + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, entry)
            body = "\n".join(lines)
        else:
            body = body.rstrip() + "\n\n## Decisions\n" + entry + "\n"
        os_.snapshot(item.spine)
        flags, drop = unflag_fields(_meta)      # they have said what it is
        write_text(item.spine, set_fields(text, {"updated": today(), **flags},
                                          drop=drop, body=body))
        os_.record("edit", os_.rel(item.spine))
        os_.commit(f"{item.ident} decision")
        Indexer(os_).build()
    Out.title("decided")
    Out.ok(f"Wrote that under Decisions for {item.title}")
    Out.note(f"it's under ## Decisions in {os_.rel(item.spine)}   ·   "
             f"./os show {handle(item)}")
    Out.note("wrong spot?  ./os undo")
    Out.raw()
    return 0


def work_named_first(os_: Zenith, text: str) -> tuple:
    """The live work these words open with, and the name they use for it — as
    in "Kitchen renovation: call the plumber on Monday". Saved, that made
    `Kitchen Renovation-2`: two items under one name, instead of a next step
    for the one already there."""
    # Compared without accents, and a plural still names it: "Cafe plans" is
    # Café Plans, "Kitchen renovations" is Kitchen Renovation.
    words = text.strip()
    plain = unaccent(words)
    best, reach = None, 0
    for it in Scanner(os_).scan():
        if it.kind != "project" or it.status not in (PUSHING, HOLDING) \
                or it.bucket == os_.bucket_for_role("archive") \
                or not (it.spine and it.spine.exists()):
            continue
        for name in {it.title, it.ident}:
            said = re.match(re.escape(unaccent(name)) + r"(?:e?s)?(?=\s*(?:[:;,]|\s[-–—]|$))",
                            plain, re.I)
            if len(name) >= 3 and said and said.end() > reach:
                best, reach = it, said.end()
    if best is None:
        return None, ""
    upto = next(k for k in range(len(words) + 1) if len(unaccent(words[:k])) >= reach)
    return best, words[:upto]


def add_next_step(os_: Zenith, item: Item, said: str, text: str, landed: Path,
                  as_json: bool) -> int:
    """Write saved words into the work they name, as its next action. `landed`
    is where they were written down first, and goes once they are in."""
    step = re.sub(r"\s+", " ", text.strip()[len(said):]).strip(" :;,-–—")
    step = step[:1].upper() + step[1:]
    with Lock(os_, "save", safe="what you typed is safe in " + os_.rel(landed)
              + " — ./os sort files it when the other run is done"):
        text_was = read_to_rewrite(item.spine)
        _meta, body = parse_frontmatter(text_was)
        section = re.search(r"^##\s+Next action[ \t]*\n(.*?)(?=^##\s|\Z)", body, re.M | re.S)
        empty = re.compile(r"^([-*][ \t]*\[ \])[ \t]*$", re.M)
        if step and section and empty.search(section.group(1)):
            filled = empty.sub(lambda m: m.group(1) + " " + step, section.group(1), count=1)
            body = body[:section.start(1)] + filled + body[section.end(1):]
        elif step and section:
            kept = section.group(1).rstrip("\n")
            body = (body[:section.start(1)] + (kept + "\n" if kept else "")
                    + f"- [ ] {step}\n\n" + body[section.end(1):])
        elif step:
            body = body.rstrip() + f"\n\n## Next action\n- [ ] {step}\n"
        if step:
            os_.snapshot(item.spine)
            write_text(item.spine, set_fields(text_was, {"updated": today()}, body=body))
            os_.record("edit", os_.rel(item.spine))
            os_.commit(f"{item.ident} next action")
            Indexer(os_).build()
        landed.unlink()
    remember_save(os_, text, item.path)
    if as_json:
        print(json.dumps({"saved": os_.rel(item.path), "id": item.ident, "kind": "project",
                          "title": item.title, "filed": True, "added_to": item.ident}, indent=2))
        return 0
    Out.title("saved")
    if not step:
        Out.ok(f"you already have {item.title}")
        Out.note(f"nothing new filed   ·   ./os show {handle(item)}")
        Out.raw()
        return 0
    Out.ok(f"added to {item.title}: {trunc(step, 60)}")
    Out.note(f"it's the next action in {os_.rel(item.spine)}   ·   ./os show {handle(item)}")
    if item.status == HOLDING:
        Out.note(f"that one is being held — on the go again?  ./os push {handle(item)}")
    Out.note("wrong spot?  ./os undo")
    Out.raw()
    return 0


def cmd_decide(os_: Zenith, argv: list[str]) -> int:
    """Write one settled thing into the item it was settled about.

    `os save` guesses which item a decision belongs to and refuses when it
    cannot tell. This is the same line, said by hand, when you already know."""
    item = _claimable(os_, argv, "decide")
    line = " ".join(argv[1:]).strip()
    if not re.search(r"[^\W_]", line, re.UNICODE):
        die(f'what was decided?   ./os decide {handle(item)} "we ship Meta first"')
    warn_if_claimed(os_, item)
    return write_decision(os_, item, DECISION_LEAD.sub("", line).strip() or line)


def bring_in_what_links_point_at(original: Path, copy: Path, home: Path) -> list:
    """After a folder is copied in with its shortcuts kept as shortcuts, bring
    in what the ones pointing outside it point at. Kept as they were, those
    pointed at nothing in the copy, or back at the original, and the words
    were lost with it. One pointing inside stays a shortcut, made relative so
    it points inside the copy. Returns the shortcuts that still point outside:
    ones to nothing, or to a folder this one or the copy sits inside."""
    base = original.resolve()
    left = []
    for link in sorted(p for p in copy.rglob("*") if p.is_symlink()):
        rel = link.relative_to(copy)
        try:
            target = (original / rel).resolve()
        except (OSError, RuntimeError):         # a loop of shortcuts
            left.append(str(rel))
            continue
        if target == base or base in target.parents:
            inside = copy / target.relative_to(base)
            if Path(os.readlink(link)).is_absolute():
                link.unlink()
                link.symlink_to(os.path.relpath(inside, link.parent))
            continue
        if not target.exists() or target in base.parents or target == home \
                or target in home.parents or home in target.parents:
            left.append(str(rel))
            continue
        link.unlink()
        if target.is_dir():
            shutil.copytree(target, link, symlinks=True)
        else:
            shutil.copy2(target, link)
    return left


def cmd_save(os_: Zenith, argv: list[str]) -> int:
    """Write something down and put it where it belongs, in one step."""
    as_json = _flag(argv, "--json")
    src = _opt(argv, "--file")
    typed = ""      # the words themselves, when words are what was saved
    pointers: list = []     # shortcuts in a saved folder still pointing outside it
    if argv and argv[0] == "--":           # the "everything after this is text" separator
        argv = argv[1:]

    # A single argument naming something real is a file, not a sentence. The name
    # has to be non-empty: Path("") is the current directory, and copying the
    # folder into itself is not what anybody meant.
    if not src and len(argv) == 1 and argv[0].strip():
        lone = argv[0].strip()
        candidate = Path(lone).expanduser()
        if candidate.name and candidate.exists():
            src, argv = str(candidate), []
        elif _looks_like_a_path(lone):
            die(f"there is no file at  {lone}\n"
                '     (to save those words as a note instead, put them in quotes '
                'with something else:  ./os save "note: ' + lone + '")')

    if src:
        landed = None
        path = Path(src.strip()).expanduser()
        if not path.name or not path.exists():
            die(f"there is no file at {src!r}")
        try:
            resolved = path.resolve()
        except OSError:
            die(f"cannot read {src}")
        stage = os_.dot / "cache" / STAGING
        if resolved.parent == stage.resolve() and resolved.is_file():
            # Words `os undo` put back into staging: sort leaves them alone on
            # purpose, so saving the file by its path is how they get filed
            # after all (found 2026-09-21: check nagged, sort and save both
            # refused, no way out).
            rel_staged = os_.rel(resolved)
            os_.state["taken_back"] = [r for r in (os_.state.get("taken_back") or [])
                                       if r != rel_staged]
            os_.save_state()
            landed = resolved
            what = path.name
        elif resolved in {p.resolve() for p in loose_at_top(os_.root)}:
            # Dropped at the top of the folder: filed from where it lies, the
            # way sort files it, instead of refused as already in this folder.
            landed = os_.root / resolved.name
            what = path.name
        elif resolved == os_.root or os_.root in resolved.parents:
            die(f"{os_.rel(resolved)} is already in this folder — "
                "nothing to bring in")
        if resolved in os_.root.parents:
            die("that is a folder this one lives inside — pick something smaller")
        if landed is None:
            landed = unique_path(Creator(os_).stage() / path.name)
            try:
                # Shortcuts inside a folder come in as shortcuts. Followed, one
                # pointing back at its own folder (common in code projects) was
                # copied into itself round and round until the disk filled.
                if path.is_file():
                    shutil.copy2(path, landed)
                else:
                    shutil.copytree(path, landed, symlinks=True)
                    pointers = bring_in_what_links_point_at(path, landed, os_.root)
            except (OSError, shutil.Error) as exc:
                die(f"could not bring that in: {exc}")
            what = path.name
    else:
        text = " ".join(argv).strip()
        # Only reach for piped input when nothing at all was typed. `os save ""`
        # is somebody making a mistake, not somebody asking us to block on stdin
        # until the end of time.
        if not argv and not text and not sys.stdin.isatty():
            try:
                text = sys.stdin.read()
            except (OSError, KeyboardInterrupt):
                text = ""
        if not re.search(r"[^\W_]", text, re.UNICODE):
            # Emoji and punctuation are `\W`, so "🎉🎉🎉" and "..." land here
            # too — and being told "tell me what to save" when you plainly did
            # reads as the folder not listening. Say which of the two it was.
            die(('there are no words in that to file it by:   '
                 './os save "the thing on your mind"') if text.strip() else
                'tell me what to save:   ./os save "the thing on your mind"')
        # A decision belongs to the thing it was made about — never to a new
        # item of its own, and never to a note called "Decided <date>".
        # "We decided …" over several lines is a page of notes, not one decision.
        if DECISION_WORDS.match(text) or (DECISION_SAID.match(text) and "\n" not in text.strip()):
            home = decision_home(os_, text)
            if home is not None:
                return write_decision(os_, home, DECISION_LEAD.sub("", text).strip() or text)
            # It reads like a decision and names nothing we hold. Filing it makes
            # the stray "Decided <date>" notes this rule exists to prevent — say
            # which command puts it where it belongs instead. "I decided to
            # learn to sail" is a thought in a sentence, though, and is saved.
            if not DECISION_SAID.match(text):
                die('That reads like a decision — use ./os decide <name> "<text>"')
        already = said_before(os_, text)
        if already is not None:
            Out.title("already written down")
            Out.ok(os_.rel(already))
            Out.note("word for word what you saved before — nothing new filed")
            Out.note(f"meant to add to it?  ./os edit {handle(already.stem if already.is_file() else already.name)}")
            Out.raw()
            return 0
        typed = text
        landed = Creator(os_).capture(text)
        what = re.sub(r"\s+", " ", text.strip().split("\n")[0])[:64]
        # Work that opens with the name of work already here is a next step
        # for that one, not a second item under the same name.
        # One short line after the name is a step for it, however it reads;
        # "Kitchen renovation, buy paint" read as a note, and made a second
        # thing called Kitchen Renovation. A longer paste goes to it only when
        # it reads as work.
        named, said = work_named_first(os_, text)
        if named is not None and (
                ("\n" not in text.strip() and len(text.strip()) - len(said) <= 160)
                or Classifier(os_).classify(landed)["kind"] == "project"):
            return add_next_step(os_, named, said, text, landed, as_json)

    # By here the words are already on disk, whether or not the lock is free.
    with Lock(os_, "save", safe="what you typed is safe in "
              + os_.rel(landed) + " — ./os sort files it when the other run is done"):
        dest, verdict = Sorter(os_).file_one(landed)
        os_.commit("save")
        Indexer(os_).build()

    if dest is None:
        die("I could not work out where that goes — it is safe in " + os_.rel(landed)
            + "\n     ./os sort picks it up from there")
    if typed:
        remember_save(os_, typed, dest)
    spine = Scanner(os_).spine_of(dest) or dest
    meta, _ = parse_frontmatter(read_all(spine)) if spine.is_file() else ({}, "")
    ident = dest.stem if dest.is_file() else dest.name
    if as_json:
        print(json.dumps({"saved": os_.rel(dest), "id": ident, "kind": verdict["kind"],
                          "title": verdict["title"], "filed": True}, indent=2))
        return 0

    twin = [i for i in Finder(os_).like(verdict["title"] or what,
                                        kinds=("project", "note"))
            if os_.rel(i.path) != os_.rel(dest)]
    Out.title("saved")
    Out.ok(verdict["title"] or what)
    if twin:
        Out.warn(f"{twin[0].ident} looks like the same thing: \"{trunc(twin[0].title, 44)}\"")
        Out.note(f"keep just one?  ./os undo   ·   compare:  ./os show {handle(twin[0])}")
    Out.note(f"that's {KIND_WORDS.get(verdict['kind'], verdict['kind'])} — it's in "
             f"{os_.rel(dest)}")
    if dest.parent.name == MEDIA_FOLDER:
        Out.note(f"big files live in {os_.rel(dest.parent)} — ./os leaves them alone")
    if pointers:
        Out.warn(f"{len(pointers)} shortcut{'s' if len(pointers) > 1 else ''} in it still "
                 f"point{'' if len(pointers) > 1 else 's'} outside it, so what "
                 f"{'they point' if len(pointers) > 1 else 'it points'} at isn't in here: "
                 + ", ".join(pointers[:3]) + (" …" if len(pointers) > 3 else ""))
    if "needs-review" in verdict.get("flags", []):
        Out.warn("I wasn't sure what this one was — worth a look")
    if typed and DECISION_SAID.match(typed):
        Out.note("a decision belongs in the thing it was about — name that thing "
                 "in it and it goes straight into its ## Decisions")
    Out.note("wrong spot?  ./os undo")
    Out.raw()
    return 0


def cmd_new(os_: Zenith, argv: list[str]) -> int:
    tags = [t for t in _opt(argv, "--tags").split(",") if t.strip()]
    domain = _opt(argv, "--domain")
    anyway = _flag(argv, "--anyway", "--force")   # before the title is read off argv
    if not argv:
        die('what kind?   ./os new work "Ship the redesign"\n'
            "     kinds: work, ongoing, note, skill, helper")
    kind = argv[0]
    title = " ".join(argv[1:]).strip().strip('"')
    if not title:
        die(f'give it a name:   ./os new {kind} "Ship the redesign"')
    resolved_kind = KIND_ALIASES.get(kind.lower(), kind)
    known = list((os_.taxonomy.get("domains") or {}).keys())
    if domain and known and domain not in known:
        # `new` took any word and wrote it into the header; `./os words` then
        # refused the same word as "no domain called that". One list, both ways.
        near = difflib.get_close_matches(domain, known, n=1, cutoff=0.6)
        die(f"no subject called '{domain}' — this folder knows: {', '.join(known)}"
            + (f"\n     did you mean --domain {near[0]}?" if near else "")
            + "\n     leave --domain off and it is guessed from the name")
    if not anyway and resolved_kind in ("project", "note"):
        clash = Finder(os_).like(title, kinds=("project", "note"))
        if clash:
            first = clash[0]
            die(f"you already have {first.ident} \u2014 \"{first.title}\".\n"
                f"     Look at it:        ./os show {handle(first)}\n"
                "     Want both anyway?  add --anyway to this command")
    with Lock(os_, "new"):
        path = Creator(os_).create(kind, title, domain, tags)
        phase = NEW_STATUS.get(kind.lower(), PUSHING)
        Indexer(os_).build()
    resolved = KIND_ALIASES.get(kind.lower(), kind)
    meta, _ = parse_frontmatter(read_text(path))
    Out.title("started")
    Out.ok(title)
    Out.note(os_.rel(path))
    if resolved == "skill":
        Out.note("type  /" + Path(path).parent.name + "  to run it")
    elif resolved == "agent":
        Out.note("your AI will call on @" + Path(path).stem + " when a job suits it")
    elif resolved == "project":
        Out.note("open it and write down what good looks like here")
        Out.note("it's " + ("being pushed — give it a next action"
                            if phase == PUSHING else
                            "being held — say how often you tend to it")
                 + paint(f"    ./os {'hold' if phase == PUSHING else 'push'} {handle(Path(path).parent.name)}"
                         " flips that", S.FAINT))
    Out.raw()
    return 0


def _set_phase(os_: Zenith, argv: list[str], phase: str) -> int:
    """Move one piece of work between being pushed and being held.

    This is the move the old two-folder layout could not make without shuffling
    files around: work stops needing a next action and starts needing a
    standard, or the other way about, and it happens over and over to the same
    item. Here it is one word in the header."""
    other = HOLDING if phase == PUSHING else PUSHING
    if not argv or not argv[0].strip():
        die(f"which one?   ./os {'push' if phase == PUSHING else 'hold'} q3-okr-review"
            "      (run ./os to see the names)")
    item = Finder(os_).by_id(argv[0])
    if item is None:
        die(f"nothing here is called {argv[0]} — try  ./os find {argv[0]}")
    # A note can turn out to be something to do. "Buy a birthday present for
    # Sarah" was filed as a note, and nothing could say otherwise: push refused
    # it, new refused the near-duplicate, and moving it by hand breaks undo.
    becomes_work = item.kind == "note" and bool(item.spine) \
        and (item.spine == item.path or item.path.is_dir())
    if item.kind != "project" and not becomes_work:
        die(f"{item.ident} is {KIND_WORDS.get(item.kind, item.kind)}, not work — "
            "only work is pushed or held")
    if not (item.spine and item.spine.exists()):
        die(f"{item.ident} has no header to change — ./os check --fix")
    warn_if_claimed(os_, item)
    with Lock(os_, "phase"):
        text = read_to_rewrite(item.spine)
        meta, _body = parse_frontmatter(text)
        was = normalize_status(meta.get("status"), "project") if not becomes_work else "note"
        # Snapshot and journal it, or `./os undo` after a flip silently reverses
        # whatever came *before* the flip instead — an empty run leaves no entry
        # on the stack, and the promise is that undo takes back the last thing.
        os_.snapshot(item.spine)
        changes = {"status": phase, "updated": today()}
        flags, drop = unflag_fields(meta)       # they have said what it is
        changes.update(flags)
        if becomes_work:
            changes["type"] = TYPE_ON_DISK["project"]
        write_text(item.spine, set_fields(text, changes, drop=drop))
        os_.record("edit", os_.rel(item.spine))
        if becomes_work and item.path.is_dir():
            dest = os_.root / os_.bucket_for_role("project") / item.path.name
            item.path = os_.move_item(item.path, dest)
        elif becomes_work:
            # Into Work/<Title>/README.md, the shape saved work has, with its
            # words kept and the rest of the blueprint built around them.
            item.path = Sorter(os_).place(item.path, {
                "kind": "project", "status": phase, "title": item.title,
                "title_from_meta": True, "domain": item.domain, "tags": item.tags,
                "summary": item.summary, "flags": []})
        os_.commit(f"{item.ident} {was} -> {phase}")
        Indexer(os_).build()
    Out.title("pushing" if phase == PUSHING else "holding")
    Out.ok(item.title)
    if becomes_work:
        Out.note(f"it's work now — it's in {os_.rel(item.path)}")
        item.ident = handle(item.path.name)
    if phase == HOLDING:
        Out.note("it won't be counted as on the go, and it won't be nagged for "
                 "going quiet — that's what holding means")
        Out.note("say how often you tend to it under ## How often")
    elif not becomes_work:
        Out.note("it's on the go again — give it a next action")
    if becomes_work:     # hold would keep it work; only undo makes it a note again
        Out.note("a note after all?  ./os undo")
    else:
        Out.note(f"back the other way?  ./os {'push' if phase == HOLDING else 'hold'} {handle(item)}")
    Out.raw()
    return 0


#: How long a claim stands before it is read as forgotten. Chats crash, laptops
#: sleep and nobody types `./os release` on the way out, so a claim that never
#: expired would lock a thing away for good.
CLAIM_STALE_HOURS = 12


def claim_label() -> str:
    """Who this chat is, as far as the folder can tell.

    Two AI sessions in one folder are two runs of the same program: nothing
    tells them apart but this. Claude Code sets CLAUDE_CODE_SESSION_ID, a
    terminal sets TERM_SESSION_ID, and failing both the process that started
    us will do. That fallback is weak inside Claude Code: the process is one
    tool call's shell, gone by the next call. Reading only CLAUDE_SESSION_ID,
    which Claude Code doesn't set, a chat's claim called that chat gone on its
    very next command. CLAUDE_SESSION_ID is still read, for anything that
    sets it."""
    for var in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "TERM_SESSION_ID"):
        value = str(os.environ.get(var) or "").strip()
        if value:
            return one_line(value).replace(" ", "-")[:40]
    return f"pid-{os.getppid()}"


def claim_owner(label: str) -> str:
    """The chat behind a claim label, without the `(what for)` note after it."""
    return re.sub(r"\s*\(.*\)\s*$", "", str(label or "")).strip()


def read_claim(raw) -> tuple[str, str]:
    """`claimed: <label> <when>` split back into its two halves.

    The label may carry words of its own (`./os claim x --as "the copy pass"`), so
    the split is from the right: the stamp is the one part with no spaces."""
    text = one_line(str(raw or "")).strip()
    if not text:
        return "", ""
    label, _, when = text.rpartition(" ")
    if not label:
        return text, ""
    return label.strip(), when.strip()


def claim_gone(label: str) -> bool:
    """Is the chat that made this claim demonstrably gone?

    A claim labelled by process id can be checked against the live process
    table; one labelled by a session id cannot, and is left to the clock.
    A second chat on 2026-09-09 could not tell a live claim from a dead one
    and released it — this answers the half of that question the folder can."""
    hit = re.fullmatch(r"pid-(\d+)", claim_owner(label))
    if not hit:
        return False
    try:
        os.kill(int(hit.group(1)), 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError, ValueError, OverflowError):
        return False
    return False


def claim_hours(when: str) -> float:
    try:
        stamp = _dt.datetime.strptime(str(when)[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return 0.0
    return max((_dt.datetime.now() - stamp).total_seconds() / 3600.0, 0.0)


def claim_words(raw) -> str:
    """A claim as a person reads it: `claimed by <label> 2h ago`."""
    label, when = read_claim(raw)
    if not label:
        return ""
    hours = claim_hours(when) if when else 0.0
    if not when:
        ago = ""
    elif hours < 1:
        ago = f" {int(hours * 60)}m ago"
    elif hours < 48:
        ago = f" {int(hours)}h ago"
    else:
        ago = f" {int(hours // 24)}d ago"
    if claim_gone(label):
        stale = " — that chat is gone"
    else:
        stale = " — stale" if when and hours >= CLAIM_STALE_HOURS else ""
    # Claude Code's session id is a long string of letters and digits; the
    # first eight tell two chats apart well enough to read.
    said = re.sub(r"^([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                  r"chat \1", label, flags=re.I)
    return f"claimed by {said}{ago}{stale}"


def warn_if_claimed(os_: Zenith, item: Item) -> None:
    """One line to whoever is about to change something another chat is holding.

    Said, never enforced. The folder cannot know which of two chats is right,
    and refusing the change would only send somebody to a text editor to make
    it unrecorded — which is the accident this is here to stop."""
    if not item.claim:
        return
    label, _when = read_claim(item.claim)
    if not label or claim_owner(label) == claim_label():
        return
    Out.warn(f"{claim_words(item.claim)}; "
             f"./os release {handle(item)} if that chat is done")


def _claimable(os_: Zenith, argv: list[str], verb: str) -> Item:
    if not argv or not argv[0].strip():
        die(f"which one?   ./os {verb} ship-the-redesign      (run ./os to see the names)")
    item = Finder(os_).by_id(argv[0])
    if item is None:
        die(f"nothing here is called {argv[0]} — try  ./os find {argv[0]}")
    if not (item.spine and item.spine.exists()):
        die(f"{item.ident} has no header to write into — ./os check --fix")
    return item


def cmd_claim(os_: Zenith, argv: list[str]) -> int:
    """Say out loud that this chat is working on something.

    Two AI sessions in one folder is normal now, and on 2026-09-08 two of them
    built the same thing at the same path within the minute — the second simply
    wrote over the first. Nothing here locks a file: the point is that the other
    chat is *told*, in the one place both of them already read."""
    note = one_line(_opt(argv, "--as")).strip()
    item = _claimable(os_, argv, "claim")
    mine = claim_label() + (f" ({note})" if note else "")
    with Lock(os_, "claim"):
        text = read_to_rewrite(item.spine)
        meta, _body = parse_frontmatter(text)
        holder, when = read_claim(meta.get("claimed"))
        took_over = ""
        if holder and claim_owner(holder) != claim_label():
            if claim_hours(when) < CLAIM_STALE_HOURS and not claim_gone(holder):
                Out.title("already claimed")
                Out.warn(claim_words(meta.get("claimed")))
                Out.note(f"./os release {handle(item)} if that chat is done, "
                         "then claim it again")
                Out.raw()
                return 1
            took_over = holder
        os_.snapshot(item.spine)
        # `updated:` is left alone on purpose. A claim says who is holding the
        # thing, not that the work moved on, and stamping it would hide how
        # long the item has actually sat still.
        write_text(item.spine, set_fields(text, {"claimed": f"{mine} {now_iso()}"}))
        os_.record("edit", os_.rel(item.spine))
        os_.commit(f"{item.ident} claimed by {mine}")
    Out.title("claimed")
    Out.ok(item.title)
    if took_over and claim_gone(took_over):
        Out.note(f"{took_over} had it, but that chat is gone — taken over")
    elif took_over:
        Out.note(f"{took_over} had it, but that claim was over "
                 f"{CLAIM_STALE_HOURS} hours old — taken over")
    Out.note("other chats see this on ./os and ./os show   ·   "
             f"./os release {handle(item)} when you're done")
    Out.raw()
    return 0


def cmd_release(os_: Zenith, argv: list[str]) -> int:
    """Let go of something claimed — whoever claimed it.

    Anybody can release anything: it is the way out when the chat that claimed
    something is closed and cannot say so itself."""
    item = _claimable(os_, argv, "release")
    with Lock(os_, "claim"):
        text = read_to_rewrite(item.spine)
        meta, _body = parse_frontmatter(text)
        holder, _when = read_claim(meta.get("claimed"))
        if not holder:
            Out.title("release")
            Out.note(f"nothing was holding {item.title}")
            Out.raw()
            return 0
        was = claim_words(meta.get("claimed"))
        os_.snapshot(item.spine)
        write_text(item.spine, set_fields(text, drop=("claimed",)))
        os_.record("edit", os_.rel(item.spine))
        os_.commit(f"{item.ident} released ({holder})")
    Out.title("released")
    Out.ok(item.title)
    if claim_owner(holder) != claim_label():
        Out.note(f"it was {was}")
    Out.raw()
    return 0


#: Where a markdown link points: `[text](here)`. No colon, so web links and
#: anything else off this disk are left alone.
LINK_TARGET = re.compile(r"(\[[^\]\n]*\]\()([^)#:\n]+)")


def _relink(os_: Zenith, old: Path, new: Path) -> int:
    """Point every markdown link that reached `old` at `new` instead.

    Renamed, a note kept every link to it saying the old file name, and
    `./os check` then reported the broken link the rename had made. Only
    links change: the name said in someone's own sentences is their writing.
    Each file is snapshotted first, so undo takes this back with the rename.
    Returns how many files changed."""
    before = os.path.normpath(str(old))
    changed = 0
    for bucket in os_.buckets():
        for top, dirs, files in os.walk(os_.root / bucket):
            dirs[:] = [d for d in dirs if not d.startswith(".")
                       and d not in IGNORE_FOLDERS and d not in ("node_modules", "__pycache__")]
            for name in files:
                path = Path(top) / name
                if path.suffix.lower() != ".md" or path.is_symlink():
                    continue
                text = read_all(path)
                if old.name not in text and old.name.replace(" ", "%20") not in text:
                    continue

                def swap(m: re.Match) -> str:
                    if m.group(2).startswith("/"):
                        return m.group(0)
                    parts, at = m.group(2).split("/"), top
                    for i, part in enumerate(parts):
                        at = os.path.normpath(os.path.join(at, part.replace("%20", " ")))
                        if at == before:
                            parts[i] = new.name if " " in part else new.name.replace(" ", "%20")
                            return m.group(1) + "/".join(parts)
                    return m.group(0)

                linked = LINK_TARGET.sub(swap, text)
                if linked != text:
                    os_.snapshot(path)
                    write_text(path, linked)
                    os_.record("edit", os_.rel(path))
                    changed += 1
    return changed


def cmd_rename(os_: Zenith, argv: list[str]) -> int:
    """Give something a new name, on disk and in its header, in one move.

    The name is the handle, so renaming by hand meant a title that said one
    thing and a folder that said another, and a `./os show` that found neither.
    Folders are Title Case With Spaces, notes stay kebab-case files, the
    card beside a file moves with it, and links to it follow."""
    if not argv or not argv[0].strip():
        die('which one?   ./os rename q3-okr-review "Q4 OKR review"')
    item = Finder(os_).by_id(argv[0])
    if item is None:
        die(f"nothing here is called {argv[0]} — try  ./os find {argv[0]}")
    if item.kind not in ("project", "note", "asset", "archive"):
        die(f"{item.ident} is {KIND_WORDS.get(item.kind, item.kind)} — rename its folder by hand")
    title = one_line(" ".join(argv[1:])).strip().strip('"')
    if not re.search(r"[^\W_]", title, re.UNICODE):
        die(f'called what?   ./os rename {handle(item)} "the new name"')
    slug = slugify(title, 44)
    if not slug:
        die("that name has no letters or numbers in it to make a file name from")
    warn_if_claimed(os_, item)
    is_dir = not item.path.is_file()
    wanted = folder_name(title, slug) if is_dir else f"{slug}{item.path.suffix}"
    target = item.path.with_name(wanted)
    # A Mac's disk doesn't tell capitals apart, so `Q3 Okr Review` to
    # `Q3 OKR Review` found the item itself in the way and was refused.
    try:
        recased = target != item.path and target.exists() and os.path.samefile(target, item.path)
    except OSError:
        recased = False
    if target != item.path and not recased and (target.exists() or target.is_symlink()):
        die(f"there is already something called {wanted} in {os_.rel(item.path.parent)}")
    if target != item.path and ignored(target):
        die(f"./os never looks inside anything called {wanted} — pick another name")
    with Lock(os_, "rename"):
        moved = item.path
        if recased:
            # Straight across would land on "-2", the old name still being
            # taken as far as the disk can tell; so by way of a spare name.
            moved = os_.move_item(moved, moved.with_name(moved.name + " (renaming)"))
        if target != item.path:
            moved = os_.move_item(moved, target)
        spine = Scanner(os_).spine_of(moved) if is_dir else moved
        if spine is None and is_dir:
            spine = moved / "README.md"
        card = moved.with_name(moved.name + ".card.md")
        for headed in ([spine] if spine and spine.exists() and spine.suffix.lower() in TEXT_SUFFIXES else []) \
                + ([card] if card.exists() else []):
            os_.snapshot(headed)
            stamp_file(headed, {"title": title}, force=("title",))
            # The heading is the title said again; it must not keep saying the old one.
            text = read_utf8(headed)
            meta, body = parse_frontmatter(text or "")
            flags, drop = unflag_fields(meta)
            retitled = False
            lead = re.match(r"^(#\s+)(.+)$", body.split("\n", 1)[0])
            if text is not None and lead \
                    and lead.group(2).strip().lower() == item.title.strip().lower():
                body = lead.group(1) + title + body[len(lead.group(0)):]
                retitled = True
            if text is not None and (retitled or flags or drop):
                write_text(headed, set_fields(text, flags, drop=drop,
                                              body=body if retitled else None))
            os_.record("edit", os_.rel(headed))
        relinked = _relink(os_, item.path, moved) if moved != item.path else 0
        os_.save_state()
        os_.commit(f"renamed {item.ident} -> {moved.stem if moved.is_file() else moved.name}")
        Indexer(os_).build()
    Out.title("renamed")
    Out.ok(f"{item.title} \u2192 {title}")
    Out.note(os_.rel(moved) + f"   ·   ./os show {handle(moved.stem if moved.is_file() else moved.name)}")
    if relinked:
        Out.note(f"links to it now use the new name, in {relinked} "
                 + ("file" if relinked == 1 else "files"))
    Out.note("wrong?  ./os undo")
    Out.raw()
    return 0


def cmd_hold(os_: Zenith, argv: list[str]) -> int:
    return _set_phase(os_, argv, HOLDING)


def cmd_push(os_: Zenith, argv: list[str]) -> int:
    return _set_phase(os_, argv, PUSHING)


def cmd_find(os_: Zenith, argv: list[str]) -> int:
    as_json = _flag(argv, "--json")
    kind = _opt(argv, "--kind")
    bucket = _opt(argv, "--in")
    limit = _count(argv, "--limit", 20)
    query = " ".join(argv).strip()
    if not query:
        die("what are you looking for?   ./os find token refresh")
    finder = Finder(os_)
    hits = finder.search(query, limit=limit, kind=kind, bucket=bucket)
    if as_json:
        print(json.dumps([{"score": s, "id": i.ident, "title": i.title, "kind": i.kind,
                           "path": os_.rel(i.path), "snippet": sn} for s, i, sn in hits], indent=2))
        return 0
    Out.title("found", f'"{query}"')
    if finder.corrected:
        swaps = ", ".join(f"{was} → {now}" for was, now in finder.corrected.items())
        Out.note(f"no exact match, so I searched for  {swaps}")
    if not hits:
        Out.warn("nothing matched — try fewer words, or a different one")
        Out.raw()
        return 1
    for score, item, snippet in hits:
        Out.raw("  " + paint(item.title, S.B)
                + paint(f"  · {KIND_LABEL.get(item.kind, item.kind)}", S.FAINT))
        Out.raw("          " + paint(trunc(os_.rel(item.path), 74), S.MUTE))
        if snippet:
            Out.raw("          " + paint(trunc(snippet, 74), S.FAINT))
    Out.raw()
    return 0


def _reveal(target: Path) -> bool:
    """Show something in the desktop's own file browser. False if we cannot."""
    if sys.platform == "darwin":
        command = ["open", "-R", str(target)]
    elif sys.platform.startswith("win"):
        command = ["explorer", "/select,", str(target)]
    else:
        # Linux: no "reveal this file" standard, so open the folder it is in
        command = ["xdg-open", str(target.parent)]
    try:
        subprocess.run(command, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, FileNotFoundError):
        return False


SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


def _sections(body: str) -> dict:
    """A markdown body split into its `## ` sections, in order."""
    out, current, buf = {}, "", []
    for line in body.split("\n"):
        m = SECTION_RE.match(line)
        if m:
            if current:
                out[current.lower()] = "\n".join(buf).strip()
            current, buf = m.group(1), []
        elif current:
            buf.append(line)
    if current:
        out[current.lower()] = "\n".join(buf).strip()
    return out


def _clean(text: str, limit: int = 3) -> list:
    """Readable lines from a section: no HTML comments, no empty checkboxes.
    A limit of 0 keeps them all."""
    lines = []
    for raw in re.sub(r"<!--.*?-->", "", text, flags=re.S).split("\n"):
        line = raw.strip()
        if not line or line in ("-", "- [ ]", "*") or line.startswith(("#", "```", "|", "---")):
            continue
        lines.append(re.sub(r"^(?:[-*+]|\d+\.)\s+(\[[ xX]\]\s*)?", "", line))
        if limit and len(lines) >= limit:
            break
    return lines


LINE_DAY = re.compile(r"^\W*(\d{4}-\d{2}-\d{2})")


def _newest(lines: list, limit: int) -> list:
    """The newest `limit` dated lines of a Log or Decisions, oldest first.

    Both are appended to, so the bottom is usually newest. But `./os decide`
    wrote newest-first until 2026-09-29, and some people keep a log that way,
    so the date decides. A line with no date goes with the one above it."""
    day, keyed = "", []
    for n, line in enumerate(lines):
        m = LINE_DAY.match(line)
        day = m.group(1) if m else day
        keyed.append((day, n, line))
    return [line for _day, _n, line in sorted(keyed)[-limit:]]


def last_logged(body: str) -> str:
    """The newest day written in a `## Log`, or "" when there is none. A date
    still to come is a plan, not a touch."""
    if not re.search(r"^##\s+Log\s*$", body, re.M | re.I):
        return ""
    days = [m.group(1) for m in map(LINE_DAY.match, _clean(_sections(body).get("log", ""), 0))
            if m and m.group(1) <= today()]
    return max(days, default="")


def cmd_show(os_: Zenith, argv: list[str]) -> int:
    """Everything worth knowing about one item, without opening a file."""
    if not argv or not argv[0].strip():
        die("which one?   ./os show q3-okr-review      (run ./os to see the names)")
    ident = argv[0]
    item = Finder(os_).by_id(ident)
    if item is None:
        die(f"nothing here is called {ident} — try  ./os find {ident}")

    Out.title(item.title, KIND_LABEL.get(item.kind, item.kind))
    Out.raw()
    Out.kv("where", os_.rel(item.path), 14)
    if item.status and item.status != "—":
        Out.kv("state", item.status, 14)
    age = days_since(item.updated)
    Out.kv("touched", item.updated + paint(
        "   today" if age <= 0 else (f"   {age} days ago" if age > 1 else "   yesterday"),
        S.AMBER if age >= int(os_.thresholds["stale_project_days"]) else S.FAINT), 14)
    if item.tags:
        Out.kv("tags", ", ".join(item.tags[:8]), 14)
    if item.claim:
        Out.kv("claimed", claim_words(item.claim), 14)

    body = ""
    if item.spine and item.spine.exists() and item.spine.suffix.lower() in TEXT_SUFFIXES:
        # All of it: Log and Decisions grow at the bottom of their sections,
        # and those newest lines are what this is asked for.
        _, body = parse_frontmatter(read_all(item.spine))
    parts = _sections(body)

    def block(heading: str, label: str, limit: int = 3, tone: str = S.INK) -> None:
        text = parts.get(heading.lower(), "")
        if heading in ("next action", "open questions"):
            # ticked off is done or answered: it is no longer what is next
            text = re.sub(r"(?m)^\s*(?:[-*+]|\d+\.)\s+\[[xX]\].*$", "", text)
        if heading in ("log", "decisions"):
            lines = _newest(_clean(text, 0), limit)
        else:
            lines = _clean(text, limit)
        if not lines:
            return
        Out.raw()
        Out.raw("  " + paint(label.upper(), S.B, S.GOLD))
        for line in lines:
            Out.raw("    " + paint(trunc(line, 70), tone))

    block("what good looks like", "good looks like", 2, S.MUTE)
    block("next action", "next", 3, S.INK)
    block("how often", "how often", 2, S.MUTE)
    block("where it stands", "where it stands", 3, S.MUTE)
    block("keeps coming back", "keeps coming back", 3, S.INK)
    # Headings from files written before Projects and Ongoing merged.
    block("done looks like", "done looks like", 2, S.MUTE)
    block("the standard", "the standard", 3, S.MUTE)
    block("in one line", "in one line", 2, S.MUTE)
    block("open questions", "open questions", 3, S.AMBER)
    block("decisions", "decided", 3, S.MUTE)
    block("log", "lately", 3, S.FAINT)
    if not any(parts.get(h) for h in ("what good looks like", "next action", "how often",
                                      "where it stands", "keeps coming back",
                                      "done looks like", "the standard",
                                      "in one line", "open questions", "decisions", "log")):
        head = item.title.lower().rstrip(".")
        summary = [line for line in _clean(body, 4)
                   if not line.lower().startswith(head[:40])][:3]
        if summary:
            Out.raw()
            for line in summary:
                Out.raw("    " + paint(trunc(line, 70), S.MUTE))

    Out.raw()
    Out.note(f"./os edit {handle(item)}  to change it   ·   "
             + (f"./os back {handle(item)}  to bring it back"
                if item.bucket == os_.bucket_for_role("archive") else
                f"./os close {handle(item)}  when it's no longer live"))
    Out.raw()
    return 0


def cmd_last(os_: Zenith, argv: list[str]) -> int:
    """What happened the last time anyone worked here, read from the items
    themselves — the latest ## Log line of everything touched that day, plus
    what ./os itself filed. Any AI can run it cold; nothing is remembered
    outside the folder."""
    items = Scanner(os_).scan()
    history = os_.state.get("history", [])

    def logged(item: Item) -> list:
        if not (item.spine and item.spine.exists()
                and item.spine.suffix.lower() in TEXT_SUFFIXES):
            return []
        _, body = parse_frontmatter(read_ends(item.spine))
        out = []
        for line in _clean(_sections(body).get("log", ""), 0):
            m = re.match(r"(\d{4}-\d{2}-\d{2})\s*[—:-]?\s*(.*)", line)
            if m:
                out.append((m.group(1), m.group(2).strip() or line))
        return out

    entries = []       # (day, title, handle, text)
    for item in items:
        if item.bucket.startswith("_"):
            continue
        for day, text in logged(item):
            entries.append((day, item.title, handle(item), text))
    days = sorted({d for d, *_ in entries} | {h["at"][:10] for h in history if h.get("at")},
                  reverse=True)
    if _flag(argv, "--json"):
        day = days[0] if days else ""
        print(json.dumps({"day": day,
                          "touched": [{"title": t, "name": n, "log": x}
                                      for d, t, n, x in entries if d == day],
                          "filed": [h for h in history if h.get("at", "")[:10] == day]},
                         indent=2))
        return 0
    if not days:
        Out.title("last time", "nothing yet")
        Out.note("Nothing has been logged here. Work on something, then ./os last shows it.")
        Out.raw()
        return 0
    day = days[0]
    age = days_since(day)
    when = "today" if age <= 0 else ("yesterday" if age == 1 else f"{age} days ago")
    Out.title("last time", f"{day} · {when}")
    touched = [(t, n, x) for d, t, n, x in entries if d == day]
    if touched:
        Out.raw()
        for title, name, text in touched[:12]:
            Out.raw("  " + paint(title, S.B, S.INK))
            Out.raw("    " + paint(trunc(text, 72), S.MUTE))
        if len(touched) > 12:
            Out.raw("  " + paint(f"… and {len(touched) - 12} more", S.FAINT))
    filed = [h for h in history if h.get("at", "")[:10] == day]
    if filed:
        Out.raw()
        Out.raw("  " + paint("FILED", S.B, S.GOLD))
        for h in filed[-6:]:
            Out.raw("    " + paint(trunc(h.get("label", ""), 72), S.FAINT))
    if touched:
        Out.raw()
        Out.note(f"./os show {touched[0][1]}  for where it stands now")
    Out.raw()
    return 0


def cmd_open(os_: Zenith, argv: list[str]) -> int:
    if not argv or not argv[0].strip():
        die("which one?   ./os open q3-okr-review")
    item = Finder(os_).by_id(argv[0])
    if item is None:
        die(f"nothing here is called {argv[0]} — try  ./os find {argv[0]}")
    # the thing itself, not the card describing it: `open` answers "where is it",
    # and for a PDF sitting in Notes that means the file, not its card
    target = item.path
    print(str(target))
    if sys.stdout.isatty():
        _reveal(target)
    return 0


def cmd_doctor(os_: Zenith, argv: list[str]) -> int:
    fix = _flag(argv, "--fix")
    as_json = _flag(argv, "--json")
    result = Doctor(os_).run(fix=fix)
    if fix:
        # Whatever was repaired, the list and the catalog must say so — a
        # skill's edited description sat stale in CATALOG.md until the next
        # unrelated rebuild (snag, 2026-08-31).
        Indexer(os_).build()
    if as_json:
        print(json.dumps(result, indent=2))
        return 0 if not any(i["level"] == "error" for i in result["issues"]) else 1
    Out.title("check", f"{result['items']} things looked at")
    for line in result.get("repaired", []):
        Out.ok("fixed: " + line)
    if not result["issues"]:
        Out.ok("all good — nothing broken, nothing missing")
        Out.raw()
        return 0
    groups: dict[str, list[dict]] = {}
    for issue in result["issues"]:
        groups.setdefault(issue["level"], []).append(issue)
    # Show a few of each KIND of finding rather than the first forty overall.
    # Forty near-duplicates would otherwise push a one-off warning off the end,
    # and the one-off is usually the one worth reading.
    PER_KIND = 4
    for level in ("error", "warn", "hint"):
        emit = {"error": Out.bad, "warn": Out.warn, "hint": Out.info}[level]
        seen: dict = {}
        hidden = 0
        for issue in groups.get(level, []):
            code = issue.get("code", "")
            seen[code] = seen.get(code, 0) + 1
            if seen[code] > PER_KIND:
                hidden += 1
                continue
            emit(issue["message"])
            detail = "  ".join(x for x in (issue.get("path"), issue.get("fix")) if x)
            if detail:
                Out.note(detail)
        for code, n in seen.items():
            if n > PER_KIND:
                Out.note(f"...and {n - PER_KIND} more like '{code}'")
                hidden -= n - PER_KIND
        if hidden > 0:
            Out.note(f"...and {hidden} more {level}s")
    Out.raw()
    # Only when something listed is one --fix repairs: offered over a list of
    # judgement calls, it ran, fixed nothing, and said the same again.
    if not fix and any(i.get("fix") == "./os check --fix" for i in result["issues"]):
        Out.note("./os check --fix   fixes everything that is safe to fix on its own")
    Out.raw()
    return 0 if not groups.get("error") else 1


def cmd_review(os_: Zenith, argv: list[str]) -> int:
    as_json = _flag(argv, "--json")
    report = Reviewer(os_).run()
    if as_json:
        print(json.dumps(report, indent=2))
        return 0
    Out.title("tidy", today())

    def block(label: str, rows: list, render, style=S.INK) -> None:
        if not rows:
            return
        Out.raw("  " + paint(label.upper(), S.B, S.GOLD) + paint(f"  ({len(rows)})", S.FAINT))
        for row in rows[:12]:
            Out.raw("    " + render(row))
        if len(rows) > 12:
            Out.note(f"...and {len(rows) - 12} more")
        Out.raw()

    block("dropped in, not filed yet", report["unfiled"],
          lambda t: paint(trunc(str(t), 70), S.AMBER))
    block("on the go", report["active"][:8],
          lambda r: pad(trunc(r["title"], 44), 46)
                    + paint(f"{r['age']}d ago", S.FAINT))
    block("not touched in a while", report["stale"],
          lambda r: pad(trunc(r["title"], 44), 46)
                    + paint(f"{r['age']}d ago", S.AMBER))
    block("keeping level", report.get("holding", [])[:8],
          lambda r: pad(trunc(r["title"], 44), 46)
                    + paint(f"last tended {r['age']}d ago", S.FAINT))
    block("gone quiet — still pushing these?", report["archive_candidates"],
          lambda r: pad(trunc(r["title"], 40), 42)
                    + paint(f"./os hold {handle(r['id'])}", S.FAINT))
    block("marked done but still sitting in Work", report["shipped"],
          lambda r: trunc(r["title"], 46))
    block("I wasn't sure where these went", report["unsure"],
          lambda r: pad(trunc(r["title"], 40), 42)
                    + paint(f"now in {r['where']}", S.FAINT))
    block("might be the same thing twice", report["duplicates"],
          lambda r: paint(trunc(r["message"], 72), S.MUTE))

    if report.get("routines"):
        Out.raw("  " + paint("YOU DO THESE BY HAND EVERY TIME", S.B, S.GOLD)
                + paint(f"  ({len(report['routines'])})", S.FAINT))
        for row in report["routines"]:
            Out.raw("    "
                    + pad(trunc(row["title"], 40), 42)
                    + paint(f'you wrote "{row["said"]}"', S.FAINT))
        Out.note("a skill writes the steps down once, so your AI just does it:")
        Out.note(f'./os new skill "{trunc(report["routines"][0]["title"], 40)}"'
                 + paint("   (or say /make-skill in the chat)", S.FAINT))
        Out.raw()

    cache = report.get("study_cache") or {}
    if cache.get("sources", 0) > Reviewer.STUDY_CACHE_NAG:
        Out.raw("  " + paint("SOURCES /LEARN HAS KEPT", S.B, S.GOLD)
                + paint(f"  ({cache['sources']}, {human_size(cache['bytes'])})", S.FAINT))
        Out.note("they are fetched again on demand, so they are safe to drop")
        Out.note("./os learn --cached   lists them   ·   ./os learn --forget <id> …")
        Out.raw()

    if not any([report["unfiled"], report["stale"], report["archive_candidates"],
                report["duplicates"], report["shipped"], report["unsure"],
                report.get("routines")]):
        Out.ok("nothing stale, nothing stuck, nothing doubled up")
    Out.raw()
    return 0


def cmd_close(os_: Zenith, argv: list[str]) -> int:
    """Take something out of the live folder.

    Not "finished" — things leave because you stopped carrying them, and that
    is as true of shipped work as of abandoned work. The word the folder uses
    should not claim more than it knows."""
    if not argv or not argv[0].strip():
        die("which one?   ./os close q3-okr-review      (run ./os to see the names)")
    with Lock(os_, "archive"):
        dest = Archivist(os_).archive(argv[0])
        Indexer(os_).build()
    Out.title("put away")
    Out.ok(os_.rel(dest))
    Out.note("it still turns up in ./os find — nothing gets deleted here")
    Out.note("still going, just quietly?  ./os back it, then ./os hold it")
    Out.note(f"changed your mind?  ./os back {handle(dest.stem if dest.is_file() else dest.name)}")
    Out.raw()
    return 0


def cmd_back(os_: Zenith, argv: list[str]) -> int:
    if not argv or not argv[0].strip():
        die("which one?   ./os back q3-okr-review")
    with Lock(os_, "restore"):
        dest = Archivist(os_).restore(argv[0])
        Indexer(os_).build()
    Out.title("back out")
    Out.ok(os_.rel(dest))
    Out.raw()
    return 0


def cmd_undo(os_: Zenith, argv: list[str]) -> int:
    anyway = _flag(argv, "--anyway")
    undo = Undo(os_)
    entry = undo.peek()
    if entry is None:
        Out.title("undo")
        Out.warn("nothing to undo — I haven't changed anything yet")
        Out.raw()
        return 1
    # Undo puts files back as the step found them. Words written in one since
    # would go too — five decisions and a log line, in a real folder — so it
    # stops and says which, and goes ahead only when told.
    since = [] if anyway else undo.written_since(entry)
    if since:
        Out.title("undo")
        Out.warn(f"'{entry.get('label', '')}' was {entry.get('at', '')[:16].replace('T', ' ')}, "
                 "and since then these have been written in:")
        for rel in since[:10]:
            Out.note(rel)
        Out.note("undoing it would take those later words out of them too; nothing has changed")
        Out.note("to undo it anyway (what they say now is kept aside):  ./os undo --anyway")
        Out.raw()
        return 1
    with Lock(os_, "undo"):
        result = undo.revert()
        Indexer(os_).build()
    Out.title("undone")
    if not result["restored"]:
        Out.warn(f"there was nothing left to reverse in '{result['label']}'")
        Out.note("whatever it made has since been moved or removed by hand")
    else:
        changes = f"{result['restored']} change" + ("" if result["restored"] == 1 else "s")
        Out.ok(f"put back the last '{result['label']}' — {changes} reversed")
        for f in result["failed"][:10]:
            Out.warn("couldn't put this one back: " + str(f))
    # Said every time, never capped: this is where words written after the
    # step now are, and nobody would think to look there.
    for where, copy in result["kept"]:
        Out.warn(f"{where} had words written in since, and undo took them out with the step; "
                 f"the whole file as it was is kept in {copy}")
    Out.raw()
    return 0 if result["restored"] else 1


def cmd_backup(os_: Zenith, argv: list[str]) -> int:
    out = Backup(os_).snapshot()
    kept = sorted((os_.dot / "backups").glob("*.zip"))
    total = sum(z.stat().st_size for z in kept)
    Out.title("backup")
    Out.ok(os_.rel(out) + paint(f"   {human_size(out.stat().st_size)}", S.FAINT))
    if len(kept) > 1:
        Out.note(f"{len(kept)} kept, {human_size(total)} in all — older ones are "
                 "dropped automatically")
    Out.note("move one somewhere else now and then; a copy on the same disk is "
             "not really a backup")
    Out.raw()
    return 0


# What the hooks hand the AI is written as facts, not orders: Claude Code's
# hooks docs warn that text framed as system commands can trip its
# prompt-injection defences, and then it is shown to the person instead of read.
PLAIN_SPEECH = (
    "This person talks in plain English. Capture, bucket, taxonomy, front matter, "
    "index, sort and health score are the folder's words, not theirs: to them it is "
    '"I wrote that down" and "it\'s in Notes as how-to-run-a-retro.md". The machinery '
    'gets explained only when they ask (AGENTS.md, "Talk like a person").'
)

FIRST_TIME = """{name} — this folder holds everything the person is working on, and keeps itself organised.

Nothing has been saved here yet: this is the person's first visit, and they almost certainly \
have no idea what the folder does.

A first visit here (AGENTS.md, "First, always") is a hello, then a line or two saying plainly \
that whatever they say gets written down and put in the right place for them — they never \
pick a folder or name a file — then a question about what they are working on at the moment.

Commands, folder names, how many things are in here and how the system works all wait until \
they ask. "Capture" is not a word they use."""

#: The note that holds what is known about the person — what to call them, how
#: they want answers. Its first lines go in every brief, so a thing said once is
#: known in every session after. A folder of that name works too.
ABOUT_THEM = "about-me"
ABOUT_THEM_LINES = 15


def _about_them(os_: Zenith, items: list) -> list[str]:
    """The first lines of their About me note, headed by where it is.

    Nothing when there is no such note. Headings, `<!-- prompts -->` and empty
    template bullets are left out: what is wanted is what they said."""
    archive = os_.bucket_for_role("archive")
    notes = sorted((i for i in items if i.kind == "note" and i.bucket != archive
                    and ABOUT_THEM in (slugify(i.title), slugify(i.ident))
                    and i.spine and i.spine.is_file()),
                   key=lambda i: len(i.path.parts))
    if not notes:
        return []
    note = notes[0]
    _, body = parse_frontmatter(read_text(note.spine, 60_000))
    lines = [line.strip() for line in COMMENT_RE.sub("", body).split("\n")]
    said = [line for line in lines if line and not line.startswith("#")
            and not re.fullmatch(r"[-*]\s*(\[[ xX]\])?\s*", line)]
    out = [f"About them ({os_.rel(note.spine)}):"]
    out += ["  " + (line if len(line) <= 200 else line[:199].rsplit(" ", 1)[0] + " …")
            for line in said[:ABOUT_THEM_LINES]]
    if note.is_dir:
        more = sorted(p.name for p in note.path.iterdir() if p.is_file() and p != note.spine
                      and p.suffix.lower() == ".md" and not p.name.endswith(".card.md"))
        if more:
            out.append(f"  More in {os_.rel(note.path)}/: " + ", ".join(more[:6])
                       + (" …" if len(more) > 6 else ""))
    return out if len(out) > 1 else []


def _brief_text(os_: Zenith) -> str:
    items = Scanner(os_).scan()
    theirs = [i for i in items if i.kind in ("project", "note", "asset")]
    loose = [i.path for i in items if Sorter.unmanaged(i)] + loose_at_top(os_.root)
    # Whatever the folder is called now: a brief that opened "ZENITH" after
    # `./os name --name Atlas` had the AI calling it the old name.
    name = str(os_.config.get("name") or "Zenith").upper()
    if not theirs:
        return FIRST_TIME.format(name=name)

    stale_days = int(os_.thresholds["stale_project_days"])
    active = sorted([i for i in items if i.kind == "project" and i.status == PUSHING],
                    key=lambda i: days_since(i.updated))
    held = sorted([i for i in items if i.kind == "project" and i.status == HOLDING
                   and i.bucket != os_.bucket_for_role("archive")],
                  key=lambda i: days_since(i.updated))
    errors = [i for i in Doctor(os_).run(items=items)["issues"] if i["level"] == "error"]

    def ago(days: int) -> str:
        return "today" if days <= 0 else ("yesterday" if days == 1 else f"{days} days ago")

    def things(n: int) -> str:
        return f"{n} thing" + ("" if n == 1 else "s")

    out = [f"{name} — one folder holding this person's work, notes and files. "
           "Work is in one of two phases: pushing (has a next action) or holding "
           "(has a standard, no next action, and is not late for anything). "
           "The full list is INDEX.md; the rules are AGENTS.md.", "", "Right now:"]
    # The name in brackets is what ./os show and the rest take. A title cut
    # to fit is not a name, and show refused it.
    if active:
        shown = "; ".join(
            f"{_shorten(i.title, 48)} [{handle(i)}] (last touched {ago(days_since(i.updated))}"
            + (", going cold)" if days_since(i.updated) >= stale_days else ")")
            for i in active[:4])
        if len(active) > 4:
            shown += f"; and {len(active) - 4} more open — the full list is INDEX.md"
        out.append("- On the go: " + shown)
    else:
        out.append("- Nothing being pushed right now.")
    if held:
        out.append("- Being kept up (no next action wanted): " + "; ".join(
            f"{_shorten(i.title, 40)} [{handle(i)}]" for i in held[:4])
            + (f"; and {len(held) - 4} more" if len(held) > 4 else ""))
    out.append(f"- {things(len(loose))} dropped in but not filed — ./os sort" if loose
               else "- Nothing waiting to be filed.")
    to_merge, where = _left_to_merge(os_)
    if to_merge:
        out.append(f"- An update left {to_merge} newer file{'' if to_merge == 1 else 's'} in {where}, "
                   "each to be merged into their file of the same name; the folder can go once that's done.")
    if errors:
        out.append(f"- {things(len(errors))} broken — ./os check --fix")
    about = _about_them(os_, items)
    if about:
        out += [""] + about
    out += ["",
            'To act for them: ./os save "<text>" writes something down and files it · '
            "./os find <words> searches everything · ./os open <name> · ./os undo reverses "
            "the last thing ./os did, never an edit made by hand.",
            "", PLAIN_SPEECH]
    return "\n".join(out)


def cmd_learn(os_: Zenith, argv: list[str]) -> int:
    """Fetch and cache what a source actually says, so an AI can learn from it.

    Deliberately does no thinking. It lists, it fetches, it cleans, it caches —
    which sources are worth reading and what they add up to is the AI's job,
    and the reason this is a command rather than a skill is so every AI can
    reach it, not just the one with the skill file.
    """
    # Don't leave a __pycache__ behind: `./os check` reports it as junk, and a
    # command that dirties the folder every time it runs is worse than a slow one.
    sys.path.insert(0, str(os_.root / ".os"))
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        import learn as L
    finally:
        sys.dont_write_bytecode = was

    as_json = _flag(argv, "--json")
    want_list = _flag(argv, "--list", "-l")
    show_cache = _flag(argv, "--cached")
    force = _flag(argv, "--force")
    limit = _count(argv, "--limit", L.LIST_LIMIT)
    forget = _flag(argv, "--forget")
    rest = _theirs(argv)      # a YouTube id can begin with a dash

    def no_ytdlp() -> int:
        line = L.install_hint()
        if as_json:
            print(json.dumps({"ok": False, "why": "yt-dlp not installed",
                              "install": line}))
            return 1
        Out.title("learn", "needs one thing first")
        Out.warn("This is the one part of Zenith that goes out to the internet,")
        Out.raw("    and the one part that wants something installed.")
        Out.raw()
        Out.raw("    " + paint(line, S.B))
        Out.raw()
        Out.note("everything else in this folder works without it")
        return 1

    if show_cache:
        rows = L.inventory(os_.root)
        if as_json:
            print(json.dumps({"ok": True, "cached": rows}))
            return 0
        Out.title("cached", f"{len(rows)} source(s)")
        for r in rows:
            Out.item("·", f"{r['id']}  {r['words']:,} words")
        return 0

    if forget:
        if not rest:
            die("which ones?   ./os learn --cached   lists what is kept"
                "\n     ./os learn --forget dQw4w9WgXcQ")
        gone = L.forget(os_.root, rest)
        if as_json:
            print(json.dumps({"ok": True, "forgotten": gone}))
            return 0
        Out.ok(f"forgot {gone} cached source(s)")
        return 0

    if not rest:
        die('what should I learn from?   ./os learn --list "<channel url>"')

    if not L.ytdlp():
        return no_ytdlp()

    try:
        if want_list:
            rows = L.listing(rest[0], limit)
            if as_json:
                print(json.dumps({"ok": True, "videos": rows}))
                return 0
            Out.title("what's there", f"{len(rows)} video(s)")
            for r in rows:
                views = f"{r['views']:,}" if r["views"] else "—"
                mins = f"{r['minutes']}m" if r["minutes"] else "—"
                Out.item("·", f"{r['id']}  {views:>12} views  {mins:>5}  {r['title'][:60]}")
            Out.raw()
            Out.note("pick the ones worth reading, then:  ./os learn <id> <id> …")
            return 0

        results = L.transcripts(os_.root, rest, force)
    except RuntimeError as exc:
        if "no-ytdlp" in str(exc):
            return no_ytdlp()
        die(str(exc))
    except subprocess.TimeoutExpired:
        die("that took too long — try one video at a time")

    # Nothing fetched is a failure, and has to be one to whatever is reading:
    # this said `ok: true` over a list in which every single entry had failed.
    got = [r for r in results if r.get("ok")]
    if as_json:
        print(json.dumps({"ok": bool(got), "sources": results}))
        return 0 if got else 1
    Out.title("learned from", f"{len(got)} of {len(results)}")
    for r in results:
        if r.get("ok"):
            Out.ok(f"{r['id']}  {r['words']:,} words"
                   + ("  (already had it)" if r.get("cached") else ""))
            if r.get("note"):
                Out.note(r["note"])
        else:
            Out.warn(f"{r.get('id', r.get('url', '?'))} — {r.get('why')}")
    if got:
        Out.raw()
        Out.note("read them from " + os_.rel(L.cache_dir(os_.root)))
    return 0 if got else 1


def cmd_snag(os_: Zenith, argv: list[str]) -> int:
    """Write down something wrong with *this folder*, as opposed to their work.

    The person using a template is the only one who finds out what is wrong
    with it, and they find out mid-sentence, while doing something else —
    which is exactly when nobody stops to file a bug report. So the AI writes
    it down as it happens, in one command, and says nothing. Later, `--export`
    turns the pile into a page that can be handed to whoever maintains the
    template, with the repeats counted: the same snag hit six times is a
    different priority from one hit once, and that count is the only piece of
    evidence a maintainer cannot get any other way.

    Deliberately not a note. Their `Notes/` is theirs; this is about the
    machinery, it lives in `.os/`, and it never shows up in a search for
    their own work."""
    as_json = _flag(argv, "--json")
    clear = _flag(argv, "--clear")
    # Clearing always writes them out first. Rule 2 of this folder is that it
    # does not delete what somebody wrote, and a snag is something they wrote.
    export = _flag(argv, "--export") or clear
    text = " ".join(_theirs(argv)).strip().strip('"')

    store = os_.dot / "snags.json"
    try:
        snags = json.loads(store.read_text(encoding="utf-8"))
        if not isinstance(snags, list):
            snags = []
    except (OSError, ValueError):
        snags = []

    def key(t: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", t.lower())).strip()

    # The engine's number stays the same from one release to the next; the
    # dated release doesn't, and it says whether a snag came before a fix.
    release = release_of(os_.root)
    if text:
        threshold = float(os_.thresholds.get("duplicate_similarity", 0.86))
        mine = key(text)
        for snag in snags:
            if difflib.SequenceMatcher(None, mine, key(snag["text"])).ratio() >= threshold:
                snag["times"] = int(snag.get("times", 1)) + 1
                snag["last"] = today()
                if release:
                    snag["release"] = release     # the newest one it still happens on
                break
        else:
            snags.append({"text": re.sub(r"\s+", " ", text)[:400], "times": 1,
                          "first": today(), "last": today(),
                          "version": ENGINE_VERSION, "release": release})
        write_text(store, json.dumps(snags, indent=2, ensure_ascii=False) + "\n")
        if as_json:
            print(json.dumps({"ok": True, "snags": len(snags)}))
            return 0
        # Quiet on purpose: this is bookkeeping about the tool, and the person
        # was in the middle of something else when it happened.
        Out.note(f"noted about this folder — {len(snags)} so far, ./os snag to read them")
        return 0

    ranked = sorted(snags, key=lambda x: (-int(x.get("times", 1)), x.get("first", "")))

    def gist(text: str) -> set:
        """The words that carry the complaint, roughly stemmed.

        `exits` and `exit`, `matches` and `match` — two people describing one
        problem rarely pick the same tense."""
        words = re.findall(r"[a-z0-9]+", text.lower())
        out = set()
        for w in words:
            if w in STOPWORDS_LITE:
                continue      # a bare "1" is the whole point of "exits 1"
            for end in ("ing", "es", "ed", "s"):
                if len(w) > 4 and w.endswith(end):
                    w = w[: -len(end)]
                    break
            out.add(w)
        return out

    def clustered(items: list) -> list:
        """Same complaint, different wording, side by side.

        Merging these outright would be guessing, and guessing here throws away
        somebody's report. Putting them next to each other costs nothing and
        does the same job for whoever reads the page."""
        groups: list = []
        for snag in items:
            mine = gist(snag["text"])
            for group in groups:
                shared = mine & group["gist"]
                if shared and len(shared) / max(1, len(mine | group["gist"])) >= 0.32:
                    group["with"].append(snag)
                    group["gist"] |= mine
                    group["times"] += int(snag.get("times", 1))
                    break
            else:
                groups.append({"head": snag, "with": [], "gist": mine,
                               "times": int(snag.get("times", 1))})
        return sorted(groups, key=lambda g: (-g["times"], g["head"].get("first", "")))

    if export:
        made = f"release {release} (engine {ENGINE_VERSION})" if release else f"engine {ENGINE_VERSION}"
        lines = [f"# What using {os_.config.get('name', 'Zenith')} turned up",
                 "",
                 f"{len(ranked)} thing{'' if len(ranked) == 1 else 's'}, "
                 f"most-repeated first, "
                 f"from {made}. Written by `./os snag --export` "
                 f"on {today()}.", ""]
        if not ranked:
            lines.append("Nothing yet. Either it is working, or nobody is writing it down.")
        for group in clustered(ranked):
            snag = group["head"]
            times = group["times"]
            when = (f"{snag.get('first')}" if times == 1
                    else f"{times}x, {snag.get('first')} → {snag.get('last')}")
            on = (f"release {snag['release']}" if snag.get("release")
                  else f"engine {snag.get('version', '?')}")
            lines += [f"## {snag['text']}", "", f"_{when} · {on}_", ""]
            if group["with"]:
                lines.append("Also written as:")
                lines += [f"- {other['text']}" for other in group["with"]]
                lines.append("")
        out = os_.root / "template-feedback.md"
        write_text(out, "\n".join(lines).rstrip() + "\n")
        if clear:
            write_text(store, "[]\n")     # written out, so safe to put away
        if as_json:
            print(json.dumps({"ok": True, "wrote": os_.rel(out),
                              "snags": len(ranked), "cleared": clear}))
            return 0
        Out.title("snags", f"{len(ranked)} written out")
        Out.ok(os_.rel(out))
        if clear:
            Out.note(f"{len(ranked)} cleared — the file above is the record now")
        Out.raw()
        Out.note("hand that file to whoever maintains this template")
        Out.raw()
        return 0

    if as_json:
        print(json.dumps({"ok": True, "snags": ranked}, indent=2))
        return 0
    if not ranked:
        Out.title("snags", "nothing yet")
        Out.note('./os snag "<what got in the way>"   writes one down')
        Out.raw()
        return 0
    Out.title("snags", f"{len(ranked)} about this folder")
    for snag in ranked:
        times = int(snag.get("times", 1))
        Out.item("·", trunc(snag["text"], 62)
                 + paint(f"   {times}x" if times > 1 else "", S.AMBER))
    Out.raw()
    Out.note("./os snag --export   writes them out as a page to hand over")
    Out.raw()
    return 0


def _words_or_die(call, *args):
    """Read or write the vocabulary, and say so plainly when it cannot be read.

    `.os/words.json` is the one file people are invited to open, and learn.py
    reads it on its own rather than through the engine so that it stays usable
    without one. That meant it had its own way of failing — an AttributeError
    about a `str` — for a file somebody had just been told to edit."""
    try:
        return call(*args)
    except json.JSONDecodeError as exc:
        die(f".os/words.json has a typo in it and can't be read.\n     {exc}\n"
            "     Usually a missing comma or an unclosed quote.")
    except ValueError as exc:
        die(f"{exc}\n     Fix that, or restore the file from a fresh copy of "
            "this folder.")
    except OSError as exc:
        die(f"cannot read .os/words.json: {exc}")


def cmd_words(os_: Zenith, argv: list[str]) -> int:
    """The vocabulary this folder files by, and how to add to it.

    Filing is word matching, so the words somebody actually uses are the single
    biggest lever on where their things land. `.os/words.json` is the one file
    they are invited to edit; this is the same thing without opening an editor,
    and it is how `/learn` hands back what a subject taught it. A command
    rather than a skill for the same reason as `learn`: every AI can reach it,
    not only the one holding the skill file — and `python3 .os/learn.py` is not
    something anybody should have to type."""
    sys.path.insert(0, str(os_.root / ".os"))
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        import learn as L
    finally:
        sys.dont_write_bytecode = was

    as_json = _flag(argv, "--json")
    rest = _theirs(argv)

    if not rest:
        rows = _words_or_die(L.domains, os_.root)
        if as_json:
            print(json.dumps({"ok": True, "domains": rows}, indent=2))
            return 0
        Out.title("words", "what this folder files by")
        for row in rows:
            extra = f"+{row['learned']} learned" if row["learned"] else ""
            Out.raw("  " + paint(pad(row["domain"], 14), S.GOLD)
                    + paint(pad(f"{row['keywords']} words", 12), S.INK)
                    + paint(extra, S.JADE))
        Out.raw()
        Out.note('./os words <domain> "<a word you use>" …   teaches it more')
        Out.note("or open .os/words.json and add them to a keywords list yourself")
        Out.raw()
        return 0

    if len(rest) == 1:
        die('give me a domain and at least one word'
            '\n     ./os words marketing "ad set" "learning phase"'
            '\n     ./os words          lists the domains')

    result = _words_or_die(L.teach, os_.root, rest[0], rest[1:])
    if as_json:
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if not result["ok"]:
        die(result["why"] + "\n     it knows: " + ", ".join(result["domains"]))

    Out.title("words", result["domain"])
    if result["added"]:
        Out.ok(f"{len(result['added'])} added — " + ", ".join(result["added"]))
    else:
        Out.note("nothing new — it knew all of those already")
    if result["already_known"] and result["added"]:
        Out.note(f"{len(result['already_known'])} it already knew")
    Out.raw()
    if result["added"]:
        Out.note("things you save about this will file themselves from now on")
        Out.raw()
    return 0


def cmd_brief(os_: Zenith, argv: list[str]) -> int:
    """What an AI is handed before the person has said anything."""
    text = _brief_text(os_)
    if _flag(argv, "--json"):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": text}}))
        return 0
    Out.raw()
    for line in text.split("\n"):
        Out.raw("  " + paint(line, S.MUTE if line.startswith("- ") else S.INK))
    Out.raw()
    return 0


def cmd_test(os_: Zenith, argv: list[str]) -> int:
    """Run the suite against a throwaway copy. Never touches this folder."""
    runner = os_.dot / "tests" / "run.py"
    if not runner.exists():
        die("the test suite is not installed (.os/tests/run.py is missing)")
    proc = subprocess.run([sys.executable, str(runner), *argv], cwd=str(os_.root))
    return proc.returncode


#: Where `./os update` gets the newest version: the `os` branch of the public
#: template, as a ZIP. `./os update --from <folder or .zip>` uses another copy.
UPDATE_URL = "https://github.com/zidery333/os-template/archive/refs/heads/os.zip"
#: What `./os update --check` reads: just the newest version's list, which
#: carries its release stamp. One small request, and nothing about the person.
CHECK_URL = "https://raw.githubusercontent.com/zidery333/os-template/os/.os/shipped.json"

#: The program itself. Nobody edits these, so a copy that matches no released
#: version was changed here and never published — an update would lose that.
UPDATE_PROGRAM = ("os", ".os/engine.py", ".os/learn.py", ".os/upgrade.py")


#: Where AGENTS.md says the folder's name, as `.os/upgrade.py` reads it: up to
#: the sentence after it, so "St. Ives" is one name, not "St.".
FOLDER_CALLED = re.compile(r"This folder is called [^\n]+?\. (?=It holds )|"
                           r"This folder is called [^\n]+?\. ")


def _sha1(path: Path) -> str:
    """A file's fingerprint. AGENTS.md's leaves out the folder's own name, the
    way `.os/upgrade.py` records it, so a renamed folder is not a new version."""
    if not path.is_file():
        return ""
    if path.name != "AGENTS.md":
        return hashlib.sha1(path.read_bytes()).hexdigest()
    text = FOLDER_CALLED.sub(lambda _: "This folder is called Zenith. ",
                             path.read_text(encoding="utf-8", errors="replace"), count=1)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _shipped(root: Path) -> dict:
    """A folder's `.os/shipped.json`: every released version of each shipped file."""
    try:
        data = json.loads((root / ".os" / "shipped.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def release_of(root: Path) -> str:
    """The dated release a folder is on, like `2026-09-30.1`. Empty before its first."""
    return str(_shipped(root).get("release") or "")


def _left_to_merge(os_: Zenith) -> tuple:
    """Newer versions an update set aside beside files they changed, and
    where: (how many, the folder). A template file that only shares a name
    with one of theirs is not theirs to merge, so it isn't counted."""
    base = os_.dot / "upgrades"
    if not base.is_dir():
        return 0, ""
    theirs = set(_shipped(os_.root).get("theirs") or [])
    found: dict = {}
    for stamp in sorted(p for p in base.iterdir() if p.is_dir()):
        n = sum(1 for f in stamp.rglob("*") if f.is_file() and f.name != ".DS_Store"
                and f.relative_to(stamp).as_posix() not in theirs)
        if n:
            found[stamp] = n
    if not found:
        return 0, ""
    where = os_.rel(next(iter(found))) + "/" if len(found) == 1 else os_.rel(base) + "/"
    return sum(found.values()), where


def _release_key(stamp: str) -> tuple:
    """`2026-09-26.2` → comparable. A folder from before stamps sorts first."""
    return tuple(int(n) for n in re.findall(r"\d+", stamp or "")) or (0,)


def _newest_release(source: str) -> str:
    """The published release stamp, or "" when it can't be found out: no
    network, no curl certificates, nothing published. Five seconds at most."""
    try:
        if source:
            return str(_shipped(Path(source).expanduser()).get("release", ""))
        curl = shutil.which("curl")
        if curl:
            got = subprocess.run([curl, "-fsSL", "--max-time", "5", CHECK_URL],
                                 capture_output=True, text=True, timeout=10)
            body = got.stdout if got.returncode == 0 else ""
        else:
            import urllib.request
            with urllib.request.urlopen(CHECK_URL, timeout=5) as r:
                body = r.read(1_000_000).decode("utf-8", "replace")
        data = json.loads(body)
        return str(data.get("release", "")) if isinstance(data, dict) else ""
    except Exception:
        return ""


def _fetch_update(into: Path, source: str) -> Path:
    """The newest version, unpacked. Returns the folder that holds its `.os/`."""
    if source:
        src = Path(source).expanduser()
        if src.is_dir():
            return src.resolve()
        if not src.is_file():
            die(f"there is nothing at {source}")
        archive = src
    else:
        archive = into / "update.zip"
        curl = shutil.which("curl")
        got = False
        if curl:
            got = subprocess.run([curl, "-fsSL", "--max-time", "120", "-o", str(archive), UPDATE_URL],
                                 capture_output=True).returncode == 0
        if not got:
            # No curl, or curl failed: Python's own. On some Macs it lacks the
            # certificates, which is why curl goes first.
            import urllib.error
            import urllib.request
            try:
                with urllib.request.urlopen(UPDATE_URL, timeout=120) as r, open(archive, "wb") as f:
                    shutil.copyfileobj(r, f)
            except urllib.error.HTTPError as exc:
                die(f"GitHub has no published version to download ({exc.code}) — nothing here changed")
            except Exception as exc:  # no network, a proxy, a certificate
                die(f"couldn't download the newest version: {exc}\n"
                    "     check the internet connection and try again — nothing here changed")
    unpacked = into / "new"
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(unpacked)
    except (zipfile.BadZipFile, OSError) as exc:
        die(f"the download wasn't a readable ZIP ({exc}) — nothing here changed")
    if (unpacked / ".os").is_dir():
        return unpacked
    tops = [p for p in unpacked.iterdir() if p.is_dir() and (p / ".os").is_dir()]
    if len(tops) != 1:
        die("the download doesn't hold a copy of this program — nothing here changed")
    return tops[0]


def cmd_update(os_: Zenith, argv: list[str]) -> int:
    """Bring the program, the shipped skills and helpers up to the newest
    published version. Their work, notes and own edits stay theirs."""
    preview = _flag(argv, "--dry-run", "-n")
    anyway = _flag(argv, "--anyway")
    source = _opt(argv, "--from")
    check = _flag(argv, "--check")
    # `./os update ~/Downloads/os-template` means that copy. Dropped, it went to
    # GitHub instead: a 404 before the first release, another version after.
    typed = _theirs(argv)
    # Typed without quotes, `~/My Downloads/os-template` arrives in pieces.
    if typed and not source and Path(" ".join(typed)).expanduser().exists():
        source = " ".join(typed)
    elif typed:
        die(f"there is no copy to update from at {' '.join(typed)}\n"
            "     ./os update                          the newest published version\n"
            "     ./os update --from <folder or .zip>  a copy you already have")
    if check:
        # Only says whether there is one. Silent when there isn't, or when it
        # can't tell: this runs at the start of a session, not on request.
        got = _newest_release(source)
        if got and _release_key(got) > _release_key(_shipped(os_.root).get("release", "")):
            Out.raw(f"  A newer version is out ({got}) — ./os update brings it in")
        return 0
    with tempfile.TemporaryDirectory(prefix="zenith-update-") as tmp:
        new = _fetch_update(Path(tmp), source)
        if new == os_.root.resolve():
            die("that is this folder — point --from at another copy")
        if not (new / ".os" / "upgrade.py").is_file() or not (new / ".os" / "engine.py").is_file():
            die("that isn't a copy of this program (it has no .os/upgrade.py)")
        mine, theirs = _shipped(os_.root), _shipped(new)
        # Only a fresh download is a release. A folder somebody has used holds
        # their own edits, words and hooks, which would come in here as if
        # they were the template's.
        listed = theirs.get("files") if isinstance(theirs.get("files"), dict) else {}
        used = [rel for rel, hashes in sorted(listed.items())
                if (new / rel).is_file() and _sha1(new / rel) not in hashes]
        lived = [b for b in ("Work", "Notes", "Archive") if (new / b).exists()]
        if used or lived:
            die("that's someone's folder, not a download of the template — "
                + (f"{', '.join(used[:3])} changed there" if used else f"it has a {lived[0]}/ folder")
                + ".\n     Use a fresh download:  ./os update")
        have, got = mine.get("release", ""), theirs.get("release", "")

        # What is new: a shipped file whose published version this folder has
        # never been given. Its own list holds every version it was given.
        given = mine.get("files", {})
        fresh = []
        for rel in sorted(theirs.get("files", {})):
            digest = _sha1(new / rel)
            if digest and digest != _sha1(os_.root / rel) and digest not in given.get(rel, []):
                fresh.append(rel)
        for rel in (".os/templates", ".os/tests"):
            a, b = new / rel, os_.root / rel
            if a.is_dir() and any(_sha1(f) != _sha1(b / f.relative_to(a))
                                  for f in a.rglob("*") if f.is_file() and f.suffix != ".pyc"):
                fresh.append(rel)
        for rel in UPDATE_PROGRAM:
            if _sha1(new / rel) != _sha1(os_.root / rel) and rel not in fresh:
                fresh.append(rel)

        Out.title("update", f"{have or 'unstamped'} → {got or 'unstamped'}")
        # A release can change only .os/words.json, .os/config.json or
        # .claude/settings.json. None is compared above; the upgrade merges them.
        newer = bool(have and got and _release_key(got) > _release_key(have))
        if not fresh and not newer:
            Out.ok("this folder already has the newest version")
            Out.raw()
            return 0
        if have and got and _release_key(got) < _release_key(have) and not anyway:
            die(f"this folder has {have}, newer than the published {got}.\n"
                "     ./os update --anyway goes back to the published one")

        if fresh:
            Out.raw("  " + paint("newer: ", S.MUTE) + ", ".join(fresh[:8])
                    + (f" and {len(fresh) - 8} more" if len(fresh) > 8 else ""))
        # The upgrade itself refuses to replace a program changed here and
        # never published, unless told --anyway: run by hand, it says so too.
        sys.stdout.flush()
        proc = subprocess.run([sys.executable, str(new / ".os" / "upgrade.py"), str(os_.root),
                               *(["--dry-run"] if preview else []), *(["--anyway"] if anyway else []),
                               "--again=./os update"],
                              cwd=str(os_.root), text=True)
        if proc.returncode == 2:
            return 1            # it stopped before changing anything, and said why
        if proc.returncode != 0:
            Out.bad("the update stopped partway — ./os update again finishes it. "
                    "What it replaced is in .os/backups/before-upgrade-*")
            return proc.returncode
    if not preview:
        Out.note("./os undo can't reverse an update — the backup above can")
        Out.raw()
    return 0


def cmd_setup(os_: Zenith, argv: list[str]) -> int:
    """Make a freshly-copied folder belong to whoever just opened it."""
    quiet = _flag(argv, "--quiet-welcome")
    label_opt = _opt(argv, "--name")
    owner = _opt(argv, "--owner") or " ".join(_theirs(argv)).strip()
    with Lock(os_, "setup"):
        result = os_.initialise(owner=owner.strip('"'), name=label_opt)
        Doctor(os_).run(fix=True)
        Indexer(os_).build()
    if quiet:
        return 0
    label = result["name"] or "Zenith"
    Out.raw(paint(f"\n  {' '.join(label.upper())}", S.B, S.GOLD))
    Out.raw("  " + paint("─" * max(10, len(label) * 2 - 1), S.FAINT))
    Out.raw()
    who = f"{result['owner']}'s folder." if result["owner"] else "This folder is yours now."
    Out.raw("  " + paint(who + " Everything you make lives here.", S.INK))
    Out.raw()
    Out.raw("  " + paint("TRY THIS", S.B, S.GOLD))
    for cmd, why in (
        ('./os save "anything on your mind"', "I put it somewhere sensible"),
        ("./os", "see where everything stands"),
        ("claude", "or any AI — then just talk to it normally"),
    ):
        Out.raw("    " + paint(pad(cmd, 36), S.GOLD) + paint(why, S.FAINT))
    Out.raw()
    Out.raw("  " + paint("./os demo", S.MUTE)
            + paint("   two minutes, shows you the whole idea", S.FAINT))
    Out.raw()
    return 0


DEMO_ITEMS = [
    ("The billing service token refresh fails every Friday night. Has to be fixed "
     "before the release on the 14th. First step is reproducing it on staging.",
     "this one has a next action"),
    ("Notes on how names work here: a thing is called what it is, so the note "
     "on running a retro is the file called how-to-run-a-retro. Nothing to do "
     "— just worth keeping.",
     "this one is just worth keeping"),
    ("Keep the codebase green: no failing tests, no lint errors, checked every "
     "week. This never ends, it is just something I hold to.",
     "this one is just kept level"),
]


def cmd_demo(os_: Zenith, argv: list[str]) -> int:
    """Show the whole idea on three throwaway items, then put the folder back."""
    keep = _flag(argv, "--keep")
    loose = [i for i in Scanner(os_).scan() if Sorter.unmanaged(i)] + loose_at_top(os_.root)
    if loose and not keep:
        die(f"you have {len(loose)} thing(s) dropped in but not filed, and the demo "
            "would sweep them up with its own.\n"
            "     Run  ./os sort  to file them first, then try the demo again.")

    def step(n, title):
        Out.raw()
        Out.raw("  " + paint(str(n), S.B, S.GOLD) + paint("  " + title, S.B, S.INK))

    Out.raw()
    Out.raw("  " + paint("TWO MINUTES", S.B, S.GOLD))
    Out.raw("  " + paint("Three things go in. You decide nothing. They all end up "
                         "somewhere sensible.", S.FAINT))
    if not keep:
        Out.raw("  " + paint("Everything the demo makes is removed again at the end.", S.FAINT))

    with Lock(os_, "demo"):
        creator = Creator(os_)
        step(1, "Write three things down. No folder, no title, no tags.")
        for text, why in DEMO_ITEMS:
            creator.capture(text, source="demo")
            Out.raw("    " + paint("→ ", S.GOLD) + paint(trunc(text, 62), S.INK))
            Out.raw("      " + paint(why, S.FAINT))

        step(2, "Watch where they go.")
        result = Sorter(os_).run()
        os_.commit("demo")
        Indexer(os_).build()
        # Work is one kind in two phases, so the label has to read the phase
        # off the filed item — that distinction is the whole point of step 2.
        phase_of = {os_.rel(i.path): i.status for i in Scanner(os_).scan()}
        for kind, _src, dst in result["moves"]:
            word = KIND_WORDS.get(kind, kind)
            if kind == "project":
                word = ("work you're pushing" if phase_of.get(dst) != HOLDING
                        else "work you keep up")
            Out.raw("    " + paint(pad(word, 20), S.GOLD)
                    + paint("→ ", S.FAINT) + paint(trunc(dst, 50), S.INK))

        step(3, "Find one again, without remembering where it went.")
        Out.raw("    " + paint('./os find "token refresh"', S.FAINT))
        for _score, item, _sn in Finder(os_).search("token refresh", limit=1):
            Out.raw("    " + paint(item.title[:56], S.B))

        step(4, "Change your mind about all of it, at once.")
        if keep:
            Out.raw("    " + paint("skipped — you passed --keep, so the three stay filed", S.AMBER))
        else:
            Out.raw("    " + paint("./os undo", S.FAINT))
            outcome = Undo(os_).revert()
            stage = Creator(os_).stage()
            for leftover in list(stage.iterdir()):
                if not ignored(leftover) and "source: demo" in read_text(leftover, 400):
                    leftover.unlink()
            Indexer(os_).build()
            Out.raw("    " + paint(f"✓ all {outcome['restored']} changes reversed — the "
                                   "three demo items are gone", S.JADE))
            Out.raw("    " + paint("(nothing of yours was touched — the demo only "
                                   "ever handles its own three)", S.FAINT))

    Out.raw()
    Out.raw("  " + paint("THAT IS THE WHOLE THING", S.B, S.GOLD))
    Out.raw("    " + paint("Say it. It gets filed. You find it later. You can always undo.", S.INK))
    Out.raw()
    Out.raw("    " + paint(pad('./os save "..."', 24), S.GOLD)
            + paint("put something real in", S.FAINT))
    Out.raw("    " + paint(pad("claude", 24), S.GOLD)
            + paint("or any AI — just talk to it", S.FAINT))
    Out.raw()
    return 0


def cmd_edit(os_: Zenith, argv: list[str]) -> int:
    """Open an item in $EDITOR, or in whatever the desktop uses."""
    if not argv or not argv[0].strip():
        die("which one?   ./os edit q3-okr-review      (run ./os to see the names)")
    item = Finder(os_).by_id(argv[0])
    if item is None:
        die(f"nothing here is called {argv[0]} — try  ./os find {argv[0]}")
    warn_if_claimed(os_, item)
    target = item.spine or item.path
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        return subprocess.run([*editor.split(), str(target)]).returncode
    if sys.platform == "darwin":
        subprocess.run(["open", str(target)], check=False)
        Out.title("edit")
        Out.ok(os_.rel(target))
        Out.note("set $EDITOR to open it in your terminal editor instead")
        Out.raw()
        return 0
    print(str(target))
    return 0


COMMAND_LIST = [
    ("save", "write something down — it gets filed for you"),
    ("find", "search everything you have ever saved"),
    ("show", "look at one thing without opening it"),
    ("last", "what happened the last time anyone worked here"),
    ("open", "show where something is on disk"),
    ("new", "start work, an ongoing thing, a note, a skill"),
    ("hold", "no next action — just keep it level"),
    ("push", "put it back on the go"),
    ("close", "no longer live — put it away in the archive"),
    ("back", "get it out of the archive again"),
    ("decide", "write a settled thing into its ## Decisions"),
    ("undo", "take back the last thing it did"),
    ("sort", "file anything you dropped in by hand"),
    ("check", "is anything broken?"),
    ("tidy", "what has gone stale, doubled up or unfiled"),
    ("backup", "zip a copy of everything"),
    ("edit", "open it in your text editor"),
    ("rename", "call it something else, everywhere at once"),
    ("claim", "tell the other chats you are working on it"),
    ("release", "let go of something you claimed"),
    ("demo", "two-minute tour, then puts everything back"),
    ("name", "put your name on this folder"),
    ("help", "everything you can type"),
]


def _completion(shell: str) -> str:
    names = " ".join(n for n, _ in COMMAND_LIST)
    if "bash" in shell:
        return """# Zenith tab-completion for bash.  Install with:
#   ./os completion bash >> ~/.bashrc
_os_complete() {
  local cur prev
  cur="${COMP_WORDS[COMP_CWORD]}"; prev="${COMP_WORDS[COMP_CWORD-1]}"
  if [ "$COMP_CWORD" -eq 1 ]; then
    COMPREPLY=( $(compgen -W "%s" -- "$cur") ); return
  fi
  case "$prev" in
    new)   COMPREPLY=( $(compgen -W "work ongoing note learning skill agent" -- "$cur") ) ;;
    sort)  COMPREPLY=( $(compgen -W "--dry-run --json" -- "$cur") ) ;;
    check) COMPREPLY=( $(compgen -W "--fix --json" -- "$cur") ) ;;
    help)  COMPREPLY=( $(compgen -W "%s" -- "$cur") ) ;;
    *)     COMPREPLY=( $(compgen -f -- "$cur") ) ;;
  esac
}
complete -F _os_complete os
""" % (names, names)
    listed = "\n".join(f"    '{n}:{d}'" for n, d in COMMAND_LIST)
    return """#compdef os
# Zenith tab-completion for zsh.  Install with:
#   ./os completion zsh > ~/.zsh/completions/_os     (then run: compinit)
_os() {
  local -a cmds
  cmds=(
%s
  )
  if (( CURRENT == 2 )); then _describe -t commands 'os' cmds; return; fi
  case ${words[2]} in
    new) _values 'kind' work ongoing note learning skill agent ;;
    open|edit|done|back|rename|claim|release|decide) _message 'a name like q3-okr-review' ;;
    sort) _values 'flag' --dry-run --json ;;
    check) _values 'flag' --fix --json ;;
    help) _describe -t commands 'os' cmds ;;
    *) _files ;;
  esac
}
_os "$@"
""" % listed


def cmd_completion(os_: Zenith, argv: list[str]) -> int:
    print(_completion((argv[0] if argv else os.environ.get("SHELL", "")).lower()))
    return 0


def cmd_name(os_: Zenith, argv: list[str]) -> int:
    """Put your name on the folder, or call the folder something else."""
    label = _opt(argv, "--name")
    owner = " ".join(_theirs(argv)).strip().strip('"')
    if not owner and not label:
        Out.title("name")
        Out.kv("this folder", os_.config.get("name", "Zenith"), 14)
        Out.kv("belongs to", os_.config.get("owner") or "nobody yet", 14)
        Out.raw()
        Out.note('./os name "Your Name"          put your name on it')
        Out.note('./os name --name "Studio"      call the folder something else')
        Out.raw()
        return 0
    if owner:
        os_.config["owner"] = owner
    if label:
        os_.set_name(label)
    os_.save_config()
    Indexer(os_).build()
    Out.title("name")
    Out.ok(f"{os_.config.get('name', 'Zenith')} · "
           f"{os_.config.get('owner') or 'no owner set'}")
    Out.raw()
    return 0


DETAIL = {
    "save": ('os save "<anything>"   |   os save <a file on your computer>',
             "Write something down. It works out what it is and puts it in the right "
             "folder straight away — you never pick one. Start with the name of "
             "work you already have, and the rest becomes its next action. Video, "
             "and anything bigger than 100 MB, goes to Work/Content, which ./os "
             "leaves alone.",
             ['os save "the auth token breaks every Friday"',
              "os save ~/Downloads/pricing-deck.pdf"],
             "Wrong place? ./os undo puts it back, every time."),
    "new": ('os new <work|ongoing|note|learning|skill|agent> "<name>"',
            "Start something from a blank template, already named and stamped. "
            "If you already have something by nearly the same name it stops and says so "
            "— add --anyway if you really want both.",
            ['os new work "Ship the redesign"',
             'os new ongoing "Keep the tests passing"',
             'os new note "How Postgres indexes work"',
             'os new learning "How sourdough is actually made"',
             'os new skill "Draft the weekly invoice"'],
            "A learning note is a note in a different shape: numbered steps, "
            "where the people who do it disagree, what goes wrong, and one "
            "thing to practise. It is what ./os learn is for — reach for it "
            "when you want to be able to *do* something, not just look it up. "
            "Work and ongoing are the same kind of thing in two phases: `work` "
            "starts it being pushed, `ongoing` starts it being held. Both live "
            "in Work/, and ./os hold and ./os push move one between them — "
            "which happens all the time, because work that ships becomes work "
            "you maintain. A skill is different: it is not work you are doing, "
            "it is a job you want *done the same way every time*, written down "
            "once so any AI opening this folder can just do it. If you catch "
            "yourself explaining the same steps twice, that's a skill. (A helper "
            "— ./os new helper — is for a big job that deserves its own clean "
            "context, like research or a review.) And ./os save already makes "
            "work when your words read like work, so you rarely need this one."),
    "claim": ('os claim <name> [--as "<what you\'re doing>"]   |   os release <name>',
              "Tell any other chat open on this folder that you are working on "
              "this one. It writes a line in the item's own header, so the next "
              "session sees it the moment it types ./os. Let go with os release.",
              ['os claim ship-the-redesign',
               'os claim ship-the-redesign --as "the copy pass"',
               "os release ship-the-redesign"],
              "It is a note, not a lock — nothing is ever refused because of a "
              "claim, it just says who was there first. A claim older than 12 "
              "hours is shown as stale and can be claimed straight over, "
              "because chats close without saying goodbye. Anyone can release "
              "anything: that is the way out when the chat that claimed "
              "something is long gone."),
    "update": ("os update   |   os update --dry-run   |   os update --check",
               "Get the newest version of this program, its skills and its helpers "
               "from GitHub. Your work and notes are never touched. A skill or helper "
               "you changed yourself is kept — the new one is put beside it in "
               ".os/upgrades/ for you or your AI to merge — and one you deleted stays "
               "deleted. Settings you changed keep your changes, with any new ones added.",
               ["os update", "os update --dry-run", "os update --check",
                "os update --from ~/Downloads/os.zip"],
               "Everything it replaces is copied into .os/backups/ first. If this "
               "folder's own program was changed and never published, it stops and "
               "says so; --anyway goes ahead. --check only says whether a newer "
               "version is out, and says nothing if not. --from uses a fresh download "
               "you already have, never a folder somebody has used."),
    "snag": ('os snag "<what got in the way>"   |   os snag --export',
             "Write down something wrong with this folder itself — a command that "
             "did the surprising thing, a rule that made no sense, a step that "
             "should have been automatic. Not your work: the machinery. Your AI "
             "writes these as it hits them, so you do not have to notice.",
             ['os snag "sort filed a photo as a project"',
              "os snag",
              "os snag --export"],
             "--export writes template-feedback.md at the top of this folder, "
             "most-repeated first, ready to hand to whoever maintains the "
             "template. The count matters: the same snag six times is a "
             "different job from one seen once. --clear writes that page first "
             "and then empties the pile, so nothing you wrote is ever simply "
             "gone. None of it is mixed in with your own notes, and none of it "
             "leaves this folder on its own."),
    "words": ('os words   |   os words <domain> "<a word you use>" …',
              "Show the vocabulary this folder files by, and add to it. Where "
              "something lands is decided by matching words, so the fastest way "
              "to make it better at your work is to give it the words you "
              "actually use — your clients, your projects, the jargon of your "
              "trade.",
              ["os words",
               'os words marketing "ad set" "learning phase"',
               'os words engineering "northwind" "the flimbus service"'],
              "It only ever adds to a `learned` list, so the keywords you wrote "
              "yourself in .os/words.json are never touched and anything added "
              "here can be deleted without disturbing them. /learn writes to it "
              "at the end of studying a subject, which is why filing gets better "
              "at a subject once you have learned one."),
    "hold": ("os hold <name>   |   os push <name>",
             "Say what a piece of work needs from you now. Holding means there "
             "is no next action, only a standard you keep level — it stops being "
             "counted as on the go, and stops being nagged for going quiet. "
             "Pushing puts it back on the go.",
             ["os hold q3-okr-review", "os push q3-okr-review"],
             "Nothing moves on disk; it is one word in the file's header. That is "
             "the point — the same job flips between the two over and over, and "
             "no filing system should make you shuffle folders for that. A note "
             "that turns out to be something to do is the exception: push or hold "
             "it and it becomes work, in Work/."),
    "find": ("os find <words>",
             "Search names, titles, tags and the full text of everything you "
             "have saved, archive included. Typos and plurals are fine — "
             "'meetings' finds 'meeting', and it will tell you when it searched "
             "for something other than what you typed. Skills and helpers are "
             "left out unless you ask for them with --kind skill.",
             ["os find token refresh", "os find billing --kind project",
              "os find weekly --kind skill"],
             "You don't have to remember where you put it, or spell it right. "
             "That is the whole point."),
    "show": ("os show <name>",
             "Everything worth knowing about one thing — what state it is in, when "
             "you last touched it, its next action, what was decided — without "
             "opening the file. The name on disk or the title said any reasonable "
             "way both find it.",
             ["os show q3-okr-review", 'os show "Q3 OKR review"'],
             "./os edit <name> opens it properly when you want to change something."),
    "open": ("os open <name>", "Print where something lives, and show it to you in "
             "Finder, Explorer or your file manager. To open the file itself for "
             "editing, use ./os edit.",
             ["os open q3-okr-review"],
             "The name is the handle: either the name on disk or the title said "
             "any reasonable way."),
    "edit": ("os edit <name>", "Open it in your text editor.",
             ["os edit q3-okr-review"], "Set $EDITOR to stay in the terminal."),
    "rename": ('os rename <name> "<new name>"',
               "Give something a new name on disk and in its header in one move, "
               "so the two never disagree. Folders come out Title Case With Spaces, "
               "notes stay kebab-case files, a file's card moves with it, and links "
               "to it from other notes follow.",
               ['os rename q3-okr-review "Q4 OKR review"'],
               "Renaming by hand leaves the title and the folder saying different "
               "things — this is the one that keeps them together. ./os undo reverses it."),
    "close": ("os close <name>   |   os back <name>",
              "Put something away in Archive/, or take it back out. Closed means "
              "no longer live — not necessarily finished. Things leave because you "
              "stopped carrying them, and that is as true of shipped work as of "
              "abandoned work.",
              ["os close q3-okr-review", "os back q3-okr-review"],
              "Nothing is deleted, and things in the archive still turn up in "
              "./os find. If it is not over, just quiet, ./os hold it instead."),
    "decide": ('os decide <name> "<what was settled>"',
               "Append one dated line to that item's ## Decisions. A decision is "
               "never a thing of its own — it is a line in the thing it is about, "
               "which is where it is still findable a year later.",
               ['os decide ship-the-redesign "three tiers, not four"'],
               "./os save does this by itself when the words name the item, and "
               "refuses when it cannot tell which one you meant."),
    "undo": ("os undo [--anyway]", "Reverse the last thing Zenith itself did — a save, a "
             "filing, a close, a new.",
             ["os undo", "os undo --anyway"],
             "It restores where files went AND what they said, twenty steps back. "
             "It cannot undo edits you made by hand in a text editor — for those, "
             "use your editor's own undo. It never throws them away either: when a "
             "file was written in since that step, undo stops and names it, and "
             "with --anyway copies it aside first and says where."),
    "sort": ("os sort [--dry-run]",
             "Take charge of anything you dropped in by hand. A folder you made "
             "keeps its name and its place, and nothing in it is rewritten. A "
             "loose file, or one left at the top of this folder, gets a plain name "
             "and a header and goes where it belongs. Also re-groups what is "
             "already filed as the folders fill up.",
             ["os sort --dry-run     # show me first, change nothing", "os sort"],
             "./os save files things the moment you say them, so this is for the "
             "times you dragged a pile of files in from Finder instead."),
    "check": ("os check [--fix]",
              "Look for anything broken: repeated names, half-written skills, "
              "dead links.",
              ["os check", "os check --fix"],
              "--fix only repairs the mechanical. Anything needing a judgement call "
              "is reported, never guessed."),
    "tidy": ("os tidy",
             "What has gone stale, what is finished but still sitting around, and "
             "what looks like the same thing twice.",
             ["os tidy"], "Ten minutes a week is all this system asks of you."),
    "backup": ("os backup", "A dated zip of everything, into .os/backups/.",
               ["os backup"],
               "Keeps the newest few — how many is keep_backups in .os/config.json. "
               "Move one somewhere else now and then; a copy on the same disk is "
               "not really a backup."),
    "name": ('os name "<your name>"', "Put your name on this folder.",
             ['os name "Sam"', 'os name --name "Studio"'], ""),
    "demo": ("os demo [--keep]", "A two-minute tour on three throwaway items.",
             ["os demo"], "It undoes itself at the end unless you pass --keep."),
    "status": ("os   |   os status", "Where everything stands right now.",
               ["os"], "This is the default — just type ./os on its own."),
    "index": ("os index", "Rebuild INDEX.md and the search data.",
              ["os index"], "Happens on its own after a Claude Code session."),
    "learn": ('os learn --list "<channel>"   |   os learn <video> …',
              "Fetch what a source actually says and keep it, so an AI can learn "
              "from the words rather than guessing from the title.",
              ['os learn --list "youtube.com/@channel"', "os learn dQw4w9WgXcQ"],
              "The one command that goes out to the internet, and the one that "
              "wants yt-dlp installed. It fetches and cleans; deciding which "
              "sources are worth anything is the AI's job."),
    "last": ("os last [--json]", "What happened the last time anyone worked here.",
             ["os last"],
             "Read from the items' own ## Log lines and what ./os filed that day, "
             "so any AI can pick up cold without a diary."),
    "brief": ("os brief", "What an AI is told about this folder before you speak.",
              ["os brief"],
              "Paste it into any AI that can't run the hook itself."),
    "test": ("os test [-v] [-k PATTERN]", "Run the test suite against a throwaway copy.",
             ["os test", "os test -k undo -v"],
             "It never touches the folder you run it from."),
    "completion": ("os completion [zsh|bash]", "Print a tab-completion script.",
                   ["os completion zsh > ~/.zsh/completions/_os"], ""),
}
for _alias, _real in (("back", "close"), ("restore", "close"), ("archive", "close"),
                      ("done", "close"), ("park", "close"),
                      ("push", "hold"), ("pushing", "hold"), ("holding", "hold"),
                      ("pause", "hold"), ("resume", "hold"),
                      ("release", "claim"), ("retitle", "rename"), ("call", "rename"),
                      ("capture", "save"), ("doctor", "check"), ("review", "tidy"),
                      ("setup", "name"), ("tour", "demo"),
                      ("decided", "decide")):
    DETAIL.setdefault(_alias, DETAIL[_real])


def cmd_help(os_: Zenith | None, argv: list[str]) -> int:
    topic = (argv[0].lstrip("/") if argv else "").lower()
    if topic in DETAIL:
        usage, what, examples, note = DETAIL[topic]
        Out.title(topic)
        Out.raw("  " + paint(usage, S.GOLD))
        Out.raw()
        Out.raw("  " + paint(what, S.INK))
        if examples:
            Out.raw()
            for line in examples:
                Out.raw("    " + paint("$ ", S.FAINT) + paint(line, S.MUTE))
        if note:
            Out.raw()
            Out.raw("  " + paint(note, S.FAINT))
        Out.raw()
        return 0
    if topic:
        Out.warn(f"there is nothing called '{topic}'")
        Out.note("./os help   lists everything you can type")
        Out.raw()
        return 1
    name = os_.config.get("name", "Zenith") if os_ else "Zenith"
    tagline = os_.config.get("tagline", "") if os_ else ""
    mark = wordmark(name)
    print(paint(mark, S.GOLD) if S.enabled else mark)
    print(HELP.format(name=paint(name, S.B), tagline=paint(tagline, S.FAINT),
                      c1=(S.B + S.GOLD) if S.enabled else "", c0=S.RESET if S.enabled else ""))
    return 0


COMMANDS = {
    # the ones people actually type
    "": cmd_status, "status": cmd_status, "st": cmd_status,
    "save": cmd_save, "s": cmd_save, "capture": cmd_save, "add": cmd_save,
    "find": cmd_find, "f": cmd_find, "search": cmd_find,
    "open": cmd_open, "o": cmd_open,
    "show": cmd_show, "view": cmd_show, "look": cmd_show,
    "new": cmd_new, "n": cmd_new, "start": cmd_new,
    "close": cmd_close, "done": cmd_close, "archive": cmd_close, "finish": cmd_close,
    "park": cmd_close,
    "hold": cmd_hold, "holding": cmd_hold, "pause": cmd_hold,
    "claim": cmd_claim, "release": cmd_release,
    "rename": cmd_rename, "retitle": cmd_rename, "call": cmd_rename,
    "push": cmd_push, "pushing": cmd_push, "resume": cmd_push,
    "back": cmd_back, "restore": cmd_back, "unarchive": cmd_back,
    "decide": cmd_decide, "decided": cmd_decide,
    "undo": cmd_undo, "oops": cmd_undo,
    # housekeeping
    "sort": cmd_sort, "file": cmd_sort,
    "check": cmd_doctor, "doctor": cmd_doctor, "fix": cmd_doctor,
    "tidy": cmd_review, "review": cmd_review, "cleanup": cmd_review,
    "backup": cmd_backup, "snapshot": cmd_backup,
    "edit": cmd_edit, "e": cmd_edit,
    "demo": cmd_demo, "tour": cmd_demo,
    "name": cmd_name, "setup": cmd_setup, "init": cmd_setup,
    "update": cmd_update,
    # plumbing
    "index": cmd_index, "reindex": cmd_index,
    "last": cmd_last,
    "brief": cmd_brief,
    "learn": cmd_learn,
    "words": cmd_words,
    "snag": cmd_snag,
    "test": cmd_test, "selftest": cmd_test,
    "completion": cmd_completion,
    "help": cmd_help, "-h": cmd_help, "--help": cmd_help,
}

#: A near miss should teach, not scold.
NEAR_MISS = {
    "list": "find", "look": "find", "ls": "status", "show": "status",
    "remember": "save", "write": "save", "note": 'new note "..."',
    "delete": "close", "remove": "close", "rm": "close", "trash": "close",
    "done": "close", "finish": "close", "complete": "close",
    "maintain": "hold", "keep": "hold", "park": "close", "unpause": "push",
    "clean": "tidy", "organize": "sort", "organise": "sort", "health": "check",
    "lock": "claim", "mine": "claim", "unlock": "release", "free": "release",
    "stats": "status", "dash": "status", "dashboard": "status",
    "guide": "help", "manual": "help", "link": "check --fix", "repair": "check --fix",
    "vocab": "words", "vocabulary": "words", "keywords": "words", "taxonomy": "words",
    "upgrade": "update", "latest": "update", "refresh": "update",
    "bug": "snag", "issue": "snag", "complain": "snag", "annoying": "snag",
}


#: Every option each command understands, by the function that handles it.
#: Anything else stops the run rather than being quietly dropped: `os sort
#: --dry` was somebody asking for a preview and getting the real thing, which
#: is the one mistake a dry-run flag exists to prevent. `None` means the
#: command hands its arguments to something else and cannot vet them. The
#: global options (--root, --quiet, --no-color, --version) are taken in main()
#: before any command sees them. `test_no_option_is_silently_ignored` reads the
#: source and fails if this table drifts out of step with it.
FLAGS: dict[str, set[str] | None] = {
    "cmd_back": set(),
    "cmd_backup": set(),
    "cmd_brief": {"--json"},
    "cmd_last": {"--json"},
    "cmd_claim": {"--as"},
    "cmd_close": set(),
    "cmd_completion": set(),
    "cmd_decide": set(),
    "cmd_demo": {"--keep"},
    "cmd_doctor": {"--fix", "--json"},
    "cmd_edit": set(),
    "cmd_find": {"--in", "--json", "--kind", "--limit"},
    "cmd_help": set(),
    "cmd_hold": set(),
    "cmd_index": {"--json", "--notify"},
    "cmd_learn": {"--cached", "--force", "--forget", "--json", "--limit", "--list", "-l"},
    "cmd_name": {"--name"},
    "cmd_new": {"--anyway", "--domain", "--force", "--tags"},
    "cmd_open": set(),
    "cmd_push": set(),
    "cmd_release": set(),
    "cmd_rename": set(),
    "cmd_review": {"--json"},
    "cmd_save": {"--file", "--json"},
    "cmd_setup": {"--name", "--owner", "--quiet-welcome"},
    "cmd_show": set(),
    "cmd_sort": {"--dry-run", "--json", "-n"},
    "cmd_status": {"--json"},
    "cmd_test": None,      # forwards everything to the suite
    "cmd_undo": {"--anyway"},
    "cmd_update": {"--anyway", "--check", "--dry-run", "--from", "-n"},
    "cmd_words": {"--json"},
    "cmd_snag": {"--clear", "--export", "--json"},
}

#: Commands whose arguments are the person's own words. Told about an option it
#: does not know, one of these also says how to keep words beginning with a dash.
FREE_TEXT = {"cmd_save", "cmd_new", "cmd_find", "cmd_name"}


def _stray_options(argv: list[str], known: set[str]) -> list[str]:
    """Options a command was handed and does not understand.

    Deliberately narrow, because the arguments to `os save` are whatever
    somebody typed. A double dash is an option — nobody writes a thought
    starting `--`. A single dash only counts when it is one bare letter:
    `-3 degrees and no heating` is the weather, and `-Xf12o4jt4` names a
    video. Everything after a lone `--` is content by definition."""
    out: list[str] = []
    for token in argv:
        if token == "--":
            break
        if " " in token or not token.startswith("-"):
            continue
        if token.startswith("--"):
            name = token.split("=", 1)[0]
            if name not in known:
                out.append(name)
        elif re.fullmatch(r"-[A-Za-z]", token) and token not in known:
            out.append(token)
    return out


def main(argv: list[str] | None = None) -> int:
    # Before anything is printed, including the errors below.
    speak_utf8()
    argv = list(sys.argv[1:] if argv is None else argv)
    if _flag(argv, "--version", "-V"):
        release = release_of(Path(__file__).resolve().parent.parent)   # its own folder
        print(f"Zenith {ENGINE_VERSION}" + (f" (release {release})" if release else ""))
        return 0
    no_color = _flag(argv, "--no-color", "--plain")
    Out.quiet = _flag(argv, "--quiet", "-q")
    root_opt = _opt(argv, "--root")
    # Settle styling before anything can fail. Finding the root and reading the
    # settings files both happen below and both can `die()` — and those are the
    # messages most likely to be read out of a redirected log, where raw escape
    # codes are exactly what NO_COLOR exists to prevent. The real preference
    # from .os/config.json is applied further down, once it can be read.
    S.setup("never" if no_color else "auto")

    command = argv[0] if argv and not argv[0].startswith("-") else ""
    rest = argv[1:] if command else argv

    if command in ("help", "-h", "--help"):
        try:
            os_ = Zenith(find_root(Path(root_opt) if root_opt else None))
        except SystemExit:
            os_ = None
        S.setup("never" if no_color else "auto")
        return cmd_help(os_, rest)

    handler = COMMANDS.get(command)
    if handler is None and not command.startswith("-"):
        # `./os q3-okr-review` — a bare name is a request to look at it
        try:
            probe = Zenith(find_root(Path(root_opt) if root_opt else None))
            if Finder(probe).by_id(" ".join([command, *rest]) if rest else command):
                handler, rest = cmd_show, [" ".join([command, *rest])] if rest else [command]
        except SystemExit:
            pass
    if handler is None:
        S.setup("never" if no_color else "auto")
        hint = NEAR_MISS.get(command) or next(
            iter(difflib.get_close_matches(
                command, [c for c in COMMANDS if c and not c.startswith("-")], 1, 0.6)), "")
        die(f"there is no './os {command}'."
            + (f"   Did you mean  ./os {hint}  ?" if hint else "")
            + "\n     ./os help   lists everything")
    # `./os file invoice.pdf` is bringing something in, and `./os fix` is asking
    # for the repair. Read as a bare sort and a bare check, the first said
    # everything was filed and brought nothing in, and the second only looked.
    if command == "file" and _theirs(rest):
        handler = cmd_save
        # A file dropped inside the folder by hand is filed by sort, where it
        # lies; save would refuse it as already here.
        try:
            home = find_root(Path(root_opt) if root_opt else None).resolve()
            given = [Path(a).expanduser().resolve() for a in _theirs(rest)]
            if all(g.exists() and home in g.parents and g.parent != home for g in given):
                handler, rest = cmd_sort, [a for a in rest if a.startswith("-") and a != "--"]
        except (SystemExit, OSError):
            pass
    elif command == "fix" and "--fix" not in rest:
        rest = ["--fix", *rest]

    known = FLAGS.get(handler.__name__, set())
    stray = _stray_options(rest, known) if known is not None else []
    if stray:
        S.setup("never" if no_color else "auto")
        spoken = f"./os {command}" if command else "./os"
        hint = next(iter(difflib.get_close_matches(stray[0], sorted(known), 1, 0.6)), "")
        lines = [f"{spoken} doesn't understand {stray[0]}."
                 + (f"   Did you mean  {hint}  ?" if hint else ""),
                 "     " + (f"it takes {', '.join(sorted(known))}"
                            if known else "it takes no options")]
        if handler.__name__ in FREE_TEXT and not hint:
            lines.append("     words of your own that start with a dash go after a bare --")
        lines.append(f"     ./os help {command}" if command else "     ./os help")
        die("\n".join(lines), 2)

    root = find_root(Path(root_opt).expanduser() if root_opt else None)
    ensure_runnable(root)
    os_ = Zenith(root)
    S.setup("never" if no_color else os_.behaviour.get("colour", "auto"))

    # A template is built on one day and opened on another. The first command in
    # a fresh copy quietly re-dates it, so nothing arrives looking stale.
    # `check` and `test` are exempt: they are what a maintainer runs inside the
    # template itself, and neither should make the shipped copy anyone's.
    if os_.is_fresh() and handler not in (cmd_setup, cmd_help, cmd_doctor, cmd_test):
        try:
            os_.initialise()
            Indexer(os_).build()
            # Not a word of it when the answer is being parsed. `--json` means
            # stdout belongs to whatever is reading it, and a greeting printed
            # above the object broke the first `./os brief --json` any AI ran in
            # a folder nobody had opened yet — which is every folder, once.
            if "--json" not in argv:
                Out.raw()
                Out.raw("  " + paint("Welcome — this folder is yours now.", S.GOLD)
                        + paint("   ./os demo", S.FAINT)
                        + paint(" shows you the whole idea in two minutes.", S.FAINT))
        except OSError:
            pass

    # What a run stopped partway had done so far is on the undo list by the
    # time these are said: the lock writes it down on the way out.
    try:
        return handler(os_, rest)
    except KeyboardInterrupt:
        Out.raw()
        Out.warn("stopped partway — ./os undo puts back what it had done so far"
                 if os_.stopped_partway else
                 "stopped — nothing was left half-moved, and ./os undo still works")
        return 130
    except BrokenPipeError:
        return 0
    except OSError as exc:
        # The disk saying no is not a bug in this program, and a Python
        # traceback is the least useful way to say "that folder is read-only".
        # Anything that is *not* an OSError still raises: a real defect should
        # stay loud, and the trace is what makes it reportable.
        where = getattr(exc, "filename", "") or ""
        Out.raw()
        Out.bad(f"the disk would not let me finish: {exc.strerror or exc}")
        if where:
            Out.note(relative_to_root(Path(where), root))
        Out.note("nothing was left half-moved — ./os undo reverses whatever did happen"
                 if os_.stopped_partway else "nothing was left half-moved")
        Out.raw()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
