# Job API design

Status: agreed design, ready to build. Nothing here is built yet. Section 12 records the decisions behind it.

## 1. What it is for

Scripts (Python, PowerShell, or a tool such as Postman) submit a comparison without the browser:

1. The script sends the two input file paths, the comparison settings and an output folder path.
2. The service checks everything it can straight away and answers with a job ID, or with a precise error.
3. The comparison runs on the same engine, queue and limits as the browser app.
4. The results are written into the output folder all at once, with a `_SUCCESS` marker file that says the folder
   is complete.
5. The script polls the job's status until it has finished (section 5), then reads the results.

API jobs are ordinary jobs. They appear in Job history and can be attached to a project, opened on the Analysis
page, recompared and exported, exactly like a job started in the browser.

Out of scope for version 1: uploading file contents through the API (paths only), JSON comparisons, compressed
inputs (`.gz`, `.zip`), paths on other machines' shares that the server has not mounted, and webhooks
(section 11 lists them as later work).

## 2. Access and allowed folders

### No sign-in

The API has no authentication, like the browser app today: anyone who can reach the server can submit, see and
cancel API jobs, from a script, `curl` or Postman. The server must therefore stay on a trusted internal network,
as `DEPLOYMENT.md` already says for team mode. What still protects the server:

- **Allowed folders.** Inputs are read only from the input folders, results are written only to the output
  folders (below), and every path is checked after resolving `..` and links. No request can read or write
  anywhere else.
- **Limits.** At most 50 API jobs waiting in the queue and at most 5 waiting from one machine, so one caller
  cannot fill the queue (`429 QUEUE_FULL`). Running jobs share the existing `--max-jobs` slots with the browser.
- **Audit log.** Every API call is written to `api-audit.jsonl`: time, the caller's address, method, path, job
  ID and result code.
- **Nothing to run.** No request field is ever passed to a shell or used as code.

Adding API keys later is a small change: one header in each script (`Authorization: Bearer <key>`), one field in
Postman. The endpoints and bodies stay the same.

The browser app's own session token and same-origin checks are unchanged. `/api/v1/*` is a separate set of
routes that does not use the browser token or cookie.

### Allowed folders

All paths are as the server sees them: its local disks and shares already mounted on it.

- **Inputs** must be inside one of the existing server source folders (`source-roots.txt`).
- **Outputs** must be inside an output folder listed in the new `output-roots.txt` (same format, one folder per
  line, git-ignored). Without any output folder the API is turned off and answers `503 API_DISABLED`.
- The data folder (`--data-dir`) and the app's own folder can never be output folders.

## 3. Endpoints

All under `/api/v1`. JSON in, JSON out, UTF-8. Times are ISO 8601 in UTC.

| Method and path | What it does |
|---|---|
| `POST /jobs/validate` | Runs every check a submission would (paths, formats, headers, keys, rules, output) and returns what it found. Starts nothing. |
| `POST /jobs` | Validates, then queues the job. `202 Accepted` with the job and a `Location` header. |
| `GET /jobs/{id}` | Status, stage, progress, and the results once finished. |
| `GET /jobs?state=&reference=&since=&limit=&cursor=` | API jobs, newest first. |
| `POST /jobs/{id}/cancel` | Requests cancellation. Calling it twice is harmless. |
| `GET /jobs/{id}/log?after=` | The run log from a line number, for following a run. |
| `GET /health` | Whether the service is up, its version and the queue length. |
| `GET /capabilities` | Supported formats, limits and rule types, so scripts can adapt. |
| `GET /openapi.json` | The OpenAPI 3.1 description of this API (Postman can import it). |

## 4. Submitting a job

