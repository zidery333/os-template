# Zenith

**One folder for your notes, plans and files, kept in order by Claude Code.**

You talk to Claude in plain words. It writes things down, puts them in the
right place, and keeps track of what you're working on. Open it tomorrow and
it knows where you left off.

![Made for Claude Code](https://img.shields.io/badge/made%20for-Claude%20Code-d97757)
![Version](https://img.shields.io/github/v/release/zidery333/os-template?label=version&display_name=release&color=2ea44f)
![MIT licence](https://img.shields.io/badge/licence-MIT-blue)

## What it looks like

```text
  Z E N I T H
  ───────────
  ~/os

  ▍Work     3     Everything you're carrying — pushing on, or keeping level.
  ▍Notes    2     Anything you'll look up later — notes, PDFs, pictures, data.
   Archive  ·     No longer live. Still searchable.

  2 things on the go.
    Bakery stall                              ./os show bakery-stall
    Garden plan                               ./os show garden-plan
  1 thing you're just keeping level.
    Spanish lessons                           ./os show spanish-lessons
```

That's what you see when you type `./os`. Most days you won't type anything.
You just talk to Claude, and it does the filing.

## What it does

- **Writes anything down.** "Remember the plum tree gets pruned in late
  winter." It's filed straight away. There's no inbox to empty later.
- **Keeps track of your work.** Everything you're doing is either *on the go*,
  with a next step, or *kept level*, with nothing to do but keep it up. Things
  you're keeping level never nag you.
- **Finds things again.** Search forgives plurals and small typos, and offers
  a near word when nothing matches.
- **Lets you take things back.** `./os undo` reverses the last thing it did.
  Finished work goes to Archive, never the bin.
- **Remembers how you like things.** Say "keep answers short" once, and every
  new chat knows.
- **Stays up to date.** `./os update` brings the newest version and never
  touches your own work.

## Your three folders

| Folder | What goes in it |
| --- | --- |
| **Work** | Anything you're doing, or keeping up |
| **Notes** | Anything you'll want to look up later, files included |
| **Archive** | Things you've stopped carrying. Still searchable |

You never pick one. Claude does.

## Four helpers, ready to go

A helper is a second Claude sent off to do one big job on its own, so your
main chat stays clear. Ask for one by name: "get the researcher on this".

| | Helper | What it does |
| :---: | --- | --- |
| 🛠️ | **Builder** | Does the hands-on work inside one project: writes the code, drafts and files, and keeps the project's page true as it goes. |
| 🌐 | **Researcher** | Looks a subject up on the web and brings back one note, with its sources. Good before you decide anything. |
| 🔍 | **Reviewer** | Checks your work against what it's meant to be, and tells you what's wrong, worst first, with the fix. |
| 🎓 | **Student** | Studies a skill properly: finds people who do it for a living, reads or watches them in full, and brings back how they do it. |

## Ten skills, built in

Say something like this, and Claude does it the same careful way every time.

| Say | What happens |
| --- | --- |
| "Catch me up" | What's moving, what's gone quiet, what's waiting |
| "Save this" | Writes it down and files it |
| "What did I write about…?" | Finds it and pulls it together |
| "Let's go with…" | Writes the decision into the work it belongs to |
| "Teach me how the pros…" | Learns from good sources and brings back a note |
| "Hand this off" | Writes one note so someone else can pick it up |
| "Tidy up" | The weekly clean-up: gone stale, doubled up, unfiled |
| "Make this a skill" | Turns something you keep asking for into a new skill |
| "Done for today" | Writes down what happened and saves a checkpoint |
| "What can you do?" | The full menu |

## What you need

- A Mac, a Linux computer, or a Windows PC (through WSL, see below).
- A paid Claude plan: Pro, Max, Team or Enterprise. The free plan doesn't
  include Claude Code.
- About ten minutes.

## Install

Every step below is a command. Paste it into your terminal, the window you
type commands into, and press Return.

### 1. Open a terminal and get the tools

**🍎 Mac**

Press Cmd+Space, type Terminal, press Return. Then check for Python:

```bash
python3 --version
```

If a box pops up, click Install and wait. If it says Python is missing, run
`xcode-select --install`.

**🪟 Windows**

Zenith runs on Windows through WSL, a small copy of Linux that Windows can
run. Right-click the Start button and open **Terminal (Admin)**, or
**PowerShell (Admin)** on Windows 10. Then:

```powershell
wsl --install
```

Restart when it's done. Open **Ubuntu** from the Start menu, and make a
username and password when it asks. Ubuntu is your terminal from now on, for
every step below. In it:

```bash
sudo apt update && sudo apt install -y python3 curl unzip git
```

It asks for your password. Nothing shows as you type it, and that's normal.

**🐧 Linux**

Press Ctrl+Alt+T on most. On Ubuntu or Debian:

```bash
sudo apt update && sudo apt install -y python3 curl unzip git
```

Other Linux: install the same four with its own installer.

### 2. Install Claude Code

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

If it says `~/.local/bin` isn't in your PATH, paste the line it gives you.
Then close the terminal and open it again. Stuck? See
[Claude Code's setup page](https://code.claude.com/docs/en/setup).

### 3. Download Zenith

Already have a `~/os`? Then use another name, like `~/os2`, in place of
`~/os` here and below.

```bash
cd ~ && curl -fsSLO https://github.com/zidery333/os-template/archive/refs/heads/os.zip && unzip -qo os.zip && rm os.zip && mv os-template-os ~/os
```

### 4. Start it

```bash
cd ~/os && ./os
```

```bash
claude
```

Log in, say yes to trusting the folder, and just talk. Want a feel for it
first? `./os demo` is a two-minute tour.

**Next time,** open your terminal and type `cd ~/os && claude`.

If `./os` ever says permission denied, type `bash os` instead, once. On
Windows, File Explorer shows your folder under **Linux**, then Ubuntu, home,
your username, os.

## Everyday commands

You don't need these, because Claude runs them for you. They're here if you'd
rather type.

| Command | What it does |
| --- | --- |
| `./os` | Where everything stands |
| `./os save "..."` | Write something down. It's filed straight away |
| `./os find <words>` | Search everything you've saved |
| `./os show <name>` | One thing: where it stands, its next step, its history |
| `./os undo` | Take back the last thing `./os` did |
| `./os close <name>` | Put something away in Archive |
| `./os help` | Every command |

## Get the newest version

Type `./os update` inside your folder. Your work and notes are never touched.
A skill you changed yourself is kept, and the new one is put beside it for you
to merge. Settings you changed keep your changes, with any new ones added.

## Other AIs

Zenith is built for Claude Code. Other AIs that can run commands on your
computer, like Codex or Gemini CLI, should work too, but nobody has tried them
yet. A chat-only app like ChatGPT can't.

The rules every AI follows are in `AGENTS.md`. `CLAUDE.md` and `GEMINI.md` are
one line each, pointing Claude Code and Gemini CLI at it. Codex reads
`AGENTS.md` by itself.

## Something not working?

Type `./os snag "what broke"` to write it down. To send them in, type
`./os snag --export`, read the `template-feedback.md` it writes, and send it
to **zidery333** on Discord.

## What's been tested

The program passes all 321 of its own checks. Type `./os test` to run them yourself.
They show that `./os` files, finds and undoes things the way it says. They
can't show that your AI files things well, or that any of this helps you.

No stranger has used this version yet. Its checks have only been run on a
Mac: Linux and Windows should work, but nobody has tried them yet.

## Older version

Looking for the older me/ notes/ work/ layout? It's on the `main` branch.

## Licence

MIT. See [LICENSE](LICENSE).
