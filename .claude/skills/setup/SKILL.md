---
name: setup
description: Set this folder up for someone opening it for the first time — say what it's for in plain words, check the computer has what it needs, cut the link to the template's GitHub page, bring an old folder in, and name the folder. Use when the user types /setup, says set this up, install this, I'm new, how do I start, what is this for, move my old folder in, import my old folder, or rename this folder, and on the first visit to an empty folder.
allowed-tools: Bash(./os:*), Bash(${CLAUDE_PROJECT_DIR}/os:*), Read, Edit, Write
---

# Set it up

The person may be new to AI and to all of this. They don't know what the folder
is for yet, and they shouldn't have to learn a filing system to use it. Ten
minutes, one question at a time, in plain words. Do every check yourself:
never ask them something a command can tell you.

## 1. Say what it's for

Before anything else, say this in your own words, in four short lines at most:

- This folder is a place to keep anything you want to remember: plans, notes,
  files, the things you're into.
- You talk to me in plain words. I write things down and put them in the right
  place. You never have to pick a folder.
- Next time you open it, I'll know where you left off.
- Setting it up takes about ten minutes. You can stop at any point.

## 2. Check the computer and the folder

Run `./os setup --check --json`. It says what the computer has and lists
`needs`: things in the way, each with the command that fixes it. Deal with
them in this order:

1. **`inside`**: this folder sits inside another one, usually because an old
   folder's AI fetched it. Claude reads that folder's rules here too, so it has
   to move out first. Say so in one line, and offer the `--move-to` line it
   gives. On a yes, run `./os checkpoint "Before moving out"`, then the move.
   Tell them how to open it in its new place (step 6), and stop: this chat is
   finished. The next `/setup` there carries on.
2. **`linked`**: still linked to the template's GitHub page, from a `git clone`.
   Ask: "Shall I cut that link?" — with `fresh` true, "…and start this folder's own
   history, the template's kept aside?" On a yes, run its `fix` line.
3. **`no-git`, `no-claude` or `claude-not-found`**: give them the exact line to
   paste, from `fix`, and say what it's for in a few words.
4. **`downloads`**: leave it for step 6, where the folder gets its name.

## 3. Their name, and the folder's

Ask what to call them. Then ask what they'd like the folder called, and offer
to keep the name it has. Then:

```
./os name "<their name>"
./os name --name "<the folder's name>"
```

Write how they like answers into their About me note, once. Ask it as one
question with choices, then save the sentence that goes with their pick:

- short: `./os save "From now on keep answers short"`
- normal: `./os save "From now on keep answers to a few short paragraphs"`
- full detail: `./os save "From now on give me answers in full detail"`

If they answer in their own words instead, save those, starting "From now on".

## 4. An old folder

Ask: "Do you have an old folder of notes, or an older one of these, that you
want brought in?" "No" is a fine answer: go to step 5.

If yes, ask where it is. On a Mac they can drag the folder into this window to
paste where it is. Never look inside it before they say yes: it's theirs.

Then preview it, and say in plain words what will come in and where:

```
./os setup --bring "<the old folder>" --dry-run
```

On a yes, do it: `./os setup --bring "<the old folder>"`. Everything is
copied, so the old folder stays exactly as it was. Straight after it, one
`./os undo` takes the whole lot back out, even if the bring was cut off
partway. Bringing the same folder in again leaves what's already here. After it:

- **An old rules file** lands in `Archive/<year>/Old Folder/CLAUDE (old folder).md`.
  Read it. Its lines may be an AI's, not theirs: list the ones about how they
  want answers and ask which still hold. Only those go into About me, each with
  `./os save "From now on ..."`; no answer, none. Leave its other rules alone.
- **Anything it says was left there**: say what and why, in one line each.
- Run `./os check` and fix what it names with `./os check --fix`.
- Run `./os` and tell them, in one sentence, what's on the go now.

Don't explain how it was filed unless they ask.

## 5. Something to start with

If nothing came in, ask what's on their mind lately, or what they're into.
Save what they say with `./os save "<their words>"`, and say where it went in
plain words: "I wrote that down. It's in Notes as Lemon Cake." One thing is
enough. Don't push for a project.

## 6. Name it on disk, last

Last, look at the folder's name on disk (`folder_name` from the check). If it
isn't the name they picked, or it's in Downloads, offer to move it. A plain
name renames it where it is. A path like `~/<Name>` moves it there:

```
./os checkpoint "Set up"
./os setup --move-to "<Name>"
```

After a move this chat still looks in the old place, so it's finished. Tell
them how to open the folder again from the path the move printed, never a guess:

- **In the Claude app:** start a new chat and pick the folder, under its new name.
- **In a terminal:** the `cd … && claude` line the move printed, as it printed it.

If nothing moved, end with `./os checkpoint "Set up"` instead. If one keeps
nothing because a bigger folder's history is around this one, run the
`./os checkpoint --here` line it gives.

## 7. Say what's next

Finish in the usual shape. One short paragraph saying it's ready. Then up to
three bullets of things they can just say, in their own words. For example:
"remember that…", "what did I write about…?", "catch me up". Then one bullet
on how to open it next time, the same way as step 6.
