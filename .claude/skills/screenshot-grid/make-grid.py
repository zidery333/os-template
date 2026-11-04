#!/usr/bin/env python3
"""Put several screenshots into one labelled picture.

    python3 .claude/skills/screenshot-grid/make-grid.py <folder> "Label=shot.png" ... [--cols N] [--title "..."]
    python3 .claude/skills/screenshot-grid/make-grid.py --shoot <url or file> <out.png> [--phone]

Writes <folder>/grid.html, then <folder>/grid.png if Chrome, Edge, Brave or
Chromium is on this computer. With none of them, open grid.html in any browser
and screenshot the whole page instead. Needs nothing beyond plain Python.
"""
import html
import math
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

CELL_H = 560      # every shot is shown this tall; its width follows its shape
GAP = 24
LABEL_H = 40
TITLE_H = 60


def size_of(path):
    """Width and height of a PNG, JPEG, GIF or WebP, read from the file's first
    bytes. None when it isn't one of those, or says it has no size."""
    with open(path, "rb") as f:
        head = f.read(30)
        size = None
        if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
            size = struct.unpack(">II", head[16:24])
        elif head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
            size = struct.unpack("<HH", head[6:10])
        elif head[:4] == b"RIFF" and head[8:12] == b"WEBP" and len(head) >= 30:
            kind = head[12:16]
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", head[26:30])
                size = (w & 0x3FFF, h & 0x3FFF)
            elif kind == b"VP8L":
                bits = int.from_bytes(head[21:25], "little")
                size = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            elif kind == b"VP8X":
                size = (int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1)
        elif head[:2] == b"\xff\xd8":
            f.seek(2)
            while True:
                byte = f.read(1)
                if not byte:
                    break
                if byte != b"\xff":
                    continue
                marker = f.read(1)
                while marker == b"\xff":
                    marker = f.read(1)
                if marker and 0xC0 <= marker[0] <= 0xCF and marker[0] not in (0xC4, 0xC8, 0xCC):
                    f.read(3)
                    dims = f.read(4)
                    if len(dims) == 4:
                        h, w = struct.unpack(">HH", dims)
                        size = (w, h)
                    break
                length = f.read(2)
                if len(length) < 2:
                    break
                f.seek(struct.unpack(">H", length)[0] - 2, 1)
    if size and size[0] > 0 and size[1] > 0:
        return size
    return None


def file_url(path):
    # as_uri turns "#", "?" and spaces into %23, %3F and %20, so they reach the file
    return pathlib.Path(path).resolve().as_uri()


def blocks_frames(url):
    """True if a web page says it won't show inside a frame."""
    if not url.startswith(("http://", "https://")):
        return False
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "make-grid"}), timeout=15) as r:
            headers = r.headers
    except Exception:
        return False  # can't ask: let the browser try
    csp = " ".join(headers.get_all("Content-Security-Policy") or []).lower()
    return bool(headers.get("X-Frame-Options")) or "frame-ancestors" in csp


def find_browser():
    names = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser", "chrome"]
    places = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for name in names:
        if shutil.which(name):
            return shutil.which(name)
    return next((p for p in places if os.path.exists(p)), None)


def screenshot(browser, page, out, size):
    """Have the browser save page as out. True only if a new file came out."""
    if os.path.isfile(out):
        os.remove(out)  # an old file left there would look like success
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tmp}",
                 f"--window-size={size}", f"--screenshot={out}", page],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            )
        except subprocess.TimeoutExpired:
            print("The browser took over a minute and was stopped.")
            return False
    return os.path.isfile(out)


def shoot(argv):
    """--shoot <url> <out.png> [--phone]: save one screenshot of a web page."""
    if len(argv) < 4:
        sys.exit(__doc__)
    url, out = argv[2], os.path.abspath(argv[3])
    phone = "--phone" in argv
    browser = find_browser()
    if not browser:
        sys.exit("No Chrome-type browser found: take this shot by hand.")
    if os.path.exists(url):
        url = file_url(url)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        target, size = url, "1440,900"
        if phone:
            if blocks_frames(url):
                sys.exit("This page won't show inside a frame, so a phone shot would come out blank. "
                         "Take it by hand in a browser window about 390 pixels wide.")
            # Chrome won't lay a page out narrower than 500px, so a phone shot
            # shows the page inside a 390px frame and keeps only that frame.
            frame = os.path.join(tmp, "phone.html")
            with open(frame, "w", encoding="utf-8") as f:
                f.write('<!doctype html><style>html,body{margin:0}iframe{border:0;width:390px;height:844px;display:block}</style>'
                        f'<iframe src="{html.escape(url)}"></iframe>')
            target, size = file_url(frame), "390,844"
        made = screenshot(browser, target, out, size)
    print(f"Made {out}" if made else f"The browser didn't make {out}: take it by hand.")


