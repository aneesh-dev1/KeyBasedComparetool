# TransUnion-themed CSV key comparison

## Web UI

On macOS, double-click **Start CSV Compare.command**. It uses the bundled Codex
Python runtime when available, otherwise your installed Python 3.10+.
On Windows, install **64-bit Python 3.10+**, extract the complete ZIP, then
double-click **Start CSV Compare.bat**. The launcher tries `py -3` first and
falls back to `python`. Keep its console window open while work is running.
On other platforms, or from a terminal in this folder:

```bash
python3 server.py --open
```

Open **http://127.0.0.1:8765**. No third-party packages are required.

1. Choose the left and right CSVs and upload them. Uploads use 8 MB chunks,
   streamed to disk. After an interruption, reselect the same files and resume.
2. Search and select the key columns, then choose the total sort memory budget.
   The 32 GB RAM profile defaults to **4 GB** and **parallel** file sorting.
   Optionally select ignored columns and enter ignored key values.
3. Continue to **Value Overrides** and add any allowed column/value pairs.
   Use **File Preview** to inspect a limited sample before starting.
4. Start the comparison. **Pipeline & Logs** shows the current phase and live
   output. You can refresh after uploading without losing the job.
5. Review row totals, per-column mismatch counts and the first 100 records in
   each category. Download the full results using the report buttons.
6. Use **Job history** in the sidebar to reopen previous results or unfinished uploads.

The sidebar contains **New comparison**, **Ignore key containers**, and **Job history**. Files, Keys & scope,
Value overrides, File preview, Pipeline & logs, and Results are phases within
New comparison. Use the arrow at the top of the sidebar to collapse it to icons
or expand it. Your choice is remembered after refreshing.

**Excel workbook:** one `.xlsx` file with exactly one sheet per mismatched
column. Ten mismatched columns produce ten mismatch sheets. Configured key exclusions add an **Ignored key containers** audit sheet.
Every sheet contains the original column name, the key columns, the left value
and the right value. A sheet contains only mismatches for its own column.
Unchanged columns are omitted. If there are no changed cells, the workbook
contains one explanatory `No mismatches` sheet.

Sheet names use the original column names wherever Excel allows. Names longer
than 31 characters or containing forbidden characters are shortened/sanitized;
case-insensitive collisions get a numeric suffix. The full original name is
always present in the first row. Values remain text, including leading zeros,
whitespace, and strings beginning with `=`.

