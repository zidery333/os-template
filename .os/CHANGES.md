# What's new

Newest first. Each release says, in plain words, what you'll notice.
`./os update` shows you the ones that are new to you.

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
