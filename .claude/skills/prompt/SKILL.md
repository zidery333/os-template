---
name: prompt
description: Write a prompt for any AI, or fix one that keeps giving bad answers — for ChatGPT, Claude, Gemini, a custom GPT, Gem or Project, custom instructions, a system prompt, a chatbot for a website, instructions for an agent like Claude Code, or an image or video tool like Midjourney, Veo or Kling. Use when someone says write me a prompt, make a prompt for, turn this into a prompt, improve my prompt, my prompt isn't working, why does my GPT or bot keep doing X, my prompt keeps making things up, custom instructions, system prompt, or prompt engineering. Not for making a skill or helper in this folder (that is /make-skill), and not for making text sound less like AI (that is /humanizer).
argument-hint: [what the prompt is for, or the prompt that isn't working]
---

# Prompt

Hand back **one** prompt, ready to paste. Not three versions to choose from.

A prompt is a brief for someone smart who has never met them and can't ask
questions: what the job is, why, who it's for, what done looks like, and
their material fenced off with tags or `---` lines. You know how to write
that. This skill is about where good prompts still go wrong, because blind
tests showed those are the only places a careful writer loses.

For an image or video tool, read `REFERENCE.md` (same folder) first.

## Before writing

**Know the tool and what it can do there.** Can it search, read files, take
actions, set a length? Write for the tool they have, not the best one: a
prompt that needs search gives nothing without it.

**Ask, or leave a blank — never guess a fact.** Their location, policy,
audience, goal, team size, brand colours: if the prompt needs it and they
didn't say it, ask (one message, two questions at most) or leave a
`[blank]`. Ask about what the answer depends on; blank the small fill-ins
they'll type each time. Don't ask what they already told you ("for Claude
Code" means you know the tool). If they can't say what they want at all,
offer to interview them first.

## While writing

- **The prompt must always produce something.** Don't tell the AI to stop
  and ask when a detail is missing: it returns a question instead of the
  work. Have it do the work with what's there, then list what's missing
  after. Stopping is right only where a guess does harm: medical, legal,
  money, a promise to a customer.
- **Give it a way out.** "If the notes don't say, write 'not in the notes'."
  Without one, it invents rather than admit a gap. For facts it can't have
  (live prices, opening hours), have it mark what it can't check and say
  where to check, rather than refuse.
- **Anything that writes to their customers,** drafts or a bot: "only
  promise what's in these facts; never say something has already been
  done".
- **A bot or GPT needs edges:** what it covers, the exact words for
  off-topic, where to send people. With files: "answer only from the files;
  if they don't cover it, say so and point to [who]". Urgent or unsafe cases
  (a medical emergency, a safety risk) get routed first, before any refusal.
  Say what the chat box can show: most website widgets need plain text, no
  bold or bullets. Send people only to contacts they gave you; a blank their
  customers might see is worse than leaving the contact out.
- **An agent** (Claude Code, a GPT with actions): what it may change, what
  it must not touch, when to stop and ask, how to check its work, what to
  report.
- **Hard limits on their own short lines.** "Under 150 words." A limit
  buried in a paragraph gets half-followed. Repeat the one most likely to
  slip as the last line.
- **Paste-ready output:** "output only the X, nothing before or after".
  Otherwise it adds notes and labels that have to be cut out by hand.
- **Leftovers get a home.** Sectioned output needs an "Also noted" line, or
  the odd item that matters gets dropped.
- **Steps of the job are fine; a script for how to think isn't.** Models that
  think on their own (most, late 2026) do worse with one.
- **Their words stay their words.** Tips, slogans, policy lines they gave go
  in exactly as written.
- **Nothing that invites invention:** "you may add general things about the
  area" or "examples like these" become a licence to make things up.
- **Missing details go after the work, never inside it.** A listing full of
  "[missing]" gaps can't be posted. Have it write around the gap, then list
  what's missing underneath.
- **Same language as they'll paste it in,** and talk to them (your questions
  too) in the language they wrote to you in. Say so if answers should come
  back in another.
- **No shouting** (CAPITALS, "CRITICAL", "you MUST"): newer models overdo
  the thing. Say it once, with the reason.

## Before handing it over

Check every one of these. Each was a real failure in testing.

1. **Every specific detail traces to something they said.** Names, places,
   numbers, goals, colours, hours, rules. If it doesn't, make it a blank, or
   name it to them as "I added this, check it". Never slip in a sensible
   extra silently ("closed Sundays", "call 999").
2. **Run it in your head on an awkward input:** empty, messy, off-topic, a
   missing detail, a customer pushing for something it mustn't give. Does it
   still produce something useful? Does the way out fire?
3. **No two rules clash, and no rule blocks the job.** "Concise" against
   "cover everything"; "only touch these five files" when the job needs
   every page changed; "no other text" when you asked for a line you didn't
   supply. Can every rule be obeyed at once? If not, fix it or say which wins.
4. **If you can start a helper** (a fresh AI that hasn't seen this chat),
   really run it on a normal and an awkward input and fix what broke.
   Otherwise say it's untested. You can't run image or video tools.

Then hand it back: one line on what it is, the prompt in a code block, then:

- **"What I filled in myself:"** every detail in the prompt they didn't give
  you, one per line (a team size, a goal, opening days, a colour). Leave the
  heading out only if there truly are none. In testing, details slipped in
  unflagged far more often than they were invented on purpose: this list is
  what catches them.
- A line or two on what to change if the first result is off.
- If there are blanks, "fill in the blanks first": pasted empty, it stalls.

## Fixing one

If they've said what went wrong, or the fault is plain in the prompt, go
ahead. Otherwise ask for one bad answer it gave. The usual causes: two rules
clash; it's too vague or missing who and why ("viral ideas for my bakery"
with no bakery details gets any bakery's ideas); no way out, or facts it
can't have; no edges on a bot; shouting; only saying "don't"; one example
copied as a template; the key rule buried in the middle. Fix the causes,
biggest first, run the checks above, then hand back the whole fixed prompt
with a short line on what changed and why.

## Also worth saying

- **Long chats drift.** For custom instructions, warn that the format slips
  after many messages, and a one-line reminder fixes it.
- **Polite questions are taken literally.** "Can you suggest changes?" gets
  suggestions, not changes.
- **A job they'll run in this folder again and again** should become a
  skill. Offer /make-skill.
