# What changed in each version

Newest at the top. `/update-os` reads this to know what to carry into files
you've made your own, so each entry says exactly what moved, file by file.

## 2026-09-29 — records ask before they're replaced, and a history from day one

What changes for you:

- **Replacing a whole decision log or write-up now asks you first.** So does
  a command that rewrites one (`sed -i`, a single `>`, `truncate`, `cp` or
  `mv` onto one), or that moves or deletes a decision log. Before, Claude
  only got a warning, and it arrived after the old text was gone. Editing
  part of one, adding to the end with `>>`, and deleting or moving a
  write-up still go through without asking.
- **`/setup` now starts a history for the folder**: a git folder with a first
  save called `Set up`, so what gets saved at the end of a session can be
  taken back. It won't in a copy made with `git clone` of the template (it
  gives you the command to start fresh instead), or on a Mac without Apple's
  developer tools. Set up before this version and have no history? Run
  `bash .claude/scripts/history.sh start`.
- **Claude Code's own memory is off in this folder**, so what Claude learns
  about you goes in `me/`, where you can read it, not in a hidden folder of
  Claude Code's. To turn it back on, delete the line
  `"autoMemoryEnabled": false,` near the top of `.claude/settings.json`.
- **`/catch-me-up` looks for a newer version.** One request to GitHub, five
  seconds at most, carrying nothing about you, and silent when you're
  offline. If one is out, it says `/update-os` brings it in.
- **"Remember ..." or "from now on ..." saves at once.** That was the asking,
  so Claude saves it and says which file it went in, instead of asking "Want
  me to save that?" back. "I can't remember ..." doesn't count.
- **The nightly save refuses to run in a clone of the template, or inside
  another git folder**, and a session start tells you why: straight away if
  it has never once worked, otherwise once it has gone two days. In a clone
  it would put your notes into the template's own history; inside a bigger
  git folder it would save all of that folder too. It also stops when git
  doesn't know your name and email, and leaves out files over 50 MB.

File by file:

- **`CLAUDE.md`:** three sentences, nothing else moved. Under "What runs on
  its own", the "When I say something lasting" bullet gains a last sentence
  after "never ask twice about the same thing.": "If I said "remember" or
  "from now on", that was the asking: save it and say where." The "Before
  any file is written" bullet gains a last sentence after "changing what was
  claimed or chosen is not.": "Replacing a whole one, or rewriting one with
  a shell command, asks me first." Under "Working rules", the first bullet
  now starts "**Throwing it away is the normal answer** for things I want to
  use." (the bold stops after "answer"); the rest of it is unchanged. If you
  edited yours, add these by hand.
- **`.gitignore`:** five new lines right after `*_rsa`: `id_dsa`,
  `id_ecdsa`, `id_ed25519`, `.netrc`, `*.kdbx`. Then, before
  "# Hook scratch", a blank line and a new block: the comment "# What a code
  project in work/ builds or downloads for itself. Big, made again / # from
  the project's own files, and nothing of yours is in it." and the lines
  `node_modules/`, `.venv/`, `venv/`, `__pycache__/`.
- **`notes/where-i-learn/who-to-trust.md`:** no `TO FILL` left in it. The
  top table's `| TO FILL | TO FILL | no record yet |` row is gone: the table
  is only its header, then a blank line and `Nothing yet.` The comment under
  the table gains a last sentence: "A new row's Record starts as "no record
  yet"." Under "On the list, no record yet", the paragraph loses its opening
  `TO FILL — ` and now starts "Add them here the day you start following
  them", with a new `Nothing yet.` line after it. In the copy-me comment at
  the bottom, the first line now reads "Template — copy this for each one
  you get a record on. The heading is the source's short name: use it word
  for word in a write-up's From: line and in notes/thrown-away.md, because
  the record is counted by it."; the heading is `### Short name` instead of
  `### Channel name — @handle`; and the line under it starts "@handle or
  link." If you filled yours in, keep your rows and sections and carry over
  only the comment wording.
