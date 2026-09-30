# Getting the words out of a source

## YouTube

```bash
./os learn --list --json "<channel or playlist url>"   # what they have
./os learn --json "<video url>" "<another>"            # the actual words
./os learn --cached                                    # what you already have
```

No channel, just a subject? `--list --json "ytsearch12:<what they asked>"` — no
web search needed. **Never pick by views**; they predict nothing either way.
Read the titles and judge. Fetching caches, so a top-up costs nothing. If yt-dlp
is missing it prints the one line to install it — pass that on and use `WebFetch`
meanwhile.

Transcripts land in `.os/transcripts/` with a `[MM:SS]` stamp every half minute.
**Cite them** — that is what turns a claim into something they can go and check.
Cite where the claim *starts*: sentences run across a stamp, so the line you
found it on is often its second half.

They are kept, never expire, and are fetched again on demand, so they cost
nothing to drop: `./os learn --forget <id> …`. `./os tidy` mentions them once
the pile gets big. Don't tidy them mid-run — a top-up costs nothing only while
they are still there.

A source that isn't in English still works: the words come back in the language
it was spoken in, and the result says which. Translate what you quote and say
that you did. A refusal is a sentence — no captions, unavailable, rate-limited,
no connection — and they mean different things: only "no captions on this one"
is a reason to go and find a different source.

### Triage before you read

Every line starts with its own timestamp, so grep whole lines — a character
window silently misses the middle of a 600-character one.

```bash
grep -icE "(term|term|term)" *.txt          # density, ranks them
grep -inE "(link in (the )?desc|book a call|sponsored|\.com/)" f.txt
```

Read the dense ones properly; skim the rest. On a real run this turned 21,000
words into a read order for nothing.

## The official source

Reach it by following the link from the thing's **own** channel, repo, profile
or book — not the first search result. Lookalike domains and SEO mirrors are the
whole business model of some subjects, and a mirror is months stale even when
it is honest.

`WebSearch` finds candidates, `WebFetch` reads them. Read the page; never
summarise it from a search snippet. Record the URL and the date you read it —
a documentation page is a fact with an expiry date on it.

Ranked search results, content farms, aggregators, wiki mirrors and "the best X
of <this year>" listicles are never primary, and rarely worth a citation at all.

## Reading the pitch

The tells are in the transcript, timestamped: *our community*, *payouts*,
*link in the description*, *book a call*, *free training*, *apply*, a Discord.

**Where the pitch lands matters more than whether there is one.** Almost
everyone worth reading sells something: a pitch after nine minutes of method is
a sponsor; a pitch at `[03:04]`, before anything is taught, *is* the video.

Inverse tell, and stronger: real numbers — sizing, thresholds, what they would
actually spend — mean real work. Say who sells what and let them price it in.
