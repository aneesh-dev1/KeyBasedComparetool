# Hash engine validation — 2026-10-25

Baseline: `9c6c3cb` on `redesign/foundation-shell-files`. Measurements compare that
checkout with the adaptive hash implementation. Median of three warm-cache runs,
256 MiB comparison budget, two requested workers. Fixture generation, upload,
HTML/Excel exports and analysis indexing are excluded. Checkpoints are disabled
in this core benchmark; source fingerprint and resume validation add I/O.

Environment: Python 3.12.14; macOS-27.0.1-arm64-arm-64bit.

## Core comparison measurements

Positive change means faster; negative change means slower.

| Fixture | Requested mode | Before | After | Time reduction |
| --- | --- | ---: | ---: | ---: |
| wide_equal | auto | 0.0932 s | 0.0912 s | +2.2% |
| wide_equal | partitioned | 0.2048 s | 0.1939 s | +5.3% |
| wide_equal | sort | 0.1681 s | 0.1948 s | -15.9% |
| wide_dense | auto | 1.1378 s | 1.1345 s | +0.3% |
| wide_dense | partitioned | 0.7927 s | 0.7601 s | +4.1% |
| wide_dense | sort | 1.2148 s | 0.7584 s | +37.6% |
| many_keys | auto | 1.0797 s | 0.9718 s | +10.0% |
| many_keys | partitioned | 0.5263 s | 0.5454 s | -3.6% |
| many_keys | sort | 1.3477 s | 0.5408 s | +59.9% |

- `wide_equal`: 1,200 rows per side × 1,780 columns, equal shared rows.
- `wide_dense`: 1,200 rows per side × 1,780 columns, 55% changed value cells.
- `many_keys`: 150,000 rows per side × 12 columns, 1% changed value cells.
- Every fixture uses independently shuffled keys and eight keys present only on
  each side. The requested `sort` mode invokes the old external sorter in the
  baseline and the compatibility alias for hash partitioning in the new version.

All expected counts and **complete order-independent report digests** matched
between versions for all nine fixture/mode combinations. No differences were
sampled or omitted. Original output order intentionally changes to bucket/source
insertion order. CSV headers and cell values remain unchanged.

The largest improvements here are replacement of the external-sort path in the
mismatch workloads. Already-partitioned narrow rows were 3.6% slower, and the
small equal-data forced-sort case was 15.9% slower (about 27 milliseconds).
Resident dense comparison was essentially unchanged. These results do not prove
a universal speedup, a two-minute bound, or performance for two 6 GB HDD files.
Native Windows and full production-sized inputs were not available for this run.

Raw results: [before](../benchmarks/hash-before.json),
[after](../benchmarks/hash-after.json). Reproduce using
[adaptive_hash.py](../benchmarks/adaptive_hash.py). Final follow-up changes added
checkpoint evidence checksums and a fail-fast oversized-row guard; these do not
change the non-checkpoint benchmark path for these fixtures.

## Functional validation

- `python -m unittest discover`: **247 tests passed**, 50.639 seconds.
- `node test_analysis_ui.cjs`: passed navigation, pipeline-adjacent state,
  keyboard shortcuts and upload pause checks.
- `node test_upload_transport.cjs`: **21 transport tests passed**.
- JavaScript syntax checks and `git diff --check`: passed.

Coverage includes CSV dialects, quoted multiline records, encodings, fixed-width
imports, Excel imports, Parquet types/nulls, mixed-format pairs, renamed headers,
composite and missing keys, duplicate first/last policies, exclusions, overrides,
typed rules, retained-evidence re-evaluation, reports, analysis, projects,
upload/resume APIs, authorization, cancellation and cleanup.

New targeted checks cover a source-independent result oracle during memory
spill, opening each input only once on overflow, forced hash collisions,
recursive bucket redistribution, oversized rows, malformed spill frames,
source/metadata/spill corruption, damaged completed evidence recomputation,
byte-valued duplicate diagnostics in resumed journals, and the legacy engine
option never calling sorting helpers.

One frontend test was already failing on the baseline because it expected New
comparison to retain the saved job pointer. Its assertion now matches the
existing implementation, which clears that pointer before reload. No application
navigation behavior was changed to satisfy the test.
