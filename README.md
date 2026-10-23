# TransUnion Data Compare

## On-premises team deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for the Linux `nohup` launcher, five-browser
independent active comparisons without login, shared completed history and Projects,
server-path inputs, resource limits, and rollout checks.

## On-premises team deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for the Linux `nohup` launcher, five-browser
workspace separation without login, resource limits, and rollout checks.

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

Open **http://127.0.0.1:8765**. CSV, Excel and JSON use the Python standard
library. Install PyArrow from `requirements.txt` for Parquet support.

The tabular workflow has four stages:

1. **Files & headers:** choose two CSV, Excel or Parquet files. Upload from your
   computer, or select **Use server path** when an administrator has enabled
   source directories. Select Excel worksheets where needed. Edit comparison
   headers in the same stage, keeping file details available in the expandable
   source area. Headers match without case sensitivity; only common columns
   continue into comparison. Optionally select a profile template and project.
2. **Keys & scope:** select keys, ignored columns and ignored keys/containers.
   **Preview files** becomes available after applying headers and opens inline
   tables with the applied names. The top-right **Comparison rules** drawer
   applies rules to one, several or all value columns. Add configured rules to
   the selection before applying; an unadded draft triggers a confirmation.
3. **Pipeline & logs:** start comparison from the top right. Switch between
   stage cards and the console without interrupting work. The console supports
   search, category filters/counts, follow, pause, copy and log download.
   HTML and Excel reports are generated during **Write results**. Completion
   offers **View results** immediately and opens Results after five seconds.
4. **Results:** inspect match bands and per-column differences. Click a column
   to open its analysis drawer, with **Mismatches** and **Patterns** tabs. A
   pattern's **List keys** action filters to that exact value pair. Add comments,
   attach the comparison to a project, or download the HTML/Excel report.

The unified picker also accepts two JSON files and opens the existing array
alignment and side-by-side JSON flow. Mixed JSON/tabular pairs are rejected.
There is no separate JSON sidebar destination. Each fresh page load opens an
empty comparison. **Compare files** offers **New comparison** and **Continue
last comparison**; a new selection does not delete prior jobs. Active uploads
remain independent and can be resumed by reselecting the same unchanged files.

**Recompare** opens Keys & scope, Value overrides and Comparison rules tabs.
The original keys stay locked. Supported changes re-evaluate immutable retained
raw differences and create a separate review version; they do not read/sort the
sources again. Removing a rule can restore its accepted raw differences. The
review plan explains when a change needs a full comparison because the original
evidence did not cover it. The original job stays available in Job history.

In team mode, everyone can browse completed Job history and Projects. Active
uploads and unfinished jobs remain in their creating browser workspace. The
creator controls cancellation, deletion, source/configuration changes and the
job's project/audit assignment. Teammates can review completed results, add
comments, download reports and create their own review versions. This is shared
access without login, not authenticated per-person ownership. Profiles and
ignore-key container libraries remain browser-workspace scoped.

The sidebar starts collapsed and expands into labelled navigation; narrow screens
use a drawer. **Take a tour** covers the workflow and library pages, with Back,
Next and Skip, and can be replayed from Docs & FAQ. TU loading animation appears
at startup and for delayed requests without imposing a fixed startup delay.
Keyboard shortcuts use **Alt** on Windows/Linux and **Option** on Mac: B toggles
navigation, 1–4 selects available stages, F focuses search, R opens Recompare,
and / opens shortcut help. Tab/Shift+Tab, Enter/Space and Escape work throughout.
Shortcuts are standardized rather than customizable. Reduced motion is respected.

**Excel workbook:** one `mismatches.xlsx` with **Comparison Summary**, **TOC**,
and one detail sheet per mismatching column. Ten mismatching columns produce ten
detail sheets in addition to summary/audit sheets. Summary includes source/type
metadata, keys, rules and totals. TOC includes counts, rates, comments and internal
View links. Detail sheets contain key column(s), **valueA**, **valueB**,
**variable**, **Comments** and a **Back to TOC** link; redundant per-row typeA,
typeB and diffAB fields are omitted. Configured exclusions add a highlighted
**Ignored key containers** audit sheet; review notes are retained as well.

