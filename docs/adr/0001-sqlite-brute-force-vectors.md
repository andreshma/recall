# 0001. SQLite with brute-force vectors and FTS5, no vector database

- Status: Accepted
- Date: 2026-10-27

## Context and problem statement

recall needs to store about 35k–112k chunks with 768-dimension vectors, their
text, their provenance and a lexical index, on a single machine. Indexing is
incremental and may be interrupted; files are added, changed and removed. Where
should vectors live?

## Decision drivers

- Adding, changing or removing a file must keep vectors and metadata consistent.
- An interrupted run must not leave the index misaligned.
- Few dependencies, installable offline.
- Query latency must fit a 1.5 s hook budget.

## Considered options

- **A. Vectors as float32 BLOBs in SQLite**, scanned in full in memory; FTS5 in the same file.
- **B. sqlite-vec extension.**
- **C. Separate memory-mapped vector file** next to SQLite.
- **D. A dedicated vector database.**

## Decision

Option **A**. Each chunk row stores its vector as a float32 BLOB; an FTS5 table
(external content, maintained by triggers) indexes the same text. The searcher
loads the matrix into memory and computes a full dot product per query. sqlite-vec
was not available in the offline package cache and its stable version is also
exhaustive; a separate memmap does not share transactions with the metadata and
needs compaction on deletes; a vector database is a server to run for a
single-user dataset this size.

## Consequences

- Positive: one file, one transaction per change; an interrupted run loses at
  most the group in progress.
- Positive: the dot product costs tens of milliseconds; embedding the query
  dominates latency.
- Negative: memory grows linearly with chunks (~0.35 GB at 112k chunks); a much
  larger library would need an approximate index.
