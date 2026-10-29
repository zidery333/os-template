# What's new

Newest first. Each release says, in plain words, what you'll notice.
`./os update` shows you the ones that are new to you.

## 2026-10-29.1

- `./os find --json` now ends with exit code 1 when nothing is found, the
  same as plain `./os find`. It still prints `[]` first. A script that
  stops on any error should treat 1 as "nothing found".
- `./os learn --list` shows roughly when each video went up, like
  "about 2024-10", for a search or a channel.
- `./os setup` no longer wipes the date of your last tidy.
- `./os checkpoint` now saves in a git history you set up yourself. It
  waits if git is partway through a merge or rebase, and leaves out
  Work/Content.
- `./os setup --bring` keeps any old folder's CLAUDE.md in Archive. Before,
  only folders laid out the old me/notes/work way kept it.
- `./os update --undo` puts back the version you had before the last
  update. It stops if you changed one of those files since, and says
  which. Works for updates made from this version on.
- If the program file goes missing, `./os` puts it back by itself and
  says where from.
- `./os note <name>` turns a piece of work back into a note, every word
  kept. `./os undo` takes it back.
- `./os new ... --dry-run` says where it would go and what it might
  duplicate, and writes nothing.
- Fewer things written that you never said:
  - Saved work only gets a next action when your words say what to do.
    A description like "the roof is leaking" leaves it empty.
  - A new skill or helper no longer starts with a blank "Use when <…>"
    an AI could read as real. `./os check` names any blank left from before.
  - The skills and helpers ask, or leave a line empty, rather than fill in
    a next action, how often, a blocker, a reason, a disagreement, or
    a rule from an old folder you never confirmed.

## 2026-10-28.3

- A handoff writes only what you said or what's already in the folder.
  It no longer fills in things like "nothing has been sent yet", adds a
  decision for the handover, or puts a "no step yet" box in Next action.
- Saving something no longer adds a guess about where it came from.
- A new piece of work no longer starts with an empty bullet under Open
  questions.
- A note headed "Book notes:", "Trip notes:" or "Film review:" stays a
  note. One whose words after the heading are a job, like "Meeting notes:
  send the deck by Friday", is still work.
- `./os name "Sam"` writes "Call me Sam" into your About me note. A new
  name takes the old one's place.
- Setting the name keeps letters like "—" as they are in .os/config.json.
- Putting part of something away, or bringing it back, no longer changes
  what its Log and Decisions say. Those lines stay as they were written.
- A name made only of symbols, like 🎉🎉, keeps its own name. Before, it
  could be mixed up with something called Untitled, and closing one closed
  the other.
- Tidying a folder whose name has a dot in it, like "v2.0 Launch", keeps
  the whole name.
- A long sentence saves on a Mac's own Python. It used to stop with "File
  name too long".
- Writing out your snags again keeps the earlier file when it holds ones
  you cleared.
- Your About me note only takes what you said yourself. "I prefer the
  plumber's second quote" is filed with your other notes, not in About me.
- Decisions no longer get a "rules out" part you never said.
- Closing something no longer asks you to fix its own "origin" line by hand.
- "buy milk", "book the dentist" and other short jobs are filed as work.
- Bringing something back out of the archive says so in its Log. Bringing
  back one folder beside a newer one says the name it landed under.
- Tidying no longer marks old notes as changed today, and no longer gives
  them the subject "unsorted".
- `./os find --kind work` works, and a kind or folder that doesn't exist
  says so.
- Updates keep the newest few copies from before, and clear older ones
  themselves. An update that stops says so once.
- A broken list of snags is kept aside instead of being written over.
- The weekly tidy, setup, save, handoff and wrap-up instructions match what
  the folder actually does, and never write things you didn't say.
- A title or tag like "No", "On" or "3.10" stays as you wrote it.
- Links in the list to names with # or % in them work.
- `./os find -- "("` searches for what comes after the `--`.
- `./os save .` says it's a folder. `./os new` lists every kind.
  `./os decide` on something put away says it's in the archive.
- `./os completion fish` says there's only zsh and bash. Renaming something
  to the name it has says nothing changed.
