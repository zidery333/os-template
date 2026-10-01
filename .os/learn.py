"""Fetching and cleaning source material, so any AI can learn from it.

This is the mechanical half of learning: list what a channel has, pull the
words out of a video, cache them. It makes no judgements — which sources are
worth reading, what the method is, and whether somebody is selling you a
course are all questions for whatever AI is driving. Those cannot be hardcoded
and this file does not try.

The one part of Zenith that goes out to the internet, and the one part that
wants something installed (yt-dlp). Everything else works with neither.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional

CACHE_DIRNAME = "transcripts"
STAMP_EVERY = 30            # seconds between timestamps in cleaned text
LIST_LIMIT = 60             # titles pulled when ranking a channel
FETCH_TIMEOUT = 300


# ── finding yt-dlp ───────────────────────────────────────────────────────────

def ytdlp() -> Optional[str]:
    """Where yt-dlp is, or None. Checks PATH, then the usual pip locations."""
    found = shutil.which("yt-dlp")
    if found:
        return found
    for guess in (Path.home() / ".local/bin/yt-dlp",
                  Path("/opt/homebrew/bin/yt-dlp"),
                  Path("/usr/local/bin/yt-dlp")):
        if guess.exists() and os.access(guess, os.X_OK):
            return str(guess)
    return None


def install_hint() -> str:
    """The one line to type. Whichever installer this machine actually has."""
    if shutil.which("pipx"):
        return "pipx install yt-dlp"
    if shutil.which("brew") and sys.platform == "darwin":
        return "brew install yt-dlp"
    return f"{Path(sys.executable).name} -m pip install --user yt-dlp"


# ── urls ─────────────────────────────────────────────────────────────────────

_ID = re.compile(r"(?:v=|/shorts/|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})")


def video_id(url: str) -> Optional[str]:
    """The eleven characters that name a video, from any shape of link."""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    hit = _ID.search(url)
    return hit.group(1) if hit else None


def as_listing(url: str) -> str:
    """Point a channel link at its uploads, leaving playlists alone."""
    if "list=" in url or "/playlist" in url:
        return url
    if re.search(r"/(videos|streams|shorts|featured)/?$", url):
        return re.sub(r"/(streams|shorts|featured)/?$", "/videos", url)
    if re.search(r"(youtube\.com/(@|c/|channel/|user/))", url):
        return url.rstrip("/") + "/videos"
    return url


# ── cleaning ─────────────────────────────────────────────────────────────────

_TAG = re.compile(r"<[^>]+>")
_CUE = re.compile(r"^(\d\d):(\d\d):(\d\d)[.,]\d+\s+-->")
_ENTITY = (("&gt;", ">"), ("&lt;", "<"), ("&amp;", "&"),
           ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " "))


def clean_vtt(path: Path, stamp_every: int = STAMP_EVERY) -> str:
    """Auto-captions into readable prose, with a timestamp every half minute.

    YouTube's automatic captions roll: each cue repeats the tail of the one
    before it so the words appear to scroll. Left alone that triples the size
    and makes it unquotable. Dropping any line identical to the last one
    collapses it — 271KB of subtitles became 30KB of prose in testing, and the
    timestamps survive, which is what makes a claim in a note checkable.
    """
    out: List[str] = []
    last = None
    seconds = 0.0
    next_stamp = 0.0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cue = _CUE.match(line)
        if cue:
            h, m, s = (int(x) for x in cue.groups())
            seconds = h * 3600 + m * 60 + s
            continue
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        text = _TAG.sub("", line).strip()
        for entity, char in _ENTITY:
            text = text.replace(entity, char)
        if not text or text == last:
            continue
        last = text
        if seconds >= next_stamp:
            out.append("\n[%02d:%02d] " % (int(seconds) // 60, int(seconds) % 60))
            next_stamp = seconds + stamp_every
        out.append(text + " ")
    return "".join(out).strip() + "\n"


# ── the cache ────────────────────────────────────────────────────────────────

def cache_dir(root: Path) -> Path:
    """Transcripts live in .os/, not in a bucket.

    Anything unfiled sitting in Work or Notes gets adopted by `./os sort`
    and reported as clutter until it is. Six transcripts per subject would
    either be filed as notes or nag forever, so they are kept out of the
    buckets entirely — and keyed by video, so a source pulled for one subject
    is free for the next.
    """
    path = root / ".os" / CACHE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached(root: Path, vid: str) -> Optional[Path]:
    path = cache_dir(root) / f"{vid}.txt"
    return path if path.exists() else None


# ── talking to yt-dlp ────────────────────────────────────────────────────────

def _run(args: List[str], timeout: int = FETCH_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


#: What yt-dlp says, and what it means to somebody who is not yt-dlp. Ordered:
#: the first match wins, so put the specific causes above the general ones.
#: Everything on the left is a fragment of a real stderr line, lowercased.
_WHY = (
    ("sign in to confirm your age", "that one is age-restricted, so its words "
                                    "can't be fetched without signing in"),
    ("confirm you're not a bot", "YouTube asked for a sign-in — try again in a "
                                 "few minutes"),
    ("sign in to confirm", "YouTube asked for a sign-in — try again in a few minutes"),
    ("private video", "that one is private"),
    ("members-only", "that one is for channel members only"),
    ("removed by the uploader", "that one has been taken down"),
    ("video unavailable", "that one isn't available — deleted, private, or "
                          "blocked where you are"),
    ("is not available", "that one isn't available — deleted, private, or "
                         "blocked where you are"),
    ("429", "YouTube is rate-limiting this machine — wait a few minutes and "
            "try again"),
    ("too many requests", "YouTube is rate-limiting this machine — wait a few "
                          "minutes and try again"),
    ("failed to resolve", "couldn't reach YouTube — check the connection"),
    ("temporary failure in name resolution", "couldn't reach YouTube — check "
                                             "the connection"),
    ("nodename nor servname", "couldn't reach YouTube — check the connection"),
    ("unable to download webpage", "couldn't reach YouTube — check the connection"),
    ("urlopen error", "couldn't reach YouTube — check the connection"),
    ("no such file or directory", "yt-dlp could not write the subtitles to disk"),
    ("unsupported url", "yt-dlp doesn't know that site"),
    ("no video formats", "there is nothing fetchable at that link"),
)


def why_empty(done: subprocess.CompletedProcess) -> str:
    """Why nothing came back, in a sentence somebody can act on.

    Every failure used to be reported as "no captions on this one", which is
    the one thing it usually was not: a deleted video, a rate-limit, a dropped
    connection and a genuinely silent video all said the same wrong thing, and
    the only honest response to it — go and find another source — was wasted
    work in three cases out of four. yt-dlp already knows which it was; it just
    says so in its own language, on stderr."""
    said = (done.stderr or "").lower()
    for fragment, meaning in _WHY:
        if fragment in said:
            return meaning
    if "no subtitles" in said or "requested format" in said:
        return "no captions on this one"
    # Something yt-dlp knows a name for and this does not. Its own last word
    # beats a guess — but only the lines it marked ERROR: the rest are notes to
    # itself about players and formats, and reading one out as the reason is
    # how a working folder starts sounding broken.
    for line in reversed((done.stderr or "").strip().splitlines()):
        if not line.strip().startswith("ERROR"):
            continue
        line = re.sub(r"^\s*ERROR:\s*(\[[^\]]+\]\s*)?", "", line.strip())
        line = re.sub(r"\s*\(caused by .*$", "", line)
        line = re.sub(r"^[A-Za-z0-9_-]{11}:\s*", "", line)
        if line and len(line) < 200:
            return line
    return "no captions on this one"


def listing(url: str, limit: int = LIST_LIMIT) -> List[dict]:
    """What a channel has, cheaply — titles and view counts, nothing fetched.

    Deliberately returns everything and ranks nothing. Sorting by views alone
    is the surest way to end up with whatever had the best thumbnail, which in
    exactly the subjects people want to learn is the sales material.
    """
    exe = ytdlp()
    if not exe:
        raise RuntimeError("no-ytdlp")
    fields = "%(id)s\t%(view_count)s\t%(upload_date)s\t%(duration)s\t%(title)s"
    done = _run([exe, "--flat-playlist", "--playlist-end", str(limit),
                 # `--` first: a "URL" starting with a dash is never read as
                 # one of yt-dlp's own options (`--exec`, say).
                 "--ignore-errors", "--print", fields, "--", as_listing(url)])
    rows = []
    for line in done.stdout.splitlines():
        bits = line.split("\t")
        if len(bits) != 5 or len(bits[0]) != 11:
            continue
        vid, views, date, secs, title = bits
        rows.append({
            "id": vid,
            "views": int(views) if views.isdigit() else None,
            "date": date if date.isdigit() else None,
            "minutes": round(int(secs) / 60) if secs.isdigit() else None,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    if not rows:
        # A channel with nothing on it is a real answer; anything else here was
        # a failure, and it used to be re-thrown as whatever yt-dlp's last line
        # of stderr happened to be — which for a name that would not resolve was
        # a hundred and eighty characters of nested TransportError.
        raise RuntimeError(why_empty(done) if done.returncode else "nothing came back")
    return rows


#: Which caption tracks to ask for, in one request. English first because it is
#: what the rest of this folder is written in — then, as a fallback, whatever
#: the video was actually spoken in: YouTube always names that track `<lang>-orig`,
#: so this is one extra file and never a hundred. Before it existed, a video in
#: any other language reported "no captions on this one" while sitting on a full
#: set of them, and there was no way to learn anything from a source that wasn't
#: in English.
SUB_LANGS = "en.*,.*-orig"


def _track_lang(path: Path) -> str:
    """The language of a subtitle file, from the name yt-dlp gave it.

    `<id>.en.vtt`, `<id>.ja-orig.vtt` — the middle piece is the language.
    `-orig` is YouTube saying "this is the one it was spoken in", which is a
    fact about the track and not about the language, so it is dropped."""
    bits = path.name.split(".")
    lang = bits[-2] if len(bits) >= 3 else ""
    return lang[:-5] if lang.endswith("-orig") else lang


def _track_rank(path: Path):
    """Sort key: English first, then the shortest name.

    Was `".auto." in name`, which is nothing yt-dlp has ever written — auto
    captions land under the plain language code like any other. So the test
    never fired, and with more than one language on offer the winner was
    whichever sorted first alphabetically."""
    lang = _track_lang(path)
    return (not lang.startswith("en"), len(path.name), path.name)


def transcript(root: Path, url: str, force: bool = False) -> dict:
    """The words of one video, cleaned and cached. Cheap on a second call."""
    vid = video_id(url)
    if not vid:
        return {"url": url, "ok": False, "why": "that is not a video link"}
    have = cached(root, vid)
    if have and not force:
        return {"id": vid, "ok": True, "path": str(have),
                "words": len(have.read_text(encoding="utf-8").split()), "cached": True}
    exe = ytdlp()
    if not exe:
        raise RuntimeError("no-ytdlp")
    with tempfile.TemporaryDirectory() as tmp:
        done = _run([exe, "--skip-download", "--write-subs", "--write-auto-subs",
                     "--sub-langs", SUB_LANGS, "--sub-format", "vtt",
                     # Keep going when one track of the several asked for fails;
                     # without it a single 429 on the English track threw away
                     # the original-language one that would have downloaded fine.
                     "--ignore-errors",
                     "-o", str(Path(tmp) / "%(id)s"),
                     "--", f"https://www.youtube.com/watch?v={vid}"])
        vtts = sorted(Path(tmp).glob("*.vtt"))
        if not vtts:
            return {"id": vid, "ok": False, "why": why_empty(done)}
        best = min(vtts, key=_track_rank)
        lang = _track_lang(best)
        text = clean_vtt(best)
    out = cache_dir(root) / f"{vid}.txt"
    out.write_text(text, encoding="utf-8")
    result = {"id": vid, "ok": True, "path": str(out), "language": lang,
              "words": len(text.split()), "cached": False}
    if lang and not lang.startswith("en"):
        # Said out loud rather than left to be noticed halfway down a wall of
        # text somebody cannot read. It is still worth having — quoting it is
        # what makes a claim checkable — but whoever is reading needs to know
        # they are about to translate rather than skim.
        result["note"] = f"these captions are in {lang}, not English"
    return result


def transcripts(root: Path, urls: Iterable[str], force: bool = False) -> List[dict]:
    return [transcript(root, u, force) for u in urls]


def inventory(root: Path) -> List[dict]:
    out = []
    for path in sorted(cache_dir(root).glob("*.txt")):
        out.append({"id": path.stem, "path": str(path),
                    "words": len(path.read_text(encoding="utf-8").split())})
    return out


def forget(root: Path, ids: Iterable[str]) -> int:
    gone = 0
    cache = cache_dir(root).resolve()
    for vid in ids:
        path = cache_dir(root) / f"{vid}.txt"
        # Only ever a file in the cache. An id of `../../Notes/diary`, or a
        # whole path, used to delete that file instead, for good.
        if path.resolve().parent != cache:
            continue
        if path.is_file():
            path.unlink()
            gone += 1
    return gone


# ── vocabulary ───────────────────────────────────────────────────────────────
#
# The other half of learning, and the half that compounds. Every subject comes
# with its own words — "ad set", "learning phase", "roas" — and this folder
# decides where things go by matching words. Feeding what a study run picked up
# back into `.os/words.json` is what makes the next `os save "hook rate was
# terrible"` land in the right place without anybody being told.
#
# It only ever writes a `learned` list. The person's own `keywords` stay theirs,
# and anything written here can be deleted without disturbing them.

def words_path(root: Path) -> Path:
    return root / ".os" / "words.json"


def load_words(root: Path) -> dict:
    """`.os/words.json`, checked far enough to be worth walking.

    This is the file the person is told to open, which makes it the one most
    likely to be half-edited. A `domains` written as a string, or a subject
    written as a list, is one keystroke away and used to be an AttributeError
    on the next `./os words` — a traceback about a file they had just been
    invited to edit. It says what is wrong instead; the shape of each subject
    is checked where it is read, below."""
    data = json.loads(words_path(root).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(".os/words.json should be a block of settings in { }")
    if not isinstance(data.setdefault("domains", {}), dict):
        raise ValueError(".os/words.json: `domains` should be a block of "
                         "subjects in { }")
    return data


def write_words(root: Path, data: dict) -> None:
    """Rewrite words.json, keeping its indentation, its accents and its newline.

    Through a temporary file: a half-written taxonomy is worse than an old one,
    and this is the file the person is invited to edit by hand. The name of that
    temporary file carries the pid, so two runs writing at once cannot pull it
    out from under each other, and ends `.tmp~` so that the one time this does
    die mid-write, what it leaves behind is already ignored by git and by
    `./os check`."""
    path = words_path(root)
    body = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp~")
    try:
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _spec(blocks: dict, name: str) -> dict:
    """One subject's block, or an empty one if it was written as something else."""
    spec = blocks.get(name)
    return spec if isinstance(spec, dict) else {}


