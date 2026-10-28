# Skipped keys: proposed behavior and review design

Status: backend and reports implemented. The app UI is unchanged at the user's request. The preview remains an illustrative design, not a deployed screen.
Preview: ../design-previews/skipped-keys.html (illustrative data).

## Input distinctions

- CSV: blank header cells get generated names; they do not make the whole file headerless.
- Parquet: names come from the schema. Blank schema names get generated names; null values are valid cells. Parquet does not have CSV-style short records.
- A parsed CSV record with 1,626 fields under 1,800 headers is malformed. A record with 1,800 fields including empty strings is valid.
- Wrong delimiters, damaged quoting, unreadable encoding, or a corrupt Parquet row group remain errors. Do not silently drop an entire file or guess record boundaries.

## Exclusion semantics

- Record each field-count mismatch while reading; avoid an additional full-file validation pass.
- If every selected key component is available, exclude that exact composite key from both sides, including every duplicate occurrence, regardless of first/last duplicate policy.
- Excluded keys do not count as equal, changed, left-only, right-only, or matched-key denominators.
- If key fields are missing, preserve an audit record with the readable components, source location and reason. Label it “key unavailable”; never fabricate a key or claim its counterpart was excluded. Clearly distinguish unknown-key records from identifiable skipped keys.
- Record too-few and too-many fields separately. Do not pad or truncate source records silently.

## UI

Use the current TU palette, typography and existing drawer conventions.
- Pipeline: compact warning notice with a running malformed-record count. Counts are provisional until both inputs have finished reading.
- Analysis: “Completed with N skipped keys”, visible before match metrics. Add a separate unidentified-record count when applicable.
- Review drawer: key, source file, logical record or explicitly labeled byte offset, expected/actual fields, reason, counterpart exclusion status.
- Keep the drawer header and download action fixed; scroll the audit list. Use server pagination/filtering for large audits.
- Keyboard: accessible dialog title, trapped focus, Escape dismissal and focus restoration.
- Motion: 280ms entry / 200ms exit with reduced-motion support.
- No toast-only reporting and no green “all data matches” conclusion when any records were excluded.
- Comparison rules explain malformed-record handling; no new top-level settings page.

## Reports

- Persist a complete streamed skipped-record audit, not just a truncated UI sample.
- HTML: warning near the verdict; compact audit section before secondary details. For large audits explicitly label bounded previews and offer the complete CSV from the app.
- Excel: dedicated Skipped keys sheet, with workbook row-limit handling and explicit continuation sheets if necessary.
- Include source/side, key components, location type/value, expected and actual field counts, reason, and exclusion action. Prevent spreadsheet formula execution in exports.
- Preserve audit data when sources expire and when reports regenerate.

## Implementation scope and verification

1. Audit schema and deterministic key-exclusion transport shared across hash, external-sort and partitioned/fingerprint paths.
2. Reader integration, including multiline CSV record boundaries, parallel range locations and cancellation.
3. Apply exclusions after both inputs have been inspected, before any key-only or cell results are emitted.
4. Checkpoint identity/versioning and resume-safe audit reconstruction without duplicate audit entries.
5. Re-evaluation must inherit exclusions and audit; recovering excluded rows requires a new comparison.
6. Server paginated audit/download endpoint, progress/summary fields and UI drawer.
7. HTML/Excel reports and regression verification.

## Required validation

- 1,800 headers / 1,626 fields; all three engines produce identical summaries.
- Missing values represented by empty cells remain comparable.
- Composite keys, missing key components, duplicate invalid/valid occurrences, opposite-side counterparts.
- Reordered rows, unequal file sizes, quoted delimiters, multiline fields, custom encodings.
- Blank CSV headers and blank Parquet schema names; Parquet nulls remain valid.
- Cancellation/resume and hash-to-sort / fingerprint-to-parser fallback do not double-count.
- Missing-field failures do not mask malformed quoting, delimiter mistakes or corrupt files.
- Re-evaluation, report regeneration, audit pagination, accessible drawer and export safety.

## Implemented integration

Malformed CSV records travel through existing read/index/checkpoint paths as reserved audit payloads. They are removed before the shared cell-comparison scanner runs. This preserves counterpart exclusion and first/last duplicate correctness across hash, sort and partitioned modes, without a separate full-file preflight scan. Fingerprint readers fall back to the audited parser for a range containing malformed records.

skipped_keys.csv is complete, streamed on output, copied into review revisions, and available through the authenticated existing download route. Summary counters include malformed records, identifiable skipped keys, unidentified records, per-side invalid records and per-side skipped rows. HTML shows a bounded 20-record preview and clearly labels it; Excel uses continuation worksheets when necessary. Existing Pipeline logs report exclusions; no new app UI is installed.

Checkpoint configuration includes the malformed-row policy and the partition journal version was advanced. An old interrupted run with an incompatible identity requires a new comparison rather than reusing output produced under different semantics.

Worker scheduling retains the existing 16-process ceiling per job, reserves one CPU on multicore servers, and queues additional comparisons rather than oversubscribing admitted jobs. Memory shares track admitted CPU shares. Reports/analysis retain their separate executor. Native pools inside partition workers use one CPU thread and one I/O thread; automatic process counts honor affinity/container CPU limits. These changes aim to reduce contention; no production latency or throughput claim has been measured.