- `./os help` counts right, and lists `learn`, `snag` and learning notes.
- Two skills no longer answer to the same words: "what should I work on"
  is catch-up, "save my progress" is wrap-up, a rule for one piece of work
  is a decision, and learning a craft is /learn.
- Setup asks before it cuts the folder's link to the template's GitHub page.
- For anything that runs `.os/upgrade.py` itself: it exits 3, not 1, when it
  stopped partway and said so. `./os save --json` gives `added_to: null` and
  `already: true` when nothing was added. `./os last --json` gives each
  thing's `id`, as find does; `name` is still there. `./os help new` says
  `helper` where it said `agent`; both still work.


## 2026-10-28.2

- `/learn` searches for a subject's official page instead of guessing its
  address. If the page won't open, the note says it couldn't get in and
  which link it tried, rather than claiming there isn't one.

## 2026-10-28.1

- `/learn` takes a subject this folder has never heard of, like birds. It
  used to stop there. Now it makes a place for it first.
- `/learn` given a link looks for what you already have on that subject,
  not on the link, so it adds to your old note instead of starting a second.
- `./os learn` reads `youtube.com/live/…` links.
- A subject split into rival camps, like diets, stays in Work while you
  weigh them. Once you settle on one, it becomes a note.
- Something learned just because you like it no longer gets a practice
  exercise from the helper that does the long reading.
- For anything that reads `./os learn --json` or `.os/learn.py`: both now
  use the same field names. A missing yt-dlp says `fix` (was `install` from
  `./os learn`), fetched videos are under `sources` (was `results` from
  `learn.py`), and forgetting says `forgotten` (was `removed` from `learn.py`).

## 2026-10-23.1

- There's a `/setup` now. Type it in the chat in a new folder. It says in
  plain words what the folder is for, checks your computer has what it
  needs, and asks what to call you and the folder.
- It brings an old folder in, an older one of these or any folder of notes.
  Everything is copied and filed the new way. The old folder isn't changed,
  and one `./os undo` takes it all back out.
- A folder downloaded with `git clone` is no longer stuck linked to the
  template's GitHub page. `/setup` cuts the link, so the Claude app shows
  the folder under its own name.
- `/setup` spots a new folder put inside an old one, and moves it out.
  It renames the folder on disk too, as its last step.
- The GitHub page starts with what this is for, for anyone new to AI, and
  says what to do if you already have an older folder.

## 2026-10-22.1

- `./os sort` no longer moves or renames anything already filed. It only
  files what you dropped in by hand. Paths your other files point at stay
  right.
- `./os save` and `./os new` put things straight into their final folder,
  under their final name, so the path you're told first stays true.
- A crowded folder is split only when you ask: `./os sort --regroup` shows
  what would move and which files name those paths, and `--yes` does it.
  It fixes those paths in your pages and skills, and lists any code or
  settings that still name an old one. Odd words like "https" or "help"
  never name a folder.
- `./os save` leans to note. Reference tables, research, transcripts and
  long text land in Notes, not Work. Say which with `--note` or `--work`.
- Something about work you already have goes inside it, with a line in
  its Log. `--into <name>` picks the work, `--new` keeps it apart.
- "About me: …" starts the About me note if there isn't one yet.
- Names never get `!` or other marks a terminal trips on, and a saved
  folder gets a proper Title Case name. `./os check` points out old names
  with those marks, and gives the rename to type.
- `./os undo` after a save puts things back exactly as they were. Nothing
  is left behind in a hidden folder, and `./os` stops counting old
  leftovers as unfiled. `./os check --fix` clears them.
- `./os update --done` clears the files an update left for you to merge,
  once you've merged them.
- Commands that only look, like `./os tidy --json`, no longer change any
  file, so a catch-up leaves nothing to save.
- `./os find --notes` searches Notes only, for helpers kept out of Work.
- `./os close <name> --part <folder>` puts one folder inside a piece of
  work away, and `./os undo` brings it back.
- Scheduled runs can call `bash os` without a permission prompt, and the
  rules say to call `./os` on its own, never after `cd`.
- Helpers never run git commands that change anything, so several working
  at once can't sweep up each other's work.

