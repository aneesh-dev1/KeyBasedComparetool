# Comparison engine strategy

How Data Compare compares two very large tabular files exactly and quickly.
The design target is two 10 GB files, each with 2,000 columns and 10 million rows.
The engine accepts any mix of CSV, delimited text (`.dat`, `.txt`, `.tsv`, `.psv`), Parquet and Excel worksheets.

## One engine, any input format

Every format is reduced to the same internal row: a **key** (the key columns, encoded as JSON text)
and a **packed payload** (the compared columns in canonical order, joined by `\x1f`, with a JSON or
null-bitmap fallback when a value contains that separator or is a Parquet null). Everything after
reading works on these bytes, so these features behave the same for every format:

- the comparison loop
- duplicate rules
- value overrides and comparison rules
- reports

| Input | Reader | Notes |
| --- | --- | --- |
| CSV / DAT / TXT / TSV / PSV | `csv_input.PackedRows` | Each file has its own delimiter, encoding and header row. Header-less files get `column1…columnN`. |
| Parquet | `parquet_input.PackedRows` | Reads only the projected columns and can read row groups in parallel. Nulls stay distinct from empty strings. |
| Excel worksheet | `excel_input.convert` | The chosen sheet is converted once to UTF-8 CSV. Excel's row limit keeps this small. |

The text dialect is detected per file from the first 1 MB (`text_dialect.detect`). The user can review and change it on the
Files & headers step. Values are always compared as **exact text** whichever dialect is chosen.

## Execution engines

`compare.py` chooses one of these engines at run time. The partitioned engine is chosen automatically
when both inputs together are at least 64 MiB, at least two CPU workers are available, and free scratch
space is more than 1.2 × the input size plus 512 MiB.

| Engine | When | How |
| --- | --- | --- |
| **partitioned** (`partitioned.py`) | Large inputs, several CPUs | Parallel range-partitioned hash join. |
| hash (`hash_join.py`) | Inputs that fit the memory budget | In-memory key lookups. |
| sort (`compare.py`) | Fallback, and resuming saved sort checkpoints | Compressed external merge sort. |

All three engines produce **byte-identical output files**. `test_partitioned.py` and the benchmarks below enforce this.

## The partitioned engine

```
           ┌──────────── plan ────────────┐
 left.dat ─┤ byte ranges at newlines      │   sampled keys → sorted key-range bounds
right.csv ─┤ (Parquet: row-group ranges)  │
           └──────────────┬───────────────┘
                          ▼
     partition (all CPUs, both files at once)
     each range → parse → (key, payload) → bisect into key range → spill segment
                          ▼
     join (all CPUs, one key range per task)
     load range from both sides in file order → sort keys → shared comparison loop → partial outputs
                          ▼
     assemble: concatenate partial outputs in key-range order
```

1. **Plan.** Each text file is cut into ~64 MiB byte ranges. Each cut is the first newline after an
   even split. Keys are sampled across the file, and sorted boundaries split the key space into ranges.
   There are enough ranges that both sides of one range fit one worker's share of the memory budget:
   `max(2 × workers, ⌈2.5 × input bytes ÷ per-worker budget⌉)`.
2. **Partition.** Worker processes parse ranges with the same packers as the other engines. Each
   (key, payload) is appended to the spill segment for its key range, using 48 MiB buffers. Both files
   are read at the same time, so no core idles while one file finishes.
3. **Join.** Each worker loads one key range from both sides in source order and applies the
   first/last duplicate policy. It sorts the keys and runs the shared `compare_pair_stream` loop into
   partial output files.
4. **Assemble.** Partial outputs are concatenated in key-range order. Each output file has one header.

### Why the result is exact

- **Record boundaries are proven, not guessed.** A range is accepted only if:
  - its strict CSV parse ends exactly at its end byte, and
  - it starts where the previous accepted range ended.

  The first range starts at the end of the header, so by induction every accepted cut is a real record
  boundary. A cut inside a quoted line break makes that range's parse fail. The range is then merged
  with the next one and re-read from the last proven boundary. Only that neighbourhood is read twice.
