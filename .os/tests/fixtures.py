"""Synthetic material for the test suite: realistic, varied, and labelled with
the bucket each piece *should* land in."""

import random

PROJECTS = [
    ("Ship the billing rewrite", "engineering",
     "Deadline Friday. Migrate the billing service off the legacy schema.\n\n"
     "## Tasks\n- [ ] write the migration\n- [ ] backfill\n- [ ] cut over\n\nBlockers: staging database is stale."),
    ("Launch the Q3 newsletter series", "writing",
     "Goal: six essays, one a week, shipped by end of quarter.\n\n"
     "## Milestones\n- [ ] outline all six\n- [ ] draft one\n- [ ] set up the send\n\nDeadline is hard."),
    ("Rebuild the onboarding funnel", "marketing",
     "Launch a new landing page and email sequence. MVP by the 15th.\n\n"
     "## Next steps\n- [ ] copy pass\n- [ ] build the page\n- [ ] wire conversion tracking"),
    ("Close the Northwind contract", "business",
     "Proposal sent, deadline for signature is the 30th. Deliverable: signed SOW and kickoff call booked.\n\n"
     "- [ ] chase legal\n- [ ] book kickoff"),
    ("Redesign the settings screen", "design",
     "Ship v2 of the settings UI. Figma mockup approved, build phase now.\n\n- [ ] component pass\n- [ ] spacing audit"),
    ("Migrate the data warehouse", "engineering",
     "Deadline end of month. Move every pipeline to the new schema.\n\n- [ ] audit pipelines\n- [ ] migrate\n- [ ] verify"),
    ("Publish the pricing study", "research",
     "Finish the competitive analysis and ship the whitepaper. Milestone: draft by the 10th.\n\n- [ ] finish the survey\n- [ ] write it up"),
    ("Automate the monthly close", "finance",
     "Build the reconcile script so the monthly close takes an hour, not a day. Ship by quarter end.\n\n- [ ] pull statements\n- [ ] write the script"),
]

AREAS = [
    ("Codebase health", "engineering",
     "Ongoing responsibility. The standard: green CI, no lint errors, test coverage never falls.\n"
     "Cadence: every week. This is maintained forever, it has no end date."),
    ("Personal finances", "finance",
     "Ongoing. The standard: every expense categorised within a week, budget reviewed monthly.\n"
     "Recurring, always. Cadence: monthly."),
    ("Writing practice", "writing",
     "Ongoing standard: 500 words every weekday. No deadline, no finish line. Recurring routine."),
    ("Client relationships", "business",
     "Ongoing stewardship of every retainer client. Standard: no client goes two weeks without contact.\n"
     "Cadence: weekly. Recurring responsibility, maintained always."),
    ("Fitness", "personal",
     "Ongoing. The standard: three workouts a week, sleep before midnight. Routine, recurring, no end date."),
]

NOTES = [
    ("How the filing works here", "operations",
     "Reference. A thing is called what it is, so the note on running a retro is the file\n"
     "named for it. Key ideas: the name is the handle, nothing is tagged. Overview only."),
    ("Prompt caching, explained", "ai",
     "Notes on how prompt caching works with Claude. TLDR: the cache key is the prefix of the context window.\n"
     "Key takeaways: order your context stable-first. Definition of a cache breakpoint."),
    ("Postgres index types cheat sheet", "engineering",
     "Reference for choosing an index. B-tree is the default. GIN for arrays and full text search.\n"
     "Overview of when each applies in a database schema."),
    ("Notes on narrative structure", "writing",
     "Summary of the three-act shape. Key ideas: the midpoint reversal is the load-bearing beat.\n"
     "Excerpt and takeaways from a craft essay on prose and story."),
    ("Typography scale reference", "design",
     "Cheat sheet. A modular scale of 1.25 reads well for body copy. Definition of leading and tracking.\n"
     "Overview of spacing, typography and layout defaults."),
    ("How MCP servers are discovered", "ai",
     "Reference notes. An MCP server is declared in settings; the agent enumerates its tools at session start.\n"
     "Key ideas: tool descriptions are the whole interface. Overview of the protocol."),
    ("Competitive pricing landscape", "research",
     "Findings from a survey of eight vendors. Analysis: everyone anchors on seats, nobody on usage.\n"
     "Source: vendor pricing pages, read this month. Methodology and evidence below."),
    ("SEO keyword research basics", "marketing",
     "Overview. Search volume matters less than intent. Definition of a long-tail keyword.\n"
     "Cheat sheet for choosing a keyword to target on a landing page."),
    ("Tax deduction categories", "finance",
     "Reference for bookkeeping. Definition of each expense category and what an accountant expects.\n"
     "Overview of what a receipt has to show."),
    ("Spaced repetition, summarised", "learning",
     "Notes on the learning technique. Key ideas: the forgetting curve, the expanding interval.\n"
     "Overview of how to build a study plan around flashcards and practice."),
    ("Sleep and recovery notes", "personal",
     "Notes on sleep hygiene. Key takeaways: consistent wake time beats total hours. Overview of the habit loop."),
    ("Runbook: restoring from backup", "operations",
     "Standard operating procedure. Checklist and process for a restore. Runbook for the on-call engineer."),
]

SESSION_NOTES = [
    ("Standup 12 March", "operations",
     "Meeting notes. Today I reviewed the migration plan. Decision log: we ship behind a flag.\n"
     "What happened: the staging cutover slipped a day."),
    ("Retro on the launch", "operations",
     "Retrospective. What happened: we shipped two days late. Decision: cut scope earlier next time.\n"
     "Session log for the team."),
]

ASSETS = [
    ("quarterly-numbers.csv", "date,revenue\n2026-01-01,12000\n2026-02-01,15400\n"),
    ("architecture-diagram.svg", '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'),
    ("brand-palette.json", '{"gold":"#a07d2c","ink":"#1a1815"}'),
    ("export-2026-08.jsonl", '{"row":1}\n{"row":2}\n'),
    ("meeting-recording.txt.mp3", "not really audio"),
]


def bulk(n: int, seed: int = 7) -> list[tuple[str, str, str]]:
    """(marker, content, expected_bucket) — n synthetic inbox items.

    The marker is a unique token embedded in the content, so a test can find
    where any given item ended up no matter how the sorter renamed it.
    Asset markers are `ASSET-<n>::<filename>`; the filename is after the `::`.
    """
    rng = random.Random(seed)
    out: list[tuple[str, str, str]] = []
    pools = [
        (PROJECTS, "Work", "pushing"),
        (AREAS, "Work", "holding"),
        (NOTES, "Notes", "note"),
        (SESSION_NOTES, "Notes", "session"),
    ]
    reserve = min(len(ASSETS), max(0, n // 12))
    i = 0
    while len(out) < max(0, n - reserve):
        pool, bucket, kind = pools[i % len(pools)]
        title, domain, body = pool[(i // len(pools)) % len(pool)]
        variant = (i // (len(pools) * len(pool))) + 1
        name = f"{kind}-{i:03d}.md"
        suffix = f" ({variant})" if variant > 1 else ""
        marker = f"FIXTURE-{i:04d}"
        text = f"# {title}{suffix}\n\n{body}\n\n<!-- {marker} -->\n"
        out.append((marker, text, bucket))
        i += 1
    for j, (name, body) in enumerate(ASSETS[:reserve]):
        out.append((f"ASSET-{j}::{name}", body, "Notes"))
    rng.shuffle(out)
    return out[:n]