## 2026-10-05.5

- `./os check` no longer calls a link broken when it's only an example
  shown in `backticks`, like `[name](name/brief.md)`. Real broken links
  next to one are still found.

## 2026-10-05.4

- The download page says addons hold skills and helpers. It said skills
  and notes, which was wrong.
- Nothing else you'd notice: one of the template's own checks was fixed so
  it no longer trips over ordinary words in a change note.

## 2026-10-05.3

- Addons: extra skills for one kind of work, kept apart from the template
  so you only get the ones you ask for. `./os addon` lists them, and
  `./os addon <name>` adds one. It never replaces anything: if you already
  have a skill called that, it stops and changes nothing.
- `./os update` keeps the addons you added up to date, the same careful way
  as everything else. A file you changed is kept, and the new one is put
  beside it for you to merge.
- `./os undo` takes an addon out again straight after you add it.
- The download page lists every addon, with what each one adds.

## 2026-10-05.2

- The download page says what `CLAUDE.md` and `GEMINI.md` are for: one
  line each, pointing Claude Code and Gemini CLI at the same rules in
  `AGENTS.md`. Nothing in your own folder changes.

## 2026-10-05.1

- The download page is rewritten: what it does, the four helpers, the
  skills, and steps for Windows and Linux, not only a Mac.
  Windows runs it through WSL, a small copy of Linux inside Windows. Only
  the Mac has been tried so far. Nothing in your own folder changes.

## 2026-10-04.1

- `./os close <name> done` or `./os close <name> dropped` says how
  something ended. A dated line goes in its Log, so later you can tell
  what was finished from what was given up.
- Saying "call me Sam", "from now on…", "I prefer…" or "keep answers…"
  through `./os save` adds one line to your About me note, instead of
  starting a piece of work. A new About me note says how sure each line
  is: said, guessed or unsure. Every new chat sees its first lines and
  its newest ones, so nothing you said lately gets cut off.
- `status: paused`, `on hold` or `waiting`, typed by hand, now keeps the
  work on your lists as something you keep up. `./os check` names any
  other status it can't read.
- `./os check` points out a page with the same heading twice, like two
  `## Decisions`, and a page that has grown very long above its Log.
- Words taken back with `./os undo` are mentioned once, then left in
  peace. `./os sort --forget` shows what they are, and
  `./os sort --forget --anyway` throws them away.
- Search reads all of a long note, not just its start and end. When one
  word of a search matches nothing, it offers the search with a near word.
- When your AI works without asking you first, the guard on your Log and
  Decisions now also stops a shell command like `sed -i` from rewriting
  them, and asks you.
- Smaller things: links written with `%27`, `<…>` or a title are no longer
  called broken, and a rename keeps them working; sort no longer makes a
  group folder for one note, and closing the last thing in a group takes
  the empty group away; `/learn` keeps a "Who to trust" list of whose
  advice worked; a claim made from an AI other than Claude no longer slips
  away between commands; and updating gives a hook nobody changed its new
  settings, and adds new words to a subject list you've edited without
  bringing back any you took out (from the update after this one).

## 2026-10-01.6

- `./os park` now holds something instead of putting it away, and
  `./os unpark` puts it back on the go.
- Things you put away are easier to get back. Trying to push one tells
  you how to bring it back first, and starting something new under its
  old name reminds you the old one is waiting in the archive.
- Saving a file you have already saved no longer makes a second copy.
  It tells you where the first one is.
- Saving a photo or a PDF reminds you to say in a line what it is, since
  that line is what search reads. Renaming one now changes its name in
  that line too.
- Undoing a close, and the two-minute demo, no longer leave empty
  folders behind, and the demo no longer shows up in what happened last
  time.
- Holding something gives it a place to say how often you tend to it.
  The hints after holding or pushing only ask for what is still missing,
  and when a note turns into work it says how to make it a note again.
- Smaller things: new file names end on a whole word; the first screen
  cuts its descriptions at a word; help shows every command the way you
  type it, starting with `./os`; tidy lists everything you keep up, with
  the right count; "keep that going", "most days" and "most mornings"
  now read as something you keep up; another folder like this one kept
  inside yours is left alone; and the download comes with its licence.