- **`README.md`:** "Start" is rewritten for someone who has never opened
  Terminal. It says you need a paid Claude plan and how to open Terminal
  (Mac: Cmd+Space, type Terminal, press Return; Ubuntu: Ctrl+Alt+T), then
  six steps: download the ZIP (unchanged); `xcode-select --install`, click
  Install, wait (Ubuntu: `sudo apt install git curl`); install Claude Code
  with `curl -fsSL https://claude.ai/install.sh | bash`, pasting the PATH
  line it gives if `~/.local/bin` isn't in PATH, then reopen Terminal (with
  a help link to `https://code.claude.com/docs/en/setup`);
  `mv ~/Downloads/os-template-main ~/os`, clicking Allow if the Mac asks
  about Downloads; `cd ~/os && claude`, log in and trust the folder;
  `/setup`, seven questions, about fifteen minutes (unchanged). The
  line after the steps now reads "Next time, just step 5. Used `git clone`
  instead? Run `rm -rf .git` first, or your notes go into the template's own
  history." The line under the commands is now "Everything else:
  `.claude/README.md`." Nothing else changed.
- **`notes/README.md`:** one sentence. "or just share something and you'll
  be asked which subject it belongs in" is now "or just share something and
  it gets filed under the subject that fits, or a new one."
- **`.claude/settings.json`:** four changes. A new first line inside the top
  brace: `"autoMemoryEnabled": false,`. The `PreToolUse` entry that runs
  `protect-the-record.sh` has `"matcher": "Write|Edit|Bash"` instead of
  `"Write|Edit"` (the `no-second-copy.sh` entry is unchanged). In
  `permissions.allow`, the three lines `Bash(git log:*)`, `Bash(git diff:*)`
  and `Bash(git show:*)` are replaced by one,
  `Bash(bash .claude/scripts/history.sh:*)`. After `allow`, two new lists:
  `"ask": ["Bash(awk *> \"*)", "Bash(awk *>\"*)"]`, so an awk command that
  writes to a file asks first, and `"deny": ["Bash(yt-dlp *--exec*)",
  "Bash(yt-dlp *--netrc-cmd*)", "Bash(yt-dlp *--print-to-file*)"]`, so the
  allowed `yt-dlp` can't run other commands or write files. Keep your own
  permissions, your `env` settings and any hook you turned off.
- **`.claude/hooks/plain-words.tsv`:** two lines taken out, `granular` and
  `delta`. Both are ordinary words about rock, rivers and more. Keep every
  line you added.