```json
{
  "name": "Ledger September vs October",
  "reference": "ledger-2026-10",
  "inputs": {
    "left":  { "path": "/data/extracts/ledger_sep.csv" },
    "right": { "path": "/data/extracts/ledger_oct.dat", "delimiter": "|", "encoding": "cp1252" }
  },
  "keys": ["account_id"],
  "columns": {
    "rename": { "right": { "ACCT_ID": "account_id" } },
    "ignore": ["load_date", "batch_id"]
  },
  "exclude_keys": { "keys": "AC0000017, AC0000233", "containers": ["NO_HIT_KEYS"] },
  "comparison_rules": [
    { "columns": ["balance", "credit_limit"], "type": "number", "tolerance": "0.01" },
    { "columns": ["customer_name"], "trim": true, "ignore_case": true }
  ],
  "value_overrides": [ { "column": "status", "left": "ACTIVE", "right": "A" } ],
  "duplicate_keys": "first",
  "template": "Monthly ledger",
  "output": {
    "path": "/data/compare-results/ledger/2026-10",
    "if_exists": "new_folder",
    "excel": { "max_rows": 10000, "truncate": true }
  },
  "checks": { "max_changed_cells": 0, "max_left_only": 0, "max_right_only": 0, "fail_on_duplicate_keys": true },
  "project": { "name": "Monthly ledger", "create": false },
  "wait_for_inputs": { "stable_seconds": 60, "timeout_minutes": 30 },
  "timeout_minutes": 240
}
```

Only `inputs`, `keys` and `output.path` are required.

| Field | Meaning |
|---|---|
| `name` | Shown in Job history. Defaults to the two file names. |
| `reference` | The script's own ID. Unique among unfinished jobs; used to find a job and to stop the same job being submitted twice. |
| `inputs.left` / `inputs.right` | File 1 (champion) and File 2 (challenger). `path` is required. `format` is detected from the extension unless given: `csv`, `delimited`, `excel`, `parquet`, `fixed_width`. |
| `inputs.*.delimiter`, `encoding`, `header` | Optional; detected otherwise, as in the browser app. `header: false` needs `column_names` or `use_header_from: "left"`/`"right"`. |
| `inputs.*.sheet` | Excel only. Required when the workbook has more than one sheet. |
| `inputs.*.fixed_width` | Fixed-width only: `layout` (inline, or `layout_path` inside an input folder), `record_length`, `trim`. |
| `keys` | Key columns, in order. Names after renaming. |
| `columns.rename` | Per side, old name → new name, applied before matching. |
| `columns.ignore` | Columns left out of the comparison. |
| `exclude_keys` | Keys skipped in both files: typed `keys` (as in the app) and saved `containers` by name. |
| `comparison_rules` | The app's rules; `columns` may be a list or `"*"` for every compared column. |
| `value_overrides` | Value pairs treated as equal in a column. |
| `duplicate_keys` | `first` (default), `last`, or `fail` (new: stop with `DUPLICATE_KEYS`). |
| `template` | A saved template by name. Fields in the request override the template's. |
| `output.path` | Folder the results are written to. See section 6. |
| `output.if_exists` | `new_folder` (default), `fail`, or `replace`. |
| `output.files` | Which files to write. Default: every file in section 6 except Excel. |
| `output.excel` | Adds `mismatches.xlsx`, with the same options as the Export menu. |
| `output.patterns` | `max_per_column` for `patterns.csv` (default: no cap). |
| `checks` | Pass or fail limits for scripts. They never change the comparison itself. |
| `project` | Attach the finished job to a project by name; `create: true` creates it if missing. |
| `wait_for_inputs` | Wait until both files exist and have not changed for `stable_seconds`, for producers still writing them. |
| `timeout_minutes` | Cancel the job if it has not finished in this time. |

`POST /jobs/validate` takes the same body and returns what the service found: each input's resolved path,
format, size, delimiter, encoding, header row, column count and first 50 column names; the keys and their
positions; columns found in only one file; renamed duplicate or blank headers; the final output folder; and any
warnings.

### What is checked when

Checked before `202` is returned (all quick, no full file reads):

- the body against the schema, with every problem reported in one response;
- both input paths: inside an allowed folder, exist, readable, a regular file, supported, within size limits;
- the header row of each file (Excel: the sheet names), so a missing key column is caught now;
- keys, renames, ignored columns, rules, overrides, containers and the template;
- the output path: inside an output folder, `if_exists`, write permission, and the reservation (section 6).

Checked when the job starts: the input fingerprints (unchanged since submission), the Excel sheet contents and free
disk space.

## 5. Job states and polling

