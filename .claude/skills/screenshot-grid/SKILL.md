---
name: screenshot-grid
description: Take 1–3 screenshots at the end of a job that changed something you can see, and put them together into one labelled picture. Use when a job changed a screen, page, app, menu, theme or design, or when the user says screenshot it, show me what it looks like, take a picture, before and after, or show every theme.
argument-hint: [step name]
allowed-tools: Bash(python3 .claude/skills/screenshot-grid/make-grid.py:*), Read
---

# Screenshot grid

A job that changed something you can see isn't finished until someone can see it.
One clear picture beats a paragraph saying "it looks good now".

## Do this

1. **Pick the folder.** The name is `<today's date>-<step name>`, and the step
   name is short, lowercase, with hyphens. Where it goes depends on the folder:
   - **A folder run by `./os`** (it has `./os` and `Work/`): inside the Work item
     the job belongs to, for example
     `Work/Website/screenshots/2026-11-04-theme-picker/`. A Work item's own
     folder can be shaped freely, but a `screenshots/` at the very top gets
     flagged by `./os check`.
   - **Anywhere else** (a plain project like a website or a game): at the top,
     for example `screenshots/2026-11-04-theme-picker/`.
2. **Take 1–3 shots from the best angle.** The angle that shows the change most
   clearly, not just the first screen you land on. If the change is easier to see
   next to how it was, take a "before" shot before you start and an "after" at the
   end. Save them as `1.png`, `2.png`, `3.png`. For a web page, this saves one
   straight to a file (add `--phone` for a phone-sized shot):

   ```bash
   python3 .claude/skills/screenshot-grid/make-grid.py --shoot http://localhost:3000 screenshots/2026-11-04-theme-picker/1.png
   ```

   For an app, use the simulator's or the computer's screenshot tool, then save the file there.
3. **Put them into one picture** with labels, whenever there is more than one shot:

   ```bash
   python3 .claude/skills/screenshot-grid/make-grid.py screenshots/2026-11-04-theme-picker "Before=screenshots/2026-11-04-theme-picker/1.png" "After=screenshots/2026-11-04-theme-picker/2.png"
   ```

   It writes `grid.html` and `grid.png` into that folder. The paths after the `=`
   count from where you run the command, not from the folder. `--cols 2` sets how many go in a row. `--title "Theme picker"`
   adds a heading. If it says no browser was found, open `grid.html` in any
   browser and screenshot the whole page as `grid.png`. Any other way works too
   (Python PIL, ImageMagick): what matters is one picture, with labels.
4. **Look at the picture yourself** before you hand it over. Labels readable,
   nothing cut off, nothing blank.

## When there are variants: all of them, in one picture

If the thing comes in versions (themes, styles, colours, buildings, states like
empty, loading, error), shoot **every one** and put them all in one grid, each
labelled. Never send two of four themes. A partial set hides the one that broke.
This can take more than 3 shots: the 1–3 limit is for plain jobs.

## When the layout matters: desktop and phone side by side

For menus, pages, screens and anything that moves around on a small screen,
shoot it wide (desktop) and narrow (phone, about 390 pixels wide), and put the
two next to each other in one picture: `"Desktop=desk.png" "Phone=phone.png"`.
With variants as well, make two pictures: one desktop-and-phone, one with every
variant.

## Rules

- Screenshots of the real thing, running. Never a drawing of what it should look like.
- Shots and the combined picture live together in the same dated folder.
- Don't crop away the part that changed, and don't fake a state you didn't reach.
- Nothing you can see changed (pure code, a plan, a note)? Take no screenshot.

## Done when

Your final message ends with the full paths, combined picture first:

```
Screenshots:
- /full/path/to/Work/Website/screenshots/2026-11-04-theme-picker/grid.png
- /full/path/to/Work/Website/screenshots/2026-11-04-theme-picker/1.png
- /full/path/to/Work/Website/screenshots/2026-11-04-theme-picker/2.png
```

or, when nothing visible changed, the one line: `no screenshot: nothing visible changed`.
