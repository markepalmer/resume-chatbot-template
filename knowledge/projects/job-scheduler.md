# Project: Postgres-backed job scheduler

**Source: work at Cascade Freight, 2024 to 2025. Stack: Go, PostgreSQL.**

## The problem

Scheduled work ran from cron on a single box. When that box was replaced the jobs
silently stopped, and when the replacement briefly overlapped with the original,
two of them ran twice. One was a billing export, which is the kind of duplicate
that generates a phone call.

## What was built

A scheduler in Go with its state in Postgres rather than in a process.

- A `jobs` table holds the schedule. A `claims` table holds who is running what.
- A worker claims a job with a conditional insert into `claims`, guarded by a
  unique constraint on the job id and the scheduled run time. Two workers racing
  the same run means one insert succeeds and one hits the constraint and backs off.
- A Postgres advisory lock covers the scan that decides what is due, so the scan
  itself is single-flighted without a leader election protocol.
- A heartbeat column lets a claim be reclaimed if the worker dies, with the timeout
  set well above the longest observed job rather than guessed.

## Why not a queue or a framework

A message queue was the obvious answer and was rejected because the semantics we
needed were about *scheduling* rather than delivery: a queue guarantees the message
arrives, not that it was supposed to be sent at that moment, and deduplicating on
the consumer side pushes the same problem one layer down. Postgres was already in
the stack, already backed up, and already understood by everyone on call.

## What it does not do

It is exactly-once for *claiming*, not for *side effects*. A worker that claims a
job, writes to a third-party API, and then crashes before marking the job done will
have the job reclaimed and the API called again. Jobs whose side effects are not
idempotent must handle that themselves, and the README says so in the first
paragraph rather than the last.

It also does not scale past one Postgres instance, which was fine for hundreds of
jobs a minute and would not be for hundreds of thousands.
