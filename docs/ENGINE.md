# Adaptive exact hash comparison

The tabular comparison engine matches unordered keys without sorting input rows,
keys, or mismatch evidence. Reading and decoding the compared values is still
required. A hash is a routing aid, never proof that keys or values are equal.

## Inputs and shared semantics

| Input | Adapter |
| --- | --- |
| CSV, DAT, TXT, TSV, PSV | `csv_input.PackedRows`, with per-file delimiter, encoding and headers |
| Parquet | `parquet_input.PackedRows`, projected columns and parallel row groups |
| Excel | Selected worksheet converted once by `excel_input.convert`, then the same engine |
| Fixed-width / supported copybook | `fixed_width` import, then the same engine |

All adapters supply full encoded composite keys and packed canonical values.
The same `compare_pair_stream` applies exact text matching, null distinctions,
exclusions, overrides, numeric/date rules, and evidence writing. Native JSON
**document** comparison retains its separate structural semantics; nested objects
and arrays are not flattened into tabular rows by this change.

## Plan and implementation

1. Remove sorted-key iteration from resident matching.
2. Replace sampled, ordered partition boundaries with stable hash buckets.
3. Continue reading into spill frames when resident memory fills, without
   reopening either source. Preserve selected duplicate rows and occurrence counts.
4. Bound bucket loading and recursively repartition overflow, including skewed keys.
5. Preserve source validation, resumable completed work, rules and report schemas.
6. Test format combinations, independent result oracles, collisions, overflow,
   cancellation, checkpoint corruption and application APIs; benchmark before push.

## One matching algorithm, two execution modes

**Resident:** small jobs retain exact-key dictionaries. Iterate champion keys in
insertion order, probe challenger, then emit challenger-only keys. There is no
sorted union or row-hash equality shortcut. Packed-byte equality skips cell
work only when the actual packed values are identical.

**Partitioned:** large jobs parse independent CSV byte ranges or Parquet row groups
in parallel. A stable CRC-32 routes each full key to a bucket. Workers load paired
buckets in source occurrence order and run the same comparison loop. CRC
collisions share a bucket but remain distinct dictionary keys. Partial CSVs are
assembled with unchanged headers.

If a resident lookup fills its budget, already-read entries spill with occurrence
counts and reading continues from the current iterator. If a bucket fills its
budget, it is redistributed using a depth-specific BLAKE2b hash. Only spill
records are reread; original files are not restarted because of memory overflow.
A single unmanageable key/row produces an explicit memory error, not an OOM
retry or silently incomplete result. Exceptional records and decoder buffers can
still exceed an allocation target: this is not an OS RSS limit.

Initial bucket sizing uses input bytes and the per-worker budget. Recursive
splitting handles compressed Parquet expansion and uneven row widths. Worker
count is bounded by allocated CPUs and memory. File-size planning is an estimate,
not a guarantee that a bucket will fit. CSV read buffers are bounded by the
worker share; comparison maps reserve headroom for decoding and output buffers.

**No sorting fallback is reachable from `compare()`.** `--engine sort` remains a
compatibility alias for hash partitioning so existing saved jobs and scripts
continue to work. Legacy sorter helpers remain for old codec/reference tests;
they are not selected by production comparisons. `--sort-workers` is a legacy
configuration field and no longer controls comparison work. Use `--workers` for
an explicit CLI parallelism limit; the application sizes resources automatically.

## Accuracy and ordering

- Full composite keys determine matches, regardless of input order or row count.
- First/last duplicates refer to source occurrence order, including across spills.
- CSV ranges are validated from the preceding accepted boundary. A cut inside
  a quoted multiline record is merged and reparsed. UTF-16 and unsupported
  splitting dialects use a single sequential read range.
- Null, empty text, leading zeroes, Unicode and delimiters retain existing semantics.
- Numeric/date equivalence requires configured rules; exact text is the default.
- Both unresolved differences and raw evidence remain available for re-evaluation.
- Output rows use bucket/insertion order, **not global key order**. Consumers must
  not rely on CSV row order. Tests compare full record multisets, retaining duplicate
  evidence, as well as counts and column statistics.

## Checkpoints and integrity

Source SHA-256 validation is retained. It reads complete source files before
checkpoint reuse and on initial checkpoint creation. Source size and modification
time are checked again before completion. This additional I/O is included when
measuring the server workflow, but not a non-checkpoint CLI comparison.

Partitioned jobs persist completed read ranges and joined buckets. Spill hashes
are calculated during writing and checked on resume. Plan/journal checksums reject
changed metadata. Completed evidence chunks are hashed during buffered writes;
resume checks these hashes and recomputes any damaged result bucket. Incomplete work is repeated; successful recorded work is reused.
The current hash-plan version does not reuse older sorted partition plans; those
inputs are rebuilt after source validation. Small resident jobs rebuild their
lookup after interruption. Completed reports retain their existing schema.

An `INCOMPLETE` marker remains until the full result succeeds. Worker locks prevent
concurrent resume of the same job. Cancellation is checked during source reads,
bucket decoding and comparison. Temporary recursive buckets are cleaned on
ordinary success, cancellation and errors; forced termination can leave scratch
for job retention cleanup.

## Dense mismatches and performance limits

For 1,000,000 rows × 1,780 columns, 55% differing cells can produce roughly
979 million cell differences. Reading, applying rules and writing two long-form
evidence files can dominate total time even with no sorting. No two-minute SLA is
claimed for two 6 GB files on an HDD. SSD/HDD throughput, CPU, encoding, Excel
import and mismatch volume must be measured on the deployment machine.

The engine retains packed equality and sparse vectorized mismatch optimizations.
Dense cells still produce complete evidence; results are not sampled or dropped
to improve benchmark numbers. Export and analysis-index generation are separate
costs and are not included in core-comparison benchmarks unless explicitly stated.

See [HASH_ENGINE_VALIDATION.md](HASH_ENGINE_VALIDATION.md) for this change's measured
before/after results and validation scope. Reproduce core measurements with:

```sh
python benchmarks/adaptive_hash.py --checkout /path/to/checkout --output timings.json
```