- **`.claude/hooks/declined.tsv`:** not changed.
- **`.claude/skills/setup/SKILL.md`:** five changes.
  (1) Under "Before you ask anything", the two checks swap places. The
  `me/setup-answers.md` check comes first, now says "before any other check",
  and gains a last sentence: if it answers every question, setup stopped
  while writing the files, so go straight to writing them. The "has setup
  already run" check comes second, starts "If it isn't there", and says setup
  hasn't run only if `me/who-i-am.md` and `CLAUDE.md` both still have
  `TO FILL` in them (the same test the folder check uses).
  (2) In question 3, each project also gets "the one next thing to do";
  "Four things about three projects is twelve answers ... ten minutes" is
  now "Five things ... fifteen answers ... fifteen minutes"; and "Past three"
  is now "Past three that they mean to finish (don't count the no-end ones)".
  (3) The machine check: the block now tests for a Mac without Apple's
  developer tools, prints `developer tools: missing` there instead of running
  `git config`, and in the `for t in yt-dlp gh node python3` loop treats a
  tool found only at `/usr/bin/<tool>` on such a Mac as not installed (that
  is the stand-in `python3`). Two new paragraphs after the `yt-dlp` one: on
  `developer tools: missing`, write `developer tools missing — run
  xcode-select --install` into `me/my-setup.md`, say it in the one-line
  report, and leave git's name and email until the tools are in; if a
  `git config` line printed nothing, give them the two
  `git config --global user.name` / `user.email` lines to run. The "look at
  where the folder is" paragraph now applies only on a Mac (`sw_vers`
  printed a version).
  (4) In the `who-to-trust.md` bullet under "Then write the files", a new
  sentence before "**Do not write a single word about how good any of them
  is.**": "Delete a "Nothing yet." line once something goes in its place;
  with nothing to add, leave it as it is."
  (5) A new section, "## Then start a history", just before "## Then
  finish": once `me/setup-answers.md` is gone, run
  `bash .claude/scripts/history.sh start "Their Name"` without asking, and
  add one line to the finish depending on what it printed: `history:
  started`, `history: the template's own` (give them
  `rm -rf .git && bash .claude/scripts/history.sh start`, don't run it),
  `history: couldn't start` (say so, with the reason), anything else says
  nothing.
- **`.claude/skills/wrapup/SKILL.md`:** step 3, "Save it", now runs
  `bash .claude/scripts/history.sh check` first instead of looking for a git
  folder itself. On `save: changes` it offers the same
  `git add -A && git commit` as before. On `save: the template's own` it
  doesn't save, and gives the start-fresh command without running it.
  Anything else skips the step; with `no history of its own`, a save would
  go into a bigger folder around this one.
- **`.claude/skills/catch-me-up/SKILL.md`:** in the command block, the
  `git log --oneline --since="7 days ago" ...` line is replaced by
  `bash .claude/scripts/history.sh moved`. New lines after the
  `thrown-away.md` one compare `# version` in `.claude/shipped.tsv` with the
  newest release at
  `https://api.github.com/repos/zidery333/os-template/releases/latest`
  (`curl --max-time 5`, silent on any failure) and print
  `newer version out: <version>` only when that release is newer. The
  `os_rot` comment says "first three", not "worst three". In what to write:
  "Needs you" adds one line when a newer version is out ("A newer version of
  this folder is out — /update-os brings it in."); "Projects" flags one with
  an end that hasn't moved in three months (was a month), and never one
  whose brief says `**This one has no end.**`; "Half-finished" now asks, one
  line each, whether to add what it taught or throw it away, instead of
  calling them throw-aways; "Changed" leaves out the `Set up` save instead of
  the first week's commits.
- **`.claude/skills/save-to-my-os/SKILL.md`:** the description ends "then
  waits for a yes, unless they already said 'remember' or 'from now on'."
  Step 4 opens with a new paragraph: "remember ..." or "from now on ..." is
  the yes, so save it now (step 6) and say in one line which file it went
  in. The offer line is now `Want me to save that to <the file from the
  table in step 2>?` instead of naming `me/who-i-am.md`. "Never save without
  asking." is now "Never save a fact they only stated without asking."
  Step 6: the sureness marks follow the file: `(said)`, `(guessed)`,
  `(unsure)` in `who-i-am.md`; `(checked)`, `(guessed)` in `my-setup.md`;
  one of the four sureness words from `notes/README.md` in `notes/`.
- **`.claude/skills/update-os/SKILL.md`:** a new paragraph at the top: if
  `.claude/.upgrade/new/` has `.new` files, an upgrade wasn't finished, so
  read `.claude/.upgrade/report.txt` and go straight to step 4. Step 1
  offers to commit only when the folder is its own git folder
  (`git rev-parse --show-toplevel` prints this folder). Step 3: if the report
  lists "Couldn't write these", say which and don't call it done. Step 4: a
  new paragraph for files listed as "New in the template, but you already
  have your own file with this name": don't merge, offer to install the
  template's one under another name, such as `wrapup-template` (a skill's
  folder and its `name:` both change). In the rules, `settings.json` also
  keeps their `env` settings; a new rule says `plain-words.tsv` and
  `declined.tsv` are their lists: keep every line they added, apply the lines
  the template added or took out, header included, never take them whole;
  and "take the new one whole" now covers only `.sh` files. Step 5
  adds that the next upgrade won't start while `.claude/.upgrade/` is there.
- **`.claude/skills/snag/SKILL.md`:** the heading over the list is now
  `Snags from my OS folder — version <v>`, with `<v>` taken from
  `# version` in `.claude/shipped.tsv`, or `unknown`.
- **`.claude/skills/tidy-up/SKILL.md`:** the line after the `os_rot` block
  says a session start shows the first three, and `/catch-me-up` shows the
  same full list.
- **`.claude/skills/learn/SKILL.md`:** the "A file path" row now says "Read
  it. Word and PowerPoint files need a command first."
- **`.claude/skills/new-skill/SKILL.md`:** "counting a queue" is now
  "counting files".
- **`.claude/skills/README.md`:** "You don't type anything — it just
  applies." is now "Most start on their own; some, like `/setup`, wait to be
  typed." The example `description:` is now "Checks writing against the
  person's rules — plain words, short sentences, answer first. Use when
  reviewing, editing, or drafting anything they will send to someone."
- **`.claude/guides/getting-the-text.md`:** YouTube: the folder is emptied
  first (`rm -rf /tmp/add && mkdir -p /tmp/add && cd /tmp/add`), and the
  clean-up reads every `/tmp/add/*.vtt` with `grep -hvE`, not only
  `*.en.vtt`. "A file path": PDFs read fine, but Word and PowerPoint files
  need their words pulled out first, with two new `unzip -p ... | awk` lines,
  or `textutil -convert txt -stdout` on a Mac.
- **`.claude/guides/blank-note.md`:** the `From:` hint says to use the short
  name word for word from `who-to-trust.md`: its section heading, or its row
  in the top table.
- **`.claude/README.md`:** a new row after "Settings", "Claude Code's own
  memory", saying it's off and how to turn it back on. The `wrapup` row adds
  "offers to save them in the folder's history"; the `update-os` row fires
  when you ask to update or whether there's a new version. In the hooks
  table: the `spot-worth-saving.sh` row adds "When you said "remember" or
  "from now on", Claude just saves it and says where."; the
  `protect-the-record.sh` row runs "Before a file is written, and before a
  command", and "Warns; never blocks." becomes "An edit gets a reminder;
  replacing the whole file, a command that rewrites one, or moving or
  deleting a decision log asks you first. Adding to the end never asks, and
  nor does deleting a write-up."; the `no-second-copy.sh` row's "Warns;
  never blocks." becomes "Turns the first try back so Claude reads it in
  time; a second try goes through." The knobs paragraph says to
  set them in the `env` block of `settings.json`, not in `hooks/lib.sh`, and
  adds `OS_SHARED_HEADINGS`. "The scripts" has a new `daily-commit.sh`
  description and a new `history.sh` item. Two small wording changes: the
  line about changing a guide says "the edit that reaches furthest", and the
  hook example is "Counting files".
- **`.claude/hooks/protect-the-record.sh`:** now also runs before shell
  commands and works out whether one rewrites, moves or deletes a record.
  Replacing a whole record, or such a command, now asks you; an edit still
  only reminds Claude.
- **`.claude/hooks/no-second-copy.sh`:** turns back the first try at a
  second write-up of the same source, so Claude reads the first one in
  time; a second try at the same file goes through. Years no longer count as
  shared figures.
- **`.claude/hooks/spot-worth-saving.sh`:** "remember" or "from now on"
  tells Claude to save and say where, with nothing added to `declined.tsv`.
  It ignores `/learn` messages, and every message while
  `me/setup-answers.md` exists. "Too many" or "shorter" count as pushback
  only when they're about the answer. Habits at their own craft go to
  `me/my-setup.md`.
- **`.claude/hooks/plain-words.sh`:** sends its note back as feedback
  instead of a block, which Claude Code showed as a hook error, and skips a
  reply that is already the rewrite instead of keeping a file per reply.
- **`.claude/hooks/keep-tidy.sh`:** in `work/` it checks only
  `projects.md` and each project's `brief.md` and `decisions.md`, not
  chapters, code or meeting notes. Its "no source" note on
  `what-i-think.md` says tastes need none, nor, in a subject with no end,
  what they saw for themselves.
- **`.claude/hooks/lib.sh`:** a new `stop_tool` the two guards use. The
  session check offers to finish a `/setup` that stopped halfway, looks for
  `TO FILL` only in `.md` files, warns on Windows, gives a Linux line for
  installing python3, names the kinds of problem beyond the first three
  instead of "more like that", and reports a nightly save that has never
  worked, with its reason (the two-day warning now gives the reason too).
  Two marked `.not-my-os` copies no longer wipe out the list on a
  Mac.
- **New: `.claude/scripts/history.sh`.** The skills run it for the folder's
  history. `start "Their Name"` (for `/setup`) makes a git folder with a
  first save called `Set up`, unless one is already there, it's a clone of
  the template, or git isn't really installed; if that fails it takes the
  half-made `.git` back out and says why. `check` (for `/wrapup`) prints
  `save: changes`, `nothing new`, `no history of its own`, `the template's
  own` or `no git`. `moved` (for `/catch-me-up`) lists this week's saves in
  `notes/`, `work/`, `me/` and `CLAUDE.md`, or with no history, the files
  changed this week, leaving out any still as shipped.
- **`.claude/scripts/daily-commit.sh`:** starts a history when there's none
  here or above; refuses inside another git folder, in a clone of the
  template, and when git has no name and email; leaves out files over 50 MB;
  leaves out more private files (ssh keys, `.netrc`, `*.kdbx`, `token.json`,
  API key and service account files); logs a project that has its own git
  folder, since its files aren't saved; writes a failure's reason to
  `.claude/.state/daily-commit.last-failure` for the session check; and
  handles names with accents.
- **`.claude/scripts/upgrade.sh`:** a template file that shares a name with
  one you made is put beside yours in `.claude/.upgrade/new/`, listed apart,
  and not offered again until the template's one changes. A file it couldn't
  write is listed under "Couldn't write these", tried again next time, and
  the run ends as failed. An unfinished upgrade stops the next one with a
  clearer message. It uses python3 when `unzip` is missing, refuses when a
  download's copy is run from inside your folder, rejects unknown options,
  and the report names the folder.
- **`.claude/tests/run.sh`:** checks for all of the above, and a new part,
  "Getting the words out", for the YouTube and Word or PowerPoint commands.
  A check that needs a tool this computer doesn't have is skipped and says
  why. 349 checks now.

## 2026-09-27.3 — /setup saves each answer as it goes

- **`.claude/skills/setup/SKILL.md`:** three changes, nothing else moved.
  Under "Before you ask anything", a new last paragraph: if
  `me/setup-answers.md` exists, an earlier setup stopped halfway, so read it,
  say how far it got, and carry on from the first unanswered question. Under
  "Rules for the interview", a new rule before the last one: write each
  answer to `me/setup-answers.md` under its question number before asking
  the next, and stop if the write fails. The last rule now says answers are
  already going into that file. Under "Then write the files", the opening
  line now says to write everything from `me/setup-answers.md`, check every
  answer landed somewhere, then delete that file.
- **`me/setup-answers.md` is yours.** The template never ships one. It only
  exists while a setup is running or stopped halfway.

## 2026-09-27.2 — /snag and a one-line contact part

- **New: `.claude/skills/snag/SKILL.md`.** `/snag <what broke>` writes down
  something wrong with the folder's machinery in `.claude/snags.md`: first
  seen, last seen, how many times, one sentence. The same snag again bumps its
  count instead of adding a line. Claude also writes one on its own when the
  machinery gets in its way, and never puts your own content in it. `/snag` on
  its own shows the list, most repeated first, ready to send.
- **`.claude/snags.md` is yours.** The template never ships one, and an
  upgrade never reads, replaces or mentions it.
- **`.claude/scripts/upgrade.sh`:** in `shipped_files()`, added
  `! -path './.claude/snags.md'` after `! -path './.claude/shipped.tsv'`, so
  the list can never end up in `shipped.tsv`.
- **`.claude/hooks/spot-worth-saving.sh`:** right after the `LOWER=` line,
  added `case "$LOWER" in /snag*) exit 0 ;; esac` with a two-line comment, so
  a `/snag` message isn't offered for saving as a fact about you.
- **`.claude/tests/run.sh`:** new part, "The snag list", before "Odd names,
  odd machines": the list stays out of the record, survives an upgrade, and
  no check nags about it. 160 checks now.
- **`.claude/README.md`:** a `snags.md` row in the first table, after
  `CHANGES.md`, and a `snag` row in the skills table, after `update-os`.
- **`CLAUDE.md`:** one new last bullet under "Working rules": "**Log snags.**
  When this folder's machinery gets in your way, write it down with `/snag`
  without asking. Never about my own work." If you edited yours, add that line
  by hand.
- **`README.md`:** `/snag` added as the last line of the Commands block. The
  "Make it better" part is one line now: "Broken thing or better idea? Send
  your `/snag` list to **zidery333** on Discord." Nothing else changed.

## 2026-09-27 — a much shorter README

- **`README.md`:** cut from about 1,900 words to about 280. It keeps the
  install steps, the four rooms, five commands, two rules and what's proven,
  and points to `.claude/README.md` for everything else. New part, "Make it
  better": how to reach the author on Discord with fixes and ideas. Nothing
  else changed; if you edited your own README, keep yours.

## 2026-09-26.6 — the home folder is gone again

- **Removed: `.claude/home/`.** Its one real user found it didn't fit how
  they work: it looked after folders but couldn't work in them. If you had it,
  `/update-os` moves it to `.claude/.upgrade/removed/` rather than deleting it,
  and any home folder you already copied out keeps working on its own.
- **`README.md`:** the "Got more than one OS folder?" part is gone.
- **`.claude/README.md`:** the table no longer lists `home/`.
- **`.claude/tests/run.sh`:** no longer runs the home folder's checks.

## 2026-09-26.5 — the home folder can set up a new OS folder

- **`.claude/home/home`:** new command, `./home new <path> "what it's for"`.
  It downloads a fresh copy of the template, starts git in it with a first
  commit, and adds it to the list. It never overwrites anything, refuses to
  put one OS folder inside another, and refuses a copy someone filled in.
  Also fixed: status now finds projects inside category folders and skips
  folders a folder's own settings ignore; sync says when a folder has gone
  instead of listing every file as new. A purpose with a line break no
  longer breaks `folders.tsv`, and stray `*` marks are gone from status.
- **`.claude/home/README.md`:** says how to use `./home new`, that a shared
  skill should be changed in `master/`, not in a folder, and exactly what
  sync writes in each kind of folder.
- **`.claude/home/CLAUDE.md`, `.claude/home/master/README.md`, `README.md`:** mention it.
- **`.claude/home/tests/run.sh`:** checks for all of the above.

## 2026-09-26.4 — an optional home folder for people with several OS folders

- **New: `.claude/home/`.** An optional home folder: copy it out beside your OS
  folders and it shows what's on the go in each (`./home status`), runs their
  checks (`./home check`), and sends shared skills to all of them
  (`./home sync`, a dry run until you add `--apply`). It never writes into
  me/, notes/ or work/, never deletes, and refuses when a folder isn't in git,
  has unsaved changes to a file it would write, or has something running in it.
  `.claude/home/README.md` says how to set it up.
- **`.claude/tests/run.sh`:** also runs the home folder's own checks, and
  checks its scripts can be run.
- **`README.md`:** new part, "Got more than one OS folder?", before "Keeping it safe".
- **`.claude/README.md`:** the table lists `home/`.

## 2026-09-26.3 — /wrapup, and setup stops guessing

- **New: `.claude/skills/wrapup/SKILL.md`.** Say you're done and it makes sure
  each brief and decision log is true about today, offers to commit, and says
  whether it's safe to close. It writes no session log.
- **`CLAUDE.md`, "What runs on its own", last paragraph:** now says no session
  log is written, and that `/wrapup` only makes the folder's files true.
- **`.claude/skills/setup/SKILL.md`:** it no longer guesses a choice that is
  still open (which blockchain, which language) into `who-to-trust.md`; it
  names it as the thing to decide. Two answer numbers left over from when
  there were eight questions are fixed.
- **`.claude/hooks/spot-worth-saving.sh`:** text you paste in is no longer
  read as you pushing back, and a long message is never taken as pushback.
- **`README.md` and `.claude/README.md`:** list `/wrapup`.

## 2026-09-26.2 — /update-os fetches the newest version itself

- **`.claude/scripts/upgrade.sh`:** with no folder named, it downloads the
  newest version from the template's GitHub page (the `TEMPLATE_HOME` line
  near the top) and uses that. Naming a folder still works.
- **`.claude/skills/update-os/SKILL.md`:** no longer asks where the new
  version is. It previews, asks, runs, then helps merge.
- **`README.md`, "Getting a newer version":** just type `/update-os`. And how
  to hear about new versions: Watch, then Custom, then Releases.
- **`.claude/README.md`, "The scripts":** says where `upgrade.sh` gets new
  versions from.
- **`.claude/tests/run.sh`:** three checks for the download.

## 2026-09-26 — first public version

Everything is new.