Excel allows 1,048,576 rows and 32,767 characters per cell. Two heading rows leave
room for **1,048,574 mismatches per column**, sufficient for a million input
rows with unique keys. If a column or cell exceeds those limits, the Excel
export fails with a clear message; it never truncates records. HTML/CSV remain
available. See [Microsoft's Excel limits](https://support.microsoft.com/en-us/excel/excel-specifications-and-limits).

Keys found on only one side are not cell mismatches: they have separate CSV
downloads and are also included in the HTML report.

**HTML report:** a ZIP containing `index.html` with the summary, and linked
result pages with 1,000 records per page. Extract the ZIP and open `index.html`.
It works offline and includes all changed cells and unmatched keys. A standalone
summary HTML download is also available. Large values appear in full in reports;
only the UI preview limits values to 1,000 characters.

Report generation is asynchronous. Click Excel or HTML once to generate it,
then click again when it says “Ready to download.” Comparison and export jobs
share one job queue. A comparison can use two file-sort processes, but multiple
comparisons and exports do not run simultaneously.

### Storage and running the app

```bash
python3 server.py --data-dir /path/to/fast-ssd/keywise-data --port 8765
```

Windows terminal example with a local SSD:

```powershell
py -3 server.py --data-dir "D:\CSVCompareData" --open
```

The data directory stores uploaded copies, scratch files, metadata and reports.
The default is `data/` beside `server.py`. For two 6 GB inputs, budget the 12 GB
uploaded copies **in addition to** comparison scratch and report space described
below. Excel grouping requires additional temporary disk space proportional to
the mismatch data. All processing and exports stream records; they do not load
the complete mismatch report into memory. A very large workbook may still be
slow to open in Excel, even when generation succeeds.

Keep the server running while processing. An interrupted server marks unfinished
jobs as failed on restart. An interrupted upload can be resumed; an interrupted
comparison must be started as a new comparison. Uploaded files and reports are
retained across restarts. To reclaim storage, stop the server and remove the
specific job folder from `data/` after saving its reports. Starting a new
comparison does not delete earlier files.

This is a local, single-user application bound to `127.0.0.1`, with session-token
protection and origin checks. It is not configured as a shared public service.

## Command-line usage

Python 3.10+ command-line tool, with no third-party dependencies.

```bash
python3 compare.py left.csv right.csv \
  --keys customer_id account_id \
  --output comparison-report \
  --memory-mb 256 \
  --temp-dir /path/to/ssd/scratch
```

Replace the example keys with your actual column names. For a single key use
`--keys customer_id`. The scratch directory must exist; the output directory
must not exist. Inputs are never modified. Progress appears every 100,000 rows.

## Comparison rules

- Keys must be unique in each input. Composite keys are encoded unambiguously.
- Values are compared as parsed CSV text: `01` differs from `1`; spaces and
  case matter. CSV quoting style and record line endings do not matter.
- Empty fields are empty strings; `NULL` is literal text. No type inference.
- Empty key components are rejected unless `--allow-empty-keys` is supplied.
- Both files must have the same column names, but column order can differ.
  Missing, empty or duplicate headers and inconsistent row widths are errors.
- Default encoding is UTF-8 with optional BOM. Use `--encoding` and
  `--delimiter` for other input formats. Quoted multiline values are supported.

## Reports

- `differences.csv`: one row per changed cell, with a JSON array representing
  the key, column name, left value and right value.
- `left_only.csv`, `right_only.csv`: key columns for unmatched rows.
- `summary.json`: row/cell totals, per-column change counts, and elapsed time.

Exit codes: **0** identical, **1** differences, **2** error. A summary is written
only after successful completion. Failed/interrupted runs retain partial reports
marked `INCOMPLETE`; use a new output directory to retry. Temporary runs are
cleaned on ordinary completion or exceptions; forcibly killed processes can leave
scratch files behind. Duplicate detection can occur late during the merge.

## Large-file design and sizing

Each CSV is read once and converted to sorted runs using a configurable memory
budget. Parallel mode sorts the two files in separate processes using Windows
compatible process spawning. Bounded fan-in merges limit simultaneously open
files. The final merge compares matching keys sequentially. Compact binary
runs avoid JSON quoting for ordinary row values; unusual separator characters
use an exact JSON fallback. Identical serialized rows are compared as bytes;
only changed rows are decoded for cell-level output. Equality is exact, without
probabilistic hash shortcuts. Complexity is O(N log N) for sorting and
O(total input bytes) for the final scan, plus the size of reports.

For the requested **32 GB Windows machine and two 6 GB files**, begin with the
UI defaults: **4 GB total sort budget**, **two sort workers**, and a local SSD
for the data directory. Each sorter receives half the budget. The UI also
offers 2 GB and 8 GB budgets; leave enough RAM for Windows, the browser and
other applications. Choose sequential mode if concurrent reads slow down a
mechanical disk or shared storage. This is a starting configuration, not an
automatic hardware measurement or a guaranteed fastest setting.

The budget is a **sort chunk target**, not a strict process memory cap. Allow
additional memory for Python processes, CSV rows, serialization, sorting and
merge buffers. A single large record can exceed the target. `--max-field-mb`
(default 64) limits individual fields. Input size and column count do not cause
the entire datasets to be retained in memory. Existing saved jobs retain their
configured memory settings; jobs created before parallel sorting use one sorter.

Use local SSD storage for uploads, scratch and reports. For two 6 GB inputs,
starting with 40–60 GB free **in addition to the uploaded copies** is a planning
estimate, not a guaranteed upper bound. Merge passes temporarily retain both
old and new runs. Compact runs reduce scratch size for typical wide tables,
but content and encodings affect the result. Measure scratch growth on a
representative subset first. Reports need additional space: if nearly every
cell differs, the long-format report can be much larger than the source files
(up to roughly 2 billion cell records at 1 million rows × 2,000 columns).

CSV parsing and cell-level comparison can still be CPU-bound. Parallel mode
accelerates the independent file preparation; matching keys and generating
reports remain sequential. Larger chunk budgets can reduce merge passes.
No runtime claim is made for the full 6 GB files without a benchmark on the
target hardware. For repeated comparisons, upstream sorted data or a reusable
indexed/columnar representation would avoid rebuilding the sort on every run.

## Validation

```bash
python3 -m unittest discover -p 'test_*.py' -v
```

Tests cover composite keys, reordered columns/rows, exact text values, Unicode,
embedded line breaks/tabs/quotes, empty files, duplicate/empty keys, malformed
rows, schema mismatches and 2,000-column inputs with multiple external merge passes.
UI/backend tests cover chunked upload and offset validation, comparison execution,
preview and downloads, exactly ten sheets for ten changed columns, per-column
row grouping, sheet-name collisions, text/formula safety, oversized-cell errors,
HTML escaping and pagination. Full 6 GB performance has not been benchmarked.

## Visual theme

The UI uses TransUnion’s logo and the cyan (`#00a6ca`), navy (`#004364`),
yellow (`#fcd800`), and neutral colors found in its public newsroom stylesheet.
Buttons use dark text on yellow; smaller links use a darker cyan for contrast.
The local logo asset also appears in offline HTML reports. Excel heading rows
use navy. The one-sheet-per-mismatched-column layout is unchanged.

References: [TransUnion newsroom](https://newsroom.transunion.com/),
[public stylesheet](https://content.presspage.com/templates/430/1104/920359/pp-transunion-rebrand.min.css),
and [TransUnion logo source](https://commons.wikimedia.org/wiki/File:TransUnion_logo.svg).
The SVG is stored locally without modification. The app uses system fonts, since
an internal licensed brand-font package was not supplied. This is an adaptation
of public branding, not a certification against internal brand guidelines.

## Ignore columns and keys

**Ignore columns** is a searchable, paginated selection list. Both column
selectors read only the CSV header record, never scan data rows to build the
list, and render at most 100 choices per page. A column selected as a key cannot
also be ignored. Ignored columns are omitted from temporary row payloads,
equality checks, per-column counts, and mismatch exports. Both input schemas
must still have the same set of column names.

**Ignore keys** excludes matching rows from both files, including rows that
otherwise would appear as left-only or right-only. For a single key enter
`001, 002, 003`. Values containing commas or leading spaces must be CSV-quoted,
for example `"Smith, Jane", " 001"`. Spaces after a separating comma are skipped;
other spaces, case and leading zeros are significant. Empty entries are errors.

For composite keys, use a JSON list of tuples in the selected key order:
`[["001","A"],["002","B"]]`. Each tuple must have one nonempty string per key.
Exclusion matches the entire tuple; excluding `["001","A"]` does not exclude
`["001","B"]`. Duplicate filter entries are deduplicated.

Rows are filtered during the existing streaming read, before sorting, joining,
and duplicate-key detection. All occurrences of an excluded key are skipped.
CSV syntax and row width are still validated. CSV fields must still be parsed
to locate the selected values; ignored columns cannot eliminate that sequential
read. No extra full-file preprocessing pass is added.

The summary retains total input rows and adds excluded and compared row counts
for each file, the ignored-column list and the number of distinct excluded key
values. Equal rows and changed rows describe only the selected scope. If every
non-key column is ignored, retained matching keys count as equal.

Command-line equivalents:

```bash
python3 compare.py left.csv right.csv --keys customer_id \
  --ignore-columns updated_at load_timestamp \
  --ignore-keys '001, 002, 003' --output results
```

Exclusion tests cover header-only reading with 2,000 column names, single and
composite keys, comma-containing keys, leading zeros, excluded duplicates,
all-value-column exclusion, validation errors, API input and filtered exports.

## Value overrides

Select a compared non-key column, enter the exact **File 1 value** and **File 2
value**, and click **Add rule**. Add multiple rules for one or several columns.
For example, `status: None → none` accepts file 1's literal `None` paired with
file 2's literal `none` for the same key. It does not accept the reverse pair or
make the entire column case-insensitive. Rules are exact directional exceptions,
not data replacements or transitive equivalence groups. Empty input represents
an empty CSV field. Duplicate rules are deduplicated; key and ignored columns
cannot have overrides.

The original source files and remaining mismatch values are preserved. Accepted
pairs are omitted from mismatch exports. A row counts as equal if all compared
values are equal or explicitly accepted. The summary records the complete rule
list and the number of unequal cells accepted by overrides.

Draft settings are saved when moving between comparison phases and when adding or
removing a rule. Once a comparison is started, its configuration is immutable.
Saved overrides remain viewable for older comparisons. To change a completed
comparison, start a new one.

CLI example:

```bash
python3 compare.py left.csv right.csv --keys id --output results \
  --value-overrides '[{"column":"status","left":"None","right":"none"}]'
```

## File previews, pipeline, and job history

**File Preview** reads only the requested first 1–1,000 rows per file. It shows
original file order, before exclusions/overrides, without aligning rows by key.
Twenty columns are returned per page; use Previous/Next columns to inspect wide
files. Cells are limited to 500 displayed characters, and responses are capped
at 2 MB per file. Fewer displayed rows may indicate end of file or that cap.
Previews do not scan the rest of the CSV or change source values.

**Pipeline & Logs** shows validation, reading/sorting each file, comparison,
report writing and completion. Progress is based on actual worker phases and
counts, not simulated percentages. The console polls incremental log output and
keeps the latest 1,000 lines. Download the complete log for the full record.
Failed runs retain their status, error, stage and logs.

**Job history** lists saved jobs newest first, 25 per page. It reads job metadata,
not CSV contents. Open a job to inspect results, preview sources, view rules or
logs, or resume a partial upload. History survives browser/server restarts as
long as the same data directory is retained. Older completed jobs without the
new metadata remain readable. All this history belongs to this local workspace.

## Ignore key containers

Open **Ignore key containers** in the sidebar. Create a reusable container with
a unique name (for example NO_HIT_KEYS), a required reason, and comma-separated
key values. For composite keys, specify the number of key columns and enter JSON
tuples in the same order as the comparison key columns. Values use exact text
matching, preserving leading zeros and quoted whitespace.

Select one or more containers in **Keys & scope**. Additional manual keys remain
available. Container selections are saved in drafts; starting a job snapshots
the selected names, reasons, and keys. Overlapping keys are excluded only once
from comparison, with each classification retained in the audit. Container
creation reads no uploaded CSV data. Saved containers persist in the local data
directory and have unique names; editing/deleting them is not currently offered.

HTML summaries and full HTML reports include container names, reasons and keys.
Excel adds an **Ignored key containers** sheet (including when there are no
mismatches). A conflicting mismatch column sheet gets a unique suffix. The audit
lists configured exclusions, including keys absent from either source; aggregate
actual excluded row counts remain in the comparison summary. Legacy manual keys
appear as Manual keys with a generic reason. Excel row/cell limits are enforced,
never silently truncated. Existing completed jobs retain their original reports.

## Excel worksheet inputs

Upload two `.xlsx` / `.xlsm` workbooks, two CSV files, or one of each. After
Excel uploads finish, choose one sheet per workbook and click **Use selected
sheets & continue**. The existing keys, scope, containers, overrides, preview,
pipeline, history, and export flow then applies unchanged. Worksheet names are
recorded with the job and in report source metadata. The first worksheet row
must contain unique, nonempty headers, with matching column sets between inputs.

Sheet discovery reads workbook metadata only. The selected sheet is streamed
into a local UTF-8 CSV for the disk-backed comparison engine; shared strings
are indexed in a temporary SQLite database with a bounded cache. Only one heavy
import/comparison/export runs at a time. Import progress appears below the sheet
selectors and is retained in the job log. Reserve disk space for the original
workbooks, imported CSVs, sorting scratch, and reports. Large Excel imports have
not been benchmarked at 6 GB. The CLI remains CSV-only; Excel selection is in
the local web app. No additional Python packages are required.

**Value rules:** compare stored cell values, not Excel display formatting. Text
(including leading zeros) is preserved. Numeric values use their stored OOXML
text; number formats are ignored, so numeric dates remain serial numbers and
workbooks with different date systems are not date-normalized. Booleans become
TRUE/FALSE. Formula cells use cached results; formulas are never recalculated.
A formula without a saved result fails with a message to recalculate and save
in Excel. Excel errors are compared as literal error strings. Missing cells
within a row become empty strings; completely blank data rows are skipped.
Merged data cells are not filled down. Macros and external links are not run.

Encrypted workbooks, binary `.xls` / `.xlsb` files, and chartsheets are not
supported. Save those inputs as an unencrypted `.xlsx` first. Changing the
selected sheets after import requires a new comparison; original uploaded
workbooks remain on disk.

## JSON comparison

Open **JSON comparison** in the sidebar and paste two JSON documents or load
UTF-8 `.json` files. Choose the default array behavior: preserve order compares
positions; ignore order matches whole blocks as a multiset (duplicates count).
Object property order is always ignored. Strings and booleans remain distinct
from numbers. Numbers compare by numeric value with decimal precision preserved.
Missing properties differ from explicit null; duplicate object properties and
non-standard NaN/Infinity values are rejected.

Add array rules to override the default. Paths support `$`, `.property`,
`["special.property"]`, and `[*]` for array ancestors, for example
`$.customers[*].addresses`. These are explicit patterns, not full JSONPath.
Match-by-field rules use a direct scalar field such as `id`; missing, null, or
duplicate IDs are errors. Different arrays can use different behaviors. A rule
that matches no array produces a visible warning. Remove a rule to replace it.

Results include added, removed, and changed values, with separate original
paths for both files. Without a match field, modified unordered blocks are
reported as removed and added. Preview shows 200 differences and shortens long
values; JSON and HTML downloads contain the complete result and rules. Inputs
are never sorted or changed. This feature does not persist to Job history;
refreshing clears its inputs/results, so download reports to retain them.

JSON comparison is memory-based and separate from the disk-backed CSV/Excel
engine: 5 MiB per input, 100 nesting levels, 200,000 values per document, 100
rules, and 10,000 differences. Exceeding these limits produces an error, not
a silently truncated report. Work uses the shared heavy-work queue.

## Batch console updates

New comparisons log each memory-bounded sorting batch as started, sorting, and
completed, including batch row count and elapsed sort/write time. Intermediate
merge passes report each merge batch and its total batches for that pass.
Comparison remains streaming: progress checkpoints start and complete every
10,000 processed keys, including the final partial checkpoint. These messages
include cumulative processed keys and changed cells; they do not imply loading
10,000 wide rows into memory. Total sorting batches are not guessed in advance.
The live console and downloadable run.log contain these messages. Existing
completed job logs are unchanged.

## Reproducible performance check

A synthetic local check used 100,000 shuffled rows × 2,000 columns per file
(about 580 MB each), with one changed field in 10% of rows. The earlier engine
with 256 MB sequential sorting took 23.820 s; the compact engine at the same
256 MB took 12.921 s; the compact engine with 4 GB total and two sorters took
7.238 s. All three produced byte-identical CSV result files (10,000 changed
cells). These are single-run measurements on the development macOS machine,
not Windows or full-size measurements. They exclude upload and report export;
input caching, CPU, storage, data shape, and mismatch frequency affect timing.

Run the included benchmark on the target Windows machine from this folder:

```bat
py -3 benchmark.py --output benchmark-100k --rows 100000 --columns 2000 --memory-mb 4096 --sort-workers 2
```

It creates synthetic inputs in a new output directory, validates known
mismatches, and writes `benchmark.json` with elapsed time and sampled scratch
usage. It does not overwrite user data or measure RAM. Choose a new directory
for each run. Compare `--sort-workers 1` and `2` on the same hardware.
`--rows 1000000` generates approximately 5.8 GB per file for a full-scale
synthetic test; reserve storage first. No full-scale or native Windows test
was performed during this change.

## Windows upload and header fixes

Blank or whitespace-only CSV headers now receive a positional name such as
`column2`. Repeated headers receive `_1`, `_2`, and subsequent free suffixes.
Explicit existing names are preserved: `a,a,a_1` becomes `a,a_2,a_1`; an empty
first header alongside an existing `column1` becomes `column1_1`. Names are
case-sensitive. Normalization reads only the header and does not rewrite source
files. Repeated names align by occurrence, and anonymous columns by position.
The same names appear in key selection, scope, previews and mismatch exports.
The UI shows up to 200 header adjustments and reports retain all adjustments.

Upload checkpoints now use the actual saved file length instead of replacing
job metadata after every 8 MB chunk. Temporary metadata files have unique names
and bounded retries for permission/sharing errors. A permanent lock still
requires closing the application holding the file or choosing a writable local
data directory; it is reported without replacing the previous metadata.
Interrupted requests reconcile the server offset before retrying, avoiding
duplicate bytes. Pause finishes the active chunk; Resume continues from the
saved offset. After reopening the browser, reselect the same source files to
resume. Job history, JSON, and container navigation remain available during
upload; opening a different job or starting a new job requires pausing first.

The layout wraps settings and long content to fit available window width and
keeps wide tables in their own scroll areas. Sidebar content can scroll on
short displays. Upload progress painting is throttled. Browser checks covered
620, 900, 1280, and 1920 CSS pixels, plus navigation/pause/resume on two roughly
580 MB synthetic files. Windows file-lock failures were simulated in tests;
this change was not run on the user's Windows laptop or external monitor.

To update: stop the running server, replace application source files from the
new ZIP, retain the existing `data` directory, restart the Windows launcher,
and refresh the browser. A failed upload can be resumed from Job history with
the same original files. Do not run two servers against the same data folder.
