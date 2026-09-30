#!/usr/bin/env bash
# Runs on every message the user sends. Watches for two things:
#
#   1. The user telling you something durable about themselves — a preference,
#      a habit, a fact about their setup.
#   2. The user pushing back on how you just answered — too long, too vague,
#      or the thing they already told you not to do.
#
# The second one matters as much as the first and is the easier one to lose.
# Nobody says "I prefer short answers". They say "why is this three
# paragraphs". Same fact. That one makes every later answer better.
#
# Pure text matching. No model call, so it costs nothing and adds no delay.
# Being text matching, it misses typos and anything worded unusually. It is a
# safety net, not the floor — notice these yourself as well.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need_python

# Read whichever field this version of Claude Code sends. The message has been
# called user_input and user_prompt at different times, and a hook that reads
# only one of them goes silent without saying so — the worst way to fail,
# because its own tests keep passing.
PROMPT=$(cat | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    for k in ("user_input", "user_prompt", "prompt", "message"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            print(v); break
except Exception: pass')

[ -n "$PROMPT" ] || exit 0

# Text they pasted in was written somewhere else — a log, an article, another
# chat. "Too long" inside it is not them pushing back on you.
PROMPT=$(printf '%s' "$PROMPT" | python3 -c 'import re,sys
sys.stdout.write(re.sub(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", " ", sys.stdin.read(), flags=re.S))')
LOWER=$(printf '%s' "$PROMPT" | tr '[:upper:]' '[:lower:]')

# A /snag is about this folder's machinery, not about them. "It always asks
# twice" there is a complaint to write down, not a habit to save. Text after
# /learn is someone else's: "I never went back" is its author talking.
case "$LOWER" in /snag*|/learn*) exit 0 ;; esac

# While /setup runs, every answer already goes into me/setup-answers.md, and
# "too much text" answers its question, not your last answer.
[ -f "$ROOT/me/setup-answers.md" ] && exit 0

# Phrases that nearly always mean "this is true about me, not just about now".
SIGNAL='remember that|remember this|note that|don.t forget|keep in mind|from now on|going forward|i prefer|i like it when|i hate|i always|i never|i usually|my setup|i use |i work with|i am working on|i.m working on|actually i|for future|next time|save this|add this to|worth keeping|good to know'

# Pushing back on the answer they just got. This never looks like a preference
# to the person saying it, which is exactly why it gets lost. "Too many" or
# "shorter" alone is just as often about tomatoes or a cover letter, and "it's
# too" about the weather, so only a word about the answer counts after it.
# ’s is spelled out: with no language set, . takes one byte of its three.
CORRECTION='too long|(^|[^a-z])(that|this|it|your (answer|reply))( was| is|.s|’s) (way )?too (long|much|many|wordy|vague|complicated|technical|detailed|dense)|too (much|many) (text|detail|words|bullets|headings)|^too (much|many)[^a-z]*$|way too|^shorter[.!]*$|(^|[^a-z])(make it|keep it|make that|you be) shorter|shorter (please|answers?)|keep it short|be brief|get to the point|just answer|just tell me|stop (doing|writing|giving|explaining)|not what i (asked|want|meant)|i thought i said|you (did|are doing) it again|no headings|no tables|no bullet|word salad|rambl|waffl|makes no sense|in plain|explain (it |this )?(again|better|simpler)|i already (said|told)|didn.t i (say|tell)'

# "Remember ..." or "from now on ..." already asks for it to be kept. Asking
# "Want me to save that?" back is a second question about the same thing.
# Only "remember" that starts a sentence counts: "I can't remember" asks nothing.
ASKED=""
printf '%s' "$LOWER" | grep -qE 'from now on|(^|[.!;:] *)((please|and|also|oh|ok|okay),? )*remember([^a-z]|$)' && ASKED=1

KIND=""
# A question is not a statement about them: "how do I use git" says nothing
# lasting, even though it contains "I use".
case "$LOWER" in
  *\?|\?*|how\ *|what\ *|why\ *|when\ *|where\ *|which\ *|can\ *|could\ *|should\ *|is\ *|are\ *|do\ *|does\ *) ;;
  *) { [ -n "$ASKED" ] || printf '%s' "$LOWER" | grep -qE "$SIGNAL"; } && KIND="fact" ;;
esac
# Pushing back is short. A long message that happens to say "too long"
# somewhere is about something else.
[ "${#LOWER}" -le 400 ] && printf '%s' "$LOWER" | grep -qE "$CORRECTION" && KIND="correction"
[ -n "$KIND" ] || exit 0

# What they have already turned down. Without this, the same question comes
# back every week and the offering becomes something to ignore.
DECLINED=""
LIST="$ROOT/.claude/hooks/declined.tsv"
if [ -f "$LIST" ]; then
  RECENT=$(grep -v '^#' "$LIST" | grep -v '^[[:space:]]*$')
  [ -n "$RECENT" ] && DECLINED="
Already turned down — do NOT offer these again. A line marked 'not-now' may be
raised again if it comes up naturally; a line marked 'no' is closed for good:
$RECENT
"
fi

YES="They asked for it to be kept, with 'remember' or 'from now on', so that is
already a yes: don't ask 'Want me to save that?' back. Answer them first, then
write it down and say in ONE short line which file it went in."

if [ "$KIND" = "correction" ]; then
  NOTICE="The user's message reads like pushback on the answer they just got —
too long, wrong shape, or something you were already told not to do.

That is a fact about how they want work delivered. It is worth as much as
anything they state outright, and this is the kind they will never state
outright.

First, fix the answer they are unhappy with. Do that before anything else, and
don't explain yourself while doing it.

Then decide: one-off, or a standing rule? A standing rule goes in the 'How I
like work delivered' section of me/who-i-am.md, in their own words, as one
line. If it's about how you work rather than about them, CLAUDE.md is the
better home.

$(if [ -n "$ASKED" ]; then echo "$YES"; else echo "Offer it in ONE short line, after the fixed answer:
  'Want me to write that down so I stop doing it?'

Never save it without asking."; fi)

Never apologise at length or list your own mistakes — that is more words,
which is usually the thing being complained about."
else
  NOTICE="The user's message matched a phrase that usually means they just
told you something lasting about themselves, their setup, or how they want work
done — not just something about this one task.

Before you answer, decide whether it belongs in a file:
  - how they want YOU to work: length, detail  -> me/who-i-am.md
  - their own habits at their own craft        -> me/my-setup.md, under 'How I do things'
  - their machine, tools, versions             -> me/my-setup.md
  - a project, its state, what is next         -> work/<project>/brief.md
  - why they chose something on a project      -> work/<project>/decisions.md
  - something they learned about a subject     -> notes/<subject>/what-i-think.md
  - nothing lasting                            -> most messages. Say nothing.

$(if [ -n "$ASKED" ]; then echo "$YES"; else echo "If it does belong somewhere, answer their actual question first, then offer in
ONE short line: 'Want me to save that to <that file>?' Do not save it
without asking, do not write a paragraph about it, and do not offer twice for
the same fact. If they say yes, write it and say only which file it went in."; fi)"
fi

# Nothing to turn down when they asked for it themselves.
[ -n "$ASKED" ] && { say_to_claude "$NOTICE" "UserPromptSubmit"; exit 0; }
say_to_claude "$NOTICE

If they say no, add a line to .claude/hooks/declined.tsv so it is never
offered again.
$DECLINED" "UserPromptSubmit"