def _listed(spec: dict, key: str) -> List[str]:
    raw = spec.get(key)
    return [str(w) for w in raw] if isinstance(raw, list) else []


def domains(root: Path) -> List[dict]:
    """Every domain this folder knows, and how much vocabulary each carries."""
    out = []
    blocks = load_words(root)["domains"]
    for name in blocks:
        spec = _spec(blocks, name)
        out.append({"domain": name, "label": str(spec.get("label") or name),
                    "keywords": len(_listed(spec, "keywords")),
                    "learned": len(_listed(spec, "learned"))})
    return out


def teach(root: Path, domain: str, terms: Iterable[str]) -> dict:
    """Add what a subject taught to one domain's `learned` list."""
    data = load_words(root)
    blocks = data["domains"]
    if domain not in blocks:
        return {"ok": False, "why": f"no domain called {domain!r}",
                "domains": sorted(blocks)}
    spec = _spec(blocks, domain)
    # Never say twice what the person already said once, in either list.
    known = {w.strip().lower() for w in _listed(spec, "keywords")}
    known |= {w.strip().lower() for w in _listed(spec, "learned")}
    added: List[str] = []
    already: List[str] = []
    for raw in terms:
        term = re.sub(r"\s+", " ", str(raw).strip().lower())
        if not term or len(term) > 40:
            continue
        if term in known:
            already.append(term)
            continue
        known.add(term)
        added.append(term)
    if added:
        spec["learned"] = _listed(spec, "learned") + added
        blocks[domain] = spec
        data["domains"] = blocks
        write_words(root, data)
    return {"ok": True, "domain": domain, "added": added,
            "already_known": already, "learned_now": len(_listed(spec, "learned"))}


