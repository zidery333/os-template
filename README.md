# Zenith

One folder for your notes, plans and files, kept in order by Claude Code.
Other AIs that can run commands on your computer, like Codex or Gemini CLI,
should work too, but nobody has tried them yet. A chat-only app like ChatGPT can't.

## Start

You need a paid Claude plan. Paste each command into Terminal (Mac: Cmd+Space,
type Terminal, press Return. Ubuntu: Ctrl+Alt+T).

1. Click the green **Code** button, then **Download ZIP**. Unzip it.
2. Mac: `python3 --version`. If a box pops up, click Install and wait. If it says
   Python is missing, type `xcode-select --install`. Ubuntu: `sudo apt install python3 curl`.
3. `curl -fsSL https://claude.ai/install.sh | bash`. If it says `~/.local/bin` isn't
   in your PATH, paste the line it gives. Reopen Terminal ([help](https://code.claude.com/docs/en/setup)).
4. Already have a `~/os`? Then use another name, like `~/os2`, in place of `~/os` here and
   below. Now: `mv ~/Downloads/os-template-os ~/os`. If your Mac asks about Downloads, click Allow.
5. `cd ~/os && ./os`, then `claude`. Log in, trust the folder, and just talk.

Next time: `cd ~/os && claude`. If `./os` ever says permission denied, type
`bash os` instead, once.

## Get the newest version

Type `./os update` inside your folder. Your work and notes are never touched. A
skill you changed yourself is kept, and the new one is put beside it for you to
merge. Settings you changed keep your changes, with any new ones added.

## Make it better

Broken thing or better idea? Type `./os snag --export`, read the
`template-feedback.md` it writes, and send it to **zidery333** on Discord.

## What's proven

The program passes all 286 of its own checks. Type `./os test` to run them yourself.
They show that `./os` files, finds and undoes things the way it says. They can't
show that your AI files things well, or that any of this helps you.

No stranger has used this version yet. Everything else is a guess.

Looking for the older me/ notes/ work/ layout? It's on the `main` branch.