```
            ┌────────────── cancel ───────────────┐
            │                                     ▼
queued ─▶ waiting_for_inputs ─▶ running ─▶ publishing ─▶ completed
            │                     │            │
            └─────────────────────┴────────────┴──────▶ failed / cancelled / timed_out
```

- `queued`: accepted, waiting for a free slot.
- `waiting_for_inputs`: only with `wait_for_inputs`.
- `running`: the app's stages (validate, read and index, compare, write results). `progress.stage` and
  `progress.percent` are the numbers the pipeline page shows.
- `publishing`: writing the results into the output folder (section 6).
- `completed` always means the output folder is complete. `outcome` then says whether the `checks` passed:
  `passed` or `failed_checks`.

A script polls `GET /jobs/{id}` every 5 to 30 seconds until the state is `completed`, `failed`, `cancelled` or
`timed_out`. Each response includes `poll_after_seconds`, a suggested wait that grows for long jobs.

Status response:

```json
{
  "id": "6f0c2a9e4b1d4c7f9a3e2b8d1c0f5a6e",
  "reference": "ledger-2026-10",
  "state": "completed",
  "outcome": "failed_checks",
  "failed_checks": ["max_changed_cells: 1,290 > 0"],
  "submitted_at": "2026-10-26T09:00:04Z",
  "started_at": "2026-10-26T09:00:05Z",
  "finished_at": "2026-10-26T09:03:41Z",
  "attempt": 1,
  "progress": { "stage": "completed", "percent": 100 },
  "summary": { "matched_keys": 499812, "equal_rows": 498528, "changed_rows": 1284, "changed_cells": 1290,
               "left_only": 188, "right_only": 3, "duplicate_keys": 0,
               "changed_cells_by_column": { "balance": 1201, "status": 89 } },
  "output": { "path": "/data/compare-results/ledger/2026-10",
              "files": [ { "name": "differences.csv", "bytes": 81233, "sha256": "…" } ] },
  "links": { "self": "/api/v1/jobs/6f0c…", "app": "https://compare.internal/#review=6f0c…" }
}
```

The response carries counts only. Mismatching values are never sent back through the API; they are in the output
folder.

### Example script

`docs/examples/compare_job.py` (standard library only) will submit a job, poll it, print progress and exit with 0
when the job completed and its checks passed, 1 otherwise. A matching PowerShell script and a Postman collection
(`docs/examples/job-api.postman_collection.json`) will cover submit, validate, status, list and cancel.

## 6. The output folder

### Files

```
/data/compare-results/ledger/2026-10/
  _SUCCESS                  written last; its presence means the folder is complete
  manifest.json             every file with its size and SHA-256, the job ID, versions and settings
  summary.json              counts, per-column counts, settings, input fingerprints, check results
  differences.csv           one row per mismatching cell
  patterns.csv              one row per distinct mismatch per column, with its count
  only_in_left.csv          keys found only in File 1
  only_in_right.csv         keys found only in File 2
  duplicate_keys.csv        only when duplicates were found
  comparison-report.html    the report from the Export menu
  mismatches.xlsx           only when output.excel is set
  run.log
```

On failure the folder holds `_FAILED` and `error.json` (code, message, stage) plus `run.log`, so a script that
watches the folder sees the outcome either way.

### Keys are written as their own columns

In the API's CSV files each key column is its own column, with its real name (the browser app's files use one
`key_json` column instead). With a two-column key, both columns come first.

### differences.csv

One row per cell that differs. Cells that match, and cells accepted by a rule or override, are not written.

```
account_id,column,file1_value,file2_value
AC000032,balance,8098.31,8099.31
AC000032,status,ACTIVE,A
AC000037,balance,5536.73,5537.73
```

Values are written in full. An empty cell is empty; a real null (Parquet) is `[NULL]`.

### patterns.csv

One row for each distinct pair of values that differ in a column: 1,284 rows of `status ACTIVE → A` become one
row with a count of 1,284. It is the grouping the HTML report's pattern drawer uses (`report-patterns.sqlite`),
written out in full.

