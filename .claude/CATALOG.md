<!-- written by Zenith. Don't edit by hand — it gets overwritten. -->
# What this folder can do

Extras for Claude Code. Type a `/name` **in the chat** (not the terminal) to run a skill. Helpers get sent off on their own when a job suits them. None of this is required — `./os help` is the plain version, and it works in any terminal with or without an AI.

## Skills

Type these in the chat, or just ask for them in your own words.

| | What it does |
| --- | --- |
| `/catchup` | Catch up on everything in this folder — what's moving, what's gone quiet, what's waiting, what's broken |
| `/commands` | Show everything you can ask for in this folder, like a bot's help menu — the skills, the helpers, and the plain things you can… |
| `/decide` | Write a settled decision into the item it belongs to, with what it rules out |
| `/find` | Find and pull together anything in this folder — notes, projects, decisions, files, past sessions |
| `/handoff` | Write the one note that lets another person or AI pick a project up cold |
| `/learn` | Learn how good practitioners actually do something, and bring back a note you can work from — from the official source and from… |
| `/make-skill` | Build a new reusable skill or helper for this folder — interview, write, check, register |
| `/save` | Write something down into this folder and file it — a thought, a link, a quote, a file, a half-formed idea |
| `/tidy` | The weekly clean-up pass — stale projects, duplicates, unfiled items, things ready to put away, and anything actually broken |
| `/wrapup` | Close out a working session — write down what happened, update the projects touched, file anything loose |

## Helpers

Picked automatically when a job is big enough to deserve its own context.

| | What it does |
| --- | --- |
| `@builder` | Does focused work inside one project — writes the code, drafts, configs and files, and keeps the project README honest as it… |
| `@researcher` | Researches a topic out on the web and brings back one well-sourced note |
| `@reviewer` | Reviews work against the standard it claims to meet — a draft, a plan, a skill, a project README, a decision — and reports… |
| `@student` | Goes and studies a subject properly — finds who actually does it for a living, reads or watches them in full, and comes back… |

## Automatic

These run on their own. You never call them.

| File | |
| --- | --- |
| `keep-the-record.sh` | Asks you first when a rewrite would lose decisions or log lines. |
| `mark-dirty.sh` | Notices when a file changed. |
| `session-start.sh` | Tells your AI where things stand, before you say anything. |
| `settle.sh` | Re-reads the folder when you stop typing, so search stays current. |