def main(argv):
    if len(argv) > 1 and argv[1] == "--shoot":
        return shoot(argv)
    cols, title, shots = None, "", []
    args = iter(argv[2:])
    for a in args:
        if a == "--cols":
            n = next(args, "")
            if not n.isdigit() or int(n) < 1:
                sys.exit("--cols needs a number after it, like --cols 2.")
            cols = int(n)
        elif a == "--title":
            title = next(args, None)
            if title is None:
                sys.exit('--title needs words after it, like --title "All four themes".')
        elif "=" in a:
            label, path = a.split("=", 1)
            shots.append((label, path))
        else:
            shots.append((os.path.splitext(os.path.basename(a))[0], a))
    if len(argv) < 3 or not shots:
        sys.exit(__doc__)
    folder = os.path.abspath(argv[1])
    sizes = []
    for _, path in shots:
        if not os.path.isfile(path):
            sys.exit(f"Can't find the shot: {path}")
        if os.path.getsize(path) == 0:
            sys.exit(f"This shot is an empty file: {path}")
        size = size_of(path)
        if not size:
            sys.exit(f"This isn't a PNG, JPEG, GIF or WebP picture with a size: {path}")
        sizes.append(size)

    os.makedirs(folder, exist_ok=True)
    cols = cols or (len(shots) if len(shots) <= 4 else math.ceil(math.sqrt(len(shots))))
    widths = [max(1, round(CELL_H * w / h)) for w, h in sizes]
    rows = [list(range(i, min(i + cols, len(shots)))) for i in range(0, len(shots), cols)]
    page_w = GAP + max(sum(widths[i] + GAP for i in row) for row in rows)
    page_h = GAP + (TITLE_H if title else 0) + len(rows) * (LABEL_H + CELL_H + GAP)

    cells = []
    for i, (label, path) in enumerate(shots):
        src = urllib.parse.quote(os.path.relpath(os.path.abspath(path), folder).replace(os.sep, "/"))
        cells.append(
            f'<figure style="width:{widths[i]}px"><figcaption>{html.escape(label)}</figcaption>'
            f'<img src="{html.escape(src)}" style="width:{widths[i]}px;height:{CELL_H}px"></figure>'
        )
    body = "".join(
        '<div class="row">' + "".join(cells[i] for i in row) + "</div>" for row in rows
    )
    heading = f"<h1>{html.escape(title)}</h1>" if title else ""
    page = f"""<!doctype html><meta charset="utf-8"><title>{html.escape(title or 'Screenshots')}</title>
<style>
html,body{{margin:0;background:#fff;font:600 22px/1 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111}}
body{{padding:{GAP}px 0 0 {GAP}px;width:{page_w - GAP}px}}
h1{{margin:0;height:{TITLE_H}px;font-size:30px}}
.row{{display:flex;gap:{GAP}px;margin-bottom:{GAP}px;align-items:flex-start}}
figure{{margin:0}}
figcaption{{height:{LABEL_H}px}}
img{{display:block;object-fit:contain;outline:1px solid #ccc;background:#f4f4f4}}
</style>{heading}{body}"""
    html_path = os.path.join(folder, "grid.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)

    browser = find_browser()
    png_path = os.path.join(folder, "grid.png")
    if not browser:
        print(f"Wrote {html_path}\nNo Chrome-type browser found: open it in a browser and screenshot the page.")
        return
    if screenshot(browser, file_url(html_path), png_path, f"{page_w},{page_h}"):
        print(f"Made {png_path} ({page_w}x{page_h})")
    else:
        print(f"Wrote {html_path}\nThe browser didn't make grid.png: open the page and screenshot it.")


if __name__ == "__main__":
    main(sys.argv)
