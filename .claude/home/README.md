# A home for several OS folders

Most people need one OS folder. Some end up with a few — one for the day job,
one for a side business, one for home life. After a while they drift apart.
You fix a skill in one folder, and the others never hear about it.

A home folder fixes that. It's one small extra folder that sits beside your OS
folders and looks after all of them. It does five things:

```
./home status   what's on the go in every folder, on one screen
./home check    runs each folder's own checks. Never changes anything.
./home sync     shows what would change if your shared skills went out
./home add      puts a folder you already have on the list
./home new      sets up a brand-new OS folder and puts it on the list
```

It's optional. With one OS folder you don't need it.

## Setting it up

**1. Copy it out.** In Terminal, inside your OS folder:

```bash
cp -R .claude/home ~/os-home
```

**2. Keep it beside your folders, never above them.** Claude reads the
`CLAUDE.md` of every folder above the one you're in. A home folder above your
OS folders would push its instructions into every chat in all of them.
`~/os-home` next to `~/os` and `~/os-work` is right. It refuses to take on a
folder it sits inside, or one that sits inside it.

**3. Tell it about your folders.**

```bash
cd ~/os-home
./home add ~/os "home life"
./home add ~/os-work "the day job"
./home status
```

**Want another OS folder?** Let the home make it:

```bash
./home new ~/os-work "the day job"
```

It downloads a fresh copy of the template, puts it at `~/os-work`, starts git
in it with a first commit, and adds it to the list. Then open it in Claude
Code and type `/setup`, the same as your first one. It never overwrites
anything: the spot has to be empty. And it refuses to put a folder inside
another OS folder, for the same reason the home sits beside them. No internet?
Point it at a blank copy you already have: `--from ~/Downloads/os-template-main`.

**4. Put the skills you want everywhere in `master/template/skills/`.** Copy
the whole skill folder in — one you made with `/new-skill`, say. A skill that
isn't there is never touched, so the ones only one folder needs stay put.
Change a shared skill here, not in a folder: the next sync replaces each
folder's copy with this one. Git still has the old one, if you want it back.

## Sending skills out

`./home sync` on its own writes nothing. It lists every file it would add or
change, in every folder. Read it. When it looks right, send it to one folder:

```bash
./home sync os-work --apply
```

One folder at a time, on purpose. Before writing anything it stops if:

- the folder isn't in git, so it couldn't be undone. "Keeping it safe" in the
  main README shows how, once.
- a file it would write has changes you haven't committed. Your edit would be lost.
- anything is running in that folder — a Claude chat, a script, a timer.

Then it writes the files and commits just those. It prints the one command
that undoes it: `git -C <folder> revert <number>`.

Newer versions of the template still come through `/update-os`, in each
folder. A shared skill the template also ships counts as one you changed, so
`/update-os` keeps yours and puts the new version beside it.

## What it never does

- It never writes into anyone's `me/`, `notes/` or `work/`. In a folder made
  from this template, sync writes only in `.claude/skills/`.
- It never deletes anything.
- It never starts a job in another folder. `check` runs each folder's own
  checks, which change nothing.
- It has no rules of its own. Each folder keeps its own `CLAUDE.md`.

## Checking it works

```bash
bash tests/run.sh
```

It builds pretend folders in a temp directory and tries every refusal above.
A few seconds. The last line should say "0 failed".

Folders that run the newer `./os` program work too. Most people won't have
any. For those, sync also hands out the program itself — `os` and its files
in `.os/` — from `master/new/`. It never touches their settings, word list or
hooks. `./home new <path> --as new` makes one from `master/new/` plus
`master/starter/`, the first files a new one needs. Once `master/starter/`
exists, `./home new` makes that kind unless you add `--as template`.
