# What's new

Newest first. Each release says, in plain words, what you'll notice.
`./os update` shows you the ones that are new to you.

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