```
column,file1_value,file2_value,count,share_of_column,kind,example_account_id
status,ACTIVE,A,1284,93.24%,other,AC000011
status,CLOSED,C,93,6.76%,other,AC000407
customer_name,John Smith,JOHN SMITH,420,61.40%,case_only,AC000019
customer_name,Mary Jones ,Mary Jones,264,38.60%,spaces_only,AC000102
balance,1200.00,1200,57,4.75%,number_format,AC000230
balance,8098.31,8099.31,1,0.08%,number_differs,AC000032
email,,old@x.com,120,100.00%,empty_vs_value,AC000025
```

- **Order:** columns with the most mismatches first; within a column, the most common pattern first.
- **`count`** is how many cells have exactly this pair; `share_of_column` is its share of that column's mismatches.
- **`example_<key>`** columns hold the first key with this pattern, so it can be found in `differences.csv`.
- **`kind`** classifies the difference: `case_only`, `spaces_only`, `number_format` (the same number written
  differently), `number_differs`, `empty_vs_value` or `other`.
- **Values** are written up to 1,000 characters; longer values end in `… [truncated]`, as in the HTML report.
  `differences.csv` always has them in full.
- **Size:** a column whose values are unique on every row (a load timestamp, say) has as many patterns as
  mismatches. `output.patterns.max_per_column` can cap it; a capped column ends with one `(other patterns)` row
  carrying the remaining count, so the counts still add up.

### only_in_left.csv, only_in_right.csv and duplicate_keys.csv

The key columns of each key found in only one file. `duplicate_keys.csv` has `side`, the key columns,
`occurrences`, `skipped_rows` and `kept_occurrence`.

### Writing it safely

- **Reservation:** at submission the service reserves the output path, so two unfinished jobs can never write to
  the same folder (`OUTPUT_IN_USE`).
- **If the folder exists** (the default, `new_folder`): the job writes to the same name with `-2`, `-3`, …
  added, and the status and `manifest.json` give the folder actually used. `fail` refuses the job instead;
  `replace` replaces the folder only if this service wrote it (its `manifest.json` says so), never a folder
  someone else made (`OUTPUT_NOT_OURS`).
- **Staging:** results are written to a hidden folder next to the target, `.2026-10.partial-6f0c…`, then renamed
  to the final name in one step, and `_SUCCESS` is written last. A reader never sees a half-written folder under
  the final name. When the data folder is on another disk, files are copied into staging first.
- **Inputs are never touched:** an output path that is or contains an input file is refused
  (`OUTPUT_OVERLAPS_INPUT`).
- **Cancel or failure** removes the staging folder and leaves any existing output untouched.

### Retention

The output folder belongs to the caller; the service never deletes it. The job's own copy in the data folder
follows `--retention-days` as today, so the Analysis page and Recompare keep working until then.

## 7. Errors

Every error has the same shape and a stable code:

```json
{ "error": { "code": "KEY_NOT_FOUND", "message": "Key column account_id is not in File 2.",
             "field": "keys[0]", "details": { "side": "right", "similar": ["ACCT_ID", "account"] } } }
```

| HTTP | When |
|---|---|
| 400 | The body is not valid JSON or does not match the schema. Every problem is listed. |
| 403 | A path is outside the allowed folders. |
| 404 | No such job. |
| 409 | Conflict: same `reference` with a different body, output folder in use or exists (with `fail`), job already finished. |
| 422 | A valid request that cannot run: missing key column, unreadable file, bad rule. |
| 429 | Queue full. `Retry-After` says when to try again. |
| 503 | The API is turned off (no output folders), the server is shutting down, or the disk is full. |

Jobs that fail after `202` keep the same codes, in `error.code` on the job and in `error.json`.

## 8. Edge cases

### Input paths

