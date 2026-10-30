# Image and video prompts

Read this when the prompt is for a picture or a clip. Everything here was true
in October 2026, and these tools change every few months. If you can search,
check the tool's own prompt guide before trusting a detail below.

## Writing one

1. **Know the tool, and still or moving.** The way you write it changes per
   tool. Midjourney settings pasted into ChatGPT are just noise. Don't know
   the tool? Ask.

2. **Describe the picture, don't brief a person.** No "you are", no rules.
   - A picture: subject, setting, style or medium, framing, light, mood.
   - A clip: subject, action, scene, camera, style. Sound in its own sentence.
   - Every word may get drawn. "Surf school website" can put surfers in the
     shot; "brand advert" can add a fake logo. Turn the purpose into how it
     should look ("calm, wide, empty beach, room for text on the left").

   Things to keep out go where the tool reads them: `--no text, logo` in
   Midjourney, the negative box in Veo, or plain words in the scene ("an empty
   beach") elsewhere. Not in your note to the person.

3. **Specific beats vague.** "1970s cartoon with cel shading", not "cartoon".
   "Jaw set, breath short", not "tense": a mood word with nothing behind it
   gives a flat result.

4. **What matters most goes first.** A word that keeps being ignored should
   move to the front. In a busy scene, describe left, right and foreground
   separately.

5. **A clip gets one action and one camera move.** "She walks in, sits down,
   then laughs" in one short clip comes out muddled. One clip per moment.

6. **Starting from a picture they already have?**
   - To change it, write an edit, never "create an image of" (that redraws
     the thing they wanted kept). "Edit this photo. Change only: … Keep
     exactly as it is: …" Think about what the change does to the rest: new
     surroundings show through glass and change shadows, so name those too.
   - To make it move, describe only the motion. Describing the picture again
     confuses it. Say "the woman", not her description.

7. **Their brand colours, fonts or names go in as they gave them.** Don't
   swap "navy" for a made-up colour code.

8. **The tool's settings go last** (see the table). No blanks in a picture
   prompt: pasted unfilled, `[colour]` ruins it. Where they gave no detail,
   pick a style choice yourself and tell them it was your pick and what to
   swap. Facts about them (their product, their brand) are still asked, not
   picked.

## How the tools differ

| Tool | Write it as | Leaving things out | Words in the picture |
| --- | --- | --- | --- |
| Midjourney (V8) | Plain sentences, then settings at the end: `--ar 16:9` for the shape, `--raw` for a photo look and closer following | `--no thing` at the end; weaker than it looks | Put the words in quotes. Long text can still drop words |
| ChatGPT images (GPT Image) | Plain sentences, or labelled parts. For a real-photo look, ask for natural flaws | Say it straight: "no watermark, no extra text" | Quote the words, add "appears once", spell odd names letter by letter |
| Gemini images (Nano Banana) | Sentences. A new picture: start with "Create an image of", or it may answer in text. Changing their photo: start with "Edit this photo" (see below) | Describe what you want: "an empty street", not "no cars" | Get the text written first, then ask for the image |
| Veo (Google video) | Subject, action, scene, camera, style; sound in separate sentences | Its own negative box, as a list of nouns. Never "no" or "don't" in the prompt | Speech goes after a colon, never in quotes, or it shows up as subtitles |
| Kling (video) | Subject, movement, scene, then camera and light. "Shot 1:", "Shot 2:" for more than one | Not in its guide; describe what you want | Name any on-screen text. Clips run 3 to 15 seconds |

Google's Imagen has been shut down; use Gemini images instead. Veo clips run 4,
6 or 8 seconds through Google's developer tools; the Gemini app may only make
8 and may have no negative box, so for app users, describe what you want and
trim the clip afterwards. Only name a setting you know their app has.

## What goes wrong

- **Too loose.** A different subject, camera and setting every time it runs.
  Pin down the subject, the light and the camera.
- **"No X" in the prompt.** Many tools read "no cars" as "cars". Use the
  tool's negative setting, or describe the scene you want instead.
  Midjourney's `--no` is the exception.
- **Too much happening in one clip.** One moment per clip; join clips after.
- **Counting and physics.** "Ten puppies" or a bouncing ball often goes wrong.
  Avoid exact numbers.
- **Camera words are requests, not settings.** "Low angle" can drift on a
  rerun. Name one move and expect to run it again.
- **Camera specs aren't physics.** "50mm lens" or a film stock gives a look,
  not an exact simulation.
- **Edits drift.** When changing a picture, say "change only X, keep
  everything else", and repeat the keep-list on every edit.
- **Midjourney ignoring the prompt.** Add `--raw` or lower `--stylize` (try
  50) before rewriting the words.

## Fixing a bad picture or clip

Name exactly what's wrong: the subject, the framing, the style, the text or
the motion. Change one thing and run it again. Changing five things at once
teaches nothing. For words that come out wrong, shorten the text and spell it
out. To learn which words a tool responds to, give a picture you like to
Midjourney's describe feature and borrow its words.

## Example

Before: `a cool picture of a perfume bottle`

After (Midjourney):
```
moody product photo of a square glass perfume bottle on wet black slate, a
single soft light from the left, deep shadows, drops of water on the glass,
dark green and amber tones, shallow depth of field, high-end editorial photo --ar 4:5 --raw --no text, label, logo
```

Swap the colours for the brand's. If the bottle looks fake, cut "high-end
editorial photo" first.

Keep the moves (what's in the shot, the light, the colours, the settings at
the end, what to keep out), not this example's words or length. Other tools
want it written their own way: see the table.
