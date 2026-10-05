# Project: This website

**Source: personal project, 2025 to present. Stack: Python, FastAPI, FAISS, fastembed, Docker.**

## What it is

The site you are reading. A personal job application page whose centerpiece is a
grounded chatbot that answers questions about Dana's background using only the
documents in `knowledge/`, and declines when a question falls outside them.

## How it answers

```
question -> route -> retrieve -> draft -> judge -> answer
```

Retrieval is hybrid. A FAISS index over the corpus handles specific questions, and
a compact always-on summary built from the resume and the roles file is injected on
every call, so a retrieval miss cannot drop the core facts.

Two model passes. A drafter writes an answer from the retrieved context, then a
judge checks it against that same context before anything reaches the page. Only
the judge pass is streamed to the browser. Streaming the draft would reach a first
token sooner, but it would put unverified text on screen and then rewrite it, which
is the single thing the pipeline exists to prevent.

## Why a grounded chatbot rather than a chat window on an API

A general model asked about a candidate will produce a fluent answer whether or not
it knows anything, and the failure is invisible to the person reading it. Grounding
means every claim has a source in the corpus, and the honest answer to a question
the corpus does not cover is a refusal.

## What it does not do

It does not know anything that is not in `knowledge/`. It has no memory between
questions. It is not a general assistant and will decline to be used as one.

## Notes for anyone who forked this

This file, and everything else in `knowledge/`, is example content for a fictional
person. Replace it with your own before deploying, or the chatbot will cheerfully
answer recruiters' questions as Dana Reyes. `python3 scripts/setup_profile.py`
names every file that still describes the previous owner.