| Case | Behaviour | Code |
|---|---|---|
| Relative path, empty, or not text | Refused at submission | `INPUT_PATH_INVALID` |
| Outside the allowed folders, including through `..` or a link | Refused. The message never says whether the file exists. | `INPUT_NOT_ALLOWED` |
| Does not exist | Refused, unless `wait_for_inputs` is set | `INPUT_NOT_FOUND` |
| A folder, device or pipe | Refused | `INPUT_NOT_A_FILE` |
| Empty, or over the 100 GB limit | Refused | `INPUT_EMPTY` / `INPUT_TOO_LARGE` |
| No read permission, locked by another program, or its share is offline | Refused; the system's message goes to the log, not the response | `INPUT_UNREADABLE` |
| Still being written by its producer | `wait_for_inputs` waits for a stable size and time; otherwise the fingerprint check below catches it | `INPUT_NOT_STABLE` |
| Changed between submission and start, or during the run | Fails; the result would describe neither version | `INPUT_CHANGED` |
| Both sides are the same file | Allowed, with a warning | warning |
| A path the server does not see (another machine's share) | Not found; the message says paths must be as the server sees them | `INPUT_NOT_FOUND` |
| Long paths, spaces, non-English characters | Supported | |
| Folder synced by OneDrive | Allowed, with the existing slowness warning | warning |
| `.gz`, `.zip`, `.json`, `.xls` | Refused in version 1 | `INPUT_FORMAT_UNSUPPORTED` |

### File contents

| Case | Behaviour | Code |
|---|---|---|
| Delimiter or encoding cannot be detected | Fails with what was tried; the script sets them explicitly | `INPUT_DIALECT_UNKNOWN` |
| Byte-order mark, NUL characters, mixed line endings, quoted line breaks | Handled as the app handles them today | |
| No header row | Needs `column_names` or `use_header_from` | `HEADER_REQUIRED` |
| Repeated or blank header names | Renamed as the app does today (blank → `column3`, repeated → `name_1`); listed by `validate` and in `summary.json` | warning |
| Header names that differ only in case or spaces | Matched as the app matches them; reported by `validate` | |
| Columns in only one file | Not compared; listed in `summary.json` | warning |
| Rows with more or fewer fields than the header | Fails with the record number, as the engine does today | `INPUT_MALFORMED` |
| Excel with several sheets and no `sheet` | Fails, listing the sheets | `SHEET_REQUIRED` |
| Password-protected or damaged workbook | Fails | `INPUT_UNREADABLE` |
| Parquet with nested types | Fails, naming the columns | `INPUT_TYPE_UNSUPPORTED` |
| Fixed-width layout that does not fit the record length | Fails, as the app's layout check | `LAYOUT_INVALID` |

### Keys, columns and rules

| Case | Behaviour | Code |
|---|---|---|
| Key column missing in a file | Fails at submission, with similar names | `KEY_NOT_FOUND` |
| Key column also ignored, or listed twice | Refused | `KEY_INVALID` |
| A key column named like an output column (`column`, `count`, `kind`, …) | The output's own columns get a leading underscore (`_column`), noted in `manifest.json` | |
| Empty key values | Compared as empty text, as today | |
| Duplicate keys | `first` / `last` keep one row and write `duplicate_keys.csv`; `fail` stops the job | `DUPLICATE_KEYS` |
| Rule or override on a key, an ignored column or an unknown column | Refused | `RULE_INVALID` |
| Numeric and date rules on the same column | Refused, as in the app | `RULE_CONFLICT` |
| Container key width does not match the keys | Refused | `CONTAINER_INVALID` |
| Template not found, or it names columns not in these files | Refused, listing them | `TEMPLATE_INVALID` |

### Output

| Case | Behaviour | Code |
|---|---|---|
| Relative path, or outside the output folders | Refused | `OUTPUT_NOT_ALLOWED` |
| Parent folder does not exist | Created, if inside an output folder | |
| Folder exists | `new_folder`: suffix added / `fail`: refused / `replace`: only if this service wrote it | `OUTPUT_EXISTS` / `OUTPUT_NOT_OURS` |
| Another unfinished job has the same output path | Refused | `OUTPUT_IN_USE` |
| Output is or contains an input file, or is inside the data folder | Refused | `OUTPUT_OVERLAPS_INPUT` |
| No write permission | Refused at submission (a probe file is created and removed) | `OUTPUT_NOT_WRITABLE` |
| Disk fills while publishing | Staging removed, existing output untouched | `OUTPUT_DISK_FULL` |
| Folder name invalid on Windows (`CON`, trailing dot, `:`) | Refused | `OUTPUT_PATH_INVALID` |
| Very large `differences.csv` | Written in streaming chunks with progress; Excel keeps its row limit | |

### Queue, lifecycle and restarts

| Case | Behaviour | Code |
|---|---|---|
| The same request sent twice (network retry) | Same `reference` and body: the existing job is returned with `200` | |
| Same `reference` with a different body | Refused | `REFERENCE_CONFLICT` |
| Queue full, or 5 jobs already waiting from one machine | Refused with `Retry-After` | `QUEUE_FULL` |
| Server restarts while a job is queued | Stays queued. New: today a restart marks queued jobs as stopped. | |
| Server restarts while a job is running | Resumed from the partitioned engine's journal when it has one; otherwise restarted once. `attempt` goes up. New: today a restart marks it stopped and the app offers Resume. | |
| Server restarts while publishing | Staging is discarded and publishing repeats from the finished results | |
| Cancel while queued, running or publishing | Stops at the next safe point; staging removed; state `cancelled` | |
| Cancel after the job finished | `409`, the job is unchanged | `JOB_FINISHED` |
| `timeout_minutes` reached | Cancelled with state `timed_out` | `TIMED_OUT` |
| Data folder out of space before or during the run | Checked before the job starts (new: today only uploads are checked); fails cleanly if writes run out | `DISK_FULL` |

### Safety without sign-in

- Only the configured folders are reachable; every path is resolved before checking, so `..`, links and
  junctions cannot escape them.
- Error messages never include file contents, and never confirm whether a file outside the folders exists.
- Request bodies are limited to 1 MB, and lists inside them are limited (for example 10,000 overrides).
- Every call is in `api-audit.jsonl` with the caller's address.

## 9. How it fits the existing code

- `job_api.py` (new): schema checks without new dependencies, path rules (allowed folders, overlaps), error codes,
  the output writer (`differences.csv` with split keys, `patterns.csv`, staging, `_SUCCESS`) and the audit log.
- `server.py`: the `/api/v1/*` routes. The checks inside today's `POST /api/jobs/{id}/start` move into one
  function used by both the browser and the API, so both always accept the same settings.
- API jobs are created like today's server-file jobs (`inspect_source`, fingerprints, `checked_path`), so the
  engine is unchanged.
- `report_patterns.py`: the pattern grouping also keeps one example key per pattern and a `kind`.
- Publishing is a new last step after Write results, shown as "Publish results" on the pipeline page for API
  jobs.
- In team mode API jobs are owned by `api`, so finished ones appear in everyone's Job history and Projects, like
  other finished jobs.
- Restart recovery reuses the resume journal (`can_resume`, `resume_strategy`); for API jobs startup re-queues
  instead of marking them stopped.

## 10. Testing

- Unit tests for the schema checks, path rules and output writer, one per row in section 8 that applies.
- End-to-end tests through the real server: submit, poll, completed folder with `_SUCCESS` and the expected files,
  failure with `_FAILED`, cancel, `new_folder` suffixes, and restart recovery.
- The example script and the Postman collection are run against a test server in the test suite.

## 11. Build plan

1. **Core:** `validate`, submit, status, list, cancel, log, the output folder (staging, `_SUCCESS` / `_FAILED`,
   split keys, `patterns.csv`), error codes, allowed output folders, audit log and queue limits.
2. **Robustness:** duplicate-submission protection by `reference`, `wait_for_inputs`, automatic resume after a
   restart, timeouts, the pre-run disk check.
3. **Integrations:** `checks` and `outcome`, project attach, templates, the OpenAPI document, a Docs page, the
   example Python and PowerShell scripts, and the Postman collection.
4. **Later:** API keys, webhooks, compressed inputs, JSON comparisons, Windows share paths for a Linux server.

## 12. Decisions

| Question | Decision |
|---|---|
| Who calls it first | Scripts, polling for status. Webhooks later. |
| Where the files are | On the server: local disks or shares already mounted on it. |
| Sign-in | None, like the browser app. The server stays on a trusted internal network; allowed folders, queue limits and the audit log still apply. API keys can be added later. |
| Default output files | Everything except Excel, plus `patterns.csv`. Excel on request. |
| Output folder already exists | `new_folder` by default: add `-2`, `-3`, …. `fail` and `replace` on request. |
| Compressed inputs | Not in version 1. |
| Who sees API jobs in the app | Everyone, like any other job. |
| Key format in the CSV files | Each key column as its own column, with its real name. |
