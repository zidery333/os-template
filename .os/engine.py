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
import bisect
import filecmp
import hashlib
import json
import errno
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import zipfile
import zlib
from pathlib import Path
from urllib.parse import quote, unquote

ENGINE_VERSION = "3.0.0"
MARKER = ".os"
TOOLKIT = "_tools"   # skills/helpers/hooks: they live in .claude/, not a managed folder
#: Where `os save` puts a thing for the moment between writing it down and
#: working out where it goes. Under .os/, deliberately: a drop folder somebody
#: can see is a drop folder things get left in.
STAGING = "incoming"
#: What a copy being brought in is called until it is whole. It ends in "~",
#: so sort never files it; check names one a stop left behind.
PARTIAL = ".partial~"


def claim_partial(landed: Path, folder: bool) -> Path:
    """A name of its own, made on disk now, for a copy to grow under until it
    is whole: `Photos.partial~`, `Photos-2.partial~`. Numbered after the
    mark, `Photos.partial~-2` no longer ended in "~", and sort filed a half
    copy (pre-merge review of round 24); worked out and not made, two chats
    saving a Photos each wrote into one, and one took the other's away."""
    for n in range(1, 500):
        part = landed.with_name(f"{landed.name}{'' if n == 1 else f'-{n}'}{PARTIAL}")
        try:
            if folder:
                os.mkdir(part)
            else:
                os.close(os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            return part
        except FileExistsError:
            continue
    die("too many half-made copies are waiting in .os/cache/incoming — ./os check names them")


def copy_whole(src: Path, dst: Path) -> None:
    """Copy a file into place whole or not at all: beside it under a name
    nothing reads, then swapped in. Copied straight there, a full disk or a
    stop partway left it cut short under its real name, read from then on
    as whole (round 25)."""
    # Short, whatever the name: a long one with more on the end was too
    # long for the disk (pre-merge review of round 25).
    tmp = Path(dst).with_name(f".copy-{os.getpid()}-{hashlib.sha1(Path(dst).name.encode()).hexdigest()[:8]}.tmp~")
    try:
        shutil.copy2(str(src), str(tmp))
        os.replace(tmp, dst)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def drop_partial(part: "Path | None") -> None:
    """Take away a half copy this run claimed (claim_partial made it, so it
    is this run's own), if there is one."""
    if part is None:
        return
    try:
        if part.is_dir() and not part.is_symlink():
            shutil.rmtree(part, ignore_errors=True)
        elif part.exists() or part.is_symlink():
            part.unlink()
    except OSError:
        pass


def place_whole(part: Path, landed: Path) -> Path:
    """Give a whole copy its name, never over another chat's that took the
    name meanwhile: looked at first and then renamed, theirs was replaced
    without a word (second pre-merge review of round 24). A file is linked
    in, which fails rather than replace; a folder's rename fails on one that
    has anything in it. Taken, the next free name. Returns where it went."""
    for _ in range(500):
        if os.path.lexists(landed):
            landed = unique_path(landed)
        try:
            if part.is_dir() and not part.is_symlink():
                # The name made ours first: renamed straight onto it, an
                # empty folder another chat had just made there was replaced
                # (third pre-merge review of round 24).
                os.mkdir(landed)
                try:
                    os.rename(part, landed)
                except BaseException:
                    try:
                        os.rmdir(landed)
                    except OSError:
                        pass
                    raise
            else:
                try:
                    os.link(part, landed)
                except FileExistsError:
                    raise
                except OSError:
                    # A drive with no hard links: looked at, then renamed.
                    if os.path.lexists(landed):
                        raise FileExistsError(errno.EEXIST, "taken", str(landed))
                    os.rename(part, landed)
                    return landed
                # In: the copy is whole under its name. One left behind as
                # well is a partial check names, never a reason to say
                # nothing was saved.
                try:
                    part.unlink()
                except OSError:
                    pass
            return landed
        except OSError as exc:
            if isinstance(exc, FileExistsError) or exc.errno in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOTDIR):
                continue
            raise
    raise OSError(errno.EEXIST, "every name for it is taken", str(landed))
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


def trunc_words(text: str, width: int) -> str:
    """`trunc`, but cut where a word ends: "pictures" read as "pict…"."""
    if vlen(text) <= width:
        return text
    cut = trunc(text, width)[:-1]
    if " " in cut.strip():
        cut = cut[:cut.rstrip().rfind(" ")]
    return cut.rstrip(" ,;:—-") + "…"


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


def slugify(text: str, limit: int = 56, at_word: bool = False) -> str:
    """ASCII slug where possible; otherwise keep the author's own characters.

    Transliterating "設計ノート" to "untitled" would be a quiet data loss, so a
    name that carries no ASCII falls back to a filesystem-safe unicode slug.

    `at_word` cuts a long one where a word ends, for a new file's name:
    "and-the-pepp" read as a typo. Matching still uses the plain cut, so a
    name made before this is never read as wrong and renamed."""
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
        cut = slug[:limit]
        if at_word and slug[limit] != "-" and cut.rfind("-") >= limit // 2:
            cut = cut[:cut.rfind("-")]
        slug = cut.rstrip("-")
    return slug or "untitled"


def handle(name) -> str:
    """A name as it is typed back: one word, so it pastes without quotes.

    A folder is `Q3 OKR Review`; the hint under it says `./os edit
    q3-okr-review`, and every command finds it either way."""
    text = str(getattr(name, "ident", name) or "")
    if nameless(text):
        return text       # "🎉🎉" said as "untitled" named some other thing
    return slugify(text) or text


def nameless(text: str) -> bool:
    """Has this name no letter or number in it, like "🎉🎉"? Then its file
    name is the stand-in `untitled`, which says nothing about which one."""
    return bool(str(text).strip()) and not re.search(r"[^\W_]", str(text), re.UNICODE)


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


#: Marks a terminal trips on in a name. `Build a Trading Bot for NQ1!` could
#: not be typed at all: the shell read the ! as "the last command", even
#: inside quotes, so the folder had to be reached with Work/Build*.
SHELL_MARKS = re.compile(r"[!$`\"'\\*?\[\]{};&|<>]")


def shell_safe(name: str) -> str:
    """A name with the marks a terminal trips on taken out. Marks that sit in
    a word go (`NQ1!` is NQ1, `Gran's` is Grans); the rest leave a space."""
    name = re.sub(r"[!?'`\"]+", "", str(name))
    name = re.sub(r"\s*" + SHELL_MARKS.pattern + r"+\s*", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def copy_failed(exc: BaseException) -> str:
    """Why a copy stopped, in one line. shutil.Error carries every file that
    failed, and a folder of 3,000 printed about a megabyte of them (audit,
    2026-10-29)."""
    if isinstance(exc, shutil.Error) and exc.args and isinstance(exc.args[0], list):
        failed = exc.args[0]
        first = failed[0] if failed else ()
        said = f"{len(failed)} file{'' if len(failed) == 1 else 's'} couldn't be copied"
        if len(first) == 3:
            said += f" — the first, {Path(str(first[0])).name}: {first[2]}"
        return said
    if isinstance(exc, OSError):
        return f"{exc.strerror or exc}" + (f": {Path(str(exc.filename)).name}" if exc.filename else "")
    return str(exc).split("\n")[0][:200]


#: Characters that change which way text runs. Kept in a folder name, they
#: can make `evil plan` show as something else (audit, 2026-10-29).
BIDI_MARKS = "\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069"


def folder_name(title: str, fallback: str = "") -> str:
    """A folder spelled the way a person would write it on a label.

    Title Case With Spaces, built from the title itself so its own capitals
    survive — `Q3 OKR Review`, not the `Q3 Okr Review` that comes back
    from re-casing a slug. Anything a file system cannot take is dropped,
    and so is anything a terminal trips on (see SHELL_MARKS)."""
    clean = re.sub(rf"[\\/:*?\"<>|\x00-\x1f{BIDI_MARKS}]+", " ", shell_safe(one_line(str(title or ""))))
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
    sentence — "Call mum on Sunday" is its own next action.

    The opening sentence only when it starts with something to do. "The roof
    has been leaking since spring" came back as the next step: a description
    of the problem, never said as one (2026-10-28). Not sure means empty."""
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
        if not STEP_FIRST.match(step):
            return ""
        # The first sentence, but "Call Dr. Patel" doesn't end at "Dr.".
        for end in re.finditer(r"[.!?](?=\s)", step):
            if not ABBREVIATION.search(step[:end.start()]):
                step = step[:end.start() + 1]
                break
    step = _shorten(re.sub(r"\s+", " ", step), 100, "…")
    return step[:1].upper() + step[1:]


#: An opening sentence that starts with one of these says what to do, so it
#: can stand as the next action. Anything else is left for them to say.
STEP_FIRST = re.compile(
    r"^\s*(?:buy|book|pay|order|renew|cancel|return|post|email|e-mail|ring|phone|call|text"
    r"|pick\s+up|drop\s+off|send|schedule|collect|fix|replace|clean|wash|write|draft"
    r"|prepare|finish|update|make|publish|share|print|file|read|review|redo|rewrite"
    r"|ask|tell|chase|arrange|plan|start|sign|submit|apply|register|move|measure|try|test"
    r"|set\s+up|look\s+into|research|contact|visit|get|find|check|sort\s+out|talk\s+to"
    r"|install|build|clear|take|put|go\s+to|choose|pick|decide|fill\s+in|sell|list"
    r"|message|reply|answer|confirm|open|close|move|plant|sow|paint|repair)\b", re.I)

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


def lone_note(folder: Path) -> Path | None:
    """The one note in a folder somebody made, when it holds just one, and
    holds it at the top; else None. Pictures and other files beside it don't
    count, and neither do cards."""
    if not folder.is_dir() or folder.is_symlink():
        return None
    found = [p for p in own_paths(folder)
             if p.is_file() and not p.is_symlink() and not ignored(p)
             and p.suffix.lower() in TEXT_SUFFIXES]
    return found[0] if len(found) == 1 and found[0].parent == folder else None


def note_title(path: Path) -> str:
    """What a note calls itself: the title in its header, else its first
    heading, else its file name."""
    meta, body = parse_frontmatter(read_text(path, 40_000))
    said = one_line(str(meta.get("title") or "")).strip()
    if said:
        return said
    h1 = re.search(r"^#\s+(.+?)\s*$", body, re.M)
    if h1 and h1.group(1).strip():
        return _shorten(h1.group(1), 72)
    return given_name(path.stem) or titleize(path.stem)


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


def keep_copy(path: Path, copy: Path) -> Path:
    """Copy `path` to `copy` for undo to keep, never writing over anything
    kept already. A folder merges into one already there: its README, taken
    back a step earlier, was there, and a plain copy failed with "File
    exists", leaving the folder and dropping the undo (review, 2026-10-29).
    A file of the same name lands beside it, as name-2."""
    copy.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir() and not path.is_symlink():
        # A folder it can't read all of isn't kept "whole": rglob passed over
        # it, and the rest was deleted (review, 2026-10-29). Stopping here
        # leaves the folder where it is.
        def unreadable(exc: OSError) -> None:
            raise exc
        for _top, _dirs, _files in os.walk(path, onerror=unreadable):
            pass
        for f in sorted(path.rglob("*")):
            to = copy / f.relative_to(path)
            if f.is_dir() and not f.is_symlink():
                to.mkdir(parents=True, exist_ok=True)
                continue
            to.parent.mkdir(parents=True, exist_ok=True)
            to = unique_path(to)
            if f.is_symlink():
                os.symlink(os.readlink(f), to)
            else:
                shutil.copy2(str(f), str(to))
        copy.mkdir(parents=True, exist_ok=True)
        return copy
    landed = unique_path(copy)
    shutil.copy2(str(path), str(landed))
    return landed


#: Past this many files, a folder's fingerprint is its names and sizes,
#: not what each says: reading 20,000 files to undo a save is a stall. A test
#: lowers it through the environment.
FOLDER_PRINT_CAP = int(os.environ.get("ZENITH_FOLDER_PRINT_CAP") or 20_000)


def folder_print(path: Path) -> str:
    """Every name in a folder and what each says, as one fingerprint: how
    undo tells whether anything came into a saved folder since. Going by
    dates, a file dragged in kept its old one and was deleted (delete audit,
    2026-10-29). Past FOLDER_PRINT_CAP files, names and sizes only, which
    still catch a file dragged in; "" only when it can't be read at all."""
    h = hashlib.sha256()
    try:
        files = sorted(p for p in path.rglob("*") if p.is_file() or p.is_symlink())
    except OSError:
        return ""
    if len(files) > FOLDER_PRINT_CAP:
        # Past the cap, "" fell back to dates, and every undo copied the
        # whole folder (78 MB in a test, 2026-10-29).
        for f in files:
            try:
                st = f.lstat()
                size, when = st.st_size, st.st_mtime_ns
            except OSError:
                size = when = -1
            # Its modified time too: an edit of the same length, "teh" to
            # "the", kept the size, and undo deleted it with no copy
            # (review, 2026-10-29). A file dragged in is caught by its name.
            h.update(str(f.relative_to(path)).encode("utf-8", "replace") + f"\0{size}\0{when}\0".encode())
        return "names:" + h.hexdigest()
    for f in files:
        h.update(str(f.relative_to(path)).encode("utf-8", "replace") + b"\0")
        if f.is_symlink():
            h.update(b"link:" + os.readlink(f).encode("utf-8", "replace"))
        else:
            _digest_file(h, f)
    return h.hexdigest()


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


#: Files kept exactly as they are, with a card, whose words can still be read
#: out from under their formatting. TextEdit saves in RTF unless told not to,
#: so on a Mac this is how most notes written outside this folder arrive; once
#: a picture is pasted in, it saves an .rtfd instead, a folder with the words
#: in a TXT.rtf inside it. See `words_file`.
WORDS_INSIDE = {".rtf", ".rtfd"}
#: The heading on such a file's card that its words are copied in under.
CARD_WORDS = "## What it says"
#: And the line the copy ends with. Search reads the file itself, so it leaves
#: out only what sits between the two: anything written on the card after it,
#: like the one sentence of what it is AGENTS.md asks for, is still searched.
CARD_WORDS_END = "<!-- end of the copied words — anything of your own goes below -->"
#: RTF groups that hold settings rather than words: fonts, colours, pictures.
_RTF_SETTINGS = {
    "fonttbl", "colortbl", "expandedcolortbl", "stylesheet", "info", "pict",
    "header", "headerl", "headerr", "footer", "footerl", "footerr", "listtable",
    "listoverridetable", "rsidtbl", "generator", "filetbl", "revtbl", "themedata",
    "colorschememapping", "latentstyles", "datastore", "xmlnsdecl", "object",
    "fldinst", "nonshppict", "private"}
_RTF_SAYS = {"par": "\n", "line": "\n", "row": "\n", "sect": "\n\n", "page": "\n\n",
             "tab": "\t", "cell": "\t", "emdash": "—", "endash": "–", "bullet": "•",
             "lquote": "‘", "rquote": "’", "ldblquote": "“", "rdblquote": "”"}
_RTF_TOKEN = re.compile(r"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\(.)"
                        r"|([{}])|[\r\n]+|([^\\{}\r\n]+)", re.S)
#: Characters that are not words and that no note should hold; tab and new line stay.
_RTF_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def rtf_words(raw: str) -> str:
    """The words of an RTF document, with its formatting codes taken out.

    Read as it sits on disk, a TextEdit note about tomatoes is a font table
    and then `\\f0\\fs24 \\cf0 Tomatoes need…`, so its card said only "Asset
    card for garden-notes.rtf" and `./os find tomatoes` found nothing. Plain
    Python, so a folder on Linux reads it the same as one on a Mac."""
    out: list[str] = []
    outer: list[tuple[bool, int]] = []
    hidden, uc, owed = False, 1, 0      # a settings group; letters owed after \u
    for m in _RTF_TOKEN.finditer(raw):
        word, arg, byte, symbol, brace, text = m.groups()
        if brace == "{":
            outer.append((hidden, uc))
            owed = 0
        elif brace == "}":
            hidden, uc = outer.pop() if outer else (hidden, uc)
            owed = 0
        elif text is not None or byte is not None:
            piece = text if byte is None else bytes([int(byte, 16)]).decode("cp1252", "replace")
            if owed:     # the stand-in written after a \u letter, for old readers
                cut = min(owed, len(piece))
                piece, owed = piece[cut:], owed - cut
            if not hidden:
                out.append(piece)
        elif symbol is not None:
            if symbol == "*":
                hidden = True
            elif not hidden:
                out.append({"\n": "\n", "\r": "\n", "~": " ", "-": "", "_": "-"}.get(symbol, symbol))
        elif word in _RTF_SETTINGS:
            hidden = True
        elif word == "uc":
            uc = int(arg or 1)
        elif word == "u":
            # A letter by its number, or nothing when it has none that is a
            # letter. `\u99999999` stopped every ./os find with a traceback,
            # for whatever was searched, because one odd .rtf was in the
            # folder; and a bare `\u` put an invisible NUL onto the card.
            if arg is None:
                continue
            n = int(arg) + (65536 if int(arg) < 0 else 0)
            if not hidden and 0 < n < 0x110000:
                out.append(chr(n))
            owed = uc
        elif word in _RTF_SAYS and not hidden:
            out.append(_RTF_SAYS[word])
    # An emoji is written as two \u codes, one half of it each. Read one at a
    # time they stayed two halves, which can't be written to a file at all: a
    # card for a seed order with a 🍅 in it stopped `./os sort` with a
    # traceback, every time it was run. Put back together here; a half on its
    # own becomes a �.
    said = "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    # Nor a control character, however it came: `\'00` put a NUL onto the
    # card, which then counted as a binary file and not a note at all.
    said = _RTF_CONTROL.sub("", said)
    lines = [line.rstrip() for line in said.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def rtf_text(path: Path, limit: int = 2_000_000) -> str:
    """The words in an RTF file, or "" when there are none to be had.

    On a Mac, textutil reads it exactly as TextEdit wrote it; anywhere else,
    or if textutil fails, `rtf_words` does. Only for filing a file, which
    happens once: search reads with `rtf_words` alone, since starting a
    program for every note on every search would make it crawl."""
    if sys.platform == "darwin" and shutil.which("textutil"):
        try:
            done = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", "-encoding", "UTF-8",
                 str(path.absolute())],
                capture_output=True, timeout=20, check=False)
            if done.returncode == 0:
                # textutil keeps what `rtf_words` takes out: `\u0` came through
                # as a NUL, so on a Mac the card still counted as a binary file
                # (review, 2026-09-30). A page break is a form feed; it stays
                # a break, or the words either side of it run together.
                said = done.stdout[:limit].decode("utf-8", "replace").replace("\f", "\n\n")
                return _RTF_CONTROL.sub("", said).strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return rtf_words(read_rtf(path, limit))


def words_file(path: Path) -> Path | None:
    """The RTF that holds a kept file's words, or None when it has none.

    That is the file itself for an .rtf, and the TXT.rtf inside for an .rtfd:
    a TextEdit note with a photo pasted in is saved as a folder, and was kept
    as "a folder of files" whose card said nothing, so `./os find rhubarb`
    missed the one note about it with a picture. Never through a shortcut:
    what it points at is not this folder's to read."""
    suffix = path.suffix.lower()
    if suffix not in WORDS_INSIDE or path.is_symlink():
        return None
    inside = path / "TXT.rtf" if suffix == ".rtfd" else path
    return inside if inside.is_file() and not inside.is_symlink() else None


def read_rtf(path: Path, limit: int = 2_000_000) -> str:
    """An RTF file's own text, codes and all. RTF is written in plain ASCII,
    but a few programs put letters in as they are, in either encoding."""
    try:
        with path.open("rb") as fh:
            data = fh.read(limit)
    except OSError:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


#: The folder being worked on, set when it is opened. "Inside" is judged by
#: this, not by ZENITH_HOME, which only the launcher sets and --root
#: overrides (round 13, 2026-10-29).
HOME: Path | None = None


def _home() -> Path | None:
    home = HOME or (Path(os.environ["ZENITH_HOME"]) if os.environ.get("ZENITH_HOME") else None)
    try:
        return home.resolve() if home else None
    except (OSError, RuntimeError):
        return None


def _under(real: Path, root: Path) -> list[str] | None:
    """The parts of `real` below `root`, or None when it isn't below it. The
    same folder by any other letter case counts: a Mac's disk doesn't tell
    Notes from NOTES (eighth review, 2026-10-29)."""
    try:
        mark = os.stat(root)
    except OSError:
        return None
    for n, up in enumerate([real, *real.parents]):
        try:
            if os.path.samestat(os.stat(up), mark):
                return list(real.parts[len(real.parts) - n:]) if n else []
        except OSError:
            continue
    return None


def inside(path: Path, whole: bool = False) -> bool:
    """Whether a path lands inside the folder being worked on, every shortcut
    on the way followed; `whole` follows the last part too. Undo writes and
    deletes only there: a folder turned into a shortcut after a step had the
    step's old words written into wherever it pointed (round 13, 2026-10-29)."""
    root = _home()
    if root is None:
        return True
    try:
        real = Path(os.path.realpath(path)) if whole else Path(os.path.realpath(path.parent)) / path.name
    except (OSError, ValueError, RuntimeError):
        return False
    if real.exists() or real.is_symlink():
        return _under(real, root) is not None
    return _under(real.parent, root) is not None


def through_link(path: Path) -> Path:
    """A shortcut to a file inside this folder, as the file it points at:
    written over, the shortcut became a plain copy and the file it pointed
    at stopped getting the words (snapshot audit, 2026-10-29). One pointing
    outside the folder, or into its .git/, .os/ or .claude/, is left as it
    was: nothing of ./os's is written there (rounds 12-13, 2026-10-29)."""
    try:
        if not path.is_symlink():
            return path
        target = Path(os.path.realpath(path))
        root = _home()
        parts = _under(target, root) if root and target.is_file() else None
        if parts and len(parts) > 1 and parts[0].lower() not in (".git", MARKER, ".claude"):
            return target
    except (OSError, RuntimeError, ValueError):
        pass
    return path


def move_path(src: Path, dst: Path) -> None:
    """shutil.move, except that a shortcut written as a path from where it
    stands still leads to the same file after the move. Closing one left it
    in Archive pointing at nothing (eighth review, 2026-10-29)."""
    text = None
    if src.is_symlink():
        try:
            text = os.readlink(src)
        except OSError:
            text = None
    if text is None or os.path.isabs(text):
        shutil.move(str(src), str(dst))
        return
    aim = os.path.normpath(os.path.join(str(src.parent), text))
    shutil.move(str(src), str(dst))
    # Made beside it and swapped in whole: taken out first, a shortcut the
    # disk then wouldn't make again was simply gone (ninth review, 2026-10-29).
    spare = dst.with_name(f".{dst.name}.{os.getpid()}.link~")
    try:
        spare.symlink_to(os.path.relpath(aim, str(dst.parent)))
        os.replace(spare, dst)
    except (OSError, ValueError):
        try:
            spare.unlink()
        except OSError:
            pass


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
    path = through_link(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A file of theirs written with Windows line endings keeps them: read,
    # they come in as plain line ends, and every line of it showed as
    # changed once sort put its header on (twenty-second review, round 27).
    # Windows writes them itself.
    if os.name != "nt" and "\r" not in text:
        try:
            with open(path, "rb") as fh:
                head = fh.read(65536)
            if b"\r\n" in head and head.count(b"\r\n") == head.count(b"\n"):
                text = text.replace("\n", "\r\n")
        except OSError:
            pass
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp~")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def made_file(path: Path, text: str) -> bool:
    """A list ./os writes from scratch, never through a shortcut: one made
    to a note of theirs had the note written over after every chat turn
    (seventeenth review, round 22). The shortcut is left as it is. Nor over
    a file of theirs of that name in other capitals, which a Mac takes for
    the same file (their index.md was written over, with no copy), nor one
    of that name without ./os's own first line (twenty-first review, round
    26). Returns whether it was written."""
    if path.is_symlink() or not mine_to_write(path):
        return False
    write_text(path, text)
    return True


def mine_to_write(path: Path) -> bool:
    """Is `path` free for a list ./os makes: nothing there under any
    capitals, or ./os's own list under exactly that name?"""
    try:
        there = [p for p in path.parent.iterdir() if p.name.casefold() == path.name.casefold()]
    except OSError:
        return True
    if not there:
        return True
    if len(there) != 1 or there[0].name != path.name or there[0].is_symlink():
        return False
    # Its first line as it has ever been: before 2026-10-28 it said "written
    # by Zenith", and every older folder's lists were taken for theirs and
    # never kept again (pre-merge review of round 27).
    return read_text(there[0], 400).startswith((GENERATED, GENERATED_BEFORE))


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
    # Checked first: `title: No` read back as False, and a tag `3.10` as 3.1
    # (audit, 2026-10-28). A title or a tag is words, whatever they are.
    if key in STRING_KEYS or key == "tags":
        return raw
    low = raw.lower()
    if low in _SCALAR_TRUE:
        return True
    if low in _SCALAR_FALSE:
        return False
    if low in ("null", "none", "~"):
        return None
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
    # An editor's byte-order mark is not part of the header: a note saved
    # with one was called unfiled, and sort put a second header on top of it
    # (check and learn sweep, 2026-10-29).
    if text.startswith("\ufeff"):
        text = text[1:]
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
        # A plain value written on the lines below its key, as YAML allows:
        # read as empty, a skill's description was missing to check and the
        # catalog (second pre-merge review of round 28). Never a list item or
        # a nested key: those stay as they were read.
        if key is not None and meta.get(key) == "" and line.startswith((" ", "\t")) \
                and not re.match(r"^\s*-\s", line) and not re.match(r"^\s+[\w-]+:(\s|$)", line):
            meta[key] = [line.strip()]
            block = (key, True)
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
    # Quoted when it would read back as something else: a number, or yes/no/
    # null, which any other tool reading the header takes for true, false or none.
    if (key in STRING_KEYS or key == "tags") and isinstance(value, str) \
            and (re.fullmatch(r"-?\d+(\.\d+)?", value)
                 or value.lower() in _SCALAR_TRUE | _SCALAR_FALSE | {"null", "none", "~"}):
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
    # A byte-order mark stays where it is, ahead of the header it was read
    # past: hold put a second header on top of one (thirteenth review).
    if text.startswith("\ufeff"):
        return "\ufeff" + set_fields(text[1:], changes, drop, body)
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
    # Only the filing fields ./os fills in itself, on a note with a date of
    # its own: nothing they wrote changed, so it hasn't been touched. Sort
    # re-dated a note from 2024 to today by adding its subject, and it never
    # showed as gone quiet again (audit, 2026-10-28).
    filed = {k for k in meta if k not in was or was[k] != meta[k]}
    if "updated" in was and not force and filed <= {"title", "type", "domain", "tags"}:
        write_text(path, set_fields(text, {k: meta[k] for k in filed}))
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
    # Nothing reached through a shortcut, and nothing whose real place is
    # outside this folder: .claude or its hooks kept as a shortcut to a shared
    # folder had every script there made runnable (round 18, 2026-10-29).
    try:
        home = Path(os.path.realpath(root))
        real_hooks = Path(os.path.realpath(hooks))
        hooks_ours = real_hooks == home / ".claude" / "hooks"
    except (OSError, ValueError):
        hooks_ours = False
    if hooks.is_dir() and hooks_ours:
        # Not a shortcut: every ./os made the file it points at runnable,
        # outside this folder too (check and learn sweep, 2026-10-29).
        targets += [h for h in hooks.iterdir()
                    if h.is_file() and not h.is_symlink() and h.suffix in (".sh", ".zsh", ".bash", ".py")]
    for target in targets:
        try:
            if target.is_file() and not target.is_symlink() and not os.access(target, os.X_OK):
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
    env = os.environ.get("ZENITH_HOME")
    if start is not None:
        here = Path(start).expanduser().resolve()
        for candidate in [here, *here.parents]:
            if (candidate / MARKER / "config.json").exists():
                if not re.fullmatch(r"\..+\.moved-\d+", candidate.name):
                    return candidate
                # One a move set aside is looked at below as the one named,
                # never run (second pre-merge review of round 22).
                env = str(candidate)
                break
        else:
            die(f"{start} is not a Zenith folder, and neither is anything above it.\n"
                "     --root wants the folder that holds AGENTS.md and .os/")

    # Not one a move set aside, config.json back or not yet hidden: run
    # through ./os, it was a second live folder (pre-merge review of round
    # 22).
    if env and (Path(env).expanduser() / MARKER / "config.json").exists() and \
            not re.fullmatch(r"\..+\.moved-\d+", Path(env).expanduser().name):
        return Path(env).expanduser().resolve()
    for place in [Path(env).expanduser()] if env else [Path.cwd(), *Path.cwd().parents]:
        if (place / MARKER / MOVING_CONFIG).exists() or (place / MARKER).is_dir() and \
                re.fullmatch(r"\..+\.moved-\d+", place.name):
            # Set aside by a move that is finishing: config.json is away
            # from its name so nothing starts here (round 21 live test).
            pid = re.fullmatch(r"\..+\.moved-(\d+)", place.name)
            said_to = read_text(place / MARKER / MOVING_TO, 4000).rstrip("\r\n")
            went = Path(said_to) if said_to and os.path.isabs(said_to) else None
            if went and not (went / MARKER / "config.json").is_file() and \
                    not (pid and Lock._alive(int(pid.group(1)))):
                # Moved there, and partly taken from here, but there can't be
                # reached now (a USB stick taken out): "its things are all
                # here" put the remains back as the folder (fourth pre-merge
                # review of round 21).
                die(f"this folder was moved to {went}, which can't be reached right now, and "
                    f"what is left here may be only part of it. Don't put this back: plug in or "
                    f"open {went} again, and work there.")
            if went and (went / MARKER / "config.json").is_file() and \
                    not (pid and Lock._alive(int(pid.group(1)))):
                # Stopped while the old place was taken away: the whole
                # folder is there, and this is what is left of the old one.
                die(f"this folder was moved to {went}, and all of it is there. What is here is "
                    f"the old place, or part of it: open the folder at {went}, and this can go "
                    f"once you have looked.")
            if place.name.endswith(" (old copy)"):
                die("this is an old copy a move left behind, kept whole, and nothing runs in "
                    "it any more. The folder itself is where it was moved to: work there, and "
                    "this can go once you have looked.")
            if pid and not Lock._alive(int(pid.group(1))):
                # The move stopped there, killed or the computer off: the way
                # back, or every command refused it for good (round 21
                # pre-merge review).
                back = place.with_name(place.name[1:].rsplit(".moved-", 1)[0])
                if os.path.lexists(back):
                    # Not into the one that is there (second and third
                    # pre-merge reviews).
                    stem, n = back.name, 1
                    while os.path.lexists(back):
                        back = back.with_name(f"{stem} (restored{'' if n == 1 else ' ' + str(n)})")
                        n += 1
                # Only the steps still to do: with its config.json back, the
                # first named one that was no longer there. And the copy the
                # move made, live too: put back unsaid, there were two
                # folders (round 22).
                aside = (place / MARKER / MOVING_CONFIG).exists()
                copy_at = read_text(place / MARKER / COPIED_TO, 4000).rstrip("\r\n")
                copy_at = copy_at if copy_at and os.path.isabs(copy_at) and \
                    os.path.realpath(copy_at) != os.path.realpath(str(back)) else ""
                die(f"a move of this folder stopped partway and left it set aside here. Its "
                    f"things are all here. To put it back as it was:\n"
                    + (f"     mv {shell_word(str(place / MARKER / MOVING_CONFIG))} "
                       f"{shell_word(str(place / MARKER / 'config.json'))}\n" if aside else "")
                    + f"     mv {shell_word(str(place))} {shell_word(str(back))}"
                    + (f"\n     The move had made a copy at {copy_at}, and this is the folder. If "
                       "nothing was saved in that copy since, it can go once this is back; if "
                       "something was, keep it." if copy_at else ""))
            hint = "" if pid or not (place / MARKER / MOVING_CONFIG).exists() else (
                "\n     No move running here any more? Put it back with:\n"
                f"     mv {shell_word(str(place / MARKER / MOVING_CONFIG))} "
                f"{shell_word(str(place / MARKER / 'config.json'))}")
            die("this folder is being moved right now, so nothing was changed. Open it in "
                "its new place and run it again there." + hint)
    if env and (Path(env).expanduser() / MARKER / "engine.py").is_file():
        # Its own folder, the one the launcher named, with its config gone:
        # walking on up ran check on a folder around it instead (audit,
        # 2026-10-29).
        die(".os/config.json is missing in this folder, so nothing was changed.\n"
            "     If you keep checkpoints:  git restore .os/config.json\n"
            "     Otherwise copy .os/config.json from a fresh download of the template.")
    here = Path.cwd().resolve()
    for candidate in [here, *here.parents]:
        if (candidate / MARKER / "config.json").exists():
            return candidate
    # engine.py itself lives in <root>/.os/
    guess = Path(__file__).resolve().parent.parent
    if (guess / MARKER / "config.json").exists():
        return guess
    # Plain words, and the way out (audit, 2026-10-29).
    die("this isn't inside a folder ./os runs: no .os/config.json here or in any folder around it.\n"
        "     Run ./os from the top of your folder. If config.json was deleted there:\n"
        "     git restore .os/config.json   (or copy it from a fresh download of the template)")


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
    "skill_body_max_lines": 120, "above_log_max_lines": 150,
    "duplicate_similarity": 0.86, "min_classify_score": 2.0,
    "big_file_mb": 100, "held_check_days": 180,
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
    if base is None and isinstance(ours, list) and isinstance(theirs, list):
        # A list neither run had read yet: each one's additions stay (eighth
        # pre-merge review of round 23: one chat's kept copies were lost).
        base = []
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
        global HOME
        self.root = root
        HOME = root
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
            die(".os/config.json doesn't list the folders to file into (its \"buckets\" list), "
                "so nothing was changed.\n"
                "     If you keep checkpoints:  git restore .os/config.json\n"
                "     Otherwise copy .os/config.json from a fresh download of the template.")
        if not isinstance(self.taxonomy, dict):
            die(".os/words.json should be a block of settings in { } — restore it "
                "from a fresh copy of this folder.")
        self.words_dropped = clean_words(self.taxonomy)
        # A state file edited into something that is not a block of settings is
        # debris rather than state: nothing in it can be read, and refusing to
        # run over it would lock somebody out of their own folder.
        # One that can't be read at all is the same: it locked them out of
        # everything, ./os check --fix too, and asked them to fix by hand a
        # file only ./os writes (audit, 2026-10-29). Kept aside, never lost.
        try:
            loaded = json.loads((self.dot / "state.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            loaded = None
        except OSError as exc:
            # Can't be opened, as opposed to can't be understood: nothing to
            # start again from, and starting again would write over it. So
            # looking still works, ./os check says what's wrong, and anything
            # that would change the folder stops at the lock. Stopping every
            # command, check too, left no way in (review, 2026-10-29).
            loaded = None
            how = ("it's a folder, not a file — move it out of .os/ and run ./os again"
                   if (self.dot / "state.json").is_dir() else
                   "on a Mac, Finder → Get Info → Sharing & Permissions")
            self.state_unreadable = f"can't read .os/state.json ({copy_failed(exc)}) — {how}"
        except ValueError:
            loaded = None
            aside = unique_path(self.dot / "cache" / f"state-unreadable-{today()}.json")
            try:
                aside.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.dot / "state.json", aside)
                (self.dot / "state.json").unlink()
            except OSError as exc:
                # Only ever started again with the old one kept: without a
                # copy, the undo list went with no word (review, 2026-10-29).
                die(f".os/state.json can't be read, and couldn't be kept aside ({copy_failed(exc)}), "
                    "so nothing was changed.\n     If you keep checkpoints:  git restore .os/state.json")
            print(paint("  ! .os/state.json couldn't be read, so ./os started it again — your "
                        "notes and work are untouched; only the list ./os undo works from "
                        f"was lost. The old one is in {aside.relative_to(root)}", S.AMBER),
                  file=sys.stderr)
        self.state = loaded if isinstance(loaded, dict) else {}
        if not hasattr(self, "state_unreadable"):
            self.state_unreadable = ""
        #: state.json as this run last read or wrote it — what `save_state`
        #: compares against to tell this run's changes from another run's.
        self._state_base = json.loads(json.dumps(self.state))
        self._state_text = None   # and its text, once this run has seen it whole
        self._shape_state()
        if not all(isinstance(spec, dict) for spec in self.config["buckets"].values()):
            # A crash report otherwise: 'str' object has no attribute
            # 'setdefault' (audit, 2026-10-29).
            die(".os/config.json lists the folders in a shape ./os can't read, so nothing "
                "was changed.\n     If you keep checkpoints:  git restore .os/config.json\n"
                "     Otherwise copy .os/config.json from a fresh download of the template.")
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
        #: how many files had a link pointed the right way by the last commit
        self.relinked = 0
        #: lines in other files that still name where the last commit moved
        #: something from, which ./os doesn't change: see Relinker.left_behind
        self.left_behind: list[dict] = []

    def _shape_state(self) -> None:
        # `setdefault` only fills a key that is *missing*. A state file carrying
        # `"undo": "yes"` kept the string and crashed the next `./os undo` on
        # `.pop()`, and `"counters": "x"` crashed the next `./os save` — so what
        # is checked is the shape, not merely the presence.
        for key, shape in (("counters", dict), ("undo", list), ("history", list), ("kept_copies", list)):
            if not isinstance(self.state.get(key), shape):
                self.state[key] = shape()
        # Always there, and only names: missing, two chats each keeping one
        # lost the other's to the merge, and a junk one crashed every save
        # (eighth pre-merge review of round 23).
        self.state["kept_copies"] = [n for n in self.state["kept_copies"] if isinstance(n, str)]
        # And each step on the undo list: a junk entry was a crash report on
        # the next ./os undo (audit, 2026-10-29). One that can't be read can't
        # be undone either, so it goes.
        self.state["undo"] = [e for e in self.state["undo"]
                              if isinstance(e, dict) and isinstance(e.get("steps"), list)
                              and all(isinstance(st, dict) and "action" in st for st in e["steps"])]
        if not isinstance(self.state.get("created"), str):
            self.state["created"] = now_iso()

    # -- persistence --------------------------------------------------------

    @staticmethod
    def _load_json(path: Path, required: bool = False):
        """Read a settings file. A broken one is always reported by name: these
        are files people edit by hand, and a stray comma should say so."""
        if not path.exists():
            if required:
                die(f".os/{path.name} is missing, so nothing was changed.\n"
                    f"     If you keep checkpoints:  git restore .os/{path.name}\n"
                    f"     Otherwise copy .os/{path.name} from a fresh download of the template.")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # The line, not Python's sentence about a char offset (audit,
            # 2026-10-29), and the way back that works when there's no backup.
            die(f".os/{path.name} has a typo on line {exc.lineno} and can't be read.\n"
                f"     If you keep checkpoints, this puts it back:  git restore .os/{path.name}\n"
                "     Or fix that line by hand: usually a missing comma or an unclosed quote.")
        except OSError as exc:
            die(f"can't read .os/{path.name}: {copy_failed(exc)} — on a Mac, "
                "Finder → Get Info → Sharing & Permissions")

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
        if self.state_unreadable:
            return      # never written over: see __init__
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
        # Letters as they are: "—" came back as "\u2014" each time the name
        # was set (follow-up to the audit, 2026-10-28).
        write_text(self.dot / "config.json",
                   json.dumps(self.config, indent=2, ensure_ascii=False) + "\n")

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
        """Staged words `os undo` put back, which sort must leave where they
        are. Undoing a save no longer puts anything there (2026-10-22), but a
        folder used with an older version can still hold some: ./os check
        names them and `./os check --fix` clears them.

        Pruned to what is still on disk, so a re-saved thought is filed again
        like anything else and this never becomes a permanent blocklist. Only
        in memory: `./os` and `./os check` read this, and a command that only
        looks must not change .os/state.json, which the folder's history keeps.
        The next command that saves the state saves the pruned list too."""
        kept = [r for r in (self.state.get("taken_back") or [])
                if (self.root / r).exists()]
        if kept != (self.state.get("taken_back") or []):
            self.state["taken_back"] = kept
        self.state.pop("taken_back_said", None)
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
        die(f"no folder is set up for {role} in .os/config.json, so nothing was changed — "
            "if you keep checkpoints:  git restore .os/config.json")

    # -- counters ------------------------------------------------------------
    # Left over from when everything here carried a tag like W.04. Nothing is
    # tagged any more — a thing's handle is its name on disk — so the counters
    # still tick over but nothing is written from them. Kept because an older
    # state file has them, and dropping them would make it unreadable.

    def next_id(self, bucket: str) -> str:
        code = self.buckets()[bucket].get("code")
        if not code:
            die(f"the {bucket}/ folder has no code in .os/config.json, so nothing was "
                "changed — if you keep checkpoints:  git restore .os/config.json")
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

    def record(self, action: str, src: str, dst: str = "", **more) -> None:
        self._pending.append({"action": action, "src": src, "dst": dst, **more})

    def brought_in(self, staged: Path) -> None:
        """Mark the move that filed `staged` as one out of a copy or a note
        this run made itself, so undo takes it away instead of putting it
        back. Put back, it sat in .os/cache/incoming for good: a file somebody
        wanted gone stayed on disk, and sort spoke of it on every run after."""
        rel = self.rel(staged)
        for step in self._pending:
            if step["action"] == "move" and step["src"] == rel:
                step["brought"] = True

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
            # One pointing inside this folder is written through (see
            # through_link), so what it points at is kept the same way.
            if f.is_symlink() and through_link(f) == f:
                # Written over, it becomes a plain file; undo puts the
                # shortcut itself back (eighth review, 2026-10-29).
                links = self.__dict__.setdefault("_links", {})
                try:
                    links.setdefault(self.rel(f), os.readlink(f))
                except OSError:
                    pass
                continue
            # Prose only: a picture renamed or closed was copied in whole each
            # time (review, 2026-10-29). A file that isn't prose is kept with
            # edited(), as ./os name keeps .os/config.json.
            if f.suffix.lower() not in TEXT_SUFFIXES or self.rel(f) in self._snapshots:
                continue
            try:
                size = f.stat().st_size
                # The page a command is about to write is kept whatever its
                # size: "past PROSE_CAP nothing rewrites it" wasn't so, and an
                # 11 MB note closed, decided on or held stayed changed after
                # undo (snapshot audit, 2026-10-29). Only other files are capped.
                # A loose file past PROSE_CAP isn't a note: it gets a card
                # and is never rewritten, so it isn't copied (a 468 MB log
                # once was). A folder's own page is.
                page = f == spine and f.name in ("README.md", "index.md")
                if (size > PROSE_CAP and not page) or (f != spine and size > cap_bytes):
                    continue
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
            # The file it really was, a shortcut followed: undo writes back
            # there or nowhere, never to whatever the name leads to by then
            # (eighth review, 2026-10-29).
            # Only for a shortcut: a plain file's own name is followed through
            # moves instead (see _where_now).
            if f.is_symlink():
                self.__dict__.setdefault("_reals", {})[key] = os.path.realpath(f)

    def _prune_snapshots(self) -> None:
        keep = int(self.behaviour.get("keep_undo_steps", 20))
        live = {e.get("blobs") for e in self.state.get("undo", []) if e.get("blobs")}
        base = self.dot / "cache" / "undo"
        if not base.exists():
            return
        # Never the copies an undo couldn't put back: they are the only ones
        # (pre-merge review of round 23).
        held = set(self.state.get("kept_copies") or [])

        def steps() -> list:
            return [d for d in sorted(base.iterdir()) if NOT_PUT_BACK not in d.name and d.name not in held]
        for d in steps():
            if d.is_dir() and d.name not in live:
                shutil.rmtree(d, ignore_errors=True)
        for d in steps()[:-max(keep, 1)]:
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

    def keep_before(self, path: Path, key: str) -> bool:
        """Keep what `path` says now, for undo, under the name `key`: where it
        was before this run started. Undo puts every moved thing back first and
        its words second, so a note that moved and then had a link fixed must
        be kept under its old place, or its words would never go back.

        The first copy of a file in a run is the one kept, as with `snapshot`."""
        if key in self._snapshots:
            return True
        try:
            data = path.read_bytes()
            blob = hashlib.sha256(key.encode()).hexdigest()[:20] + ".bak"
            (self._ensure_run_dir() / blob).write_bytes(data)
        except OSError:
            return False
        self._snapshots[key] = blob
        self._snapped_at[key] = 0
        return True

    def commit(self, label: str) -> int:
        if not self._pending or getattr(self, "commit_held", 0):
            # Held: a command doing several things as one undo step commits
            # them together at the end (./os name and its About me line,
            # 2026-10-29).
            return 0
        # Links between notes follow anything this run moved, before undo
        # writes down how each file was left: see Relinker.
        self.relinked = 0
        self.left_behind = []
        try:
            relinker = Relinker(self, self._pending)
            self.relinked = relinker.run()
            self.left_behind = relinker.left_behind()
        except Exception:
            pass     # a link left as it was is better than a move undo can't reverse
        # How each file stood when this run was done with it, so undo can tell
        # whether anybody has written in it since: see Undo.revert.
        after = {}
        for rel in self._snapshots:
            now = self.root / self._where_now(rel, self._snapped_at.get(rel, 0))
            # A shortcut page is written through (see through_link), so how it
            # was left is what it points at; without it every undo kept a copy
            # nobody had written in (round 12 live test, 2026-10-29).
            if now.is_file() and (not now.is_symlink() or through_link(now) != now):
                after[rel] = content_print(now)
        for n, step in enumerate(self._pending):
            if step["action"] == "create":
                now = self.root / self._where_now(step["src"], n + 1)
                if now.is_file() and not now.is_symlink():
                    step["digest"] = content_print(now)
            elif step["action"] == "move" and step.get("brought"):
                # What a save brought in, as it was then: see Undo.revert.
                now = self.root / self._where_now(step["dst"], n + 1)
                if now.is_dir() and not now.is_symlink():
                    step["print"] = folder_print(now)
                elif now.is_file() and not now.is_symlink():
                    step["print"] = content_print(now)
        # Which chat did it, so another chat's ./os undo can stop before
        # taking it back: see cmd_undo. Blank when there is no telling.
        links = self.__dict__.pop("_links", {})
        for rel in links:
            now = self.root / self._where_now(rel, 0)
            if now.is_file() and not now.is_symlink():
                after[rel] = content_print(now)
        entry = {"at": now_iso(), "label": label, "steps": self._pending,
                 "snapshots": dict(self._snapshots), "after": after,
                 "reals": self.__dict__.pop("_reals", {}), "links": links,
                 "blobs": self._run_dir.name if self._run_dir else "",
                 "chat": this_chat()}
        follow_brought(self.state, [(st["src"], st["dst"]) for st in self._pending
                                    if st.get("action") == "move" and st.get("src") and st.get("dst")])
        undo = self.state.setdefault("undo", [])
        undo.append(entry)
        # A step made after one undo can't take back is the last thing again.
        self.state.pop("not_undoable", None)
        # At least the one just made: 0 kept every step (`undo[-0:]` is all of
        # it), and a negative number threw away the newest (audit, 2026-10-28).
        keep = max(1, int(self.behaviour.get("keep_undo_steps", 20)))
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

        It used to re-date every file in Work/, Notes/ and Archive/ to today, so
        a template built one day and opened another would not arrive looking
        stale. The blank folder ships none of those, so the only files it ever
        re-dated were the person's own: a note from 2019 dropped in before the
        first ./os said 2026, and undo couldn't put it back (stranger test,
        2026-09-30). `./os setup` on a used folder did the same. What still
        needs a date gets one here: when it was installed, once."""
        day = today()
        fresh = self.is_fresh()

        if owner:
            self.config["owner"] = owner
        if name:
            self.set_name(name)
        # It also wrote `"review": {"last_run": null}` here, which nothing reads
        # and which wiped the date of the last real tidy every time `./os setup`
        # ran on a used folder (2026-10-28). Only `./os tidy` writes that date.
        # The empty one an older version left is cleared here.
        review = self.config.get("review")
        if isinstance(review, dict) and "last_run" in review and review["last_run"] is None:
            del review["last_run"]
        self.save_config()

        self.state["fresh"] = False
        self.state.setdefault("installed", day)
        self.state.setdefault("undo", [])
        # What ./os last reads. Emptied for a copy nobody has opened yet, and
        # kept by `./os setup` in one somebody has.
        if fresh:
            self.state["history"] = []
        self.save_state()
        # Last, so the first save holds the folder as it now is.
        began = History(self).start()
        return {"day": day, "history": began,
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
        # The folders this move has to make, deepest first, so undo can take
        # them away again. Closing one thing makes Archive/<year>/Work, and
        # undoing it left those standing empty.
        made, up = [], dst.parent
        while not up.exists() and self.root in up.parents:
            made.append(self.rel(up))
            up = up.parent
        dst.parent.mkdir(parents=True, exist_ok=True)
        move_path(src, dst)
        self.record("move", self.rel(src), self.rel(dst))
        if made:
            self._pending[-1]["made"] = made
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

    def removed(self, path: Path) -> None:
        """Take one file away, keeping a copy for undo. The copy goes when the
        step falls off the end of what undo remembers. Only for files ./os
        made itself, like what an update set aside: never what they wrote."""
        key = self.rel(path)
        blob = hashlib.sha256(("removed " + key).encode()).hexdigest()[:20] + ".bak"
        shutil.copy2(str(path), str(self._ensure_run_dir() / blob))
        path.unlink()
        self.record("remove", key, blob=blob)

    def edited(self, path: Path, was: bytes) -> None:
        """Remember what a file said before this run rewrote it, so undo can
        put it back. For the few files `snapshot` does not keep because they
        are not prose, like .os/words.json; everything else goes through
        `snapshot`, before the change."""
        key = self.rel(path)
        if key not in self._snapshots:
            blob = hashlib.sha256(key.encode()).hexdigest()[:20] + ".bak"
            (self._ensure_run_dir() / blob).write_bytes(was)
            self._snapshots[key] = blob
            self._snapped_at[key] = len(self._pending)
        self.record("edit", key)

    def make_dir(self, path: Path) -> Path:
        if not path.exists():
            # Every folder made on the way, outermost first, so undo takes
            # them all away again: the demo left an empty Work/ behind.
            missing, up = [], path
            while not up.exists() and (up == path or self.root in up.parents):
                missing.append(up)
                up = up.parent
            path.mkdir(parents=True, exist_ok=True)
            for made in reversed(missing):
                self.record("mkdir", self.rel(made))
        return path


#: Windows, where some of what a process can do is done another way.
ON_WINDOWS = os.name == "nt"


def _alive_on_windows(pid: int) -> bool:
    """Whether a process is running, asked of Windows itself. When it can't
    be asked, it counts as running: a lock is then only taken over once it
    is old."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, int(pid))   # may read its state
        if not handle:
            return ctypes.get_last_error() == 5                  # there, but not ours to read
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259                             # still running
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        return True


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
        if ON_WINDOWS:
            # Signal 0 there is Ctrl-C, sent to everything on the console,
            # not a look: every update stopped itself partway (fifteenth
            # review, 2026-10-29).
            return _alive_on_windows(pid)
        try:
            os.kill(pid, 0)
        except (OSError, TypeError, ValueError):
            return False
        return True

    def _holder(self) -> dict | None:
        """Whoever is holding the lock right now, or None.

        None means the file on disk is debris rather than a holder: unreadable,
        ours already, left by a process that has since died, or simply older
        than any run could plausibly still be. {} means it is gone: let go of
        since the last look, which is nobody's debris."""
        try:
            held = json.loads(self.path.read_text())
            pid = int(held["pid"])
            age = time.time() - float(held.get("at", 0))
        except FileNotFoundError:
            return {}
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
        if getattr(self.os, "state_unreadable", ""):
            # Every change goes through here first, so none is made over a
            # state file that couldn't be read (review, 2026-10-29).
            die(f"{self.os.state_unreadable}. Nothing was changed.")
        if getattr(self.os, "lock_depth", 0):
            # Already this run's: taken again inside it, as ./os name does
            # around its About me line, it's simply still held.
            self.os.lock_depth += 1
            self.nested = True
            return self
        self.nested = False
        # A command an update runs for itself (./os sort, ./os index) works
        # under the update's lock, which the update holds for its whole run
        # (fourteenth review, 2026-10-29): taken again, it waited for itself.
        owner = os.environ.get("ZENITH_LOCK_OWNER", "")
        if owner.isdigit():
            try:
                held = json.loads(self.path.read_text())
                if str(held.get("pid")) == owner and self._alive(int(owner)):
                    self.held = False
                    self.os.lock_depth = 1
                    self.os.refresh_state()
                    return self
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        payload = json.dumps({"pid": os.getpid(), "at": time.time(), "label": self.label})
        tmp = self.path.with_name(f".lock.{os.getpid()}")
        try:
            wait = max(0.0, float(os.environ.get("ZENITH_LOCK_WAIT", self.WAIT)))
        except ValueError:
            wait = self.WAIT
        deadline = time.monotonic() + wait
        cleared = gone_tries = 0
        while True:
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.link(tmp, self.path)
            except FileExistsError:
                # Let go of between our try and this look: nobody's debris,
                # so try again. Taken for debris, the next run's fresh lock
                # was deleted, and two runs were inside at once (audit,
                # 2026-10-29: 4 rounds in 50).
                held = self._holder()
                gone_tries += held == {}
                # A few tries even with no wait at all: with ZENITH_LOCK_WAIT=0
                # a lock let go of in between was called busy (review,
                # 2026-10-29).
                if held == {} and (time.monotonic() < deadline or gone_tries <= 20):
                    time.sleep(0.01)
                    continue
                if held is None:
                    try:
                        self.path.unlink()   # debris from a run that is long gone
                    except OSError:
                        pass
                    cleared += 1
                    if cleared <= 2:
                        continue
                if time.monotonic() >= deadline:
                    if not (self.os.dot / "config.json").is_file():
                        # Being moved: what was written here goes with the
                        # old place, so it is not "safe" (round 21 review).
                        die("this folder was moved or renamed while this was waiting, so "
                            "nothing was kept. Open the folder in its new place and run it "
                            "again there.")
                    if not held:
                        # lost every try to a run that keeps replacing the lock
                        die("another run is already working here. Wait a moment and run it "
                            "again. Still stuck after a minute, with no other chat working here?  "
                            "rm .os/.lock"
                            + (f"\n     {self.safe}" if self.safe else ""))
                    # No "pid", no "dead": what to do, and the line to type
                    # (audit, 2026-10-29).
                    die(f"another run is already working here ('{held.get('label', '?')}'). "
                        "Wait a moment and run it again. Still stuck after a minute, with no "
                        "other chat working here?  rm .os/.lock"
                        + (f"\n     {self.safe}" if self.safe else ""))
                time.sleep(0.15)
                continue
            except OSError:
                # The folder moved away while this waited (./os setup
                # --move-to in another chat): never carried on without a
                # lock into a folder that isn't there (fourteenth review,
                # 2026-10-29).
                if not (self.os.dot / "config.json").is_file():
                    die("this folder was moved or renamed while this was waiting, so nothing "
                        "was changed. Open the folder in its new place and run it again there.")
                # A filesystem with no hard links, or one that will not take the
                # write at all. Neither is a reason to refuse somebody their own
                # folder: no lock is worse than a lock, and better than a wall.
                self.os.refresh_state()
                # Said once: two chats saving at the same moment here lost 4
                # in 200 in a test (2026-10-29). Whether to run like this is
                # the owner's call; saying so isn't.
                # Not when nobody sees it: the session-start hook's quiet brief
                # took it, with stderr thrown away (review, 2026-10-29).
                if not self.os.state.get("said_no_lock") and not Out.quiet:
                    # On stderr, so a --json answer stays whole.
                    print(paint("  ! this drive can't hold ./os's lock, so if two chats change "
                                "things at the same moment here, one can undo or overwrite the "
                                "other — keep to one chat at a time in this folder", S.AMBER),
                          file=sys.stderr)
                    self.os.state["said_no_lock"] = today()
                    # Kept now: a checkpoint never saves state, and said it
                    # every time (review, 2026-10-29).
                    try:
                        self.os.save_state()
                    except OSError:
                        pass
                self.os.lock_depth = 1
                return self
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            # Taken in a folder a move has set aside, or in .os made again
            # where the folder used to be: what runs next would land there
            # and be lost (round 21 pre-merge review).
            if not (self.os.dot / "config.json").is_file():
                try:
                    self.path.unlink()
                except OSError:
                    pass
                die("this folder was moved or renamed just now, so nothing was changed. Open "
                    "it in its new place and run it again there.")
            self.held = True
            self.os.lock_depth = 1
            self.os.refresh_state()
            return self

    def __exit__(self, kind, error, trace) -> bool:
        if getattr(self, "nested", False):
            self.os.lock_depth -= 1
            return False
        self.os.lock_depth = 0
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
            # Only if it is still ours: one taken over meanwhile is another
            # run's (pre-merge review, 2026-10-29).
            try:
                if json.loads(self.path.read_text()).get("pid") == os.getpid():
                    self.path.unlink()
            except (OSError, ValueError, AttributeError):
                pass
        return False

    def keep_fresh(self) -> None:
        """Say again that this run still holds the lock: one held longer than
        STALE_AFTER, as a long update does, was taken for debris and taken
        over (pre-merge review, 2026-10-29)."""
        if not self.held:
            return
        try:
            # Only while it is still ours: one taken over meanwhile (a laptop
            # shut for longer than STALE_AFTER) is another run's, and writing
            # over it let both in at once (second pre-merge review).
            if json.loads(self.path.read_text()).get("pid") != os.getpid():
                self.held = False
                return
            fresh = self.path.with_name(f".lock.{os.getpid()}.fresh")
            fresh.write_text(json.dumps({"pid": os.getpid(), "at": time.time(), "label": self.label}),
                             encoding="utf-8")
            os.replace(fresh, self.path)
        except FileNotFoundError:
            # Gone: taken over and let go of by another run (a laptop shut
            # past STALE_AFTER), so not this run's now (review of the lock
            # timer). A .fresh that couldn't be written is a full disk, below.
            if not self.path.exists():
                self.held = False
        except OSError:
            pass
        except (ValueError, AttributeError):
            # Not a lock this run wrote, so not one it holds: a damaged one
            # crashed an update's wait and let go of nothing (third pre-merge
            # review of round 19).
            self.held = False


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
#: What `made:` says in the header of a folder somebody made and named
#: themselves. Sort never moves one of those into a subject group.
BY_HAND = "by hand"

#: Folders a Mac shows as one document. `Garden.rtfd` is TextEdit's note with
#: a picture in it, the words in a TXT.rtf inside; taken for a folder of notes,
#: it would be pulled apart into the files it is made of. These are the ones
#: seen; document_bundle() takes any folder named the same way.
DOCUMENT_BUNDLES = {".rtfd", ".textbundle", ".pages", ".numbers", ".key", ".app",
                    ".scriv", ".scrivtemplate", ".photoslibrary", ".photolibrary",
                    ".logicx", ".band", ".fcpbundle", ".imovielibrary", ".dtbase2",
                    ".oo3", ".graffle", ".sparsebundle", ".bundle", ".framework",
                    ".plugin", ".xcodeproj", ".xcworkspace", ".playground",
                    ".workflow", ".scptd", ".lproj"}
#: Endings people do give their own folders: a web address, a JavaScript
#: library, a copy kept aside. `Notes/amazon.com`, holding receipts, is theirs.
FOLDER_ENDINGS = {".com", ".org", ".net", ".edu", ".gov", ".old", ".bak", ".new",
                  ".tmp", ".copy", ".backup", ".orig"}


def document_bundle(path: Path) -> bool:
    """Is this a folder a program keeps as one document?

    Finder tells by the ending on the name: `My Novel.scriv` is a Scrivener
    project, shown and opened as one file. Taken for a folder of notes, it
    was pulled apart: a header went into the version.txt and every
    synopsis.txt Scrivener reads back, a card beside every file and folder in
    it, and ./os check then called two of its synopses the same thing. An
    ending of three or more small letters marks one, whatever program made
    it, since nobody names a folder of their own that way."""
    if not path.is_dir() or path.is_symlink():
        return False
    return bundle_ending(path.name)


def bundle_ending(name: str) -> bool:
    """Whether a folder with this name would be taken for one document."""
    ending = Path(name).suffix
    return ending.lower() in DOCUMENT_BUNDLES or (
        bool(re.fullmatch(r"\.[a-z]{3,16}", ending)) and ending not in FOLDER_ENDINGS)

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


def another_os(path: Path) -> bool:
    """Is this a whole other ./os folder kept in here, a copy or a backup?

    It keeps its own lists. Read as one of ours, its own Work showed up here
    as something on the go."""
    try:
        return path.is_dir() and not path.is_symlink() \
            and (path / MARKER / "config.json").is_file()
    except OSError:
        return False


def holds_nothing(path: Path) -> bool:
    """A folder nobody has put anything in yet, so nothing of theirs to file
    or warn about.

    What Finder and Windows leave in any folder, and what an app keeps out of
    sight (a new Obsidian vault holds only `.obsidian`), counts as nothing.
    So do folders made ahead of time with only that in them: `mkdir -p
    Recipes/Italian` is still nothing until a recipe goes in."""
    if not path.is_dir() or path.is_symlink():
        return False
    try:
        return all(p.name in ("Icon\r", "desktop.ini", "Thumbs.db") or p.name.startswith(".")
                   or holds_nothing(p) for p in path.iterdir())
    except OSError:
        return False


def group_trouble(os_: "Zenith", label: str) -> str:
    """Why a folder called `label` in Work or Notes couldn't hold a subject's
    group, or "" when it can.

    Sort names the group after the subject, and anything in a folder ./os
    skips vanishes from ./os, find and check while check says all good:
    Work/Content, where 14 video projects went, a name ending like a
    leftover (`Wine~`), or like a document (`Wine.rtfd`, which a Mac opens
    as one TextEdit file). A slash made a folder inside a folder."""
    if label.casefold() in {n.casefold() for n in IGNORE_FOLDERS}:
        return "which is where big files go, and ./os never looks in there"
    if (not label.strip(" .") or label.startswith(".") or ignored(Path(label))
            or re.search(r'[\\/:*?"<>|\x00-\x1f]', label)):
        return "and ./os skips a folder named like that, as if it were a leftover file"
    if bundle_ending(label):
        return ("and a Mac shows a folder with that ending as one document, "
                "so what's in it couldn't be opened")
    if label.casefold() in {n.casefold() for n in (*os_.buckets(), *IGNORE_NAMES)}:
        return "and this folder already uses that name for something of its own"
    return ""


def group_label(os_: "Zenith", domain: str, labels: dict) -> str:
    """The folder sort groups a thing in, by the subject in its header.

    `./os words --new` refuses a subject whose folder would vanish, but a
    header written by hand never asked: `domain: content` on 14 video
    projects put them all in Work/Content, and ./os, find and check lost
    every one (review, 2026-09-30). A subject with no safe folder of its own
    goes in General, with whatever else is too small for one."""
    label = labels.get(domain) or folder_name(domain or "", "Unsorted")
    return "General" if group_trouble(os_, label) else label


#: Words that say nothing about what is in a group. Sort named groups
#: Help, Https and Nq after words in the titles of the notes in them
#: (reported three times, 2026-10). Pieces of a web address, words that fit
#: anything, and leftovers like "the" never name a group.
JUNK_GROUP_WORDS = {
    "http", "https", "www", "com", "org", "net", "html", "htm", "php",
    "help", "note", "notes", "misc", "miscellaneous", "general", "new", "the",
    "and", "for", "with", "from", "this", "that", "other", "others", "stuff",
    "thing", "things", "item", "items", "untitled", "unsorted", "file", "files",
    "page", "pages", "link", "links", "doc", "docs", "info", "random", "todo",
}


def junk_group_name(name: str) -> bool:
    """Would `name` make a group nobody could guess the contents of?

    True when every word in it is a piece of a web address, a word that fits
    anything, a number, or two letters or fewer. A group named like that
    isn't made, and what would have gone in it stays where it is."""
    words = [w for w in re.split(r"[\W_]+", unaccent(str(name)).lower()) if w]
    return all(len(w) <= 2 or any(c.isdigit() for c in w) or w in JUNK_GROUP_WORDS
               for w in words)


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


def staged_words(path: Path) -> str:
    """What a staged file says first, to name it by; else its file name.

    A TextEdit note taken back with ./os undo was named by its first line as
    it sits on disk, `{\\rtf1\\ansi\\ansicpg1252\\cocoartf2907`, and a
    photo's first line is not words at all."""
    if words_file(path) is not None:
        body = rtf_words(read_rtf(path, 20_000))
    elif not is_binary(path):
        _, body = parse_frontmatter(read_text(path, 2_000))
    else:
        body = ""
    return next((trunc(ln.strip(), 44) for ln in body.split("\n") if ln.strip()), path.name)


#: What belongs at the top of the folder: this program, and the files an AI
#: reads its rules from. Any other file there was dropped in.
TOP_NAMES = {"AGENTS.md", "CLAUDE.md", "README.md", "INDEX.md", "os", "template-feedback.md",
             "CLAUDE.local.md", "AGENTS.override.md", "GEMINI.md",
             # The template ships under a licence; it isn't something to file.
             "LICENSE", "LICENSE.md", "LICENSE.txt"}


#: What ./os snag --export writes at the top, by date when one is there
#: already: filed by sort into Notes, the dated one went to their notes
#: (twenty-first review, round 26).
FEEDBACK = re.compile(r"template-feedback(?:-\d{4}-\d{2}-\d{2}(?:-\d+)?)?\.md")


def loose_at_top(root: Path, buckets=None) -> list:
    """Files and folders dropped at the top of the folder, outside every bucket.

    The most natural place to drop a file, and the one place nothing looked:
    `./os` never mentioned it, sort said everything was filed, find could not
    see it, and save refused it as already in this folder. Sort files these
    like anything else dropped in by hand.

    Folders too. Only files were taken, so `My Recipes` dragged in beside
    Work and Notes was never mentioned, never sorted and never found, while
    check said all good (review, 2026-09-30). Not the buckets themselves
    (`buckets`, read from .os/config.json when not given), nor a folder with
    nothing in it yet, nor a hidden one or one config's `ignore` names. A
    folder called Content is theirs here: only Work/Content is left alone,
    and one dragged in beside Work was never mentioned, sorted or found."""
    if buckets is None:
        try:
            said = json.loads((root / MARKER / "config.json").read_text(encoding="utf-8"))
            buckets = said.get("buckets") if isinstance(said, dict) else None
        except (OSError, ValueError):
            buckets = None
        buckets = buckets if isinstance(buckets, dict) and buckets else ("Work", "Notes", "Archive")
    kept = {str(b).casefold() for b in buckets}
    try:
        return sorted(p for p in root.iterdir()
                      if not p.is_symlink() and not p.name.startswith(".")
                      and (not ignored(p) or (p.name == MEDIA_FOLDER and p.is_dir()))
                      and ((p.is_file() and p.name not in TOP_NAMES and not FEEDBACK.fullmatch(p.name))
                           or (p.is_dir() and p.name.casefold() not in kept
                               and not holds_nothing(p))))
    except OSError:
        return []


def name_key(name: str, folder: bool) -> str:
    """The name a thing answers to, as names are compared: `Pizza/` and
    `pizza.md` both answer to `./os show pizza`. A name with no letters,
    like `🎉🎉`, answers only to itself, not to the `untitled` stand-in."""
    said = nfc(name if folder else Path(name).stem)
    return said.lower() if nameless(said) else slugify(said)


def names_in_group(base: Path, skip: Path | None = None) -> set[str]:
    """Every name already used in this bucket, its subject groups included.

    Sort checked a new name only against the folder it was going into, so a
    second note called Pizza went into Notes while the first was in
    Notes/Food. The next sort put it in General, and ./os check said two
    things were called pizza on every run after, with nothing to fix it
    (stranger test, 2026-09-30). `skip`: the thing being named, whose own
    name isn't in anyone's way."""
    out: set[str] = set()
    todo = [base]
    while todo:
        node = todo.pop()
        try:
            children = list(node.iterdir())
        except OSError:
            continue
        for child in children:
            if ignored(child) or child == skip or child.name.endswith(".card.md"):
                continue
            if Scanner.is_category(child):
                todo.append(child)
                continue
            out.add(name_key(child.name, not child.is_file()))
    return out


class Item:
    """One thing the OS knows about."""

    __slots__ = (
        "path", "bucket", "kind", "ident", "title", "status", "domain", "tags",
        "created", "updated", "summary", "is_dir", "words", "trail",
        "flags", "fingerprint", "spine", "blurb", "managed", "claim", "by_hand",
        "lives",
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
        #: Its header says `made: by hand`: a folder somebody made and named
        #: themselves, which sort adopted where they put it. See Sorter.stays_put.
        self.by_hand = False
        #: A note's `lives:` header: where the video it stands for is kept,
        #: outside this folder or in Work/Content. See footage_note.
        self.lives = ""
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

#: The subject for whatever matches none of the others. It has no words of its
#: own, so nothing lands there except by default.
CATCH_ALL = "general"


def catch_all(taxonomy: dict) -> str:
    """The subject a thing gets when no subject's words match it.

    It was `unsorted`, which `./os check` reads as "no subject set", so a
    folder of home notes that matched nothing got one hint per note telling
    them to pick a subject. `general` is a subject like the others, and the
    group every thin one is pooled into when a folder is split up anyway.
    A words.json without it, one edited by hand or older, still gets
    `unsorted`."""
    domains = taxonomy.get("domains")
    return CATCH_ALL if isinstance(domains, dict) and CATCH_ALL in domains else "unsorted"


#: A line that opens with a heading naming what it is: "Book notes:", "Notes
#: on the trip:", "Thoughts about the garden:", "Film review:".
NOTE_HEADING = re.compile(
    r"^\s*([^:\n]{0,40}?\b(?:notes?|thoughts|review|summary|recap|impressions|takeaways"
    r"|reflections?|observations?)\b[^:\n]{0,40}):", re.I)
#: A heading that starts with one of these is a job, notes or not.
JOB_FIRST = re.compile(r"^\s*(?:write|draft|prepare|finish|send|update|fix|make|publish|post|share"
                       r"|email|print|type|file|read|review|redo|rewrite)\b", re.I)
#: A short line that starts with one of these is a job: "buy milk", "book
#: the dentist", "pay the gas bill". Not "call": "call me Sam" is about them.
ERRAND = re.compile(r"^\s*(?:buy|book|pay|order|renew|cancel|return|post|email|ring|phone|text"
                    r"|pick\s+up|drop\s+off|send|schedule|collect|fix|replace|clean|wash)\s+\S", re.I)


#: The tools Claude Code knows by name, any one of which in a tools: line
#: says it is a helper's.
CLAUDE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "Bash", "Grep", "Glob", "LS", "WebFetch",
                "WebSearch", "Task", "Agent", "TodoWrite", "NotebookEdit", "NotebookRead", "Skill",
                "BashOutput", "KillShell", "SlashCommand"}


def is_skill_folder(path: Path) -> bool:
    """A folder holding SKILL.md by that very name (a Mac's disk took
    skill.md for it), whose header already says a skill's name and what
    it's for: a note folder was moved out of Notes into .claude/skills/,
    photo and all, and given a header to fit (twenty-second review, round
    27)."""
    try:
        if "SKILL.md" not in os.listdir(path):
            return False
    except OSError:
        return False
    meta, _ = parse_frontmatter(read_text(path / "SKILL.md", 20_000))
    return bool(meta.get("name") and meta.get("description"))


def claude_tools(said) -> bool:
    """A helper's tools: line: every entry a tool's name (Read, Bash,
    mcp__x). A note's tools: chisel, saw, drill made it a helper (fifth
    pre-merge review of round 27)."""
    names = said if isinstance(said, list) else str(said or "").split(",")
    names = [str(n).strip() for n in names if str(n).strip()]
    shaped = all(re.fullmatch(r"(?:[A-Z][A-Za-z]+|mcp__[\w-]+)(?:\([^)]*\))?", n) for n in names)
    # And one Claude knows by that name: tools: Figma, Sketch on a note about
    # a designer made it a helper (seventh pre-merge review of round 27).
    known = any(n.split("(")[0] in CLAUDE_TOOLS or n.startswith("mcp__") for n in names)
    return bool(names) and shaped and known


def claude_model(said) -> bool:
    """A helper's model: line names a Claude model, not a car's (model: 2015
    hybrid made a note a helper)."""
    # As Claude Code writes it, in small letters: model: Opus on a note about
    # a car made it a helper (seventh pre-merge review of round 27).
    word = str(said or "").strip()
    return word in ("sonnet", "opus", "haiku", "inherit") or bool(re.fullmatch(r"claude-[a-z0-9.-]+", word))


class Classifier:
    """Decides what an unfiled thing is, what phase it is in, and where it goes.

    Signals, strongest first:
        1. explicit front matter (type / domain / bucket)
        2. a filename convention  (project--x.md, note--x.md, ...)
        3. structural shape       (a folder holding SKILL.md is a skill)
        4. file extension         (media and data are assets)
        5. weighted keyword score against .os/words.json
    Anything below `min_classify_score` is filed as a note. When there was
    nothing at all to go on — no sign of a note or of work, and no subject in
    its words — it is also flagged `needs-review`, so a real guess is visible
    rather than silent.
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
            name: self._terms(list(spec["keywords"]) + list(spec.get("learned", [])),
                              plural=spec.get("learned", []))
            for name, spec in self.tax["domains"].items()
        }
        self._intent_words = {
            name: self._terms(spec.get("keywords", []))
            for name, spec in self.tax.get("intent", {}).items()
        }

    @classmethod
    def _terms(cls, keywords: list, plural=()) -> list:
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
            # Plurals only for words they taught, of four letters or more: on
            # every word, "planes" was "plan" and a note was filed as work
            # (review, 2026-10-29).
            taught = str(kw).lower() in {str(w).lower() for w in plural} and len(key) >= 4
            out.append((str(kw).lower(), cls._matcher(kw, taught)))
        return out

    @staticmethod
    def _matcher(keyword: str, plural: bool = False):
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
        # And, for a word they taught, its plural: taught "wader", "three
        # waders" went to the catch-all, while ./os find forgave it (audit,
        # 2026-10-29).
        if plural:
            # The plural English makes of the last word, not any "es": "plan"
            # took "planes" (review, 2026-10-29). -es after s, x, z, ch, sh;
            # -ies for a y after a consonant; -s otherwise.
            last = re.split(r"[ -]+", k)[-1]
            if re.search(r"(?:s|x|z|ch|sh)$", last):
                joined += r"(?:es)?"
            elif last.endswith("o"):
                joined += r"(?:e?s)?"      # photos, tomatoes
            elif re.search(r"[^aeiou]y$", last):
                joined = joined[:-1] + r"(?:y|ies)"
            else:
                joined += r"s?"
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

    #: Saved words longer than this are a note unless they say they are work.
    LONG_WORDS = 150
    #: The one sign in the words themselves that they are work: a section
    #: for what to do next.
    NEXT_ACTION = re.compile(r"^#{1,4}\s+Next (?:action|step)s?\b", re.I | re.M)
    #: "Checked 2026-09-29", "checked on 3 May 2026", "checked Sept 29, 2026".
    CHECKED = re.compile(r"\bchecked\s+(?:on\s+)?(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}"
                         r"|\d{1,2}\s+[a-z]{3,9}\.?\s+\d{4}|[a-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})", re.I)
    #: Words near the top that say a page is for looking things up.
    LOOK_UP_WORDS = re.compile(r"\b(?:references?|simulat(?:ed|ion|ions)|comparisons?|compared"
                               r"|transcript|transcribed)\b", re.I)
    #: A line of a conversation: "Sam: ...", "[00:01:23] Alex: ...".
    SPEAKER = re.compile(r"^\s*(?:[\[(]?\d{1,2}:\d{2}(?::\d{2})?[\])]?\s*[-–—]?\s*)?"
                         r"([A-Z][\w.'-]{0,20}(?: [A-Z][\w.'-]{0,20}){0,2})\s*:\s+\S", re.M)

    @classmethod
    def _looked_up(cls, title: str, body: str) -> str:
        """What says these words are something to look things up in, or "".

        A table, "checked" with a date, "reference", "simulated",
        "comparison", or a conversation written down."""
        if len(re.findall(r"^\s*\|.*\|\s*$", body, re.M)) >= 3:
            return "a table"
        if cls.CHECKED.search(title + "\n" + body):
            return "checked on a date"
        lines = [line for line in body.strip().split("\n") if line.strip()]
        head = title + "\n" + "\n".join(lines[:5])
        word = cls.LOOK_UP_WORDS.search(head)
        if word:
            return f"it says {word.group(0).lower()}"
        # Speakers come back again and again; "Open:" and "Plan:" don't.
        names = [m.group(1) for m in cls.SPEAKER.finditer(body)]
        if len(names) >= 6 and max(names.count(n) for n in set(names)) >= 3:
            return "a conversation written down"
        return ""

    def _note_by_default(self, lean: str, title: str, body: str) -> str:
        """Why something saved is a note though its words read like work, or ""
        when it is work after all.

        Saved by its path, a file is a note. Saved as words, they are a note
        when there are more than LONG_WORDS of them, or when they are a table,
        a comparison or the like (_looked_up). A `## Next action` in it, or
        `type: work` in its header, keeps it work. One short line is left to
        the guess, so "fix the boiler" is still work."""
        if self.NEXT_ACTION.search(body):
            return ""
        if lean == "path":
            return "a file saved by its path — a note unless it says it is work"
        if len(re.findall(r"[^\W_]+", body)) > self.LONG_WORDS:
            return f"more than {self.LONG_WORDS} words — a note unless it has a ## Next action"
        if "\n" in body.strip():
            sign = self._looked_up(title, body)
            if sign:
                return f"{sign} — something to look up, not to do"
        return ""

    @staticmethod
    def _sample(path: Path) -> tuple[str, str]:
        """(title-ish text, body text) for a file or a folder."""
        rtf = words_file(path)
        if rtf is not None:
            return path.stem, rtf_text(rtf)[:120_000]
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

    #: What a file's extension adds to a subject that lists it. It says what
    #: kind of file this is, never what it is about: while `.md` and `.txt`
    #: counted for Writing, every note anybody saved was filed as writing.
    EXTENSION_WEIGHT = 2.5

    def _worded(self, scores: dict, suffix: str) -> bool:
        """Did any subject match on the words themselves, not only on the
        kind of file? A folder still carrying an older words.json gives every
        .md to Writing on its extension, and that is not something to go on."""
        domains = self.tax["domains"]
        return any(score - (self.EXTENSION_WEIGHT if suffix and suffix in
                            domains.get(name, {}).get("extensions", []) else 0) > 0.01
                   for name, score in scores.items())

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
                total += self.EXTENSION_WEIGHT
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

    def classify(self, path: Path, lean: str = "", said: str = "") -> dict:
        """What `path` is. `lean` is how `./os save` was handed it, "path" or
        "words", and makes a note the guess unless it says it is work (see
        _note_by_default). `said` is what they said it is, with --note or
        --work, and beats any guess."""
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
        # A folder says what it is on its own page, never through a note in
        # it: `Notes/Recipes/Italian/pizza.md` gave Recipes the pizza note's
        # title, and sort renamed the folder after it.
        meta, stripped = parse_frontmatter(body) if body.lstrip().startswith("---") else ({}, body)
        if path.is_dir() and not any((path / n).is_file() for n in SPINE_NAMES):
            meta = {}
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
            if path.is_dir() and is_skill_folder(path):
                verdict["kind"] = "skill"
                verdict["confidence"] += 10
                verdict["why"].append("folder contains SKILL.md")
            elif path.is_file() and suffix in TEXT_SUFFIXES and meta.get("name") and meta.get("description") \
                    and not meta.get("type") \
                    and (claude_tools(meta.get("tools")) or claude_model(meta.get("model"))
                         or (re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(meta.get("name")))
                             and (meta.get("color")
                                  or re.search(r"\b(?:this agent|this helper|proactively)\b",
                                               str(meta.get("description")), re.I)))):
                # A helper says what it may use or what runs it, or is called
                # by a name made to be typed (code-reviewer) and carries what
                # Claude Code writes: a color, or "Use this agent when". Less
                # than that, a note stays a note: name: dentist, "a recipe I
                # use for Sunday lunch", "Use when the boiler breaks" were all
                # taken for helpers (pre-merge reviews of round 27). A note about
                # someone (name: Sarah Jones, description: my dentist) was
                # taken for one, moved into .claude/agents/, and was gone from
                # Notes and from find (twenty-first review, round 26).
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

        # An RTF's words are read for its subject and its card, but it keeps
        # the name it was saved under, the way a .txt does. Only "Untitled.rtf"
        # and the like are named after what they say.
        # A file only: an .rtfd is a folder, and given a title here it was
        # renamed to it, lost its .rtfd, and TextEdit no longer opened it.
        if path.is_file() and words_file(path) is not None and not verdict["title"] \
                and given_name(path.stem):
            verdict["title"], verdict["title_given"] = given_name(path.stem), True

        # 5. content scoring
        domain, dscore, dall = self.score_domain(title_src, body, suffix)
        intent, iscore, iall = self.score_intent(title_src, body)
        if not verdict["domain"] and domain:
            verdict["domain"] = domain
            verdict["why"].append(f"reads as {self.tax['domains'][domain]['label'].lower()} ({dscore:g})")
        guessed = not verdict["kind"]
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

        # Unsure means there was nothing at all to go on: no sign it is a note
        # or work, and no subject in its words. The note-or-work score alone
        # used to decide it, and a plain fact has no cue either way, so "Q3
        # revenue was up 8%" and "Fox dug up the bulbs by the gate" both came
        # back "I wasn't sure what this one was", and so did almost every
        # other thing a person saved. A weak guess is still kept as a note.
        floor = float(self.os.thresholds.get("min_classify_score", 2.0))
        if verdict["confidence"] < floor:
            if not iall and not self._worded(dall, suffix):
                verdict["flags"].append("needs-review")
                verdict["why"].append("not sure what this is — kept in Notes so it is easy to spot")
            if verdict["kind"] not in ("asset", "skill", "agent"):
                verdict["kind"] = "note"
                verdict["status"] = ""

        # Saved, a guess at work is a note unless it says it is work. Pages of
        # simulated odds and a comparison table were filed as work twice, on
        # words like "eval", "pass" and "Open:" (snags, 2026-09-29).
        lean_why = self._note_by_default(lean, title_src, body) \
            if lean and guessed and verdict["kind"] == "project" else ""
        if lean_why:
            verdict["kind"] = "note"
            verdict["status"] = ""
            verdict["why"].append(lean_why)
        # "Book notes: chapter three was slow" was work: "book" starts a line
        # as a job ("book the dentist"). A line headed "<something> notes:",
        # "Thoughts on …:" or "… review:" says what it is, a note about
        # something, unless the heading itself is a job: "Write the release
        # notes: due Friday" (follow-up to the audit, 2026-10-28).
        said_text = body.strip() or str(title_src)
        head = NOTE_HEADING.match(said_text)
        if guessed and not declared and verdict["kind"] == "project" and head \
                and not JOB_FIRST.match(head.group(1)) \
                and self.score_intent("", said_text[head.end():])[0] != "pushing":
            verdict["kind"], verdict["status"] = "note", ""
            verdict["why"].append("headed as notes about something, not a job")
        # "buy milk" was kept as a note, marked "wasn't sure": a few words that
        # start with a job to do are something to do (audit, 2026-10-28).
        # Only short ones: a paragraph that opens "Book" is about a book.
        line = body.strip() or str(title_src).strip()
        if guessed and not declared and verdict["kind"] == "note" and "\n" not in line \
                and len(line.split()) <= 8 and ERRAND.match(line):
            verdict["kind"], verdict["status"] = "project", PUSHING
            verdict["flags"] = [f for f in verdict["flags"] if f != "needs-review"]
            verdict["why"].append("a few words that start with a job to do")
        # What they said it is: --note or --work.
        if said == "note" and verdict["kind"] == "project":
            verdict["kind"], verdict["status"] = "note", ""
            verdict["why"].append("they said it is a note")
        elif said == "work" and verdict["kind"] in ("note", "project", "asset"):
            if verdict["kind"] != "project":
                verdict["status"] = ""
            verdict["kind"] = "project"
            verdict["status"] = verdict["status"] or PUSHING
            verdict["why"].append("they said it is work")
        if said in ("note", "work"):
            verdict["flags"] = [f for f in verdict["flags"] if f != "needs-review"]

        if not verdict["domain"]:
            verdict["domain"] = catch_all(self.tax)
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

    @staticmethod
    def borrows_page(path: Path, spine: Path | None) -> bool:
        """Is this a folder with no page of its own, read through the first
        note in it?

        `mkdir Notes/Recipes` with two saved notes moved into it was read as
        the one about chilli oil: sort renamed the folder after that note,
        and a folder like it in Work went to Notes, because the note said
        `type: note` (review, 2026-09-30). A folder made like that is known
        by its own name and is what the place they put it says, and sort
        neither renames nor moves it. Every note in it is still found, by
        its words (Finder._inside)."""
        return spine is not None and spine.parent == path and spine.name not in SPINE_NAMES

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
        item.by_hand = str(meta.get("made") or "").strip().lower() == BY_HAND
        item.lives = str(meta.get("lives") or "").strip()
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
        borrowed = item.is_dir and self.borrows_page(item.path, item.spine)
        raw_status = meta.get("status")
        if borrowed:
            # The note's own title is kept only when it is the folder's name,
            # spelled as they spelled it: an older sort named such folders
            # after it, `where-i-learn` for "Where I learn". A long title it
            # cut short to name the folder is kept too, or an update renamed
            # an old folder in every list, and words only in its title went
            # unfound (a released folder, 2026-09-30).
            own = given_name(item.path.name) or item.path.name
            # One made in Notes for a single note is known by that note's own
            # title instead (see Sorter.place).
            if self.os.buckets().get(item.bucket, {}).get("role") == "note" \
                    and lone_note(item.path) == item.spine:
                item.title = note_title(item.spine)
            elif slugify(item.title, 44) != slugify(own, 44) \
                    and folder_name(item.title, slugify(item.title, 44)) != item.path.name:
                item.title = own
            # A note's `status: —` says it is no piece of work; the folder in
            # Work is one, on the go until they hold it.
            if str(raw_status or "").strip() == "—":
                raw_status = ""
        # One shelf now holds prose and files alike, so what a thing *is* comes
        # from its own header; the folder only says where it lives. Without this
        # a PDF filed in Notes would call itself a note in every listing.
        if item.kind in ("note", "project") and not borrowed:
            declared = str(meta.get("type") or "").strip().lower()
            resolved = TYPE_FROM_DISK.get(declared, declared)
            if resolved in ("project", "note", "asset"):
                item.kind = resolved
        item.status = normalize_status(raw_status, item.kind, meta.get("type"))
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
            if ignored(child) or another_os(child):
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
            # A script runs; a word list beside it (plain-words.tsv) is only
            # read, so it isn't listed as something that runs on its own.
            if ignored(f) or f.is_dir() or f.suffix.lower() in (".tsv", ".txt", ".md", ".json"):
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
#: ones people type. Anything not here is left alone and shown as written,
#: and ./os check names it: typed by hand, `status: paused` took that work
#: off every list, neither on the go nor kept level.
STATUS_ALIASES = {
    "active": PUSHING, "in-progress": PUSHING, "in progress": PUSHING,
    "open": PUSHING, "doing": PUSHING, "started": PUSHING,
    "ongoing": HOLDING, "maintained": HOLDING, "area": HOLDING,
    "paused": HOLDING, "on hold": HOLDING, "on-hold": HOLDING, "waiting": HOLDING,
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
      2. identify— every managed item's header is given what search needs;
                   nothing already filed is renamed
      3. balance — only for `./os sort --regroup`: groups appear when a
                   bucket earns them and fold back when it no longer needs
                   them (with hysteresis, so a bucket hovering at the
                   threshold does not thrash)
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
        self.brought: set[Path] = set()                # folders taken in from the top of this folder

    # -- pass 1: staged captures, and anything dropped in by hand ------------

    def _take(self, src: Path, here: bool = False, by_hand: bool = False) -> Path | None:
        """Classify one unfiled thing and put it where it belongs."""
        verdict = self.classifier.classify(src)
        try:
            dest = self.place(src, verdict, here, by_hand)
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
        # An older undo of a save put the words back here, where they started.
        # The next sort used to file them straight out again, so two items
        # undone on 2026-09-08 came back on their own and had to be closed to
        # make it stick. Something the person took back is not something
        # waiting, and sort says nothing about it: ./os check offers to clear
        # them, once (see Doctor._taken_back).
        refused = self.os.taken_back()
        filed = 0
        for child in sorted(stage.iterdir()):
            if ignored(child):
                continue
            # Not a failure to file, so not in `skipped`: counted there, it made
            # sort exit 1 and `./os` nag about it on every run after.
            if self.os.rel(child) in refused:
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

    def bring_in_from_top(self) -> int:
        """Move each folder left at the top of this folder into the bucket it
        belongs in, under its own name, to be sorted from there the way a
        folder dropped into that bucket by hand is.

        Only files were taken from the top, so `My Recipes`, dragged in
        beside Work and Notes, was never mentioned, sorted or found (review,
        2026-09-30). Filed the way a saved folder is, it would lose its name
        to a lower-case one. From inside Notes it is filed where it lies,
        under its own name, the same as a folder dragged into Notes. Footage,
        a program's document, a skill or a helper go on the way anything
        else at the top does, in adopt()."""
        moved, names = 0, None
        for src in loose_at_top(self.os.root, self.os.buckets()):
            if not src.is_dir() or document_bundle(src):
                continue
            role = ROLE_FOR_KIND.get(self.classifier.classify(src)["kind"])
            if role is None or big_media(self.os, src):
                continue
            if names is None:
                names = {it.ident.casefold() for it in self.scanner.scan() if it.ident}
            # Its own name, or Finder's `My Recipes 2` when that is in use
            # anywhere here, so no two things share a name.
            base = self.os.root / self.os.bucket_for_role(role)
            dest, n = base / src.name, 2
            while dest.exists() or dest.is_symlink() or ignored(dest) \
                    or dest.name.casefold() in names:
                dest, n = base / f"{src.name} {n}", n + 1
            names.add(dest.name.casefold())
            self.brought.add(src)
            self.moves.append(("folder", self.os.rel(src), self.os.rel(dest)))
            if not self.dry:
                self.os.move(src, dest)
            moved += 1
        return moved

    def adopt(self, items: list["Item"]) -> int:
        """Take charge of anything sitting in a bucket that the OS never filed.

        With no drop folder, the natural move is to drag a PDF straight into
        Notes//. Left alone that is a file with no card — no subject, no tags,
        and nothing for a search to match on. So the sorter adopts it
        where it lies, rather than asking the person to have put it somewhere
        special first. A loose file still goes where it belongs: a PDF dropped
        into Work/ is a thing you look up later wherever you put it. A folder
        somebody made stays where they made it, under their name for it."""
        todo = [(it.path, True) for it in items
                if it.bucket in self.os.buckets() and self.unmanaged(it)]
        # A file left at the top of the folder lies in no bucket at all, so it
        # goes where it belongs, the same as a file that was saved. A folder
        # there went into its bucket first (bring_in_from_top).
        todo += [(path, False) for path in loose_at_top(self.os.root, self.os.buckets())
                 if path not in self.brought]
        # A preview leaves a folder at the top, where the real run files it
        # from the bucket it was brought into; counted the same, or the
        # preview said "2 filed" where sort then said "3 filed".
        taken = len(self.brought) if self.dry else 0
        for path, here in todo:
            if self._still_being_written(path):
                self.waiting.append(self.os.rel(path))
                continue
            if self.dry:
                verdict = self.classifier.classify(path)
                dest = self.place(path, verdict, here=here, by_hand=True)
                if dest is not None:
                    self.moves.append((verdict["kind"], self.os.rel(path),
                                       self.os.rel(dest)))
                    taken += 1
                continue
            if self._take(path, here=here, by_hand=True) is not None:
                taken += 1
        return taken

    def file_one(self, src: Path, lean: str = "", said: str = "") -> tuple[Path | None, dict]:
        """Classify and place exactly one thing.

        `os save` uses this so nothing is ever left staged waiting for the
        person to remember a second command. `lean` and `said`: see
        Classifier.classify."""
        verdict = self.classifier.classify(src, lean, said)
        dest = self.place(src, verdict)
        if dest is not None:
            self.moves.append((verdict["kind"], self.os.rel(src), self.os.rel(dest)))
        return dest, verdict

    def home(self, bucket: str, domain: str, tags: list) -> Path:
        """The folder something new goes in, decided once, when it is made.

        Sort used to make a note at the top of Notes and move it into a
        subject group later, so a path written down in between went stale:
        a skill and a bot's code both pointed at notes that had moved
        (reported, 2026-10). Now it goes straight into the group it belongs
        in, by the same rule a regroup uses (group_label, then a tag), and
        nothing moves it after. Only into a group that is already there:
        making new groups is what `./os sort --regroup` is for."""
        base = self.os.root / bucket
        if not self.os.buckets().get(bucket, {}).get("categorize"):
            return base
        labels = {k: v["label"] for k, v in self.os.taxonomy["domains"].items()}
        label = group_label(self.os, domain, labels)
        if junk_group_name(label) or not Scanner.is_category(base / label):
            return base
        base = base / label
        # Never a group named after the one it is in, or after a word that
        # says nothing: an earlier release made Garden/Garden and Help.
        try:
            subs = {name_key(p.name, True): p for p in base.iterdir()
                    if Scanner.is_category(p) and not junk_group_name(p.name)
                    and name_key(p.name, True) != name_key(label, True)}
        except OSError:
            return base
        for tag in (tags or [])[:5]:
            sub = subs.get(name_key(titleize(tag), True))
            if sub is not None:
                return sub
        return base

    def place(self, src: Path, verdict: dict, here: bool = False,
              by_hand: bool = False) -> Path | None:
        """Put one thing where it belongs. `here`: it was dropped into a
        bucket by hand, rather than saved. `by_hand`: dropped in anywhere
        here by hand, at the top of this folder too."""
        kind = verdict["kind"]
        slug = slugify(verdict["title"] or src.stem, 44)
        # A note's or a file's own name is cut where a word ends. Skills and
        # helpers keep the plain cut: their name is checked against it.
        named = slugify(verdict["title"] or src.stem, 44, at_word=True)
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
                    # Only an empty folder goes. ignored() isn't "nothing":
                    # it covers CLAUDE.md, README.md, Content/ and more, and
                    # all of those went with the folder, unrecorded (delete
                    # audit, 2026-10-29). Anything else and the folder stays.
                    clutter = (".DS_Store", "Thumbs.db", "desktop.ini")
                    if all(p.is_file() and (p.name in clutter or p.name.startswith("._"))
                           for p in src.rglob("*")):
                        shutil.rmtree(src, ignore_errors=True)
                    return moved
            else:
                moved = self.os.move(src, dest)
                self._ensure_agent(moved, verdict)
                return moved

        # A program's document, `Garden Plan.rtfd` or `My Novel.scriv`, is one
        # file to a Mac, whatever is inside it. Filed from the top of this
        # folder as a folder, it lost its ending and no longer opened as a
        # document, and a .scriv got a README of ours inside it (review,
        # 2026-09-30). It is kept whole, with a card beside it, the way a
        # file is, under the name it came with.
        bundle = document_bundle(src)
        if bundle and kind in ("note", "project"):
            kind = verdict["kind"] = "asset"
            verdict["status"] = ""
        # A folder is somebody's own: its files are never rewritten (see
        # _ensure_spine). One they made by hand is also adopted where it lies —
        # the bucket they put it in says what it is, and a name they chose is
        # its name. `Work/Wedding Speech` became `Notes/venue-the-old-barn`,
        # after the first line of the notes inside it.
        theirs = src.is_dir() and not src.is_symlink() and not bundle
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
            # A folder made in Notes for one note is known by that note's own
            # title, and stays where it is. Named after the folder, ./os find
            # listed `Trading` for a note that calls itself something else
            # (reported, 2026-10).
            only = lone_note(src) if kind == "note" else None
            if only is not None:
                verdict["title"], keep = note_title(only), True
            # Said in its header, so sort never moves it into a subject group
            # however big the folder gets (stays_put).
            verdict["by_hand"] = True

        # A file dropped in by hand keeps the name and ending it came with.
        # `Shopping List.txt` became shopping-list.md, and TextEdit, saving it
        # again, wrote a second copy that sort filed as shopping-list-2.md
        # (stranger test, 2026-09-30). Only a name the computer chose, like
        # Untitled.txt, is named after what it says, and keeps its ending.
        # Words given to ./os save, and files it's handed, are named as before.
        # One that reads like work goes into a folder of its name, as it is:
        # `To Do.txt` became Work/To Do/README.md, and the .txt was gone
        # (review, 2026-09-30). Only a .md is still made the page itself.
        # A program's document keeps its name however it comes in.
        own = ""
        whole = src.is_file() and (kind != "project" or src.suffix.lower() not in (".md", ".markdown"))
        if not src.is_symlink() and (bundle or by_hand and whole) \
                and kind in ("note", "asset", "project"):
            name = src.name if bundle else src.stem
            own = given_name(name)
            # The same words as the name, or sort would rename it to match
            # its title the next time round (identify).
            if own and slugify(own, 44) == slugify(name, 44):
                verdict["title"] = own
            else:
                own = ""

        bucket = self.os.bucket_for_role(ROLE_FOR_KIND.get(kind, "note"))
        ident = f"{self.os.buckets()[bucket]['code']}.??" if self.dry else self.os.next_id(bucket)
        # Dropped by hand into its bucket, or into a group in it: filed where
        # it lies. Anything else goes straight into the group it belongs in,
        # and stays there (home).
        top = root / bucket
        lies = src.parent
        if here and (lies == top or (top in lies.parents and all(
                Scanner.is_category(p) for p in [lies, *lies.parents] if top in p.parents))):
            base = lies
        else:
            base = self.home(bucket, verdict.get("domain", ""), verdict.get("tags") or [])

        # Names are plain words, and the name is the handle: nothing is tagged.
        # A name ./os never looks inside (Content, README.md) is taken too: work
        # called "Content" was made inside the media folder, and vanished.
        # Every name in use here, in its subject groups too, so no two
        # things in one bucket answer to one name (names_in_group).
        folder = kind == "project" or not src.is_file()
        used: set[str] | None = None

        def in_use(cand: Path) -> bool:
            # The thing itself is in nobody's way, but its name can be, when
            # something elsewhere in the bucket has it.
            nonlocal used
            if cand != src and (cand.exists() or cand.is_symlink() or ignored(cand)):
                return True
            if used is None:
                used = names_in_group(top, skip=src)
            return name_key(cand.name, folder) in used

        def free(name: str) -> Path:
            cand = base / name
            # Adopting a file where it already lies is not a move — without
            # this it went to `name-2` and straight back again.
            if not in_use(cand):
                return cand
            suf = "" if folder and not bundle else cand.suffix
            stem = cand.name[: -len(suf)] if suf else cand.name
            n = 2
            while in_use(base / f"{stem}-{n}{suf}"):
                n += 1
            return base / f"{stem}-{n}{suf}"

        def move_to(dest: Path) -> Path:
            return src if dest == src else self.os.move(src, dest)

        def own_place(name: str = src.stem, ending: str = src.suffix) -> Path:
            # Where it lies, when that is the folder it belongs in, or else
            # beside the rest there, under its own name: `Shopping List 2.txt`
            # when that is taken, as Finder would, and called that. Less any
            # mark a terminal trips on (see SHELL_MARKS).
            name = shell_safe(name) or name
            if here and base in src.parents and src.name == name + ending \
                    and not in_use(src):
                return src
            dest, n = base / f"{name}{ending}", 2
            while in_use(dest):
                dest, n = base / f"{name} {n}{ending}", n + 1
            called = dest.name if bundle or not ending else dest.name[:-len(ending)]
            verdict["title"] = given_name(called) or verdict["title"]
            return dest

        if kind == "project":
            dest_dir = src if keep else own_place(own, "") if own \
                else free(folder_name(verdict["title"], slug))
            if self.dry:
                return dest_dir
            if src.is_dir():
                moved = move_to(dest_dir)
            else:
                self.os.make_dir(dest_dir)
                inner = dest_dir / ("README.md" if src.suffix.lower() in TEXT_SUFFIXES and not own
                                    else src.name)
                self.os.move(src, inner)
                moved = dest_dir
            self._ensure_spine(moved, ident, verdict, kind, theirs)
            return moved

        if kind == "asset":
            suffix = src.suffix if src.is_file() or bundle else ""
            dest = src if keep else own_place() if own else free(
                folder_name(verdict["title"], named) if src.is_dir() and not bundle
                else f"{named}{suffix}")
            if self.dry:
                return dest
            moved = move_to(dest)
            self._sidecar(moved, ident, verdict)
            return moved

        # note
        if src.is_dir():
            # A folder is named like every other folder: Title Case With
            # Spaces. A saved folder of notes was called `trading-notes`.
            dest = src if keep else free(folder_name(verdict["title"], named))
            if self.dry:
                return dest
            moved = move_to(dest)
            self._ensure_spine(moved, ident, verdict, "note", theirs)
            return moved
        if own:
            dest = own_place()
        else:
            ending = src.suffix if by_hand or src.suffix.lower() not in TEXT_SUFFIXES else ".md"
            dest = free(f"{named}{ending}")
        if self.dry:
            return dest
        moved = self.os.move(src, dest)
        if moved.suffix.lower() in TEXT_SUFFIXES:
            # A .txt takes its header inside, as a .md does, and not on a card
            # beside it: search reads a note's own words, and with a card it
            # would have read only the card's. TextEdit opens and saves it as
            # the plain text it is, header and all, under the same name.
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
            readme = folder / "README.md"
            spine = folder.with_name(folder.name + ".card.md") \
                if code or readme.exists() else readme
            # A piece of work they wrote up themselves, with its decisions, log
            # or next action in its README: the header goes on that README.
            # With a card beside it, a decision made later went into the card,
            # and its decisions were split across two files (stranger test,
            # 2026-09-30). Only the header is added; every line of theirs
            # stays as it is.
            if kind == "project" and not code and self._worked_on(readme):
                spine = readme
                # Kept as it was, so ./os undo puts it back word for word.
                self.os.snapshot(spine)
                self.os.record("edit", self.os.rel(spine))
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
            "made": BY_HAND if verdict.get("by_hand") else None,
        })
        self._finish_header(spine, verdict)

    #: The sections of a piece of work that only grow or change as it goes.
    WORK_SECTIONS = re.compile(r"^##\s+(Decisions|Log|Next action)\b", re.M | re.I)

    @classmethod
    def _worked_on(cls, readme: Path) -> bool:
        """Does this README already keep a piece of work's decisions, log or
        next action? One that isn't plain text is kept as it is, with a card."""
        if not readme.is_file() or readme.is_symlink():
            return False
        text = read_utf8(readme)
        return text is not None and bool(cls.WORK_SECTIONS.search(parse_frontmatter(text)[1]))

    def _sidecar(self, asset: Path, ident: str, verdict: dict) -> None:
        card = asset.with_name(asset.name + ".card.md")
        if card.exists():
            return
        # A file whose words can be read carries them, so the card says what
        # is in it and not only its name (see WORDS_INSIDE).
        said = verdict.get("captured", "") if asset.suffix.lower() in WORDS_INSIDE else ""
        write_text(card, compose({
            "title": verdict["title"], "type": "file",
            "status": "—", "domain": verdict["domain"], "tags": verdict["tags"],
            # `updated:` from the start: without it the next sort stamped
            # one in, unrecorded, and undo then refused to take the save back
            # as "written in since" (audit, 2026-10-29).
            "created": today(), "updated": today(), "source": asset.name,
        }, f"# {verdict['title']}\n\nAsset card for `{asset.name}`.\n\n"
           + (f"{CARD_WORDS}\n\n{said}\n\n{CARD_WORDS_END}\n" if said
              else f"{verdict.get('summary','')}\n")))
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
        # pass makes sure the header carries what search needs. It never
        # renames anything already filed: sort renamed notes to match their
        # titles, and a note made with ./os new minutes before, whose path had
        # already been given out, got a new name (reported, 2026-10). A name
        # changes only when somebody asks:  ./os rename.
        fixed = 0
        for it in items:
            if it.kind not in ("project", "note", "asset"):
                continue
            # Anything adopt() chose to leave alone stays alone: stamping a
            # header onto it here would file it without ever classifying it.
            if not it.managed:
                continue
            # A folder they made, read through a note in it: its name is
            # theirs, and that note's header is the note's (borrows_page).
            if it.is_dir and Scanner.borrows_page(it.path, it.spine):
                continue
            # A headed file counts as filed, but its header can still disagree
            # with where it sits — a hand-dropped folder saying `type: work`
            # left in Notes is work, so it belongs in Work. It keeps its name.
            home = self.os.bucket_for_role(ROLE_FOR_KIND.get(it.kind, "note"))
            if it.bucket != home:
                base = self.home(home, it.domain, it.tags)
                dest = base / it.path.name
                n = 2
                while dest.exists() or ignored(dest):
                    # A folder's whole name: `v2.0 Launch` has no file type to keep apart
                    dest = base / (f"{it.path.stem}-{n}{it.path.suffix}" if it.path.is_file()
                                   else f"{it.path.name}-{n}")
                    n += 1
                if self.dry:
                    self.moves.append(("id", self.os.rel(it.path), self.os.rel(dest)))
                    fixed += 1
                    continue
                new = self.os.move_item(it.path, dest)
                self.moves.append(("id", self.os.rel(it.path), self.os.rel(new)))
                it.path, it.bucket = new, home
                it.trail = [p.name for p in reversed(new.parents)
                            if self.os.root / home in p.parents]
                it.spine = self.scanner.spine_of(new)
                fixed += 1
            if not self.dry and it.spine and it.spine.exists():
                # "unsorted" is the scanner's word for none, which check then
                # reported as "no subject set" on a note sort had just filed
                # (audit, 2026-10-28): the folder's catch-all subject instead.
                # What it said before is kept for undo, when it changes: a note
                # already filed had lines added with no step, and undo then
                # said it put everything back (snapshot audit, 2026-10-29).
                try:
                    was = it.spine.read_bytes()
                except OSError:
                    was = None
                stamp_file(it.spine, {"title": it.title,
                                      "type": TYPE_ON_DISK.get(it.kind, it.kind),
                                      "domain": it.domain if it.domain != "unsorted"
                                      else catch_all(self.os.taxonomy),
                                      "tags": it.tags})
                try:
                    if was is not None and it.spine.read_bytes() != was:
                        self.os.edited(it.spine, was)
                except OSError:
                    pass
            it.ident = it.path.stem if it.path.is_file() else it.path.name
        return fixed

    # -- pass 3: balance ----------------------------------------------------

    def _cluster_key(self, group: list[Item], depth_cap: int, parent: str = "") -> dict[str, str]:
        """Assign each item in `group` a second-level folder, deterministically.

        Named after a tag, so never one ./os would skip, as the first level
        isn't: 14 video projects tagged `content` went into
        Work/General/Content, where ./os never looks, and the next sort took
        General away with all of them in it (review, 2026-09-30).

        Never named after the group it sits in, and never after a tag every
        one of them has. Fourteen garden notes, each tagged `garden`, went
        into Notes/Garden/Garden: a folder inside a folder of the same name,
        with everything in it and nothing beside it (found 2026-10-01). A
        sub-group that takes all of them splits nothing, so it isn't made."""
        tally: dict[str, int] = {}
        safe: dict[str, bool] = {}
        mine = name_key(parent, True) if parent else ""
        for it in group:
            for t in it.tags[:5]:
                if t and t != "unsorted" and safe.setdefault(
                        t, not group_trouble(self.os, titleize(t))
                        and not junk_group_name(t)
                        and name_key(titleize(t), True) != mine):
                    tally[t] = tally.get(t, 0) + 1
        floor = max(2, len(group) // 8)
        ranked = [t for t, n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
                  if floor <= n < len(group)]
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
            # A folder somebody made stays where it is, in a group or not, and
            # so says nothing about whether the rest are grouped now.
            currently_split = any(it.trail for it in pool if not self.stays_put(it))
            should_split = len(pool) > split if not currently_split else len(pool) >= collapse
            if not should_split:
                for it in pool:
                    plan[str(it.path)] = []
                self._leave_theirs(pool, plan)
                continue

            groups: dict[str, list[Item]] = {}
            for it in pool:
                label = group_label(self.os, it.domain, labels)
                groups.setdefault(label, []).append(it)

            # Too many thin groups? Keep only the biggest. The rest stay loose:
            # they went into General, a name that says nothing about what is
            # in it, and no group is named like that now (junk_group_name).
            ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            keep = {name for name, g in ordered[:max_cats]
                    if len(g) >= 2 and not junk_group_name(name)}
            for name, group in groups.items():
                for it in group:
                    plan[str(it.path)] = [name] if name in keep else []

            # second level, only where a single category is itself crowded
            regrouped: dict[str, list[Item]] = {}
            for it in pool:
                if plan[str(it.path)]:
                    regrouped.setdefault(plan[str(it.path)][0], []).append(it)
            for cat, group in regrouped.items():
                nested_now = any(len(it.trail) > 1 for it in group)
                crowded = len(group) > split if not nested_now else len(group) >= collapse
                if not crowded:
                    continue
                mapping = self._cluster_key(group, max_cats, cat)
                # The groups _cluster_key no longer makes: one named after its
                # parent, or after a tag every one of them has. One an earlier
                # release made stays exactly where it is, with all that is in
                # it: what a released engine filed is never moved (decided
                # 2026-09-30). Only new ones aren't made.
                everyone = set.intersection(*(set(i.tags[:5]) for i in group))
                refused = {name_key(cat, True)} | {name_key(titleize(t), True) for t in everyone}
                for it in group:
                    sub = mapping.get(str(it.path))
                    if len(it.trail) == 2 and it.trail[0] == cat \
                            and name_key(it.trail[1], True) in refused:
                        plan[str(it.path)] = list(it.trail)
                    elif sub:
                        plan[str(it.path)] = [cat, sub]
            self._leave_theirs(pool, plan)
        return plan

    def stays_put(self, it: Item) -> bool:
        """Is this a folder somebody made and named, which sort never moves
        into a subject group or out of one?

        Once Notes passed 12 things, `mkdir Notes/Recipes` with two notes in
        it was moved to Notes/Food/Recipes (stranger test, 2026-09-30). A
        folder they named stays where they put it, however big the folder
        gets (decided 2026-10-01). The loose notes and work ./os filed
        itself are grouped by `./os sort --regroup`, when somebody asks.

        Sort never makes a folder in Notes: every one there came from
        somebody, made by hand or handed to ./os save. In Work, where sort
        makes a folder for every piece of work, it is one made by hand: read
        through a note in it, with a card beside it, or saying `made: by
        hand`, which sort writes when it takes one in where it lies. A
        program's document is a file to them, and is grouped like one."""
        if not it.is_dir or document_bundle(it.path) \
                or it.kind not in ("project", "note", "asset"):
            return False
        if self.os.buckets().get(it.bucket, {}).get("role") == "note":
            return True
        card = it.path.with_name(it.path.name + ".card.md")
        return it.by_hand or Scanner.borrows_page(it.path, it.spine) or it.spine == card

    def _leave_theirs(self, pool: list[Item], plan: dict[str, list[str]]) -> None:
        """Plan every folder somebody made to stay exactly where it is."""
        for it in pool:
            if self.stays_put(it):
                plan[str(it.path)] = list(it.trail)

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
            # A group's name already on something of theirs, like the Money
            # folder they made in Notes: sort stopped on moving it into
            # itself, having marked it as one of its own groups, and the next
            # sort filled it with their other notes (review, 2026-09-30).
            # What would go in that group stays where it is.
            taken = False
            node = self.os.root / it.bucket
            for part in want:
                node = node / part
                if (node.exists() or node.is_symlink()) and not Scanner.is_category(node):
                    taken = True
                    break
            if taken:
                continue
            target = dest_dir / it.path.name
            moved += 1
            if self.dry:
                self.moves.append(("sort", self.os.rel(it.path), self.os.rel(target)))
                continue
            self._make_category(dest_dir, want)
            new = self.os.move_item(it.path, target)
            # Where it landed, which is not `target` when that name was taken.
            self.moves.append(("sort", self.os.rel(it.path), self.os.rel(new)))
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
                # Only a group with nothing left in it but its own mark, what
                # a computer leaves, and the card of a file that's gone.
                # Emptiness was judged by `ignored()`, and a group holding
                # only a Content folder, which ./os skips, looked empty:
                # rmtree took it and everything in it, and undo couldn't
                # bring it back (review, 2026-09-30).
                inside = list(path.iterdir())
                litter = [p for p in inside if p.is_file() and not p.is_symlink()
                          and (p.name in (CATEGORY_MARKER, ".DS_Store", "Thumbs.db", "desktop.ini")
                               or p.name.startswith("._"))]
                # A card whose file has gone is no longer litter: it may hold
                # the only copy of what they wrote about it, and went with the
                # group, unrecorded (delete audit, 2026-10-29). The group
                # stays, and ./os check names the card.
                if len(litter) < len(inside):
                    continue
                try:
                    for p in litter:
                        p.unlink()
                    path.rmdir()
                except OSError:
                    continue
                self.os.record("rmdir", self.os.rel(path))
                removed += 1
        return removed

    # -- the whole run ------------------------------------------------------

    def run(self) -> dict:
        # Read the tree first: what is already on disk decides what a new name
        # is allowed to be, so nothing can land on top of something else.
        # Sort files what was dropped in by hand, and nothing more. It used to
        # group a crowded folder by subject as well, and moved notes that
        # skills, pages and code pointed at by path (reported three times,
        # 2026-10). Grouping happens only when asked:  ./os sort --regroup.
        brought = self.bring_in_from_top()
        items = self.scanner.scan()
        filed = self.file_staged() + self.adopt(items)
        items = self.scanner.scan()
        identified = self.identify(items)
        pruned = 0 if self.dry else self.prune_categories()
        if not self.dry:
            self.os.save_state()
        return {"filed": filed, "identified": identified, "balanced": 0,
                "pruned": pruned, "brought": brought, "moves": self.moves, "notes": self.notes,
                "waiting": self.waiting,
                "skipped": [{"path": path, "why": why} for path, why in self.skipped]}

    def regroup(self) -> dict:
        """`./os sort --regroup`: split a crowded Notes or Work into subject
        groups, and fold groups back that are no longer needed. Only when
        somebody asks, and as a preview first (`dry`); every move is one
        step for ./os undo. Something not filed yet is left for sort."""
        items = [it for it in self.scanner.scan() if it.managed]
        balanced = self.balance(items)
        if not self.dry:
            self.os.save_state()
        return {"balanced": balanced, "moves": self.moves}


# ---------------------------------------------------------------------------
# the indexer — registry, human maps, dashboard
# ---------------------------------------------------------------------------

GENERATED = "<!-- written by ./os. Don't edit by hand — it gets overwritten. -->"
#: What it said until 2026-10-28.
GENERATED_BEFORE = "<!-- written by Zenith. Don't edit by hand — it gets overwritten. -->"


def md_cell(text: str, link_text: bool = False) -> str:
    """Text that will sit in a markdown table, and mean what it says.

    A title reading `Bench | results [draft]` is ordinary English and a broken
    table row: the pipe opens a column that is not there, and the brackets eat
    the link. Nobody should have to avoid punctuation to be indexed."""
    # Backslashes first, or they eat what comes after: `C:\temp\` ended in
    # one that undid the `]` closing the link, and `a\|b` became a pipe that
    # split the row (audit, 2026-10-29).
    out = one_line(text).replace("\\", "\\\\").replace("|", "\\|")
    return out.replace("[", "\\[").replace("]", "\\]") if link_text else out


#: What breaks a link target once markdown has read it. The space was always
#: here. The rest arrived with a file somebody had named across two lines: the
#: newline ended the table row halfway through the link, so that row and the one
#: after it both stopped being rows. Percent-encoding is the escape markdown
#: understands, and it leaves anything not listed — accents, kanji — readable.
#: `%` and `#` too: "Issue #12 fix" linked to a place on a page called
#: "Issue%20", and "50%" made "%%20", which isn't an escape (audit, 2026-10-28).
_LINK_ESCAPES = {"%": "%25", "#": "%23", " ": "%20", "\t": "%09", "\n": "%0A", "\r": "%0D",
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
    "plain-words.sh": "After each reply, catches stuffy words and has them said again plainly.",
    "keep-sources.sh": "Asks you first before anything in a sources/ folder or a decisions.md is rewritten.",
    "scratch-only.sh": "While helpers test in scratch copies, keeps them out of this folder.",
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
        made_file(self.os.root / "INDEX.md", "\n".join(lines).rstrip() + "\n")

    # -- toolkit catalog ----------------------------------------------------

    def write_catalog(self, items: list[Item]) -> None:
        skills = [i for i in items if i.kind == "skill"]
        agents = [i for i in items if i.kind == "agent"]
        # A hook's own test is not a hook: keep-sources-test.sh was listed as
        # one that "runs on its own" (audit, 2026-10-28).
        hooks = [i for i in items if i.kind == "hook" and not i.path.stem.endswith("-test")]
        # It opened "Extras for Claude Code", which told any other AI reading
        # it that the skills weren't for it. AGENTS.md says they are.
        lines = [
            GENERATED, "# What this folder can do", "",
            "Skills any AI here can use. In Claude Code, type a `/name` **in the chat** "
            "(not the terminal) to run one. Helpers get sent off on their own when a job suits them. "
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
        made_file(self.os.root / ".claude" / "CATALOG.md", "\n".join(lines) + "\n")


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

    #: Names ./os passes over wherever a thing would sit, so a file or folder of
    #: theirs called that is never filed or found. Work/Content is the one that
    #: is meant; CLAUDE.md is read by Claude as its own rules, and left alone.
    #: So is a README.md: it says what the folder it's in is for, like the
    #: ones the old layout kept in notes/ and work/. Renamed so sort would
    #: file it, "# My notes" became a piece of work on the go, and a folder
    #: that was fine before an update was said to need fixing (review,
    #: 2026-09-30).
    PASSED_OVER = {MEDIA_FOLDER}
    #: What `_out_of_reach` says. Those whose fix is ./os check --fix count
    #: on the front screen and in the brief as things that need fixing.
    OUT_OF_REACH = ("left-at-top", "passed-over", "work-inside-work", "too-far-in",
                    "card-left-behind", "archived-by-hand")

    @staticmethod
    def _filed_work(path: Path, folder: bool) -> str:
        """The title of the piece of work this is, filed by ./os, or "".

        Ours says `type: work` (or a word older folders used) and has every
        line our header has: a status, a subject, the day it was made and the
        day it last changed. A status and a date were taken as enough, and an
        Obsidian vault's Areas/Health.md, `type: area` / `status: active`,
        was called work hidden in a folder, and moved out into Work by
        check --fix (review, 2026-09-30)."""
        spine: Path | None = path
        if folder:
            spine = next((path / n for n in ("README.md", "index.md") if (path / n).is_file()), None)
        if spine is None or spine.suffix.lower() not in TEXT_SUFFIXES:
            return ""
        head = read_text(spine, 4_000)
        if not head.lstrip("\ufeff").startswith("---"):
            return ""
        meta, _ = parse_frontmatter(head)
        kind = str(meta.get("type") or "").strip().lower()
        if kind not in ("work", "ongoing", "area") \
                or not all(meta.get(k) for k in ("title", "status", "domain", "created", "updated")):
            return ""
        return str(meta.get("title") or "").strip() or given_name(path.name if folder else path.stem)

    def _free_beside(self, folder: Path, name: str, suffix: str = "") -> Path:
        """`name` in `folder`, or the name Finder would give it when that is taken."""
        n, cand = 2, folder / f"{name}{suffix}"
        while cand.exists() or cand.is_symlink() or ignored(cand):
            cand, n = folder / f"{name} {n}{suffix}", n + 1
        return cand

    def _out_of_reach(self, items: list[Item], fix: bool) -> list[str]:
        """Name anything of theirs that neither ./os find nor the list reaches.

        One rule for a run of holes that were each found on their own: a
        folder of PDFs no search found, a piece of work moved into another's
        folder that dropped off the list, a folder dragged in beside Work and
        Notes, a Content folder in Notes, a card left behind when its PDF was
        dragged into a folder, and all the while this said all good (review,
        2026-09-30). It walks Work and Notes as search does, never into
        Work/Content, a code project or a program's document, and says nothing
        about a file in a thing's own folder that search reads or matches by
        name, but for a hint when a Notes folder holds more notes than search
        reads the words of. Each finding says where, and what to do."""
        root, repaired = self.os.root, []
        rel = self.os.rel
        live = {name: spec for name, spec in self.os.buckets().items()
                if spec.get("role") in ("project", "note")}
        work = self.os.bucket_for_role("project")
        # Names they told ./os to leave alone, in config's `ignore`. Content is
        # listed there as shipped, meaning Work/Content, and is not one of them.
        chosen = {str(n).strip() for n in (self.os.config.get("ignore") or [])
                  if isinstance(n, str)} - {MEDIA_FOLDER}

        if self.os.state_unreadable:
            self.flag("error", "state-unreadable", self.os.state_unreadable, ".os/state.json",
                      "fix that, then run ./os again — nothing that changes the folder runs until then")

        # Left at the top, beside the buckets: no command looks there but sort.
        for path in loose_at_top(root, self.os.buckets()):
            self.flag("warn", "left-at-top",
                      f"'{path.name}' is at the top of this folder, where ./os find "
                      "doesn't look and nothing lists it", path.name, "./os sort")

        # A shortcut to something that's gone: nothing said so (audit,
        # 2026-10-29).
        for it in items:
            if it.path.is_symlink() and not it.path.exists():
                self.flag("hint", "broken-shortcut",
                          f"'{it.title}' is a shortcut to something that isn't there any more",
                          self.os.rel(it.path), f"put back what it points at, or  ./os close {handle(it)}")
        # Dropped in by hand and not filed: ./os said so on its first screen
        # while check said all good (audit, 2026-10-29).
        for it in items:
            if Sorter.unmanaged(it):
                self.flag("warn", "not-filed",
                          f"'{it.title}' isn't filed: it was put here by hand, or the "
                          "header at its top is gone",
                          self.os.rel(it.path), "./os sort")
        # An empty folder on the list: a close the disk stopped partway left
        # one, and check said all good (audit, 2026-10-29).
        for it in items:
            if it.kind in ("project", "note") and it.path.is_dir() and not it.path.is_symlink() \
                    and not any(p.is_file() and p.name not in (".DS_Store", "Thumbs.db", "desktop.ini")
                                and not p.name.startswith("._") for p in it.path.rglob("*")):
                self.flag("hint", "empty-folder",
                          f"'{it.title}' is an empty folder, with nothing in it to keep",
                          self.os.rel(it.path), f"put something in it, or  ./os close {handle(it)}")

        # A name ./os passes over, where a thing of theirs would be; and a
        # card whose file has gone from beside it.
        renames: list[tuple[Path, Path]] = []
        lone: list[Path] = []
        todo = [root / name for name in live]
        while todo:
            node = todo.pop()
            try:
                children = sorted(node.iterdir())
            except OSError:
                continue
            for child in children:
                if Scanner.is_category(child):
                    todo.append(child)
                elif child.name.endswith(".card.md") and not child.name.startswith(".") \
                        and child.is_file():
                    thing = child.with_name(child.name[:-len(".card.md")])
                    if not (thing.exists() or thing.is_symlink()):
                        lone.append(child)
                elif child.name in self.PASSED_OVER and child.name not in chosen \
                        and not child.is_symlink() and not holds_nothing(child) \
                        and not (child.name == MEDIA_FOLDER and node == root / work):
                    # Called after where it is, `Notes Content`, since a bare
                    # `Content 2` would say nothing about where it came from.
                    dest = self._free_beside(node, f"{node.name} {Path(child.name).stem}",
                                             child.suffix if child.is_file() else "")
                    if fix:
                        renames.append((child, dest))
                    else:
                        self.flag("warn", "passed-over",
                                  f"{rel(child)} is never filed or found: ./os passes "
                                  f"over anything called {child.name} there",
                                  rel(child),
                                  f"./os check --fix   calls it {dest.name}, then ./os sort files it")

        # Inside each thing's own folder: work filed on its own and hidden in
        # another's folder, and anything further in than search goes.
        finder = Finder(self.os, self.scanner)
        finder._items_at = {str(it.path) for it in items}
        for it in items:
            if it.bucket not in live or it.kind not in ("project", "note", "asset") \
                    or not it.is_dir or it.path.is_symlink():
                continue
            got = finder.contents(it, all_of_it=True)
            lone += got.lone
            inside: list[Path] = []
            # Only a piece of work in Work is one that work can hide in (settled
            # 2026-09-30). One never filed is waiting for sort, and ./os says
            # so already; once sort has filed it, this looks inside it too.
            hides = it.kind == "project" and it.bucket == work and it.managed
            for path, _words, folder in got.named if hides else ():
                if any(up in path.parents for up in inside):
                    continue
                title = self._filed_work(path, folder)
                if not title:
                    continue
                # Work/Wedding, with a README of ours, is one piece of work,
                # and `Book the Venue` moved into it was part of it: gone from
                # ./os and from show, while sort said nothing was waiting.
                # Said, not counted as broken, and never moved by --fix: a
                # piece of work's own folder is theirs to arrange (AGENTS.md),
                # and work kept inside another under the released ./os was
                # called broken after an update, and moved out (review,
                # 2026-09-30). The command puts it back on the list.
                inside.append(path)
                out = self._free_beside(root / work, path.stem if path.is_file() else path.name,
                                        path.suffix if path.is_file() else "")
                self.flag("hint", "work-inside-work",
                          f"'{title}' is inside {it.title}, so it isn't on your list on "
                          "its own and ./os show can't reach it",
                          rel(path), f"mv -n {shell_word(rel(path))} {shell_word(rel(out))}")
            unseen = [p for p in got.unseen if not any(up in p.parents for up in inside)]
            if unseen:
                one = len(unseen) == 1 and not got.more
                many = f"more than {len(unseen):,}" if got.more else f"{len(unseen):,}"
                if got.why == "deep":
                    # Up into the thing's own folder, under a name nothing there
                    # has: a plain mv overwrote the IMG_0001.JPG already at the
                    # top, for good, with the one from thirteen folders down.
                    # The command alone, with nothing after it: pasted whole,
                    # words after it would be taken for more files to move.
                    level, why = "warn", f"more than {Finder.NAMES_DEPTH} folders down"
                    up = self._free_beside(it.path, unseen[0].stem, unseen[0].suffix)
                    cure = f"mv -n {shell_word(rel(unseen[0]))} {shell_word(rel(up))}"
                else:
                    # Only footage and exports run to tens of thousands of
                    # files, and those belong in Work/Content, where ./os leaves
                    # them alone; Phone Export itself is found meanwhile, so
                    # this is said, not counted. `mv ... Work/Content/` renamed
                    # the folder to Work/Content when there was none yet, and
                    # left its card behind.
                    level, why = "hint", f"past the first {Finder.NAMES_LOOKED:,} names in it"
                    shelf = root / work / MEDIA_FOLDER
                    if it.kind == "project":
                        # Never a piece of work itself: moved into Work/Content
                        # it was off the list, and its next action, log and
                        # notes went with it (review, 2026-09-30). The folder
                        # in it that holds most of those files goes instead;
                        # the names at its top are always matched, so the
                        # files not seen are in a folder inside it.
                        count: dict[Path, int] = {}
                        for p in unseen:
                            top = it.path / p.relative_to(it.path).parts[0]
                            count[top] = count.get(top, 0) + 1
                        big = max(count, key=count.__getitem__)
                        to = self._free_beside(shelf / it.path.name, big.name)
                        cure = (f"mkdir -p {shell_word(rel(to.parent))} && mv -n "
                                f"{shell_word(rel(big))} {shell_word(rel(to))}")
                        why += (f"; a folder this big, like {big.name}, belongs in "
                                f"{work}/{MEDIA_FOLDER}, which ./os leaves alone")
                    else:
                        to = self._free_beside(shelf, it.path.name)
                        card = it.path.with_name(it.path.name + ".card.md")
                        cure = (f"mkdir -p {shell_word(rel(shelf))} && mv -n {shell_word(rel(it.path))} "
                                f"{shell_word(rel(to))}"
                                + (f" && mv -n {shell_word(rel(card))} {shell_word(rel(to) + '.card.md')}"
                                   if card.exists() else ""))
                        why += f"; a folder this big belongs in {work}/{MEDIA_FOLDER}, which ./os leaves alone"
                self.flag(level, "too-far-in",
                          f"{many} file{'' if one else 's'} in {it.title} "
                          f"{'is' if one else 'are'} too far in for ./os find to see ({why}), "
                          f"like {rel(unseen[0])}",
                          rel(it.path), cure)
            # A folder of notes sort filed as one thing: the words of only
            # the first notes in it are read, and `spice45` was "corrected"
            # to the spice35 note while check said nothing. Said, not counted
            # as broken: it was filed like that, and nothing here can change
            # it without moving their notes. Each is still found by its name.
            if live[it.bucket].get("role") == "note":
                read = {str(where) for where, _text in finder._inside(it)}
                words = finder.contents(it)
                past = [q for shelves in words.shelves for shelf in shelves for q in shelf
                        if str(q.parent if q.parent.suffix.lower() == ".rtfd" else q) not in read
                        and not q.name.endswith(".card.md") and _size(q) > 0]
                if past and len(read) >= Finder.INSIDE_FILES:
                    self.flag("hint", "too-far-in",
                              f"{len(past):,} note{'' if len(past) == 1 else 's'} in "
                              f"{it.title} {'is' if len(past) == 1 else 'are'} past the "
                              f"{Finder.INSIDE_FILES} whose words ./os find reads, "
                              f"like {rel(past[0])}",
                              rel(it.path),
                              "./os find still finds each one by its name")

        # A card left behind is the only place a PDF's description is, and a
        # lone card is read by nothing. Put back beside its file when there
        # is just one file of that name in Work and Notes with no card yet.
        cards: list[tuple[Path, Path]] = []
        if lone:
            where: dict[str, list[Path]] = {}
            for it in items:
                if it.bucket in live:
                    where.setdefault(it.path.name, []).append(it.path)
                    if it.is_dir:
                        got = finder.contents(it, all_of_it=True)
                        for path in [q for q, _w, _f in got.named] + got.unseen:
                            where.setdefault(path.name, []).append(path)
            for card in lone:
                name = card.name[:-len(".card.md")]
                there = [q for q in where.get(name, []) if q != card.with_name(name)]
                bare = [q for q in there if not q.with_name(q.name + ".card.md").exists()]
                # Nothing in a sources/ folder is ever changed, so a card
                # there stays where it is, and nothing says it could go:
                # --fix moved one out of it and left the folder empty
                # (review, 2026-09-30). A copy can go beside its file.
                if any(part.casefold() == "sources" for part in card.relative_to(root).parts[:-1]):
                    if len(there) == 1 and bare:
                        self.flag("hint", "card-left-behind",
                                  f"the card for {name} is in a sources folder, which ./os "
                                  f"never changes, and {name} is in {rel(bare[0].parent)}",
                                  rel(card), f"cp -n {shell_word(rel(card))} "
                                             f"{shell_word(rel(bare[0]) + '.card.md')}")
                    continue
                # Nor is one ever put into a sources/ folder: --fix moved a
                # card in beside a file kept there (check and learn sweep,
                # 2026-10-29).
                into_sources = bool(there) and any(
                    part.casefold() == "sources" for part in bare[0].relative_to(root).parts[:-1]) if bare else False
                if len(there) == 1 and bare and not into_sources:
                    if fix:
                        cards.append((card, bare[0].with_name(name + ".card.md")))
                        continue
                    self.flag("warn", "card-left-behind",
                              f"what the card for {name} says isn't found: {name} moved "
                              f"to {rel(bare[0].parent)}, and its card stayed behind",
                              rel(card), "./os check --fix   puts the card back beside it")
                elif there:
                    self.flag("hint", "card-left-behind",
                              f"the card for {name} stayed behind when it moved, and "
                              f"{'it has a new one' if len(there) == 1 else 'there are ' + str(len(there)) + ' files called that'}"
                              f": {', '.join(rel(q) for q in there[:2])}",
                              rel(card), f"move what it says into the card beside the {name} you mean")
                else:
                    self.flag("hint", "card-left-behind",
                              f"the card for {name} is still here, and {name} isn't",
                              rel(card), f"put {name} back beside it, or, if it's gone for good, "
                                         "the card can go too")

        # Moved the way sort moves things, so ./os undo puts them back. The
        # run holding the lock (cmd_doctor, setup) is the one doing this. One
        # the disk won't let go of is said, the same as without --fix.
        for child, dest in renames:
            try:
                self.os.move_item(child, dest)
            except OSError as exc:
                self.flag("warn", "passed-over", f"{rel(child)} is never filed or "
                          f"found, and couldn't be renamed: {exc}", rel(child))
                continue
            repaired.append(f"{rel(child)} is called {dest.name} now, so "
                            "./os sort can file it — ./os undo puts it back")
        for card, dest in cards:
            try:
                self.os.move(card, dest)
            except OSError as exc:
                self.flag("warn", "card-left-behind", f"the card for {dest.name[:-8]} "
                          f"stayed behind, and couldn't be moved: {exc}", rel(card))
                continue
            repaired.append(f"the card for {dest.name[:-8]} is back beside it, in "
                            f"{rel(dest.parent)} — ./os undo puts it back")
        if repaired:
            self.os.commit("check")
        return repaired

    def _archived_by_hand(self) -> None:
        """Name anything put in Archive by hand, where nothing can reach it.

        ./os close files a thing as Archive/<year>/<where it was>/<name>, and
        only that shape is read. A project dragged straight into Archive was
        gone from the list, from ./os show and from ./os find, and check said
        all good (stranger test, 2026-09-30). Each one is said, with the
        command that puts it back and closes it properly. Nothing is moved:
        which folder it came from is their call."""
        shelf = next((n for n, spec in self.os.buckets().items()
                      if spec.get("role") == "archive"), "")
        base = self.os.root / shelf
        if not shelf or not base.is_dir():
            return
        buckets = self.os.buckets()
        work, notes = self.os.bucket_for_role("project"), self.os.bucket_for_role("note")
        rel = self.os.rel

        def odd(path: Path) -> bool:
            if ignored(path) or path.name.startswith(".") or holds_nothing(path):
                return False
            # A card goes with its file, and is in the command for it.
            if path.name.endswith(".card.md") and path.name != ".card.md":
                thing = path.with_name(path.name[:-len(".card.md")])
                return not (thing.exists() or thing.is_symlink())
            return True

        found: list[Path] = []
        try:
            tops = sorted(base.iterdir())
        except OSError:
            return
        for top in tops:
            if not odd(top):
                continue
            if not (top.is_dir() and not top.is_symlink() and re.fullmatch(r"\d{4}", top.name)):
                found.append(top)
                continue
            try:
                inside = sorted(top.iterdir())
            except OSError:
                continue
            for origin in inside:
                if not odd(origin):
                    continue
                # Loose in a year's folder, or a piece of work put there
                # whole: its own README read as the folder a thing came from.
                if not origin.is_dir() or (origin.name not in buckets and any(
                        (origin / n).is_file() for n in ("README.md", "index.md"))):
                    found.append(origin)

        for path in found:
            folder = path.is_dir()
            head = Scanner.spine_of(path)
            meta = parse_frontmatter(read_text(head, 4_000))[0] \
                if head and head.suffix.lower() in TEXT_SUFFIXES else {}
            declared = TYPE_FROM_DISK.get(str(meta.get("type") or "").strip().lower(),
                                          str(meta.get("type") or "").strip().lower())
            home = work if declared == "project" or (folder and not declared) else notes
            dest = self._free_beside(self.os.root / home,
                                     path.name if folder else path.stem,
                                     "" if folder else path.suffix)
            name = dest.name if folder else dest.stem
            card = path.with_name(path.name + ".card.md")
            steps = [f"mv -n {shell_word(rel(path))} {shell_word(rel(dest))}"]
            if card.exists():
                steps.append(f"mv -n {shell_word(rel(card))} {shell_word(rel(dest) + '.card.md')}")
            if not (meta.get("type") or meta.get("title") or card.exists()):
                steps.append("./os sort")
            steps.append(f"./os close {shell_word(name)}")
            self.flag("warn", "archived-by-hand",
                      f"'{path.name}' was moved into {shelf} by hand, where the list, "
                      "./os show and ./os find can't reach it. This moves it back and "
                      f"closes it, which files it in {shelf} the way they can",
                      rel(path), " && ".join(steps))

    def _taken_back(self, refused: set, fix: bool) -> list[str]:
        """What an older ./os undo put back in .os/cache/incoming.

        Undoing a save used to put the file it had filed back in there, where
        it stayed: wanted gone, it was still on disk, `./os` counted it as
        saved and never filed, and sort spoke of it on every run (reports,
        2026-10-22). Undo takes a save's copy away now. Those left from
        before are said here, in one finding that names each, and --fix
        throws them away, saying which. Only what sits in that folder itself:
        a state file edited by hand must never point this at a note. Anything
        else in there is a save that never finished, and is left alone."""
        stage = (self.os.dot / "cache" / STAGING).resolve()
        found = []
        for rel in sorted(refused):
            path = self.os.root / rel
            if path.parent.resolve() != stage or ignored(path):
                continue
            words = (f"a folder of {sum(1 for p in path.rglob('*') if p.is_file())} files"
                     if path.is_dir() and not path.is_symlink() else staged_words(path))
            found.append((rel, words))
        if not found:
            return []
        if not fix:
            named = "; ".join(f"{rel} ('{words}')" for rel, words in found[:12]) \
                + (f"; and {len(found) - 12} more" if len(found) > 12 else "")
            one = len(found) == 1
            self.flag("hint", "taken-back-capture",
                      f"{len(found)} thing{'' if one else 's'} you took back with ./os undo "
                      f"{'is' if one else 'are'} still kept, not filed: {named}",
                      self.os.rel(stage), "./os check --fix  throws "
                      + ("it" if one else "them") + " away (./os undo can't bring "
                      + ("it" if one else "them") + " back)")
            return []
        done = []
        for rel, words in found:
            path = self.os.root / rel
            try:
                shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
            except OSError:
                continue
            done.append(f"threw away {rel} ('{words}'), which you took back with ./os undo")
        self.os.taken_back()        # crosses off what is no longer there
        self.os.save_state()
        return done

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
        for claude_md, pointer, reader in ((root / "CLAUDE.md", "@AGENTS.md", "Claude Code"),
                                           (root / "GEMINI.md", "@AGENTS.md", "Gemini CLI"),
                                           (root / ".claude" / "CLAUDE.md", "@../AGENTS.md", "Claude Code")):
            if not claude_md.exists() or "AGENTS.md" in read_text(claude_md, 4_000):
                continue
            # A link to AGENTS.md is AGENTS.md: the same rules, word for word.
            # It was called out of step, and the fix said to put the pointer on
            # its first line, which wrote it through the link into AGENTS.md
            # itself. An update leaves a link alone, and so does this. Made
            # with `ln` and no -s it is the same file too, but not a symlink,
            # so samefile, not resolve(): that one was still told to write.
            try:
                if rules.exists() and claude_md.samefile(rules):
                    continue
                linked = claude_md.is_symlink() or claude_md.stat().st_nlink > 1
            except OSError:
                linked = claude_md.is_symlink()
            if linked:
                fix = (f"it's a link, so don't write into it: point it at AGENTS.md, or put a "
                       f"plain file there whose first line is `{pointer}`")
            else:
                fix = f"put `{pointer}` on its first line"
            self.flag("warn", "rules-drift",
                      f"{self.os.rel(claude_md)} doesn't point at AGENTS.md, so "
                      f"{reader} and every other AI are reading different rules",
                      self.os.rel(claude_md), fix)
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
                # The line, and the way back (audit, 2026-10-29).
                self.flag("error", "bad-settings",
                          f".claude/settings.json has a typo on line {getattr(exc, 'lineno', '?')}, so "
                          "Claude skips this folder's hooks and permissions",
                          ".claude/settings.json",
                          "git restore .claude/settings.json   (if you keep checkpoints), "
                          "or fix that line: usually a missing comma")

        # Newer versions of files they changed, set aside by an update. Said
        # once in its output and never again, they would wait there for good.
        waiting, where = _left_to_merge(self.os)
        if waiting:
            self.flag("warn", "update-to-merge",
                      f"an update left {waiting} newer file{'' if waiting == 1 else 's'} "
                      f"to merge into yours in {where}",
                      where, "fold each into the file of the same name, then  ./os update --done")

        # 2b. words that never reached a folder. Saving files in one breath, so
        # anything here is a run that died in between. Left unsaid it is
        # invisible to every command a person would think to run — `os` says
        # "nothing started yet" and `check` says "all good" over the top of it.
        refused = self.os.taken_back()
        repaired += self._taken_back(refused, fix)
        for path in staged_captures(self.os.dot):
            if self.os.rel(path) in refused:
                continue
            try:
                age = int((time.time() - path.stat().st_mtime) // 86_400)
            except OSError:
                age = 0
            words = staged_words(path)
            stale = age >= STALE_STAGE_DAYS
            when = "today" if age < 1 else f"{age} days ago"
            self.flag("error" if stale else "warn", "unfiled-capture",
                      f"'{words}' was written down {when} and never filed",
                      self.os.rel(path), "./os sort")
        # A copy a stop left half made, under the name sort never files: said,
        # or it sat there unseen (round 24).
        stage = self.os.dot / "cache" / STAGING
        for path in sorted(stage.glob("*" + PARTIAL)) if stage.is_dir() else []:
            name = path.name[:-len(PARTIAL)]
            self.flag("warn", "half-copied",
                      f"bringing '{name}' in was stopped partway, so only part of it is here, "
                      "and it wasn't filed",
                      self.os.rel(path), "save or bring it in again from where it is "
                      "(./os save, or ./os setup --bring); this half copy can go after")

        # 2c. Where ./os keeps its lists, a file of theirs by that name (any
        # capitals): it is left as it is, and the list isn't kept, so said
        # (twenty-first review, round 26).
        for made in (self.os.root / "INDEX.md", self.os.root / ".claude" / "CATALOG.md"):
            if not mine_to_write(made):
                self.flag("warn", "list-name-taken",
                          f"{self.os.rel(made)} is a file of yours (or has that name in other "
                          "capitals), so ./os leaves it alone and doesn't keep its list there",
                          self.os.rel(made), "rename yours if you want the list back")

        if items is None:
            items = self.scanner.scan()

        # 3. identity and metadata
        seen: dict[str, list[Item]] = {}
        for it in items:
            if it.kind in ("project", "note", "asset"):
                if not it.ident:
                    self.flag("warn", "no-id", f"'{it.title}' has no name on disk yet", self.os.rel(it.path), "./os sort")
                else:
                    seen.setdefault(slugify(nfc(it.ident)), []).append(it)
                # Not for one with no header at all: "set domain: at the top"
                # asked for a line in a header that wasn't there. It's said
                # as not filed, and ./os sort puts the header back (audit,
                # 2026-10-29).
                if it.spine and (not it.domain or it.domain == "unsorted") and not Sorter.unmanaged(it):
                    # `new` writes `domain: unsorted` when nothing matched, so
                    # "add a domain: line" asked for one that was already there.
                    subjects = ", ".join(self.os.taxonomy.get("domains") or {})
                    self.flag("hint", "no-domain", f"'{it.title}' has no subject set, so it can't be grouped",
                              self.os.rel(it.path),
                              f"set `domain:` at the top of the file to one of: {subjects}"
                              if subjects else "set `domain:` at the top of the file")
            if it.spine and it.spine.exists() and it.words == 0 and it.kind != "asset":
                self.flag("hint", "empty", f"'{it.title}' is empty", self.os.rel(it.path))
        # One name on two things: Pizza in Work and pizza.md in Notes. It
        # was an error for good, since the fix it gave, ./os sort, only keeps
        # names apart inside one folder, so ./os said "needs fixing" on every
        # run and --fix changed nothing (review, 2026-09-30). Nothing is
        # broken: ./os show and the rest ask which one is meant, and take
        # the folder with the name. So it's said once, as a hint.
        finder = Finder(self.os, self.scanner)
        shared = [group for group in seen.values() if len(group) > 1]
        for group in shared:
            # Only the three shown: each asks about every thing here, and all
            # of 200 copies of `Meeting notes` made ./os take three seconds.
            ways = [finder.qualified(g, group, items) for g in group[:3]]
            self.flag("hint", "same-name",
                      f"{len(group)} things are called {group[0].ident}, so a command "
                      "given that name asks which one you mean",
                      self.os.rel(group[1].path),
                      "  ·  ".join(f"./os show {w}" for w in ways))
        named_twice = {id(g) for group in shared for g in group}
        # A name with a mark a terminal trips on: `Build a Trading Bot for
        # NQ1!` could not be typed, even in quotes (see SHELL_MARKS). Only a
        # new name fixes it, and only they can pick one, so --fix leaves it.
        for it in items:
            if it.kind not in ("project", "note", "asset", "archive") \
                    or not SHELL_MARKS.search(it.path.name):
                continue
            marks = "".join(dict.fromkeys(SHELL_MARKS.findall(it.path.name)))
            better = shell_safe(it.title) or shell_safe(it.path.stem) or "a new name"
            self.flag("hint", "name-trips-terminal",
                      f"'{it.path.name}' has {' '.join(marks)} in its name, which a terminal "
                      "trips on, so commands that name it can fail",
                      self.os.rel(it.path), f'./os rename {handle(it)} "{better}"')

        # 3b. a note for a video that isn't where it said any more: moved,
        # deleted, or on a drive that isn't plugged in. Only they know which,
        # so it's said once, quietly, and nothing is changed.
        for it in items:
            if not it.lives or it.kind not in ("note", "project"):
                continue
            there = Path(it.lives).expanduser()
            try:
                found = (there if there.is_absolute() else root / there).exists()
            except OSError:
                found = False
            if not found:
                self.flag("hint", "not-there",
                          f"'{it.title}' says its file is at {it.lives}, and nothing is there "
                          "now — moved, deleted, or on a drive that isn't plugged in",
                          self.os.rel(it.spine or it.path),
                          f"moved? put where it is now on its `lives:` line:  ./os edit {handle(it)}")

        # 3c. anything of theirs that neither search nor the list can reach
        repaired += self._out_of_reach(items, fix)
        self._archived_by_hand()

        # 4. duplicates
        by_print: dict[str, list[Item]] = {}
        # Nothing in it is nothing to have twice: every empty folder and
        # broken shortcut had the same print, and two were "the same thing
        # in 2 places" (audit, 2026-10-29).
        nothing = hashlib.sha256().hexdigest()[:16]
        for it in items:
            if it.fingerprint and it.fingerprint != nothing and it.kind != "asset":
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
            # Said already, and more usefully, as one name on two things.
            if len(group) > 1 and len({g.fingerprint for g in group}) > 1 \
                    and not all(id(g) in named_twice for g in group):
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
                elif re.search(r"<[^<>\n]+>", str(meta["description"])):
                    # A blank in <angle brackets> left from a template reads to
                    # an AI as the words to listen for (2026-10-28).
                    self.flag("warn", "skill-placeholder-description",
                              f"the skill '{it.path.name}' still has a blank to fill in its description",
                              self.os.rel(skill_md), "ask them what they'd say to ask for it, and write that in")
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
                elif re.search(r"<[^<>\n]+>", str(meta["description"])):
                    self.flag("warn", "agent-placeholder-description",
                              f"the helper '{it.path.name}' still has a blank to fill in its description",
                              self.os.rel(it.path), "ask them when it should be used, and write that in")
            if it.kind == "hook" and it.path.suffix in (".sh", ".zsh", ".bash", ".py"):
                if not os.access(it.path, os.X_OK):
                    # Never through a shortcut: one to a file outside this
                    # folder had that file made runnable, which undo can't
                    # take back (check and learn sweep, 2026-10-29).
                    if fix and not it.path.is_symlink() and inside(it.path, whole=True):
                        it.path.chmod(it.path.stat().st_mode | 0o755)
                        repaired.append(f"made {it.path.name} runnable again")
                    elif it.path.is_symlink() or not inside(it.path, whole=True):
                        # --fix leaves the file it leads to alone, so it was
                        # told to run --fix again and again (thirteenth review).
                        real = os.path.realpath(it.path)
                        self.flag("warn", "hook-not-executable",
                                  f"'{it.path.name}' can't run — it leads to {real}, which needs to be executable",
                                  self.os.rel(it.path), f"chmod +x {shell_word(real)}   (it's outside ./os's reach)")
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
        # A file named like one ./os writes beside another and swaps in, but
        # not surely one: a person's own budget.2024.tmp~ was taken away
        # with no undo, and so was another chat's write still going on
        # (twentieth review, round 25). Only one in ./os's own exact shape,
        # whose run is over and that is an hour old, is taken away. An hour,
        # because nothing ./os writes takes more than seconds, and a run
        # that slept partway is still alive, so it is never taken from one.
        # The rest are said, and left.
        def left_by_os(q: Path) -> str:
            """"gone" for a file ./os was writing and surely never will
            again; "maybe" for one in its shape it can't be sure of; "" for
            anything else."""
            name = q.name
            mine = re.fullmatch(r"\.copy-(\d+)-[0-9a-f]{8}\.tmp~", name)
            beside = re.fullmatch(r"(.+)\.(\d+)\.tmp~", name)
            if mine:
                pid = int(mine.group(1))
            elif beside and (q.parent / beside.group(1)).is_file():
                pid = int(beside.group(2))      # the file it was being written for is there
            elif beside:
                return "maybe"
            else:
                return ""
            try:
                old = time.time() - q.stat().st_mtime > 3600
            except OSError:
                return ""
            return "gone" if old and not Lock._alive(pid) else "maybe"
        # Only what is junk by what it is, not by its name: a `._interview.txt`
        # of words in sources/ was deleted, and undo couldn't bring it back
        # (check and learn sweep, 2026-10-29). A `._` file is the Mac's own
        # only when it starts with that file's mark; a `.tmp~` only when it
        # is one ./os itself was writing (name.<pid>.tmp~). Never a folder,
        # never anything in a sources/ folder.
        def junk(q: Path) -> bool:
            # The part below this folder only: one kept under a Sources
            # folder of their own never had its litter cleared (thirteenth
            # review, 2026-10-29).
            try:
                below = q.relative_to(root).parts
            except ValueError:
                return False
            if q.is_symlink() or not q.is_file() or any(part.lower() == "sources" for part in below):
                return False
            if q.name in (".DS_Store", ".localized"):
                return True
            if q.name.startswith("._"):
                try:
                    with q.open("rb") as fh:
                        return fh.read(4) == b"\x00\x05\x16\x07"
                except OSError:
                    return False
            return q.name.endswith(".tmp~") and not q.name.startswith(".capture-") \
                and left_by_os(q) == "gone"
        litter = [q for q in root.rglob("*")
                  if (q.name in (".DS_Store", ".localized") or q.name.endswith(".tmp~")
                      or q.name.startswith("._")) and junk(q)]
        # A code project makes __pycache__ every run, and it's that project's
        # own business, not clutter. Anywhere else one is litter. It was left
        # alone whenever git ignored it, but every folder now has a history
        # whose .gitignore lists __pycache__/, so none was ever reported
        # again (review, 2026-09-30). A code project is a folder under this
        # one with a git, a .gitignore or a project file of its own, or what
        # one downloads for itself; or this folder, when it has a project file.
        def in_code(q: Path) -> bool:
            for up in q.parents:
                if up == root:
                    # It always has a .git and a .gitignore: ./os made them.
                    return any((root / f).exists() for f in CODE_MARKERS if f != ".git") \
                        or any((root / f).exists() for f in ("setup.py", "requirements.txt"))
                if up.name in (".venv", "venv", "node_modules") or any(
                        (up / f).exists() for f in (*CODE_MARKERS, ".gitignore")):
                    return True
            return False
        # Nor inside Work/Content or a code project: on an exFAT drive a ._
        # file holds Finder tags and more, and --fix took them from the
        # footage and the code that ./os otherwise leaves alone (delete audit,
        # 2026-10-29).
        litter = [q for q in litter if MEDIA_FOLDER not in q.relative_to(root).parts[:2]
                  and not in_code(q)]
        unsure = [q for q in root.rglob("*.tmp~")
                  if q.is_file() and not q.is_symlink() and q not in litter
                  and not q.name.startswith(".capture-") and left_by_os(q) == "maybe"
                  and MEDIA_FOLDER not in q.relative_to(root).parts[:2] and not in_code(q)
                  and not any(part.lower() == "sources" for part in q.relative_to(root).parts)
                  and STAGING not in q.relative_to(root).parts]
        # Only one that holds nothing but Python's own compiled files: one
        # with a note in it was taken away whole, with no undo (words sweep,
        # 2026-10-29).
        def only_compiled(q: Path) -> bool:
            try:
                return all(f.is_file() and not f.is_symlink() and f.suffix in (".pyc", ".pyo")
                           for f in q.rglob("*") if not f.is_dir() or f.is_symlink())
            except OSError:
                return False
        caches = [q for q in root.rglob("__pycache__") if q.is_dir() and not q.is_symlink()
                  and not in_code(q) and only_compiled(q)
                  and not any(part.lower() == "sources" for part in q.relative_to(root).parts)]
        # A git they keep themselves that ignores one is their choice, as the
        # owner settled on 2026-09-29, when a code project's check listed
        # eighteen as junk. Dropping that for everyone brought it back
        # (review, 2026-09-30). Only the .gitignore of a history ./os keeps,
        # or of a git clone of the template, lists __pycache__ for them.
        if caches and (root / ".git").exists():
            kept_by = History(self.os)
            if kept_by.where() == "theirs":
                told = kept_by._git("check-ignore", "--", *(self.os.rel(q) for q in caches))
                theirs = set(told.stdout.splitlines())
                caches = [q for q in caches if self.os.rel(q) not in theirs]
        litter += caches
        # A save's spare copy is what they typed, never litter: one left by a
        # save that stopped before claiming its name was thrown away by
        # --fix (review, 2026-10-29). Kept as a staged save for sort to file.
        stage = self.os.dot / "cache" / STAGING
        for spare in sorted(stage.glob(".capture-*.tmp~")) if stage.exists() else []:
            pid = re.sub(r"\D", "", spare.name.split(".")[1]) if "." in spare.name else ""
            try:
                age = time.time() - spare.stat().st_mtime
            except OSError:
                continue
            # A save still running; but a pid now used by another program
            # was taken for one forever (review, 2026-10-29): an hour old, it isn't.
            if pid and pid.isdigit() and Lock._alive(int(pid)) and age < 3600:
                continue
            # Never deleted, whatever it looks like: a check that cleared
            # spares "already filed" went wrong three rounds running, the last
            # time deleting "ok" because "booking" holds it (review,
            # 2026-10-29). A spare whose note did land becomes a second note:
            # two notes beats none.
            if fix:
                # Named by its words, as a save names it: called "Stopped
                # Partway", its note said nothing of what was in it (review,
                # 2026-10-29).
                first = next((l for l in parse_frontmatter(read_text(spare))[1].splitlines() if l.strip()), "")
                slug = slugify(re.sub(r"\s+", " ", first).strip()[:60]) or "stopped-partway"
                kept = unique_path(stage / f"{_dt.datetime.now():%Y%m%d-%H%M%S}-{slug}.md")
                try:
                    spare.rename(kept)
                    repaired.append(f"kept the words of a save that stopped partway: {self.os.rel(kept)} "
                                    "— ./os sort files them")
                except OSError:
                    pass
            else:
                self.flag("warn", "save-stopped", f"a save stopped partway, and its words are in "
                          f"{self.os.rel(spare)}", self.os.rel(spare), "./os check --fix keeps them")
        for q in unsure:
            self.flag("hint", "maybe-leftover",
                      f"{self.os.rel(q)} looks like something ./os was writing, but it may "
                      "still be being written, or be yours, so it's left as it is",
                      self.os.rel(q), "if it's yours, rename it; if not, it can go once nothing is running")
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

        # 6b. A history that has lost track of its last save takes no more
        # checkpoints until it's put back. Only the brief and a checkpoint
        # said so: `./os` said nothing, and this said "all good" (review,
        # 2026-09-30). Put back, it points at the save git's own record says
        # it last had; nothing else in it changes. The same when a crash
        # emptied some of git's files of what it saved.
        history = History(self.os)
        lost = history.lost_track()
        if lost.get("first") and fix:
            got = history.start_again()
            repaired.append("finished this folder's first history save, which a crash had "
                            "cut off" if got["result"] == "started" else
                            "cleared what a crash left in this folder's history before its "
                            f"first save was done, but couldn't make it again ({got.get('why')})"
                            " — ./os checkpoint tries once more")
        elif lost.get("first"):
            self.flag("error", "history-lost",
                      "a crash during this folder's first save left its history empty, so "
                      "no checkpoint can go in it", ".git", "./os check --fix")
        elif lost.get("last") and fix:
            moved = history.put_back(lost)
            repaired.append("put this folder's history back on its last save"
                            + (" that can still be read" if lost.get("emptied") else "")
                            if moved else "cleared what a crash left empty in this folder's history")
        elif lost.get("emptied"):
            self.flag("error", "history-lost",
                      "a crash while saving left some of this folder's history empty, so "
                      "no checkpoint can go in it", ".git", "./os check --fix")
        elif lost:
            self.flag("error", "history-lost",
                      "this folder's history has lost track of its last save, so no "
                      "checkpoint can go in it", ".git",
                      "./os check --fix" if lost.get("last") else
                      "git fsck --lost-found   finds the saves it still has")

        # 7. hygiene and decay
        stale = int(self.os.thresholds.get("stale_project_days", 30))
        dormant = int(self.os.thresholds.get("dormant_project_days", 75))
        for it in items:
            if it.kind != "project" or it.status in CLOSED_STATUSES:
                continue
            # A status ./os can't read, typed by hand, took that work off every
            # list: neither on the go nor kept level, and nothing said so. The
            # words people type for a pause are read as holding (STATUS_ALIASES);
            # any other is named here. The header is theirs and only they know
            # which of the two it is, so --fix leaves it as they wrote it.
            if it.status not in LIVE_STATUSES:
                self.flag("warn", "status-unread",
                          f"'{it.title}' says `status: {it.status}`, a word ./os can't read, "
                          "so it's on no list: not on the go, and not kept level",
                          self.os.rel(it.spine or it.path),
                          f"./os hold {handle(it)}   or   ./os push {handle(it)}  "
                          "if it has a next action")
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
                          f"{bucket}/{'/'.join(trail)}", "split it yourself, or raise category_max_items in .os/config.json")

        # 9. broken internal links
        for it in items:
            if not it.spine or not it.spine.exists() or it.spine.suffix.lower() not in TEXT_SUFFIXES:
                continue
            # a link shown inside a code fence or `backticks` is an example, not a link
            text = read_text(it.spine, 80_000)
            # Nor one in a dated Log or Decisions entry: it says where a thing
            # was that day, nothing may change it, and check said so after
            # every move with no way to quiet it (tenth review, 2026-10-29).
            kept = dated_entries(text)
            if any(kept):
                text = "".join(line for line, k in zip(text.splitlines(keepends=True), kept) if not k)
            text = re.sub(r"^ {0,3}(```|~~~).*?^ {0,3}\1", "", text, flags=re.S | re.M)
            text = MD_CODE_SPAN.sub(" ", text)
            # Read the way rename reads them (link_file): `<a note.md>`, one
            # with a title after it and one with %27 in it are links too.
            for m in MD_LINK.finditer(text):
                plain = link_file(m.group(2))
                if not plain or not plain.endswith(".md"):
                    continue
                target = (it.spine.parent / plain).resolve()
                if not target.exists():
                    self.flag("hint", "broken-link", f"'{it.title}' links to something that isn't there: {plain}",
                              self.os.rel(it.spine))
                    break

        # 10. a page grown out of shape. A README ended up with two
        # `## Decisions` after two helpers added to it: ./os decide wrote under
        # the first, ./os show read the second, and this said nothing. Counted
        # under each `# ` title, so a note of two recipes, each with its own
        # `## Method`, is not one heading written twice. And the part above a
        # `## Log`, read first every time anyone picks the thing up, grown past
        # what anyone reads. The Log itself is meant to grow, and is never
        # counted. Both ends of the page are read, as search reads them: a
        # second heading added to a long README lands at the bottom.
        cap = int(self.os.thresholds.get("above_log_max_lines", 150))
        for it in items:
            if it.kind not in ("project", "note") or not it.spine or not it.spine.exists() \
                    or it.spine.suffix.lower() not in TEXT_SUFFIXES:
                continue
            _meta, body = parse_frontmatter(read_ends(it.spine))
            under: set[str] = set()
            twice: dict[str, str] = {}
            above, fence, log = 0, "", False
            for line in body.split("\n"):
                mark = re.match(r" {0,3}(```|~~~)", line)
                head = SECTION_RE.match(line)
                if fence:
                    # A heading shown inside a code block is an example.
                    if mark and mark.group(1) == fence:
                        fence = ""
                elif mark:
                    fence = mark.group(1)
                elif re.match(r"#\s", line):
                    under = set()
                elif head:
                    word = head.group(1).rstrip("#").strip()
                    log = log or word.casefold() == "log"
                    if word.casefold() in under:
                        twice.setdefault(word.casefold(), word)
                    under.add(word.casefold())
                if not log and line.strip():
                    above += 1
            if twice:
                said = " and ".join(f"## {w}" for w in list(twice.values())[:3])
                self.flag("hint", "heading-twice",
                          f"'{it.title}' has {said} more than once, and ./os only reads one "
                          f"of {'them' if len(twice) == 1 else 'each'}",
                          self.os.rel(it.spine),
                          "move what's under the second one up under the first, then take "
                          "out the second heading")
            if log and above > cap:
                self.flag("hint", "long-above-log",
                          f"'{it.title}' has {above} lines above its ## Log, past the {cap} "
                          "that stay quick to read, and that part is read first every time "
                          "anyone picks it up",
                          self.os.rel(it.spine),
                          "move the long parts into a file of their own beside it, with a line "
                          "saying where, or raise above_log_max_lines in .os/config.json")

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


#: What may follow a word's root for it to be the same word: its last letter
#: again (run, running), an e (bake, baking), then an ending, then the end of
#: the word. Nothing else: the root of "winner" is "win", and matched as the
#: start of any word it found the note about winter (review, 2026-09-30).
_ROOT_TAIL = r"(?:{double})?{e}(?:" + "|".join(_SUFFIXES + ("y",)) + r")?(?![a-z0-9])"


def root_pattern(root: str, doubled: bool = False) -> "re.Pattern":
    """A root found as a whole word with one of its endings: "garden" finds
    garden, gardens, gardener and gardening, and never gardenia. A root that
    ends in y also takes the endings that swap it, so "berry" finds berries.
    A root whose last letter was doubled before its ending, win in winner,
    never ends in e: winner is not wine."""
    tail = _ROOT_TAIL.format(double=re.escape(root[-1]), e="" if doubled else "e?")
    if root.endswith("y") and len(root) > 3:
        body = re.escape(root[:-1]) + r"(?:y" + tail + r"|i(?:es|ed|er|est|ly)(?![a-z0-9]))"
    else:
        body = re.escape(root) + tail
    return re.compile(r"(?<![a-z0-9])" + body)


class Term:
    """One search word: how it was typed, and its root.

    The word as typed matches anywhere, the way it always has. The root only
    matches at the start of a word — otherwise "biling" stems to "bil" and
    starts finding every note about anything mobile — and only with an
    ending after it, so "winner" (root "win") never finds "winter"."""

    __slots__ = ("word", "root", "_rx")

    def __init__(self, word: str):
        self.word = word
        root = stem(word)
        self.root = root if root != word else ""
        # "winner" → "win": the n was doubled, so the word is win, not wine.
        doubled = len(word) > len(root) and word.startswith(root) and word[len(root)] == root[-1]
        self._rx = root_pattern(root, doubled) if self.root else None

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


class _Unaccent(dict):
    """For str.translate: a letter with an accent → the same letter without
    it, one character for one, so a place found in the plain text is the same
    place in the text as written. Worked out once for each letter met."""

    def __missing__(self, code: int) -> int:
        bare = unicodedata.normalize("NFD", chr(code))
        plain = ord(bare[0]) if len(bare) > 1 and all(unicodedata.combining(c) for c in bare[1:]) else code
        self[code] = plain
        return plain


_UNACCENT = _Unaccent()


def unaccented(text: str) -> str:
    """`Diátaxis` as `Diataxis`, `café` as `cafe`: search reads both ways
    the same. Typed without the accent, a word was found only because the
    near-word guess happened to land on part of it, and that guess no longer
    searches on its own."""
    return text if text.isascii() else text.translate(_UNACCENT)


def shell_word(text: str) -> str:
    """A name or path as it is typed into a shell: bare when it can be,
    in double quotes when that is enough, and fully quoted otherwise."""
    if re.fullmatch(r"[\w./-]+", text):
        return text
    return f'"{text}"' if not re.search(r'["$`\\!]', text) else shlex.quote(text)


def name_words(name: str) -> str:
    """A file's name as the words it is made of, to match a search against.

    `Self_assessment-2024` is found by "self assessment", and
    `MathsNotesWeek1` by "maths notes"."""
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", nfc(name))
    return re.sub(r"[\W_]+", " ", words, flags=re.UNICODE).strip()


class Contents:
    """What is in a thing's own folder, as far as search goes: Finder.contents."""

    __slots__ = ("shelves", "named", "unseen", "lone", "more", "why")

    def __init__(self):
        self.shelves: list = []      # each level: each folder's text files, words read
        self.named: list = []        # (path, its name as words, a folder?): matched by name
        self.unseen: list = []       # files further in than names are matched
        self.lone: list = []         # cards of ours whose file is not beside them
        self.more = False            # and more of those than were counted
        self.why = ""                # "deep" or "many": what stopped the names


class Finder:
    """Ranked full-text search over the whole OS. No index server, no daemon."""

    #: how much of a term's score a root-only match earns
    STEM_WEIGHT = 0.6
    #: What is read in a thing's own folder besides its page: this many files
    #: at most, the start of each, out of this many names looked at, and this
    #: much in all for one search. Enough for the notes a person keeps in a
    #: piece of work, and never a whole code project on every search.
    INSIDE_FILES, INSIDE_CHARS, INSIDE_LOOKED, INSIDE_BUDGET = 40, 60_000, 4_000, 30_000_000
    #: How much of the things' own pages one search reads whole, in all.
    #: Past it a page is read as read_ends reads it, its start and its end
    #: (see _page).
    PAGES_BUDGET = 30_000_000
    #: How far in the names of the files in a thing's own folder are matched:
    #: this many folders down, out of this many names looked at. Names cost
    #: nothing to read, so they go much further than the words do; what lies
    #: beyond both, ./os check names (Doctor._out_of_reach, `too-far-in`).
    NAMES_DEPTH, NAMES_LOOKED = 12, 20_000
    #: Folders inside that hold what programs made, not what a person wrote,
    #: besides .git, node_modules, __pycache__ and venv, which `ignored()`
    #: names; anything starting with a dot is left out too.
    NOT_READ_INSIDE = {"env", "site-packages", "vendor", "dist", "build", "target",
                       "Pods", "DerivedData", "bower_components"}
    #: Maps ./os writes itself, which name everything and would match anything.
    MADE_HERE = {"INDEX.md", "_index.md", "CATALOG.md"}
    #: What Finder and Windows leave in a folder: not a name anybody chose.
    LITTER = {"Icon\r", "desktop.ini", "Thumbs.db"}

    def __init__(self, os_: "Zenith", scanner: "Scanner | None" = None):
        self.os = os_
        self.scanner = scanner or Scanner(os_)
        #: A near word for each word typed that is in nothing, to offer as a
        #: search of its own. Never searched for.
        self.near: dict[str, str] = {}
        #: Every word typed that something had in it, in the last search.
        self.matched: set[str] = set()
        self._pages_left = self.PAGES_BUDGET
        #: For a thing found by words in another file in its folder, that file
        #: (by the thing's path), so a hit can say where it was.
        self.found_in: dict[str, Path] = {}
        self._items_at: set[str] = set()
        self._inside_read: dict[str, list] = {}
        self._contents: dict[str, "Contents"] = {}
        self._budget = self.INSIDE_BUDGET
        #: Every thing the last by_id() matched, when that was more than one,
        #: and what that one looked through.
        self.ambiguous: list[Item] = []
        self._scanned: list[Item] = []
        self._slugs: dict[str, str] = {}
        self._wheres: dict[str, tuple] = {}

    def _read_inside(self, name: str) -> bool:
        """Whether a folder inside a thing's own folder is read by search.

        A folder called Content is: only Work/Content is the one ./os never
        looks in, and it is never inside a thing. Website copy kept in
        Work/Website Redesign/Content was left out as if it were footage."""
        return not (name.startswith(".") or name in self.NOT_READ_INSIDE
                    or name in IGNORE_NAMES or name.endswith(IGNORE_SUFFIXES))

    def _inside(self, it: Item) -> list[tuple[Path, str]]:
        """The other notes in a thing's own folder, as (file, what it says).

        Search read a folder's page and nothing else, so a quote kept in
        Work/Kitchen Refit/quotes.md was never found, nor anything a person
        kept inside a piece of work. Text files and TextEdit notes in it are
        read now, up to the caps above. Never another thing's files: a thing
        filed inside this folder is searched as itself.

        The files at the top come first, then one level down, and so on; and
        in each level every folder takes a turn, one file each. Read folder by
        folder, 45 chapters in Chapters/ used up the whole cap, and the one
        note in Research/ beside it was never searched."""
        key = str(it.path)
        if key in self._inside_read:
            return self._inside_read[key]
        out: list[tuple[Path, str]] = []
        self._inside_read[key] = out
        if not it.is_dir or it.path.is_symlink() or words_file(it.path) is not None:
            return out
        for shelves in self.contents(it).shelves:
            for turn in range(max(len(shelf) for shelf in shelves)):
                for shelf in shelves:
                    if turn >= len(shelf):
                        continue
                    if len(out) >= self.INSIDE_FILES or self._budget <= 0:
                        return out
                    path = shelf[turn]
                    if path.suffix.lower() == ".rtf":
                        text = rtf_words(read_rtf(path, self.INSIDE_CHARS * 4))
                    elif path.name.endswith(".card.md"):
                        # A card: what it says is about the file beside it,
                        # its tags and description as much as its words.
                        meta, text = parse_frontmatter(read_text(path, self.INSIDE_CHARS))
                        tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
                        text = " ".join([str(meta.get("description") or ""),
                                         " ".join(map(str, tags)), text])
                        path = path.with_name(path.name[:-len(".card.md")])
                    else:
                        _, text = parse_frontmatter(read_text(path, self.INSIDE_CHARS))
                    if text.strip():
                        self._budget -= len(text)
                        # A TextEdit note with a picture is the .rtfd, not its TXT.rtf
                        out.append((path.parent if path.parent.suffix.lower() == ".rtfd"
                                    else path, text))
        return out

    def contents(self, it: Item, all_of_it: bool = False) -> "Contents":
        """What is in a thing's own folder, as far as search goes into it.

        `shelves`: the text files whose words are read (_inside), level by
        level, one list for each folder. `named`: every file and folder in it
        whose name is matched. `unseen`: asked for `all_of_it`, the files
        further in than names are matched, which ./os check names.

        Search matched the words of text files and nothing else, so a folder
        of PDFs, Notes/Taxes, or of Word files, Notes/School, was one thing
        whose files no name found, the ones added later too, while sort said
        nothing was waiting and check said all good (review, 2026-09-30).
        Every name in it is matched now. Not the names inside a code project
        or a program's document, `My Novel.scriv`: those are the program's,
        and its own name is matched as the one file it is."""
        key = f"{it.path}\0{all_of_it}"
        if key in self._contents:
            return self._contents[key]
        got = self._contents[key] = Contents()
        if not it.is_dir or it.path.is_symlink():
            return got
        # ./os check asks for all of it, and has no use for the words.
        words_ok = words_file(it.path) is None and not all_of_it
        level, looked, depth = [(it.path, document_bundle(it.path))], 0, 0
        while level:
            shelves, below = [], []
            for here, sealed in level:
                words_too = words_ok and looked < self.INSIDE_LOOKED
                names_too = not sealed and looked < self.NAMES_LOOKED and depth < self.NAMES_DEPTH
                if not (words_too or names_too or (all_of_it and not sealed)):
                    continue
                if not sealed and not names_too and not got.why:
                    got.why = "many" if looked >= self.NAMES_LOOKED else "deep"
                if looked >= self.NAMES_LOOKED * 5:
                    got.more = True           # ./os check counts this far, and no further
                    break
                try:
                    with os.scandir(here) as found:
                        entries = sorted(found, key=lambda e: e.name)
                except OSError:
                    continue
                looked += len(entries)
                # Another copy of a folder like this one, kept inside, is
                # sealed too: its cards are its own (check and learn sweep).
                sealed = sealed or any(e.name in CODE_MARKERS for e in entries) or another_os(here)
                shelf = []
                for entry in entries:
                    path = here / entry.name
                    try:
                        if str(path) in self._items_at or entry.is_symlink():
                            continue
                        folder = entry.is_dir()
                    except OSError:
                        continue
                    if folder:
                        if not self._read_inside(entry.name):
                            continue
                        bundle = document_bundle(path)
                        if names_too and not sealed:
                            got.named.append((path, name_words(path.stem if bundle else entry.name)
                                              + " " + nfc(entry.name), True))
                        below.append((path, sealed or bundle))
                        continue
                    name = entry.name
                    # A card put back beside its file in here (Doctor, `card-
                    # left-behind`) is read for that file; one whose file has
                    # gone is said by ./os check.
                    if name.endswith(".card.md") and not name.startswith(".") and not sealed:
                        if (here / name[:-len(".card.md")]).exists():
                            if words_too:
                                shelf.append(path)
                        elif all_of_it:
                            got.lone.append(path)
                        continue
                    if name.startswith(".") or name.endswith(IGNORE_SUFFIXES) \
                            or name in self.LITTER or path == it.spine:
                        continue
                    if words_too and name not in self.MADE_HERE \
                            and path.suffix.lower() in TEXT_SUFFIXES | {".rtf"}:
                        shelf.append(path)
                    if sealed:
                        continue
                    if names_too:
                        got.named.append((path, name_words(path.stem) + " " + nfc(name), False))
                    elif all_of_it:
                        got.unseen.append(path)
                if shelf:
                    shelves.append(shelf)
            if shelves:
                got.shelves.append(shelves)
            level, depth = below, depth + 1
        return got

    def _page(self, path: Path) -> str:
        """A thing's own page, as search reads it: all of it.

        `read_ends` keeps the start and the end of a long page, which is
        where a long log's newest lines are, and search read pages that way
        too: a word in the middle of a book-length note was never found. A
        page is read whole now, until PAGES_BUDGET of them has been read in
        one search, so a folder full of long ones still answers quickly;
        past that, the start and the end, as before."""
        try:
            size = path.stat().st_size
        except OSError:
            return read_ends(path)
        if size > self._pages_left:
            return read_ends(path)
        self._pages_left -= size
        return read_text(path, -1)

    # -- scoring ------------------------------------------------------------

    def _pass(self, items: list[Item], terms: list["Term"],
              vocabulary: set | None) -> list[tuple[float, Item, str]]:
        results = []
        for it in items:
            hay_title = unaccented(nfc(f"{it.ident} {it.title} {it.path.name}").lower())
            hay_meta = unaccented(nfc(f"{it.domain} {' '.join(it.tags)} {it.status} {it.blurb}").lower())
            raw = ""
            if it.spine and it.spine.exists() and it.spine.suffix.lower() in TEXT_SUFFIXES:
                _, raw = parse_frontmatter(self._page(it.spine))
            # An RTF is read itself, not only its card: TextEdit goes on saving
            # into the same file, and a card written before that, or before
            # cards carried the words at all, knows none of it. The card's own
            # copy is left out, so a word is not counted twice, and one taken
            # out of the file is not found. Only the copy: everything from the
            # heading down was dropped, and a sentence added at the end of the
            # card, where an AI or a person naturally puts one, was never found.
            # A card whose end line was edited away is read whole: a word
            # counted twice is better than one of theirs never found.
            rtf = words_file(it.path)
            if rtf is not None:
                head, _, rest = raw.partition(CARD_WORDS)
                if CARD_WORDS_END in rest:
                    rest = rest.split(CARD_WORDS_END, 1)[1]
                raw = head + "\n" + rest + "\n" + rtf_words(read_rtf(rtf))
            raw = nfc(COMMENT_RE.sub(" ", raw))
            # Its page first, then the other notes in its folder (see _inside):
            # (the file it came from, or None for the page; the words; lowered)
            pages = [(None, raw, unaccented(raw.lower()))]
            for where, text in self._inside(it):
                text = nfc(COMMENT_RE.sub(" ", text))
                pages.append((where, text, unaccented(text.lower())))
            # Then the name of everything in its folder (see contents), with no
            # words of its own to show: the line under the hit says which file.
            if it.is_dir:
                pages += [(where, "", unaccented(words.lower()))
                          for where, words, _folder in self.contents(it).named]
            # The file whose name has every word asked for, when one does:
            # `img 39099` is IMG_39099.jpg, not the first IMG_ in the folder.
            named_all = next((where for where, text, low in pages[1:] if not text
                              and all(t.weight(low) for t in terms)), None) \
                if len(terms) > 1 else None

            # Collect the words we have already read, so a failed search can ask
            # "did you mean" without opening a single extra file.
            if vocabulary is not None and len(vocabulary) < 60_000:
                vocabulary.update(WORD_RE.findall(hay_title))
                vocabulary.update(WORD_RE.findall(hay_meta))
                for _where, _text, low in pages:
                    vocabulary.update(WORD_RE.findall(low[:8_000]))

            score, snippet, found_in, shown = 0.0, (it.blurb or it.summary), None, False
            for term in terms:
                in_title, in_meta = term.weight(hay_title), term.weight(hay_meta)
                score += 10 * in_title
                score += 4 * in_meta
                hits, found = 0, []        # (worth, form, page) for each page it is in
                for one in pages:
                    n, w, f = term.count(one[2])
                    if n:
                        hits += n
                        found.append((w, f, one))
                if hits or in_title or in_meta:
                    self.matched.add(term.word)
                if not hits:
                    continue
                score += min(hits, 8) * 1.2 * found[0][0]
                # Show the words around it, unless they already show; and never
                # swap the words found inside for a word of its own name, or for
                # nothing. `./os find "harlow kitchen"` showed the quote from
                # Harlow Joinery, then "kitchen" in the heading of the kitchen
                # refit's page replaced it with an empty line and lost the file.
                if found[0][1] in unaccented((snippet or "").lower()) or (shown and term.weight(hay_title)):
                    continue
                for _worth, form, (where, text, low) in found:
                    if not text:
                        # Found by a file's name. The file is what to show,
                        # never in place of words already found inside.
                        if not shown:
                            snippet, found_in, shown = "", named_all or where, True
                        break
                    pos = low.find(form)
                    words = gist(text[max(0, pos - 60): pos + 120], 200)
                    if words:
                        snippet, found_in, shown = words, where, True
                        break
            if len(terms) > 1 and all(t.weight(hay_title) for t in terms):
                score += 12
            if score > 0:
                results.append((round(score, 2), it, snippet or ""))
                if found_in is not None:
                    self.found_in[str(it.path)] = found_in
        results.sort(key=lambda r: (-r[0], r[1].title.lower()))
        return results

    # -- the search ---------------------------------------------------------

    def search(self, query: str, limit: int = 20, kind: str = "", bucket: str = "",
               notes_only: bool = False) -> list[tuple[float, Item, str]]:
        self.near, self.matched, self._pages_left = {}, set(), self.PAGES_BUDGET
        terms = [t for t in re.split(r"\s+", unaccented(nfc(query).lower().strip())) if t]
        if not terms:
            return []
        # Skills and helpers are the machinery, not the person's work. Searching
        # "one sentence" should not hand back nine skill files ahead of the two
        # notes they were actually looking for. Ask for them by kind to see them.
        toolkit = {"skill", "agent", "hook"}
        every = self.scanner.scan()
        self.found_in, self._inside_read, self._budget = {}, {}, self.INSIDE_BUDGET
        self._contents = {}
        self._items_at = {str(it.path) for it in every}
        items = [it for it in every
                 if (kind in toolkit or it.kind not in toolkit)
                 and (not kind or it.kind == kind)
                 and (not bucket or it.bucket.lower().startswith(bucket.lower()))
                 and (not notes_only or self.a_note(it))]

        vocabulary: set[str] = set()
        hits = self._pass(items, [Term(t) for t in terms], vocabulary)

        # A word nothing has in it. A near word it knows may be what they
        # meant: every word in the folder is already in hand from the pass
        # above, so this costs no reads. It is only offered, never searched
        # for. Searched for, a real word was swapped for another one: bike
        # for bake, june for jungle, valve for valet, and the notes shown were
        # about something else (review, 2026-09-30). Offered when the other
        # words found something, too: `lemon recipie` showed the lemon notes
        # and said nothing about recipie, where `recipie` alone offered recipe.
        for term in terms:
            if len(term) < 4 or term in vocabulary or term in self.matched:
                continue
            near = difflib.get_close_matches(term, vocabulary, n=1, cutoff=0.75)
            if near and near[0] != term:
                self.near[term] = near[0]
        return hits[:limit]

    def a_note(self, it: Item) -> bool:
        """Is this in Notes, or put away from Notes? `./os find --notes` looks
        only at these, for a helper told to keep out of Work: its results
        printed lines from Work items, so it skipped the search altogether."""
        buckets = self.os.buckets()
        role = buckets.get(it.bucket, {}).get("role")
        if role == "archive":
            came_from = it.trail[1] if len(it.trail) > 1 else ""
            role = buckets.get(came_from, {}).get("role")
        return role == "note"

    def like(self, title: str, kinds: tuple = (), threshold: float = 0.82,
             archived: bool = False) -> list:
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
            # `archived` looks only in the archive, where everything is kind
            # "archive", so the kinds asked for don't apply there.
            in_archive = it.bucket == self.os.bucket_for_role("archive")
            if in_archive != archived:
                continue
            if kinds and not archived and it.kind not in kinds:
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

    def by_id(self, ident: str, archived: bool = False) -> Item | None:
        """The one item carrying this name. An empty string matches nothing.

        Things are addressed by name now — the folder or file name, or the
        title said any reasonable way ("Q3 OKR review" and q3-okr-review
        are the same thing). Skills and helpers are tools, not items, so they
        are never matched here — `./os close tidy` must not archive a skill.

        The name on disk wins, then that name as its one-word handle, and only
        then the title. Two notes can share a title: taking the first title
        match the scan met, `./os close same-title-here` put away
        same-title-here-2.md, and the handle `resume-review-2` that ./os had
        just printed found nothing at all.

        One name can be on two things: Pizza.md in My Recipes and in Recipes.
        ./os show took the first, and the other could be reached only by
        renaming one by hand (review, 2026-09-30). Every thing the name fits
        is kept in `ambiguous`, for the_one() to ask which is meant; the first
        is still returned, to callers asking only whether a name is in use.
        The folder a thing is in tells them apart, `Recipes/Pizza`, and so
        does its path from the top of this folder. Something live is meant
        before something put away, as it always was; `archived` turns that
        round for ./os back, which only ever means one put away. After
        `./os close Acme/Website`, the `./os back website` it printed brought
        out nothing, since Work/Website was live and taken first."""
        self.ambiguous = []
        ident = nfc(ident).strip()
        if not ident:
            return None
        items = self._scanned = [it for it in self.scanner.scan()
                                 if it.kind in ("project", "note", "asset", "archive")]
        found = self._named(ident, items) or (self._placed(ident, items) if "/" in ident else [])
        if not found:
            return None
        shelves = {n for n, spec in self.os.buckets().items() if spec.get("role") == "archive"}
        pool = [it for it in found if (it.bucket in shelves) == archived] or found
        if len(pool) > 1:
            self.ambiguous = pool
        return pool[0]

    def _slug(self, text: str) -> str:
        """slugify(), remembered: asking which of 800 things a name fits
        slugged every one of them again for each name asked about, and ./os
        took three seconds on a folder of 200 folders of the same four notes."""
        got = self._slugs.get(text)
        if got is None:
            got = self._slugs[text] = slugify(nfc(text)).lower()
        return got

    def _where(self, it: Item) -> tuple[str, str, list[str]]:
        """A thing's name, its path from the top and the folders it is in, as
        they are compared: worked out once for each, for the same reason."""
        key = str(it.path)
        got = self._wheres.get(key)
        if got is None:
            at = nfc(self.os.rel(it.path))
            got = self._wheres[key] = (nfc(it.ident).lower(), at.lower(),
                                       [self._slug(p) for p in Path(at).parent.parts])
        return got

    def _named(self, ident: str, items: list[Item], where=None) -> list[Item]:
        """Every thing answering to this name, by the first way that fits any:
        its name on disk, that name as a handle, then its title."""
        exact, slug = ident.lower(), self._slug(ident)
        if nameless(ident):
            # Its slug is the stand-in "untitled", which fits Work/Untitled
            # too: `./os close 🎉🎉` put that away instead (audit, 2026-10-28).
            hits = [it for it in items if exact in (self._where(it)[0], nfc(it.title).lower())
                    and (where is None or where(it))]
            return hits
        # Its exact title before any handle: "C# notes" and "C++ notes" share
        # the handle c-notes, and `./os close "C# notes"` put C++ Notes away
        # (audit, 2026-10-29).
        for same in (lambda it: self._where(it)[0] == exact,
                     lambda it: nfc(it.title).lower() == nfc(ident).lower(),
                     lambda it: self._slug(it.ident) == slug,
                     lambda it: self._slug(it.title) in (exact, slug)):
            hits = [it for it in items if same(it) and (where is None or where(it))]
            if hits:
                return hits
        return []

    def _placed(self, said: str, items: list[Item]) -> list[Item]:
        """A name with folders it is in, in order: `Recipes/Pizza`, or
        `Archive/Website` for Archive/2026/Work/Website, or the whole path,
        `Notes/Recipes/Pizza.md`."""
        said = said.strip().strip("/")
        whole = [it for it in items if self._where(it)[1] == said.lower()]
        if whole:
            return whole
        parts = [p.strip() for p in said.split("/") if p.strip()]
        if len(parts) < 2:
            return []
        tail = [self._slug(p) for p in parts[:-1]]

        def under(it: Item) -> bool:
            ups = iter(self._where(it)[2])
            return all(any(up == want for up in ups) for want in tail)
        return self._named(parts[-1], items, under)

    def typed_names(self, items: list[Item], shown: list[Item]) -> dict[str, str]:
        """What to print after `./os show` for each thing `shown`: its handle,
        or, when another live thing answers to that too, the name with its
        folder. `items` is everything, to tell which names are shared.

        The front screen printed `./os show garden` beside the Garden in Work
        while Notes had a Garden too, and that command stopped with "which
        one?"; the brief told the AI the same handle."""
        live = [it for it in items if it.kind in ("project", "note", "asset")
                and self.os.buckets().get(it.bucket, {}).get("role") != "archive"]
        by_slug: dict[str, list[Item]] = {}
        for it in live:
            by_slug.setdefault(self._slug(it.ident), []).append(it)
        out = {}
        for it in shown:
            twins = by_slug.get(self._slug(it.ident), [])
            out[str(it.path)] = self.qualified(it, twins, live) if len(twins) > 1 else handle(it)
        return out

    def qualified(self, it: Item, among: list[Item], items: list[Item] | None = None) -> str:
        """The shortest way to name `it` that nothing else answers to, ready to
        type: its name with the folder it is in, `"My Recipes/Pizza"`, then
        more of the folders above, and failing all that its whole path."""
        items = items if items is not None else self._scanned or among
        items = [i for i in items if i.kind in ("project", "note", "asset", "archive")]
        ups = Path(self.os.rel(it.path)).parent.parts
        tries = ["/".join((*ups[-n:], it.ident)) for n in range(1, len(ups) + 1)]
        said = next((t for t in tries
                     if [o.path for o in (self._named(t, items) or self._placed(t, items))]
                     == [it.path]), self.os.rel(it.path))
        return shell_word(said)


#: What `./os close <name>` can say about how a thing ended, and the line each
#: writes in its ## Log.
CLOSED_AS = {"done": "Closed as done", "dropped": "Closed as dropped"}


def with_log_line(body: str, entry: str) -> str:
    """`body` with one more line at the end of its `## Log`, which is made at
    the bottom when there is none. Last, like a decision (see write_decision),
    because the newest line goes at the bottom."""
    lines = body.split("\n")
    start = next((n for n, line in enumerate(lines)
                  if re.match(r"##\s+Log\s*$", line, re.I)), None)
    if start is None:
        return body.rstrip() + "\n\n## Log\n" + entry + "\n"
    end = next((n for n in range(start + 1, len(lines)) if SECTION_RE.match(lines[n])),
               len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, entry)
    return "\n".join(lines)


class Archivist:
    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.finder = Finder(os_)

    def archive(self, ident: str, how: str = "", commit: bool = True) -> Path:
        item = the_one(self.os, ident, "close", finder=self.finder)
        # Said here as hold, rename and decide say it: close put away work
        # another chat had claimed, without a word (audit, 2026-10-29).
        warn_if_claimed(self.os, item)
        shelf = self.os.bucket_for_role("archive")
        if item.bucket == shelf:
            die(f"{ident} is already in the archive — ./os back {handle(item)} brings it out")
        if how and not (item.spine and item.spine.exists()):
            die(f"{ident} has no page to write '{how}' on, so nothing was changed.\n"
                f"     to put it away without it:   ./os close {handle(item)}")
        dest = self.os.root / shelf / today()[:4] / item.bucket / item.path.name
        # Parts of it put away earlier this year, waiting there under its
        # name: its own Log says they went there (see archive_part).
        coming = [(rel, where) for rel, where in self._parts_away(item)
                  if self._holder_of(where, rel) == dest] if item.path.is_dir() else []
        parts_there = bool(coming) and self._holds_parts(dest)
        text = None
        if item.spine and item.spine.exists():
            # Before the header is touched, not after the move: undo restores
            # what a file *said* as well as where it was, and a snapshot taken
            # afterwards would put the archived header back on a live item.
            text = read_to_rewrite(item.spine)
            self.os.snapshot(item.spine)
        merged: list = []
        if parts_there:
            # They go back inside it first, so the whole thing lands under its
            # own name, not beside them as a second one. Only its own: another
            # thing's of the same name went in with it (twentieth review).
            # Before its page says so: written first, a part that couldn't be
            # moved was off its Log and out of reach (third pre-merge review
            # of round 26). Stopped here, the page is as it was, and running
            # it again takes in the rest.
            for rel, where in coming:
                target = item.path / rel
                if where.is_dir() and target.is_dir() and not target.is_symlink():
                    self._move_into(where, target)
                else:
                    self.os.move(where, unique_path(target))
                merged.append(rel)
                up = where.parent
                while up != dest.parent and dest in (up, *up.parents):
                    self._take_away_if_empty(up)
                    up = up.parent
        if text is not None:
            meta, body = parse_frontmatter(text)
            # What it was before it left, so ./os back can put it back as it was
            # rather than waking every filed note up as work with a next action.
            was = str(meta.get("status") or "").strip()
            changes = {"status": "archived", "archived": today()}
            if was:
                changes["was"] = was
            changes["origin"] = self.os.rel(item.path)
            changes["updated"] = today()
            # Finished and given up looked the same in the archive a year on.
            # Said when it is closed, it goes in its own Log, where its story is.
            said = with_log_line(body, f"- {today()} · {CLOSED_AS[how]}") if how else None
            # The parts it took back in are off its Log, as a back --part
            # takes them off: left on, once it came back out its old lines
            # claimed the next thing's part put in that place (second
            # pre-merge review of round 26).
            # Every one its Log puts in this holder, taken in now or by a run
            # of this close that stopped partway: one left on claimed what
            # landed there next (fourth pre-merge review of round 26).
            for rel, where in self._parts_away(item, gone_too=True):
                if self._holder_of(where, rel) == dest:
                    said = with_log_line(body if said is None else said,
                                         f"- {today()} · Brought {rel} back out of {self.os.rel(dest)}")
            write_text(item.spine, set_fields(text, changes, body=said))
        gone_from = item.path.parent
        moved = self.os.move_item(item.path, dest)
        self._take_away_emptied(gone_from)
        if commit:
            self.os.commit(f"archive {ident}")
        return moved

    def _take_away_emptied(self, folder: Path) -> None:
        """Take away the group this close left empty, and each group around it
        left empty too. Sort did it, but only on its next run, so the group
        stood empty until then.

        Only a group ./os made, with nothing left in it but its own mark and
        what a computer leaves. A folder somebody made is never taken away, and
        neither is a group with anything else in it. Its mark goes in the step,
        so ./os undo makes it again as a group, not as a folder that looks like
        somebody's own."""
        groups = [self.os.root / name for name, spec in self.os.buckets().items()
                  if spec.get("categorize")]
        while Scanner.is_category(folder) and any(g in folder.parents for g in groups):
            inside = list(folder.iterdir())
            litter = [p for p in inside if p.is_file() and not p.is_symlink()
                      and (p.name in (CATEGORY_MARKER, ".DS_Store", "Thumbs.db", "desktop.ini")
                           or p.name.startswith("._"))]
            if len(litter) < len(inside):
                return
            try:
                mark = (folder / CATEGORY_MARKER).read_text(encoding="utf-8")
                for p in litter:
                    p.unlink()
                folder.rmdir()
            except (OSError, ValueError):
                return
            self.os.record("rmdir", self.os.rel(folder), mark=mark)
            folder = folder.parent

    def restore(self, ident: str) -> Path:
        item = the_one(self.os, ident, "back", finder=self.finder, archived=True)
        warn_if_claimed(self.os, item)
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
        if dest.exists() and self._holds_parts(item.path):
            # Only parts of a thing that is still live: brought out whole, they
            # landed beside it as a second one, with -2 on its name.
            inside = sorted(p.name for p in item.path.iterdir() if not ignored(p))
            die(f"{ident} is still live, and what is put away is part of it: "
                f"{', '.join(inside[:6])}\n"
                f"     to bring one back:   ./os back {handle(item)} --part {shell_word(inside[0] if inside else 'raw')}")
        if item.spine and item.spine.exists():
            # A note or a file is not work, and coming out of the archive must
            # not turn it into work with a next action. Only what was pushed or
            # held goes back to a live phase; everything else keeps its own word.
            was = str(meta.get("was") or "").strip()
            status = was or ("—" if str(meta.get("type", "")) != "work" else PUSHING)
            # Its Log ended "Closed as done" while it was live again (audit,
            # 2026-10-28): coming back is said there too, where there is one.
            _m, body = parse_frontmatter(text)
            if re.search(r"^##\s+Log\s*$", body, re.M):
                body = with_log_line(body, f"- {today()} · Brought back out of the archive")
            write_text(item.spine, set_fields(text, {"status": status, "updated": today()},
                                              drop=("was", "archived", "origin"), body=body))
        came_from = item.path.parent
        moved = self.os.move_item(item.path, dest)
        self._empty_shelves(came_from)
        #: the name it would have had, when that was taken: see cmd_back
        self.beside = dest.name if moved.name != dest.name else ""
        self.os.commit(f"restore {ident}")
        return moved

    # -- one folder inside a thing ------------------------------------------
    # A folder of raw working files could only be put away by hand, with git
    # mv, and ./os undo could not take that back. It goes where the whole
    # thing would, Archive/<year>/<folder>/<its name>/, keeping its path
    # inside, and the thing stays live. Only folders: a thing's main page, a
    # sources/ folder and anything outside the thing are refused.

    @staticmethod
    def _holds_parts(path: Path) -> bool:
        """A folder in the archive named after a thing, holding only parts
        of it: folders, and no page of its own."""
        if not path.is_dir() or path.is_symlink() or Scanner.spine_of(path) is not None:
            return False
        litter = (".DS_Store", "Thumbs.db", "desktop.ini")
        return all(p.is_dir() for p in path.iterdir()
                   if p.name not in litter and not p.name.startswith("._"))

    def _parts_away(self, item: Item, gone_too: bool = False) -> list:
        """(part, where it is) for each part of `item` its own Log says was
        put away and that is still there, newest last. The Log is what says
        whose a part is: two things of one name in two groups shared one
        holder, and each was given the other's (twentieth review, round 25)."""
        if not (item.spine and item.spine.exists()):
            return []
        # Read in order, each part a list of where it went: one brought back
        # comes off it, the newest one from that holder, as back takes the
        # newest. Kept after it came back, its line claimed the next thing's
        # part put in that place; one name per part forgot an older copy
        # still there (pre-merge review of round 26).
        away: list = []
        for line in read_text(item.spine, -1).splitlines():
            put = re.match(r"^- \S+ · Put away (.+?): it is in (.+?)\s*$", line)
            back = re.match(r"^- \S+ · Brought (.+?) back out of (.+?)(?:, as .*)?\s*$", line)
            if put:
                away.append((put.group(1), self.os.root / put.group(2)))
            elif back:
                rel, holder = back.group(1), self.os.root / back.group(2)
                for n in range(len(away) - 1, -1, -1):
                    if away[n][0] == rel and self._holder_of(away[n][1], rel) == holder:
                        del away[n]
                        break
        # Only in a holder of its own name in the archive: a line written by
        # hand, or a page copied from another thing, brought a live folder
        # of another thing's in (second pre-merge review of round 26).
        shelf = (self.os.root / self.os.bucket_for_role("archive")).resolve()

        def its_own(rel: str, where: Path) -> bool:
            try:
                if ".." in Path(rel).parts or ".." in where.relative_to(self.os.root).parts:
                    return False
            except ValueError:          # a path from the top of the disk
                return False
            holder = self._holder_of(where, rel)
            try:
                inside = holder.resolve().relative_to(shelf).parts
            except (ValueError, OSError):
                return False
            # Any name: renamed since, its parts are still under the old one
            # (third pre-merge review of round 26). But only a holder of
            # parts, never a thing put away whole, page and all (fourth).
            return len(inside) == 3 and inside[1] == item.bucket and self._holds_parts(holder)
        if gone_too:
            return away
        return [(rel, where) for rel, where in away
                if where.is_dir() and not where.is_symlink() and its_own(rel, where)]

    @staticmethod
    def _holder_of(where: Path, rel: str) -> Path:
        holder = where
        for _ in Path(rel).parts:
            holder = holder.parent
        return holder

    def _move_into(self, src: Path, dst: Path) -> None:
        """Everything in `src` into `dst`, folder into folder of the same
        name, then `src` taken away once nothing is left in it. Each move is
        written down, so undo puts it all back where it was."""
        for child in sorted(src.iterdir()):
            if child.name in (".DS_Store", "Thumbs.db", "desktop.ini") or child.name.startswith("._"):
                continue
            target = dst / child.name
            if child.is_dir() and not child.is_symlink() and target.is_dir() and not target.is_symlink():
                self._move_into(child, target)
            else:
                self.os.move(child, target)
        self._take_away_if_empty(src)

    def _empty_shelves(self, folder: Path) -> None:
        """Archive/<year>/Work and the like, left empty by what came back
        out: they stood there for good (audit, 2026-10-29). Never the archive
        itself, and only what's empty."""
        shelf = self.os.root / self.os.bucket_for_role("archive")
        while shelf in folder.parents:
            self._take_away_if_empty(folder)
            if folder.exists():
                return
            folder = folder.parent

    @staticmethod
    def _take_away_if_empty(folder: Path) -> None:
        try:
            inside = list(folder.iterdir())
            if all(p.is_file() and (p.name == ".DS_Store" or p.name.startswith("._")) for p in inside):
                for p in inside:
                    p.unlink()
                folder.rmdir()
        except OSError:
            pass

    def _part_of(self, item: Item, part: str, verb: str) -> Path:
        """The folder inside `item` that `part` names, as a path from the
        item's top. Stops, saying why, when it is anything else."""
        name = handle(item)
        raw = Path(part.strip())
        if raw.is_absolute():
            try:
                raw = raw.relative_to(item.path)
            except ValueError:
                die(f"{part} is not inside {item.path.name}, so it was left alone")
        rel = Path(os.path.normpath(str(raw))) if str(raw) else Path(".")
        if rel == Path(".") or not rel.parts:
            die(f"that is all of {item.path.name}, not a part of it:   ./os {verb} {name}")
        if rel.parts[0] == ".." or rel.is_absolute():
            die(f"{part} is not inside {item.path.name}, so it was left alone")
        if any(p.lower() == "sources" for p in rel.parts):
            die(f"{part} is in a sources folder, and those stay as they are, so it was left alone")
        return rel

    def archive_part(self, ident: str, part: str) -> Path:
        item = the_one(self.os, ident, "close", then=f" --part {shell_word(part)}", finder=self.finder)
        shelf = self.os.bucket_for_role("archive")
        name = handle(item)
        if item.bucket == shelf:
            die(f"{ident} is already in the archive, all of it — ./os back {name} brings it out")
        if not item.path.is_dir() or item.path.is_symlink():
            die(f"{ident} is one file, with no folder inside it to put away.\n"
                f"     to put all of it away:   ./os close {name}")
        rel = self._part_of(item, part, "close")
        src = item.path / rel
        if src.is_symlink() or (src.exists() and not src.is_dir()):
            die(f"{rel.as_posix()} is not a folder; --part puts away one folder inside {item.path.name}")
        if not src.is_dir():
            inside = sorted(p.name for p in item.path.iterdir() if p.is_dir() and not ignored(p))
            die(f"{item.path.name} has no folder called {rel.as_posix()}"
                + (f" — it has {', '.join(inside[:8])}" if inside else ""))
        # Through a shortcut on the way, it could be somewhere else entirely.
        if item.path.resolve() not in src.resolve().parents:
            die(f"{part} is not inside {item.path.name}, so it was left alone")
        spine = item.spine.resolve() if item.spine and item.spine.exists() else None
        if spine is None:
            die(f"{ident} has no page to write down what was put away, so nothing was changed")
        if src.resolve() in spine.parents:
            die(f"{rel.as_posix()} holds {item.path.name}'s own page, so it can't go without the rest.\n"
                f"     to put all of it away:   ./os close {name}")
        for _top, dirs, _files in os.walk(src):
            if any(d.lower() == "sources" for d in dirs):
                die(f"{rel.as_posix()} has a sources folder in it, and those stay where they are, "
                    "so it was left alone")
        holder = self.os.root / shelf / today()[:4] / item.bucket / item.path.name
        mine = {self._holder_of(where, rel) for rel, where in self._parts_away(item)}
        n = 1
        # A whole thing of the same name put away before, or another thing's
        # parts: never shared (twentieth review, round 25).
        while holder.exists() and not (self._holds_parts(holder) and holder in mine):
            n += 1
            holder = holder.with_name(f"{item.path.name}-{n}")
        moved = self.os.move(src, holder / rel)
        text = read_to_rewrite(item.spine)
        self.os.snapshot(item.spine)
        meta, body = parse_frontmatter(text)
        said = with_log_line(body, f"- {today()} · Put away {rel.as_posix()}: "
                                   f"it is in {self.os.rel(moved)}")
        write_text(item.spine, set_fields(text, {"updated": today()}, body=said))
        self.os.commit(f"put away part of {ident}")
        return moved

    def restore_part(self, ident: str, part: str) -> Path:
        item = the_one(self.os, ident, "back", then=f" --part {shell_word(part)}", finder=self.finder)
        shelf = self.os.bucket_for_role("archive")
        name = handle(item)
        if item.bucket == shelf:
            die(f"{ident} is put away, all of it — ./os back {name} brings it all out")
        rel = self._part_of(item, part, "back")
        base = item.path.name
        if not (item.spine and item.spine.exists()):
            die(f"{ident} has no page to write down what came back, so nothing was changed")
        # Where its own Log says it went: the first holder of the name with a
        # part of that name in it brought another thing's back (twentieth
        # review, round 25).
        away = dict(self._parts_away(item)).get(rel.as_posix())   # the newest still away
        if away is None:
            die(f"{item.path.name}'s own Log doesn't say {rel.as_posix()} was put away, so it can't "
                f"tell which one is its own — ./os find --kind archive {shell_word(base)} shows what is")
        found = self._holder_of(away, rel.as_posix())
        moved = self.os.move(away, item.path / rel)
        # The folders it was in, if nothing else is in them now.
        up = away.parent
        while up != found.parent and found in (up, *up.parents):
            self._take_away_if_empty(up)
            up = up.parent
        self._empty_shelves(found.parent)
        text = read_to_rewrite(item.spine)
        self.os.snapshot(item.spine)
        meta, body = parse_frontmatter(text)
        # A folder already called that came back first, and this one landed
        # beside it as Raw-2 while the Log named Raw (audit, 2026-10-28).
        landed = moved.relative_to(item.path).as_posix()
        said = with_log_line(body, f"- {today()} · Brought {rel.as_posix()} back out of "
                                   f"{self.os.rel(found)}"
                                   + (f", as {landed}: {rel.as_posix()} was taken" if landed != rel.as_posix() else ""))
        write_text(item.spine, set_fields(text, {"updated": today()}, body=said))
        self.os.commit(f"bring back part of {ident}")
        return moved


#: How a step's copies folder ends once undo couldn't put them all back:
#: kept for good, never swept with the steps.
NOT_PUT_BACK = "-not-put-back"


class Undo:
    def __init__(self, os_: "Zenith"):
        self.os = os_

    def rel_of(self, path: Path) -> str:
        return relative_to_root(path, self.os.root)

    def peek(self) -> dict | None:
        undo = self.os.state.get("undo") or []
        return undo[-1] if undo else None

    @staticmethod
    def describe(entry: dict, limit: int | None = 4) -> list[str]:
        """What a step did, in a few plain lines: "saved Notes/lemon-cake.md",
        "moved Work/Fix the Boiler to Archive/2026/Work/Fix the Boiler". Undo
        said only "put back the last 'save'", which named nothing, so nobody
        could tell whose words had just gone."""
        steps = entry.get("steps") or []
        made = [st["src"] for st in steps if st.get("action") == "mkdir"]
        # What a save or a bring copied in is said once, as "saved …": the
        # README it then made or changed inside it is part of that. Listed as
        # well, the list ran longer than the count above it (audit, 2026-10-29).
        made += [st["dst"] for st in steps if st.get("action") == "move" and st.get("brought")]
        lines: list[str] = []
        for st in steps:
            act, src, dst = st.get("action"), str(st.get("src") or ""), str(st.get("dst") or "")
            if act == "move" and src.startswith(f"{MARKER}/cache/"):
                line = f"saved {dst}"
            elif act == "move":
                line = f"moved {src} to {dst}"
            elif act == "mkdir":
                line = f"made {src}"
            elif act in ("create", "edit") and any(src.startswith(d + "/") for d in made):
                continue            # inside a folder it made, which says it
            elif act == "create":
                line = f"made {src}"
            elif act == "edit":
                line = f"changed {src}"
            elif act == "remove":
                line = f"removed {src}"
            else:
                continue
            if line not in lines:
                lines.append(line)
        if limit is not None and len(lines) > limit:
            lines = lines[:limit] + [f"...and {len(lines) - limit} more"]
        return lines

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
            elif step.get("action") == "move" and step.get("brought") and step.get("print"):
                # A saved folder or file with anything new in it is said
                # too: undo deleted those without asking (delete audit,
                # 2026-10-29).
                path = now_at(step["dst"], n + 1)
                if path.exists() and not path.is_symlink():
                    now = folder_print(path) if path.is_dir() else content_print(path)
                    if now != step["print"]:
                        found.append(self.rel_of(path))
        blobs = self.os.dot / "cache" / "undo" / str(entry.get("blobs") or "")
        reals = entry.get("reals") or {}
        for rel, blob in (entry.get("snapshots") or {}).items():
            path = now_at(rel, 0)
            if rel in reals and os.path.realpath(path) != reals[rel]:
                path = Path(reals[rel])   # what undo would write back into: see revert
            if not (blobs / blob).is_file() or not path.is_file() \
                    or path.is_symlink() and through_link(path) == path:
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

    def in_the_way(self, entry: dict) -> list[str]:
        """Where a save moved something in from, when something else is there
        now. Undoing a save puts the folder back as it was, so the file goes
        back where it came from, and never lands beside it under a new name."""
        if entry.get("label") != "save":
            return []
        steps = entry.get("steps") or []
        later = {st.get("dst") for st in steps if st.get("action") == "move"}
        out = []
        for st in steps:
            src = str(st.get("src") or "")
            if st.get("action") != "move" or st.get("brought") or src in later \
                    or src.startswith(f"{MARKER}/cache/"):
                continue
            spot = self.os.root / src
            if spot.exists() or spot.is_symlink():
                out.append(src)
        return out

    def revert(self) -> dict:
        undo = self.os.state.get("undo") or []
        if not undo:
            die("nothing to undo")
        entry = undo.pop()
        follow_brought(self.os.state, [(st["dst"], st["src"]) for st in reversed(entry.get("steps") or [])
                                       if st.get("action") == "move" and st.get("src") and st.get("dst")])
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
            copy = keep_copy(path, copy)
            if (self.rel_of(path), self.rel_of(copy)) not in kept:
                kept.append((self.rel_of(path), self.rel_of(copy)))

        try:
            then = _dt.datetime.fromisoformat(str(entry.get("at"))).timestamp() + 5
        except ValueError:
            then = None

        def written_in(path: Path, rel: str, step: dict | None = None) -> bool:
            """Has anybody written in what the step brought in, since?"""
            if step is not None and step.get("print") and id(step) in changed_before:
                # What it was when brought in, against what it was before this
                # undo began: not the dates, which a file dragged in keeps
                # from before, and not after this undo has already taken out
                # the README sort made in it, which made every undo of a
                # saved folder look written in (review, 2026-10-29). Too big
                # to fingerprint ("") falls through to the dates, as before.
                return changed_before[id(step)]
            if path.is_file() and isinstance(after, dict) and rel in after:
                return content_print(path) != after[rel]
            files = [path] if not path.is_dir() else \
                [p for p in path.rglob("*") if p.is_file() and not p.is_symlink()]
            try:
                return then is not None and any(p.stat().st_mtime > then for p in files)
            except OSError:
                return False

        #: where each thing actually came back to. A name can already be taken
        #: by the time we put something back — two runs undone in turn, the
        #: second having reused the first's filenames — and then it lands beside
        #: its old name rather than on it. Its saved content has to follow it
        #: there, or it is written over whatever now holds that name instead.
        landed: dict[str, str] = {}

        def now_at(rel: str) -> str:
            """Where a file is now, its folder having come back beside its
            old name: a copy inside it went to the old name, over whatever
            had that name by then (pre-merge review of round 23)."""
            for was in sorted(landed, key=len, reverse=True):
                if rel == was or rel.startswith(was + "/"):
                    return landed[was] + rel[len(was):]
            return rel
        #: the steps that went back whole, and whether the disk refused one
        done_steps: set = set()
        refused = False
        #: for each brought-in folder or file, whether it differs from its
        #: print, judged before any step is taken back: see written_in().
        changed_before: dict[int, bool] = {}
        for n, step in enumerate(entry["steps"]):
            if step.get("action") == "move" and step.get("brought") and step.get("print"):
                # Followed as now_at() and _where_now() follow it, the
                # folder it's in moving too (review, 2026-10-29).
                rel = step["dst"]
                for later in entry["steps"][n + 1:]:
                    if later.get("action") != "move":
                        continue
                    if rel == later["src"]:
                        rel = later["dst"]
                    elif rel.startswith(later["src"] + "/"):
                        rel = later["dst"] + rel[len(later["src"]):]
                at = self.os.root / rel
                if at.exists():
                    now = folder_print(at) if at.is_dir() and not at.is_symlink() else content_print(at)
                    changed_before[id(step)] = not now or now != step["print"]
        for n, step in reversed(list(enumerate(entry["steps"]))):
            said = len(failed)
            try:
                if step["action"] == "move":
                    # Where it is now: a later step's undo may have put it
                    # back beside its name (pre-merge review of round 23).
                    src = self.os.root / now_at(step["dst"])
                    # Into its folder where that came back, not a new one
                    # with the old name (sixth pre-merge review of round 23).
                    dst = self.os.root / now_at(step["src"])
                    # Never into or out of somewhere a shortcut now leads
                    # outside this folder (round 13, 2026-10-29).
                    if not (inside(src) and inside(dst)):
                        failed.append(f"{step['dst']}: a shortcut to somewhere outside this folder now")
                        continue
                    # `exists()` follows the link, so a shortcut whose target is
                    # gone reads as "not there" and undo used to abandon it in
                    # whichever bucket the sort had put it in.
                    if not src.exists() and not src.is_symlink():
                        failed.append(step["dst"])
                        continue
                    if step.get("brought"):
                        # A copy save made, or the note it wrote from words:
                        # before the save there was nothing, so it goes. The
                        # file it was copied from is still where it was.
                        if written_in(src, step["src"], step):
                            # Kept under where it was, not the staging name
                            # it had for a moment (audit, 2026-10-29).
                            keep(src, step["dst"])
                        if src.is_dir() and not src.is_symlink():
                            shutil.rmtree(src)
                        else:
                            src.unlink()
                    else:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        free = unique_path(dst)
                        move_path(src, free)
                        if self.rel_of(free) != step["src"]:
                            landed[step["src"]] = self.rel_of(free)
                    restored += 1
                    # The folders the move made, if nothing else is in
                    # them now. Anything else in one keeps it.
                    for rel in step.get("made") or []:
                        folder = self.os.root / rel
                        if not folder.is_dir() or folder.is_symlink():
                            continue
                        contents = list(folder.iterdir())
                        if all(p.is_file() and (p.name == ".DS_Store" or p.name.startswith("._"))
                               for p in contents):
                            for p in contents:
                                p.unlink()
                            folder.rmdir()
                elif step["action"] == "create":
                    # Followed, as a move's is: a new file had its name.
                    path = self.os.root / now_at(step["src"])
                    if not inside(path):
                        failed.append(f"{step['src']}: a shortcut to somewhere outside this folder now")
                        continue
                    if path.is_file():
                        if content_print(path) != step.get("digest"):
                            keep(path, step["src"])
                        path.unlink()
                        restored += 1
                elif step["action"] == "mkdir":
                    # Only ever an empty folder. Emptiness used to be judged by
                    # `ignored()`, which counts .git, node_modules and CLAUDE.md
                    # as not there, and the whole folder went with rmtree.
                    path = self.os.root / now_at(step["src"])
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
                elif step["action"] == "remove":
                    # A file ./os took away, like what an update left to
                    # merge: put back from the copy kept, unless something
                    # else has its name by now.
                    path = self.os.root / now_at(step["src"])
                    copy = self.os.dot / "cache" / "undo" / str(entry.get("blobs") or "") \
                        / str(step.get("blob") or "")
                    if path.exists() or path.is_symlink() or not copy.is_file():
                        failed.append(step["src"])
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    copy_whole(copy, path)
                    restored += 1
                elif step["action"] == "rmdir" and "mark" in step:
                    # A group a close took away once it was empty: made again
                    # with its mark, before what was in it goes back. Without
                    # the mark it would look like a folder somebody made, and
                    # sort would never touch it again. A folder of that name
                    # there by now is somebody's, and is left as it is.
                    path = self.os.root / step["src"]
                    if not path.exists() and not path.is_symlink():
                        path.mkdir(parents=True)
                        write_text(path / CATEGORY_MARKER, step["mark"])
                        restored += 1
            except OSError as exc:
                failed.append(f"{step.get('dst') or step.get('src', '?')}: {exc}")
                refused = True
            finally:
                if len(failed) == said:
                    done_steps.add(n)
        # Where something didn't come back: words for a file in there would
        # go into whatever has its name now, so they wait, kept (pre-merge
        # reviews of round 23).
        not_back = [str(st.get("src") or "") for n, st in enumerate(entry["steps"])
                    if n not in done_steps and st.get("action") in ("move", "remove")]
        held: dict[str, str] = {}

        def waits(rel: str) -> bool:
            return any(was and (rel == was or rel.startswith(was + "/")) for was in not_back)

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
            if waits(rel):
                held[rel] = blob
                continue
            source = blobs / blob
            # Through a shortcut only to a file inside this folder: one that
            # points outside by now is never written (seventh review,
            # 2026-10-29).
            named = self.os.root / now_at(rel)
            real = (entry.get("reals") or {}).get(rel)
            if real and now_at(rel) == rel and os.path.realpath(named) != real:
                # Back into the file it really was, if that is still a plain
                # file in this folder; never wherever the name leads now.
                named = Path(real)
                if named.is_symlink() or not named.is_file() or not inside(named, whole=True):
                    failed.append(f"{rel}: a shortcut that leads somewhere else now")
                    continue
            target = through_link(named)
            if not source.exists() or not target.is_file():
                continue
            if target.is_symlink() or not inside(target, whole=True):
                failed.append(f"{now_at(rel)}: a shortcut to somewhere outside this folder now")
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
                    keep(target, now_at(rel))
                target.write_bytes(older)
                restored += 1
            except OSError as exc:
                failed.append(f"{rel}: {exc}")
                held[rel] = blob
                refused = True
        # A shortcut the step wrote over with a plain file goes back to being
        # the shortcut. Undo said it had reversed everything and left the
        # plain copy (eighth review, 2026-10-29).
        for rel, text in (entry.get("links") or {}).items():
            if waits(rel):
                continue
            path = self.os.root / now_at(rel)
            try:
                if path.is_symlink() or not path.is_file() or not inside(path):
                    continue
                if isinstance(after, dict) and rel in after and content_print(path) != after[rel]:
                    keep(path, now_at(rel))
                path.unlink()
                path.symlink_to(text)
                restored += 1
            except OSError as exc:
                failed.append(f"{rel}: {exc}")
        # A step that copied nothing aside has no folder of its own, and `blobs`
        # is then the folder holding every other step's copies.
        kept_copies = ""
        if entry.get("blobs") and (refused or held) and blobs.is_dir():
            # Not thrown away with the step: they were the only copy of the
            # words before it (seventeenth review, round 22). Out of
            # .os/cache/undo, which the next command sweeps (pre-merge review
            # of round 23), with a note of which copy was which file.
            # Renamed where it is, which needs no room on a full disk; a new
            # folder elsewhere did, and failing, they were swept anyway.
            home = unique_path(blobs.with_name(blobs.name + NOT_PUT_BACK))
            try:
                os.rename(blobs, home)
            except OSError:
                # Named as kept instead, so nothing sweeps them, then or 20
                # commands on (seventh pre-merge review of round 23).
                self.os.state["kept_copies"] = (self.os.state.get("kept_copies") or []) + [blobs.name]
                home = blobs
            kept_copies = self.rel_of(home)
            try:
                lines = [f"{blob}  was  {rel}" for rel, blob in sorted(held.items())]
                lines += [f"{st.get('blob')}  was  {st.get('src')}" for n, st in enumerate(entry["steps"])
                          if n not in done_steps and st.get("action") == "remove" and st.get("blob")]
                if lines:
                    write_text(home / "WHAT-THESE-ARE.txt",
                               f"Files as they were before '{entry.get('label')}', which undo couldn't "
                               "put back. Each line names a copy here and the file it was:\n\n"
                               + "".join(line + "\n" for line in lines))
            except OSError:
                pass
        elif entry.get("blobs"):
            shutil.rmtree(blobs, ignore_errors=True)

        # What went back into the staging area is what the person took back.
        # `os sort` reads this and leaves it alone; see `Sorter.file_staged`.
        # A save's own copy is not among them: undo took that away.
        back = [self.rel_of(self.os.root / landed.get(st["src"], st["src"]))
                for st in entry["steps"] if st["action"] == "move" and not st.get("brought")
                and str(st["src"]).startswith(f"{MARKER}/cache/{STAGING}/")]
        if back:
            self.os.state["taken_back"] = (
                (self.os.state.get("taken_back") or []) + back)[-60:]

        self.os.state["undo"] = undo
        self.os.save_state()
        # (also not journalled — see Zenith.commit)
        return {"label": entry["label"], "restored": restored, "failed": failed,
                "kept": kept, "kept_copies": kept_copies}


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


#: The git that History runs. ZENITH_GIT stands in for it, so the checks can
#: try a computer that has none.
GIT = os.environ.get("ZENITH_GIT") or "git"
#: Where the published template lives. A folder downloaded with `git clone`
#: carries its history, and that one is the template's own.
TEMPLATE_REPO = "zidery333/os-template"


class History:
    """The folder's own history, kept with git, so an edit made by hand can be
    taken back to how it stood at the last checkpoint.

    `./os undo` only reverses what ./os itself did. A line deleted from About
    me by hand was gone for good, nothing ever saved, and `./os help` still said
    every change could be undone (stranger test, 2026-09-30). So the first
    `./os` in a new folder starts a history, the way main's /setup does, and
    /wrapup ends each session with `./os checkpoint`. A zip from `./os backup`
    wasn't enough: it copies all the footage every time.

    Footage in Work/Content is left out by .gitignore, and so are the usual
    names for keys and passwords, with a second look at names for the ones it
    missed. A code project with a git of its own keeps its files there. It
    only ever writes in a history ./os started itself: one that came with a
    `git clone` of the template, one the person keeps themselves, and a bigger
    one around this folder (a home folder kept in git, which every save would
    take whole) are left alone."""

    #: Set in .git/config when ./os starts the history: it's ours to write in.
    MARK = "zenith.history"
    #: Why the last save failed, kept beside it until one works: what the
    #: brief says instead of promising the next checkpoint will start it.
    FAILED = "zenith.failed"
    #: Pointed at the wrong history, every command here would write in it: a
    #: git hook running ./os sets these for the repository it belongs to.
    NOT_OURS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_PREFIX",
                "GIT_NAMESPACE")
    #: Names that hold keys or passwords, for when .gitignore missed one: the
    #: list main's nightly save leaves out. Once in the history, a key stays
    #: there after the file is deleted. Only a file's own name is read, so a
    #: folder called "keeping secrets" is a subject, not a leak.
    PRIVATE = re.compile(r"^\.env|\.(pem|p12|kdbx)$|_rsa$|^id_(dsa|ecdsa|ed25519)$|^\.netrc$"
                         r"|^token\.json$|^api[-_]?keys?\.|^service[-_]?account.*\.json$")
    #: "secret", "credential" or "password" anywhere in a name, except a
    #: note's: ./os names a note after its title, and "Secret Santa" is a
    #: plan, not a key. `./os help checkpoint` promised names like a password,
    #: and passwords.txt still went in (review, 2026-09-30).
    PRIVATE_WORDS = re.compile(r"secret|credential|password")
    #: What `_git` says when git ran out of time and was stopped.
    SLOW = 124
    TOO_SLOW = ("git took more than two minutes to take it all in — big files "
                "belong in Work/Content, which the history leaves out")

    @staticmethod
    def first_wait() -> float:
        """How long the first run gives git to take in what's already here.

        The start-of-chat check stops everything at 15 seconds. 382 MB of
        photos took the first run 9 seconds, and one stopped partway left
        git's lock behind, so every checkpoint after it failed (review,
        2026-09-30). Past this, the first checkpoint starts it instead.
        ZENITH_HISTORY_WAIT sets it, so the checks need not wait it out."""
        try:
            return max(0.1, float(os.environ.get("ZENITH_HISTORY_WAIT") or 8))
        except ValueError:
            return 8.0

    def __init__(self, os_: "Zenith"):
        self.os = os_
        self.root = os_.root

    @staticmethod
    def no_git() -> bool:
        """No git to run. On a Mac without Apple's developer tools, git is only
        a stand-in that pops up an install box, so it counts as none: the same
        test ./os makes of Python before it starts."""
        if not shutil.which(GIT):
            return True
        if sys.platform == "darwin" and GIT == "git":
            try:
                return subprocess.run(["xcode-select", "-p"], capture_output=True,
                                      timeout=10).returncode != 0
            except (OSError, subprocess.SubprocessError):
                return True
        return False

    @staticmethod
    def get_git() -> str:
        """How to get git, in one line a person can follow."""
        if sys.platform == "darwin":
            return "type  xcode-select --install  to add it"
        return "install git to add it"

    #: What only reads a history. Anything else changes one.
    READS = {"rev-parse", "rev-list", "ls-files", "diff", "diff-tree", "check-ignore",
             "log", "show", "status", "cat-file"}

    def own_dir(self) -> bool:
        """Whether this folder's .git is a history kept inside it.

        A `git worktree`, a submodule or a .git linked from elsewhere has only
        a pointer here, and the history it points at belongs to another
        folder. A test run in a worktree of this workshop removed the real
        folder's GitHub link and moved its main (2026-10-29)."""
        git = self.root / ".git"
        if git.is_symlink() or not git.is_dir() or (git / "commondir").exists():
            return False
        try:
            return git.resolve().parent == self.root.resolve()
        except OSError:
            return False

    def _reads(self, args: tuple) -> bool:
        words = [a for i, a in enumerate(args) if not (a == "-c" or (i and args[i - 1] == "-c"))]
        if not words:
            return True
        cmd, rest = words[0], words[1:]
        if cmd in self.READS:
            return True
        if cmd == "config":
            return not rest[1:] or any(a in ("--get", "--get-all", "--list", "-l") for a in rest)
        if cmd == "remote":
            return not rest or rest[0] in ("get-url", "-v", "show")
        if cmd == "symbolic-ref":
            return len([a for a in rest if not a.startswith("-")]) <= 1
        return False

    def _git(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        # A change goes only in a history kept in this folder, or in the one
        # `git init` starts in it.
        if not self._reads(args) and not self.own_dir() and not (
                args[:1] == ("init",) and not os.path.lexists(self.root / ".git")):
            return subprocess.CompletedProcess(
                list(args), 1, "", "error: this folder's history is kept in another folder")
        env = {k: v for k, v in os.environ.items() if k not in self.NOT_OURS}
        began = time.time() - 1
        try:
            return subprocess.run([GIT, "-C", str(self.root), *args], capture_output=True,
                                  text=True, errors="replace", env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            # Stopped, git leaves its lock behind, and every save after would
            # fail on it. The one made since this began was that git's own.
            lock = self.root / ".git" / "index.lock"
            try:
                if self.own_dir() and lock.stat().st_mtime >= began:
                    lock.unlink()
            except OSError:
                pass
            return subprocess.CompletedProcess(list(args), self.SLOW, "", "git took too long")
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(list(args), 1, "", str(exc))

    def where(self) -> str:
        """'no git', 'none' (no history yet), 'ours', 'template' (it came with
        a git clone, and nothing of theirs is in it yet), 'theirs' (one ./os
        didn't start) or 'inside' (this folder sits inside a bigger one)."""
        if self.no_git():
            return "no git"
        if os.path.lexists(self.root / ".git") and not self.own_dir():
            return "theirs"
        if (self.root / ".git").exists():
            if self._git("config", "--local", "--get", self.MARK).stdout.strip() == "true":
                return "ours"
            origin = self._git("remote", "get-url", "origin").stdout.strip()
            cloned = re.search(r"[:/]" + re.escape(TEMPLATE_REPO) + r"(?:\.git)?/?$", origin)
            if not cloned:
                return "theirs"
            # The template's, only while it holds nothing past the download.
            # Once they save in it themselves it's theirs: read as the
            # template's, every brief told them their changes weren't going in
            # it, and to  rm -rf .git , which would have thrown their own
            # saves away (review, 2026-09-30). On any branch or in a stash, not
            # only the one open: saves on a branch of their own still got told
            # to rm -rf .git.
            ahead = self._git("rev-list", "--count", "--all", "--not", "--remotes").stdout
            return "template" if ahead.strip() == "0" else "theirs"
        if self._git("rev-parse", "--show-toplevel").returncode == 0:
            return "inside"
        return "none"

    def _commit(self, message: str) -> subprocess.CompletedProcess:
        # With no name set, a Mac makes one up from the computer's, and Linux
        # refuses to save at all; so the one they gave here, or "me". Their
        # own hooks and signing are for their code, not their notes.
        named = all(self._git("config", key).stdout.strip() for key in ("user.name", "user.email"))
        who = [] if named else ["-c", f"user.name={self.os.config.get('owner') or 'me'}",
                                "-c", "user.email=me@localhost"]
        return self._git(*who, "-c", "commit.gpgsign=false", "commit", "-q",
                         "--no-verify", "-m", message)

    @staticmethod
    def _why(proc: subprocess.CompletedProcess) -> str:
        """git's reason, in its own words: the first "error:" line, which
        names the file, over the last, which only says it gave up. A file it
        wasn't allowed to read was "fatal: adding files failed", and nobody
        could tell which (review, 2026-09-30)."""
        said = [line.strip() for line in (proc.stderr or proc.stdout or "").splitlines()
                if line.strip()]
        errors = [line[len("error:"):].strip() for line in said if line.startswith("error:")]
        if errors:
            locked = re.match(r'open\("(.+)"\): Permission denied$', errors[0])
            return f"{locked.group(1)} can't be read, as this computer won't let git open it" \
                if locked else errors[0]
        return said[-1] if said else "git said no"

    @classmethod
    def looks_private(cls, name: str) -> bool:
        name = name.rsplit("/", 1)[-1].lower()
        return bool(cls.PRIVATE.search(name)) or (
            not name.endswith(".md") and bool(cls.PRIVATE_WORDS.search(name)))

    def _add(self, timeout: float = 120) -> tuple[subprocess.CompletedProcess, dict]:
        """Everything as it is now, ready for the next save, but for two kinds
        of thing, which it names.

        A code project with a git of its own keeps its files there. One set up
        with nothing saved in it yet made `git add` fail for the whole folder,
        so no checkpoint was ever kept again (review, 2026-09-30). And a file
        named like a key or a password, if .gitignore missed it."""
        # git lists a folder with a git of its own as one name ending in /.
        found = self._git("ls-files", "--others", "--exclude-standard", "-z")
        # A crash can garble git's list of what was taken in (.git/index).
        # Every checkpoint after it failed with "bad signature 0x00000000",
        # said in every brief, while ./os check said all good (review,
        # 2026-09-30). The list holds nothing that isn't in the files and
        # the last save, so it's made again from them.
        if found.returncode != 0 and re.search(
                r"index file corrupt|bad signature|index file smaller|bad index", found.stderr):
            (self.root / ".git" / "index").unlink(missing_ok=True)
            found = self._git("ls-files", "--others", "--exclude-standard", "-z")
        found = found.stdout
        own = sorted(n.rstrip("/") for n in found.split("\0") if n.endswith("/"))
        # Footage in Work/Content stays out even where a .gitignore of their
        # own doesn't list it, as one set up by hand usually doesn't.
        # Named only when git doesn't already leave it out: git refuses to
        # take in anything when told to skip a folder it ignores anyway.
        media = [f"{b}/{MEDIA_FOLDER}" for b in self.os.buckets()
                 if (self.root / b / MEDIA_FOLDER).is_dir()]
        media = [m for m in media if self._git("check-ignore", "-q", "--", m).returncode != 0]
        added = self._git("add", "-A", "--", ".", *(f":(exclude,literal){n}" for n in own + media),
                          timeout=timeout)
        if added.returncode != 0:
            return added, {}
        staged = self._git("diff", "--cached", "--name-only", "--diff-filter=d", "-z").stdout
        private = [n for n in staged.split("\0") if n and self.looks_private(n)]
        if private:
            spec = [f":(literal){n}" for n in private]
            # The second is for a history with nothing saved in it yet.
            if self._git("reset", "-q", "--", *spec).returncode != 0:
                self._git("rm", "-q", "--cached", "--ignore-unmatch", "--", *spec)
        return added, {"own": own, "private": private}

    def _begin(self, message: str, timeout: float = 120) -> dict:
        """Start one where there was none, holding everything as it is now."""
        done = self._git("init", "-q")
        # Marked before anything is added: a first run stopped partway (the
        # start-of-chat check gives it 15 seconds) leaves one the next
        # checkpoint finishes, not one it takes for somebody else's.
        if done.returncode == 0:
            done = self._git("config", self.MARK, "true")
        if done.returncode != 0:
            return {"result": "failed", "why": self._why(done)}
        # Not taken back when the rest fails: marked, with nothing saved in
        # it, it is what the next checkpoint finishes (see keep). ./os never
        # deletes a .git.
        return self._save(message, timeout, first=True)

    def _head(self) -> bool:
        """Whether there is a last save for the next one to follow."""
        return self._git("rev-parse", "--verify", "-q", "HEAD^{commit}").returncode == 0

    def _lost(self) -> dict:
        """A last save git has lost track of: the file naming it is there but
        empty or garbled, which a crash in the middle of a save can leave, or
        gone while git's record of the branch says it had saves.

        It used to be read as "nothing saved yet", and the whole history was
        deleted and started again: every earlier checkpoint gone, with the
        record that could have found them (review, 2026-09-30). git's own
        commands refuse to touch a branch in this state, so what's found here
        is the save it last pointed at, for the one line that puts it back."""
        git = self.root / ".git"
        try:
            head = (git / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return {}
        ref = head[len("ref:"):].strip() if head.startswith("ref:") else ""
        if not ref.startswith("refs/heads/") or ".." in ref:
            return {}
        # Neither is a branch nothing was saved on yet: a first run cut off.
        try:
            had = (git / ref).is_file() or (git / "logs" / ref).stat().st_size > 0
        except OSError:
            had = False
        if not had:
            return {}
        last = ""
        for record in (git / "logs" / ref, git / "logs" / "HEAD"):
            try:
                lines = record.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            # Newest first, the first save git can read whole: the one the
            # branch names may be there with what it holds emptied.
            for line in reversed(lines):
                sha = (line.split() + ["", ""])[1]
                if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) and self._whole(sha):
                    last = sha
                    break
            if last:
                break
        return {"ref": ref, "last": last}

    def _looks_fine(self) -> bool | None:
        """Whether the branch plainly names a save that is whole, read
        straight off the disk: the check runs on every ./os, and this spares
        it three gits. None when that can't be told from here: git has
        packed the save away, which it only does with ones it wrote whole.

        Only the name was read. A crash more often leaves the save's own file
        empty, and that was taken as fine: the checkpoint and every brief said
        to run ./os check --fix, which found nothing to fix (review,
        2026-09-30). Then the save was read and not what it holds: with the
        list of its files emptied, every checkpoint after said nothing had
        changed and kept nothing, while check said all good (review, the
        same day). So that list is read too."""
        git = self.root / ".git"
        try:
            head = (git / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return True         # no history here, or not one to look inside
        if not head.startswith("ref:"):
            return True         # a save picked by hand, not a branch
        ref = head[len("ref:"):].strip()
        try:
            named = (git / ref).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            try:
                packed = (git / "packed-refs").read_text(encoding="utf-8", errors="replace")
            except OSError:
                return False
            named = next((line.split()[0] for line in packed.splitlines()
                          if line.endswith(" " + ref)), "")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", named):
            return False
        wants = b"commit "
        while True:
            try:
                saved = (git / "objects" / named[:2] / named[2:]).read_bytes()
            except FileNotFoundError:
                return None     # packed away
            except OSError:
                return False
            try:
                start = zlib.decompressobj().decompress(saved, 128)
            except zlib.error:
                return False
            if not start.startswith(wants):
                return False
            if wants == b"tree ":
                return True
            listed = re.match(rb"commit \d+\0tree ([0-9a-f]{40}|[0-9a-f]{64})\n", start)
            if not listed:
                return False
            named, wants = listed.group(1).decode(), b"tree "

    def _whole(self, save: str) -> bool:
        """Whether git can read a save and everything in it."""
        return self._git("rev-list", "--objects", "--no-walk", save).returncode == 0

    #: How git names the file of one thing it saved, in .git/objects/<2>/.
    SAVED_FILE = re.compile(r"[0-9a-f]{38}|[0-9a-f]{62}")

    def _emptied(self) -> list:
        """git's files of what it saved that a crash left empty.

        git never writes one empty, so each is a crash's; and while one is
        there, git takes it as written and never writes it again. With only
        the files of what a checkpoint took in emptied, the save after it
        took them as they were, and could never be read back, while check
        said all good (review, 2026-09-30). One look at each, on every ./os."""
        found = []
        try:
            for fan in os.scandir(self.root / ".git" / "objects"):
                if len(fan.name) != 2 or not fan.is_dir(follow_symlinks=False):
                    continue
                for part in os.scandir(fan.path):
                    if self.SAVED_FILE.fullmatch(part.name) \
                            and part.stat(follow_symlinks=False).st_size == 0:
                        found.append(Path(part.path))
        except OSError:
            pass
        return found

    def _never_saved(self) -> bool:
        """Whether git holds no save it could ever read back: a crash during
        the very first one. Read off the disk, since git's own commands stop
        at the first empty file. Anything packed away was a save once."""
        objects = self.root / ".git" / "objects"
        try:
            if any((objects / "pack").glob("*.pack")):
                return False
            fans = [f for f in os.scandir(objects) if len(f.name) == 2
                    and f.is_dir(follow_symlinks=False)]
            for fan in fans:
                for part in os.scandir(fan.path):
                    if not self.SAVED_FILE.fullmatch(part.name) \
                            or part.stat(follow_symlinks=False).st_size == 0:
                        continue
                    try:
                        with open(part.path, "rb") as fh:
                            start = zlib.decompressobj().decompress(fh.read(64), 16)
                    except (OSError, zlib.error):
                        continue
                    if start.startswith(b"commit ") and self._whole(fan.name + part.name):
                        return False
        except OSError:
            return False
        return True

    def lost_track(self) -> dict:
        """The branch and its last save, when a history ./os keeps has lost
        track of it, or of what it holds; otherwise nothing. `emptied`: the
        save the branch names can still be read, and only other files of
        git's are empty. `first`: a crash during its very first save, so
        there is no save to go back to, and nothing in it to lose."""
        fine, emptied = self._looks_fine(), bool(self._emptied())
        if (fine and not emptied) or self.where() != "ours":
            return {}
        head = self._head() and (fine is None or self._whole("HEAD"))
        if head and not emptied:
            return {}
        lost = self._lost()
        if not head and not lost.get("last") and (emptied or lost) and self._never_saved():
            return {"ref": lost.get("ref", ""), "last": "", "emptied": True, "first": True}
        return {**lost, "emptied": True} if lost and head else lost

    def start_again(self) -> dict:
        """Clear what a crash during the very first save left, and make that
        save again (./os check --fix). Only git's own files go: the empty
        ones, its list of what was taken in, and the branch, which names no
        save. No file of theirs changes. Says how the save went.

        Left as they were, ./os and check said all good, and every checkpoint
        after failed on git's words about an empty file, said in every brief
        (review, 2026-09-30)."""
        git = self.root / ".git"
        for part in self._emptied():
            part.unlink(missing_ok=True)
        (git / "index").unlink(missing_ok=True)
        try:
            head = (git / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            head = ""
        ref = head[len("ref:"):].strip() if head.startswith("ref:") else ""
        if ref.startswith("refs/heads/") and ".." not in ref:
            # Its records too: they name no save that can be read, or one
            # would have been gone back to instead (_lost).
            for gone in (git / ref, git / "logs" / ref, git / "logs" / "HEAD"):
                gone.unlink(missing_ok=True)
        got = self._save("The folder as it was first opened", first=True)
        return self._remember(got if got["result"] != "later" else
                              {"result": "failed", "why": self.TOO_SLOW})

    def put_back(self, lost: dict) -> bool:
        """Point the branch at the last save git's own record of it names
        that can still be read (./os check --fix). No file of theirs changes.

        git doesn't make sure its newest files reach the disk, so a crash in
        a checkpoint often leaves several of them empty, not just the save:
        the files of what was taken in too. While an empty one is there, git
        takes it as written and never writes it again, and every checkpoint
        after failed on it. An empty file holds nothing to lose, so those go,
        and so does git's list of what was taken in, which named them: the
        next checkpoint takes every file in again. Only then is the save to
        go back to picked: git reads an emptied file of one as if it were
        there, and a gone one as gone. Says whether the branch moved."""
        git = self.root / ".git"
        was = self._git("rev-parse", "-q", "--verify", "HEAD").stdout.strip()
        emptied = False
        for part in self._emptied():
            try:
                part.unlink()
                emptied = True
            except OSError:
                pass
        if emptied:
            (git / "index").unlink(missing_ok=True)
        last = self._lost().get("last") or lost["last"]
        branch = git / lost["ref"]
        branch.parent.mkdir(parents=True, exist_ok=True)
        branch.write_text(last + "\n", encoding="utf-8")
        # git's record still named the saves that can't be read, and git's
        # own tidy-up stopped on them every time from then on ("failed to
        # run repack"). Only those lines go; every save in it stays named.
        self._git("reflog", "expire", "--expire=never", "--expire-unreachable=never",
                  "--stale-fix", "--all")
        return last != was

    def _changed(self, *commit: str) -> list:
        """What a commit holds, or with none given, what is about to go in."""
        names = self._git("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root",
                          *commit).stdout if commit else \
            self._git("diff", "--cached", "--name-only", "-z").stdout
        return [n for n in names.split("\0") if n]

    def _theirs(self, *commit: str) -> int:
        """How many of those are the person's own. ./os writes its bookkeeping
        in .os/ on every run, so counted in, every checkpoint said a file had
        changed when they had touched nothing."""
        return len([n for n in self._changed(*commit) if not n.startswith(MARKER + "/")])

    def start(self) -> str:
        """The first time ./os runs here: start one, unless it can't or shouldn't."""
        where = self.where()
        if where == "inside" and self._kept_around():
            return "kept around"
        if where != "none":
            return where
        began = self._begin("The folder as it was first opened", timeout=self.first_wait())
        if began["result"] in ("started", "later"):
            return began["result"]
        self._remember(began)
        return f"failed: {began['why']}"

    def _kept_around(self) -> bool:
        """Whether the bigger history around this folder already keeps its
        files. Then it's on purpose, and a hand edit can go back in that one:
        the brief still said, every session, that it kept none, and offered
        --here, which would have hidden these files from it (review,
        2026-09-30)."""
        return bool(self._git("ls-files", "-z", "--", ".").stdout)

    def _remember(self, got: dict) -> dict:
        """Keep why a save failed beside the history, until one works.

        After a first start that failed, the brief said "the first ./os
        checkpoint starts it", and every checkpoint failed the same way
        (review, 2026-09-30). This is what it says instead."""
        if got["result"] == "failed":
            self._git("config", self.FAILED, one_line(got.get("why") or "git said no"))
        elif got["result"] in ("kept", "started", "nothing"):
            self._git("config", "--unset", self.FAILED)
        return got

    def keep(self, message: str, here: bool = False) -> dict:
        """A checkpoint: everything as it is now, with what changed since the last.

        `here` starts one for this folder alone when it sits inside a bigger
        one: never on its own, only when the person asks for it."""
        where = self.where()
        if where == "inside" and here:
            where = "none"
        if where == "none":
            began = self._begin(message)
            return self._remember(began if began["result"] != "later" else
                                  {"result": "failed", "why": self.TOO_SLOW})
        if where == "inside":
            return {"result": where, "around": self._kept_around()}
        if where == "theirs" and linked_to_template(self.os):
            # Still a git clone of the template underneath: a save here is
            # one push away from the template's own page.
            return {"result": where}
        if where == "theirs":
            # A history they set up by hand is theirs to keep, and asking for a
            # checkpoint is asking for a save in it: it was refused, so a
            # folder kept in git by hand never had one (2026-10-29). Not while
            # git is partway through something of theirs, where a save would
            # land in the middle of it.
            busy = self._busy()
            if busy:
                return {"result": "busy", "why": busy}
        elif where != "ours":
            return {"result": where}
        first = not self._head()
        lost = self.lost_track()
        if lost:
            return {"result": "lost", **lost}
        lock = self.root / ".git" / "index.lock"
        try:
            # Left by a save stopped partway, it stops every one after. No git
            # ./os runs is given two minutes, so one this old is nobody's; and
            # with nothing saved yet, it was left by a first run cut off (the
            # start-of-chat check gives it 15 seconds). A first run still
            # going holds ./os's own lock, so a checkpoint waits for it first.
            if first or time.time() - lock.stat().st_mtime >= Lock.STALE_AFTER:
                lock.unlink()
        except OSError:
            pass
        # With nothing saved yet, this is its first save, made where it is.
        # It used to be deleted and started again, and a history whose last
        # save was only lost track of was deleted with it (review, 2026-09-30).
        got = self._save(message, first=first)
        return self._remember(got if got["result"] != "later" else
                              {"result": "failed", "why": self.TOO_SLOW})

    def _busy(self) -> str:
        """What git is partway through here, in words, or "" when nothing."""
        git = self.root / ".git"
        # A .git linked from elsewhere passed as a folder, and a save then
        # cleared the other history's files by hand (round 20 review).
        if not self.own_dir():
            return "this folder's .git is a pointer to another one"
        for name, what in (("MERGE_HEAD", "a merge"), ("rebase-merge", "a rebase"),
                           ("rebase-apply", "a rebase"), ("CHERRY_PICK_HEAD", "a cherry-pick"),
                           ("REVERT_HEAD", "a revert"), ("BISECT_LOG", "a bisect")):
            if (git / name).exists():
                return f"git is in the middle of {what}"
        if self._git("symbolic-ref", "-q", "HEAD").returncode != 0:
            return "git isn't on a branch here, so a save would be easy to lose"
        return ""

    def _save(self, message: str, timeout: float = 120, first: bool = False) -> dict:
        """Take in everything, and save it. `first`: there is no last save, so
        it says "started", never "changed since the last one" (review,
        2026-09-30: a first run stopped partway said 67 files had)."""
        added, left = self._add(timeout)
        if added.returncode == self.SLOW:
            return {"result": "later"}
        if added.returncode != 0:
            if (self.root / ".git" / "index.lock").exists():
                return {"result": "failed", "why": "git is busy in this folder, or was "
                        "stopped partway in the last 15 minutes — try again after that"}
            return {"result": "failed", "why": self._why(added)}
        if not first:
            # A failed look was read as no change: with the last save's list
            # of files emptied by a crash, every checkpoint said "nothing has
            # changed since the last one" and kept nothing (review,
            # 2026-09-30). One that can't be compared isn't kept.
            since = self._git("diff", "--cached", "--quiet")
            if since.returncode not in (0, 1):
                return {"result": "failed", "why": self._why(since)}
            if since.returncode == 0:
                return {"result": "nothing", **left}
        files = self._theirs()
        done = self._commit(message)
        if done.returncode == self.SLOW:
            return {"result": "later"}
        if done.returncode != 0:
            return {"result": "failed", "why": self._why(done)}
        if first:
            return {"result": "started", "files": files, **left}
        return {"result": "kept" if files else "nothing", "files": files, **left}

    def first_words(self, began: str) -> str:
        """The one line the first run says when it couldn't start one."""
        if began == "no git":
            return ("this folder can't keep a history of your changes yet, as there's no "
                    f"git on this computer — {self.get_git()}")
        if began == "template":
            return ("this folder's history came with the download, so your changes "
                    "won't go in it — to keep your own:  ./os setup --unlink --fresh")
        if began == "inside":
            return ("this folder sits inside a bigger folder's history, so it keeps none of "
                    "its own — to keep one for this folder alone:  ./os checkpoint --here")
        if began == "later":
            return ("there's a lot in this folder, so the history of your changes "
                    "starts at the first  ./os checkpoint  instead")
        if began == "none":
            return ("nothing is in the history of your changes yet — the first  "
                    "./os checkpoint  starts it")
        if began == "lost":
            return ("this folder's history has lost track of its last save, so nothing "
                    "more goes in it until that's put back —  ./os check --fix")
        if began == "emptied":
            return ("a crash while saving left some of this folder's history empty, so "
                    "nothing more goes in it until that's put right —  ./os check --fix")
        if began.startswith("failed"):
            return f"couldn't start a history of your changes ({began[8:]})"
        # "kept around": the bigger history keeps this folder's files, which
        # is somebody's plan, so there's nothing to say.
        return ""

    def standing(self) -> str:
        """That line, for as long as it's true: what the brief carries.

        In Claude Code the start-of-chat check is the first run, and it asks
        for the brief, so the line the first run prints was never seen: a
        git clone, no git, or a big folder went unsaid until /wrapup, and a
        later ./os said nothing either (review, 2026-09-30)."""
        where = self.where()
        if where == "ours":
            failed = self._git("config", "--local", "--get", self.FAILED).stdout.strip()
            lost = self.lost_track()
            if lost:
                return self.first_words("emptied" if lost.get("emptied") else "lost")
            if self._head():
                return (f"the last  ./os checkpoint  didn't work ({failed}), so hand edits "
                        "since the one before it can't go back yet") if failed else ""
            return self.first_words(f"failed: {failed}" if failed else "none")
        if where == "inside" and self._kept_around():
            return ""
        return self.first_words(where)


class Twins:
    """A second write-up of something already written down.

    Matching on the name does not work: the old template filed a write-up
    called `roediger-karpicke-testing-effect` beside one called
    `retrieval-practice`, the same study with no word in common. What two
    write-ups of one thing cannot avoid repeating is its figures and its
    names: the people, the date, the numbers. So that is what is compared,
    the way the old template's no-second-copy check did.

    It only ever warns. Two things really can share a number and a name, and
    a second note from a new angle is sometimes right. To keep false alarms
    down, ordinary things don't count: a number under 100, a weight like
    `200g`, a year on its own, a word that opens a sentence (`Preheat`), or a
    name that turns up all over the folder (their own, say). And it takes a
    name plus figures, never figures alone: two recipes share `200` and `180`
    all the time."""

    MONTHS = ("january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december")
    #: Capitalised words that are not names of anything.
    NOT_NAMES = {
        "the", "this", "that", "these", "those", "they", "their", "there", "then",
        "and", "but", "not", "for", "with", "from", "what", "when", "where", "who",
        "why", "how", "yes", "okay", "note", "notes", "todo", "next", "action",
        "decision", "decisions", "log", "open", "questions", "where", "stands",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "today", "tomorrow", "yesterday", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
        *MONTHS,
    }
    FIGURE = re.compile(
        r"(?<![\w.])(?:[£$€]\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m|bn)?\b"       # £40,000  $3.5m
        r"|\d[\d,]*(?:\.\d+)?%"                                            # 61%  28.64%
        r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])"                           # 3,733
        r"|\d+\.\d+(?![\w.])"                                               # 28.64
        r"|\d{3,}(?![\w.,%]))", re.I)                                        # 1450
    _MONTH = "|".join([*MONTHS, "sept", *(m[:3] for m in MONTHS)])
    DATE = re.compile(
        r"\b(?:(\d{4})-(\d{1,2})-(\d{1,2})"
        r"|(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+(" + _MONTH + r")\b\.?"
        r"|(" + _MONTH + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?)\b", re.I)
    NAME = re.compile(r"(?<![\w'’])([A-Z][a-z]+(?:[A-Z][a-z]+)?|[A-Z]{2,6})(?:['’]s)?(?![\w'’-])")
    #: What can come right before a sentence starts, on the same line.
    OPENS = re.compile(r"(?:^|[.!?:;\u2014\u2013\"\u201c(]\s*|^\s*(?:[-*+>#|]+\s*|\d+[.)]\s*|\[[ xX]\]\s*)+)$")
    #: Read this much of each note: a write-up says what it is about early.
    READ = 60_000

    def __init__(self, os_: "Zenith"):
        self.os = os_

    @classmethod
    def marks(cls, text: str) -> tuple[set, set]:
        """(figures, names) that a write-up of the same thing would repeat."""
        _, body = parse_frontmatter(text)
        body = COMMENT_RE.sub(" ", body)
        body = re.sub(r"`[^`\n]*`", " ", body)
        body = re.sub(r"\]\([^)\n]*\)", "] ", body)            # where a link points
        body = re.sub(r"https?://\S+", " ", body)
        figures = set()
        for m in cls.DATE.finditer(body):
            # said the same way however it was written: 2026-03-12, 12th March
            # and March 12 are all "12 March"
            if m.group(1):
                month, day = int(m.group(2)), int(m.group(3))
            else:
                short = (m.group(5) or m.group(6)).lower()[:3]
                month = next(n for n, x in enumerate(cls.MONTHS, 1) if x.startswith(short))
                day = int(m.group(4) or m.group(7))
            if 1 <= month <= 12 and 1 <= day <= 31:
                figures.add(f"{day} {cls.MONTHS[month - 1].title()}")
        for m in cls.FIGURE.finditer(cls.DATE.sub(" ", body)):
            raw = m.group(0).lower().replace(" ", "")
            plain = raw.replace(",", "")
            digits = re.sub(r"\D", "", plain)
            if re.fullmatch(r"(19|20)\d\d", plain):
                continue        # a year alone: every note from this year shares it
            if "%" not in plain and not plain[0] in "£$€" and len(digits) < 3:
                continue        # 2.5, 12: everyday numbers
            figures.add(plain)
        names = set()
        for line in body.split("\n"):
            for m in cls.NAME.finditer(line):
                word = m.group(1)
                if word.lower() in cls.NOT_NAMES or len(word) < 3:
                    continue
                if cls.OPENS.search(line[:m.start()]):
                    continue    # the start of a sentence: any word is capitalised there
                names.add(word)
        return figures, names

    def _items(self):
        shelf = self.os.bucket_for_role("archive")
        for it in Scanner(self.os).scan():
            if it.kind not in ("note", "project") or it.bucket == shelf:
                continue
            spine = it.spine
            if not spine or not spine.is_file() or spine.suffix.lower() not in TEXT_SUFFIXES:
                continue
            yield it, read_text(spine, self.READ)

    @staticmethod
    def _match(a: tuple, b: tuple, common: set) -> list:
        """What a and b share, when it is enough to say so; else []."""
        figures = a[0] & b[0]
        names = (a[1] & b[1]) - common
        if not names or not figures or len(figures) + len(names) < 3:
            return []
        # A long note shares a few things with anything. Two lists of the same
        # channels, kept on the same days, shared a third of what each said and
        # were two different notes; a write-up said twice shares most of it.
        smaller = min(len(a[0]) + len(a[1]), len(b[0]) + len(b[1]))
        if (len(figures) + len(names)) < 0.4 * smaller:
            return []
        return sorted(names) + sorted(figures)

    @staticmethod
    def _common(marked: list, total: int) -> set:
        """Names said in so many notes they say nothing: the owner's own name,
        their town, the company they work for."""
        seen: dict = {}
        for _it, (_f, names) in marked:
            for n in names:
                seen[n] = seen.get(n, 0) + 1
        bar = max(6, total // 20)
        return {n for n, c in seen.items() if c >= bar}

    def like(self, text: str, skip: tuple = ()) -> list:
        """Things already here that `text` may be a second write-up of:
        [(item, [what they share])], the closest first."""
        new = self.marks(text)
        if len(new[0]) + len(new[1]) < 3 or not new[0] or not new[1]:
            return []
        skipped = {str(p) for p in skip}
        marked = [(it, self.marks(t)) for it, t in self._items()
                  if str(it.path) not in skipped and str(it.spine) not in skipped]
        common = self._common(marked, len(marked))
        hits = []
        for it, m in marked:
            shared = self._match(new, m, common)
            if shared:
                hits.append((len(shared), it, shared))
        hits.sort(key=lambda h: (-h[0], h[1].title))
        return [(it, shared) for _n, it, shared in hits]

    def like_title(self, title: str) -> list:
        """For `./os new learning`, which has only a name to go on: notes
        already naming two or more of the same people, places or figures."""
        figures = {f for f in self.marks("x " + title)[0]}
        names = {w for w in re.findall(r"[A-Z][a-z]+|[A-Z]{2,6}", title)
                 if w.lower() not in self.NOT_NAMES and len(w) >= 3}
        if len(figures) + len(names) < 2:
            return []
        marked = [(it, self.marks(t)) for it, t in self._items()]
        common = self._common(marked, len(marked))
        names -= common
        hits = []
        for it, (f, n) in marked:
            shared = sorted(names & n) + sorted(figures & f)
            if len(shared) >= 2:
                hits.append((len(shared), it, shared))
        hits.sort(key=lambda h: (-h[0], h[1].title))
        return [(it, shared) for _n, it, shared in hits]

    def pairs(self, limit: int = 20) -> list:
        """Every pair of things here that look like one write-up twice, for tidy.
        Compared only where they share a name, so it stays quick with thousands."""
        marked = [(it, self.marks(t)) for it, t in self._items()]
        common = self._common(marked, len(marked))
        by_name: dict = {}
        for n, (_it, (_f, names)) in enumerate(marked):
            for name in names - common:
                by_name.setdefault(name, []).append(n)
        seen, out = set(), []
        for holders in by_name.values():
            for i, a in enumerate(holders):
                for b in holders[i + 1:]:
                    if (a, b) in seen:
                        continue
                    seen.add((a, b))
                    one, two = marked[a][0], marked[b][0]
                    if one.path in two.path.parents or two.path in one.path.parents:
                        continue
                    shared = self._match(marked[a][1], marked[b][1], common)
                    if shared:
                        out.append((len(shared), one, two, shared))
        out.sort(key=lambda h: (-h[0], h[1].title, h[2].title))
        return [(one, two, shared) for _n, one, two, shared in out[:limit]]

    @staticmethod
    def say(shared: list, n: int = 4) -> str:
        return ", ".join(shared[:n]) + (" …" if len(shared) > n else "")


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
    #: Words saying something already does the job without them. A note that
    #: said "a scheduled task on the Mac does this every week on its own now"
    #: was listed as done by hand every time, with a skill offered for it.
    #: Plurals too: "my scheduled tasks do this" and "this is automated now"
    #: were still listed, and Scheduled Tasks is what Claude's apps call them.
    DONE_FOR_THEM = re.compile(r"\b(?:scheduled (?:tasks?|jobs?)|cron(?:tab| jobs?)?|launchd|on its own"
                               r"|by itself|runs itself|automat(?:ed|ically)|on a timer)\b", re.I)

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
        this says nothing. The cadence and step words live in .os/words.json,
        where somebody can teach it their own; the words saying something
        already does the job are DONE_FOR_THEM, above."""
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
            # Already done for them, by a timer or a scheduled task: no job for
            # a skill. When in doubt this says nothing, as above. Said in the
            # title, a heading or a line of its own, not inside a step: "2.
            # upload them to the app, which emails the client automatically"
            # is how one step goes, and hid a job they said they do by hand.
            said_done = [ln for ln in [it.title] + clean.split("\n") if not self.STEP_LINE.match(ln)]
            if self.DONE_FOR_THEM.search("\n".join(said_done)):
                continue
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

    def same_write_ups(self) -> list:
        """Two notes that look like one thing written up twice, by the
        figures and names they share (see Twins). Only said here, in the
        weekly pass, never counted against the folder by ./os check: two
        notes can share all that and both be right."""
        out = []
        for one, two, shared in Twins(self.os).pairs():
            out.append({"level": "hint", "code": "same-write-up",
                        "message": f"'{one.title}' and '{two.title}' both mention {Twins.say(shared, 3)}",
                        "pair": [one.title, two.title], "shares": shared,
                        "path": self.os.rel(two.path),
                        "fix": f"./os show {handle(one)}   ·   ./os show {handle(two)}"})
        return out

    def crowded(self) -> list:
        """Each of Notes and Work that `./os sort --regroup` would split into
        subject folders, and how many things it would move. Said here, in
        the weekly pass, and nowhere else: sort no longer does it on its own,
        and a folder that is only big is not a fault."""
        moved: dict[str, int] = {}
        for _what, src, _dst in Sorter(self.os, dry=True).regroup()["moves"]:
            top = src.split("/", 1)[0]
            moved[top] = moved.get(top, 0) + 1
        return [{"bucket": b, "would_move": n, "fix": "./os sort --regroup"}
                for b, n in sorted(moved.items())]

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

    def run(self, record: bool = False) -> dict:
        """What the weekly pass finds. `record`: this is the weekly pass
        itself, plain `./os tidy`, so the day it ran is written down. Asked
        for as JSON, by `./os --json` or the catch-up skill, it only looks:
        it wrote the day into .os/config.json, and a catch-up that changed
        nothing left a change behind in the folder's history (report,
        2026-10-22)."""
        items = self.scanner.scan()
        health = Doctor(self.os).run(items=items)
        stale_days = int(self.os.thresholds.get("stale_project_days", 30))
        dormant_days = int(self.os.thresholds.get("dormant_project_days", 75))
        held_days = int(self.os.thresholds.get("held_check_days", 180))

        shelf = self.os.bucket_for_role("archive")
        active = [i for i in items if i.kind == "project" and i.status in (PUSHING, "")]
        holding = [i for i in items if i.kind == "project" and i.status == HOLDING
                   and i.bucket != shelf]
        report = {
            "generated": now_iso(),
            "score": health["score"],
            "counts": {k: v for k, v in Indexer._counts(items).items()},
            "unfiled": [i.title for i in items if Sorter.unmanaged(i)]
                       + [f.name for f in loose_at_top(self.os.root, self.os.buckets())],
            "active": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in
                       sorted(active, key=lambda x: days_since(x.updated))],
            "stale": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in active
                      if stale_days <= days_since(i.updated) < dormant_days],
            "holding": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in
                        sorted(holding, key=lambda x: days_since(x.updated))],
            # Held work untouched for half a year: asked about here, in the
            # weekly pass, and nowhere else. Something paused or kept up never
            # came back up at all, even after a year (stranger test,
            # 2026-09-30). It is a question, not a fault: never stale in ./os,
            # never in ./os check, never off the health score. "Touched" is
            # what it is everywhere else: the header's `updated:` date, which
            # every ./os command on it moves (./os hold too), or the newest
            # dated Log line if that is later. A file's own date is only used
            # when the header has none: a copy or a checkout changes that.
            "still_keeping": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)}
                              for i in sorted(holding, key=lambda x: -days_since(x.updated))
                              if days_since(i.updated) >= held_days],
            # Only work being pushed can look abandoned. Something held is quiet
            # by design, so it is never offered up for the archive on age alone.
            "archive_candidates": [{"id": i.ident, "title": i.title, "age": days_since(i.updated)} for i in active
                                   if days_since(i.updated) >= dormant_days
                                   and i.bucket != shelf],
            "duplicates": [i for i in health["issues"] if i["code"] in ("duplicate-content", "near-duplicate")]
                          + self.same_write_ups(),
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
            "crowded": self.crowded(),
            "study_cache": self.study_cache(),
            "issues": health["issues"],
        }
        if record:
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
        values = {
            "{{ID}}": ident, "{{TITLE}}": title, "{{SLUG}}": slugify(title),
            "{{KIND}}": TYPE_ON_DISK.get(kind, kind), "{{STATUS}}": status or "—",
            "{{DATE}}": today(), "{{DOMAIN}}": domain or catch_all(self.os.taxonomy),
            "{{TAGS}}": ", ".join(tags), "{{YEAR}}": today()[:4],
            "{{OWNER}}": str(self.os.config.get("owner") or ""),
            "{{OS_NAME}}": str(self.os.config.get("name", "Zenith")),
        }
        # A header line that is one value with a name in it goes through
        # `_emit`, which quotes it when it has to be. `description: My
        # Skill: weekly.` wasn't a header Claude Code could read (audit,
        # 2026-10-29). Lists like `tags: [{{TAGS}}]` are left as they are.
        head = re.match(r"---\n.*?\n---\n", text, re.S)
        if head:
            lines = head.group(0).split("\n")
            for n, line in enumerate(lines):
                key, sep, rest = line.partition(": ")
                if sep and "{{" in rest and not rest.lstrip().startswith(("[", '"', "'")):
                    for k, v in values.items():
                        rest = rest.replace(k, one_line(v))
                    lines[n] = f"{key}: {_emit(rest, key)}"
            text = "\n".join(lines) + text[head.end():]
        for key, value in values.items():
            # Every blueprint puts {{TITLE}} on a front-matter line as well as
            # in a heading, so anything substituted in has to stay on the line
            # it lands on: `_emit` only guards values that go through it, and a
            # template substitution never does. See `one_line`.
            text = text.replace(key, one_line(value))
        return text

    def create(self, kind: str, title: str, domain: str = "", tags: list[str] | None = None,
               status: str = "", dry: bool = False) -> Path:
        """Make it, and say where. `dry` works out the same place and the same
        subject, and writes nothing: no file, no counter, no undo step."""
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
            # About me is a list of facts about them, each marked with how
            # sure it is. None of an ordinary note's headings fit that, so it
            # has a form of its own: templates/about-me.md.
            if not shape and slugify(title) == ABOUT_THEM:
                shape = ABOUT_THEM
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
                die(f"there's already a skill called {slug} at {self.os.rel(folder)} — "
                    "pick another name, or change that one by editing its SKILL.md")
            if dry:
                return folder / "SKILL.md"
            self.os.record("mkdir", self.os.rel(folder))
            write_text(folder / "SKILL.md", self.render("skill", title, "", domain, tags))
            self.os.created(folder / "SKILL.md")
            self.os.commit(f"new skill /{slug}")
            return folder / "SKILL.md"

        if kind == "agent":
            path = root / ".claude" / "agents" / f"{slug}.md"
            if path.exists():
                die(f"there's already a helper called {slug} — pick another name, or change "
                    f"that one by editing .claude/agents/{slug}.md")
            if dry:
                return path
            write_text(path, self.render("agent", title, "", domain, tags))
            self.os.created(path)
            self.os.commit(f"new agent {slug}")
            return path

        bucket = self.os.bucket_for_role(kind if kind == "project" else "note")
        ident = "" if dry else self.os.next_id(bucket)
        # Made in the folder it belongs in, under the name it keeps: sort
        # never moves or renames it after (Sorter.home). A note made with
        # ./os new, its path already given out, was moved into a group and
        # renamed by the next sort (reported, 2026-10).
        base = Sorter(self.os).home(bucket, domain, tags)
        used = names_in_group(root / bucket)
        if kind == "project":
            # The name on disk is the handle: plain words, nothing in front of them.
            # Born with the name a person would write on it.
            name = folder_name(title, slug)
            folder = base / name
            # `Content` is the media folder ./os never looks inside: work made
            # there was reported as started, and then nothing could find it.
            if folder.exists() or ignored(folder) or name_key(name, True) in used:
                n = 2
                while (base / f"{name}-{n}").exists() or name_key(f"{name}-{n}", True) in used:
                    n += 1
                folder = base / f"{name}-{n}"
            if dry:
                return folder / "README.md"
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

        # Cut where a word ends, the way sort and rename name a note.
        slug = slugify(title, 44, at_word=True)
        path = base / f"{slug}.md"
        if path.exists() or name_key(path.name, False) in used:
            n = 2
            while (base / f"{slug}-{n}.md").exists() or name_key(f"{slug}-{n}.md", False) in used:
                n += 1
            path = base / f"{slug}-{n}.md"
        if dry:
            return path
        write_text(path, self.render("note", title, ident, domain, tags, shape=shape))
        self.os.created(path)
        self.os.save_state()
        self.os.commit(f"new note — {title}")
        return path

    def stage(self) -> Path:
        """The staging area a capture passes through on its way to a folder."""
        stage = self.os.dot / "cache" / STAGING
        # Never made from nothing: a save that found the folder just before
        # it was moved made .os again at the old place, filed its words in
        # that shell and said SAVED; in one being moved, its words went with
        # the old place (round 21 review of the move).
        try:
            if not (self.os.dot / "config.json").is_file():
                raise FileNotFoundError
            (self.os.dot / "cache").mkdir(exist_ok=True)
            stage.mkdir(exist_ok=True)
        except OSError:
            die("this folder was moved or renamed just now, so nothing was saved here. "
                "Open the folder in its new place and save it again there.")
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
        # Something taken back with ./os undo keeps its name on a list the sort
        # won't file from. Saved again in the same second, new words got that
        # same name and sat unfiled; the demo lost all three of its own that way.
        refused = self.os.taken_back()
        # The name is claimed in one step that fails if it's taken. Two saves
        # in the same second both found it free, and the second wrote over
        # the first: 17 of 200 texts lost (audit, 2026-10-29). Claimed with
        # the words already in it, by linking a written copy: an empty claim
        # could be filed by a sort as an empty note (review, 2026-10-29).
        spare = self.stage() / f".capture-{os.getpid()}.tmp~"
        words = header + text.rstrip() + "\n"
        # Into the staging folder as it is, never one made again: set aside
        # by a move in between, this made .os again where the folder was,
        # and the save said SAVED there (round 21 pre-merge review).
        def put(where: Path) -> None:
            try:
                with open(where, "w", encoding="utf-8", newline="") as fh:
                    fh.write(words)
            except FileNotFoundError:
                die("this folder was moved or renamed just now, so nothing was saved here. "
                    "Open the folder in its new place and save it again there.")
        put(spare)
        try:
            for n in range(1, 10_000):
                landed = path if n == 1 else path.with_name(f"{path.stem}-{n}{path.suffix}")
                if self.os.rel(landed) in refused:
                    continue
                try:
                    if not spare.exists():
                        put(spare)                  # taken away meanwhile: written again
                    os.link(spare, landed)
                except FileExistsError:
                    continue
                except OSError:
                    # A drive with no hard links: claim the name empty, then
                    # write, as before. Never into a folder made again (round
                    # 21 pre-merge review).
                    try:
                        os.close(os.open(landed, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
                    except FileExistsError:
                        continue
                    except FileNotFoundError:
                        die("this folder was moved or renamed just now, so nothing was saved "
                            "here. Open the folder in its new place and save it again there.")
                    # The whole copy put in place in one step: written again
                    # here, a full disk or a stop partway left the note cut
                    # short, and the whole one was then taken away
                    # (nineteenth review, round 24).
                    if not spare.exists():
                        put(spare)
                    os.replace(spare, landed)
                break
        finally:
            try:
                spare.unlink()
            except OSError:
                pass
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

  {c1}THE ONES YOU'LL ACTUALLY USE{c0}
    ./os                           what's going on right now
    ./os save "<anything>"         write it down — I file it for you
    ./os find <words>              search everything you've ever saved
    ./os show <name>               one thing: state, next action, log
    ./os open <name>               show me where something is on disk
    ./os undo                      take back the last thing ./os did
    ./os decide <name> "<text>"    write a settled thing into its ## Decisions

  {c1}STARTING, AND STOPPING{c0}
    ./os new work "<name>"         start something you're pushing on
    ./os new ongoing "<name>"      start something you'll just keep up
    ./os new note "<name>"         write a note yourself · learning: a how-to
    ./os new skill "<name>"        a job your AI should do the same way · or helper
    ./os hold <name>               no next action, just keep it level
    ./os push <name>               back on the go · ./os note <name>  a note again
    ./os close <name>              no longer live — put it away in Archive/
    ./os back <name>               get it out of the archive again

  {c1}HOUSEKEEPING (rarely needed){c0}
    ./os sort                      file what you dropped in · --regroup groups by subject
    ./os check                     is anything broken?   --fix repairs it
    ./os tidy                      what's gone stale, doubled up or unfiled
    ./os backup · ./os checkpoint  a zip of it all · a save point for hand edits
    ./os update · ./os addon       the newest version · extra skills to add
    ./os edit · ./os rename        open it in your editor · give it a new name
    ./os claim · ./os release      tell other chats you're on it · let go again
    ./os demo · name · setup       a tour · your name on it · set it up with you

  {c1}IF YOU NEED IT{c0}
    ./os last · ./os learn         the last session here · a video's words
    ./os help <command>            more about any one (most take --json)
    ./os test · ./os snag          prove it still works · a fault in this folder
    ./os index · brief · words     rebuild the list · what your AI gets told · its words

  Nothing of yours is ever deleted, and every filing ./os does can be undone (./os undo).
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


def _opt_given(argv: list[str], name: str, example: str) -> str:
    """An option that means nothing without its value. Left off, `_opt` gave
    back "" and the command ran as if the option weren't there:
    `./os close hidden --part` put all of Hidden away (audit, 2026-10-29)."""
    there = name in argv or any(a.startswith(name + "=") for a in argv)
    value = _opt(argv, name)
    if there and not value.strip():
        die(f"{name} needs something after it, so nothing was changed.   {example}", 2)
    return value


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


def _one_name(os_: "Zenith", argv: list[str], verb: str) -> None:
    """Stop a command that takes one name when words come after it.

    They were thrown away without a word: `./os done shop "sold out, finished"`
    put Shop away and the words went nowhere, and `./os close Kitchen
    Renovation`, unquoted, put away Kitchen instead (stranger test,
    2026-09-30). Now they stop the run the way an option it doesn't know does,
    before anything changes, and it shows how to write a name with spaces.

    A bare -- says what comes after it is a name, whatever it looks like, so
    it's taken out of argv here and the name is argv[0] for the command too.
    Left in, `./os close -- garden` was told to try `./os close "-- garden"`
    (review, 2026-09-30)."""
    if "--" in argv:
        argv.remove("--")
        if not argv:
            die(f"which one?   ./os {verb} fix-the-boiler      (run ./os to see the names)")
    extra = argv[1:]
    if not extra:
        return
    whole = " ".join(argv)
    finder = Finder(os_)
    known = finder.by_id(whole) is not None
    first = None if known else finder.by_id(argv[0])
    if first is not None:
        right = (f"just the name:   ./os {verb} {handle(first)}      "
                 "(a name with spaces goes in quotes)")
    else:
        right = f'a name with spaces goes in quotes:   ./os {verb} "{whole}"' \
            + ("" if known else "      (run ./os to see the names)")
    die(f"./os {verb} takes one name, and '{' '.join(extra)}' came after it, "
        f"so nothing was changed.\n     {right}", 2)


def _where_the_name_ends(os_: "Zenith", argv: list[str], verb: str, then: str) -> None:
    """Stop a command whose name has words of its own after it (decide, rename)
    when it can't tell where the name ends.

    `./os decide Kitchen Renovation "we tile the floor"`, unquoted, wrote
    "Renovation we tile the floor" under Kitchen, and rename called Kitchen
    "Renovation Kitchen Refit" (review, 2026-09-30). Their words are meant, so
    they can't be refused the way close's are; but when the first few words
    together are the name of something else here, it asks for quotes.

    Two words count too: `./os decide Kitchen Renovation` may be Kitchen
    Renovation with the decision left off. But the way it offered to say it
    was Kitchen's, `./os rename van "Insurance"`, was the same command again,
    and was refused again (review, 2026-09-30). A bare -- says where the name
    ends, so that is the way now. It is taken out of argv here, with the name
    left at argv[0]: left in, the -- was written into the decision, or the
    new name."""
    if "--" in argv:
        cut = argv.index("--")
        if cut:
            argv[:] = [" ".join(argv[:cut]), *argv[cut + 1:]]
            return
        del argv[0]
        if not argv:
            die(f"which one?   ./os {verb} fix-the-boiler      (run ./os to see the names)")
    words = list(argv)
    if len(words) < 2:
        return
    # One look at the names first: a long decision is many words to try.
    names: set[str] = set()
    for it in Scanner(os_).scan():
        if it.kind in ("project", "note", "asset", "archive"):
            names.update((slugify(nfc(it.ident)), slugify(nfc(it.title))))
    finder = Finder(os_)
    for upto in range(len(words), 1, -1):
        name = " ".join(words[:upto])
        longer = finder.by_id(name) if slugify(nfc(name)) in names else None
        if longer is None:
            continue
        first = finder.by_id(words[0])
        if first is not None and first.path == longer.path:
            return
        rest = " ".join(words[upto:])
        lines = [f"'{name}' is the name of something here, so it isn't clear where "
                 "the name ends — nothing was changed.",
                 f'     the name in quotes:   ./os {verb} "{name}" "{rest or then}"']
        if first is not None:
            # With nothing after the longer name, quoting the rest is this
            # same command; the -- is what makes it different.
            lines.append(f'     or, for {first.ident}:   ./os {verb} {handle(first)} '
                         + ("" if rest else "-- ") + f'"{" ".join(words[1:])}"')
        die("\n".join(lines), 2)


def _then(argv: list[str]) -> str:
    """What was typed after the name, as it goes back after another one."""
    rest = " ".join(argv[1:]).strip()
    return f' "{rest}"' if rest else ""


def the_one(os_: "Zenith", name: str, verb: str, then: str = "",
            finder: "Finder | None" = None, archived: bool = False) -> Item:
    """The one thing a command names, or stop before anything changes.

    Nothing by that name says so, and how to look. More than one, Pizza in
    Work and pizza.md in Notes, lists each with the command that reaches it:
    show and the rest took the first, and the other could not be reached
    (review, 2026-09-30). `then` is what the command takes after the name;
    `archived`, that it means something put away (./os back)."""
    finder = finder or Finder(os_)
    item = finder.by_id(name, archived)
    if item is None:
        die(f"nothing here is called {name} — try  ./os find {name}")
    if finder.ambiguous:
        among = finder.ambiguous
        # The first dozen: 200 client folders each with its own Ideas.md
        # printed 200 lines, and took a second and a half to.
        shown = among[:12]
        ways = [f"./os {verb} {finder.qualified(it, among)}{then}" for it in shown]
        width = min(max(vlen(w) for w in ways), 60)
        lines = [f"{len(among)} things here are called {trunc(name, 60)} — which one?"]
        lines += [f"     {pad(way, width)}   {os_.rel(it.path)}" for way, it in zip(ways, shown)]
        if len(among) > len(shown):
            lines.append(f"     and {len(among) - len(shown)} more — the folder it's in "
                         f"tells them apart, like {ways[0].split(' ', 2)[2]}")
        die("\n".join(lines), 2)
    return item


def cmd_status(os_: Zenith, argv: list[str]) -> int:
    """One screen. Plain sentences, not a dashboard."""
    if _flag(argv, "--json"):
        print(json.dumps(Reviewer(os_).run(), indent=2))
        return 0
    if os_.state_unreadable:
        Out.warn(f"{os_.state_unreadable} — until then, nothing that changes this folder will run")
    if not (os_.dot / "registry.json").exists():
        Indexer(os_).build()
    scanner = Scanner(os_)
    items = scanner.scan()
    health = Doctor(os_).run(items=items)

    mark = wordmark(os_.config.get("name", "Zenith"))
    Out.raw(paint(mark, S.GOLD) if S.enabled else mark)
    Out.raw("  " + paint(str(os_.root), S.FAINT))
    Out.raw()

    rows = [(b, len([i for i in items if i.bucket == b]), spec["blurb"])
            for b, spec in os_.buckets().items()]
    width = max(len(r[0]) for r in rows) + 3
    # As much as the window has room for, cut where a word ends.
    room = max(30, shutil.get_terminal_size((80, 24)).columns - width - 9)
    for bucket, n, blurb in rows:
        bar = paint("▍", S.GOLD) if n else " "
        Out.raw("  " + bar + paint(pad(bucket, width - 1), S.INK if n else S.FAINT)
                + paint(pad(str(n) if n else "·", 6), S.B if n else S.FAINT)
                + paint(trunc_words(blurb, room), S.FAINT))
    Out.raw()

    waiting = len([i for i in items if Sorter.unmanaged(i)]) \
        + len(loose_at_top(os_.root, os_.buckets()))
    # Words taken back with ./os undo sit in the same place on purpose, and are
    # no worry at all: counted here they were a nag with no way to stop it.
    taken = os_.taken_back()
    staged = [p for p in staged_captures(os_.dot) if os_.rel(p) not in taken]
    active = [i for i in items if i.kind == "project" and i.status == PUSHING]
    held = [i for i in items if i.kind == "project" and i.status == HOLDING
            and i.bucket != os_.bucket_for_role("archive")]
    cold = [i for i in active if days_since(i.updated) >= int(os_.thresholds["stale_project_days"])]
    # A file ./os passes over is missing from everywhere, and only ./os
    # check said so. check --fix puts it right, so it counts here, as does
    # anything of theirs ./os check --fix makes findable again. Work kept
    # inside another piece of work is only said, by ./os check: where it is
    # was their choice.
    errors = [i for i in health["issues"] if i["level"] == "error"
              or (i["code"] in Doctor.OUT_OF_REACH
                  and str(i.get("fix") or "").startswith("./os check --fix"))]

    def plural(n, word):
        return f"{n} {word}" + ("" if n == 1 else "s")

    def names(group: list) -> None:
        # Every "which one?" says "run ./os to see the names", so here they
        # are: the newest few, each with the name a command takes.
        group = sorted(group, key=lambda i: days_since(i.updated))
        typed = Finder(os_, scanner).typed_names(items, group[:5])
        for it in group[:5]:
            Out.raw("    " + paint(pad(trunc(it.title, 40), 42), S.INK)
                    + paint(f"./os show {typed[str(it.path)]}", S.FAINT))
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
                + paint(f"   {where}  ·  merged?  ./os update --done", S.FAINT))
    if errors:
        Out.raw("  " + paint(plural(len(errors), "thing")
                             + (" needs" if len(errors) == 1 else " need") + " fixing.", S.RED)
                + paint("   ./os check --fix", S.FAINT))
    elif health["score"] < 85:
        Out.raw("  " + paint("A few small things could be tidier.", S.AMBER)
                + paint("   ./os tidy", S.FAINT))

    Out.raw()
    # "Open this folder in any AI and just talk" read as if the ChatGPT app
    # would do. It takes an AI that can run commands on this computer, the
    # way the README says: Claude Code is the one it names.
    Out.raw("  " + paint('./os save "anything on your mind"', S.GOLD)
            + paint("   writes it down and files it", S.FAINT))
    Out.raw("  " + paint("claude", S.GOLD)
            + paint("   or another AI that can run commands here, then just talk", S.FAINT))
    Out.raw("  " + paint("./os help", S.MUTE) + paint("   everything you can type", S.FAINT))
    Out.raw()
    return 0


def cmd_sort(os_: Zenith, argv: list[str]) -> int:
    dry = _flag(argv, "--dry-run", "-n")
    as_json = _flag(argv, "--json")
    forget = _flag(argv, "--forget")
    anyway = _flag(argv, "--anyway")
    regroup = _flag(argv, "--regroup")
    yes = _flag(argv, "--yes", "-y")
    if forget:
        return forget_taken_back(os_, anyway and not dry, as_json)
    if anyway:
        die("--anyway goes with --forget:   ./os sort --forget --anyway")
    if regroup:
        return sort_regroup(os_, yes and not dry, as_json)
    if yes:
        die("--yes goes with --regroup:   ./os sort --regroup --yes")
    if dry:
        result = Sorter(os_, dry=True).run()
    else:
        with Lock(os_, "sort"):
            result = Sorter(os_, dry=False).run()
            os_.commit("sort")
            result["relinked"] = os_.relinked
            result["left_behind"] = os_.left_behind
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

    Out.title("filing", "a preview — nothing has moved" if dry else "")
    if not result["moves"]:
        if result.get("skipped") or result.get("waiting"):
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
    if result.get("brought"):
        n = result["brought"]
        bits.append(f"{n} folder{'' if n == 1 else 's'} brought in from the top")
    if result["filed"]:
        bits.append(f"{result['filed']} filed")
    if result["identified"]:
        bits.append(f"{result['identified']} named")
    if result["balanced"]:
        bits.append(f"{result['balanced']} tucked into folders")
    if result.get("relinked"):
        # Said, so a changed line in a note they wrote is never a surprise.
        n = result["relinked"]
        bits.append(f"links kept working in {n} note{'' if n == 1 else 's'}")
    if dry:
        # Under "a preview — nothing has moved", a tick saying "1 folder
        # brought in" read as done.
        Out.note("./os sort would do this: " + (" · ".join(bits) or "nothing"))
    else:
        Out.ok(" · ".join(bits) or "nothing to do")
    report_skipped()
    if not dry:
        say_left_behind(os_)
        Out.note("didn't like any of that?  ./os undo puts it all back")
    Out.raw()
    return 1 if result.get("skipped") else 0


def sort_regroup(os_: Zenith, apply: bool, as_json: bool) -> int:
    """`os sort --regroup`: split a crowded Notes or Work into subject
    groups, only when somebody asks.

    Sort did this on its own once a folder passed a dozen things, and moved
    notes that a skill, a page and a bot's code pointed at by path (reported
    three times, 2026-10). So without --yes this only shows the plan: what
    would move where, and every file here that names a path that would
    change. With --yes it moves them, keeps the paths in pages and skills
    right, says which other files still name an old path, and ./os undo
    takes all of it back."""
    if not apply:
        result = Sorter(os_, dry=True).regroup()
        steps = [{"action": "move", "src": src, "dst": dst} for _w, src, dst in result["moves"]]
        relinker = Relinker(os_, steps)
        named = relinker.left_behind()
        for m in named:
            m["updated"] = Path(m["file"]).suffix.lower() in (".md", ".markdown", ".txt") \
                and relinker._writes_paths(m["file"])
        result["mentions"] = named
    else:
        with Lock(os_, "sort"):
            result = Sorter(os_).regroup()
            os_.commit("regroup")
            result["relinked"] = os_.relinked
            result["left_behind"] = os_.left_behind
            Indexer(os_).build()
    if as_json:
        print(json.dumps(result, indent=2))
        return 0
    moves = result["moves"]
    Out.title("regroup", "" if apply else "a preview — nothing has moved")
    if not moves:
        Out.ok("nothing to regroup — no folder here is crowded enough to split")
        Out.raw()
        return 0
    width = min(max((vlen(m[1]) for m in moves), default=10), 46)
    for _what, src, dst in moves[:200]:
        Out.raw("  " + pad(trunc(src, width), width + 2) + paint("→", S.FAINT) + " "
                + paint(trunc(dst, 52), S.INK))
    if len(moves) > 200:
        Out.note(f"...and {len(moves) - 200} more")
    Out.raw()
    n = len(moves)
    if apply:
        bits = [f"{n} moved into subject folders"]
        if result.get("relinked"):
            k = result["relinked"]
            bits.append(f"paths and links kept right in {k} file{'' if k == 1 else 's'}")
        Out.ok(" · ".join(bits))
        say_left_behind(os_)
        Out.note("didn't like any of that?  ./os undo puts it all back")
        Out.raw()
        return 0
    named = result.get("mentions") or []
    if named:
        files = len({m["file"] for m in named})
        Out.warn(f"{files} file{'' if files == 1 else 's'} here name{'s' if files == 1 else ''} "
                 "a path that would change:")
        for m in named[:20]:
            Out.note(f"{m['file']} line {m['line']}:  {m['old']}  — "
                     + ("./os would update it" if m["updated"] else "you'd fix this one by hand"))
            Out.note("    " + trunc(m["text"], 76))
        if len(named) > 20:
            Out.note(f"...and {len(named) - 20} more lines")
    else:
        Out.note("no file here names a path that would change")
    Out.note(f"to move these {n}:  ./os sort --regroup --yes   ·   ./os undo takes it back")
    Out.raw()
    return 0


def forget_taken_back(os_: Zenith, anyway: bool, as_json: bool) -> int:
    """`os sort --forget`: throw away the words taken back with ./os undo,
    when somebody asks, and only then.

    Undo keeps them on purpose so it never loses words, and sort leaves them
    where they are, so nothing ever cleared them: on 2026-10-04 the owner
    deleted five by hand out of a hidden folder. It is part of sort because
    sort is what finds them and says how to file them after all; the other
    way out belongs beside that one. This is the one place ./os deletes words
    somebody wrote, so it asks the way undo does: the first time it says
    exactly what would be lost and changes nothing, and --anyway goes ahead.
    ./os undo can't bring them back after."""
    stage = (os_.dot / "cache" / STAGING).resolve()

    def kept() -> list[tuple[str, str]]:
        """Each one, and what it says. Only what sits in the staging folder
        itself: a state file edited by hand must never point this at a note."""
        out = []
        for rel in sorted(os_.taken_back()):
            path = os_.root / rel
            if path.parent.resolve() != stage:
                continue
            if path.is_dir() and not path.is_symlink():
                n = sum(1 for p in path.rglob("*") if p.is_file())
                out.append((rel, f"a folder with {n} file{'' if n == 1 else 's'} in it"))
            else:
                out.append((rel, staged_words(path)))
        return out

    gone: list[str] = []
    failed: list[str] = []
    if not anyway:
        lost = kept()           # only looking, so it waits on nobody
    else:
        with Lock(os_, "sort"):
            lost = kept()
            for rel, _words in lost:
                path = os_.root / rel
                try:
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    gone.append(rel)
                except OSError as exc:
                    failed.append(f"{rel}: {exc}")
            os_.taken_back()    # crosses off what is no longer there
            os_.save_state()
    if as_json:
        print(json.dumps({"taken_back": [{"path": r, "words": w} for r, w in lost],
                          "forgotten": gone, "failed": failed}, indent=2))
        return 1 if failed or (lost and not anyway) else 0
    Out.title("forget", "what you took back with ./os undo")
    if not lost:
        Out.ok("nothing taken back with ./os undo is being kept — nothing to throw away")
        Out.raw()
        return 0
    if not anyway:
        Out.warn(f"these {len(lost)} would be thrown away for good:" if len(lost) > 1
                 else "this would be thrown away for good:")
        for rel, words in lost:
            Out.note(f"'{words}'   {rel}")
        Out.note("nothing has been thrown away yet")
        Out.note('to keep one, file it first:  ./os save "<where it is>"')
        Out.note("to throw them away (./os undo can't bring them back):  "
                 "./os sort --forget --anyway")
        Out.raw()
        return 1
    if gone:
        Out.ok(f"threw away {len(gone)} thing{'' if len(gone) == 1 else 's'} "
               "you took back with ./os undo")
    for line in failed:
        Out.warn(f"couldn't throw away {line}")
    Out.raw()
    return 1 if failed else 0


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
        loose += [os_.rel(p) for p in loose_at_top(os_.root, os_.buckets())]
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
    # Only the ones written: one of theirs by that name is left (round 27).
    kept = [n for n in ("INDEX.md", ".claude/CATALOG.md") if mine_to_write(os_.root / n)]
    if kept:
        Out.note(" · ".join(kept))
    for n in ("INDEX.md", ".claude/CATALOG.md"):
        if n not in kept:
            Out.note(f"{n} is yours, so it was left as it is")
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
              "asset": "a file", "skill": "a skill", "agent": "a helper",
              "archive": "archived"}

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


def file_already_here(os_: Zenith, src: Path) -> Path | None:
    """Where a copy of this very file already is in the folder, if anywhere.

    Saving garden-notes.rtf a second time made garden-notes-2.rtf and said
    nothing. Same size first, then the fingerprint, so only a likely match is
    ever read. A note gets a header written into it, so for one of those the
    words under the header are what is compared."""
    try:
        size = src.stat().st_size
        wanted = content_print(src)
        text = src.suffix.lower() in TEXT_SUFFIXES and size <= PROSE_CAP
        words = src.read_text(encoding="utf-8", errors="replace").strip() if text else ""
    except OSError:
        return None
    for bucket in os_.buckets():
        base = os_.root / bucket
        if not base.is_dir():
            continue
        for found in base.rglob("*"):
            try:
                if found.is_symlink() or not found.is_file() or ignored(found) \
                        or found.name.endswith(".card.md"):
                    continue
                if found.stat().st_size == size and content_print(found) == wanted:
                    return found
                if words and found.suffix.lower() in TEXT_SUFFIXES \
                        and found.stat().st_size <= size + 4000 \
                        and parse_frontmatter(read_all(found))[1].strip() == words:
                    return found
            except OSError:
                continue
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


def in_media_folder(os_: Zenith, path: Path) -> bool:
    """Is this inside Work/Content, the folder ./os never files or reads?"""
    shelf = (os_.root / os_.bucket_for_role("project") / MEDIA_FOLDER).resolve()
    return shelf == path or shelf in path.parents


def footage_note(os_: Zenith, target: Path) -> tuple[Path, dict]:
    """Write a note in Notes that says where a big file lives, and file it.

    Saved, a video used to be copied in whole, into Work/Content, where search
    never looks: a 2 GB file twice on the disk, and `./os find` couldn't find
    it. Now the video stays where it is, and this note is what search reads:
    its name, the file's name, and the sentence and tags written into it. The
    `lives:` line is how ./os check notices when the file isn't there any more.
    The caller holds the lock, commits and rebuilds the list."""
    inside = target == os_.root or os_.root in target.parents
    lives = where_said(os_, target)
    files = [target] if target.is_file() else \
        [p for p in own_paths(target) if p.is_file() and not ignored(p)]
    try:
        size = human_size(sum(p.stat().st_size for p in files))
    except OSError:
        size = ""
    video = bool(files) and all(p.suffix.lower() in VIDEO_SUFFIXES for p in files)
    what = ("A folder of footage" if target.is_dir() else "A video") if video else \
        ("A big folder" if target.is_dir() else "A big file")
    title = titleize(target.stem if target.is_file() else target.name)
    where = (f"It is in {os_.rel(target.parent)}, which ./os leaves alone." if inside else
             "It stays where it is, outside this folder. Nothing was copied in.")
    # Its subject comes from its name alone: the folders on the way to it
    # (Movies, Downloads, a user name) say nothing about what it is.
    domain = Classifier(os_).score_domain(title, f"{title} {target.name}", target.suffix)[0]
    # Shown with ~ for the home folder, so it reads as a place and not code.
    home = str(Path.home())
    shown = "~" + lives[len(home):] if not inside and lives.startswith(home + os.sep) else lives
    meta = {"title": title, "type": "note", "domain": domain or "general",
            "tags": ["video"] if video else [],
            "summary": f"{what} kept at {shown}", "lives": lives}
    body = (f"# {title}\n\n{what}. {where}\n\n"
            f"- Where it lives: {shown}\n"
            f"- File: {target.name}" + (f" ({size})" if size else "") + "\n"
            "- What it is: not written yet\n")
    staged = unique_path(Creator(os_).stage() / f"{slugify(title) or 'footage'}.md")
    write_text(staged, compose(meta, body))
    return Sorter(os_).file_one(staged)


def where_said(os_: Zenith, path: Path) -> str:
    """Where a file lives, as a `lives:` line says it: inside this folder
    from its top, so the line still holds when the folder moves."""
    return os_.rel(path) if path == os_.root or os_.root in path.parents else str(path)


def save_footage(os_: Zenith, target: Path, as_json: bool) -> int:
    """`./os save` for a video, or anything too big to read: a note that says
    where it lives, and the file itself left alone."""
    said = where_said(os_, target)
    # Saved twice, it is the same file: say where the note already is.
    known = next((it for it in Scanner(os_).scan()
                  if it.lives == said and it.kind in ("note", "project")), None)
    if known is not None:
        if as_json:
            print(json.dumps({"saved": os_.rel(known.path), "id": known.ident, "kind": known.kind,
                              "title": known.title, "filed": True, "lives": said,
                              "copied": False, "already": True}, indent=2))
            return 0
        Out.title("already written down")
        Out.ok(known.title)
        Out.note(f"the note that says where it lives is {os_.rel(known.path)}")
        Out.raw()
        return 0
    with Lock(os_, "save"):
        dest, verdict = footage_note(os_, target)
        os_.commit("save")
        Indexer(os_).build()
    if dest is None:
        die("I could not write the note for it — nothing was changed. Is the folder "
            "full or read-only? Try again, or  ./os check")
    return said_footage(os_, dest, verdict, target, as_json, copied=False)


def said_footage(os_: Zenith, dest: Path, verdict: dict, lives: Path, as_json: bool,
                 copied: bool) -> int:
    """What `./os save` says once a note for a big file is filed."""
    said = where_said(os_, lives)
    if as_json:
        print(json.dumps({"saved": os_.rel(dest), "id": dest.stem, "kind": verdict["kind"],
                          "title": verdict["title"], "filed": True, "lives": said,
                          "copied": copied}, indent=2))
        return 0
    Out.title("saved")
    Out.ok(verdict["title"])
    if in_media_folder(os_, lives):
        Out.note(f"{'copied into' if copied else 'it is in'} {said} — ./os leaves that folder alone")
    else:
        Out.note(f"left where it is: {said} — nothing was copied in")
    Out.note(f"a note that says where it lives is in {os_.rel(dest)}")
    Out.note(f"say what it is in a sentence, and add a few tags:  ./os edit {handle(dest.stem)}")
    Out.note("wrong spot?  ./os undo")
    Out.raw()
    return 0


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
    # One line, always. A decision with line breaks in it wrote them as they
    # were, and one starting `## Next action` became a second next action
    # (audit, 2026-10-29).
    line = re.sub(r"\s+", " ", one_line(line)).strip()
    with Lock(os_, "decision"):
        text = read_to_rewrite(item.spine)
        _meta, body = parse_frontmatter(text)
        said = line.rstrip(".")
        entry = f"- {today()} · {said}"
        lines = body.split("\n")
        start = next((n for n, text in enumerate(lines)
                      if re.match(r"##\s+Decisions\s*$", text, re.I)), None)
        if start is not None:
            end = next((n for n in range(start + 1, len(lines))
                        if SECTION_RE.match(lines[n])), len(lines))
            # The same words again add nothing, and Decisions is never
            # rewritten, so a second copy could never be taken out.
            # The newest only: "use the plumber", "not the plumber after all",
            # then "use the plumber" again is a change of mind to record, and
            # comparing every line refused it (review, 2026-10-29).
            # The newest line, dated or not, however it's dated: an undated
            # or "2026-10-30:" line was passed over (review, 2026-10-29).
            entries = [l for l in lines[start + 1:end] if re.match(r"^\s*[-*]\s+\S", l)]

            def words_of(line: str) -> str:
                line = re.sub(r"^\s*[-*]\s+", "", line)
                line = re.sub(r"^\d{4}-\d\d-\d\d\s*[·:—–-]?\s*", "", line)
                return re.sub(r"\s+", " ", line).strip().rstrip(".").casefold()
            if entries and words_of(entries[-1]) == re.sub(r"\s+", " ", said).casefold():
                Out.title("decided")
                Out.ok(f"{item.title} already has that under Decisions — nothing added")
                Out.note(f"./os show {handle(item)}")
                Out.raw()
                return 0
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


def keeps_coming_back(body: str, line: str) -> str:
    """`body` with one more line under `## Keeps coming back`, the section held
    work has where pushed work has a next action.

    Made where the held template has it, before Decisions and Log, when the
    item has none yet. A plain line, not a box to tick: something kept up is
    never done, and a box on it read as one more thing to do. The template's
    empty `- [ ]` is filled with it rather than left above it."""
    section = re.search(r"^##\s+Keeps coming back[ \t]*\n(.*?)(?=^##\s|\Z)", body, re.M | re.S)
    entry = f"- {line}"
    if section:
        inside = section.group(1)
        placeholder = re.compile(r"^[-*][ \t]*\[ \][ \t]*$", re.M)
        if placeholder.search(inside):
            inside = placeholder.sub(lambda _m: entry, inside, count=1)
        else:
            kept = inside.rstrip("\n")
            inside = (kept + "\n" if kept else "") + entry + "\n\n"
        return body[:section.start(1)] + inside + body[section.end(1):]
    later = re.search(r"^##\s+(?:Open questions|Decisions|Log)\b.*$", body, re.M | re.I)
    if later:
        return (body[:later.start()].rstrip("\n") + "\n\n## Keeps coming back\n"
                + entry + "\n\n" + body[later.start():])
    return body.rstrip() + "\n\n## Keeps coming back\n" + entry + "\n"


def with_how_often(text: str) -> str:
    """`text` with an empty `## How often` and `## Where it stands`, if it
    lacks them.

    Held work is kept up rather than finished, and how often is the one thing
    it needs said. `./os hold` told people to write it under ## How often in a
    file that had no such heading. Made where the held template has it: after
    What good looks like, before everything else. Where it stands is what a
    session's end rewrites for held work (AGENTS.md), and work that was
    pushed first had none to rewrite (audit, 2026-10-29)."""
    for name, comment, after in (
            ("How often", "Weekly, monthly, every spring.",
             r"Where it stands|Keeps coming back|Next action|Open questions|Decisions|Log"),
            ("Where it stands", "How it's going right now. Replace this each time; it isn't a history.",
             r"Keeps coming back|Next action|Open questions|Decisions|Log")):
        if re.search(rf"^##\s+{name}\b", text, re.M | re.I):
            continue
        section = f"## {name}\n<!-- {comment} -->\n\n"
        later = re.search(rf"^##\s+(?:{after})\b.*$", text, re.M | re.I)
        if later:
            text = text[:later.start()] + section + text[later.start():]
        else:
            text = text.rstrip() + "\n\n" + section.rstrip() + "\n"
    return text


def section_empty(path: Path, name: str) -> bool:
    """Is this `## ` section missing, or holding nothing but its comment?"""
    try:
        _meta, body = parse_frontmatter(read_text(path))
    except OSError:
        return False
    return not _clean(_sections(body).get(name.lower(), ""), 1)


def add_next_step(os_: Zenith, item: Item, said: str, text: str, landed: Path,
                  as_json: bool) -> int:
    """Write saved words into the work they name, as its next action. `landed`
    is where they were written down first, and goes once they are in.

    Held work has no next action: what is said about it goes under `## Keeps
    coming back` instead. "Garden Upkeep: mow the lawn" made a box to tick
    on something kept up, and a note about the roses became one too (stranger
    test, 2026-09-30). It still goes to the item it names: saved as a note
    instead, it would be a second thing under the same name."""
    step = re.sub(r"\s+", " ", text.strip()[len(said):]).strip(" :;,-–—")
    step = step[:1].upper() + step[1:]
    held = item.status == HOLDING
    with Lock(os_, "save", safe="what you typed is safe in " + os_.rel(landed)
              + " — ./os sort files it when the other run is done"):
        text_was = read_to_rewrite(item.spine)
        _meta, body = parse_frontmatter(text_was)
        section = re.search(r"^##\s+Next action[ \t]*\n(.*?)(?=^##\s|\Z)", body, re.M | re.S)
        empty = re.compile(r"^([-*][ \t]*\[ \])[ \t]*$", re.M)
        if step and held:
            body = keeps_coming_back(body, step)
        elif step and section and empty.search(section.group(1)):
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
            os_.commit(f"{item.ident} {'keeps coming back' if held else 'next action'}")
            Indexer(os_).build()
        landed.unlink()
    remember_save(os_, text, item.path)
    if as_json:
        # Only the name, with nothing after it, adds nothing: said as added,
        # an AI told them it was written down (audit, 2026-10-28).
        print(json.dumps({"saved": os_.rel(item.path), "id": item.ident, "kind": "project",
                          "title": item.title, "filed": bool(step),
                          "added_to": item.ident if step else None, "already": not step,
                          "under": ("keeps coming back" if held else "next action") if step else None},
                         indent=2))
        return 0
    Out.title("saved")
    if not step:
        Out.ok(f"you already have {item.title}")
        Out.note(f"nothing new filed   ·   ./os show {handle(item)}")
        Out.raw()
        return 0
    Out.ok(f"added to {item.title}: {trunc(step, 60)}")
    if held:
        Out.note(f"it's under Keeps coming back in {os_.rel(item.spine)}   ·   "
                 f"./os show {handle(item)}")
        Out.note(f"a one-off to do instead?  ./os undo, then ./os push {handle(item)} "
                 "and save it again")
    else:
        Out.note(f"it's the next action in {os_.rel(item.spine)}   ·   ./os show {handle(item)}")
    Out.note("wrong spot?  ./os undo")
    Out.raw()
    return 0


#: A save that says what to call them or how they want answers: "Call me
#: Sam", "From now on, answer in French", "I prefer bullet points", "Keep
#: answers short". Filed like anything else, "Call me Sam" became a piece of
#: work called Call Me Sam, and the next chat still didn't know their name.
#: "Call me back about the quote" is a job, not a name, and is filed as one.
#: "About me: I trade from London" says where it goes outright; it became a
#: loose note called About Me I Trade From London (snag, 2026-09-29).
ABOUT_THEM_SAID = re.compile(
    r"^\s*(?:(?:please[,\s]+)?(?:call\s+me(?!\s+(?:back|later|when|if|at|on|about|after|before"
    r"|tomorrow|tonight|today|asap)\b)|from\s+now\s+on|i\s+prefer"
    r"|keep\s+(?:your\s+|the\s+)?answers)\b|about\s+me\s*[:\-–—]\s*\S)", re.I)
#: After "From now on" or "I prefer", a word that says it is about them or
#: how they want answers. "I prefer the second quote from the plumber" and
#: "From now on the kitchen budget is 5000" went into About me marked
#: (said), as if they were about the person (audit, 2026-10-28).
ABOUT_THEM_HOW = re.compile(
    r"\b(?:me|my|i|i'm|you|your|answers?|repl(?:y|ies)|respon(?:d|ses?)|bullets?|tables?"
    r"|short(?:er)?|long(?:er)?|brief|concise|detail(?:ed)?|plain|words|sentences|tone|explain"
    r"|english|french|german|spanish|italian|portuguese|dutch|chinese|japanese|korean)\b", re.I)
ABOUT_THEM_LOOSE = re.compile(r"^\s*(?:please[,\s]+)?(?:from\s+now\s+on|i\s+prefer)\b[,\s]*", re.I)


def about_them(text: str) -> bool:
    """Is this saved line about them: what to call them, how they want answers?"""
    if not ABOUT_THEM_SAID.match(text):
        return False
    loose = ABOUT_THEM_LOOSE.match(text)
    return not loose or bool(ABOUT_THEM_HOW.search(text[loose.end():]))


#: The "About me:" in front of a line, which the note's own name already says.
ABOUT_THEM_LEAD = re.compile(r"^\s*about\s+me\s*[:\-–—]\s*", re.I)


def add_about_them(os_: Zenith, text: str, as_json: bool) -> int:
    """Write saved words about them into their About me note, as one line
    marked (said): they said it themselves. With no About me yet, one is
    started in the same step, so one ./os undo takes back the line and the
    note both. Every chat starts by reading that note: see _about_them."""
    words = re.sub(r"\s+", " ", ABOUT_THEM_LEAD.sub("", text.strip()))
    words = words[:1].upper() + words[1:]
    if words[-1] not in ".!?…":
        words += "."
    entry = f"- {words} (said)"

    def said(line: str) -> str:
        """A line as compared, so the same thing said twice is written once."""
        line = re.sub(r"^\s*[-*]\s+|\s*\((?:said|guessed|unsure)\)\s*$", "", line.strip())
        return re.sub(r"\s+", " ", line).strip(" .!?…").lower()

    with Lock(os_, "save"):
        note = about_them_note(os_, Scanner(os_).scan())
        if note is None:
            # Under personal when this folder has that subject, as AGENTS.md
            # has `./os new note "About me" --domain personal` make it.
            domain = "personal" if "personal" in (os_.taxonomy.get("domains") or {}) else ""
            spine = unique_path(os_.root / os_.bucket_for_role("note") / f"{ABOUT_THEM}.md")
            text_was = Creator(os_).render("note", "About me", "", domain, [], shape=ABOUT_THEM)
        else:
            spine = note.spine
            text_was = read_to_rewrite(spine)
        _meta, body = parse_frontmatter(text_was)
        already = note is not None and any(said(line) == said(entry) for line in body.split("\n"))
        if not already:
            # Where an About me made in the ordinary note form keeps what it
            # says; anywhere else, at the end, so the newest is last.
            lines = body.split("\n")
            start = next((n for n, line in enumerate(lines)
                          if re.match(r"##\s+What it says\s*$", line, re.I)), None)
            if start is not None:
                end = next((n for n in range(start + 1, len(lines))
                            if SECTION_RE.match(lines[n])), len(lines))
                while end > start + 1 and not lines[end - 1].strip():
                    end -= 1
                lines.insert(end, entry)
                body = "\n".join(lines)
            else:
                kept = body.rstrip()
                last = kept.split("\n")[-1].lstrip()
                body = kept + ("\n" if last.startswith(("- ", "* ")) else "\n\n") + entry + "\n"
            if note is not None:
                os_.snapshot(spine)
            write_text(spine, set_fields(text_was, {"updated": today()}, body=body))
            if note is None:
                os_.created(spine)
            else:
                os_.record("edit", os_.rel(spine))
            os_.commit("save")
            Indexer(os_).build()
    ident = note.ident if note is not None else spine.stem
    title = note.title if note is not None else "About me"
    if as_json:
        print(json.dumps({"saved": os_.rel(spine), "id": ident, "kind": "note", "title": title,
                          "filed": True, "added_to": ident, "already": already,
                          "started": note is None}, indent=2))
        return 0
    if already:
        Out.title("already written down")
        Out.ok(os_.rel(spine))
        Out.note(f"that's already in {title} — nothing new written")
        Out.note(f"to change it:  ./os edit {handle(ident)}")
        Out.raw()
        return 0
    Out.title("saved")
    Out.ok(f"added to {title}: {trunc(words, 60)}")
    if note is None:
        Out.note(f"there was no {title} note yet, so this started one")
    Out.note(f"it's in {os_.rel(spine)}, which every new chat reads first   ·   "
             f"./os show {handle(ident)}")
    Out.note("wrong spot?  ./os undo")
    Out.raw()
    return 0


def cmd_decide(os_: Zenith, argv: list[str]) -> int:
    """Write one settled thing into the item it was settled about.

    `os save` guesses which item a decision belongs to and refuses when it
    cannot tell. This is the same line, said by hand, when you already know."""
    item = _claimable(os_, argv, "decide", more=True)
    line = " ".join(argv[1:]).strip()
    if not re.search(r"[^\W_]", line, re.UNICODE):
        die(f'what was decided?   ./os decide {handle(item)} "a new one, not another repair"')
    warn_if_claimed(os_, item)
    if item.kind == "archive":
        # Allowed — a lesson from a finished thing is still a decision — but
        # said, as hold and push say it, since it lands out of sight (audit, 2026-10-28).
        Out.warn(f"{item.ident} is archived — this goes in its Decisions there. "
                 f"Still live?  ./os back {handle(item)}")
    return write_decision(os_, item, DECISION_LEAD.sub("", line).strip() or line)


def bring_in_what_links_point_at(original: Path, copy: Path, home: Path, fence: Path | None = None) -> list:
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
            # In a bring, still aimed at where it led, from where it is now:
            # kept relative, one leading into this folder or a parent of the
            # old one broke or turned round (second pre-merge review).
            if fence is not None and target.exists():
                link.unlink()
                link.symlink_to(target)
            left.append(str(rel))
            continue
        # Outside the fence (a bring's old folder), nothing is copied in: a
        # shortcut to their tax papers, or to all of Pictures, came in whole
        # without a word (pre-merge review, 2026-10-29). It goes on leading
        # where it did.
        if fence is not None:
            fenced = fence.resolve()
            if not (target == fenced or fenced in target.parents):
                link.unlink()
                link.symlink_to(target)
                left.append(str(rel))
                continue
        link.unlink()
        if target.is_dir():
            shutil.copytree(target, link, symlinks=True)
        else:
            shutil.copy2(target, link)
    return left


def plain_name(text: str) -> str:
    """A name as it is compared: no marks, no accents, no capitals."""
    return re.sub(r"[\W_]+", " ", unaccent(str(text)).casefold()).strip()


def work_named_in(os_: Zenith, title: str, body: str) -> Item | None:
    """The live work a saved thing is about, when its name says so: the
    work's whole name, marks and capitals aside, in the title or the first
    lines. A note about Build a Trading Bot for NQ1!'s setups was made a new
    piece of work beside it (snag, 2026-09-29).

    Only a whole name of two words or more counts, and two pieces of work
    named alike count for neither: put in the wrong place, a note is harder
    to find than one left on its own."""
    lines = [line for line in body.split("\n") if line.strip()][:8]
    hay = " " + plain_name(title + " " + " ".join(lines)) + " "
    best, longest, tied = None, 0, False
    for it in Scanner(os_).scan():
        if it.kind != "project" or it.status not in (PUSHING, HOLDING) \
                or it.bucket == os_.bucket_for_role("archive") or not it.path.is_dir() \
                or not (it.spine and it.spine.exists()):
            continue
        for name in {it.title, it.ident}:
            needle = plain_name(name)
            if len(needle.split()) < 2 or f" {needle} " not in hay:
                continue
            if len(needle) > longest:
                best, longest, tied = it, len(needle), False
            elif len(needle) == longest and best is not None and best.path != it.path:
                tied = True
    return None if tied else best


def save_into_work(os_: Zenith, item: Item, landed: Path, title: str, typed: str,
                   as_json: bool, src: str = "", brought: bool = False) -> int:
    """Put a saved thing inside a piece of work, as a file of its own, with a
    line in its ## Log saying so. `landed` is where it was written down
    first; it moves from there, so one ./os undo takes it all back. `src` is
    the path it was saved from, if it was."""
    if not item.path.is_dir() or not (item.spine and item.spine.exists()):
        die(f"{item.title} has no folder to put it in — it is safe in {os_.rel(landed)}")
    # Named for what it says about the work, not for the work again:
    # "Kitchen Renovation - quotes" is quotes.md inside Kitchen Renovation.
    first = next((line.strip().lstrip("#").strip() for line in
                  (typed or (title or "")).split("\n") if line.strip()), "")
    for name in (item.title, item.ident):
        words = plain_name(name).split()
        lead = re.match(r"^\W*" + r"\W+".join(map(re.escape, words)) + r"(?:\W+|$)",
                        unaccent(first), re.I) if words else None
        rest = first[lead.end():].strip() if lead else ""
        if re.search(r"[^\W_]", rest):
            title = infer_title(rest[:1].upper() + rest[1:], landed)
            break
    if landed.is_dir():
        name = folder_name(title, slugify(landed.name, 44))
    elif typed:
        name = f"{slugify(title, 44, at_word=True)}.md"
    else:
        stem = landed.stem if given_name(landed.stem) else title
        name = f"{slugify(stem, 44, at_word=True)}{landed.suffix}"
    with Lock(os_, "save", safe="what you saved is safe in " + os_.rel(landed)
              + " — ./os sort files it when the other run is done"):
        dest = os_.move(landed, unique_path(item.path / name))
        if brought:
            os_.brought_in(landed)
        if typed:
            # Their words, under a heading, without the note the save wrote on top.
            _meta, words = parse_frontmatter(read_all(dest))
            words = words.strip()
            if not re.match(r"#\s+\S", words):
                words = f"# {title}\n\n{words}"
            write_text(dest, words + "\n")
        text = read_to_rewrite(item.spine)
        _meta, body = parse_frontmatter(text)
        link = os.path.relpath(dest, item.spine.parent)
        entry = f"- {today()} · saved [{trunc(title, 60)}]({md_link(link)}) in here"
        os_.snapshot(item.spine)
        write_text(item.spine, set_fields(text, {"updated": today()},
                                          body=with_log_line(body, entry)))
        os_.record("edit", os_.rel(item.spine))
        os_.commit("save")
        Indexer(os_).build()
    if typed:
        remember_save(os_, typed, dest)
    if as_json:
        print(json.dumps({"saved": os_.rel(dest), "id": item.ident, "kind": "project",
                          "title": title, "filed": True, "into": item.ident}, indent=2))
        return 0
    Out.title("saved")
    Out.ok(f"into {item.title}: {trunc(title, 60)}")
    Out.note(f"it's in {os_.rel(dest)}, with a line in its ## Log   ·   ./os show {handle(item)}")
    Out.note("a thing of its own instead?  ./os undo, then "
             + (f"./os save --new {shell_word(src)}" if src else "save it again with --new"))
    Out.note("wrong spot?  ./os undo")
    Out.raw()
    return 0


def cmd_save(os_: Zenith, argv: list[str]) -> int:
    """Write something down and put it where it belongs, in one step."""
    if os_.state_unreadable:
        # Before anything is copied in: the lock refused later, after the
        # words were already in staging, and a retry made two (review,
        # 2026-10-29).
        die(f"{os_.state_unreadable}. Nothing was saved, and your words are still here to save again.")
    as_json = _flag(argv, "--json")
    # What they say it is, and where it goes, beat any guess: --note and
    # --work say which it is, --into puts it inside a piece of work, and
    # --new keeps it out of any.
    said = "note" if _flag(argv, "--note") else ""
    if _flag(argv, "--work"):
        if said:
            die("a note or work? say one:  --note  or  --work", 2)
        said = "work"
    apart = _flag(argv, "--new")
    into_name = _opt_given(argv, "--into", './os save "call the plumber" --into kitchen-renovation').strip()
    if apart and into_name:
        die("--new keeps it out of other work, and --into puts it in one — say one", 2)
    into = None
    if into_name:
        into = the_one(os_, into_name, "save --into")
        if into.kind != "project" or not into.path.is_dir():
            die(f"{into.title} isn't work with a folder — --into puts things inside work."
                f"\n     to add to a note:  ./os edit {handle(into)}", 2)
    # A video, or anything too big to read, is left where it is unless they
    # asked for a copy in here: see footage_note.
    copy_in = _flag(argv, "--copy")
    src = _opt(argv, "--file")
    typed = ""      # the words themselves, when words are what was saved
    # A copy this save made in the staging folder, or the note it wrote from
    # the words typed. Undo takes that away again; see Undo.revert.
    brought = False
    pointers: list = []     # shortcuts in a saved folder still pointing outside it
    if argv and argv[0] == "--":           # the "everything after this is text" separator
        argv = argv[1:]

    # A single argument naming something real is a file, not a sentence. The name
    # has to be non-empty: Path("") is the current directory, and copying the
    # folder into itself is not what anybody meant.
    if not src and len(argv) == 1 and argv[0].strip():
        lone = argv[0].strip()
        candidate = Path(lone).expanduser()
        try:
            there = bool(candidate.name) and candidate.exists()
        except (OSError, ValueError):
            # Python 3.9 asks the disk about a 300-letter sentence as if it
            # were a file name, and the disk says "too long" (audit, 2026-10-28).
            there = False
        if there:
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
        top = {p.resolve() for p in loose_at_top(os_.root, os_.buckets())}
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
        elif resolved in top and (resolved.is_file() or document_bundle(resolved)):
            # Dropped at the top of the folder: filed from where it lies, the
            # way sort files it, instead of refused as already in this folder.
            landed = os_.root / resolved.name
            what = path.name
        elif resolved in top:
            # A folder dropped there goes into its bucket under its own name,
            # and what is in it is filed from there: sort does all of that.
            die(f"{resolved.name} is already in this folder — ./os sort files it")
        elif in_media_folder(os_, resolved) and in_media_folder(os_, resolved.parent):
            # Already in Work/Content, which search never reads: saved by its
            # path, it gets a note that says it's there, and stays put.
            return save_footage(os_, resolved, as_json)
        elif resolved == os_.root or os_.root in resolved.parents:
            die(f"{os_.rel(resolved)} is already in this folder — "
                "nothing to bring in")
        if resolved in os_.root.parents:
            die("that is a folder this one lives inside — pick something smaller")
        if landed is None and not copy_in and big_media(os_, resolved):
            # Footage, or anything too big to read, stays where it is. A copy
            # doubled it on the disk, into a folder search never reads; the
            # note is what search finds. --copy brings it into Work/Content.
            # 100 MB is the folder's own line for too big to read (big_file_mb
            # in .os/config.json), the same one sort uses for Work/Content.
            return save_footage(os_, resolved, as_json)
        if landed is None and path.is_file():
            have = file_already_here(os_, resolved)
            if have is not None:
                ident = handle(have.stem if have.is_file() else have.name)
                if as_json:
                    print(json.dumps({"saved": os_.rel(have), "id": ident,
                                      "filed": True, "already": True}, indent=2))
                    return 0
                Out.title("already here")
                Out.ok(os_.rel(have))
                Out.note("the very same file is already in this folder — nothing new saved")
                Out.note(f"look at it:  ./os show {ident}")
                Out.raw()
                return 0
        if landed is None:
            landed = unique_path(Creator(os_).stage() / path.name)
            # Copied under a name sort never files, and given its own only
            # once whole: a Ctrl-C or a closed window partway left the half
            # copy under its real name, and the next sort filed it into Notes
            # as if whole (eighteenth review, round 23).
            part = None
            try:
                part = claim_partial(landed, folder=not path.is_file())
                # Shortcuts inside a folder come in as shortcuts. Followed, one
                # pointing back at its own folder (common in code projects) was
                # copied into itself round and round until the disk filled.
                if path.is_file():
                    shutil.copy2(path, part)
                else:
                    shutil.copytree(path, part, symlinks=True, dirs_exist_ok=True)
                    pointers = bring_in_what_links_point_at(path, part, os_.root)
                # One step: a stop between the two took away a name another
                # chat could have claimed by then (pre-merge review, round 24).
                landed, part = place_whole(part, landed), None
            except BaseException as exc:
                # A part-copy left here was filed by the next sort as if whole.
                drop_partial(part)
                if not isinstance(exc, (OSError, shutil.Error)):
                    raise
                die(f"could not bring that in: {copy_failed(exc)} — nothing was saved, "
                    f"and {shell_word(str(path))} is as it was")
            what = path.name
            brought = True
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
        if text.strip() in (".", "..", "./", "../"):
            # "no words in that" was wrong: it names a folder (audit, 2026-10-28).
            die("that's a folder, not something to save — name a file in it, or put words in quotes:   "
                './os save "the thing on your mind"')
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
        free = not (said or apart or into)
        if free and (DECISION_WORDS.match(text)
                     or (DECISION_SAID.match(text) and "\n" not in text.strip())):
            home = decision_home(os_, text)
            if home is not None:
                return write_decision(os_, home, DECISION_LEAD.sub("", text).strip() or text)
            # It reads like a decision and names nothing we hold. Filing it makes
            # the stray "Decided <date>" notes this rule exists to prevent — say
            # which command puts it where it belongs instead. "I decided to
            # learn to sail" is a thought in a sentence, though, and is saved.
            if not DECISION_SAID.match(text):
                die('That reads like a decision — use ./os decide <name> "<text>"')
        # "Call me Sam" is a line in their About me note, not a thing of its
        # own: see add_about_them. One line only; several that open "From now
        # on" are a page of notes, and are filed as one.
        if about_them(text) and "\n" not in text.strip() \
                and said != "work" and not (apart or into):
            return add_about_them(os_, text, as_json)
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
        brought = True
        what = re.sub(r"\s+", " ", text.strip().split("\n")[0])[:64]
        # Work that opens with the name of work already here is a next step
        # for that one, not a second item under the same name.
        # One short line after the name is a step for it, however it reads;
        # "Kitchen renovation, buy paint" read as a note, and made a second
        # thing called Kitchen Renovation. A longer one goes to it only when
        # it reads as work. Several lines are a page about it, and go inside
        # it as one (see save_into_work): pasted notes on a broker became
        # one long box to tick.
        named, name_said = work_named_first(os_, text) \
            if not (apart or into or said == "note") and "\n" not in text.strip() \
            else (None, "")
        if named is not None and (
                len(text.strip()) - len(name_said) <= 160
                or Classifier(os_).classify(landed, "words", said)["kind"] == "project"):
            return add_next_step(os_, named, name_said, text, landed, as_json)

    # About a piece of work already here: it goes inside that one, as a file
    # of its own. --into names the work outright; --new and --work keep it
    # apart. A folder of code, a skill or a helper is never put in one, and
    # nor is one short line: "Ship the mobile app soon" is not a page about
    # Ship the Mobile App, and is filed, and warned about, as before.
    one_line_said = bool(typed) and "\n" not in typed.strip() and len(typed.split()) <= 40
    if into is not None or not (apart or said == "work" or one_line_said):
        look = Classifier(os_).classify(landed, "words" if typed else "path", said)
        if look["kind"] not in ("skill", "agent") and not (
                into is None and landed.is_dir() and any((landed / f).exists() for f in CODE_MARKERS)):
            home = into or work_named_in(os_, look["title"], look.get("captured") or "")
            if home is not None:
                return save_into_work(os_, home, landed, look["title"] or what, typed,
                                      as_json, src, brought)

    # By here the words are already on disk, whether or not the lock is free.
    with Lock(os_, "save", safe="what you typed is safe in "
              + os_.rel(landed) + " — ./os sort files it when the other run is done"):
        dest, verdict = Sorter(os_).file_one(landed, "words" if typed else "path", said)
        if brought:
            os_.brought_in(landed)
        # Brought in with --copy, it goes to Work/Content, where search never
        # looks: a note in Notes says it's there. One step, so one undo.
        footage = None
        if copy_in and dest is not None and in_media_folder(os_, dest.resolve()):
            footage = footage_note(os_, dest.resolve())
        os_.commit("save")
        Indexer(os_).build()
    if footage is not None and footage[0] is not None:
        return said_footage(os_, footage[0], footage[1], dest.resolve(), as_json, copied=True)

    if dest is None:
        die("I could not work out where that goes — it is safe in " + os_.rel(landed)
            + "\n     ./os sort picks it up from there")
    if typed:
        remember_save(os_, typed, dest)
    spine = Scanner(os_).spine_of(dest) or dest
    meta, _ = parse_frontmatter(read_all(spine)) if spine.is_file() else ({}, "")
    ident = dest.stem if dest.is_file() else dest.name
    # A second write-up of something already here, found by the figures and
    # names both carry rather than by the name: see Twins. Only for words.
    same: list = []
    if spine.is_file() and spine.suffix.lower() in TEXT_SUFFIXES:
        same = Twins(os_).like(read_text(spine, Twins.READ), skip=(dest, spine))
    if as_json:
        print(json.dumps({"saved": os_.rel(dest), "id": ident, "kind": verdict["kind"],
                          "title": verdict["title"], "filed": True,
                          "may_repeat": [{"id": it.ident, "title": it.title, "path": os_.rel(it.path),
                                          "shares": shared} for it, shared in same[:3]]},
                         indent=2))
        return 0

    twin = [i for i in Finder(os_).like(verdict["title"] or what,
                                        kinds=("project", "note"))
            if os_.rel(i.path) != os_.rel(dest)]
    Out.title("saved")
    Out.ok(verdict["title"] or what)
    if twin:
        Out.warn(f"{twin[0].ident} looks like the same thing: \"{trunc(twin[0].title, 44)}\"")
        Out.note(f"keep just one?  ./os undo   ·   compare:  ./os show {handle(twin[0])}")
    elif same:
        it, shared = same[0]
        Out.warn(f"you may have written this down before, in \"{trunc(it.title, 44)}\": "
                 f"both mention {Twins.say(shared)}")
        Out.note(f"look at it:  ./os show {handle(it)}   ·   keep just one?  ./os undo")
    Out.note(f"that's {KIND_WORDS.get(verdict['kind'], verdict['kind'])} — it's in "
             f"{os_.rel(dest)}")
    if dest.parent.name == MEDIA_FOLDER:
        Out.note(f"big files live in {os_.rel(dest.parent)} — ./os leaves them alone")
    elif dest.is_file() and dest.with_name(dest.name + ".card.md").exists():
        # Search reads the card, not the file, and the card starts out blank.
        Out.note(f"say what it is in its card, so search finds it:  ./os edit {handle(ident)}")
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
    if verdict["kind"] in ("note", "project") and not said:
        # The way to say which it is, for the next time it guesses wrong.
        other = "--work" if verdict["kind"] == "note" else "--note"
        line = re.sub(r"\s+", " ", typed.strip())
        again = (f"./os save {other} {shell_word(src)}" if src else
                 # words that start with a dash need the `--` they came with,
                 # or the hint is refused as an option (audit, 2026-10-28)
                 f"./os save {other} {'-- ' if line.startswith('-') else ''}{shell_word(line)}"
                 if typed and len(line) <= 60
                 and "\n" not in typed.strip() else f"save it again with {other}")
        Out.note(f"{'work' if other == '--work' else 'a note'}, not "
                 f"{KIND_WORDS[verdict['kind']]}?  ./os undo, then {again}")
    Out.note("wrong spot?  ./os undo")
    Out.raw()
    return 0


def _new_preview(os_: Zenith, kind: str, title: str, domain: str, tags: list, anyway: bool) -> int:
    """`./os new ... --dry-run`: where it would go, under what subject, and
    what it might duplicate. Nothing is written (audit, 2026-10-28)."""
    resolved = KIND_ALIASES.get(kind.lower(), kind)
    path = Creator(os_).create(kind, title, domain, tags, dry=True)
    clash = Finder(os_).like(title, kinds=("project", "note")) \
        if resolved in ("project", "note") else []
    put_away = Finder(os_).like(title, kinds=("project", "note"), archived=True) \
        if resolved in ("project", "note") else []
    subject = domain
    if not subject and resolved in ("project", "note"):
        guess, score, _ = Classifier(os_).score_domain(title, title, "")
        subject = guess if guess and score >= 3.0 else ""
    Out.title("new", "a preview — nothing will change")
    Out.ok(f"would start {title}")
    Out.note(f"in {os_.rel(path)}")
    if resolved in ("project", "note"):
        Out.note(f"subject: {subject}" if subject else "subject: none it could tell from the name")
    if clash and not anyway:
        Out.warn(f"you already have {clash[0].ident} \u2014 \"{clash[0].title}\": "
                 "the real run stops there unless told --anyway")
    if put_away:
        Out.warn(f"you put away \"{trunc(put_away[0].title, 44)}\" before — it's in the archive")
    Out.raw()
    return 0


def cmd_new(os_: Zenith, argv: list[str]) -> int:
    tags = [t for t in _opt(argv, "--tags").split(",") if t.strip()]
    domain = _opt(argv, "--domain")
    anyway = _flag(argv, "--anyway", "--force")   # before the title is read off argv
    preview = _flag(argv, "--dry-run", "-n")
    if not argv:
        die('what kind?   ./os new work "Fix the boiler"\n'
            "     kinds: work, ongoing, note, learning, skill, helper")
    kind = argv[0]
    words = argv[1:]
    if words and words[0] == "--":         # what comes after it is the name
        words = words[1:]
    title = " ".join(words).strip()
    # Only a pair of quotes around the whole name is taken off. Stripping
    # each end cut `Read "Dune"` to `Read "Dune` (audit, 2026-10-29).
    if len(title) > 1 and title[0] == title[-1] == '"':
        title = title[1:-1].strip()
    if not title:
        die(f'give it a name:   ./os new {kind} "Fix the boiler"')
    resolved_kind = KIND_ALIASES.get(kind.lower(), kind)
    known = list((os_.taxonomy.get("domains") or {}).keys())
    if domain and known and domain not in known:
        # `new` took any word and wrote it into the header; `./os words` then
        # refused the same word as "no domain called that". One list, both ways.
        # Close enough to be a typo: "baking" was offered "marketing" (audit,
        # 2026-10-29).
        near = difflib.get_close_matches(domain, known, n=1, cutoff=0.75)
        die(f"no subject called '{domain}' — this folder knows: {', '.join(known)}"
            + (f"\n     did you mean --domain {near[0]}?" if near else "")
            + "\n     leave --domain off and it is guessed from the name")
    if preview:
        return _new_preview(os_, kind, title, domain, tags, anyway)
    def refuse_a_twin() -> None:
        if not anyway and resolved_kind in ("project", "note"):
            clash = Finder(os_).like(title, kinds=("project", "note"))
            if clash:
                first = clash[0]
                die(f"you already have {first.ident} \u2014 \"{first.title}\".\n"
                    f"     Look at it:        ./os show {handle(first)}\n"
                    "     Want both anyway?  add --anyway to this command")
    refuse_a_twin()
    # One put away under this name is easy to forget, and starting it again
    # from blank said nothing about it. Said, not refused: both can be wanted.
    put_away = Finder(os_).like(title, kinds=("project", "note"), archived=True) \
        if not anyway and resolved_kind in ("project", "note") else []
    with Lock(os_, "new"):
        # Again, now nothing else can start one: three of the same at once
        # each looked before any was made, and made three (audit, 2026-10-29).
        refuse_a_twin()
        path = Creator(os_).create(kind, title, domain, tags)
        phase = NEW_STATUS.get(kind.lower(), PUSHING)
        Indexer(os_).build()
    resolved = KIND_ALIASES.get(kind.lower(), kind)
    meta, _ = parse_frontmatter(read_text(path))
    # A learning note starts as only a name, so the names and figures in it
    # are all there is to go on: two of them already in one note is worth a look.
    same: list = []
    if NEW_SHAPE.get(kind.lower()) == "learning":
        same = [(it, shared) for it, shared in Twins(os_).like_title(title)
                if Path(path) not in (it.path, it.spine)]
    Out.title("started")
    Out.ok(title)
    Out.note(os_.rel(path))
    if put_away:
        Out.warn(f"you put away \"{trunc(put_away[0].title, 44)}\" before — it's in the archive")
        Out.note(f"meant that one?  ./os undo, then  ./os back {handle(put_away[0])}")
    if same:
        it, shared = same[0]
        Out.warn(f"\"{trunc(it.title, 44)}\" may already cover this: "
                 f"it mentions {Twins.say(shared)} too")
        Out.note(f"look at it first:  ./os show {handle(it)}   ·   not needed after all?  ./os undo")
    if resolved in ("skill", "agent"):
        # The template no longer writes a "Use when <…>" blank: left unfilled,
        # an AI read the blank as the words to listen for (2026-10-28).
        Out.note("open it and add, to its description, \"Use when …\" in the words "
                 "you'd say to ask for it")
    if resolved == "skill":
        # "/name" alone is Claude Code's way in. Every other AI finds skills
        # the way AGENTS.md says: by the request fitting one.
        Out.note("ask your AI for it by name — in Claude Code,  /" + Path(path).parent.name
                 + "  runs it too")
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
        die(f"which one?   ./os {'push' if phase == PUSHING else 'hold'} fix-the-boiler"
            "      (run ./os to see the names)")
    _one_name(os_, argv, "push" if phase == PUSHING else "hold")
    item = the_one(os_, argv[0], "push" if phase == PUSHING else "hold")
    # A note can turn out to be something to do. "Buy a birthday present for
    # Sarah" was filed as a note, and nothing could say otherwise: push refused
    # it, new refused the near-duplicate, and moving it by hand breaks undo.
    becomes_work = item.kind == "note" and bool(item.spine) \
        and (item.spine == item.path or item.path.is_dir())
    if item.kind == "archive":
        # Put away, not gone: say how to get it out, not just that it's wrong.
        die(f"{item.ident} is archived — bring it back first:  ./os back {handle(item)}")
    if item.kind != "project" and not becomes_work:
        die(f"{item.ident} is {KIND_WORDS.get(item.kind, item.kind)}, not work — "
            "only work is pushed or held")
    if not (item.spine and item.spine.exists()):
        no_header(os_, item, "push" if phase == PUSHING else "hold")
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
        rewritten = set_fields(text, changes, drop=drop)
        if phase == HOLDING and not becomes_work:
            rewritten = with_how_often(rewritten)
        write_text(item.spine, rewritten)
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
        if becomes_work and phase == HOLDING:
            page = Scanner(os_).spine_of(item.path) if item.path.is_dir() else item.path
            if page and page.is_file() and page.suffix.lower() in TEXT_SUFFIXES:
                # Whole, or stop: read_text stops at 400,000 characters, and
                # a 574 KB note came back cut (delete audit, 2026-10-29).
                was_text = read_to_rewrite(page)
                if with_how_often(was_text) != was_text:
                    os_.snapshot(page)
                    write_text(page, with_how_often(was_text))
                    os_.record("edit", os_.rel(page))
        os_.commit(f"{item.ident} {was} -> {phase}")
        Indexer(os_).build()
    Out.title("pushing" if phase == PUSHING else "holding")
    Out.ok(item.title)
    if becomes_work:
        Out.note(f"it's work now — it's in {os_.rel(item.path)}")
        item.ident = handle(item.path.name)
        say_left_behind(os_)
    # Each hint only when the file still needs it: told to give a next action
    # that was already written, people looked for what they had missed.
    spine = item.spine if item.spine and item.spine.exists() else \
        next((item.path / n for n in ("README.md", "index.md") if (item.path / n).exists()), None)
    if phase == HOLDING:
        Out.note("it won't be counted as on the go, and it won't be nagged for "
                 "going quiet — that's what holding means")
        if spine and section_empty(spine, "How often"):
            Out.note("say how often you tend to it under ## How often")
        # Kept, since the words are theirs and a hold is often pushed again,
        # but said: held work has no next action (audit, 2026-10-29).
        if spine and not section_empty(spine, "Next action"):
            Out.note("its old next action is still written — clear it if there's nothing to do now")
    elif not becomes_work:
        if spine and section_empty(spine, "Next action"):
            Out.note("it's on the go again — give it a next action")
        else:
            Out.note("it's on the go again")
    if becomes_work:
        # Undo works straight after. Later on, the header is the way back:
        # sort reads type: note and files it with the notes again.
        Out.note(f"a note after all?  ./os undo   ·   later:  ./os note {handle(item.path.name)}")
    else:
        Out.note(f"back the other way?  ./os {'push' if phase == HOLDING else 'hold'} {handle(item)}")
    Out.raw()
    return 0


#: How long a claim stands before it is read as forgotten. Chats crash, laptops
#: sleep and nobody types `./os release` on the way out, so a claim that never
#: expired would lock a thing away for good.
CLAIM_STALE_HOURS = 12


def session_label() -> str:
    """The id an AI or a terminal gives this chat, or "" when there is none.

    Claude Code sets CLAUDE_CODE_SESSION_ID and a terminal sets
    TERM_SESSION_ID. Reading only CLAUDE_SESSION_ID, which Claude Code doesn't
    set, a chat's claim called that chat gone on its very next command.
    CLAUDE_SESSION_ID is still read, for anything that sets it."""
    for var in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "TERM_SESSION_ID"):
        value = str(os.environ.get(var) or "").strip()
        if value:
            return one_line(value).replace(" ", "-")[:40]
    return ""


#: Shells. One started to run a single command (`bash -c "./os claim x"`) is
#: how most AIs run each command, and it is gone by the next one.
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "tcsh", "csh", "busybox"}
#: Programs that only start one command and wait for it, so they come and go
#: with it too.
ONE_COMMAND = {"timeout", "time", "sudo", "doas", "xargs", "script", "caffeinate"}
#: The walk up the process table gives one answer for the whole of a run.
_CHAT_PROCESS: dict = {}


def _process(pid: int) -> tuple[int, list[str]] | None:
    """The parent of a running process and the words that started it, or None
    when this computer won't say. Linux keeps both under /proc; a Mac has no
    /proc, and `ps` answers there. Anything odd is None, never an error."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        words = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
        # The name in brackets can hold spaces and brackets of its own.
        return int(stat.rpartition(")")[2].split()[1]), [w for w in words.split("\0") if w]
    except (OSError, ValueError, IndexError):
        pass
    try:
        said = subprocess.run(["ps", "-ww", "-o", "ppid=", "-o", "args=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=3).stdout.split()
        return int(said[0]), said[1:]
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _only_one_command(words: list[str]) -> bool:
    """Is this a process that lives for one command only: a shell started
    with -c, or a program like `timeout` that runs one thing and waits?

    A shell started any other way is somebody's terminal, typing command
    after command, and lasts as long as they do."""
    name = Path(words[0]).name.lstrip("-") if words else ""
    if name in ONE_COMMAND:
        return True
    if name not in SHELLS:
        return False
    for word in words[1:]:
        if not word.startswith("-"):
            return False          # what it runs, or a script: the options are over
        if not word.startswith("--") and "c" in word:
            return True           # -c, -lc, -ec: the one command comes next
    return False


def chat_process() -> int:
    """The process that lasts as long as the chat: the AI itself, or the
    terminal somebody is typing into.

    An AI that gives no session id runs each command in a shell of its own,
    and that shell is gone by the next command. Labelled by it, a chat's own
    claim read "that chat is gone" at once, and any other chat could take it
    straight over. So this walks up past every shell and program there for
    one command only, to the first thing that outlasts them. When the
    process table can't be read, on any computer, it is the process that
    started this one, as it always was."""
    if "pid" not in _CHAT_PROCESS:
        pid = os.getppid()
        for _ in range(12):
            seen = _process(pid)
            if seen is None or seen[0] <= 1 or not _only_one_command(seen[1]):
                break
            pid = seen[0]
        _CHAT_PROCESS["pid"] = pid
    return _CHAT_PROCESS["pid"]


def claim_label() -> str:
    """Who this chat is, as far as the folder can tell.

    Two AI sessions in one folder are two runs of the same program: nothing
    tells them apart but this. The id the AI or terminal gives the chat comes
    first; failing that, the process that lasts as long as the chat does."""
    return session_label() or f"pid-{chat_process()}"


def this_chat() -> str:
    """The chat running this, by the id its AI or terminal gives it, or ""
    when there is none. The last resort in claim_label, a process id, is a
    best guess: good enough to say who is holding something, but a wrong one
    here would stop a chat's ./os undo of its own change."""
    return session_label()


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
    if ON_WINDOWS:
        # Signal 0 there is Ctrl-C to the whole console (fifteenth review).
        return not _alive_on_windows(int(hit.group(1)))
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


def no_header(os_: Zenith, item: Item, verb: str, change: str = "change") -> None:
    """Why there's no page to write in, said truly: a shortcut was sent to
    check --fix, which found nothing to mend (round 13, 2026-10-29)."""
    if item.path.is_symlink():
        target = Path(os.path.realpath(item.path))
        where = os_.rel(target) if inside(item.path, whole=True) else str(target)
        die(f"{item.ident} is a shortcut to {where} — ./os {verb} works on the thing itself, "
            "not a shortcut to it")
    die(f"{item.ident} has no header to {change} — ./os check --fix")


def _claimable(os_: Zenith, argv: list[str], verb: str, more: bool = False) -> Item:
    """The one item a command names. `more`: words may follow it (decide)."""
    if not argv or not argv[0].strip():
        die(f"which one?   ./os {verb} fix-the-boiler      (run ./os to see the names)")
    if more:
        _where_the_name_ends(os_, argv, verb, "what was decided")
    else:
        _one_name(os_, argv, verb)
    item = the_one(os_, argv[0], verb, _then(argv))
    if not (item.spine and item.spine.exists()):
        no_header(os_, item, verb, "write into")
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


#: A markdown link and where it points: `[text](here)`, `![picture](here)`,
#: `[text](<a name with spaces>)`, `[text](here "a title")`.
MD_LINK = re.compile(r"""(!?\[[^\]\n]*\]\()(<[^>\n]*>|[^)\s<][^)\n]*?)((?:\s+(?:"[^"\n]*"|'[^'\n]*'))?\s*\))""")
#: The other way to write one: a line `[name]: here` that `[text][name]` uses.
MD_REF = re.compile(r"^( {0,3}\[[^\]\n]+\]:[ \t]*)(<[^>\n]*>|\S+)", re.M)
#: A fence around code. A link shown inside one is an example, not a link.
MD_FENCE = re.compile(r"^ {0,3}(```|~~~)", re.M)
# `code` or ``code with a ` in it``: the same number of backticks each side
MD_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)[^\n]*?(?<!`)\1(?!`)")
#: Notes bigger than this are not read for links: nobody hand-writes links
#: into a 2 MB file, and reading thousands of them would make every sort slow.
RELINK_CAP = 2_000_000


def link_file(dest: str) -> str | None:
    """The file a link points at, spelt the way it is on disk, or None when
    it is not a file in here: a web address, a place on the same page, a
    path from the top of the disk.

    `dest` is where MD_LINK or MD_REF says it points, its title already left
    off. `<Mum's soup.md>` loses its brackets, and `Mum%27s%20soup.md`
    becomes Mum's soup.md. ./os check turned only %20 back into a space, so
    a link with %27 in it was a file nobody had, and called broken though it
    worked. Rename and check read links the same way, here."""
    angle = dest.startswith("<") and dest.endswith(">")
    path = (dest[1:-1] if angle else dest).partition("#")[0]
    if not path or path.startswith(("/", "~", "\\")) or "?" in path \
            or re.match(r"[A-Za-z][A-Za-z0-9+.\-]*:", path):
        return None
    return path if angle else unquote(path)


#: A heading, any level. Under `## Log` or `## Decisions` is the record.
RECORD_HEAD = re.compile(r"^(#{1,6})\s+(.*?)[\s#]*$")
RECORD_SECTIONS = ("log", "decisions")


def record_lines(text: str) -> list[bool]:
    """For each line of `text`, is it under a `## Log` or `## Decisions`
    heading? Those lines are history: a path in one says where something was
    on the day it was written, and stays true only if nothing rewrites it."""
    out, inside, level, fence = [], False, 0, ""
    for line in text.splitlines():
        bare = line.strip()
        mark = MD_FENCE.match(line)
        if mark and (not fence or mark.group(1) == fence):
            fence = "" if fence else mark.group(1)
        head = None if fence or mark else RECORD_HEAD.match(bare)
        if head and (not inside or len(head.group(1)) <= level):
            inside = head.group(2).strip().lower() in RECORD_SECTIONS
            level = len(head.group(1))
            out.append(False)
            continue
        out.append(inside)
    return out


#: A dated entry, as keep-the-record.sh reads one: "- 2026-10-29 · …".
DATED_LINE = re.compile(r"^\s*(?:[-*+]|#{1,6})?\s*[*_\[(]*\d{4}-\d\d-\d\d")


def dated_entries(text: str) -> list[bool]:
    """For each line, is it part of a dated entry under `## Log` or
    `## Decisions`: the dated line and the lines that carry it on, up to a
    blank line, the next item or a heading. Those are the lines
    keep-the-record guards (tenth review, 2026-10-29)."""
    marks, out, going = record_lines(text), [], False
    for mine, line in zip(record_lines(text), text.splitlines()):
        if not mine or not line.strip() or RECORD_HEAD.match(line.strip()):
            going = False
        elif DATED_LINE.match(line):
            going = True
        elif re.match(r"^\s*(?:[-*+]|\d+[.)])\s", line):
            going = False
        out.append(going)
    return out + [False] * (len(text.splitlines()) - len(out))


def outside_the_record(text: str, change, dated: bool = False) -> str:
    """`change` applied to all of `text` but its `## Log` and `## Decisions`
    lines, which are left exactly as they are (AGENTS.md, rule 4). `dated`:
    only the dated entries there, the lines keep-the-record guards."""
    lines = text.splitlines(keepends=True)
    marks = record_lines(text)
    if dated:
        marks = [m and bool(DATED_LINE.match(line)) for m, line in zip(marks, lines)]
    if not any(marks):
        return change(text)
    out, run, kept = [], [], False
    for line, mine in zip(lines, marks + [False] * (len(lines) - len(marks))):
        if run and mine != kept:
            out.append("".join(run) if kept else change("".join(run)))
            run = []
        run.append(line)
        kept = mine
    if run:
        out.append("".join(run) if kept else change("".join(run)))
    return "".join(out)


def outside_links(text: str, change) -> str:
    """`change` applied to all of `text` but where its links point, which the
    links pass looks after: a path said in full was also changed inside a
    link to a project's own file, which then led nowhere (sweep review,
    2026-10-29)."""
    spans = sorted([m.span(2) for m in MD_LINK.finditer(text)] + [m.span(2) for m in MD_REF.finditer(text)])
    if not spans:
        return change(text)
    out, at = [], 0
    for start, end in spans:
        if start < at:
            continue
        out.append(change(text[at:start]))
        out.append(text[start:end])
        at = end
    out.append(change(text[at:]))
    return "".join(out)


class Relinker:
    """Keep links between notes working when ./os moves things.

    Sort tucks notes into subject folders, close moves work into Archive,
    rename gives something a new name. Each of those left links saying where
    a thing used to be: a note linking `pizza-dough.md` broke the moment both
    were sorted into different folders, and `./os check` then reported a
    broken link that ./os itself had made. The old fix only swapped one name
    for another, so it could not follow a note into a new folder.

    After the moves of one run, every link is worked out again both ways, in
    one pass: links in other notes that reach something that moved, and links
    inside a moved note that reach anything at all. Each file is kept aside
    first, so ./os undo puts every word back with the move.

    What it never does: change a web address, change a link that was already
    broken before the move, or touch anything in a `sources/` folder or a
    `decisions.md`. Those are never rewritten, even to fix a link."""

    def __init__(self, os_: "Zenith", steps: list[dict]):
        self.os = os_
        self.root = str(os_.root)
        mine = MARKER + "/"
        #: (from, to), in the order they happened, inside the folder only.
        #: A move in or out of .os/ is a save on its way in, not a move a
        #: link could have pointed at.
        self.moves = [(s["src"], s["dst"]) for s in steps
                      if s.get("action") == "move" and s.get("src") and s.get("dst")
                      and not s["src"].startswith(mine) and not s["dst"].startswith(mine)]
        self.made = {s["src"] for s in steps if s.get("action") == "create"}

    # -- where things were, and where they are now ----------------------------

    def forward(self, rel: str) -> str:
        """Where the thing that was at `rel` before this run is now."""
        for src, dst in self.moves:
            if rel == src:
                rel = dst
            elif rel.startswith(src + "/"):
                rel = dst + rel[len(src):]
        return rel

    def backward(self, rel: str) -> str:
        """Where the thing now at `rel` was before this run."""
        for src, dst in reversed(self.moves):
            if rel == dst:
                rel = src
            elif rel.startswith(dst + "/"):
                rel = src + rel[len(dst):]
        return rel

    def _abs(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def _rel(self, path: str) -> str | None:
        out = os.path.relpath(path, self.root)
        return None if out == ".." or out.startswith("../") else out.replace(os.sep, "/")

    # -- one link ------------------------------------------------------------

    def retarget(self, dest: str, was_in: str, now_in: str) -> str | None:
        """The new way to write `dest`, or None to leave it exactly as it is.

        `was_in` is the folder the note sat in before this run, `now_in` the
        one it sits in now: the same folder unless the note itself moved."""
        angle = dest.startswith("<") and dest.endswith(">")
        raw = dest[1:-1] if angle else dest
        path, hashmark, anchor = raw.partition("#")
        plain = link_file(dest)
        if plain is None:
            return None      # a web address, a place on this page, or not ours
        was = os.path.normpath(os.path.join(was_in, plain))
        inside = self._rel(was)
        target = os.path.normpath(self._abs(self.forward(inside))) if inside is not None else was
        if os.path.normpath(os.path.join(now_in, plain)) == target:
            return None      # still lands where it did
        if not os.path.lexists(target):
            return None      # broken before this run: leave it for ./os check to report
        new = os.path.relpath(target, now_in).replace(os.sep, "/")
        if path.endswith("/") and not new.endswith("/"):
            new += "/"
        if path.startswith("./") and not new.startswith("."):
            new = "./" + new
        if angle:
            return "<" + new.replace("<", "%3C").replace(">", "%3E") + hashmark + anchor + ">"
        if " " in path and "%" not in path:
            # written with plain spaces: kept that way, so it reads as theirs
            new = new.replace("(", "%28").replace(")", "%29")
        else:
            new = md_link(new)
        return new + hashmark + anchor if hashmark else new

    #: A line that starts a list item, whose indented lines after a blank
    #: are more of the item, not code.
    LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")

    def rewrite(self, text: str, was_in: str, now_in: str, keep: list | None = None) -> str:
        """`text` with every link that needs it pointed the right way.
        Code is left alone, fenced or indented: a link shown inside it is an
        example. An indented block counted only after a blank line, and not
        inside a list item (round 14, 2026-10-29)."""
        out, inside, mark = [], False, ""
        indented, blank, listed, headed, item_col = False, True, False, False, 0

        def indent_of(line: str) -> int:
            wide = line.expandtabs(4)
            return len(wide) - len(wide.lstrip())
        for n, line in enumerate(text.splitlines(keepends=True)):
            fence = MD_FENCE.match(line)
            empty = not line.strip()
            deep = line.startswith(("    ", "\t"))
            if keep and n < len(keep) and keep[n]:
                # A line of the record, word for word; read through, so a
                # fence or a list in it still counts (tenth review, 2026-10-29).
                if fence and (not inside or fence.group(1) == mark):
                    inside, mark = (not inside), fence.group(1)
                out.append(line)
            elif inside or fence:
                if fence and (not inside or fence.group(1) == mark):
                    inside, mark = (not inside), fence.group(1)
                out.append(line)
            elif indented and (empty or deep):
                out.append(line)
            elif deep and (blank or headed) and (not listed or indent_of(line) >= item_col + 4):
                # Straight after a heading is code too, and inside a list it
                # takes four more than the item's own words (tenth review,
                # 2026-10-29).
                indented = True
                out.append(line)
            else:
                indented = False
                out.append(self._rewrite_prose(line, was_in, now_in))
            if not empty:
                headed = bool(re.match(r"^ {0,3}#{1,6}(\s|$)", line))
                # Carried on by any indented line: a wrapped item's second
                # line is two spaces in (tenth review, 2026-10-29).
                item = self.LIST_ITEM.match(line)
                if item:
                    # Code inside an item is four in from the item's own
                    # words, however deep the item: eight spaces was right
                    # only for one at the margin (twelfth review).
                    item_col = len(item.group(0).expandtabs(4))
                listed = bool(item) or (listed and line[:1] in " \t")
            blank = empty
        return "".join(out)

    def _rewrite_prose(self, text: str, was_in: str, now_in: str) -> str:
        """Every link in `text` pointed the right way, but one that starts in
        backticks, or points from inside them: that is an example, as ./os
        check reads it. Spans are blanked in a copy to find the links, so
        `[`list`](a.md)`, code in a link's words, is still a link (sweep and
        ninth reviews, 2026-10-29)."""
        spans = [m.span() for m in MD_CODE_SPAN.finditer(text)] if "`" in text else []
        # Built once, and looked up by halving: a line of 8000 spans took
        # seven seconds (tenth review, 2026-10-29).
        pieces, at = [], 0
        for a, b in spans:
            pieces += [text[at:a], "x" * (b - a)]
            at = b
        look = "".join(pieces) + text[at:]
        starts = [a for a, _ in spans]

        def coded(a: int, b: int) -> bool:
            n = bisect.bisect_left(starts, b) - 1
            return n >= 0 and spans[n][1] > a

        edits = []
        for m in list(MD_LINK.finditer(look)) + list(MD_REF.finditer(look)):
            a, b = m.span(2)
            if coded(m.start(), m.start() + 1) or coded(a, b):
                continue
            new = self.retarget(text[a:b], was_in, now_in)
            if new is not None:
                edits.append((a, b, new))
        out, at = [], 0
        for a, b, new in sorted(edits):
            if a < at:
                continue
            out += [text[at:a], new]
            at = b
        out.append(text[at:])
        return "".join(out)

    # -- the whole folder ----------------------------------------------------

    def _clues(self) -> list[str]:
        """Words a note must contain to link to anything that moved: a link
        into a moved thing has to say its name, one way or another."""
        out = set()
        for src, _ in self.moves:
            name = src.rsplit("/", 1)[-1]
            out.update({name, quote(name), md_link(name), name.replace(" ", "%20")})
        return sorted(out)

    #: Folders nobody wrote by hand: never read for links or paths.
    SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
    #: Files looked at for a path left behind, at most: a folder holding a
    #: big code project must not make every move slow.
    LOOK_AT = 20_000

    def _notes(self):
        """Every note whose links are kept working, and the skills: the
        pages and how-tos in Work, Notes, Archive and .claude/skills."""
        tops = [self.os.root / bucket for bucket in self.os.buckets()]
        tops.append(self.os.root / ".claude" / "skills")
        for base in tops:
            for top, dirs, files in os.walk(base):
                # sources/ is never rewritten, so there is nothing to look at there
                # Nor into another copy of a folder like this one, a worktree,
                # or a project with a git history of its own: those keep
                # their own paths, as left_behind() already knew (sweep
                # review, 2026-10-29).
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in self.SKIP_DIRS
                           and d not in IGNORE_FOLDERS and d.lower() != "sources"
                           and d != "worktrees" and not another_os(Path(top) / d)]
                # A project with a git history of its own keeps its own
                # paths, but its page is theirs: a work item that was a git
                # repo lost its README's links (ninth review, 2026-10-29).
                for d in [d for d in dirs if (Path(top) / d / ".git").exists()]:
                    dirs.remove(d)
                    # Only an item's own page, straight under Work or Notes:
                    # a repo kept deeper is someone else's (tenth review).
                    # A grouping folder holds items too: after sort --regroup
                    # one with its own git history lost its page's links
                    # (twelfth round, 2026-10-29).
                    if Path(top) != base and not Scanner.is_category(Path(top)):
                        continue
                    for page in ("README.md", "index.md"):
                        if (Path(top) / d / page).is_file():
                            yield Path(top) / d / page
                            break
                for name in files:
                    if not name.lower().endswith((".md", ".markdown", ".txt")) \
                            or name.lower() == "decisions.md" or name in Finder.MADE_HERE \
                            or name.startswith("._"):
                        continue
                    yield Path(top) / name

    # -- a path written out in full, like Notes/Finance/rent.md ----------------

    def _old_paths(self) -> list[str]:
        """Where things were, as a path from the top of this folder, for each
        thing this run moved somewhere else. Only ones with a folder in them:
        `draft.md`, brought in from the top, is a name any note might say."""
        out = {src for src, _ in self.moves if "/" in src and self.forward(src) != src}
        return sorted(out, key=len, reverse=True)

    def _path_pattern(self, strict: bool) -> "re.Pattern | None":
        """A path where something was, said in full. `strict`, for changing
        it: not just after a slash, so `../Notes/rent.md`, a link from one
        note to another that the links pass already handles, and a path from
        the top of the disk are left as they are. Not strict, for saying
        where it is still named: any of those."""
        olds = self._old_paths()
        if not olds:
            return None
        before = r"(?:(?<=^\./)|(?<=[^\w.\-/~]\./)|(?<![\w.\-/~]))" if strict \
            else r"(?<![\w.\-])"
        tail = r"(?:/[^\s\"'`<>()\[\]{},;|*]*)?" if strict else ""

        def one(o: str) -> str:
            # Never the start of another thing's name beside it: "Work/Kitchen"
            # matched "Work/Kitchen Tiles", which then pointed at a folder
            # that never was (twentieth review, round 25).
            folder, _, base = o.rpartition("/")
            try:
                beside = [p.name[len(base):] for p in (self.os.root / folder).iterdir()
                          if p.name != base and p.name.startswith(base) and len(p.name) > len(base)]
            except OSError:
                beside = []
            longer = [rest for rest in beside if not re.match(r"[\w\-]|\.\w", rest)]
            guard = "(?!" + "|".join(re.escape(rest) + r"(?![\w\-]|\.\w)" for rest in longer) + ")" \
                if longer else ""
            return re.escape(o) + guard
        return re.compile(before + "(" + "|".join(one(o) for o in olds) + ")"
                          + r"(?![\w\-]|\.\w)" + tail, re.M)

    def _writes_paths(self, rel: str) -> bool:
        """Is this a page whose plain paths ./os keeps right: a .md or .txt
        in Work, Notes or .claude/skills? Anything else is only pointed out."""
        top = rel.split("/", 1)[0]
        role = self.os.buckets().get(top, {}).get("role")
        return role in ("project", "note") or rel.startswith(".claude/skills/")

    def left_behind(self, limit: int = 200) -> list[dict]:
        """Every line of every other file here that still names a place
        something used to be: code, settings, a page in Archive, CLAUDE.md.
        Those are not changed, only said, so they can be fixed by hand."""
        found = self._path_pattern(strict=False)
        if found is None:
            return []
        needles = [o.encode("utf-8") for o in self._old_paths()]
        out: list[dict] = []
        root = self.os.root
        read = 0
        for top, dirs, files in os.walk(root):
            here = Path(top)
            # Not into another copy of a folder like this one, kept here or
            # made by an AI as a worktree: it keeps its own paths.
            dirs[:] = sorted(d for d in dirs
                             if d not in self.SKIP_DIRS and d not in IGNORE_FOLDERS
                             and d.lower() != "sources" and d != MARKER and d != "worktrees"
                             and (not d.startswith(".") or (here == root and d == ".claude"))
                             and not another_os(here / d))
            for name in sorted(files):
                read += 1
                if read > self.LOOK_AT:
                    return out
                if name.lower() == "decisions.md" or name in Finder.MADE_HERE \
                        or name.startswith("._") or name.endswith(".card.md"):
                    continue
                path = here / name
                try:
                    if path.is_symlink() or path.stat().st_size > RELINK_CAP:
                        continue
                    data = path.read_bytes()
                except OSError:
                    continue
                if not any(n in data for n in needles) or b"\x00" in data[:8000]:
                    continue
                rel = self.os.rel(path)
                text = data.decode("utf-8", "replace")
                history = record_lines(text) if name.lower().endswith(".md") else []
                for n, line in enumerate(text.splitlines(), 1):
                    if n <= len(history) and history[n - 1]:
                        continue      # the record says where it was then, and stays
                    if name.lower().endswith(".md") and line.startswith("origin:"):
                        continue      # close wrote it: where it came from, on purpose
                    # Not the old path where it sits inside the new one:
                    # Notes/cake.md is the end of Archive/2026/Notes/cake.md,
                    # and every close of a linked note said to fix by hand a
                    # link already put right (audit, 2026-10-29).
                    # Nor a path that is this file's own, from its own folder
                    # (round 14, 2026-10-29).
                    m = next((m for m in found.finditer(line)
                              if not line[:m.end()].endswith(self.forward(m.group(1)))
                              and not os.path.lexists(here / m.group(1))), None)
                    if m:
                        out.append({"file": rel, "line": n, "text": line.strip(),
                                    "old": m.group(1), "new": self.forward(m.group(1))})
                        if len(out) >= limit:
                            return out
        return out

    def run(self) -> int:
        """Fix every link these moves touched, and every path said in full
        in a page that names where a moved thing was. Returns how many files
        changed."""
        if not self.moves:
            return 0
        clues = self._clues()
        paths = self._path_pattern(strict=True)
        changed = 0
        for path in self._notes():
            try:
                if path.is_symlink() or path.stat().st_size > RELINK_CAP:
                    continue
                now = self.os.rel(path)
                if now in self.made:
                    continue
                was = self.backward(now)
                data = path.read_bytes()
            except OSError:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            linked = text
            if "](" in text or "]:" in text:
                looked = was != now or any(c in text for c in clues)
                if not looked:
                    # Any letter of a name can be written as %XX, and a web page
                    # writes Mum's%20fish%2C%20chips.md, which is none of the ways
                    # above. Read as the letters they stand for, every way of
                    # writing the name says it.
                    decoded = unquote(text) if "%" in text else ""
                    looked = any(c in decoded for c in clues)
                if looked:
                    # Not under ## Log or ## Decisions either: those lines
                    # are only ever added to, links and all, and
                    # keep-the-record guards them (round 14, 2026-10-29).
                    linked = self.rewrite(text, os.path.dirname(self._abs(was)), str(path.parent),
                                          keep=dated_entries(text))
            # A path said in full, `Notes/Finance/rent.md`, in a skill or a
            # page: a skill and a bot's notes went on naming where a note had
            # been after sort moved it (reported, 2026-10). Inside code fences
            # too: in a skill, that is the command it runs.
            # Not under ## Log or ## Decisions: "it is in Archive/…" was true
            # the day it was written, and rewritten it says what nobody saw.
            # A link there is still kept working above; its words aren't changed.
            def own_or(m: re.Match, here: Path) -> str:
                # A path that is also this page's own, from its own folder,
                # is about that and stays (round 14, 2026-10-29).
                return m.group(0) if os.path.lexists(here / m.group(0)) else self.forward(m.group(0))

            if paths is not None and self._writes_paths(now):
                linked = outside_the_record(
                    linked, lambda part: outside_links(
                        part, lambda bit: paths.sub(lambda m: own_or(m, path.parent), bit)))
            if linked == text:
                continue
            if not self.os.keep_before(path, was):
                continue      # couldn't keep a copy for undo: leave it as it is
            try:
                write_text(path, linked)
            except OSError:
                continue
            self.os.record("edit", now)
            changed += 1
        return changed


def say_left_behind(os_: "Zenith", limit: int = 10) -> None:
    """Say each file ./os didn't change that still names where something
    was, with the line, so it can be fixed by hand. Code and settings are
    never edited: a path in them may mean something ./os can't know."""
    left = os_.left_behind
    if not left:
        return
    files = len({m["file"] for m in left})
    Out.warn(f"{files} other file{'' if files == 1 else 's'} still "
             f"name{'s' if files == 1 else ''} where something was — ./os doesn't "
             "change those, so fix them by hand:")
    for m in left[:limit]:
        Out.note(f"{m['file']} line {m['line']}:  {m['old']}  is now  {m['new']}")
        Out.note("    " + trunc(m["text"], 76))
    if len(left) > limit:
        Out.note(f"...and {len(left) - limit} more lines")


def cmd_rename(os_: Zenith, argv: list[str]) -> int:
    """Give something a new name, on disk and in its header, in one move.

    The name is the handle, so renaming by hand meant a title that said one
    thing and a folder that said another, and a `./os show` that found neither.
    Folders are Title Case With Spaces, notes stay kebab-case files, the
    card beside a file moves with it, and links to it follow."""
    if not argv or not argv[0].strip():
        die('which one?   ./os rename fix-the-boiler "Replace the boiler"')
    _where_the_name_ends(os_, argv, "rename", "the new name")
    item = the_one(os_, argv[0], "rename", _then(argv))
    if item.kind not in ("project", "note", "asset", "archive"):
        die(f"{item.ident} is {KIND_WORDS.get(item.kind, item.kind)} — rename its folder by hand")
    title = one_line(" ".join(argv[1:])).strip().strip('"')
    if not title:
        die(f'called what?   ./os rename {handle(item)} "the new name"')
    if not re.search(r"[^\W_]", title, re.UNICODE):
        # Said, not "called what?": they did say a name (audit, 2026-10-29).
        die(f"\"{title}\" has no letters or numbers in it, and a file name needs one — "
            f'try something like  ./os rename {handle(item)} "Party plans"')
    slug = slugify(title, 44, at_word=True)
    if not slug:
        die("that name has no letters or numbers in it to make a file name from")
    warn_if_claimed(os_, item)
    is_dir = not item.path.is_file()
    wanted = folder_name(title, slug) if is_dir else f"{slug}{item.path.suffix}"
    target = item.path.with_name(wanted)
    if target == item.path and title == item.title:
        # It said "renamed" and kept a step to undo for nothing (audit, 2026-10-28).
        Out.title("rename")
        Out.ok(f"it's already called {title} — nothing changed")
        Out.raw()
        return 0
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
        # A folder with no page of its own is called by its name alone: the
        # note it reads through keeps its own title (Scanner.borrows_page).
        if is_dir and Scanner.borrows_page(moved, spine):
            spine = None
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
            # A card names the file it is about. Renamed, it went on naming
            # the old one, in its header and its first line.
            if headed == card and text is not None and moved.name != item.path.name:
                old = item.path.name
                if str(meta.get("source") or "") == old:
                    flags["source"] = moved.name
                if f"`{old}`" in body:
                    body = body.replace(f"`{old}`", f"`{moved.name}`")
                    retitled = True
            if text is not None and (retitled or flags or drop):
                write_text(headed, set_fields(text, flags, drop=drop,
                                              body=body if retitled else None))
            os_.record("edit", os_.rel(headed))
        os_.save_state()
        os_.commit(f"renamed {item.ident} -> {moved.stem if moved.is_file() else moved.name}")
        relinked = os_.relinked
        Indexer(os_).build()
    Out.title("renamed")
    Out.ok(f"{item.title} \u2192 {title}")
    Out.note(os_.rel(moved) + f"   ·   ./os show {handle(moved.stem if moved.is_file() else moved.name)}")
    if relinked:
        Out.note(f"links to it now use the new name, in {relinked} "
                 + ("file" if relinked == 1 else "files"))
    say_left_behind(os_)
    Out.note("wrong?  ./os undo")
    Out.raw()
    return 0


def cmd_note(os_: Zenith, argv: list[str]) -> int:
    """Turn a piece of work back into a note: nothing to do or keep up, only
    something to look up later.

    `./os push` made a note into work, and the way back was to change the
    header by hand and run sort (audit, 2026-10-28). Every word stays: only the
    header's type and status change, and it moves from Work/ to Notes/."""
    if not argv or not argv[0].strip():
        die("which one?   ./os note fix-the-boiler      (run ./os to see the names)\n"
            '     to write a new note:  ./os save "..."   or   ./os new note "..."')
    _one_name(os_, argv, "note")
    item = the_one(os_, argv[0], "note")
    if item.kind == "archive":
        die(f"{item.ident} is archived — bring it back first:  ./os back {handle(item)}")
    if item.kind == "note":
        die(f"{item.ident} is a note already")
    if item.kind != "project":
        die(f"{item.ident} is {KIND_WORDS.get(item.kind, item.kind)}, not work — "
            "only work is turned back into a note")
    if not (item.spine and item.spine.exists()):
        no_header(os_, item, "note")
    warn_if_claimed(os_, item)
    # A folder holding only its README goes back as one file, the shape a
    # note has. Moved whole, a note pushed and turned back was a folder
    # around one file (audit, 2026-10-29).
    clutter = (".DS_Store", "Thumbs.db", "desktop.ini")

    def where_to() -> tuple[bool, Path]:
        alone = item.path.is_dir() and item.spine.parent == item.path \
            and all(p == item.spine or p.name in clutter for p in item.path.iterdir())
        dest = os_.root / os_.bucket_for_role("note") / \
            (slugify(item.title) + item.spine.suffix if alone else item.path.name)
        if dest.exists() or dest.is_symlink():
            die(f"there is already something called {dest.name} in {os_.rel(dest.parent)}")
        return alone, dest
    where_to()
    with Lock(os_, "note"):
        # Again, now nothing else can write in it: decided before the lock, a
        # file put in the folder while it waited was deleted with the clutter
        # (review, 2026-10-29).
        alone, dest = where_to()
        text = read_to_rewrite(item.spine)
        meta, _body = parse_frontmatter(text)
        was = normalize_status(meta.get("status"), "project")
        os_.snapshot(item.spine)
        flags, drop = unflag_fields(meta)
        changes = {"type": "note", "updated": today()}
        changes.update(flags)
        write_text(item.spine, set_fields(text, changes, drop=tuple(drop) + ("status",)))
        os_.record("edit", os_.rel(item.spine))
        if alone:
            folder = item.path
            item.path = os_.move_item(item.spine, dest)
            for junk in folder.iterdir():
                if junk.name in clutter:
                    junk.unlink()
            try:
                folder.rmdir()
            except OSError:
                # Something else landed in it, from outside ./os: it stays,
                # and is said (review, 2026-10-29).
                try:
                    stays = [p.name for p in folder.iterdir()][:3]
                except OSError:
                    stays = []
                Out.warn(f"{os_.rel(folder)} stays, since {', '.join(stays)} came into it meanwhile "
                         "— it isn't part of the note" if stays else
                         f"{os_.rel(folder)} couldn't be taken away, so it stays, empty")
        else:
            item.path = os_.move_item(item.path, dest)
        os_.commit(f"{item.ident} {was} -> note")
        Indexer(os_).build()
    Out.title("note")
    Out.ok(item.title)
    Out.note(f"it's a note now — it's in {os_.rel(item.path)}")
    say_left_behind(os_)
    # A file's name without its .md: `./os push pasta-recipe-md` found nothing.
    back = item.path.stem if item.path.is_file() else item.path.name
    Out.note(f"wrong?  ./os undo   ·   something to do after all?  ./os push {handle(back)}")
    Out.raw()
    return 0


def cmd_hold(os_: Zenith, argv: list[str]) -> int:
    return _set_phase(os_, argv, HOLDING)


def cmd_push(os_: Zenith, argv: list[str]) -> int:
    return _set_phase(os_, argv, PUSHING)


def cmd_find(os_: Zenith, argv: list[str]) -> int:
    as_json = _flag(argv, "--json")
    notes_only = _flag(argv, "--notes")
    kind = _opt(argv, "--kind")
    bucket = _opt(argv, "--in")
    limit = _count(argv, "--limit", 20)
    # A bare `--` says what follows is words, not options, and is not itself
    # one: `./os find -- "("` searched for "-- (" (audit, 2026-10-28).
    if "--" in argv:
        argv.remove("--")
    query = " ".join(argv).strip()
    if not query:
        die("what are you looking for?   ./os find boiler")
    # In the folder's own words: `--kind work` found nothing at all, since
    # inside it is "project", and `--kind bogus` said only "nothing matched"
    # (audit, 2026-10-28).
    if kind:
        said = kind
        kind = kind.lower()
        kind = next((k for k, w in KIND_LABEL.items() if w == kind), TYPE_FROM_DISK.get(kind, kind))
        if kind not in KIND_LABEL:
            die(f"there is no kind called {said} — one of: "
                + ", ".join(sorted(set(KIND_LABEL.values()))))
    if bucket:
        named = {b.lower(): b for b in os_.buckets()}
        if bucket.lower() not in named:
            die(f"there is no {bucket}/ here — one of: {', '.join(os_.buckets())}")
        bucket = named[bucket.lower()]
    finder = Finder(os_)
    hits = finder.search(query, limit=limit, kind=kind, bucket=bucket, notes_only=notes_only)

    def inside(item) -> dict:
        """The file in a thing's folder its words were found in, when it
        was not the thing's own page."""
        where = finder.found_in.get(str(item.path))
        return {"file": os_.rel(where)} if where is not None else {}
    if as_json:
        print(json.dumps([{"score": s, "id": i.ident, "title": i.title, "kind": i.kind,
                           "path": os_.rel(i.path), "snippet": sn, **inside(i)}
                          for s, i, sn in hits], indent=2))
        # [] still printed, so whatever reads the list reads an empty one; the
        # exit is 1, as plain `./os find` and grep give for nothing found (#41).
        return 0 if hits else 1
    Out.title("found", f'"{query}"')
    # Offered as a command to type, never searched for on its own: `bike` is
    # a real word, and bake is not what they asked for.
    words = [finder.near.get(w, w) for w in re.split(r"\s+", unaccented(nfc(query).lower().strip())) if w]
    more = (["--notes"] if notes_only else []) \
        + (["--kind", KIND_LABEL.get(kind, kind)] if kind else []) + (["--in", bucket] if bucket else [])
    offer = paint("./os find " + " ".join(shell_word(w) for w in words + more), S.GOLD)
    if not hits:
        Out.warn("nothing matched — try fewer words, or a different one")
        if finder.near:
            Out.raw("  " + paint("try a near word:  ", S.MUTE) + offer)
        Out.raw()
        return 1
    for score, item, snippet in hits:
        Out.raw("  " + paint(item.title, S.B)
                + paint(f"  · {KIND_LABEL.get(item.kind, item.kind)}", S.FAINT))
        Out.raw("          " + paint(trunc(os_.rel(item.path), 74), S.MUTE))
        where = inside(item).get("file")
        if where:
            Out.raw("          " + paint("in " + trunc(where[len(os_.rel(item.path)) + 1:], 71), S.MUTE))
        if snippet:
            Out.raw("          " + paint(trunc(snippet, 74), S.FAINT))
    if finder.near:
        # The other words found these. This one is in nothing, and may be a slip.
        missed = " or ".join(f'"{w}"' for w in finder.near)
        Out.raw()
        Out.raw("  " + paint(f"nothing has {missed} in it — try a near word:  ", S.MUTE) + offer)
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
        die("which one?   ./os show fix-the-boiler      (run ./os to see the names)")
    _one_name(os_, argv, "show")
    ident = argv[0]
    item = the_one(os_, ident, "show")

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
    # Work being pushed with no next action is said: show left the heading
    # off, and catchup and tidy had to open the file to find it empty (live
    # skill runs, 2026-10-29).
    if item.kind == "project" and item.status == PUSHING and "next action" in parts \
            and not _clean(re.sub(r"(?m)^\s*(?:[-*+]|\d+\.)\s+\[[xX]\].*$", "",
                                  parts.get("next action", "")), 1):
        Out.raw()
        Out.raw("  " + paint("NEXT", S.B, S.GOLD))
        Out.raw("    " + paint("none written yet — what's the first step?", S.AMBER))
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
            # `·` too: the Log's own mark, which came back as "· Fired." (audit, 2026-10-28)
            m = re.match(r"(\d{4}-\d{2}-\d{2})\s*[—:·-]?\s*(.*)", line)
            if m:
                out.append((m.group(1), m.group(2).strip() or line))
        return out

    entries = []       # (day, title, handle, text, id)
    for item in items:
        if item.bucket.startswith("_"):
            continue
        for day, text in logged(item):
            entries.append((day, item.title, handle(item), text, item.ident))
    days = sorted({d for d, *_ in entries} | {h["at"][:10] for h in history if h.get("at")},
                  reverse=True)
    if _flag(argv, "--json"):
        day = days[0] if days else ""
        print(json.dumps({"day": day,
                          # "id", as find and status say it; "name" kept for
                          # whatever already reads it (audit, 2026-10-28)
                          "touched": [{"title": t, "id": i, "name": n, "log": x}
                                      for d, t, n, x, i in entries if d == day],
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
    touched = [(t, n, x) for d, t, n, x, _i in entries if d == day]
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
        die("which one?   ./os open fix-the-boiler")
    _one_name(os_, argv, "open")
    item = the_one(os_, argv[0], "open")
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
    if fix:
        # --fix moves things now (Doctor._out_of_reach), so it takes its turn.
        with Lock(os_, "check"):
            result = Doctor(os_).run(fix=True)
            # Whatever was repaired, the list and the catalog must say so — a
            # skill's edited description sat stale in CATALOG.md until the next
            # unrelated rebuild (snag, 2026-08-31).
            Indexer(os_).build()
    else:
        result = Doctor(os_).run()
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
    if not fix and any(str(i.get("fix") or "").startswith("./os check --fix")
                       for i in result["issues"]):
        Out.note("./os check --fix   fixes everything that is safe to fix on its own")
    Out.raw()
    return 0 if not groups.get("error") else 1


def cmd_review(os_: Zenith, argv: list[str]) -> int:
    as_json = _flag(argv, "--json")
    report = Reviewer(os_).run(record=not as_json)
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
    # The whole list goes in: block() shows twelve and says how many more.
    # Cut to eight here first, the count said 8 and the rest vanished.
    block("on the go", report["active"],
          lambda r: pad(trunc(r["title"], 44), 46)
                    + paint(f"{r['age']}d ago", S.FAINT))
    block("not touched in a while", report["stale"],
          lambda r: pad(trunc(r["title"], 44), 46)
                    + paint(f"{r['age']}d ago", S.AMBER))
    asked = {r["id"] for r in report.get("still_keeping", [])}
    block("keeping level", [r for r in report.get("holding", []) if r["id"] not in asked],
          lambda r: pad(trunc(r["title"], 44), 46)
                    + paint(f"last tended {r['age']}d ago", S.FAINT))
    if report.get("still_keeping"):
        # A question only they can answer. Held work is quiet on purpose, so
        # this is never a warning, and only the weekly pass asks it.
        rows = report["still_keeping"]
        Out.raw("  " + paint("STILL KEEPING THESE UP?", S.B, S.GOLD)
                + paint(f"  ({len(rows)})", S.FAINT))
        for r in rows[:12]:
            Out.raw("    " + pad(trunc(r["title"], 44), 46)
                    + paint(f"untouched {r['age']}d", S.FAINT))
        if len(rows) > 12:
            Out.note(f"...and {len(rows) - 12} more")
        first = handle(rows[0]["id"])
        Out.note(f"still keeping it up?  ./os hold {first}   (or add a line to its ## Log)")
        Out.note(f"it's over?            ./os close {first}")
        Out.raw()
    block("gone quiet — still pushing these?", report["archive_candidates"],
          lambda r: pad(trunc(r["title"], 40), 42)
                    + paint(f"./os hold {handle(r['id'])}", S.FAINT))
    block("marked done but still sitting in Work", report["shipped"],
          lambda r: trunc(r["title"], 46))
    block("I wasn't sure where these went", report["unsure"],
          lambda r: pad(trunc(r["title"], 40), 42)
                    + paint(f"now in {r['where']}", S.FAINT))
    def doubled(r: dict) -> str:
        if r.get("code") != "same-write-up":
            return paint(trunc(r["message"], 72), S.MUTE)
        # Two lines: which two, then what they share and how to look.
        one, two = r["pair"]
        return (paint(f"'{trunc(one, 34)}' and '{trunc(two, 34)}'", S.MUTE)
                + "\n      " + paint(f"both mention {Twins.say(r['shares'], 3)}", S.FAINT)
                + "\n      " + paint("look:  " + r["fix"], S.FAINT))

    block("might be the same thing twice", report["duplicates"], doubled)

    if report.get("routines"):
        # A question, not a fact: it is found by words like "every week", and
        # those can't tell whether they really do it by hand, or how often.
        Out.raw("  " + paint("DONE BY HAND EVERY TIME?", S.B, S.GOLD)
                + paint(f"  ({len(report['routines'])})", S.FAINT))
        for row in report["routines"]:
            Out.raw("    "
                    + pad(trunc(row["title"], 40), 42)
                    + paint(f'you wrote "{row["said"]}"', S.FAINT))
        Out.note("if so, a skill writes the steps down once, so your AI just does it:")
        Out.note(f'./os new skill "{trunc(report["routines"][0]["title"], 40)}"'
                 + paint("   (or say /make-skill in the chat)", S.FAINT))
        Out.raw()

    if report.get("crowded"):
        # Only a suggestion: grouping moves things, so the plan comes first.
        Out.raw("  " + paint("CROWDED — COULD BE SPLIT INTO SUBJECT FOLDERS", S.B, S.GOLD)
                + paint(f"  ({len(report['crowded'])})", S.FAINT))
        for row in report["crowded"]:
            n = row["would_move"]
            Out.raw("    " + pad(row["bucket"], 12)
                    + paint(f"{n} thing{'' if n == 1 else 's'} would move into subject folders",
                            S.FAINT))
        Out.note("see what would move first, and what names those paths:  ./os sort --regroup")
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
                report.get("routines"), report.get("still_keeping")]):
        Out.ok("nothing stale, nothing stuck, nothing doubled up")
    Out.raw()
    return 0


def cmd_close(os_: Zenith, argv: list[str]) -> int:
    """Take something out of the live folder.

    Not "finished" — things leave because you stopped carrying them, and that
    is as true of shipped work as of abandoned work. The word the folder uses
    should not claim more than it knows."""
    part = _opt_given(argv, "--part", './os close kitchen-renovation --part "Old Quotes"')
    if not argv or not argv[0].strip():
        die("which one?   ./os close fix-the-boiler      (run ./os to see the names)")
    if part:
        _one_name(os_, argv, "close")
        with Lock(os_, "archive"):
            dest = Archivist(os_).archive_part(argv[0], part)
            Indexer(os_).build()
        Out.title("put away")
        Out.ok(os_.rel(dest))
        Out.note("only that folder: the rest is still live, and its ## Log says where it went")
        Out.note(f"changed your mind?  ./os back {shell_word(argv[0])} --part {shell_word(part)}")
        Out.raw()
        return 0
    how = _how_it_ended(os_, argv)
    _one_name(os_, argv, "close")
    with Lock(os_, "archive"):
        dest = Archivist(os_).archive(argv[0], how)
        Indexer(os_).build()
    Out.title("put away")
    Out.ok(os_.rel(dest))
    if how:
        Out.note(f"its ## Log now says it was {how}")
    Out.note("it still turns up in ./os find — nothing gets deleted here")
    say_left_behind(os_)
    Out.note("still going, just quietly?  ./os back it, then ./os hold it")
    # ./os back looks among what is put away first. Two put away under one
    # name, it is told which by the folders it is in.
    back = handle(dest.stem if dest.is_file() else dest.name)
    finder = Finder(os_)
    if finder.by_id(back, archived=True) is not None and finder.ambiguous:
        mine = next((it for it in finder.ambiguous if it.path == dest), None)
        back = finder.qualified(mine, finder.ambiguous) if mine else os_.rel(dest)
    Out.note(f"changed your mind?  ./os back {back}")
    Out.raw()
    return 0


def _how_it_ended(os_: Zenith, argv: list[str]) -> str:
    """`done` or `dropped` said after the name of what is being closed, taken
    off the end of argv; "" when neither was.

    Close doesn't guess which, since things leave because they stopped being
    carried. Said, it goes in the thing's own Log. A live thing whose name
    ends in the word, typed without quotes, is still that name, and is asked
    about the way it always was."""
    words = [a for a in argv if a != "--"]
    if len(words) < 2 or argv[-1] != words[-1] or words[-1].strip().lower() not in CLOSED_AS:
        return ""
    whole = Finder(os_).by_id(" ".join(words))
    if whole is not None and whole.kind != "archive":
        return ""
    return argv.pop().strip().lower()


def cmd_back(os_: Zenith, argv: list[str]) -> int:
    part = _opt_given(argv, "--part", './os back kitchen-renovation --part "Old Quotes"')
    if not argv or not argv[0].strip():
        die("which one?   ./os back fix-the-boiler")
    _one_name(os_, argv, "back")
    with Lock(os_, "restore"):
        archivist = Archivist(os_)
        dest = archivist.restore_part(argv[0], part) if part else archivist.restore(argv[0])
        Indexer(os_).build()
    Out.title("back out")
    Out.ok(os_.rel(dest))
    if getattr(archivist, "beside", ""):
        # Two live things of the same name, and nothing said (audit, 2026-10-29).
        Out.warn(f"something here is already called {archivist.beside}, so this one came "
                 f"back as {dest.name}, under the same title;  ./os rename  one of them")
    say_left_behind(os_)
    Out.raw()
    return 0


def cmd_undo(os_: Zenith, argv: list[str]) -> int:
    if os_.state_unreadable:
        # The undo list is in that file: "I haven't changed anything yet"
        # was untrue (review, 2026-10-29).
        die(f"{os_.state_unreadable}. The list ./os undo works from is in that file, so nothing was undone.")
    anyway = _flag(argv, "--anyway")
    theirs = _flag(argv, "--other-chat")
    undo = Undo(os_)
    entry = undo.peek()
    # The last thing done may be one undo doesn't take back, like ./os learn.
    # Undo then reached past it and took back an older, unrelated step without
    # a word (check and learn sweep, 2026-10-29). Said once, and asked: the
    # next ./os undo goes ahead.
    late = os_.state.get("not_undoable")
    if isinstance(late, dict) and entry is not None:
        with Lock(os_, "undo"):
            os_.state.pop("not_undoable", None)
            os_.save_state()
        Out.title("undo")
        Out.warn(f"the last thing done here was {late.get('what') or 'something'}, which undo doesn't take back")
        Out.note(f"the last step undo would take back is '{entry.get('label')}', from {str(entry.get('at'))[:16].replace('T', ' ')}")
        Out.note("if that's the one you mean:  ./os undo")
        Out.raw()
        return 1
    if entry is None:
        Out.title("undo")
        if os_.state.get("history"):
            # Not "I haven't changed anything": after 22 saves and 22 undos,
            # two saves were still there (audit, 2026-10-29).
            keep = max(1, int(os_.behaviour.get("keep_undo_steps", 20)))
            Out.warn(f"nothing more to undo — it keeps the last {keep} changes, "
                     "and those have all been taken back")
        else:
            Out.warn("nothing to undo — I haven't changed anything yet")
        Out.raw()
        return 1
    # Undo takes back the newest step, whoever made it. With two chats in one
    # folder, one chat's "undo that" took back the note the other had just
    # saved (stranger test, 2026-09-30). Compared only when both chats have a
    # real id: a step from before this was written down has none, and works
    # as it always did.
    mine, made_by = this_chat(), str(entry.get("chat") or "")
    if not theirs and mine and made_by and made_by != mine:
        when = str(entry.get("at", ""))[:16].replace("T", " ")
        Out.title("undo")
        Out.warn(f"the last change here was made in another chat: "
                 f"'{entry.get('label', '')}' at {when}, which")
        for line in Undo.describe(entry):
            Out.note(line)
        Out.note("undo only takes back this chat's own changes, and that one is newer; "
                 "I've left everything as it is")
        Out.note("to take it back anyway:  ./os undo --other-chat")
        Out.raw()
        return 1
    # A save that moved a file in puts it back where it came from, or not at
    # all: beside it under a new name is not how the folder was.
    taken = undo.in_the_way(entry)
    if taken:
        Out.title("undo")
        Out.warn("the save moved " + ", ".join(taken[:5]) + " in from where it was, "
                 "and something else is there now")
        # "Nothing has changed" read as "the folder hasn't changed", the
        # opposite of what was meant (2026-10-29).
        Out.note("I've left everything as it is — move that out of the way, then  ./os undo  again")
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
        Out.note("undoing it would take those later words out of them too, so I've left "
                 "everything as it is")
        Out.note("to undo it anyway (what they say now is kept aside):  ./os undo --anyway")
        Out.raw()
        return 1
    # A folder undo would have to keep but can't read all of stops it here,
    # before anything is taken back: stopped halfway, the step was used up
    # and couldn't be tried again (review, 2026-10-29).
    for st in entry.get("steps") or []:
        where = os_.root / str(st.get("dst") or "")
        if st.get("action") == "move" and st.get("brought") and where.is_dir() and not where.is_symlink():
            blocked: list = []
            for _top, _dirs, _files in os.walk(where, onerror=blocked.append):
                pass
            if blocked:
                bad = os_.rel(Path(getattr(blocked[0], "filename", "") or where))
                Out.title("undo")
                Out.warn(f"{bad} can't be read, so undo couldn't keep a copy of {os_.rel(where)} "
                         "first — I've left everything as it is")
                Out.note("on a Mac, Finder → Get Info → Sharing & Permissions on that folder; "
                         "then  ./os undo  again")
                Out.raw()
                return 1
    with Lock(os_, "undo"):
        # Looked at before the lock; another chat may have saved or undone
        # since. Undo then took back their newest and named this one, and two
        # at once both said they took back the same (audit, 2026-10-29).
        now = undo.peek()
        if now is None or {k: now.get(k) for k in ("at", "label", "chat", "steps")} \
                != {k: entry.get(k) for k in ("at", "label", "chat", "steps")}:
            Out.title("undo")
            Out.warn("something else changed here just as you asked, so nothing was undone")
            Out.note("to see what undo would take back now:  ./os undo")
            Out.raw()
            return 1
        result = undo.revert()
        Indexer(os_).build()
    Out.title("undone")
    if not result["restored"]:
        Out.warn(f"there was nothing left to reverse in '{result['label']}'")
        if result["failed"]:
            # Why, when it's known: "moved or removed by hand" was said of a
            # shortcut that now leads somewhere else (round 13, 2026-10-29).
            for f in result["failed"][:10]:
                Out.note("couldn't put this one back: " + str(f))
        else:
            Out.note("whatever it made has since been moved or removed by hand")
    else:
        # The number the list below adds up to. Undo's own count also took in
        # steps inside what came in, and said 15 over a list of 18 (audit,
        # 2026-10-29). With some not put back, what was is the true number.
        count = result["restored"] if result["failed"] else len(Undo.describe(entry, None)) or result["restored"]
        changes = f"{count} change" + ("" if count == 1 else "s")
        Out.ok(f"put back the last '{result['label']}' — {changes} reversed")
        # An update since that step isn't on undo's list, and undo reached past
        # it without a word (live test, 2026-10-29).
        def still_in(backup: Path) -> bool:
            """An update not already taken back with ./os update --undo."""
            try:
                return not json.loads((backup / "undo.json").read_text()).get("undone")
            except (OSError, ValueError, AttributeError):
                return True
        stamps = sorted(p.name[len("before-upgrade-"):] for p in (os_.dot / "backups").glob("before-upgrade-*")
                        if p.is_dir() and still_in(p))
        try:
            when = _dt.datetime.fromisoformat(str(entry.get("at"))).replace(tzinfo=None)
            newer = bool(stamps) and _dt.datetime.strptime(stamps[-1][:17], "%Y-%m-%d-%H%M%S") > when
        except (ValueError, TypeError):
            newer = False
        if newer:
            Out.note("an update came after it, and stays — ./os update --undo takes that back")
        # Not "took back" and "couldn't put back" for the same thing (audit,
        # 2026-10-29).
        failed = [str(f).split(": ", 1)[0] for f in result["failed"]]
        for line in Undo.describe(entry):
            if not any(line.endswith(" " + f) or f" {f} " in line for f in failed):
                # "undid:", not "took back:" — "took back: moved Work/X to
                # Archive/X" read as a move it had just made (live test,
                # 2026-10-29).
                Out.note("undid: " + line)
        # What it made inside a folder that stays is named on its own: the
        # folder's line is left out above, and the README it took out was
        # counted and never said (undo rules, 2026-10-29).
        for st in entry.get("steps") or []:
            src = str(st.get("src") or "")
            if st.get("action") == "create" and any(src.startswith(f + "/") for f in failed):
                Out.note(f"undid: made {src}")
        for f in result["failed"][:10]:
            Out.warn("couldn't put this one back: " + str(f))
    # Said every time, never capped: this is where words written after the
    # step now are, and nobody would think to look there.
    for where, copy in result["kept"]:
        Out.warn(f"{where} had words written in since, and undo took them out with the step; "
                 f"the whole {'folder' if (os_.root / copy).is_dir() else 'file'} as it was is kept in {copy}")
    if result.get("kept_copies"):
        listed = (os_.root / result["kept_copies"] / "WHAT-THESE-ARE.txt").is_file()
        Out.warn(f"what couldn't go back is kept as it was before, in {result['kept_copies']}"
                 + (" (WHAT-THESE-ARE.txt there says which file each one was)" if listed else ""))
    Out.raw()
    return 0 if result["restored"] else 1


def cmd_checkpoint(os_: Zenith, argv: list[str]) -> int:
    """Keep everything as it is now in the folder's history (see History).

    /wrapup ends every session with this, so a hand edit made after it can be
    taken back to how it is now. Held under the lock, so a save running beside
    it can't be caught half written."""
    here = _flag(argv, "--here")
    words = one_line(" ".join(_theirs(argv))).strip() or f"Checkpoint {today()}"
    with Lock(os_, "checkpoint"):
        got = History(os_).keep(words, here=here)
    result = got["result"]
    Out.title("checkpoint")
    if result in ("kept", "started"):
        files = got.get("files", 0)
        said = f"{files} file" + ("" if files == 1 else "s")
        Out.ok(f"kept — {said} changed since the last one" if result == "kept" else
               f"started this folder's history — {said} kept as they are now")
        Out.note("an edit made by hand after this can be put back to how it is now")
    elif result == "nothing":
        Out.ok("nothing has changed since the last one")
    elif result == "no git":
        Out.warn("this computer has no git, so there's no history to keep yet — "
                 + History.get_git())
        Out.note("everything is still saved in the folder; only what ./os did can be undone")
    elif result == "template":
        Out.warn("this folder's history came with the download (git clone), so your "
                 "files would go into the template's own — nothing kept")
        Out.note("to start one of its own instead:  ./os setup --unlink --fresh")
    elif result == "theirs":
        Out.warn("this folder's history came from a git clone of the template, so a "
                 "save here would go towards the template's own — nothing kept")
        Out.note("to make it yours:  ./os setup --unlink")
    elif result == "busy":
        Out.warn(f"{got['why']}, so nothing was kept — finish that, then  ./os checkpoint")
    elif result == "inside" and got.get("around"):
        # Already keeping these files: --here would hide them from it.
        Out.warn("this folder's files are kept in the history of a bigger folder around "
                 "it, so save them there — nothing kept here")
    elif result == "inside":
        Out.warn("this folder sits inside a bigger folder's history, and a checkpoint "
                 "there would take everything around it too — nothing kept")
        # Main's nightly save offers `git init <folder>`; this said nothing
        # at all about what to do instead (review, 2026-09-30).
        Out.note("to keep one for this folder alone:  ./os checkpoint --here")
    elif result == "lost" and got.get("emptied"):
        Out.warn("a crash while saving left some of this folder's history empty, so "
                 "nothing was kept, and nothing in it was changed")
        Out.note("to put it right:  ./os check --fix   then  ./os checkpoint")
    elif result == "lost":
        Out.warn("this folder's history has lost track of its last save — a crash while "
                 "saving can do that — so nothing was kept, and nothing in it was changed")
        if got.get("last"):
            Out.note("to put it back:  ./os check --fix   then  ./os checkpoint")
        else:
            Out.note("your files are all still here — to find the saves it still has:  "
                     "git fsck --lost-found")
    else:
        Out.bad(f"couldn't keep a checkpoint: {got.get('why', '')}")
    for name in got.get("own", []):
        Out.note(f"{name} has a git history of its own, so none of its files are in "
                 "this one — save them there")
    if got.get("private"):
        Out.note("left out, as the names look like keys or passwords: "
                 + ", ".join(got["private"]))
    Out.raw()
    return 0 if result in ("kept", "started", "nothing") else 1


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

# The first question is what's on their mind, not what they're working on:
# asked first, work shapes everything after it like a workplace, and this
# folder is meant as a home (decisions, 2026-08-27). How they like answers was
# never asked at all, so the first sessions guessed.
FIRST_TIME = """{name} — this folder holds whatever the person wants kept: plans, notes, files, \
the things they're into. It keeps itself organised.

Nothing has been saved here yet: this is the person's first visit, and they almost certainly \
have no idea what the folder does.

A first visit here (AGENTS.md, "First, always") is a hello, then a line or two saying plainly \
that whatever they say gets written down and put in the right place for them — they never \
pick a folder or name a file — then an offer to set it up together: the setup skill \
(.claude/skills/setup/SKILL.md) says what the folder is for, checks the computer, and brings \
in an old folder. If they'd rather not, ask what's on their mind lately, or what they're \
into. "Nothing yet" is a fine answer.

How they like their answers (short or long, plain or detailed, bad news first or last) is \
asked once, early on. That and their name go in the About me note \
(./os new note "About me" --domain personal), whose first lines are in every brief after this one.

Commands, folder names, how many things are in here and how the system works all wait until \
they ask. "Capture" is not a word they use."""

#: The note that holds what is known about the person — what to call them, how
#: they want answers. Its first lines go in every brief, so a thing said once is
#: known in every session after. A folder of that name works too.
ABOUT_THEM = "about-me"
ABOUT_THEM_LINES = 15
#: Of those, how many are the newest. A save adds its line at the end, so
#: with only the first lines shown, everything said after the fifteenth was
#: written down and never reached another chat.
ABOUT_THEM_NEWEST = 5


def about_them_note(os_: Zenith, items: list) -> Item | None:
    """Their About me note, or None. The one nearest the top when there are
    two, so the brief reads, and `./os save` writes in, the same one."""
    archive = os_.bucket_for_role("archive")
    notes = sorted((i for i in items if i.kind == "note" and i.bucket != archive
                    and ABOUT_THEM in (slugify(i.title), slugify(i.ident))
                    and i.spine and i.spine.is_file()),
                   key=lambda i: len(i.path.parts))
    return notes[0] if notes else None


def _about_them(os_: Zenith, items: list) -> list[str]:
    """The first lines of their About me note, headed by where it is.

    Nothing when there is no such note. Headings, `<!-- prompts -->` and empty
    template bullets are left out: what is wanted is what they said."""
    note = about_them_note(os_, items)
    if note is None:
        return []
    _, body = parse_frontmatter(read_text(note.spine, 60_000))
    lines = [line.strip() for line in COMMENT_RE.sub("", body).split("\n")]
    said = [line for line in lines if line and not line.startswith("#")
            and not re.fullmatch(r"[-*]\s*(\[[ xX]\])?\s*", line)]
    def shown(line: str) -> str:
        return "  " + (line if len(line) <= 200 else line[:199].rsplit(" ", 1)[0] + " …")

    out = [f"About them ({os_.rel(note.spine)}):"]
    if len(said) <= ABOUT_THEM_LINES:
        out += [shown(line) for line in said]
    else:
        first = ABOUT_THEM_LINES - ABOUT_THEM_NEWEST
        left_out = len(said) - ABOUT_THEM_LINES
        out += [shown(line) for line in said[:first]]
        out.append(f"  … {left_out} more line{'' if left_out == 1 else 's'} in "
                   f"{os_.rel(note.spine)}, between these and the newest: read it for all of them")
        out += [shown(line) for line in said[-ABOUT_THEM_NEWEST:]]
    if note.is_dir:
        more = sorted(p.name for p in note.path.iterdir() if p.is_file() and p != note.spine
                      and p.suffix.lower() == ".md" and not p.name.endswith(".card.md"))
        if more:
            out.append(f"  More in {os_.rel(note.path)}/: " + ", ".join(more[:6])
                       + (" …" if len(more) > 6 else ""))
    return out if len(out) > 1 else []


def _brief_text(os_: Zenith) -> str:
    scanner = Scanner(os_)
    items = scanner.scan()
    theirs = [i for i in items if i.kind in ("project", "note", "asset")]
    loose = [i.path for i in items if Sorter.unmanaged(i)] + loose_at_top(os_.root, os_.buckets())
    # Whatever the folder is called now: a brief that opened "ZENITH" after
    # `./os name --name Atlas` had the AI calling it the old name.
    name = str(os_.config.get("name") or "Zenith").upper()
    # Why hand edits can't be taken back yet, while that's so. Said here
    # because in Claude Code this is the first run, and what it prints is
    # never seen (see History.standing).
    # No full stop: most end in a command, and `--here.` copied as it
    # stood was refused (review, 2026-09-30).
    kept = History(os_).standing()
    kept = kept[:1].upper() + kept[1:]
    if not theirs:
        return FIRST_TIME.format(name=name) + (f"\n\n{kept}" if kept else "")

    stale_days = int(os_.thresholds["stale_project_days"])
    active = sorted([i for i in items if i.kind == "project" and i.status == PUSHING],
                    key=lambda i: days_since(i.updated))
    held = sorted([i for i in items if i.kind == "project" and i.status == HOLDING
                   and i.bucket != os_.bucket_for_role("archive")],
                  key=lambda i: days_since(i.updated))
    errors = [i for i in Doctor(os_).run(items=items)["issues"] if i["level"] == "error"
              or (i["code"] in Doctor.OUT_OF_REACH
                  and str(i.get("fix") or "").startswith("./os check --fix"))]
    # A name another live thing has too is given with its folder: see typed_names.
    typed = Finder(os_, scanner).typed_names(items, active[:4] + held[:4])

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
            f"{_shorten(i.title, 48)} [{typed[str(i.path)]}] (last touched {ago(days_since(i.updated))}"
            + (", going cold)" if days_since(i.updated) >= stale_days else ")")
            for i in active[:4])
        if len(active) > 4:
            shown += f"; and {len(active) - 4} more open — the full list is INDEX.md"
        out.append("- On the go: " + shown)
    else:
        out.append("- Nothing being pushed right now.")
    if held:
        out.append("- Being kept up (no next action wanted): " + "; ".join(
            f"{_shorten(i.title, 40)} [{typed[str(i.path)]}]" for i in held[:4])
            + (f"; and {len(held) - 4} more" if len(held) > 4 else ""))
    out.append(f"- {things(len(loose))} dropped in but not filed — ./os sort" if loose
               else "- Nothing waiting to be filed.")
    to_merge, where = _left_to_merge(os_)
    if to_merge:
        out.append(f"- An update left {to_merge} newer file{'' if to_merge == 1 else 's'} in {where}, "
                   "each to be merged into their file of the same name; once that's done, ./os update --done clears them.")
    if errors:
        out.append(f"- {things(len(errors))} broken — ./os check --fix")
    if kept:
        out.append(f"- {kept}")
    about = _about_them(os_, items)
    if about:
        out += [""] + about
    out += ["",
            'To act for them: ./os save "<text>" writes something down and files it · '
            "./os find <words> searches everything · ./os open <name> · ./os undo reverses "
            "the last thing ./os did, never an edit made by hand.",
            "", PLAIN_SPEECH]
    return "\n".join(out)


def not_undoable(os_: Zenith, what: str) -> None:
    """Write down that `what` just happened and undo can't take it back, so
    the next ./os undo says so before reaching past it (see cmd_undo)."""
    try:
        with Lock(os_, "learn"):
            os_.state["not_undoable"] = {"what": what, "at": now_iso()}
            os_.save_state()
    except (OSError, SystemExit):
        pass


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
            print(json.dumps(L.missing_ytdlp()))
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
        # The ones not kept are said, not dropped: "forgot 1" for three asked
        # left two unexplained (audit, 2026-10-29).
        missing = L.not_kept(os_.root, rest)
        gone = L.forget(os_.root, rest)
        if gone:
            not_undoable(os_, "./os learn --forget")
        if as_json:
            print(json.dumps({"ok": True, "forgotten": gone, "not_kept": missing}))
            return 0
        Out.ok(f"forgot {gone} cached source(s)")
        if missing:
            Out.note(f"not kept here, so nothing to forget: {', '.join(missing[:5])}"
                     + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
                     + "   ·   ./os learn --cached  lists what is")
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
                # Month only: the day is worked out from "3 weeks ago", and a
                # day printed would claim more than YouTube said.
                when = f"about {r['date'][:4]}-{r['date'][4:6]}" if r.get("date") else "date unknown"
                Out.item("·", f"{r['id']}  {views:>12} views  {mins:>5}  {when:<13}  {r['title'][:60]}")
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
    if any(not r.get("cached") for r in got):
        not_undoable(os_, "./os learn")
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
    except FileNotFoundError:
        snags = []
    except (OSError, ValueError):
        snags = None
    if not isinstance(snags, list):
        # A snag must never stop the work it was noted in, so it starts over;
        # but read as none, the next one wrote over all of them (audit,
        # 2026-10-28). The broken file is kept beside it, and said.
        aside = unique_path(store.with_name(f"snags-unreadable-{today()}.json"))
        try:
            store.rename(aside)
            Out.warn(f"{os_.rel(store)} couldn't be read — kept as {os_.rel(aside)}, and started again")
        except OSError:
            pass
        snags = []
    # One with no words, written by hand, stopped every snag command with a traceback.
    snags = [s for s in snags if isinstance(s, dict) and str(s.get("text") or "").strip()]

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
        # After --clear that file is the only record of what was cleared, and
        # the next export wrote over it with the new ones alone (audit,
        # 2026-10-28). Holding a snag this export doesn't, it is kept, and
        # this one gets a dated name beside it.
        page = "\n".join(lines).rstrip() + "\n"
        same = [p for p in sorted(os_.root.glob("template-feedback*.md")) if read_utf8(p) == page]
        old = read_utf8(out) if out.exists() and not same else None
        if same:
            out = same[0]
        elif old is not None:
            said = {s["text"] for s in ranked} | {o["text"] for g in clustered(ranked) for o in g["with"]}
            lost = [h for h in re.findall(r"^## (.+)$", old, re.M) if h.strip() not in said]
            if lost:
                out = unique_path(os_.root / f"template-feedback-{today()}.md")
        write_text(out, page)
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
        # Each snag is kept word for word, and one about a note can name who or
        # what the note was about. Nothing said so, and it went out as written.
        Out.note("read it before you send it, in case something personal slipped in")
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
        die(f".os/words.json has a typo on line {exc.lineno} and can't be read.\n"
            "     Usually a missing comma or an unclosed quote. If you keep checkpoints:  "
            "git restore .os/words.json")
    except ValueError as exc:
        die(f"{exc}\n     Fix that, or restore the file from a fresh copy of "
            "this folder.")
    except OSError as exc:
        die(f"can't read .os/words.json: {copy_failed(exc)} — on a Mac, "
            "Finder → Get Info → Sharing & Permissions")


def _new_subject(L, root: Path, key: str, label: str) -> bool:
    """Add a subject with no words yet to words.json; False if it is there.

    Its words then go where `./os words` always puts them, in `learned`: an
    update never touches those, and a release never ships them."""
    data = L.load_words(root)
    blocks = data["domains"]
    if key in blocks:
        return False
    blocks[key] = {"label": label, "keywords": [], "extensions": []}
    L.write_words(root, data)
    return True


def _subject_called(os_: Zenith, said: str) -> str:
    """The subject someone meant, however they spelled its name.

    `./os words Garden "dahlias"` was refused with "no subject called
    'Garden'", and then offered `--new garden` for a subject that was right
    there; so was "Bee keeping" once --new had made it bee-keeping. Spaces,
    capitals and its label all find it now. Unknown, it comes back as given."""
    domains = os_.taxonomy.get("domains") or {}
    if said in domains:
        return said
    want = slugify(said, 24)
    for key, spec in domains.items():
        if want in (slugify(key, 24), slugify(str(spec.get("label") or ""), 24)):
            return key
    return said


def _subject_label(os_: Zenith, said: str, key: str) -> str:
    """The name of the folder a new subject's things will be grouped in.

    Once Notes or Work holds more than a dozen things, sort groups them in a
    folder per subject, named after it, and the name was used just as it was
    typed. `--new content` grouped every video note into Work/Content, the
    one folder ./os never looks in, so they all vanished; `Food/drink` made a
    folder inside a folder, and `../outside` moved notes out of this folder
    altogether, while `./os check` said all good. Now a slash or dots can't
    get into it, and a name already used for something else is refused. So
    is one ending the way leftover files do (`wine~`, `x.swp`): ./os skips a
    folder named like that, and 14 merlot notes grouped in Notes/Wine~ were
    never found again. `group_trouble` says which names can't be one; sort
    asks it too, of a subject written into a header by hand."""
    label = folder_name(said, key)
    again = '     Pick another name:   ./os words --new "<another name>" …'
    why = group_trouble(os_, label) or (
        "and this folder already uses that name for something of its own"
        if label.casefold() == "unsorted" else "")
    if why:
        die(f"'{said}' can't be a subject's name: what you save about it would be "
            f"grouped in a folder called {label}, {why}.\n" + again)
    for other, spec in (os_.taxonomy.get("domains") or {}).items():
        if str(spec.get("label") or "").casefold() == label.casefold():
            die(f"'{said}' would share a folder with the subject {other}, which is "
                f"called {spec.get('label')} too.\n"
                f'     Add your words to that one:   ./os words {other} "<a word>" …\n' + again)
    for bucket, spec in os_.buckets().items():
        if not spec.get("categorize"):
            continue
        try:
            here = [p for p in (os_.root / bucket).iterdir()
                    if p.name.casefold() == label.casefold()]
        except OSError:
            here = []
        # A group sort made itself is fine to share; anything else is not.
        if any(not Scanner.is_category(p) for p in here):
            die(f"'{said}' can't be a subject's name: {bucket}/{here[0].name} is already "
                "here, and what you save about the subject would be grouped in a folder "
                "with that name.\n" + again)
    return label


def _words_step(os_: Zenith, change):
    """Make one change to .os/words.json as a step `./os undo` takes back.

    It was not one, so `./os undo` straight after `./os words --new
    beekeeping "hive"` left beekeeping where it was and took back the save
    before it instead. A run that changes nothing is not a step at all."""
    path = os_.dot / "words.json"
    with Lock(os_, "words"):
        try:
            was = path.read_bytes()
        except OSError:
            was = None
        out = _words_or_die(change)
        try:
            changed = was is not None and path.read_bytes() != was
        except OSError:
            changed = False
        if changed:
            os_.edited(path, was)
            os_.commit("words")
    return out


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
    make = _flag(argv, "--new")
    rest = _theirs(argv)

    if not rest and not make:
        rows = _words_or_die(L.domains, os_.root)
        if as_json:
            print(json.dumps({"ok": True, "domains": rows}, indent=2))
            return 0
        Out.title("words", "what this folder files by")
        # As wide as the longest name: one made with --new can be 24 letters,
        # and ran straight into its count ("a-very-long-subject-name0 words").
        wide = max([14] + [len(row["domain"]) + 2 for row in rows])
        for row in rows:
            extra = f"+{row['learned']} learned" if row["learned"] else ""
            if row["domain"] == CATCH_ALL and not row["keywords"] and not extra:
                extra = "whatever fits none of the others"
            Out.raw("  " + paint(pad(row["domain"], wide), S.GOLD)
                    + paint(pad(f"{row['keywords']} words", 12), S.INK)
                    + paint(extra, S.JADE))
        Out.raw()
        Out.note('./os words <subject> "<a word you use>" …   teaches it more')
        Out.note('./os words --new <name> "<a word>" …        makes a subject of your own')
        Out.note("or open .os/words.json and add them to a keywords list yourself")
        Out.raw()
        return 0

    # A subject of their own. The folder shipped knowing only the subjects it
    # came with, and `./os words garden "bulbs"` was refused as "no domain
    # called 'garden'", so a folder of garden notes had nowhere of its own to
    # go. It takes --new, not any word at all: a typo of a real subject would
    # otherwise quietly become a second one.
    said = rest[0] if rest else ""
    label = ""
    if make:
        if not rest:
            die('what is it called?   ./os words --new knitting "yarn" "purl"')
        if not re.search(r"[^\W\d_]", said) or slugify(said, 24) == "unsorted":
            die(f"'{said}' can't be a subject's name — try a plain word, "
                'like  ./os words --new knitting "yarn"')
        key = _subject_called(os_, said)
        if key not in (os_.taxonomy.get("domains") or {}):
            key = slugify(said, 24)
            label = _subject_label(os_, said, key)
        rest = [key] + rest[1:]
    elif len(rest) == 1:
        die('give me a subject and at least one word'
            '\n     ./os words garden "dahlias" "runner beans"'
            '\n     ./os words --new knitting "yarn" "purl"   makes a new subject'
            '\n     ./os words          lists the subjects')
    else:
        rest = [_subject_called(os_, said)] + rest[1:]

    def change() -> tuple[bool, dict | None]:
        made = _new_subject(L, os_.root, rest[0], label or titleize(rest[0])) if make else False
        return made, (L.teach(os_.root, rest[0], rest[1:]) if len(rest) > 1 else None)
    made, result = _words_step(os_, change)

    if result is None:          # a new subject, and no words for it yet
        key = rest[0]
        if as_json:
            print(json.dumps({"ok": True, "domain": key, "made": made,
                              "added": [], "already_known": []}, indent=2))
            return 0
        Out.title("words", key)
        (Out.ok if made else Out.note)(f"a new subject: {key}" if made
                                       else f"there is already a subject called {key}")
        Out.note(f'give it the words you use for it:   ./os words {key} "<a word>" …')
        Out.raw()
        return 0
    if make:
        result["made"] = made
    if as_json:
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if not result["ok"]:
        die(result["why"].replace("domain", "subject") + "\n     it knows: "
            + ", ".join(result["domains"])
            + f'\n     a new one?  ./os words --new {slugify(rest[0], 24) or "knitting"} '
            + " ".join(json.dumps(w, ensure_ascii=False) for w in rest[1:3]))

    Out.title("words", result["domain"])
    if made:
        Out.ok(f"a new subject: {result['domain']}")
    if result["added"]:
        Out.ok(f"{len(result['added'])} added — " + ", ".join(result["added"]))
    else:
        Out.note("nothing new — it knew all of those already")
    if result["already_known"] and result["added"]:
        Out.note(f"{len(result['already_known'])} it already knew")
    if result.get("skipped"):
        Out.warn(f"left out {len(result['skipped'])}: a word has to be 1 to 40 letters long")
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
        # Line endings aside, as upgrade.py's sha1 (a friend's update).
        return hashlib.sha1(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    text = FOLDER_CALLED.sub(lambda _: "This folder is called Zenith. ",
                             path.read_text(encoding="utf-8", errors="replace"), count=1)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _was(path: Path, recorded) -> bool:
    """Whether a file is as its recorded fingerprint says. One recorded
    before fingerprints left line endings aside was of the bytes as they
    were: on Windows, every untouched addon file read as edited by hand
    (pre-merge review of the Windows fix)."""
    if not recorded or not path.is_file():
        return False
    return recorded in (_sha1(path), hashlib.sha1(path.read_bytes()).hexdigest())


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


#: The change note: `.os/CHANGES.md`, one `## <release>` heading per
#: release, newest first, with plain-words bullets under it. An update only
#: said which files it replaced, never what anybody would notice.
CHANGES_FILE = "CHANGES.md"
#: The heading of the entry still being written. release-os.sh refuses to
#: publish without one, and swaps it for the real release name as it builds.
UNRELEASED = "Next release"
CHANGES_HEADING = re.compile(r"^## +(.+?)[ \t]*$", re.M)
RELEASE_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}(?:\.\d+)?")


def change_entries(text: str) -> list:
    """Each entry in a change note, in its order: (heading, what it says).
    Only headings that are a release, or the one still being written."""
    parts = CHANGES_HEADING.split(text or "")
    return [(head.strip(), body.strip()) for head, body in zip(parts[1::2], parts[2::2])
            if head.strip() == UNRELEASED or RELEASE_STAMP.fullmatch(head.strip())]


def changes_since(text: str, release: str) -> list:
    """The entries newer than `release`: all of them for a folder from before
    releases, and the one still being written always."""
    return [(head, body) for head, body in change_entries(text)
            if head == UNRELEASED or not release or _release_key(head) > _release_key(release)]


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
            die(f"there is nothing at {source} — check the path, or leave --from off "
                "to fetch the newest from GitHub")
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
        die("the download wasn't a readable ZIP, so nothing here changed — it may have "
            "stopped partway; run  ./os update  again")
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
    # A folder of addons by name, used instead of GitHub for the addons.
    addons_from = _opt(argv, "--addons-from")
    check = _flag(argv, "--check")
    done = _flag(argv, "--done")
    undo = _flag(argv, "--undo")
    as_json = _flag(argv, "--json")
    if undo:
        if argv or source or addons_from or check or done or as_json:
            die("--undo goes on its own:   ./os update --undo      (or --undo --dry-run)")
        return update_undo(os_, preview, anyway)
    if done:
        if argv or preview or source or addons_from or check:
            die("--done goes on its own:   ./os update --done      (or --done --anyway)")
        return update_done(os_, anyway, as_json)
    if as_json:
        die("--json goes with --done:   ./os update --done --json")
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
            # Not a list of hidden program files, which meant nothing to them
            # (audit, 2026-10-29).
            die("that's someone's folder, not a download of the template — "
                + (f"{len(used)} of its program files {'was' if len(used) == 1 else 'were'} changed there"
                   if used else f"it has a {lived[0]}/ folder")
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
            # The shapes are named one by one above once a release records
            # them: "newer: .os/templates/holding.md, .os/templates" said it
            # twice (update sweep, 2026-10-29).
            if any(r.startswith(rel + "/") for r in theirs.get("files", {})):
                continue
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
            code = _then_addons(os_, preview, addons_from)
            Out.raw()
            return code
        if have and got and _release_key(got) < _release_key(have) and not anyway:
            die(f"this folder has {have}, newer than the published {got}.\n"
                "     ./os update --anyway goes back to the published one")

        if fresh:
            Out.raw("  " + paint("newer: ", S.MUTE) + ", ".join(fresh[:8])
                    + (f" and {len(fresh) - 8} more" if len(fresh) > 8 else ""))
        # The upgrade itself refuses to replace a program changed here and
        # never published, unless told --anyway: run by hand, it says so too.
        sys.stdout.flush()
        # A closed window doesn't cut the update short: turned into Ctrl-C
        # here, it had the update itself killed outright partway (pre-merge
        # review, 2026-10-29). It finishes, or stops on its own terms.
        # Only a closed window is let pass: a kill, or a chat's time limit,
        # still stops it, with its own word on how to go on; a hung update
        # could otherwise be stopped by nothing but Ctrl-C (fourteenth review).
        held = {}
        for name in ("SIGHUP",):
            try:
                held[name] = signal.signal(getattr(signal, name), signal.SIG_IGN)
            except (AttributeError, ValueError, OSError):
                pass
        # Under the folder's lock from start to end: another ./os, or the same
        # update tried again, ran alongside a hung one (fourteenth review).
        lock = Lock(os_, "update")
        lock.__enter__()
        try:
            child = subprocess.Popen([sys.executable, str(new / ".os" / "upgrade.py"), str(os_.root),
                                      *(["--dry-run"] if preview else []), *(["--anyway"] if anyway else []),
                                      "--again=./os update"],
                                     cwd=str(os_.root), text=True,
                                     env=dict(os.environ, ZENITH_LOCK_OWNER=str(os.getpid())))
            # Ctrl-C reaches the update too, which says for itself that it
            # stopped and what to do: waited for, not killed, and not said
            # over with "nothing was left half-moved" (round 19 live test).
            passed_on = False
            while child.poll() is None:
                try:
                    child.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    # A stop landing in here left the loop, and the lock was
                    # let go of while the update went on (review of the lock
                    # timer). The update hears it on its own.
                    try:
                        lock.keep_fresh()
                    except KeyboardInterrupt:
                        pass
                except KeyboardInterrupt:
                    # Passed on once if it didn't reach the update itself (a
                    # stop sent to this run alone), so it can still be
                    # stopped; never again over its own stopping.
                    if not passed_on:
                        try:
                            child.wait(timeout=2)
                        except (subprocess.TimeoutExpired, KeyboardInterrupt):
                            # Windows has no SIGINT to send: it said so with
                            # a crash, and the lock was let go of while the
                            # update went on (review of the fifteenth's fixes).
                            # Its console's Ctrl-C reaches the update anyway.
                            try:
                                child.send_signal(signal.SIGINT)
                            except (OSError, ValueError):
                                pass
                        passed_on = True
            proc = child
        finally:
            lock.__exit__(None, None, None)
            for name, was in held.items():
                try:
                    signal.signal(getattr(signal, name), was)
                except (ValueError, OSError):
                    pass
        if proc.returncode == 2:
            return 1            # it stopped before changing anything, and said why
        if proc.returncode == 3:
            return 1            # it stopped partway, and said so: once is enough
        if proc.returncode != 0:
            Out.bad("the update stopped partway — ./os update again finishes it. "
                    "What it replaced is in .os/backups/before-upgrade-*")
            return proc.returncode
    code = _then_addons(os_, preview, addons_from)
    if not preview:
        Out.note("not wanted after all?  ./os update --undo puts the last version back")
        Out.raw()
    return code


def update_undo(os_: Zenith, preview: bool, anyway: bool) -> int:
    """`./os update --undo`: put the folder back as it was before the last
    update, from the copy that update kept in .os/backups/.

    Nothing did: `./os undo` can't reverse an update, and the way back was to
    copy files out of the backup by hand (audit, 2026-10-28). The update's
    undo.json says what each file looked like straight after it, so a file
    changed since stops this, and says which: --anyway goes ahead. What's here
    now goes into a backup of its own first, so the undo can be undone too.
    An addon's files are left as they are: `./os update` updates them on their
    own, and `./os addon` is their way back."""
    backups = os_.dot / "backups"
    made = sorted(p for p in backups.glob("before-upgrade-*") if p.is_dir()) if backups.is_dir() else []
    if not made:
        die("there's no update to undo — no copy from before one is in .os/backups/")
    # One that stopped before changing anything is passed over: it hid the
    # last real update from --undo for good (twelfth review, 2026-10-29).
    def marked(p: Path) -> dict:
        try:
            got = json.loads((p / "undo.json").read_text(encoding="utf-8"))
            return got if isinstance(got, dict) else {}
        except (OSError, ValueError):
            return {}
    real = [p for p in made if not marked(p).get("nothing_changed")]
    backup = real[-1] if real else made[-1]
    record_file = backup / "undo.json"
    try:
        record = json.loads(record_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        die(f"the last update was made by an older version, which didn't write down what it\n"
            f"     changed, so this can't undo it safely. Its copy from before is in\n"
            f"     {os_.rel(backup)}")
    if record.get("nothing_changed"):
        Out.title("update", "undo")
        Out.ok("the last update stopped before it changed anything, so there's nothing to undo")
        Out.note("./os update  tries it again")
        Out.raw()
        return 0
    if record.get("undone"):
        die(f"the last update ({record.get('release_after') or 'unstamped'}) was already undone — "
            "./os update brings it back")
    after = record.get("after") if isinstance(record.get("after"), dict) else {}
    addon_files = {rel for a in _addons(os_.root).values() for rel in a["files"]}

    def digest(path: Path) -> str:
        # Line endings aside, as the update records them: on Windows every
        # file it had just written was "changed after the update" (pre-merge
        # review of the Windows fix).
        return hashlib.sha1(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() \
            if path.is_file() else ""

    def raw(path: Path) -> str:
        # How an update before that recorded them, and what tells a file to
        # put back: one differing only in line endings is still put back as
        # it was, as a launcher with CR LF in it can't run (live test).
        return hashlib.sha1(path.read_bytes()).hexdigest() if path.is_file() else ""

    # Nor the list of what it wrote, which is the backup's own: --undo put
    # written.txt at the top of their folder (twelfth review, 2026-10-29).
    before = {f.relative_to(backup).as_posix(): f for f in sorted(backup.rglob("*"))
              if f.is_file() and f != record_file and f != backup / "written.txt"
              and "__pycache__" not in f.parts}
    put_back = [rel for rel in sorted(before) if rel not in addon_files
                and raw(os_.root / rel) != raw(before[rel])]
    # Never one that is a shortcut now: that is theirs, whatever it leads to.
    take_out = [rel for rel in sorted(after) if rel not in before and rel not in addon_files
                and (os_.root / rel).is_file() and not (os_.root / rel).is_symlink()]
    if record.get("partial"):
        # One that stopped partway wrote down each file it put in place: what
        # it added goes too. Without that, --undo said "back to" the old
        # version and left the new files (eleventh review, 2026-10-29).
        try:
            said = (backup / "written.txt").read_text(encoding="utf-8")
            # Not a last line cut short by the stop: what it named could be
            # the start of a path of theirs (round 25).
            lines = said.splitlines() if said.endswith("\n") else said.splitlines()[:-1]
        except OSError:
            lines = []
        for line in lines:
            try:
                rel = Path(line).relative_to(os_.root).as_posix()
            except ValueError:
                continue
            # Only what the update owns, the places it backed up: a note of
            # theirs it had rewritten to take an old tag out was "added",
            # and --undo deleted it (twelfth review, 2026-10-29).
            touched = [str(x) for x in (record.get("touched") or []) if isinstance(x, str)]
            if not any(rel == top or rel.startswith(top.rstrip("/") + "/") for top in touched):
                continue
            if rel.startswith(".os/backups/") or rel.startswith(".os/upgrades/") or rel in before \
                    or rel in addon_files or rel in take_out or not (os_.root / rel).is_file():
                continue
            take_out.append(rel)
    changed = [rel for rel in sorted(set(put_back) | set(take_out))
               if rel in after and after[rel] not in (digest(os_.root / rel), raw(os_.root / rel))]
    was, now = record.get("release_before") or "unstamped", record.get("release_after") or (
        "the update that stopped partway" if record.get("partial") else "unstamped")

    Out.title("update", f"undo {now} → {was}" + ("   a preview — nothing will change" if preview else ""))
    if changed and not anyway:
        Out.warn(f"{len(changed)} of these {'was' if len(changed) == 1 else 'were'} changed after the update:")
        for rel in changed[:12]:
            Out.note(rel)
        if len(changed) > 12:
            Out.note(f"and {len(changed) - 12} more")
        Out.note("undoing puts the version from before the update over them. They go into")
        Out.note("a backup first, so nothing is lost:   ./os update --undo --anyway")
        Out.raw()
        return 1
    if not put_back and not take_out:
        Out.ok("everything is already as it was before the last update")
        Out.raw()
        return 0
    if preview:
        for rel in put_back:
            Out.item("·", f"would put back   {rel}")
        for rel in take_out:
            Out.item("·", f"would take out   {rel}   (the update added it)")
        Out.raw()
        return 0
    with Lock(os_, "update undo"):
        stamp = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
        keep = backups / f"before-undo-{stamp}"
        n = 1
        while keep.exists():
            n += 1
            keep = backups / f"before-undo-{stamp}-{n}"
        for rel in put_back + take_out:
            here = os_.root / rel
            if here.is_file():
                (keep / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(here, keep / rel)
        for rel in put_back:
            here = os_.root / rel
            # A shortcut the update wrote through is put back the same way:
            # undo swapped it for a plain file and their own file kept the
            # update's words (twelfth review, 2026-10-29).
            if here.is_symlink() and here.resolve().is_file():
                here = here.resolve()
            here.parent.mkdir(parents=True, exist_ok=True)
            spare = here.with_name(here.name + ".undoing")
            shutil.copy2(before[rel], spare)
            os.replace(spare, here)
        for rel in take_out:
            (os_.root / rel).unlink()
            folder = (os_.root / rel).parent
            # A folder the update made for it, empty now, goes with it.
            while folder != os_.root and folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
                folder = folder.parent
        # What it set aside for merging belonged to the version undone.
        aside = os_.dot / "upgrades" / backup.name[len("before-upgrade-"):]
        if aside.is_dir():
            keep.mkdir(parents=True, exist_ok=True)
            shutil.move(str(aside), str(keep / "set-aside-to-merge"))
        record["undone"] = today()
        record_file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        Indexer(os_).build()
    Out.ok(f"back to {was}: put back {len(put_back)} file(s)"
           + (f", took out {len(take_out)} the update added" if take_out else ""))
    if addon_files:
        Out.note("your addons stay as they are")
    Out.note(f"what was here is kept in {os_.rel(keep)}")
    Out.note("./os update brings the newest again")
    Out.raw()
    return 0


def _unmerged(os_: Zenith, stamp: Path, rel: str) -> bool:
    """Does the new version an update set aside in `stamp` still look
    unmerged into theirs? It does when theirs hasn't changed since that
    update: the same as the copy the update kept of it before it ran, or,
    with that copy gone, not written in since the update's own time."""
    mine, new = os_.root / rel, stamp / rel
    if not mine.is_file() or _sha1(mine) == _sha1(new) \
            or rel in set(_shipped(os_.root).get("theirs") or []):
        return False        # nothing to merge into, merged whole, or not theirs to merge
    for kind in ("upgrade", "addons"):
        before = os_.dot / "backups" / f"before-{kind}-{stamp.name}" / rel
        if before.is_file():
            return _sha1(before) == _sha1(mine)
    try:
        when = _dt.datetime.strptime(stamp.name[:17], "%Y-%m-%d-%H%M%S").timestamp()
        return mine.stat().st_mtime <= when
    except (ValueError, OSError):
        return True         # can't tell, so it asks


def update_done(os_: Zenith, anyway: bool, as_json: bool) -> int:
    """`./os update --done`: clear away what an update set aside to merge,
    once it's merged.

    Nothing did: `./os` said files were waiting to merge for good, and an AI
    isn't allowed to delete a folder of ./os's own by hand (report,
    2026-10-22). A file there that still looks unmerged stops it, and says
    which; --anyway clears them all the same. ./os undo brings them back."""
    base = os_.dot / "upgrades"
    found = []
    for stamp in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
        for f in sorted(stamp.rglob("*")):
            if f.is_file() and not f.is_symlink() and f.name not in (".DS_Store", "Thumbs.db"):
                rel = f.relative_to(stamp).as_posix()
                found.append((f, rel, _unmerged(os_, stamp, rel)))
    left = [(os_.rel(f), rel) for f, rel, waiting in found if waiting]
    if left and not anyway:
        if as_json:
            print(json.dumps({"removed": [], "unmerged": [{"path": p, "into": r} for p, r in left]},
                             indent=2))
            return 1
        Out.title("update", "what it left to merge")
        Out.warn(f"{len(left)} of these still look{'s' if len(left) == 1 else ''} unmerged — "
                 "yours hasn't changed since the update set the new one aside:")
        for path, rel in left[:20]:
            Out.note(f"{path}   into   {rel}")
        if len(left) > 20:
            Out.note(f"...and {len(left) - 20} more")
        Out.note("nothing has been removed")
        Out.note("merge each into yours first, or to clear them anyway:  ./os update --done --anyway")
        Out.raw()
        return 1
    gone: list[str] = []
    if found:
        with Lock(os_, "update"):
            for f, _rel, _waiting in found:
                try:
                    os_.removed(f)
                    gone.append(os_.rel(f))
                except OSError:
                    pass
            os_.commit("clear what an update left to merge")
        # What is left is empty folders and the junk a computer leaves.
        if base.is_dir() and all(p.is_dir() or p.name in (".DS_Store", "Thumbs.db")
                                 for p in base.rglob("*")):
            shutil.rmtree(base, ignore_errors=True)
    if as_json:
        print(json.dumps({"removed": gone, "unmerged": []}, indent=2))
        return 0
    Out.title("update", "what it left to merge")
    if not found:
        Out.ok("nothing is waiting to merge")
        Out.raw()
        return 0
    Out.ok(f"cleared {os_.rel(base)}/ — removed {len(gone)} file{'' if len(gone) == 1 else 's'}:")
    for rel in gone:
        Out.note(rel)
    Out.note("want them back?  ./os undo")
    Out.raw()
    return 0


# ---------------------------------------------------------------------------
# addons — extra skills and helpers for one kind of work, added by name
# ---------------------------------------------------------------------------

#: Each addon is its own branch of the template on GitHub, `addon-<name>`, so
#: the normal download carries none and nobody gets one they didn't ask for.
#: At the top of the branch, addon.json says what it is and lists every file
#: it puts in a folder, by where the file goes.
ADDON_REPO = "zidery333/os-template"
ADDON_ZIP = "https://github.com/" + ADDON_REPO + "/archive/refs/heads/addon-{name}.zip"
ADDON_INFO = "https://raw.githubusercontent.com/" + ADDON_REPO + "/addon-{name}/addon.json"
#: Every branch whose name starts `addon-`, in one small request.
ADDON_BRANCHES = "https://api.github.com/repos/" + ADDON_REPO + "/git/matching-refs/heads/addon-"
#: What this folder has added, in .os/: each addon's files as they were put
#: in, so an update can tell one nobody touched (replaced) from one they
#: changed (kept, and the new one set aside to merge).
ADDONS_FILE = "addons.json"
ADDON_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
#: The only places an addon may put things: a skill's own folder, or a
#: helper. Never the program, never their work.
ADDON_SKILL = re.compile(r"\.claude/skills/([a-z0-9][a-z0-9-]*)/[^/]+(?:/[^/]+)*")
ADDON_HELPER = re.compile(r"\.claude/agents/[a-z0-9][a-z0-9-]*\.md")


def _addon_place(rel: str) -> str:
    """Where a file of an addon lands, as one thing: a skill's folder, or a
    helper's file. "" for anywhere an addon may not put things."""
    if ".." in rel.split("/") or "\\" in rel:
        return ""
    hit = ADDON_SKILL.fullmatch(rel)
    if hit:
        return f".claude/skills/{hit.group(1)}"
    return rel if ADDON_HELPER.fullmatch(rel) else ""


def _download(url: str, dest: Path, seconds: int = 60) -> int:
    """Fetch `url` into `dest`. 0 when it came; GitHub's answer when it said
    no, like 404 for nothing there; -1 when it couldn't be reached at all."""
    curl = shutil.which("curl")
    if curl:
        try:
            got = subprocess.run([curl, "-sSL", "--max-time", str(seconds), "-o", str(dest),
                                  "-w", "%{http_code}", url],
                                 capture_output=True, text=True, timeout=seconds + 10)
            code = int((got.stdout or "").strip()[-3:] or 0)
        except (OSError, subprocess.SubprocessError, ValueError):
            code = 0
        if code == 200:
            return 0
        if code >= 400:
            dest.unlink(missing_ok=True)
            return code
    # No curl, or it couldn't get through: Python's own. On some Macs it
    # lacks the certificates, which is why curl goes first.
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=seconds) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        return 0
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return -1


def _fetch_json(url: str, dest: Path) -> object:
    """What a small JSON file on GitHub says. None when it can't be had."""
    if _download(url, dest, 10):
        return None
    try:
        return json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _addon_fetch(name: str, into: Path, source: str) -> tuple:
    """One addon, unpacked: (the folder holding its addon.json, or None, and
    what went wrong). `source` is a folder of addons by name, used instead of
    GitHub."""
    if source:
        folder = Path(source).expanduser() / name
        if (folder / "addon.json").is_file():
            return folder, ""
        return None, f"there's no addon called {name} in {source}"
    archive = into / f"addon-{name}.zip"
    code = _download(ADDON_ZIP.format(name=name), archive)
    if code == 404:
        return None, f"there's no addon called {name}"
    if code:
        return None, "couldn't reach GitHub — check the internet connection and try again"
    unpacked = into / f"addon-{name}"
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(unpacked)
    except (zipfile.BadZipFile, OSError) as exc:
        return None, "the download wasn't a readable ZIP — it may have stopped partway; try again"
    tops = [p for p in (unpacked, *unpacked.iterdir()) if p.is_dir() and (p / "addon.json").is_file()]
    if not tops:
        return None, f"the {name} download has no addon.json"
    return tops[0], ""


def _addon_info(folder: Path, root: Path) -> tuple:
    """An addon's addon.json, checked against what an addon may do: (what
    it says, what's wrong with it). A skill or helper the template ships is
    never an addon's to replace, and nor is a name Claude Code uses itself."""
    try:
        info = json.loads((folder / "addon.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, "its addon.json won't read"
    files = info.get("files") if isinstance(info, dict) else None
    if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
        return {}, "its addon.json lists no files"
    shipped = {_addon_place(rel) or rel for rel in (_shipped(root).get("files") or {})}
    for rel in files:
        place = _addon_place(rel)
        if not place:
            return {}, f"it would put {rel} somewhere an addon can't"
        if place in shipped:
            return {}, f"it would replace {place}, which comes with this folder"
        if place.startswith(".claude/skills/") and Path(place).name in RESERVED_COMMANDS:
            return {}, f"its skill would hide Claude Code's own /{Path(place).name}"
        if not (folder / rel).is_file():
            return {}, f"{rel} is missing from it"
    return info, ""


def _addons(root: Path) -> dict:
    """The addons this folder has, from .os/addons.json: each one's files as
    they were put in. Only those with a file still here: an addon whose files
    are all gone was taken out, by ./os undo or by hand."""
    try:
        data = json.loads((root / MARKER / ADDONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: a for name, a in (data.items() if isinstance(data, dict) else [])
            if isinstance(a, dict) and isinstance(a.get("files"), dict)
            and any((root / rel).is_file() for rel in a["files"])}


def _then_addons(os_: Zenith, preview: bool, source: str) -> int:
    """After an update, every addon this folder has, brought up to date by
    the program that is here now: the new one. Its exit is passed on: an
    addon that failed to update was followed by a clean exit (audit, 2026-10-28)."""
    if not _addons(os_.root):
        return 0
    sys.stdout.flush()
    # The engine itself, not the bash launcher: Windows can't run that from
    # here, and every update ended "not a valid Win32 application" with its
    # addons never updated (pre-merge review of the Windows fix).
    return subprocess.run([sys.executable, str(os_.root / MARKER / "engine.py"), "addon", "--update",
                    *(["--dry-run"] if preview else []), *(["--from", source] if source else [])],
                   cwd=str(os_.root), env=dict(os.environ, ZENITH_HOME=str(os_.root))).returncode


def cmd_addon(os_: Zenith, argv: list[str]) -> int:
    """List the addons there are, or add one by name. `./os update` keeps the
    ones added up to date, through `./os addon --update`."""
    source = _opt(argv, "--from")
    preview = _flag(argv, "--dry-run", "-n")
    if _flag(argv, "--update"):
        return _addons_update(os_, source, preview)
    names = _theirs(argv)
    if not names:
        return _addons_list(os_, source)
    if len(names) > 1:
        die(f"one at a time:   ./os addon {names[0].lower()}")
    name = names[0].strip().lower()
    if not ADDON_NAME.fullmatch(name):
        die(f"there's no addon called {names[0]} — ./os addon lists them")
    have = _addons(os_.root)
    if name in have:
        Out.title("addon")
        Out.ok(f"{have[name].get('name') or name} is already added")
        Out.note("./os update brings its newest version")
        Out.raw()
        return 0
    with tempfile.TemporaryDirectory(prefix="zenith-addon-") as tmp:
        folder, problem = _addon_fetch(name, Path(tmp), source)
        if folder is None:
            die(problem + (" — ./os addon lists them" if problem.startswith("there's no addon") else ""))
        info, problem = _addon_info(folder, os_.root)
        if problem:
            die(f"the {name} addon can't be added: {problem}. Nothing here changed")
        files = info["files"]
        # Never over anything of theirs: a skill or helper by the same name
        # stops it, with nothing changed.
        owned = {rel: other for other, a in have.items() for rel in a["files"]}
        clash = []
        for rel in files:
            dst = os_.root / rel
            if rel in owned:
                clash.append(f"{rel}, part of the {owned[rel]} addon")
            elif (dst.exists() or dst.is_symlink()) and _sha1(dst) != _sha1(folder / rel):
                clash.append(rel)
        if clash:
            die("you already have something by the same name, so nothing was added:\n"
                + "".join(f"       {c}\n" for c in clash)
                + "     Rename yours, then add it again.")
        title = one_line(str(info.get("name") or name))
        places = sorted({_addon_place(rel) for rel in files})
        if preview:
            Out.title("addon", "a preview — nothing will change")
            Out.ok(f"would add {title}: " + ", ".join(places))
            Out.raw()
            return 0
        with Lock(os_, "addon"):
            for rel in files:
                dst = os_.root / rel
                os_.make_dir(dst.parent)
                there = dst.exists()
                copy_whole(folder / rel, dst)
                if not there:
                    os_.created(dst)
            # The record goes back with ./os undo too, or a skill of their
            # own by the same name, made later, would read as this addon.
            listed = os_.dot / ADDONS_FILE
            was = listed.read_bytes() if listed.is_file() else None
            try:
                record = json.loads(was or b"{}")
            except ValueError:
                record = {}
            record = {**(record if isinstance(record, dict) else {}), **have,
                      name: {"name": title, "release": str(info.get("release") or ""),
                             "files": {rel: _sha1(os_.root / rel) for rel in files}}}
            write_text(listed, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
            if was is None:
                os_.created(listed)
            else:
                os_.edited(listed, was)
            os_.commit(f"added the {name} addon")
            Indexer(os_).build()
    Out.title("addon added")
    icon = one_line(str(info.get("icon") or "")).strip()
    about = one_line(str(info.get("about") or "")).strip()
    Out.ok(f"{icon + '  ' if icon else ''}{title}" + (f" — {about}" if about else ""))
    Out.note("in " + ", ".join(places) + "   ·   a chat started from now on can use it")
    Out.note("./os update keeps it up to date   ·   wrong?  ./os undo")
    Out.raw()
    return 0


def _addons_list(os_: Zenith, source: str) -> int:
    """Every addon there is: what each one adds, and how to add it."""
    found: list = []
    if source:
        base = Path(source).expanduser()
        if not base.is_dir():
            die(f"there's no folder at {source}")
        for folder in sorted(p for p in base.iterdir() if (p / "addon.json").is_file()):
            try:
                info = json.loads((folder / "addon.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            found.append((folder.name, info if isinstance(info, dict) else {}))
    else:
        with tempfile.TemporaryDirectory(prefix="zenith-addon-") as tmp:
            refs = _fetch_json(ADDON_BRANCHES, Path(tmp) / "refs.json")
            if not isinstance(refs, list):
                die("couldn't get the list of addons from GitHub — check the internet "
                    "connection, or try again in a while")
            for ref in refs:
                name = str(ref.get("ref") or "").rpartition("/addon-")[2] if isinstance(ref, dict) else ""
                if ADDON_NAME.fullmatch(name):
                    info = _fetch_json(ADDON_INFO.format(name=name), Path(tmp) / f"{name}.json")
                    found.append((name, info if isinstance(info, dict) else {}))
    have = _addons(os_.root)
    Out.title("addons", "extra skills for one kind of work, added by name")
    if not found:
        Out.note("there are no addons yet")
        Out.raw()
        return 0
    for name, info in found:
        icon = one_line(str(info.get("icon") or "")).strip() or "·"
        title = one_line(str(info.get("name") or name))
        about = one_line(str(info.get("about") or "")).strip()
        Out.raw(f"  {icon}  " + paint(title, S.B) + (paint("   added", S.MUTE) if name in have else ""))
        if about:
            Out.raw("      " + paint(about, S.INK))
        if name not in have:
            Out.raw("      " + paint(f"./os addon {name}", S.GOLD))
    Out.raw()
    Out.note("./os update keeps the ones you add up to date")
    Out.raw()
    return 0


def _addons_update(os_: Zenith, source: str, preview: bool) -> int:
    """Bring every addon this folder has up to its newest version. A file of
    an addon nobody touched is replaced; one they changed is kept, and the
    new one set aside in .os/upgrades/ to merge; one they deleted stays
    deleted. Everything replaced is copied to .os/backups/ first."""
    have = _addons(os_.root)
    if not have:
        return 0
    stamp = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = os_.dot / "backups" / f"before-addons-{stamp}"
    aside = os_.dot / "upgrades" / stamp
    record = dict(have)
    touched = False

    def put(src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        copy_whole(src, dst)

    with tempfile.TemporaryDirectory(prefix="zenith-addon-") as tmp, Lock(os_, "addon"):
        for name, had in sorted(have.items()):
            title = str(had.get("name") or name)
            folder, problem = _addon_fetch(name, Path(tmp), source)
            info: dict = {}
            if folder is not None:
                info, problem = _addon_info(folder, os_.root)
            if problem:
                Out.raw(f"  ▲ the {title} addon: {problem}; yours is kept as it is")
                continue
            old = had["files"]
            offered = had.get("offered") if isinstance(had.get("offered"), dict) else {}
            files, waiting, done, said = {}, {}, {"new": 0, "replaced": 0, "taken out": 0}, []
            for rel in info["files"]:
                src, dst = folder / rel, os_.root / rel
                now = _sha1(src)
                if dst.is_file():
                    mine = _sha1(dst)
                    if mine == now:
                        files[rel] = now
                    elif _was(dst, old.get(rel)):
                        if not preview:
                            put(dst, backup / rel)
                            put(src, dst)
                        files[rel], done["replaced"] = now, done["replaced"] + 1
                    else:
                        # Changed by them, or theirs from before: kept, and
                        # the new one put beside it once, not every update.
                        if rel in old:
                            files[rel] = old[rel]
                        waiting[rel] = now
                        if offered.get(rel) != now:
                            if not preview:
                                put(src, aside / rel)
                            said.append(f"  {'would keep' if preview else 'kept     '} {rel} — "
                                        + ("you edited it" if rel in old else "yours has the same name")
                                        + ("" if preview else f"; the new one is in {os_.rel(aside / rel)}"))
                elif rel in old:
                    files[rel] = old[rel]
                    said.append(f"  {'would leave out' if preview else 'left out '} {rel} — "
                                "you deleted it, so it stays deleted")
                else:
                    if not preview:
                        put(src, dst)
                    files[rel], done["new"] = now, done["new"] + 1
            # A file the addon no longer has goes, unless they changed it.
            for rel, was in old.items():
                dst = os_.root / rel
                if rel in files or not _was(dst, was):
                    continue
                if not preview:
                    put(dst, backup / rel)
                    dst.unlink()
                    try:
                        dst.parent.rmdir()          # only if nothing is left in it
                    except OSError:
                        pass
                done["taken out"] += 1
            record[name] = {"name": one_line(str(info.get("name") or title)),
                            "release": str(info.get("release") or had.get("release") or ""),
                            "files": files, **({"offered": waiting} if waiting else {})}
            counts = [f"{n} {what}" for what, n in done.items() if n]
            touched = touched or bool(counts)
            Out.raw(f"  {'would update' if preview else 'updated  '} the {title} addon — {', '.join(counts)}"
                    if counts else f"  ✓ the {title} addon is up to date")
            for line in said:
                Out.raw(line)
        if not preview:
            write_text(os_.dot / ADDONS_FILE, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
            if touched:
                Indexer(os_).build()
    return 0


#: The skills the old me/ notes/ work/ layout shipped. Any other skill in an
#: old folder is one they made, and comes in with the rest of their things.
OLD_LAYOUT_SKILLS = {"archive", "catch-me-up", "learn", "new-project", "new-skill",
                     "new-subject", "save-to-my-os", "setup", "snag", "tidy-up",
                     "update-os", "what-do-i-know", "wrapup"}
#: What an old folder holds that is the template's, not theirs: never brought in.
NOT_THEIRS = {"README.md", "LICENSE", "AGENTS.md", "GEMINI.md", "INDEX.md", "os",
              "CHANGES.md", "template-feedback.md"}


def linked_to_template(os_: Zenith) -> list:
    """The names of this folder's links to the template's own GitHub page.

    A `git clone` keeps one, called origin. The Claude app then showed the
    folder as the template's, whatever it was renamed to, and ./os kept no
    history of its own in it (a friend's first try, 2026-10-23)."""
    h = History(os_)
    # A worktree's link is the other folder's, and cutting it cut theirs.
    if History.no_git() or not h.own_dir():
        return []
    found = []
    for remote in h._git("remote").stdout.split():
        url = h._git("remote", "get-url", remote).stdout.strip()
        if re.search(r"[:/]" + re.escape(TEMPLATE_REPO) + r"(?:\.git)?/?$", url):
            found.append(remote)
    return found


def folder_around(root: Path) -> Path | None:
    """The nearest folder above this one that is another AI folder, or None.

    Claude Code reads every CLAUDE.md above the folder it opens, so a new
    folder put inside an old one got the old one's rules as well as its own.
    That is how it lands when the old folder's AI is asked to fetch the new
    one (a friend's first try, 2026-10-23)."""
    home = Path.home().resolve()
    for up in root.resolve().parents:
        if up == home or up == up.parent:
            return None
        if (up / "CLAUDE.md").is_file() or (up / "AGENTS.md").is_file() \
                or (up / MARKER / "config.json").is_file():
            return up
    return None


def tool_version(*cmd: str) -> str:
    """The first line a program says about its version, or "" if it isn't there."""
    if shutil.which(cmd[0]) is None:
        return ""
    try:
        said = subprocess.run(list(cmd), capture_output=True, text=True, errors="replace",
                              timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    first = (said.stdout or said.stderr).strip().split("\n")[0]
    return one_line(first)[:60] or "yes"


def setup_report(os_: Zenith) -> dict:
    """What /setup looks at before it asks anything: the computer, the tools,
    and anything about where this folder sits that will trip them up later.

    `needs` holds what is wrong, each with the plain fix. Nothing is changed."""
    if sys.platform == "darwin":
        system = "Mac"
    elif "microsoft" in read_text(Path("/proc/version"), 400).lower():
        system = "Windows (WSL)"
    elif sys.platform.startswith("linux"):
        system = "Linux"
    else:
        system = sys.platform
    claude = tool_version("claude", "--version")
    if not claude and (Path.home() / ".local" / "bin" / "claude").is_file():
        claude = "installed, but this terminal can't find it yet"
    git = "" if History.no_git() else tool_version(GIT, "--version")
    root = os_.root.resolve()
    linked = linked_to_template(os_)
    around = folder_around(root)
    needs = []
    if not git:
        needs.append({"code": "no-git", "say": "git isn't installed, so hand edits can't be "
                      "taken back", "fix": History.get_git()})
    if not claude:
        needs.append({"code": "no-claude", "say": "Claude Code isn't installed",
                      "fix": "curl -fsSL https://claude.ai/install.sh | bash"})
    elif claude.startswith("installed,"):
        needs.append({"code": "claude-not-found", "say": claude,
                      "fix": "close the terminal and open it again"})
    if linked:
        # Nothing of theirs saved in it yet: a history of its own is on offer.
        fresh = History(os_).where() == "template"
        needs.append({"code": "linked", "say": "this folder is still linked to the template's "
                      "GitHub page, so the Claude app shows it as the template's",
                      "fix": "./os setup --unlink --fresh" if fresh else "./os setup --unlink",
                      "fresh": fresh})
    if around is not None:
        needs.append({"code": "inside", "say": f"this folder is inside another one ({around}), "
                      "so Claude reads that one's rules here too",
                      "fix": "./os setup --move-to " + shell_word(str(around.parent / root.name))})
    downloads = Path.home() / "Downloads"
    if downloads.resolve() in (root, *root.parents):
        needs.append({"code": "downloads", "say": "this folder is in Downloads, which gets "
                      "cleared out and asks for permission on a Mac",
                      "fix": "./os setup --move-to " + shell_word(str(Path.home() / root.name))})
    return {"system": system, "python": sys.version.split()[0], "git": git, "claude": claude,
            "folder": str(root), "folder_name": root.name,
            "name": str(os_.config.get("name") or "Zenith"),
            "owner": str(os_.config.get("owner") or ""),
            "linked": bool(linked), "inside": str(around) if around else "", "needs": needs}


def unlink_from_template(os_: Zenith, fresh: bool = False) -> dict:
    """Cut this folder's link to the template's GitHub page, and make its
    history ours, so checkpoints go in it from now on.

    Only the link goes: every save in the history stays, theirs included. A
    link to anywhere else is theirs, and left alone.

    `fresh`: start a history of its own instead, when nothing of theirs is
    saved in the clone's yet. Every save in it was the template's, so kept,
    theirs would sit on top of the template's whole past (2026-10-29). The
    clone's .git goes into .os/backups/, never deleted, and the folder as it
    is now is the new history's first save."""
    gone = linked_to_template(os_)
    if not gone:
        return {"result": "not linked"}
    h = History(os_)
    if fresh:
        if h.where() != "template":
            return {"result": "has saves"}
        stamp = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
        aside = os_.dot / "backups" / f"git-from-the-template-{stamp}"
        try:
            aside.parent.mkdir(parents=True, exist_ok=True)
            (os_.root / ".git").rename(aside)
        except OSError as exc:
            return {"result": "failed", "why": str(exc)}
        kept = h.keep("The folder as it was, in a history of its own")
        return {"result": "fresh", "kept": kept["result"], "aside": os_.rel(aside),
                **({"why": kept["why"]} if kept.get("why") else {})}
    for remote in gone:
        h._git("remote", "remove", remote)
    h._git("config", h.MARK, "true")
    kept = h.keep("Cut the link to the template's GitHub page")
    return {"result": "cut", "kept": kept["result"]}


def old_folder_kind(src: Path) -> str:
    """'old layout' (main's me/ notes/ work/), 'os' (an older ./os folder),
    or 'plain' (any folder of things)."""
    if (src / "me").is_dir() and ((src / "notes").is_dir() or (src / "work").is_dir()):
        return "old layout"
    if (src / MARKER).is_dir() and (src / "os").is_file():
        return "os"
    return "plain"


def _theirs_in(folder: Path, skip: set = frozenset()) -> list:
    """What in an old folder is theirs: not hidden, not the computer's own
    files, and not one of the template's, sorted by name."""
    try:
        found = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    return [p for p in found if not p.name.startswith(".") and not ignored(p)
            and p.name not in NOT_THEIRS and p.name not in skip and not p.is_symlink()]


def _passed_over(folder: Path, skip: set = frozenset()) -> list:
    """What _theirs_in leaves out of an old folder that is theirs all the
    same, with why: hidden files and shortcuts went without a word, so the
    setup skill couldn't tell anyone (bring sweep, 2026-10-29)."""
    try:
        found = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    out = []
    for p in found:
        if p.name in skip or p.name in NOT_THEIRS or p.name in (".git", MARKER, ".claude", ".DS_Store") \
                or (ignored(p) and not p.name.startswith(".")):
            continue
        if p.is_symlink():
            out.append((p, "a shortcut — not brought in; copy what it points at in by hand"))
        elif p.name.startswith("."):
            out.append((p, "a hidden file — not brought in; copy it in by hand if it's yours"))
    return out


def _their_skills(src: Path, template_skills: set) -> list:
    """Skills in an old folder that they made, not the template."""
    skills = src / ".claude" / "skills"
    shipped = set(template_skills)
    record = src / ".claude" / "shipped.tsv"
    if record.is_file():
        for line in read_all(record).splitlines():
            parts = line.split("\t")[-1].split("/")
            if len(parts) > 3 and parts[:2] == [".claude", "skills"]:
                shipped.add(parts[2])
    return [p for p in _theirs_in(skills) if p.is_dir() and p.name not in shipped
            and (p / "SKILL.md").is_file()]


def bring_plan(os_: Zenith, src: Path) -> dict:
    """What `./os setup --bring` would bring in from an old folder, and how.

    Each step is (path in the old folder, how). How is "about" (into About
    me), "work", "note", "closed" (work that was put away), "as is" (to the
    same place here, from an older ./os folder), "skill", "old" (into Archive,
    the old layout's own lists), "save" (filed the way ./os save files it), or
    "left" (stays where it is, with why)."""
    kind = old_folder_kind(src)
    steps: list = []
    left = lambda p, why: steps.append((p, "left", why))
    if kind == "old layout":
        for p in _theirs_in(src / "me"):
            steps.append((p, "about", ""))
        if (src / "CLAUDE.md").is_file():
            steps.append((src / "CLAUDE.md", "old", ""))
        for p in _theirs_in(src / "notes", {"subjects.md"}):
            steps.append((p, "note", ""))
        if (src / "notes" / "subjects.md").is_file():
            steps.append((src / "notes" / "subjects.md", "old", ""))
        for p in _theirs_in(src / "work", {"archive", "projects.md"}):
            steps.append((p, "work" if p.is_dir() else "note", ""))
        for p in _theirs_in(src / "work" / "archive"):
            steps.append((p, "closed" if p.is_dir() else "old", ""))
        if (src / "work" / "projects.md").is_file():
            steps.append((src / "work" / "projects.md", "old", ""))
        for p in _their_skills(src, OLD_LAYOUT_SKILLS):
            steps.append((p, "skill", ""))
        for p in _theirs_in(src, {"me", "notes", "work", "CLAUDE.md"}):
            steps.append((p, "save", ""))
    elif kind == "os":
        buckets = list(os_.buckets())
        for bucket in buckets:
            if (src / bucket / MEDIA_FOLDER).is_dir():
                left(src / bucket / MEDIA_FOLDER, "big files stay where they are")
            for p in _theirs_in(src / bucket):
                if bucket == os_.bucket_for_role("archive") and p.is_dir():
                    for q in (q for b in _theirs_in(p) for q in ([b] if not b.is_dir()
                              or b.name not in buckets else _theirs_in(b))):
                        steps.append((q, "as is", ""))
                else:
                    steps.append((p, "as is", ""))
        for p in _their_skills(src, set(upgrade_lists()[0])):
            steps.append((p, "skill", ""))
        # Not brought in, but said: both went without a word (audit, 2026-10-29).
        shipped_helpers = {f"{a}.md" for a in upgrade_lists()[1]}
        for p in _theirs_in(src / ".claude" / "agents"):
            if p.suffix == ".md" and p.name not in shipped_helpers:
                left(p, "a helper you made — not brought in; copy it into .claude/agents/ to keep it")
        words = src / MARKER / "words.json"
        try:
            domains = json.loads(read_all(words)).get("domains") or {}
        except (OSError, ValueError, AttributeError):
            domains = {}
        here = (os_.taxonomy.get("domains") or {})

        def listed(spec: dict, key: str) -> list:
            # A list, whatever was typed by hand: "learned": "wader" was a
            # crash report (review, 2026-10-29).
            got = spec.get(key) if isinstance(spec, dict) else None
            return got if isinstance(got, list) else []

        def known_here(name: str) -> set:
            spec = here.get(name) if isinstance(here.get(name), dict) else {}
            return {str(w).lower() for k in ("keywords", "learned") for w in listed(spec, k)}
        # Only words this folder doesn't have: both taught "wader" still said
        # 1 word not brought in (review, 2026-10-29).
        added = sum(len({str(w).lower() for w in listed(spec, "learned")
                         + ([] if name in here else listed(spec, "keywords"))} - known_here(name))
                    for name, spec in domains.items() if isinstance(spec, dict))
        if added:
            left(words, f"{added} word{'s' if added != 1 else ''} you taught it — "
                        "not brought in; add the ones you miss with ./os words")
        for p in _theirs_in(src, set(buckets) | {"CLAUDE.md"}):
            steps.append((p, "save", ""))
    else:
        for p in _theirs_in(src, {"CLAUDE.md"}):
            steps.append((p, "save", ""))
    # Any other folder's rules file was left out without a word, and /setup
    # looks for it in Archive (live test, 2026-10-29). One that only points at
    # AGENTS.md, as this folder's own does, has nothing of theirs in it.
    rules = src / "CLAUDE.md"
    if kind != "old layout" and rules.is_file() and not rules.is_symlink() \
            and re.sub(r"@AGENTS\.md", "", read_text(rules)).strip():
        steps.append((rules, "old", ""))
    for top in ([src] + [src / b for b in os_.buckets()] if kind == "os" else [src]):
        for p, why in _passed_over(top):
            if not any(q == p for q, _h, _w in steps):
                left(p, why)
    # Shortcuts inside what comes in that lead out of the old folder: kept as
    # shortcuts, nothing copied in, and said in the trial run too.
    fenced = src.resolve()
    for p, how, _why in list(steps):
        if how == "left" or not p.is_dir() or p.is_symlink():
            continue
        for link in (q for q in p.rglob("*") if q.is_symlink()):
            try:
                target = link.resolve()
            except (OSError, RuntimeError):
                continue
            if target.exists() and not (target == fenced or fenced in target.parents):
                left(link, "a shortcut to something outside the old folder — it still leads there; "
                           "nothing of it was copied in")
    for n, (p, how, why) in enumerate(steps):
        # Said in the trial run too: it listed one as coming in, and the real
        # run then left it (audit, 2026-10-29).
        if how == "skill" and (os_.root / ".claude" / "skills" / p.name).exists():
            steps[n] = (p, "left", "a skill here has that name")
        if how in ("save", "note", "work") and big_media(os_, p):
            steps[n] = (p, "left", "too big to copy: ./os save it to write a note saying where it is")
        # Into a place where something of that name is already: it lands
        # beside it as name-2, and the trial run says so. It said "same
        # place" and the real run made a second one (bring sweep, 2026-10-29).
        if how == "as is" and kind == "os":
            try:
                here = os_.root / p.relative_to(src)
                if here.exists() or here.is_symlink():
                    steps[n] = (p, how, f"beside the {here.name} already here, as {unique_path(here).name}")
            except ValueError:
                pass
        # Brought in before, unchanged, and still here: left, and said.
        try:
            was = (os_.state.get("brought") or {}).get(str(p.resolve()))
            if steps[n][1] != "left" and isinstance(was, dict) and (os_.root / str(was.get("to"))).exists() \
                    and was.get("print") and _bring_print(p) == was.get("print"):
                steps[n] = (p, "left", f"already brought in — it's at {was['to']}")
        except OSError:
            pass
    return {"kind": kind, "steps": steps}


def upgrade_lists() -> tuple:
    """The skills this template ships, from .os/upgrade.py, which keeps the one list."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import upgrade  # type: ignore
        return tuple(upgrade.SHIPPED_SKILLS), tuple(upgrade.SHIPPED_AGENTS)
    except Exception:
        return (), ()
    finally:
        sys.path.pop(0)


def old_brief_as_page(text: str, has_decisions: bool) -> str:
    """An old layout's brief.md, as this folder's page for a piece of work.

    Every line of theirs stays. The old brief kept its next step and its
    finish line as bold lines, `**Next:** ...`, and its decisions in a
    decisions.md beside it. Brought in as it was, ./os found no next action
    and put an empty page on a card beside it, so each piece of work had two
    pages, and the empty one was the one it read."""
    def said(label: str) -> str:
        found = re.search(r"^\*\*" + label + r":?\*\*:?\s*(.+)$", text, re.M | re.I)
        value = found.group(1).strip() if found else ""
        return "" if not value or value.upper().startswith("TO FILL") else value
    have = lambda heading: re.search(r"^##\s+" + heading + r"\b", text, re.M | re.I)
    page = text.rstrip() + "\n"
    if said("Done looks like") and not have("What good looks like"):
        page += f"\n## What good looks like\n{said('Done looks like')}\n"
    if not have("Next action"):
        page += "\n## Next action\n" + (f"- [ ] {said('Next')}\n" if said("Next") else "- [ ] \n")
    if not have("Decisions"):
        page += "\n## Decisions\n" + ("- The ones from before are in [decisions.md](decisions.md).\n"
                                     if has_decisions else "")
    if not have("Log"):
        page += f"\n## Log\n- {today()} · Brought in from the old folder.\n"
    return page


def bring_in(os_: Zenith, src: Path) -> dict:
    """Bring an old folder's things in, each filed where it belongs here.

    Everything is copied: the old folder isn't changed at all, so it is
    still there to look at if something came out wrong. One ./os undo takes
    the whole lot back out again."""
    plan = bring_plan(os_, src)
    notes = os_.root / os_.bucket_for_role("note")
    stage = Creator(os_).stage()
    year = today()[:4]
    done: list = []
    about = about_them_note(os_, Scanner(os_).scan())
    about_dir = None
    about_files: list = []
    sorter = Sorter(os_)
    closing: list = []

    def copy(p: Path) -> Path:
        landed = unique_path(stage / p.name)
        # Grown under a name sort never files, and given its own only once
        # whole, as save does: a full disk or a Ctrl-C partway left the half
        # copy under its real name, and the next sort filed it as whole
        # (nineteenth review, round 24).
        part = None
        try:
            part = claim_partial(landed, folder=p.is_dir())
            if p.is_dir():
                shutil.copytree(p, part, symlinks=True, dirs_exist_ok=True)
                # Shortcuts in it re-aimed, and what they point at outside it
                # brought in, as save does: one written relative to the old
                # folder arrived leading nowhere (bring sweep, 2026-10-29).
                bring_in_what_links_point_at(p, part, os_.root, fence=src)
            else:
                shutil.copy2(p, part)
            landed, part = place_whole(part, landed), None
        except BaseException:
            drop_partial(part)
            raise
        return landed

    def place(landed: Path, dest: Path) -> Path:
        moved = os_.move(landed, dest)
        os_.brought_in(landed)
        return moved

    for p, how, why in plan["steps"]:
        rel = os.path.relpath(p, src)
        if how == "left":
            done.append({"from": rel, "how": how, "why": why})
            continue
        try:
            landed = copy(p)
        except (OSError, shutil.Error) as exc:
            done.append({"from": rel, "how": "left", "why": f"couldn't copy it: {copy_failed(exc)}"})
            continue
        dest = None
        if how == "about":
            if about_dir is None:
                # An About me here already is theirs too: the old files go
                # beside it, to be merged.
                about_dir = unique_path(notes / ("About me" if about is None
                                                 else "About me from the old folder"))
            dest = place(landed, about_dir / p.name)
            about_files.append(p.name)
        elif how == "old":
            # Kept to look things up in. Not as CLAUDE.md: Claude would take an
            # old folder's rules as rules for anything it read beside them.
            name = "CLAUDE (old folder).md" if p.name == "CLAUDE.md" else p.name
            dest = place(landed, os_.root / os_.bucket_for_role("archive") / year
                         / "Old Folder" / name)
        elif how == "skill":
            target = os_.root / ".claude" / "skills" / p.name
            if target.exists():
                done.append({"from": rel, "how": "left", "why": "a skill here has that name"})
                shutil.rmtree(landed, ignore_errors=True)
                continue
            dest = place(landed, target)
        elif how == "as is":
            dest = place(landed, os_.root / rel)
        else:
            if how in ("work", "closed") and landed.is_dir() \
                    and not (landed / "README.md").exists() and (landed / "brief.md").is_file():
                # The old layout's page for a piece of work, under this
                # folder's name for it, and in its shape.
                brief = landed / "brief.md"
                write_text(landed / "README.md", old_brief_as_page(
                    read_all(brief), (landed / "decisions.md").is_file()))
                brief.unlink()
            said = {"work": "work", "closed": "work", "note": "note"}.get(how, "")
            dest, verdict = sorter.file_one(landed, "path", said)
            if dest is not None:
                os_.brought_in(landed)
                if how == "closed":
                    closing.append(dest)
                elif how == "work" and "this one has no end" in read_text(
                        Scanner(os_).spine_of(dest) or dest, 20_000).lower():
                    spine = Scanner(os_).spine_of(dest)
                    if spine is not None:
                        os_.snapshot(spine)
                        write_text(spine, set_fields(read_all(spine), {"status": HOLDING}))
        if dest is None:
            done.append({"from": rel, "how": "left", "why": "couldn't work out where it goes"})
            continue
        done.append({"from": rel, "how": how, "to": os_.rel(dest)})
        # Written down as each lands, so a bring cut off partway knows what it
        # had brought (fourteenth review, 2026-10-29).
        try:
            os_.state.setdefault("brought", {})[str(p.resolve())] = {"to": os_.rel(dest), "print": _bring_print(p)}
        except OSError:
            pass
    if about_dir is not None and not (about_dir / "README.md").exists():
        # Their About me's own page, made the way this folder makes one, with
        # a line for each file it holds: it is what every chat reads first.
        domain = "personal" if "personal" in (os_.taxonomy.get("domains") or {}) else ""
        page = Creator(os_).render("note", about_dir.name, "", domain, [], shape=ABOUT_THEM)
        body = (f"# {about_dir.name}\n\n"
                + "".join(f"- [{given_name(Path(f).stem)}]({quote(f)})\n" for f in about_files)
                + "\nBrought in from the old folder. Read them before doing anything for me.\n")
        write_text(about_dir / "README.md", set_fields(page, {}, body=body))
        os_.created(about_dir / "README.md")
    for dest in closing:
        try:
            # One undo step for the whole bring, under its name: each one
            # made its own, and undo then named the last thing put away
            # (audit, 2026-10-29).
            put = Archivist(os_).archive(os_.rel(dest), commit=False)
        except SystemExit:
            continue
        for entry in done:
            if entry.get("to") == os_.rel(dest):
                entry["to"] = os_.rel(put)
    return {"kind": plan["kind"], "from": str(src), "steps": done}


def follow_brought(state: dict, moves: list) -> None:
    """Where each thing a bring brought in is now, after these moves: a note
    renamed or put away came in again on the next bring (fourteenth review,
    2026-10-29). Undo follows them back the same way."""
    for rec in (state.get("brought") or {}).values():
        if not isinstance(rec, dict) or not isinstance(rec.get("to"), str):
            continue
        for src, dst in moves:
            if rec["to"] == src:
                rec["to"] = dst
            elif rec["to"].startswith(src + "/"):
                rec["to"] = dst + rec["to"][len(src):]


def _bring_print(p: Path) -> str:
    return folder_print(p) if p.is_dir() and not p.is_symlink() else content_print(p)


def _say_bring(got: dict, preview: bool) -> None:
    words = {"about": "About me", "work": "work", "note": "a note", "closed": "put away",
             "as is": "same place", "skill": "a skill", "old": "kept in Archive",
             "save": "filed"}
    Out.title("would bring in" if preview else "brought in", got["kind"])
    for entry in got["steps"]:
        if entry["how"] == "left":
            Out.warn(f"{entry['from']} — left there: {entry['why']}")
        elif preview:
            Out.raw(f"    {pad(entry['from'], 44)} {paint(words[entry['how']], S.FAINT)}"
                    + (f"  {paint('— ' + entry['why'], S.FAINT)}" if entry.get("why") else ""))
        else:
            Out.ok(f"{entry['from']}  →  {entry['to']}")
    Out.raw()
    if preview:
        Out.note("nothing copied yet — to do it:  ./os setup --bring " + shell_word(got["from"]))
    elif not any(e["how"] != "left" for e in got["steps"]):
        # Nothing came in: undo would take back the bring before this one
        # (pre-merge review, 2026-10-29).
        Out.note("nothing new was brought in, so ./os undo now would take back the step before this one")
    elif any(e["how"] == "left" and str(e.get("why", "")).startswith("couldn't copy")
             for e in got["steps"]):
        # Not "everything was copied" over one that couldn't be (nineteenth
        # review, round 24).
        Out.note("what couldn't be copied is listed above, and is still only in the old "
                 "folder, which is just as it was")
        Out.note("anything wrong?  ./os undo  straight after this takes the whole lot back out")
    else:
        Out.note("everything was copied: the old folder is just as it was")
        Out.note("anything wrong?  ./os undo  straight after this takes the whole lot back out")
    Out.raw()


def reaim_links(old: Path, new: Path, *also: Path) -> None:
    """Shortcuts in the moved folder that led into it by its whole path, led
    on into the old place, and nowhere once it had gone: a bring writes them
    that way (third pre-merge review, round 19). They lead into the new
    place now. Relative ones move with it already."""
    olds = sorted({str(p) for p in (old, *also)}, key=len, reverse=True)
    for folder, dirs, files in os.walk(new):
        for name in dirs + files:
            link = os.path.join(folder, name)
            if not os.path.islink(link):
                continue
            try:
                # Tidied first: one through `..` out of the folder leads
                # outside it, whatever it starts with.
                to = os.path.normpath(os.readlink(link))
                if not os.path.isabs(to):
                    continue
                was = next((o for o in olds if to == o or to.startswith(o + os.sep)), None)
                if was is None:
                    continue
            except OSError:
                continue
            # Made beside it, then put in its place in one step: taken away
            # first, a new one that couldn't be made left neither (second
            # review of round 19's fixes).
            spare = os.path.join(folder, f".{name}.reaim-{os.getpid()}")
            try:
                os.symlink(str(new) + to[len(was):], spare)
                os.replace(spare, link)
            except OSError:
                try:
                    os.unlink(spare)
                except OSError:
                    pass


#: How _not_in_copy says two names became one file at the new place.
SAME_THERE = "same there: "


#: Where a move is taking the folder, kept in the old place's .os while it
#: is set aside and taken away (second pre-merge review of round 21).
MOVING_TO = "moving-to"


class _TakenOver(Exception):
    """A move's lock was taken by another chat while it copied."""


#: What the old place's config.json is called while a move looks it over:
#: away from its name, no ./os starts in it (round 21 review of the move).
MOVING_CONFIG = "config.json.moving"

#: Where a move made its copy, kept in the old place's .os from the moment it
#: is set aside: stopped before it says where it went, the old place is the
#: folder, and says the copy there is not (round 22).
COPIED_TO = "copied-to"


def _not_in_copy(old: Path, copy: Path, began: float, was_at=(), now_at: str = "",
                 config_aside: bool = False) -> str:
    """The first thing the old place and its copy don't agree on, or "": one
    missing from either, a file changed since the copy began and different
    there, or a shortcut that leads somewhere else. `was_at` are the old
    place's own paths, which a shortcut in the copy was re-aimed from to
    `now_at`. Left out: ./os's own lock files, which the copy leaves out
    too, the hooks' .dirty mark, and Finder's .DS_Store."""
    own = {".lock", ".dirty", MOVING_TO, COPIED_TO}

    def entries(top: str):
        for folder, dirs, files in os.walk(top):
            rel_folder = os.path.relpath(folder, top)
            for name in files + dirs:
                rel = os.path.normpath(os.path.join(rel_folder, name))
                bare = name[2:] if name.startswith("._") else name    # its Mac companion too
                if rel.split(os.sep)[0] == MARKER and os.path.dirname(rel) == MARKER and (
                        bare in own or bare.startswith(".lock.")):
                    continue
                if bare == ".DS_Store":          # Finder's, written by just looking
                    continue
                yield rel

    # The old place's config.json, under the name it has while looked over,
    # and its Mac companion on a USB stick, renamed with it (third pre-merge
    # review of round 21: every move from one was refused).
    names = {os.path.join(MARKER, MOVING_CONFIG): os.path.join(MARKER, "config.json"),
             os.path.join(MARKER, "._" + MOVING_CONFIG): os.path.join(MARKER, "._config.json")} \
        if config_aside else {}
    back = {v: k for k, v in names.items()}
    olds = sorted({os.path.normpath(str(p)) for p in was_at}, key=len, reverse=True)
    landed = {}
    for rel in entries(str(old)):
        there = names.get(rel, rel)
        try:
            was = os.lstat(os.path.join(old, rel))
            now = os.lstat(os.path.join(copy, there))
        except OSError:
            return rel
        # Two names that a drive which ignores capitals takes for one
        # (plan.md and Plan.md): one was copied over the other, and lost
        # with the old place (round 21 pre-merge review).
        if (now.st_dev, now.st_ino) in landed and now.st_ino:
            return f"{SAME_THERE}{landed[(now.st_dev, now.st_ino)]} and {rel}"
        landed[(now.st_dev, now.st_ino)] = rel
        if stat.S_ISLNK(was.st_mode):
            # Pointed somewhere else meanwhile, it was put back as it was
            # (round 21 review of the move).
            try:
                to = os.path.normpath(os.readlink(os.path.join(old, rel)))
                if os.path.isabs(to) and now_at:
                    hit = next((o for o in olds if to == o or to.startswith(o + os.sep)), None)
                    if hit is not None:
                        to = now_at + to[len(hit):]
                if not stat.S_ISLNK(now.st_mode) or \
                        os.path.normpath(os.readlink(os.path.join(copy, there))) != to:
                    return rel
            except OSError:
                return rel
            continue
        # Changed since the copy began: read whole, as a same-size edit
        # within a second or two of its copy passed a look at the times
        # (review of the check before removal). By the later of its two
        # times: a file swapped in by Finder, a sync app or `cp -p` keeps an
        # older modified time, but its change time can't be set back
        # (round 21). Only these few are read.
        # A `._name` of theirs is read whole whatever its times: on a USB
        # stick the Mac writes its own over it (second pre-merge review).
        if stat.S_ISREG(was.st_mode) and (max(was.st_mtime, was.st_ctime) >= began - 2
                                          or os.path.basename(rel).startswith("._")):
            try:
                if not filecmp.cmp(os.path.join(old, rel), os.path.join(copy, there),
                                   shallow=False):
                    return rel
            except OSError:
                return rel
    # Taken away meanwhile, by them or another chat: in the copy it would
    # have come back at the new place (round 21).
    for rel in entries(str(copy)):
        if not os.path.lexists(os.path.join(old, back.get(rel, rel))):
            # The Mac's own `._name` beside a file on a FAT or exFAT drive:
            # every move to a USB stick was refused (pre-merge review). Known
            # by how it starts: one of theirs taken away meanwhile came back
            # (third pre-merge review).
            if os.path.basename(rel).startswith("._"):
                try:
                    with open(os.path.join(copy, rel), "rb") as fh:
                        if fh.read(4) == b"\x00\x05\x16\x07":
                            continue
                except OSError:
                    pass
            return rel
    return ""


def _copy_all(src, dst, *, follow_symlinks=True):
    """A file copied whole. On a Mac, with what copy2 leaves behind: Finder
    tags and comments, and the rest kept beside the file (round 21 review of
    the move: a file tagged Red arrived with no tag)."""
    got = shutil.copy2(src, dst, follow_symlinks=follow_symlinks)
    if sys.platform == "darwin" and follow_symlinks:
        try:
            import ctypes
            lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            lib.copyfile.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32)
            lib.copyfile(os.fsencode(os.fspath(src)), os.fsencode(os.fspath(got)), None,
                         0x4 | 0x1)                         # what's kept beside it, and who may
        except (OSError, AttributeError):
            pass
    return got


def _remove_tree(path: Path) -> bool:
    """Take a folder away, read-only files and all, and say whether it went.
    Left read-only, git's own files stopped it on Windows, and a half copy
    stayed while it was said to be gone (round 21 review of the move)."""
    top = os.path.abspath(path)

    def again(func, name, *_):
        # Only what takes something away is tried again: 3.9 hands os.open
        # here too, and calling it bare crashed (round 21 pre-merge review).
        if func not in (os.unlink, os.remove, os.rmdir):
            return
        try:
            # Never through a shortcut, whose permissions are the place it
            # leads to: a script outside lost its run bit (same review). Its
            # folder only, and only inside what is being taken away.
            if not os.path.islink(name):
                os.chmod(name, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if os.path.isdir(name) else 0))
            parent = os.path.dirname(os.path.abspath(name))
            if parent == top or parent.startswith(top + os.sep):
                os.chmod(parent, os.stat(parent).st_mode | stat.S_IWRITE | stat.S_IEXEC)
            func(name)
        except OSError:
            pass
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=again)
        else:
            shutil.rmtree(path, onerror=again)
    except OSError:
        pass
    return not os.path.lexists(path)


#: How often, in seconds, a long copy says again that it still holds the lock.
LOCK_REFRESH = 60


def setup_move(os_: Zenith, where: str, lock: "Lock | None" = None) -> Path:
    """Move or rename this whole folder, on disk. A plain name puts it beside
    where it is now; a path puts it there. Nothing inside it changes: it
    finds itself wherever it is. `lock`, the one held for the move, is said
    again to be held while a copy to another disk goes on."""
    root = os_.root.resolve()
    target = Path(where).expanduser()
    if len(target.parts) == 1 and where.strip() not in ("", ".", ".."):
        target = root.parent / where.strip()
    target = Path(os.path.abspath(target))
    # Where it really is, every shortcut on the way followed: a path into
    # this folder through a shortcut passed the check, and Python 3.9's move
    # copied the folder into itself and then deleted it, copy and all (bring
    # sweep, 2026-10-29).
    target = Path(os.path.realpath(target.parent)) / target.name
    # Where it went is written down one line long (sixth pre-merge review).
    if "\n" in str(target) or "\r" in str(target):
        die("that name has a line break in it — pick one without, "
            "like  ./os setup --move-to \"~/My Folder\"")
    # Inside first: `--move-to .` said there was already something there
    # (audit, 2026-10-29).
    if target == root or root in target.parents:
        die("that is this folder, or inside it — pick somewhere outside it, "
            "like  ./os setup --move-to \"~/My Folder\"")
    if target.exists():
        die(f"there is already something at {target} — pick another name, "
            "or move that out of the way first")
    if not target.parent.is_dir():
        die(f"there is no folder {target.parent} to put it in — make it first, "
            "or pick a place that's there, like  ~/My Folder")
    # Out of it first: Windows won't rename a folder a running program
    # stands in (round 21 review of the move).
    try:
        os.chdir(root.parent)
    except OSError:
        pass
    try:
        os.rename(root, target)          # the same disk: all at once, or not at all
        reaim_links(root, target, os_.root)
        return target
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            die(f"couldn't move it: {copy_failed(exc)} — nothing was moved; is the place "
                "you picked writable?")
    # Another disk: copied whole first, and only then taken from here. A
    # stop partway said "nothing was moved" over 3031 files at the new place,
    # or over an old place left with one folder in it (bring sweep).
    try:
        # Only ./os's own lock is left out, in .os itself: a pattern for every
        # folder left out their own .lock files, and the old place was then
        # taken away with them (pre-merge review, 2026-10-29).
        home_dot = str(root / MARKER)

        def not_the_lock(folder, names):
            return [n for n in names if n == ".lock" or n.startswith(".lock.")
                    or n in (MOVING_TO, COPIED_TO)] \
                if os.path.normpath(folder) == home_dot else []
        # A copy longer than STALE_AFTER let another chat take the lock as
        # debris and save into the old place, which then went (fifteenth
        # review, 2026-10-29). Said again on a timer while it goes, not
        # between files: one big video can take longer than that alone.
        began = time.time()
        done = threading.Event()

        def beat() -> None:
            while not done.wait(LOCK_REFRESH):
                lock.keep_fresh()
        beating = threading.Thread(target=beat, daemon=True) if lock is not None else None
        # Only one held at the start can be lost: a drive with no hard links
        # holds none, and every move to one was refused (review of the lock
        # timer).
        had = lock is not None and lock.held
        if beating is not None:
            beating.start()
        try:
            shutil.copytree(root, target, symlinks=True, ignore=not_the_lock,
                            copy_function=_copy_all)
        finally:
            done.set()
            if beating is not None:
                beating.join()
        # Still ours? Taken over all the same (a laptop shut mid-copy), what
        # another chat saved since is only in the old place, so that stays.
        if had:
            lock.keep_fresh()
            if not lock.held:
                raise _TakenOver()
        reaim_links(root, target, os_.root)
    except BaseException as exc:
        # Stopped too, Ctrl-C or a closed window: the half copy goes, and
        # only then is the stop passed on (pre-merge review, 2026-10-29).
        # Said gone only once it is (round 21 review of the move).
        gone = _remove_tree(target)
        left = "the copy there was taken away again" if gone else \
            f"a half-made copy is still at {target}, and can go by hand"
        if isinstance(exc, _TakenOver):
            die(f"another chat took this folder over while it was being copied, so it "
                f"wasn't moved, and {left}. Run it again when nothing else is working here.")
        if not isinstance(exc, OSError):
            raise
        # Which file, when it was one: one that couldn't be read was "is
        # there room?" (round 21 review of the move).
        if isinstance(exc, shutil.Error) and exc.args and isinstance(exc.args[0], list) \
                and exc.args[0] and isinstance(exc.args[0][0], tuple):
            first = exc.args[0][0]
            why = f"{os_.rel(Path(first[0])) if str(first[0]).startswith(str(root)) else first[0]} " \
                  f"couldn't be copied ({one_line(str(first[-1]))[:120]})"
        else:
            why = copy_failed(exc)
        die(f"couldn't move it: {why} — nothing was moved, and {left}; "
            "is there room, and can everything here be read?")
    # Out of the way under another name first, in one step: a chat waiting
    # for the lock then finds the folder gone and says so, never taking the
    # lock in a folder being taken away (pre-merge review, 2026-10-29).
    going = root.with_name(f".{root.name}.moved-{os.getpid()}")
    try:
        os.rename(root, going)
    except OSError:
        going = root
    if going != root:
        try:
            (going / MARKER / COPIED_TO).write_text(str(target) + "\n", encoding="utf-8")
        except OSError:
            # Not an older one left naming another place.
            try:
                (going / MARKER / COPIED_TO).unlink(missing_ok=True)
            except OSError:
                pass
    # No ./os starts in it from here on: one in a chat still standing in it
    # wrote its words there, said they were safe, and they went with it
    # (round 21 review of the move). Its name comes back if it stays.
    config = going / MARKER / "config.json"
    hidden = going / MARKER / MOVING_CONFIG
    try:
        os.rename(config, hidden)
    except OSError:
        hidden = config

    def put_back() -> bool:
        # A marker left in a folder put back would later call it moved
        # (fifth pre-merge review): not put back then.
        try:
            (going / MARKER / MOVING_TO).unlink(missing_ok=True)
        except OSError:
            return False
        try:
            if hidden != config:
                os.rename(hidden, config)
            if going != root:
                os.rename(going, root)
        except OSError:
            return False
        # Only once it is back: not put back, it names the copy (round 22).
        try:
            (root / MARKER / COPIED_TO).unlink(missing_ok=True)
        except OSError:
            pass
        return True

    # Last, a look at what is about to go: a lock can be lost in ways it
    # can't tell (one taken in the moment it was said again, after a laptop
    # slept), and a save made meanwhile went with the old place (review of
    # the lock fixes). Anything not in the copy keeps the old place. Stopped
    # here, it is put back, not left hidden (round 21 review of the move).
    try:
        missed = _not_in_copy(going, target, began, (root, os_.root), str(target),
                              config_aside=hidden != config)
    except BaseException:
        put_back()
        _remove_tree(target)
        raise
    if missed:
        if not put_back():
            # Not put back: the copy lacks what changed, so both stay, and
            # the one to keep is named (review of the check before removal).
            die(f"something changed here while it was being copied ({missed}), so it wasn't "
                f"moved — and it couldn't be put back under its own name. Keep {going}: it "
                f"has the change. The copy at {target} doesn't, and can go once you have "
                "checked. Nothing was taken away.")
        left = "the copy there was taken away again" if _remove_tree(target) else \
            f"a copy is still at {target}, without that change, and can go by hand"
        if missed.startswith(SAME_THERE):
            die(f"{missed[len(SAME_THERE):]} have names that differ only in capitals, and the "
                f"place you picked can't tell them apart, so one would have gone. Nothing was "
                f"moved, and {left}. Rename one of them, then run it again.")
        die(f"something changed here while it was being copied ({missed}), so it wasn't "
            f"moved, and {left}. Run it again when nothing else is working here.")
    if going == root:
        # Not set aside, it is still where other chats look. Taken away, one
        # waiting for the lock saved into it as it went (round 21); kept,
        # it stayed live, or could be made live, and a save landed in it and
        # nowhere else (fifth and sixth pre-merge reviews). So the move is
        # called off: the folder stays the one live place, as it was.
        if not put_back():
            die(f"couldn't move it: the folder is in use, and couldn't be put back as it "
                f"was either. Keep {root}: it is the folder. The copy at {target} can go "
                "once you have checked.")
        left = "the copy there was taken away again" if _remove_tree(target) else \
            f"a copy is still at {target}, and can go by hand"
        die(f"couldn't move it: the folder is in use (another window or program has "
            f"something in it open), so nothing was moved, and {left}. Close what has it "
            "open, then run it again.")
    # Said where it went, once the look found it all there and before any of
    # it goes: stopped while it was taken away, what was left offered to be
    # put back as the folder (second pre-merge review of round 21). Not
    # before the look: a move killed during it then called a change that
    # wasn't copied something that could go (third). Outside the removal's
    # own handler: a stop here, or while the move was called off, was told
    # all of it was at the new place (round 22).
    try:
        (going / MARKER / MOVING_TO).write_text(str(target) + "\n", encoding="utf-8")
    except BaseException as exc:
        if not isinstance(exc, OSError):
            put_back()
            _remove_tree(target)
            raise
        # Not said, nothing goes: taken away unsaid, what was left said its
        # things were all there (fourth pre-merge review). Kept as an old
        # copy, it could stay or be made live (fifth and sixth). So the move
        # is called off, and the folder is as it was.
        if not put_back():
            die(f"couldn't move it: {copy_failed(exc)}, and the folder couldn't be put back "
                f"under its own name either. Keep {going}: it is the folder. The copy at "
                f"{target} can go once you have checked.")
        left = "the copy there was taken away again" if _remove_tree(target) else \
            f"a copy is still at {target}, and can go by hand"
        die(f"couldn't move it: nothing could be written in the folder ({copy_failed(exc)}), "
            f"so nothing was moved, and {left}. Is its disk full?")
    try:
        # What says where it went, and what says so (./os, its engine), go
        # last.
        last = {"os", MARKER}
        keep = {"engine.py", MOVING_CONFIG, MOVING_TO}
        for child in [*(c for c in going.iterdir() if c.name not in last),
                      *(c for c in (going / MARKER).iterdir() if c.name not in keep)]:
            if child.is_dir() and not child.is_symlink():
                _remove_tree(child)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass
        # The last few one by one, in an order that never leaves what says
        # where it went without what lets ./os say it (fourth review: a
        # drive's own order put moving-to before config.json.moving).
        for last_one in (going / MARKER / MOVING_CONFIG, going / MARKER / "engine.py",
                         going / MARKER / MOVING_TO, going / "os"):
            try:
                last_one.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                break
        if not _remove_tree(going):
            raise OSError(errno.EACCES, "some of it couldn't be taken away")
    except SystemExit:
        # A move called off has said so, and is over: taken for a stop here,
        # it said next that all of it was at the new place (seventh
        # pre-merge review of round 21).
        raise
    except BaseException as exc:
        why = copy_failed(exc) if isinstance(exc, OSError) else "it was stopped"
        # Under a name they can see: a hidden one, named as what can go by
        # hand, couldn't be found in Finder, and a whole copy of their folder
        # stayed on the disk (third pre-merge review of round 19).
        seen = root.with_name(f"{root.name} (old copy)")
        if going != root and not os.path.lexists(seen):
            try:
                os.rename(going, seen)
                going = seen
            except OSError:
                pass
        Out.warn(f"it is all at {target} now, but some of it wasn't taken from where it was "
                 f"({why}): what's left in {going} is a copy, and can go by hand")
        # Said once, and only that: a stop passed on was followed by "nothing
        # was left half-moved" (second pre-merge review, 2026-10-29).
        sys.exit(1 if isinstance(exc, OSError) else 130)
    return target


def cmd_setup(os_: Zenith, argv: list[str]) -> int:
    """Make a freshly-copied folder belong to whoever just opened it.

    /setup walks through the rest: --check says what the computer has and
    what's in the way, --unlink cuts a git clone's link to the template's
    GitHub page, --bring brings an old folder's things in, and --move-to
    moves or renames the folder itself, last of all."""
    as_json = _flag(argv, "--json")
    if _flag(argv, "--check"):
        got = setup_report(os_)
        if as_json:
            print(json.dumps(got, indent=2))
            return 0
        Out.title("setup check")
        Out.kv("computer", got["system"], 14)
        Out.kv("python", got["python"], 14)
        Out.kv("git", got["git"] or "missing", 14)
        Out.kv("claude code", got["claude"] or "missing", 14)
        Out.kv("folder", got["folder"], 14)
        Out.kv("called", got["name"], 14)
        Out.raw()
        for need in got["needs"]:
            Out.warn(f"{need['say']} —  {need['fix']}")
        if not got["needs"]:
            Out.ok("nothing in the way")
        Out.raw()
        return 0
    fresh = _flag(argv, "--fresh")
    if fresh and "--unlink" not in argv:
        die("--fresh goes with --unlink:   ./os setup --unlink --fresh")
    if _flag(argv, "--unlink"):
        with Lock(os_, "setup"):
            got = unlink_from_template(os_, fresh=fresh)
        if as_json:
            print(json.dumps(got, indent=2))
            return 0
        Out.title("setup")
        if got["result"] == "fresh" and got["kept"] in ("started", "kept"):
            Out.ok("cut the link to the template's GitHub page, and started a history of "
                   "this folder's own, with everything as it is now")
            Out.note(f"the template's old history is kept in {got['aside']}")
            Out.note("the Claude app shows it under its own name once you open it again")
        elif got["result"] == "fresh":
            Out.warn("cut the link, but the new history didn't start"
                     + (f" ({got['why']})" if got.get("why") else "")
                     + " — the next  ./os checkpoint  starts it")
            Out.note(f"the template's old history is kept in {got['aside']}")
        elif got["result"] == "has saves":
            Out.warn("you've saved in this history yourself, so it stays: a fresh one "
                     "would leave your saves out — nothing changed")
            Out.note("to cut the link and keep it all:  ./os setup --unlink")
            Out.raw()
            return 1
        elif got["result"] == "failed":
            Out.bad(f"couldn't put the old history aside: {got['why']} — nothing changed")
            Out.raw()
            return 1
        elif got["result"] == "cut":
            Out.ok("cut the link to the template's GitHub page — this folder is yours alone")
            Out.note("the Claude app shows it under its own name once you open it again")
        else:
            Out.ok("this folder isn't linked to the template's GitHub page")
        Out.raw()
        return 0
    bring = _opt_given(argv, "--bring", './os setup --bring "~/My Old Folder"')
    if bring:
        preview = _flag(argv, "--dry-run", "--preview")
        src = Path(bring).expanduser()
        if not src.is_dir():
            die(f"there is no folder at {bring}")
        src = src.resolve()
        root = os_.root.resolve()
        if src == root or root in src.parents:
            die("that is this folder, or inside it — name the old folder")
        if src in root.parents:
            # Their new folder was put inside the old one. Bringing the old one
            # in would bring this one into itself.
            die(f"this folder is inside {src}. Move it out first:  ./os setup --move-to "
                + shell_word(str(src.parent / root.name)) + "\n     then open it there and bring the old one in")
        if preview:
            plan = bring_plan(os_, src)
            got = {"kind": plan["kind"], "from": str(src),
                   "steps": [{"from": os.path.relpath(p, src), "how": how, "why": why}
                             for p, how, why in plan["steps"]]}
        else:
            with Lock(os_, "setup"):
                got = bring_in(os_, src)
                os_.commit(f"bring in {src.name}")
                Indexer(os_).build()
        if as_json:
            print(json.dumps(got, indent=2))
            return 0
        _say_bring(got, preview)
        return 0
    move_to = _opt_given(argv, "--move-to", './os setup --move-to "~/My Folder"')
    if move_to:
        # Held for the whole move, not only checked: let go before a copy to
        # another disk, a note another chat saved meanwhile went with the
        # old place (fourteenth review, 2026-10-29). The lock goes with the
        # folder, and is taken out of it at the new place.
        with Lock(os_, "setup") as lock:
            target = setup_move(os_, move_to, lock)
            for left in (target / MARKER).glob(".lock*"):
                try:
                    left.unlink()
                except OSError:
                    pass
        if as_json:
            print(json.dumps({"moved": str(target)}, indent=2))
            return 0
        Out.title("moved")
        Out.ok(str(target))
        Out.note("this chat still looks in the old place — close it, and open the folder there:")
        Out.note(f"in a terminal:  cd {shell_word(str(target))} && claude")
        Out.note("in the Claude app: open a new chat, and pick that folder")
        Out.raw()
        return 0
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
    said = History(os_).first_words(result["history"])
    if said:
        Out.note(said)
    Out.raw()
    Out.raw("  " + paint("TRY THIS", S.B, S.GOLD))
    # Not "any AI", as the first screen and the demo say: a chat-only app
    # like ChatGPT can't run ./os (review, 2026-09-30). 80 wide at most.
    for cmd, why in (
        ('./os save "anything on your mind"', "I put it somewhere sensible"),
        ("./os", "see where everything stands"),
        ("claude", "or another AI that can run commands here"),
    ):
        Out.raw("    " + paint(pad(cmd, 36), S.GOLD) + paint(why, S.FAINT))
    Out.raw()
    Out.raw("  " + paint("./os demo", S.MUTE)
            + paint("   two minutes, shows you the whole idea", S.FAINT))
    Out.raw()
    return 0


#: Home things, not a job's: the first thing a stranger sees ./os do was a
#: billing service's token refresh and a codebase kept green, and somebody
#: with a garden read that as a tool for programmers. Each one has to land as
#: the kind it says it is — the demo's second step shows all three.
DEMO_ITEMS = [
    ("The boiler keeps cutting out at night. Has to be fixed before winter. "
     "First step is booking the engineer.",
     "this one has a next action"),
    ("Notes on Gran's lemon cake: 200g butter, 200g sugar, four eggs, 200g "
     "flour, the zest of two lemons. Nothing to do — just worth keeping.",
     "this one is just worth keeping"),
    ("Keep the vegetable garden weeded: every week from spring to autumn. "
     "This never ends, it is just something I hold to.",
     "this one is just kept level"),
]
#: What step 3 searches for: a word from the first item that none of the
#: others has, so it is the one found.
DEMO_FIND = "boiler"


def cmd_demo(os_: Zenith, argv: list[str]) -> int:
    """Show the whole idea on three throwaway items, then put the folder back."""
    keep = _flag(argv, "--keep")
    loose = [i for i in Scanner(os_).scan() if Sorter.unmanaged(i)] \
        + loose_at_top(os_.root, os_.buckets())
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
        staged = []
        for text, why in DEMO_ITEMS:
            staged.append(creator.capture(text, source="demo"))
            Out.raw("    " + paint("→ ", S.GOLD) + paint(trunc(text, 62), S.INK))
            Out.raw("      " + paint(why, S.FAINT))

        step(2, "Watch where they go.")
        result = Sorter(os_).run()
        # Made here, so undo takes them away: after `demo --keep`, undo put
        # the three back in .os/cache/incoming, and check spoke of them for
        # good (audit, 2026-10-29).
        for path in staged:
            os_.brought_in(path)
        os_.commit("demo")
        Indexer(os_).build()
        # Work is one kind in two phases, so the label has to read the phase
        # off the filed item — that distinction is the whole point of step 2.
        phase_of = {os_.rel(i.path): i.status for i in Scanner(os_).scan()}
        # Where each of the three is now. In a folder with more than a dozen
        # things, sort then groups them by subject, a second move of the same
        # thing: that printed as a bare "sort →" line, and the phase was read
        # off the path from before it, so the kept-up garden was work to push.
        moves = result["moves"]

        def landed(n: int, path: str) -> str:
            for kind, src, dst in moves[n + 1:]:
                if kind in ("sort", "id") and (path == src or path.startswith(src + "/")):
                    path = dst + path[len(src):]
            return path
        filed = [(kind, landed(n, dst)) for n, (kind, _src, dst) in enumerate(moves)
                 if kind not in ("sort", "id", "group")]
        for kind, dst in filed:
            word = KIND_WORDS.get(kind, kind)
            if kind == "project":
                word = ("work you're pushing" if phase_of.get(dst) != HOLDING
                        else "work you keep up")
            Out.raw("    " + paint(pad(word, 20), S.GOLD)
                    + paint("→ ", S.FAINT) + paint(trunc(dst, 50), S.INK))

        step(3, "Find one again, without remembering where it went.")
        Out.raw("    " + paint(f"./os find {DEMO_FIND}", S.FAINT))
        # The demo's own, never theirs: in a folder that already held a note
        # about the boiler, straight after "find one again" it showed theirs.
        ours = {dst for _kind, dst in filed}
        for _score, item, _sn in Finder(os_).search(DEMO_FIND, limit=200):
            if os_.rel(item.path) in ours:
                Out.raw("    " + paint(item.title[:56], S.B))
                break

        step(4, "Change your mind about all of it, at once.")
        if keep:
            Out.raw("    " + paint("skipped — you passed --keep, so the three stay filed", S.AMBER))
        else:
            Out.raw("    " + paint("./os undo", S.FAINT))
            # Counted as ./os undo counts it: this said 9 where undo said 6.
            reversed_ = len(Undo.describe((os_.state.get("undo") or [{}])[-1], None))
            outcome = Undo(os_).revert()
            # Taken back, so it didn't happen: ./os last said "FILED demo".
            history = os_.state.get("history") or []
            if history and history[-1].get("label") == "demo":
                history.pop()
                os_.save_state()
            stage = Creator(os_).stage()
            for leftover in list(stage.iterdir()):
                if not ignored(leftover) and "source: demo" in read_text(leftover, 400):
                    leftover.unlink()
            Indexer(os_).build()
            Out.raw("    " + paint(f"✓ all {reversed_ or outcome['restored']} changes reversed — the "
                                   "three demo items are gone", S.JADE))
            Out.raw("    " + paint("(nothing of yours was touched — the demo only "
                                   "ever handles its own three)", S.FAINT))

    Out.raw()
    Out.raw("  " + paint("THAT IS THE WHOLE THING", S.B, S.GOLD))
    Out.raw("    " + paint("Say it. It gets filed. You find it later. You can always undo.", S.INK))
    Out.raw()
    Out.raw("    " + paint(pad('./os save "..."', 18), S.GOLD)
            + paint("put something real in", S.FAINT))
    # Not "any AI": one that only chats, like the ChatGPT app, can't run ./os.
    # Said as the first screen says it, and under 80 wide: it was 86, and
    # wrapped in a Terminal window as it opens.
    Out.raw("    " + paint(pad("claude", 18), S.GOLD)
            + paint("or another AI that can run commands here, then just talk", S.FAINT))
    Out.raw()
    return 0


def cmd_edit(os_: Zenith, argv: list[str]) -> int:
    """Open an item in $EDITOR, or in whatever the desktop uses."""
    if not argv or not argv[0].strip():
        die("which one?   ./os edit fix-the-boiler      (run ./os to see the names)")
    _one_name(os_, argv, "edit")
    item = the_one(os_, argv[0], "edit")
    warn_if_claimed(os_, item)
    target = item.spine or item.path
    # Only a person at a terminal gets an editor or a window. An AI running
    # this has no screen, so what it opened landed on the person's desk, and
    # the self-checks run `edit` too: every `./os test` opened TextEdit.
    if not sys.stdout.isatty():
        print(str(target))
        return 0
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
    sort)  COMPREPLY=( $(compgen -W "--dry-run --forget --json" -- "$cur") ) ;;
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
    open|edit|done|back|rename|claim|release|decide) _message 'a name like fix-the-boiler' ;;
    sort) _values 'flag' --dry-run --forget --json ;;
    check) _values 'flag' --fix --json ;;
    help) _describe -t commands 'os' cmds ;;
    *) _files ;;
  esac
}
_os "$@"
""" % listed


def cmd_completion(os_: Zenith, argv: list[str]) -> int:
    shell = (argv[0] if argv else os.environ.get("SHELL", "")).lower()
    # Anything but bash got the zsh script: `completion fish` printed it
    # with no word that it was the wrong one (audit, 2026-10-28).
    if argv and not any(s in shell for s in ("bash", "zsh")):
        die(f"there's tab-completion for zsh and bash, not {argv[0]}:   ./os completion zsh")
    print(_completion(shell))
    return 0


def call_them(os_: Zenith, owner: str, was: str) -> None:
    """Their name in About me too, where every chat looks for what to call
    them: `./os name "Sam"` set it only in config.json, which no chat reads
    (follow-up to the audit, 2026-10-28). A "Call me" line with the name it
    had before is changed, not kept beside the new one: two names would be
    one wrong."""
    import contextlib
    import io
    note = about_them_note(os_, Scanner(os_).scan())
    if note is not None and was and note.spine and note.spine.exists():
        text = read_to_rewrite(note.spine)
        old = re.compile(r"^([-*]\s+)Call me " + re.escape(was) + r"\.?(\s*\(said\))?\s*$", re.I | re.M)
        if old.search(text):
            with Lock(os_, "name"):
                os_.snapshot(note.spine)
                write_text(note.spine, set_fields(old.sub(lambda m: f"{m.group(1)}Call me {owner}. (said)",
                                                          text, count=1), {"updated": today()}))
                os_.record("edit", os_.rel(note.spine))
                os_.commit("name")
            return
    with contextlib.redirect_stdout(io.StringIO()):
        add_about_them(os_, f"Call me {owner}", True)


def cmd_name(os_: Zenith, argv: list[str]) -> int:
    """Put your name on the folder, or call the folder something else."""
    # One line each: a name with a line break wrote two lines into
    # AGENTS.md (review, 2026-10-29).
    label = one_line(_opt(argv, "--name")).strip()
    owner = one_line(" ".join(_theirs(argv))).strip().strip('"')
    if not owner and not label:
        Out.title("name")
        Out.kv("this folder", os_.config.get("name", "Zenith"), 14)
        Out.kv("belongs to", os_.config.get("owner") or "nobody yet", 14)
        Out.raw()
        Out.note('./os name "Your Name"          put your name on it')
        Out.note('./os name --name "Studio"      call the folder something else')
        Out.raw()
        return 0
    was_owner = str(os_.config.get("owner") or "")
    if (not owner or owner == was_owner) and (not label or label == os_.config.get("name")):
        # No step for no change: undo then said there was nothing left to
        # reverse, as if removed by hand (review, 2026-10-29).
        Out.title("name")
        Out.ok(f"{os_.config.get('name', 'Zenith')} · {os_.config.get('owner') or 'no owner set'} — already so")
        Out.raw()
        return 0
    # One undo step, About me line and all: without one, ./os undo straight
    # after took back the save before it; as two, one undo left the new
    # owner (undo rules and review, 2026-10-29). Kept by edited(), not
    # snapshot(), which keeps only prose.
    with Lock(os_, "name"):
        changing = [os_.dot / "config.json"] + ([os_.root / "AGENTS.md"] if label else [])
        for path in changing:
            if path.is_file():
                os_.edited(path, path.read_bytes())
        if owner:
            os_.config["owner"] = owner
        if label:
            os_.set_name(label)
        os_.save_config()
        os_.commit_held = getattr(os_, "commit_held", 0) + 1
        try:
            if owner and owner != was_owner:
                call_them(os_, owner, was_owner)
        finally:
            os_.commit_held -= 1
        os_.commit("name")
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
             "work you already have, and the rest becomes its next action, or, for "
             "something you keep up, goes under Keeps coming back. Start with "
             "\"call me\", \"from now on\", \"I prefer\", \"keep answers\" or "
             "\"About me:\", and it becomes a line in your About me note. "
             "A file, or more than about 150 words, is a note unless it has a "
             "## Next action or says type: work. Say which it is with --note or "
             "--work. Something that names a piece of work you have goes inside "
             "it, as a file of its own, with a line in its ## Log; --into <name> "
             "picks the work, and --new keeps it a thing of its own. A video, "
             "or anything bigger than 100 MB, stays where it is: a note in Notes "
             "says where it lives, and that note is what search finds. --copy "
             "puts a copy in Work/Content too, which ./os leaves alone.",
             ['os save "the boiler cuts out every night"',
              "os save ~/Downloads/boiler-manual.pdf",
              "os save --work ~/Desktop/garden-plan.md",
              'os save --into kitchen-renovation "quotes from three tilers"',
              "os save ~/Movies/garden-timelapse.mov"],
             "Wrong place? ./os undo puts it back, every time."),
    "new": ('os new <work|ongoing|note|learning|skill|helper> "<name>"',
            "Start something from a blank template, already named and stamped. "
            "If you already have something by nearly the same name it stops and says so "
            "— add --anyway if you really want both. --dry-run says where it "
            "would go, under what subject, and what it might duplicate, and "
            "writes nothing.",
            ['os new work "Fix the boiler"',
             'os new ongoing "Keep the garden weeded"',
             'os new note "Gran\'s lemon cake"',
             'os new learning "How sourdough is actually made"',
             'os new skill "Plan the week\'s meals"'],
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
              ['os claim fix-the-boiler',
               'os claim fix-the-boiler --as "ringing round for quotes"',
               "os release fix-the-boiler"],
              "It is a note, not a lock — nothing is ever refused because of a "
              "claim, it just says who was there first. A claim older than 12 "
              "hours is shown as stale and can be claimed straight over, "
              "because chats close without saying goodbye. Anyone can release "
              "anything: that is the way out when the chat that claimed "
              "something is long gone."),
    "update": ("os update   |   os update --dry-run   |   os update --check   |   os update --done"
               "   |   os update --undo",
               "Get the newest version of this program, its skills and its helpers "
               "from GitHub. Your work and notes are never touched. A skill or helper "
               "you changed yourself is kept — the new one is put beside it in "
               ".os/upgrades/ for you or your AI to merge — and one you deleted stays "
               "deleted. Settings you changed keep your changes, with any new ones added. "
               "Every addon you added is brought up to date too, the same way. Once "
               "those are merged, ./os update --done clears .os/upgrades/ away; if one "
               "still looks unmerged it says which, and --done --anyway clears them all "
               "the same. ./os undo brings them back. Not wanted after all? "
               "./os update --undo puts back the version from before the last "
               "update, and takes out what it added; a file changed since stops it, "
               "and --undo --anyway goes ahead. Addons stay as they are.",
               ["os update", "os update --dry-run", "os update --check",
                "os update --from ~/Downloads/os.zip", "os update --done", "os update --undo"],
               "Everything it replaces is copied into .os/backups/ first. If this "
               "folder's own program was changed and never published, it stops and "
               "says so; --anyway goes ahead. --check only says whether a newer "
               "version is out, and says nothing if not. --from uses a fresh download "
               "you already have, never a folder somebody has used; --addons-from "
               "does the same for addons, from a folder holding them by name."),
    "addon": ("os addon   |   os addon <name>",
              "Extra skills and helpers for one kind of work, kept apart from the "
              "template so you only get the ones you ask for. On its own it lists "
              "every addon there is, with what each one adds. With a name it adds "
              "that one. ./os update keeps the ones you added up to date.",
              ["os addon", "os addon tate"],
              "An addon only ever adds skills and helpers: it never changes your "
              "work, your notes, or anything the folder came with. If you already "
              "have a skill or helper by the same name, it stops and changes "
              "nothing. ./os undo takes an addon out again straight after; later, "
              "delete its skill's folder in .claude/skills/. A file of it you "
              "change is yours: an update keeps it and puts the new one beside it "
              "in .os/upgrades/."),
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
    "words": ('os words   |   os words <subject> "<a word you use>" …   |   '
              'os words --new <name> "<a word>" …',
              "Show the words this folder files by, and add to them. Where "
              "something lands is decided by matching words, so the fastest way "
              "to make it better is to give it the words you actually use — "
              "people, places, plants, the names of things you do. No subject "
              "that fits? --new makes one of your own.",
              ["os words",
               'os words garden "dahlias" "runner beans"',
               'os words --new knitting "yarn" "purl" "cast on"'],
              "It only ever adds — a new subject, or words in a subject's "
              "`learned` list — so the keywords you wrote yourself in "
              ".os/words.json are never touched and anything added here can be "
              "deleted without disturbing them. Changed your mind straight "
              "away? ./os undo takes the last one back. /learn writes to it "
              "at the end of studying a subject, which is why filing gets better "
              "at a subject once you have learned one. Anything that matches no "
              "subject at all goes under general."),
    "note": ("os note <name>",
             "Turn a piece of work back into a note, when there turns out to be "
             "nothing to do or keep up, only something to look up later. Every "
             "word stays; its header says note, and it moves from Work/ to Notes/.",
             ["os note fix-the-boiler"],
             "./os undo takes it straight back; ./os push makes it work again. "
             "To write a new note, use ./os save or ./os new note."),
    "hold": ("os hold <name>   |   os push <name>",
             "Say what a piece of work needs from you now. Holding means there "
             "is no next action, only a standard you keep level — it stops being "
             "counted as on the go, and stops being nagged for going quiet. "
             "Pushing puts it back on the go.",
             ["os hold fix-the-boiler", "os push fix-the-boiler"],
             "Nothing moves on disk; it is one word in the file's header. That is "
             "the point — the same job flips between the two over and over, and "
             "no filing system should make you shuffle folders for that. A note "
             "that turns out to be something to do is the exception: push or hold "
             "it and it becomes work, in Work/."),
    "find": ("os find <words>",
             "Search names, titles, tags and the full text of everything you "
             "have saved, archive included, the notes kept inside a piece of "
             "work's folder, and the name of every file in a folder, a PDF or a "
             "photo too. Plurals and word endings are fine — 'meetings' finds "
             "'meeting'. When a word you typed is in nothing, it offers a near "
             "word as a command to type, and never searches for a different "
             "word on its own. Skills and helpers are "
             "left out unless you ask for them with --kind skill. --notes looks "
             "only in Notes and what was put away from Notes, and never shows "
             "a word from Work: it is for a helper told to keep out of Work. "
             "Nothing found ends with exit code 1; with --json it still "
             "prints [] first.",
             ["os find boiler", "os find garden --kind work",
              "os find weekly --kind skill", "os find --notes boiler"],
             "You don't have to remember where you put it. A misspelling gets "
             "you the right spelling to try."),
    "show": ("os show <name>",
             "Everything worth knowing about one thing — what state it is in, when "
             "you last touched it, its next action, what was decided — without "
             "opening the file. The name on disk or the title said any reasonable "
             "way both find it. When two things share a name, it lists both and "
             "how to name each: with the folder it is in, Recipes/Pizza.",
             ["os show fix-the-boiler", 'os show "Fix the boiler"'],
             "./os edit <name> opens it properly when you want to change something."),
    "open": ("os open <name>", "Print where something lives, and show it to you in "
             "Finder, Explorer or your file manager. To open the file itself for "
             "editing, use ./os edit.",
             ["os open fix-the-boiler"],
             "The name is the handle: either the name on disk or the title said "
             "any reasonable way."),
    "edit": ("os edit <name>", "Open it in your text editor.",
             ["os edit fix-the-boiler"], "Set $EDITOR to stay in the terminal."),
    "rename": ('os rename <name> "<new name>"',
               "Give something a new name on disk and in its header in one move, "
               "so the two never disagree. Folders come out Title Case With Spaces, "
               "notes stay kebab-case files, a file's card moves with it, and links "
               "to it from other notes follow.",
               ['os rename fix-the-boiler "Replace the boiler"'],
               "Renaming by hand leaves the title and the folder saying different "
               "things — this is the one that keeps them together. ./os undo reverses it."),
    "close": ("os close <name> [done|dropped|--part <folder>]   |   os back <name>",
              "Put something away in Archive/, or take it back out. Closed means "
              "no longer live — not necessarily finished. Things leave because you "
              "stopped carrying them, and that is as true of shipped work as of "
              "abandoned work. To say which, put done or dropped after the name, "
              "and a dated line saying so goes in its ## Log. To put away one "
              "folder inside it and keep the rest live, like a folder of old "
              "quotes: ./os close fix-the-boiler --part quotes. It goes to "
              "Archive/ under the thing's name, and its ## Log says where. Its "
              "main page and sources folders stay. ./os back fix-the-boiler "
              "--part quotes brings it back.",
              ["os close fix-the-boiler", "os back fix-the-boiler",
               "os close fix-the-boiler done"],
              "Nothing is deleted, and things in the archive still turn up in "
              "./os find. If it is not over, just quiet, ./os hold it instead."),
    "decide": ('os decide <name> "<what was settled>"',
               "Append one dated line to that item's ## Decisions. A decision is "
               "never a thing of its own — it is a line in the thing it is about, "
               "which is where it is still findable a year later.",
               ['os decide fix-the-boiler "a new one, not another repair"'],
               "./os save does this by itself when the words name the item, and "
               "refuses when it cannot tell which one you meant."),
    "undo": ("os undo [--anyway] [--other-chat]", "Reverse the last thing Zenith itself did — a save, a "
             "filing, a close, a new — and say what it took back. When the last thing was "
             "done in another chat, it stops and says what that was; --other-chat takes "
             "it back all the same.",
             ["os undo", "os undo --anyway", "os undo --other-chat"],
             "It restores where files went AND what they said, twenty steps back. "
             "Undoing a save puts the folder back as it was: a file it moved in goes "
             "back where it came from, and a copy or a note it made is removed. "
             "It cannot undo edits you made by hand in a text editor — the "
             "folder's history can, back to the last checkpoint (./os help "
             "checkpoint). It never throws those edits away either: when a "
             "file was written in since that step, undo stops and names it, and "
             "with --anyway copies it aside first and says where. Not everything "
             "./os does is a step it can take back: not ./os learn (the first "
             "undo after it says so, and the next one goes ahead), not an update "
             "(./os update --undo does that), and not the junk ./os check --fix "
             "throws away."),
    "checkpoint": ('os checkpoint "<what changed>" [--here]',
                   "Keep everything in this folder as it is now, in its own history, so "
                   "an edit made by hand after this can be taken back. The first ./os "
                   "in a new folder starts the history, and your AI keeps one at the "
                   "end of each session.",
                   ['os checkpoint "the boiler notes, and next week\'s plan"'],
                   "It needs git (on a Mac: xcode-select --install). Footage in "
                   "Work/Content is left out, and so is everything .gitignore lists "
                   "or named like a key or a password, like .env or credentials.json. "
                   "A code project with a git of its own keeps its files in that one. "
                   "A history you set up here yourself gets the save too, unless "
                   "git is partway through a merge or a rebase. One that came with "
                   "a git clone of the template is left alone, and so is a bigger "
                   "one around this folder: --here starts one for this folder "
                   "alone. To see or take back a hand "
                   "edit, an AI uses git: git diff shows what changed since, and git "
                   "restore puts a file back."),
    "sort": ("os sort [--dry-run]   |   os sort --regroup [--yes]",
             "Take charge of anything you dropped in by hand. A folder you made "
             "keeps its name, and nothing in it is rewritten. A loose file, or "
             "one left at the top of this folder, keeps its own name and ending "
             "and goes where it belongs: with a header (a card beside it, if it "
             "isn't text), or, if it reads like work, in a folder of its name in "
             "Work. A folder left there goes into Notes, or Work if it reads like "
             "work, under its own name. A folder you made stays where you put "
             "it, and anything already filed stays exactly where it is, under "
             "the name it has: sort never moves or renames it.",
             ["os sort --dry-run     # show me first, change nothing", "os sort",
              "os sort --regroup     # what would move into subject folders, and what names those paths",
              "os sort --regroup --yes"],
             "./os save files things the moment you say them, so this is for the "
             "times you dragged a pile of files in from Finder instead. When Notes "
             "or Work gets crowded, --regroup splits it into subject folders, only "
             "when you ask. On its own it shows the plan and changes nothing: what "
             "would move where, and each file here that names a path that would "
             "change. With --yes it moves them, updates those paths in your pages "
             "and skills, and lists any other file (code, settings) still naming "
             "an old path, with the line, for you to fix. ./os undo takes it all "
             "back."),
    "check": ("os check [--fix]",
              "Look for anything broken, like half-written skills or dead links, "
              "and anything of yours that ./os find and the list can't reach: a "
              "folder left at the top, work moved inside another piece of work, "
              "a file too far in, a PDF's card left behind when the PDF was "
              "moved. Each comes with the command that fixes it. Two things "
              "with one name are fine, and said once.",
              ["os check", "os check --fix"],
              "--fix only repairs the mechanical. Anything needing a judgement call "
              "is reported, never guessed. It also names what an older ./os undo "
              "kept in .os/cache/incoming; --fix throws those away and says which."),
    "tidy": ("os tidy",
             "What has gone stale, what is finished but still sitting around, "
             "what looks like the same thing twice, and a folder crowded enough "
             "to split by subject.",
             ["os tidy"], "Ten minutes a week is all this system asks of you."),
    "backup": ("os backup", "A dated zip of everything, into .os/backups/.",
               ["os backup"],
               "Keeps the newest few — how many is keep_backups in .os/config.json. "
               "Move one somewhere else now and then; a copy on the same disk is "
               "not really a backup."),
    "name": ('os name "<your name>"', "Put your name on this folder.",
             ['os name "Sam"', 'os name --name "Studio"'], ""),
    "setup": ("os setup --check | --unlink | --bring <old folder> | --move-to <name>",
              "The steps /setup walks a new person through. --check says what the "
              "computer has and what's in the way, each with its fix. --unlink cuts a "
              "git clone's link to the template's GitHub page, keeping its history; "
              "with --fresh, while nothing of yours is saved in it, it starts a "
              "history of the folder's own instead, the old one kept in "
              ".os/backups/. --bring copies an old "
              "folder's things in, each filed where it belongs here; the old folder "
              "isn't changed, and --dry-run shows what would come in first. "
              "--move-to renames the folder, or moves it, on disk.",
              ["os setup --check", "os setup --bring ~/old-os --dry-run",
               'os setup --move-to "Home"'],
              "One ./os undo takes a whole --bring back out."),
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
                      ("done", "close"), ("park", "hold"), ("unpark", "hold"),
                      ("push", "hold"), ("pushing", "hold"), ("holding", "hold"),
                      ("pause", "hold"), ("resume", "hold"),
                      ("release", "claim"), ("retitle", "rename"), ("call", "rename"),
                      ("capture", "save"), ("doctor", "check"), ("review", "tidy"),
                      ("tour", "demo"),
                      ("decided", "decide"), ("addons", "addon")):
    DETAIL.setdefault(_alias, DETAIL[_real])


def cmd_help(os_: Zenith | None, argv: list[str]) -> int:
    topic = (argv[0].lstrip("/") if argv else "").lower()
    if topic in DETAIL:
        usage, what, examples, note = DETAIL[topic]
        # What is typed starts with ./ — a bare `os save` says "command not
        # found" in a terminal.
        usage = re.sub(r"(?<![\w./])os(?= |$)", "./os", usage)
        examples = [re.sub(r"^os(?= |$)", "./os", line) for line in examples]
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
        near = NEAR_MISS.get(topic, "").split(" ")[0]
        if near in DETAIL:
            Out.note(f"did you mean  ./os help {near}  ?")
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
    "hold": cmd_hold, "park": cmd_hold, "holding": cmd_hold, "pause": cmd_hold,
    "note": cmd_note,
    "claim": cmd_claim, "release": cmd_release,
    "rename": cmd_rename, "retitle": cmd_rename, "call": cmd_rename,
    "push": cmd_push, "pushing": cmd_push, "resume": cmd_push, "unpark": cmd_push,
    "back": cmd_back, "restore": cmd_back, "unarchive": cmd_back,
    "decide": cmd_decide, "decided": cmd_decide,
    "undo": cmd_undo, "oops": cmd_undo,
    # housekeeping
    "sort": cmd_sort, "file": cmd_sort,
    "check": cmd_doctor, "doctor": cmd_doctor, "fix": cmd_doctor,
    "tidy": cmd_review, "review": cmd_review, "cleanup": cmd_review,
    "backup": cmd_backup, "snapshot": cmd_backup,
    "checkpoint": cmd_checkpoint,
    "edit": cmd_edit, "e": cmd_edit,
    "demo": cmd_demo, "tour": cmd_demo,
    "name": cmd_name, "setup": cmd_setup, "init": cmd_setup,
    "update": cmd_update,
    "addon": cmd_addon, "addons": cmd_addon,
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
    "remember": "save", "write": "save",
    "delete": "close", "remove": "close", "rm": "close", "trash": "close",
    "done": "close", "finish": "close", "complete": "close",
    "maintain": "hold", "keep": "hold", "unpause": "push",
    "clean": "tidy", "organize": "sort", "organise": "sort", "health": "check",
    "lock": "claim", "mine": "claim", "unlock": "release", "free": "release",
    "stats": "status", "dash": "status", "dashboard": "status",
    "guide": "help", "manual": "help", "link": "check --fix", "repair": "check --fix",
    "vocab": "words", "vocabulary": "words", "keywords": "words", "taxonomy": "words",
    "upgrade": "update", "latest": "update", "refresh": "update",
    "plugin": "addon", "plugins": "addon", "extension": "addon", "extensions": "addon",
    # git's word and the thing it keeps: `./os commit` and `./os history` were
    # answered "./os help lists everything", and the list didn't have it.
    "commit": "checkpoint", "history": "checkpoint", "versions": "checkpoint",
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
    "cmd_back": {"--part"},
    "cmd_backup": set(),
    "cmd_checkpoint": {"--here"},
    "cmd_brief": {"--json"},
    "cmd_last": {"--json"},
    "cmd_claim": {"--as"},
    "cmd_close": {"--part"},
    "cmd_completion": set(),
    "cmd_decide": set(),
    "cmd_demo": {"--keep"},
    "cmd_doctor": {"--fix", "--json"},
    "cmd_edit": set(),
    "cmd_find": {"--in", "--json", "--kind", "--limit", "--notes"},
    "cmd_help": set(),
    "cmd_hold": set(), "cmd_note": set(),
    "cmd_index": {"--json", "--notify"},
    "cmd_learn": {"--cached", "--force", "--forget", "--json", "--limit", "--list", "-l"},
    "cmd_name": {"--name"},
    "cmd_new": {"--anyway", "--domain", "--dry-run", "--force", "--tags", "-n"},
    "cmd_open": set(),
    "cmd_push": set(),
    "cmd_release": set(),
    "cmd_rename": set(),
    "cmd_review": {"--json"},
    "cmd_save": {"--copy", "--file", "--json", "--note", "--work", "--new", "--into"},
    "cmd_setup": {"--name", "--owner", "--quiet-welcome", "--check", "--unlink", "--fresh", "--bring",
                  "--move-to", "--dry-run", "--preview", "--json"},
    "cmd_show": set(),
    "cmd_sort": {"--anyway", "--dry-run", "--forget", "--json", "-n", "--regroup", "--yes", "-y"},
    "cmd_status": {"--json"},
    "cmd_test": None,      # forwards everything to the suite
    "cmd_undo": {"--anyway", "--other-chat"},
    "cmd_update": {"--addons-from", "--anyway", "--check", "--done", "--dry-run", "--from", "--undo",
                   "--json", "-n"},
    "cmd_addon": {"--dry-run", "--from", "--update", "-n"},
    "cmd_words": {"--json", "--new"},
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

    # The first command in a copy nobody has opened makes it theirs: the day it
    # was installed, and the start of its history (see Zenith.initialise).
    # `check` and `test` are exempt: they are what a maintainer runs inside the
    # template itself, and neither should make the shipped copy anyone's.
    if os_.is_fresh() and handler not in (cmd_setup, cmd_help, cmd_doctor, cmd_test):
        try:
            # Held, as `./os setup` holds it: a checkpoint made while the first
            # run was still taking everything in took git's lock away from
            # under it, and the git words it failed with sat in every brief
            # after (review, 2026-09-30). Now it waits its turn.
            with Lock(os_, "first run"):
                began = os_.initialise()["history"]
            Indexer(os_).build()
            # Not a word of it when the answer is being parsed. `--json` means
            # stdout belongs to whatever is reading it, and a greeting printed
            # above the object broke the first `./os brief --json` any AI ran in
            # a folder nobody had opened yet — which is every folder, once.
            # Nor before `update --check`, whose every line AGENTS.md says to
            # pass on: a fresh folder's first one was "Welcome" (audit, 2026-10-29).
            # Nor above a brief, which is written for the AI and says hello
            # itself (audit, 2026-10-29).
            if "--json" not in argv and handler is not cmd_brief \
                    and not (handler is cmd_update and "--check" in argv):
                Out.raw()
                Out.raw("  " + paint("Welcome — this folder is yours now.", S.GOLD)
                        + paint("   ./os demo", S.FAINT)
                        + paint(" shows you the whole idea in two minutes.", S.FAINT))
                # No git is no reason to stop: one line, and on with the command.
                # Not before a brief, which says it too: in a terminal it came
                # twice (review, 2026-09-30).
                said = History(os_).first_words(began)
                if said and handler is not cmd_brief:
                    Out.note(said)
        except OSError:
            pass

    # A window closed, or a chat's time limit, stops a run as Ctrl-C does:
    # only Ctrl-C used to write down what a stopped bring had filed, and the
    # next bring then brought it all in twice (bring sweep, 2026-10-29).
    def stopped(_signum, _frame):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGHUP"):
        try:
            # One this run was told to let pass (an update's own ./os sort,
            # while the window may close) stays let pass (fourteenth review).
            if signal.getsignal(getattr(signal, name)) is signal.SIG_IGN:
                continue
            signal.signal(getattr(signal, name), stopped)
        except (AttributeError, ValueError, OSError):
            pass      # not on this system, or not the main thread
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
            # The file it was writing, not the spare name it writes first:
            # .os/state.json.92502.tmp~ meant nothing (audit, 2026-10-29).
            Out.note(relative_to_root(Path(re.sub(r"\.\d+\.tmp~$", "", str(where))), root))
        if getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM, errno.EROFS):
            Out.note("that folder, or a file in it, can't be written to — on a Mac, "
                     "Finder → Get Info → Sharing & Permissions; then run it again")
        elif getattr(exc, "errno", None) == errno.ENOSPC:
            Out.note("the disk is full — make some room, then run it again")
        # update and addon copy files in without undo steps, so that can't be
        # said of them: an addon half-copied was reported as nothing moved.
        if handler in (cmd_update, cmd_addon):
            Out.note("it may have stopped partway — run it again and it finishes")
        elif os_.stopped_partway:
            Out.note("it stopped partway — ./os undo reverses whatever did happen")
        else:
            # Not "nothing was left half-moved": a close into a folder that
            # couldn't be written had already moved its README (audit,
            # 2026-10-29). What can't be known isn't promised.
            Out.note("some of it may have happened — ./os check shows anything out of place")
        Out.raw()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
