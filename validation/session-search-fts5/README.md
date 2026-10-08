# Canonical session FTS5 performance evidence

These measurements cover PR [HKUDS/nanobot#5826](https://github.com/HKUDS/nanobot/pull/5826).

- Measured source: `26f4f19281c5f2d8e5f30f658353f3c0eb23eac9`.
- Main reference: `3865bde3feab9e3c9c5de15a85ff29eeab79328e`.
- This evidence branch adds only the benchmark, results and this document to the measured source commit. These files are outside the upstream PR diff.

Run from a checkout containing the candidate implementation:

```sh
python validation/session-search-fts5/benchmark.py \
  --base 3865bde3feab9e3c9c5de15a85ff29eeab79328e \
  --work-dir ../fts5-benchmark-data \
  --output ../fts5-results.json
```

The script creates a fresh temporary fixture directory for every run, isolates legacy session paths, uses no model API, and never modifies existing session records. It loads the unmodified main reader with `git show` and compares it with the checked-out candidate against the same real SessionManager/JSONL fixtures. The production manager change only adds a canonical path accessor; the reference reader never uses it. Synthetic titles do not match the query, and the matching session is older than all other sessions. The baseline therefore scans every body before returning one match. This measures the expensive full-history path, rather than title-only or early-result queries.

Repeated queries use three-run medians with a warm OS file cache. First index construction is separately timed once. The initial baseline warms the OS cache before that construction; “first index” is not an OS cold-cache result. A separate reference batches the existing file locks to distinguish that effect from FTS candidate filtering. Results and excerpts are asserted equal for all probes.

Environment: Windows 10, Python 3.13.6, SQLite 3.50.4.

| Canonical workload | Main match | First index + match | Warm indexed match | Main with batched locks only |
| --- | ---: | ---: | ---: | ---: |
| 200 sessions × 20 messages | 242.26 ms | 237.35 ms | 72.76 ms | 113.28 ms |
| 300 sessions × 500 messages | 1328.96 ms | 2184.12 ms | 106.28 ms | 1132.43 ms |

Warm body reads fall from 200/300 to one. New reader instances reuse the persisted cache. Warm no-match queries take 69.95/101.30 ms, compared with 235.68/1319.68 ms on main. For the large workload, FTS improves on the batched-lock-only reference by 10.66×; the overall main-to-candidate comparison is 12.50×. For the small workload, those factors are 1.56× and 3.33× respectively.

The index is additive storage: 1,413,120 bytes for 831,801 bytes of small-workload JSONL, and 40,202,240 bytes for 29,663,801 bytes of large-workload JSONL. Initial construction and changed-session reindexing cost more than a warm query. Metadata enumeration still scales with session count. Queries shorter than three case-folded characters use the canonical scan.

Local validation at the measured source: 154 tests passed, two existing Windows-dependent tests skipped; whole-tree Ruff passed; strict BasedPyright checks passed for all three changed production modules. The new tests also cover cache corruption/recovery, saves during SQLite lookup without blocking the writer, cache reuse, Unicode/CJK/literal substring behavior and custom stores. Full cross-platform CI is recorded separately on the PR's current source commit.
