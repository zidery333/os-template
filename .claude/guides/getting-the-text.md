# Getting the text out of a thing

`/learn` sends you here once it knows what it's been handed. This is only
about getting clean text. What to do with the text is back in `/learn`.

## A YouTube link

`yt-dlp` fetches the automatic subtitles without downloading the video. The
folder starts empty for each video, because the clean-up below reads every
subtitle file in it:

```bash
rm -rf /tmp/add && mkdir -p /tmp/add && cd /tmp/add
yt-dlp --skip-download --write-auto-subs --sub-lang en --sub-format vtt \
  -o '%(id)s' "<the URL>"
```

For a video that isn't in English, change `en` to its language — `es` for
Spanish, `fr` for French.

Subtitle files repeat every line several times as the captions roll. Clean
that up, or you'll read the same sentence three times and the file will be
about ten times bigger than it should be:

```bash
grep -hvE '^(WEBVTT|Kind:|Language:|[0-9]{2}:)' /tmp/add/*.vtt \
  | sed -e 's/<[^>]*>//g' \
        -e 's/\[[^][]*\]//g' \
        -e 's/[[:space:]][[:space:]]*/ /g' \
        -e 's/^ //' -e 's/ $//' \
  | awk 'NF && $0 != prev { print; prev = $0 }'
```

Sanity check: expect roughly **225 words per minute** of video. A 21-minute
video should come out around 4,700 words. Ten times that means the
de-duplication didn't work and you're about to learn from tripled text.

A warning about "no supported JavaScript runtime" is normal and harmless for
subtitles. If subtitle fetching itself starts failing, that warning becomes
the likely cause, and installing `deno` fixes it.

No automatic subtitles? Ask the person to paste them from YouTube's "Show
transcript" button. Don't scrape the page — the transcript isn't in it.

Videos get thrown away more often than anything else. There's runtime to
fill, and filling it is not the same as having something to say.

## Any other link

Fetch the URL. If what comes back is menus and navigation rather than the
article — common on documentation sites that build themselves in the
browser — try again with the browser tools.

**Never extract from a page you only partly got.** Say what happened instead.
Half a page produces a note that's confidently wrong.

Anything listed as an official source in `notes/where-i-learn/who-to-trust.md` — docs,
release notes, the engineering blog — comes in as **from the source**, and
**beats a video on the same question**.

Articles get thrown away less often than videos; there's no runtime to pad.
But a release note that changes nothing you do is still a throw-away.

## A file path

Read it. PDFs read fine. Word and PowerPoint files don't — the file reader
turns them away — so pull the words out first, Word on the first line,
PowerPoint on the second:

```bash
unzip -p "<the file>" word/document.xml | awk '{ gsub(/<\/w:p>/, "\n"); gsub(/<[^>]*>/, ""); print }'
unzip -p "<the file>" 'ppt/slides/slide*.xml' | awk '{ gsub(/<\/a:p>/, "\n"); gsub(/<[^>]*>/, ""); print }'
```

On a Mac, `textutil -convert txt -stdout "<the file>"` does Word, RTF and
OpenDocument files more cleanly.

## Pasted text

Use it as it is.

## Two sources that disagree

Write the disagreement down. Don't quietly overwrite the older one. Which is
right is worth knowing, and so is the fact that they disagreed.
