---
name: {{SLUG}}
description: {{TITLE}}.
tools: Read, Grep, Glob, Bash
color: cyan
---
<!-- The `description:` line decides when this helper is called on, and it is
     the only thing read to decide it: make <the situation> specific.
     No `model:` line on purpose: left out, a helper runs on whatever model the
     conversation is already using, so this folder keeps working — and keeps
     getting better — as models are replaced. Name one only if this job truly
     needs a particular model. -->


Your job: {{TITLE}}.

## Your job
<!-- One paragraph. What you own, and what you explicitly don't. -->

## How you work
1. 
2. 
3. 

## Never
- 

## What you return
<!-- Only your final message comes back. Say what shape it takes:
     a ranked list, a file path, a decision with reasons. -->