Sheet names preserve column names where Excel permits. Names longer than 31
characters or containing forbidden characters are shortened/sanitized;
case-insensitive collisions get a numeric suffix. Full names remain in the
workbook. Values remain text, including leading zeros, whitespace and strings
beginning with `=`. Excel allows 1,048,576 rows and 32,767 characters per cell;
exports fail clearly if a limit would be exceeded rather than silently truncating.
See [Microsoft's Excel limits](https://support.microsoft.com/en-us/excel/excel-specifications-and-limits).

**HTML report:** one self-contained `comparison-report.html` with TransUnion
branding. The desktop dashboard keeps summary metrics, column match-band chart,
mismatching columns and matching columns in one view, with contained scrolling.
Bands use exact rates: 100%, 99–<100%, 95–<99% and below 95%. Click a mismatching
column to open its drawer with exact recurring value pairs, count/share, comments
and up to 20 sample keys with both values. Comparison details, coverage, ignored
keys/containers and review notes open from the dashboard. Container names,
reasons and configured keys are highlighted. It works offline; printing includes
report sections. Small screens reflow for readability. Sample/pattern values
longer than 1,000 characters are marked as shortened; Excel keeps full values.

The app presents one HTML and one Excel export. The AI package is deferred from
the current UI. Both reports are prepared in the pipeline; download buttons use
those files directly. If notes or report versions make an export stale, a single
click queues regeneration, shows progress and downloads it when ready. Report
work shares the heavy-job queue with comparisons. Default concurrency is one.

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

Keep the server running while processing. Upload offsets persist across restarts.
Interrupted comparisons can reuse validated completed external-sort checkpoints;
an interrupted in-memory hash run rebuilds its lookup. Open an eligible job's
**Resume comparison** action in Job history. See the resume section below for
source validation and limits. Completed jobs and reports survive restarts.

Automatic cleanup defaults to seven days of inactivity and protects active work.
The creator can delete a finished or unused job from Job history after saving
needed reports. Starting a new comparison does not delete existing jobs. Cleanup
never removes original files referenced through server paths.

The default local server binds `127.0.0.1`, with request-token and origin checks.
Use the documented on-premises launcher and `PUBLIC_URL` for shared internal
access; shared mode does not introduce a login system.

### Use files already on the server

An administrator can enable read-only source directories:

```bash
python3 server.py --data-dir /srv/key-compare/data \
  --source-root /srv/comparison-inputs --source-root /mnt/team-exports
```

Then choose **Use server path** in Files & headers and enter two absolute paths
visible to that server. CSV and Parquet are read in place, avoiding browser
transfer and uploaded copies. Excel still imports the selected worksheet into a
job-local CSV. JSON opens the existing limited-size JSON workflow. Allowed roots
are resolved before access; paths and symlinks escaping them are rejected. The
service account needs read access. A saved file identity/size/modification-time
check rejects sources that change before use; keep sources unchanged during the
run. Server paths do not make a laptop-only file accessible to the server.

The source mode is disabled unless at least one root is configured. Only expose
input directories the whole trusted team is permitted to read. See
[DEPLOYMENT.md](DEPLOYMENT.md) for `SOURCE_ROOTS` and operational details.

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

- Duplicate keys generate warnings. The first source row is retained by default; select last occurrence to change this. Extra rows are skipped and audited in duplicate_keys.csv and exports. Composite keys are encoded unambiguously.
- Values are compared as parsed CSV text: `01` differs from `1`; spaces and
  case matter. CSV quoting style and record line endings do not matter.
- Empty fields are empty strings; `NULL` is literal text. No type inference.
- Empty key components are rejected unless `--allow-empty-keys` is supplied.
- Only common column names are compared, ignoring header case; column order can differ.
  Blank headers receive `column<number>` names; duplicate/case-colliding names
  receive distinct numeric suffixes. Inconsistent row widths remain errors.
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

The default engine builds exact key lookups from both sources, retaining packed rows within a measured memory budget. Key order and different row counts do not need to match. First/last duplicate handling is preserved. Only key references are ordered for deterministic output; full row payloads avoid sort files when the lookups fit. If the budget is exceeded, the lookups are released and comparison restarts using compressed external sorting. Saved sort checkpoints select that fallback directly. Inputs still must be read; checkpoint fingerprint validation also reads source files.

The application measures currently available RAM, CPU capacity and free workspace storage at worker launch. There is **no fixed default memory ceiling**. It keeps 25% of available RAM (at least 2 GB where possible) outside the comparison budget, accounts for configured concurrent workers, and adapts read batches and buffers to column width. Hash objects use at most 80% of that budget, reserving more space for transient decoding and ordering. These are allocation targets, not a hard process RSS limit. A single exceptionally large row can exceed a target. An administrator can optionally impose an explicit deployment ceiling with `MAX_SORT_MB`; its default is `0` (automatic).

Source reads and disk fallback use one sorting worker to avoid HDD seek contention. The server defaults to one heavy job at a time; other users queue independent comparisons. The chosen resources appear in the console and `runtime-settings.json`. Settings has been removed from the sidebar. Browser display preferences are under Docs & FAQ; comparison rules remain in Keys & scope.

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
HTML escaping, exact match-rate bands, and single-file exports. Full 6 GB performance has not been benchmarked.

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
equality checks, per-column counts, and mismatch exports. Non-common columns are automatically omitted from comparison and recorded in the summary.

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

From Results, choose **Recompare → Value overrides**. Select a compared non-key column, enter the exact **File 1 value** and **File 2
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
Choose **Start recomparison** to create a separate review version. Supported
rule/scope changes stream immutable retained evidence without reading source
files; unsupported changes require a full comparison. Baseline files are
hard-linked within the data filesystem, so hard-link support is required.
Original results remain unchanged in Job history. Saved profile overrides still
apply when a template is selected during Files & headers.

CLI example:

```bash
python3 compare.py left.csv right.csv --keys id --output results \
  --value-overrides '[{"column":"status","left":"None","right":"none"}]'
```

## File previews, pipeline, and job history

**Preview files** is optional and appears after applying the header layout.
It expands inline tables, side by side on desktop, without creating another
stage. It reads only the requested first 1–1,000 rows, in source order before
scope/rule processing; it does not align records by key. Twenty columns are
returned per page, with 500 displayed characters per cell and a 2 MB response
cap per file. Fewer displayed rows may indicate end of file or the response cap.
Applied comparison header names are shown. Preview never changes source values.

**Pipeline & logs** reflects actual validation, read/index or sort, comparison,
report writing and completion stages, not simulated progress. Review revisions
show their retained-evidence stages instead of pretending to reread sources.
The console keeps the latest 1,000 lines, with search, category filters/counts,
follow, pause, copy and complete-log download. Pause affects the display only;
scrolling upward stops following. Switching views preserves the running job.
Completed jobs offer immediate results and a visible five-second countdown.
Failed runs retain status, error, stage and logs.

**Job history** lists saved jobs newest first, 25 per page, using metadata rather
than scanning source files. In shared mode it includes all completed jobs plus
the creating browser's unfinished jobs. Open completed results and logs, or
resume your own partial upload/interrupted comparison when eligible. The browser
cookie controls unfinished-job ownership; clearing it does not hide shared
completed history but loses management access to the previous workspace. History
persists while the data directory and its retention policy retain the job.

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

HTML summaries and leadership HTML reports include container names, reasons and keys.
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
is treated as headers and receives the same blank/duplicate normalization as
CSV. The two inputs may have different column sets; comparison uses only common
names after applying the layout.

Sheet discovery reads workbook metadata only. The selected sheet is streamed
into a local UTF-8 CSV for the disk-backed comparison engine; shared strings
are indexed in a temporary SQLite database with a bounded cache. Only one heavy
import/comparison/export runs at a time. Import progress appears below the sheet
selectors and is retained in the job log. Reserve disk space for the original
workbooks, imported CSVs, sorting scratch, and reports. Large Excel imports have
not been benchmarked at 6 GB. The CLI remains CSV-only; Excel selection is in
the web app. No additional Python packages are required for Excel.

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

Choose two UTF-8 `.json` files in the shared Files picker (or server-path mode)
to open JSON comparison. The JSON workspace also supports pasted documents. Choose the default array behavior: preserve order compares
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
a silently truncated report. JSON uses its own serialized comparison slot; a
concurrent request receives a retry message.

## Batch console updates

New comparisons log each memory-bounded sorting batch as started, sorting, and
completed, including batch row count and elapsed sort/write time. Intermediate
merge passes report each merge batch and its total batches for that pass.
Comparison remains streaming: progress checkpoints start and complete every
10,000 processed keys, including the final partial checkpoint. These messages
include cumulative processed keys and changed cells; they do not imply loading
10,000 wide rows into memory. Total sorting batches are not guessed in advance.
The comparison console and downloadable run.log contain these messages. Existing
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

Upload checkpoints use the committed file length and a small pending-chunk journal
instead of replacing job metadata after every chunk. Partial chunks are rolled
back on interruption/restart; status never advertises uncommitted bytes. Temporary metadata files have unique names
and bounded retries for permission/sharing errors. A permanent lock still
requires closing the application holding the file or choosing a writable local
data directory; it is reported without replacing the previous metadata.
Interrupted requests reconcile the server offset before retrying, avoiding
duplicate bytes. Pause aborts the active request; Resume verifies the server checkpoint before
continuing from the saved offset. After reopening the browser, reselect the same source files to
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

### Column headers within Files & headers

After CSV/Parquet upload or Excel worksheet import, review headers in **Files & headers** before
choosing keys. Rename any header in either file to align differently named
columns, such as `AT02S` and `AT01S`. Header matching ignores case, so `AT01S`
and `at01s` match automatically. Column order may differ. Results and key/scope
lists use the spelling from file 1.

The editor searches and pages through loaded header metadata (50 names per
file per page). It does not read data rows or rewrite either uploaded file.
Apply validates that every header is nonempty and unique ignoring case, and
selects only the shared comparison names. Different column names/counts do not
fail the step when common columns remain; non-common columns are recorded as
out of scope. This step renames comparison headers without rewriting source data. Existing keys, ignored columns, and value overrides
follow renamed file 1 columns by position. Header layouts persist in job history
and become read-only when comparison starts. Cell values and key values remain
case-sensitive. Case-only duplicate CSV headers receive distinct numeric
suffixes during initial normalization.

### Side-by-side JSON results

JSON results show aligned code panes with original pretty-printed line numbers,
original paths on hover, and changed/added/removed highlighting. Next/previous
difference controls navigate every reported change; a context filter hides
unrelated equal lines. Matching honors preserve-order, ignore-order, and
match-by-field array rules, including rules for nested arrays. Reordered blocks
retain their original paths and line numbers. This is a comparison view, not a
merge editor; it does not modify the input JSON.

Only 200 aligned rows are rendered at a time to bound browser DOM work. Lines
longer than 2,000 characters are shortened in the interactive preview; JSON and
side-by-side HTML downloads contain full values. All existing input, nesting,
node-count, and difference-count limits still apply. Number text is preserved
by the server, including integers larger than JavaScript's exact numeric range.

### Built-in Docs & FAQ

Open **Docs & FAQ** under Help in the sidebar for six workflow guides and
15 troubleshooting answers. Search across all topics; matching FAQ answers
expand automatically. This page is bundled with the app, works without internet,
and stays accessible during uploads and comparisons. Clear the search to restore
all topics and use the topic links to jump to a section.

## Data cleanup

Job history includes a Delete action to remove a job and all uploaded copies,
scratch files, logs and reports. Original client files are unaffected. Automatic
cleanup runs at startup and hourly with a default retention of 7 inactive days,
including old uploads and drafts. Running/queued work and active file transfers
are protected. Download reports before expiry. Set `RETENTION_DAYS` in
`deploy.env` (or `--retention-days` when launching server.py); use 0 to disable
automatic expiry. This policy applies to existing jobs after upgrading. See
[deployment and retention details](DEPLOYMENT.md).

## Choosing JSON array keys

Paste or upload both JSON versions, click **Find arrays & choose keys**, and choose
a field for each array that may arrive in a different order. For example, choose
`id` for `students`: A1/A2/A3 and A1/A3/A2 then match, while changed marks are
still reported. Nested arrays can use independent keys such as
`students[*].subjects` → `code`. Arrays without a selected key retain pasted order
with the default settings. Advanced order rules remain available.

Only direct scalar fields present, non-null and unique in every occurrence of
an array on both sides are offered. No key is selected automatically. Editing
inputs keeps existing rules; find arrays again to review keys, or remove rules
in Advanced array rules. Discovery supports up to 100 distinct array paths.

## Comparison reports

The HTML dashboard is one offline file with metrics, exact match bands, all
mismatching/matching columns, coverage and highlighted ignored containers.
Click a mismatch column for a paginated pattern drawer, comments and up to 20
sample keys. Pattern counts include all retained mismatches for that column;
only displayed values/samples are shortened. Report generation streams pattern
data through a disk-backed cache rather than holding all pairs in memory.

Excel starts with **Comparison Summary** and **TOC**. Summary records metadata,
keys, source types, rules and totals. TOC has counts, match/mismatch rates,
comments and internal links. Each mismatching column has a sheet containing keys,
valueA, valueB, variable and Comments, plus Back to TOC. Types appear in Summary,
not as repeated typeA/typeB/diffAB detail columns. Values remain text and rates
use retained matched keys; zero matched keys yields N/A. Ignored containers and
analysis notes retain dedicated audit sheets.

Both reports are generated during Write results and refreshed for a new review
version. Notes invalidate stale exports; one click regenerates/downloads a stale
report. Previous exports can be regenerated after upgrading the server.

## Analysis and report progress

In Results, click a mismatching column to open its analysis drawer. Switch
between **Mismatches** and **Patterns**, or use the key filter to inspect a record. Single keys are literal text; composite
keys are JSON arrays of strings. Key-level results include changed keys and
keys found in only one file. Equal keys and excluded keys are not indexed.

The first detail/key query queues an on-disk SQLite index using the shared
heavy-job worker limit. This needs additional disk space; it is removed with
the job. Results page 50 rows at a time and cap displayed values at 1,000
characters. Full exported values are unchanged. Indexing progress appears in
the analysis drawer, with a retry option if preparation fails. Active analysis
preparation is protected from cleanup, just like comparison and export.

Export buttons show queued/generating status with an accessible spinner and
live progress messages. Ignore-key containers and reasons are highlighted in
HTML dashboard/drawer callouts and the Excel audit sheet, with a link from
Comparison Summary.

## Team workflow additions

- **Storage & queue:** workspace-owned jobs grouped by upload, report, analysis,
  and temporary/metadata bytes; global server free space; queued/running tasks;
  cancel controls and confirmed job deletion. Refresh to update the snapshot.
  Upload preflight estimates 3× combined input size and warns when space is tight;
  this is not a quota, reservation, or worst-case guarantee.
- **Cancellation:** queued work is removed immediately. Active comparisons,
  worksheet imports, exports and analysis stop at safe checkpoints, so large
  sorts or ZIP operations may take time to finish their current operation.
  Comparison/import cancellation requires a new comparison; cancelled exports
  and analysis can be generated again. Uploaded files remain until job cleanup.
- **Profiles:** save/apply/delete named profiles in the Profiles page; select one on the file-upload page. They include
  header aliases by original name, keys, ignored columns/keys/containers, value
  overrides and optional comparison rules. Performance is sized automatically at runtime. Profiles are private
  to the browser workspace; saving the same name replaces it. Missing profile
  columns or containers produce a validation error before comparison.
- **Analysis notes:** classify a column, key, or key/column pair as Expected,
  Needs investigation, or Resolved, with comments. Notes do not alter results.
  Excel includes an Analysis notes sheet and column notes in TOC; HTML includes
  the full notes table. Changing notes invalidates older exports. Editing notes
  is blocked while an export runs. Notes are limited to 2 MiB per job.
- **Duplicate keys:** comparison continues and reports the first duplicate group's exact
  count, key, and up to three sampled records (20 compared columns, 500 characters
  per value). The bounded diagnostic is supplemented by a full duplicate-key CSV audit.
- **Column rules:** optional whitespace trimming, case folding, absolute decimal
  tolerance, or explicit per-file date formats. Key values remain exact text.
  Invalid numeric/date values stay mismatches; exact equal text still matches.
  Original text is retained in reports. Configured rules and accepted-cell counts
  are recorded in the summary. Numeric diagnostics are bounded to 1,000-character
  values and exponents within ±1,000; more extreme unequal values remain mismatches.
- **Notifications:** opt in under Storage & queue. Browser permission is required,
  and remote servers generally need HTTPS for desktop notifications. The app
  polls every 10 seconds while the page is open; closing the page stops alerts.
  Disable notifications with the adjacent button. Alerts contain task type/status,
  not source values or filenames.

Profiles are limited to 100 entries / 5 MiB per workspace. Notifications and UI
logic have automated checks, but browser permissions and performance on your
Windows/on-prem deployment still need a representative pilot.

### Batch settings and duplicate handling

The application automatically chooses read/sort and comparison batch sizes; Keys & scope retains first/last source occurrence for duplicates. CLI equivalents: `--read-batch-size`, `--compare-batch-size`, `--duplicate-policy`. Batch sizes accept integers from 1 to 1,000,000. Sorting flushes at the row cap or memory budget, whichever comes first. Comparison remains streaming; its batch setting controls progress reporting. Duplicate counts and skipped rows are shown in Results and exports. Match percentages use only retained rows.

### Column analysis and comments

Click a column in Results to open **Mismatches**. Use **Patterns** to group exact
File 1 → File 2 pairs with counts and percentage share in the current column.
**List keys** returns to Mismatches filtered to that exact pair; the visible
filter can be cleared without losing the selected column. Key filtering remains
available in the drawer. **All changes for this key** shows its mismatches across
columns. Values are shaded separately and empty strings are labelled.

A column note remains visible as context for every key reviewed in that column.
A key comment follows the key across all mismatching columns. Cell-specific
comments remain scoped to that key/column. Comments annotate results and appear
in HTML and Excel; they do not change comparison equality.

Run `node test_analysis_ui.cjs` for navigation interaction checks, in addition
to the Python analysis API tests. These are DOM simulation checks, not a browser
rendering test.

### Resume interrupted comparisons

New comparisons keep durable, job-local sort checkpoints. After a restart, open
the job in Job history and choose **Resume from saved sort batches** in Pipeline
& logs. SHA-256 checks validate both full source files and saved runs before reuse;
this requires sequential disk reads, but avoids reparsing and sorting completed
records. UTF-8/CSV multiline record boundaries are preserved using text-stream
seek positions. An incomplete sort batch or merge pass is repeated. The final
comparison is restarted from sorted data and partial result CSVs are replaced,
never appended. Changed sources/settings or corrupt/missing runs reject resume.
Worker and sorter locks prevent concurrent reuse, including surviving child
processes after a forced restart. Keep the same Python runtime when resuming.

Checkpoints consume disk until success, job deletion, or normal retention cleanup.
During merge, a completed checkpoint remains until the next merge pass commits,
so allow additional scratch space. Successful server jobs remove their checkpoints.
Jobs started before this feature have no resumable checkpoints. Source validation
and checkpoint creation add I/O; 6 GB performance has not been benchmarked.

### Mismatch patterns and numeric formatting

In the column analysis drawer, choose **Patterns**. Recurring File 1 → File 2 pairs are
ranked by occurrence count, with a column filter and per-category totals. Each
mismatched cell belongs to one category, in this order: blank → value, value →
blank, whitespace only, case only, case plus whitespace, numeric formatting, or
other value change. Patterns describe mismatches; they do not accept or remove them.
Preparing analysis stores pattern counts in the disk-backed index. Existing
indexes rebuild on demand. Displayed values are capped at 1,000 characters;
full-value hashes keep distinct long value pairs separate.

To accept `0` / `0.00` and `-11` / `-11.00`, open Comparison rules in Keys & scope and choose a value column,
tick **Ignore numeric formatting**, then save the column rule. This uses exact
decimal equality (zero tolerance). Keys and other columns retain their configured
comparison rules; leading-zero numeric identifiers should remain exact text.
Use Recompare on a completed job to apply new rules to retained evidence when
the review plan confirms coverage.

HTML sample controls use native expandable dropdowns, without requiring JavaScript
to show the 20 key samples. Regenerate old exports to get the updated controls.

### Workspace settings and comments

Resource settings are calculated at worker launch. Comparison rules are chosen in Keys & scope and can be saved in profiles. In team mode, active jobs and reusable profiles/containers stay browser scoped; completed history and Projects are shared. Rule names match common column headers without case sensitivity; key, ignored and absent columns are skipped. Explicit profile rules take precedence over legacy saved defaults. Completed runs retain their original rules. Save a numeric tolerance of zero to treat `0` and `0.00` as equivalent.

Analysis shows a browsable, paged list of mismatching columns, with optional filtering. Comments can apply to a whole column, a key across all columns, or a single key/column pair. Existing comments can be viewed and edited. Key comments appear alongside every mismatch for that key in analysis and report samples, and in the corresponding Excel mismatch sheets.

### Compact review workspace

Navigation starts collapsed on each page load. Expand it using the bottom toggle; narrow screens use a drawer, and reduced-motion preferences disable the transition. The expanded logo sits directly on the navy background. Redundant sidebar storage text and the upload-page disk-check button have been removed; storage management remains in Storage & queue.

Analysis opens in a right drawer with compact metrics, Mismatches/Patterns tabs, expandable key filters, and a bounded mismatch table. Comments and findings show their scope explicitly; saved comments expand separately.

Keys & scope and Column headers keep explanatory text in help disclosures available on hover, keyboard focus or click. Header apply/reset actions sit at the top. Desktop scope setup uses three bounded panels with Start comparison at the top-right, matching Column headers.

Choose an optional profile in Files & headers. It applies automatically once CSV headers or the selected Excel worksheets are ready; schema errors are reported for manual correction. Header rows display the original name and editable comparison name side by side.

The profile picker is the template icon beside upload status, after the two file cards. The primary upload/header actions are at the top right. Preview is offered after headers are applied. Profile selection stays in Files & headers.
# Enterprise workspace design

The UI uses a shared TransUnion-inspired visual system: navy navigation, the existing TransUnion wordmark and compact TU mark, cyan accents, yellow primary actions and light data surfaces. The sidebar starts collapsed. Expanded branding keeps clear spacing between the logo and Data Compare label.

CSV/Excel/Parquet use four stages: Files & headers, Keys & scope, Pipeline & logs, and Results, with meaningful icons in the vertical review rail. File preview is optional after applying headers, profile selection remains in Files & headers, and selecting a mismatching column opens the analysis drawer. Recompare uses tabs for scope, overrides and column rules. JSON separates Inputs, Array alignment and Comparison; successful comparisons open the side-by-side result automatically.

Performance is automatic; display and accessibility preferences are under Docs & FAQ. Comparison rules live in the top-right drawer on Keys & scope and can be adjusted in Recompare. Select one, several, or all current value columns and combine whitespace, case, and numeric/date rules. Keys and ignored columns are excluded. Explicit comparison rules override legacy workspace rule defaults, including an empty configuration. Storage & queue is accessible from Docs & FAQ. Resource budgets are chosen at worker launch; motion and density preferences are local to the browser. Shortcuts use the same standard mapping throughout the app. Container creation and the saved library are side by side on desktop. Docs retain searchable content in expandable guides.

Keyboard: Tab / Shift+Tab move between controls; Enter / Space activate them; arrows and Home / End navigate tab and phase controls. Alt+B toggles navigation, Alt+1–4 opens an available comparison stage, Alt+F focuses the current search, Alt+R opens recomparison, and Alt+/ opens the shortcut guide. On Mac, use Option in place of Alt; physical-key handling preserves these shortcuts with Mac keyboard layouts. Escape closes dialogs. Shortcuts never start, cancel, delete or export a job. Operating-system and browser shortcuts may take priority. Reduced motion follows the system preference or can be selected in Docs & FAQ → Display & accessibility.

All pages reflow at narrow widths. Data tables and long lists use bounded scrolling; controls remain reachable rather than being clipped to enforce a fixed page height.

The shared Files picker detects CSV, Excel, Parquet and JSON by file extension and validates JSON syntax before opening the JSON workspace. JSON pairs use the existing array alignment and side-by-side comparison flow (5 MiB per input). CSV/Excel pairs retain sheet selection, headers and key comparison. Mixed JSON/tabular pairs are rejected. JSON remains session-only and is not added to tabular Job history.

## Parquet comparisons

The shared Files picker accepts `.parquet` alongside CSV, Excel and JSON. Install Parquet support with the server's 64-bit Python (Python 3.10+):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

On Windows use `.venv\Scripts\python.exe -m pip install -r requirements.txt`. The launchers prefer this environment when it exists. Restart a running server after installing or updating dependencies. Tested with PyArrow 25.0.1.

Parquet headers come from schema metadata. The comparison worker reads only selected key/value columns using Arrow batches, then feeds the existing bounded external sort and merge engine directly. It never converts the whole file to CSV or loads a full dataframe. CSV/Parquet and Excel/Parquet pairs are supported too. Native batch rows adapt to the selected columns' uncompressed metadata estimate and share the configured read/sort limit. File sorting concurrency, cancellation, duplicate handling, rules, reports and restart checkpoints still apply. Resuming skips completed row groups and resumes inside the next group; existing source fingerprint validation is retained.

Parquet values are rendered deterministically as text: booleans `true`/`false`, dates/times in ISO form, binary in base64, nested values as JSON (object fields sorted, array order preserved). Physical numeric widths are not treated as differences; numeric-format rules are optional as with CSV. Null is distinct from empty text and is displayed as `[NULL]` in reports; a literal string `[NULL]` is marked `[NULL] (text)` in mismatches. Null keys are rejected. Duplicate Parquet field names must be fixed before upload; ordinary blank/case-colliding headers use the existing header normalization.

The sort budget is not a process-wide RAM cap: Arrow buffers, schema metadata, decompression and unusually large individual cells add overhead. Compressed inputs may expand substantially in scratch storage. Use a fast local SSD, keep server concurrency conservative, and benchmark representative files before committing to a 6 GB SLA.

```sh
.venv/bin/python benchmark.py --format parquet --output benchmark-parquet --rows 10000 --columns 2000 --memory-mb 64 --sort-workers 1
```

This generates shuffled keys and known mismatches, validates counts, and saves timing/input/scratch measurements. Increase `--rows` for a representative machine test. Timing excludes generation and export; RAM is not measured by this script.

## Projects and audit workspace

Projects provides a compact desktop dashboard with comparison counts, manually recorded audit statuses, a latest-run difference metric, a difference trend and an event timeline. Large lists scroll within the workspace; create, attach and review actions use keyboard-accessible drawers. Smaller screens reflow. Use **Attach to project** on a result, or **Projects → Attach comparison** to assign an existing comparison. One comparison belongs to at most one project. Recomparison inherits the project but starts with an Unreviewed audit.

Audit statuses are Unreviewed, In review, Approved and Changes requested. Approval requires a completed comparison and does not change computed mismatch results. Reviewer names are optional labels, not authenticated identities. Projects and completed comparison history are shared across the team; no login was added. The creator browser controls a tabular job's project/audit assignment; teammates can create their own review revision. The focused Results attachment drawer does not duplicate the Projects audit form. JSON comparisons persist a compact audit summary, not the original JSON, full differences or rendered report. CSV/Excel/Parquet results remain accessible until normal job cleanup; cleanup preserves project audit summaries and marks the source/report as removed.

Project records and events are stored in one shared `projects.sqlite` database. Existing per-workspace project databases are migrated once at startup. The dashboard reads metadata only; it does not scan source CSV/Parquet files. Project charts report differing cells for tabular runs and changed/added/removed nodes for JSON, not a combined cross-format match percentage. Use Refresh to load changes while viewing a project. Existing tabular jobs are indexed on the first Projects visit.

## Review revisions

New runs retain `raw_differences.csv` (including unequal values accepted by rules
and explicit null flags) and a matched-key inventory. Applying rules creates an
independent review revision, preserves the original, inherits its project as
Unreviewed, and regenerates both reports. It streams retained evidence with
disk-backed distinct-key counting, without opening or sorting source files.
Supported reviews still work when the original sources are unavailable.

Rules can be added, removed or tightened. Additional column/key exclusions and
ignore-container selections can be applied; revision-only exclusions can be
restored from the unchanged baseline. Original key columns remain locked in the
dialog. Key, duplicate-selection, header/source changes, or restoration of data
excluded from the original baseline require a full comparison. Older jobs without
raw evidence require one full run. The review plan explains this before execution.

Retained evidence increases disk use, especially for widespread mismatches, and
follows job cleanup. Revisions hard-link immutable baseline files and have their
own annotations/results. Counts distinguish original mismatches, raw differences,
accepted values, excluded values and remaining mismatches. Accepted values may
exceed original mismatches when the original already had rules.

The current Results interface offers only the HTML report and Excel workbook.
AI evidence package controls are deferred from the UI; the existing backend
package functionality is not part of this workflow.

## Wide-file performance validation

The Parquet reader caches the Arrow schema and packs string, integer and boolean columns with native Arrow kernels, materializing only keys and packed rows in Python. Other types retain the existing text conversion to preserve comparison semantics. Nulls and embedded separators remain distinct. CSV, Excel and Parquet share a cheaper null check and sort-run checksums calculated during writes, avoiding a second scratch-file read. Resume still verifies saved checksums. Reading and external key sorting remain necessary in this implementation; it does not assume row order or equal row counts.

Measured on local Mac storage with synthetic integer Parquet data (not a server SLA):

| Fixture | Original engine | Optimized engine |
| --- | ---: | ---: |
| 100,000 rows × 694 columns per file, shuffled keys, 100,000 changed cells | 35.777 s | 10.366 s |
| 500,000 / 499,200 rows × 694 columns, independently shuffled keys, checkpoints enabled | Not measured | 39.244 s |

The larger fixture used two approximately 2.35 GB files, a 512 MB sort budget and two sort workers. It correctly reported 499,000 changed cells, 1,000 left-only keys and 200 right-only keys. Including the Excel workbook and HTML report took 46.115 s. Timings exclude upload and fixture generation. The smaller comparison excludes reports and checkpoints. Text distributions, nested values, Excel parsing, mismatch volume, storage and concurrent jobs can materially change performance; two minutes is not guaranteed for every format.

Run `python benchmark_wide_parquet.py --output <new-directory>` to reproduce the larger fixture, comparison and report timings. This consumes several GB of disk space. Scratch files using the new null bitmap format require this version or newer to resume; when rolling back, start a fresh comparison.

### Additional Parquet read/sort tuning

Native packing now allows a working-set target of up to 64 MiB (still capped at one quarter of the file worker's sort budget, with the same eightfold metadata expansion allowance and configured read-row limit). This amortizes per-column kernel setup for wide inputs. Decimals with nonnegative scale use Arrow string conversion; float conversion retains Python formatting with less dispatch overhead. Negative-scale decimals retain the reference conversion. Exact-format tests cover decimal scales through 38, nulls, signed zero, NaN/infinity and float32/float64.

Compared with commit `84d59d0`, local read-and-pack microbenchmarks improved from 3.368 s to 2.167 s for 100,000 × 694 integer values and from 6.008 s to 2.194 s for 20,000 × 694 mixed decimal/float values. These timings exclude sorting, validation and reports. The larger 500,000/499,200-row integer fixture above completed comparison in 32.983 s, and comparison plus both reports in 39.857 s, with identical expected counts. Timings are individual synthetic runs on local storage, not promised production speedups. Decoding threading remains unchanged because the measured benefit was small.

## CSV and HDD performance

Ordinary unquoted CSV rows with the same selected column order are now packed directly: delimiter counting and replacement run without creating a Python object for every cell. Keys remain exact text. Quoted/multiline records, embedded payload separators, projected/reordered columns and large records use the existing strict CSV parsing semantics. UTF encodings, field limits and text-stream checkpoint positions are preserved. Logs report direct/parsed record counts and each file's total read/sort duration plus retained scratch bytes. Excel's imported CSV also benefits when eligible.

All tabular formats now write losslessly compressed temporary sort runs in bounded 4 MiB blocks. PyArrow LZ4 is preferred when installed, with standard-library zlib as fallback. Blocks without useful compression stay raw. Merge reads decompress one block at a time, and checkpoint hashes cover the actual stored bytes. Existing uncompressed checkpoints remain readable. Rolling back to an older app requires fresh comparisons if saved runs use the new format. Compression adds bounded buffers (up to 4 MiB decoded data per open merge run, plus transient compression/decompression buffers); the sort budget remains distinct from a process-wide RAM cap.

For a **32 GB machine with an HDD**, use the automatic resource sizing and sequential source reads. Batches still flush on reaching the memory budget. Keep heavy-job concurrency at one for a single physical HDD; five browser users can queue independent jobs without five simultaneous disk-intensive workers. Parallel sorting remains suitable for an SSD when measured beneficial. Keeping sources and scratch on an SSD gives a larger I/O improvement than increasing comparison progress-batch size. Excel ZIP/XML import must still run, and every input must still be read.

Local synthetic 100,000 × 1,780 CSV comparison (shuffled keys, identical values, 512 MB budget, sequential sorting, no checkpoints/reports) improved from 12.863 s to 2.287 s with direct packing alone. This uses short numeric text and repeated patterns, and is not an HDD timing guarantee. Quoted/projected data follows the parser fallback; compression benefits depend on the data.

To measure the complete pipeline on the deployment machine:

```sh
python benchmark.py --format csv --output benchmark-csv-million --rows 1000000 --columns 1780 --memory-mb 4096 --sort-workers 1 --checkpoints --reports
```

Use a new output directory and allow space for two inputs, scratch and reports. The script checks expected mismatch counts and records comparison and total elapsed time separately; fixture creation and upload are excluded. Never disable validation or drop differences to meet a time target.

The 1,000,000 × 1,780 fixture (5.16 GB per input, 100,000 changed cells, independently shuffled keys) completed comparison in 37.701 s and comparison plus reports in 38.998 s on local Mac storage with a 512 MB sort budget, sequential sorting and checkpoints enabled. Retained compressed scratch was 90,761,291 bytes. The unusually high compression comes from repeated numeric patterns; real data may compress much less, and these are not measurements on an HDD.

The existing 500,000-row × 694-column integer Parquet benchmark remained comparable after enabling shared compression: 33.710 s comparison / 40.708 s including reports, versus 32.983 / 39.857 s before. The purpose of compression is to reduce scratch I/O on slower disks; it can add a small CPU overhead on fast storage.

Read progress is logged every 50,000 source rows for CSV, imported Excel and Parquet. These updates do not sort or flush a batch. Read/sort batches are automatically sized up to 100,000 rows; the memory budget may flush smaller batches. Comparison progress batches are sized up to 10,000 keys.

## Dense mismatch results

All mismatch evidence is still written to the result files. At 50–60% of 1,780 million cells, result volume can approach one billion records; writing those records and creating a full Excel workbook remains substantial work. The app does not silently sample full exports or guarantee two-minute completion.

Validation on a local Mac: 2,000 shuffled rows × 1,780 total columns (one key), exactly 2,134,800 changed cells. Automatic hash comparison took 2.110 seconds versus 2.266 seconds for disk sorting, with checkpoint validation enabled; all six output CSV files had identical SHA-256 hashes. Each run wrote 166.6 MB of evidence. This small benchmark excludes HTML/Excel generation and does not predict 6 GB HDD performance. `benchmark_mismatch_scan.py` separately measures only sparse/dense cell scanning.

### Large uploads over the team network

The browser compresses CSV chunks with native `CompressionStream('gzip')`, when
available, and sends the compressed bytes to the workspace server. The server
restores the **exact original bytes** before parsing: encoding, quoting, line
endings and numeric text are unchanged. A bounded first chunk tests whether
compression reduces payload size by at least 15%; otherwise the transfer uses raw
bytes. Excel and Parquet use the direct path. This is upload compression, not a
conversion of the file format. No whole-file buffer or third-party upload service
is used. The server still needs disk space for the original, uncompressed files.

The Files view shows per-file saved progress, estimated remaining time and
original-file throughput (including compression). Payload savings describe
acknowledged chunks of the current file in this session; retry traffic is not
included in that percentage. Compression costs CPU and may not improve elapsed
time on a fast LAN. Measure on representative files and the actual network.

Chunks adapt between 256 KiB and 8 MiB. The client retries transient failures,
checks the server's acknowledged offset after an uncertain response, waits while
o previous chunk is still being written, and pauses on browser-reported offline
status. A progressing slow upload is not stopped by a fixed total timeout;
a 60-second inactivity watchdog still detects a stalled connection. Retry limits
preserve a manual Resume action when recovery fails. Pause cancels the current
request; it may already have been committed, so Resume checks the server.

Keep the tab open while uploading. After a page/browser restart, open the job in
Job history and reselect the same **unchanged** files to resume. Name and size
checks do not prove content identity; selecting a different file with the same
name and size is not supported. Starting a fresh job does not deduplicate existing
uploads. Recompare already reuses its job's source data. Uploads remain sequential
within a job to avoid concurrent HDD writes; multiple team workspaces remain
independent. This custom chunk protocol does not implement the tus specification.