# ── the command line ─────────────────────────────────────────────────────────
#
# Everything above is a library; this is the half an AI can actually reach.
# Output is JSON on stdout, always, so whatever is driving can read it without
# parsing prose. Anything gone wrong is JSON too, with a `why` a person could
# act on — a missing yt-dlp says the one line to type on *this* machine.

def find_root(start: Optional[Path] = None) -> Path:
    """The Zenith folder this is running inside. Same rule the engine uses."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".os" / "config.json").exists():
            return candidate
    return Path(__file__).resolve().parent.parent


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "help"
    rest = args[1:]
    root = find_root()

    def emit(payload) -> int:
        """Print the answer, and exit non-zero when it is a refusal.

        `ok` and the exit status have to agree: a script that only looks at one
        of them must not get a different story from the one that looks at the
        other."""
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("ok", True) else 1

    if command in ("-h", "--help", "help"):
        return emit({"commands": {
            "list <channel-or-playlist-url>": "what it has: id, title, views, date, minutes",
            "get <video-url> [more...]": "fetch and cache the words of each",
            "have": "transcripts already cached",
            "forget <video-id> [more...]": "drop cached transcripts",
            "words": "the domains this folder knows",
            "words <domain> <term> [more...]": "teach a domain the words a subject uses",
        }, "note": "every command prints JSON. list and get need yt-dlp; words does not."})

    try:
        if command == "list":
            if not rest:
                return emit({"ok": False, "why": "give me a channel or playlist link"})
            return emit({"ok": True, "videos": listing(rest[0])})
        if command == "get":
            if not rest:
                return emit({"ok": False, "why": "give me one or more video links"})
            # `ok` is whether anything came back, not whether the run finished.
            # It used to be a flat true, so a batch where every single source
            # failed reported success with a list of failures inside it, and
            # anything checking only the top line believed it.
            results = transcripts(root, rest)
            return emit({"ok": any(r.get("ok") for r in results), "results": results})
        if command == "have":
            return emit({"ok": True, "cached": inventory(root)})
        if command == "forget":
            return emit({"ok": True, "removed": forget(root, rest)})
        if command == "words":
            if not rest:
                return emit({"ok": True, "domains": domains(root),
                             "usage": 'words <domain> "<term>" [more...]'})
            if len(rest) == 1:
                return emit({"ok": False,
                             "why": "give me a domain and at least one word",
                             "domains": [d["domain"] for d in domains(root)]})
            return emit(teach(root, rest[0], rest[1:]))
    except RuntimeError as exc:
        if "no-ytdlp" in str(exc):
            return emit({"ok": False, "why": "yt-dlp is not installed",
                         "fix": install_hint(),
                         "note": "everything else in this folder works without it"})
        return emit({"ok": False, "why": str(exc)})
    except subprocess.TimeoutExpired:
        return emit({"ok": False, "why": "that took too long — try one video at a time"})
    except json.JSONDecodeError as exc:
        return emit({"ok": False, "why": f".os/words.json has a typo in it — {exc}"})
    except ValueError as exc:
        # Raised by `load_words` about a shape, and already a sentence.
        return emit({"ok": False, "why": str(exc)})
    except OSError as exc:
        # Named rather than assumed: this used to blame words.json for every
        # OSError, including a transcript that could not be written to disk.
        where = getattr(exc, "filename", "") or "a file it needed"
        return emit({"ok": False, "why": f"could not read or write {where} — {exc}"})

    return emit({"ok": False, "why": f"no such command '{command}'",
                 "try": "python3 .os/learn.py help"})


if __name__ == "__main__":
    raise SystemExit(main())

