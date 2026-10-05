# Dana Reyes

Backend Engineer, Portland, OR

## Education

**Portland State University** — B.S. Computer Science, September 2021 to June 2025.
Coursework in distributed systems, databases, and compilers. Senior project was a
query planner visualizer used by the department's databases course.

## Experience

**Cascade Freight** — Backend Engineer, data platform. June 2023 to August 2025.

- Owned the event ingestion path from carrier APIs into the warehouse: roughly 12
  million events a day across 40 integrations, each with its own idea of what a
  timestamp is.
- Rebuilt the nightly batch import as an incremental pipeline with idempotent
  writes. The import window went from about 40 minutes to under 4, and a partial
  failure became safe to rerun instead of a manual cleanup.
- Built a Postgres-backed job scheduler in Go using advisory locks and a claim
  table, so a duplicated worker cannot run the same job twice.
- Ran a dual-write and backfill migration of a 90 million row table to a new key
  layout, verified with a row-level diff before cutover and no read downtime.
- Wrote the on-call runbooks for the six alerts that paged most often. Each one
  names the first query to run rather than describing the system in general.

**Portland State University, CS Department** — Teaching Assistant, databases.
September 2022 to June 2023. Ran lab sections and graded query optimization work.

## Projects

**Ingestion pipeline rebuild** (2024). Incremental, idempotent, reruns safely.
Python, PostgreSQL, Airflow.

**Job scheduler** (2024 to 2025). Exactly-once job execution on Postgres, with the
failure modes it does not cover written down in the README rather than discovered
later. Go, PostgreSQL.

**Zero downtime migration** (2025). Dual write, backfill, row-level verification,
cutover. PostgreSQL, Python.

**Resume chatbot** (2025 to present). This site. A grounded RAG chatbot over my own
background, with a drafter and judge pipeline so nothing unverified reaches the page.
Python, FastAPI, FAISS, Docker.

## Skills

Python, Go, FastAPI, PostgreSQL, Airflow, SQL, schema design, backfills, Docker,
CI/CD, observability, testing, Linux, distributed systems.
