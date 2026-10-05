# Project: Event ingestion pipeline rebuild

**Source: work at Cascade Freight, 2024. Stack: Python, PostgreSQL, Airflow, Docker.**

## Confidentiality

This work was done for an employer, and this file is written to the same rule the
rest of this corpus follows: the things that are confidential are **not written
down here**, rather than written down with an instruction not to repeat them.

Public, and free to discuss: the architecture, the engineering decisions in this
file, and the shape of the problem.

Not published here: customer names, contract terms, per-carrier volumes, revenue
figures, and internal system names beyond the ones above. Nothing in this corpus
states these and they cannot be derived from what it does state.

## The problem

Carrier events arrived from 40 integrations and were imported by a nightly batch
job that truncated and rebuilt the destination tables. It took about 40 minutes.
Two things were wrong with it beyond the runtime. A failure halfway through left
the tables in a state that needed a human before the next run could start. And
because it was nightly, the product's freshest data was up to 24 hours old.

## What was built

An incremental pipeline. Each source is polled on its own schedule, events are
written with a natural key derived from the carrier's own identifiers, and the
write is an upsert rather than an insert, so replaying a window is harmless.

The three decisions that mattered:

- **Idempotency first, speed second.** The performance win was a consequence of
  incremental loading, not the goal. The goal was that a rerun could never make
  things worse, because that is what made the job safe to retry automatically.
- **A natural key, not a surrogate one.** Deriving the key from carrier fields
  meant a duplicate delivery of the same event collapsed onto the same row without
  a lookup. It also meant discovering that two carriers reused ids across years,
  which is why the key includes the source and the event date.
- **The clock is not trustworthy.** Timestamps arrived in at least four formats and
  two of the integrations sent local time with no offset. Normalizing at the edge,
  and recording the raw value alongside the parsed one, made a whole category of
  bug diagnosable instead of theoretical.

## Result

The import window went from roughly 40 minutes to under 4. Partial failures became
safe to rerun without intervention. Data freshness went from up to 24 hours to
about 15 minutes.

## What it does not do

It does not guarantee ordering across sources, and it does not detect a carrier
silently dropping events; a source that sends nothing looks the same as a source
with nothing to send. Both were written down at the time rather than discovered
later.