- **Every key is compared exactly once.** The key-range bounds split the key space, and each key is
  placed by binary search on the same bounds for both files. Matching keys therefore always meet in
  the same range, and no key appears in two ranges.
- **Duplicate rules are unchanged.** Segments are loaded in file order, so "first" and "last" refer to
  the same rows as in a sequential read.
- **The same values and rules are applied.** Values are compared as decoded text, so `10.0` ≠ `10.00`
  unless a rule says otherwise. Tolerance rules use `Decimal`, never binary floating point.
- **The same order and the same bytes.** Keys are sorted within a range and ranges are in key order,
  so output order matches the other engines exactly.
- **The same errors.** A malformed record is reported with the same global record number that the
  sequential engines report.

Byte ranges are used only for encodings in which `\n` and `"` are single, unambiguous bytes:
UTF-8 and Windows-1252. These files are not split into ranges:

- UTF-16 files
- files with bare-CR line endings

Each such file is read as a single range, and the join phase is still parallel.

## Sizing for 10 GB × 2,000 columns × 10 M rows per file

| Resource | Requirement |
| --- | --- |
| CPU | Scales with cores up to 16 workers. Each job's worker count is the CPU count divided by concurrently running jobs. |
| Memory | The budget is the available RAM minus a reserve (25 %, at least 2 GB). Each worker holds one key range of both sides. The number of key ranges grows to fit any budget, so a smaller machine runs more, smaller ranges rather than failing. |
| Scratch disk | About one packed copy of both inputs (≈ 20 GB) plus outputs. Use local SSD. |
| Output disk | Depends on the number of differences. In the worst case, where every cell differs, the long-format differences file is 10 M × 2,000 records. |

**Projection.** On the 10-core benchmark machine below, the whole 6.2 GB comparison ran at ≈ 0.38 GB/s
of input. Every phase is linear in input bytes, because the work per key range stays constant as the
number of ranges grows. At that rate two 10 GB files take about 55 seconds, plus output-writing time
proportional to the number of differences. This is a projection from the measured runs, not a
measurement on 10 GB files. Benchmark on your own hardware.

## Measured results

Measured on a MacBook (10 CPU cores, 16 GB RAM, internal SSD) with Python 3.9. Each file has
2,000 columns and the rows are in shuffled order. 10 % of rows have one changed cell, and 0.1 % of keys
exist on only one side. The old engine is the `HEAD` before this change. Outputs were compared
byte for byte.

| Dataset | Old engine | Partitioned engine | Outputs |
| --- | --- | --- | --- |
| 100 k rows, 2 × 0.78 GB, all columns | 4.63 s (hash) | **1.65 s** | identical |
| 100 k rows, 2 × 0.78 GB, 1 ignored column | 23.75 s | **3.83 s** | identical |
| 400 k rows, 2 × 3.1 GB, all columns | 39.82 s (sort fallback) | **16.19 s** | identical |

Reproduce with `benchmark.py` or by generating a pair and running
`python3 compare.py left.csv right.csv --keys id --output out --engine partitioned`.
Add `--workers N` to choose the worker count.

## Command-line options

| Option | Meaning |
| --- | --- |
| `--engine {auto,sort,partitioned}` | `auto` chooses as described above. |
| `--workers N` | Partitioned worker processes. Defaults to the CPU count, up to 16. |
| `--delimiter X` / `--right-delimiter X` | Delimiter for both files, or a different delimiter for the right file. |
| `--no-header` | Treat the first row as data. Columns are named `column1…columnN`. |

## Future work

- **Fixed-width `.dat` files** (mainframe layouts) need a column-layout definition, such as a
  copybook or a list of offsets. They can then use the same packers and engine.
- **Repeated comparisons** of the same large source could reuse the partition spill files when the
  file's size, modification time and inode are unchanged.
- **Parquet** uses the same partitioned path, with row-group ranges. It was not benchmarked here
  because `pyarrow` is not installed on the benchmark machine.