## 2026-10-01.5

- Saving a video no longer copies it into your folder. It stays where it
  is, and a short note in Notes says where it lives. Search finds that
  note by the video's name and by whatever you write in it. Big files
  over 100 MB are treated the same way. If you do want a copy kept inside
  the folder, your AI asks you first.
- If a video a note points at gets moved or deleted, or sits on a drive
  that isn't plugged in, the folder's check mentions it once, quietly,
  and changes nothing.
- Replies now come in plain words by default: a short answer first, then
  the details, then any questions for you. After each reply, a quick check
  looks for stuffy words like "leverage" and has them said again plainly.
  The list of words is yours to add to or trim.
- Updating keeps your own settings. A setting you switched off stays off.

## 2026-10-01.4

- Something you keep up, like a garden or a hobby, no longer fills up with
  boxes to tick. Start a save with its name and what you wrote goes under
  "Keeps coming back" on it, as a plain line. Work you are pushing on still
  gets it as the next thing to do.
- `./os tidy` now asks about things you have been keeping up but haven't
  touched in half a year: still keeping these up? It gives the line to type
  for each answer. It is only a question, so `./os` and `./os check` never
  call them stale.
- With two chats open in one folder, one chat's `./os undo` no longer takes
  back what the other one just did. It stops, says what the other chat did,
  and tells you how to take it back anyway if you really mean to.
- `./os undo` now says what it took back, like the note it removed or the
  folder it moved back.
- Lots of notes on one subject, like the garden, no longer end up in a
  folder inside a folder called the very same thing. One sort made before
  stays exactly where it is, with everything in it; new notes go beside it.

## 2026-10-01.3

- Links between your notes keep working when things move. When sort tucks
  notes into folders, or you close or rename something, the links that
  pointed at it are changed to its new place, and so are the links inside
  it. `./os undo` puts every one back. Web addresses and links that were
  already broken are left alone, and so are notes kept as sources or as a
  list of decisions.
- If you save something you have already written down in other words, like
  two write-ups of one meeting with the same people, date and figures, you
  are told, and given the line to type to look at the first one. It is
  still saved. Notes that only share a number or two, like two recipes
  with 200 g of flour, say nothing.
- Starting a learning note tells you if a note you already have mentions
  the same people or figures.
- `./os tidy` lists pairs of notes that look like the same thing written up
  twice.

## 2026-10-01.2

- A folder you make yourself stays where you put it. Before, once you had
  more than 12 notes, sort could tuck it inside a subject folder like Food.
  The notes it filed for you are still grouped as before.
- Two notes called the same thing no longer end up in different subject
  folders. The second one gets a "-2" on its name, so `./os show` always
  knows which you mean.
- If you make a work folder yourself and the page in it already has
  decisions, a log or a next action, everything stays on that one page.
  Before, a second page was made beside it, and new decisions went there.
- If you drag something into Archive yourself, `./os check` now tells you
  it can't be found there, and gives you the one line to type that puts it
  away properly.

## 2026-10-01.1

- `./os update` now tells you in plain words what changed, like this list.
- Search no longer looks for a different word without asking. If nothing
  matches, it says so and offers a near word for you to type, like
  `./os find bake`. Plurals and word endings still work: "lemons" finds your
  lemon notes.
- Searching for "winner" no longer turns up your note about winter.
- Your AI checks for a newer version when you start a chat, and tells you if
  one is out.

## 2026-09-30.1

- Clearer steps in the README for getting started, even if you've never
  opened Terminal.
- A folder you make and name yourself keeps its name, and files you drop in
  by hand keep theirs. A TextEdit note stays a TextEdit note.
- Search reads TextEdit notes, and the other notes inside a piece of work.
- The first chat asks what's on your mind, and how you like your answers.
- Home subjects (home, food, garden, health, money), and recipes are kept as
  notes, not as jobs to do.
- Your edits can be taken back too: `./os checkpoint` keeps a copy of
  everything as it is now.

## 2026-09-29.1

- The first version of this folder.
