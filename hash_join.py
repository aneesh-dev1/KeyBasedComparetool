"""Memory-bounded exact key lookup with adaptive hash spilling.

Both selected-row maps are retained before writing any results. This is necessary
for the last-occurrence duplicate policy: a later source row can replace an
earlier match. Keys and rows are never sorted.
"""
import contextlib
import itertools
import json
import sys
import time
from pathlib import Path

from task_control import check_cancel


class _BudgetExceeded(Exception):
    pass


@contextlib.contextmanager
def _packed_source(path, names, canonical, keys, args, side=None):
    # Local imports keep compare.py free to select this engine at runtime.
    from compare import input_rows, is_parquet, pack_values

    parquet = is_parquet(path)
    projection = [names.index(name) for name in canonical] if parquet else None
    source_names = canonical if parquet else names
    order = [source_names.index(name) for name in canonical]
    key_positions = [source_names.index(name) for name in keys]
    if parquet and not getattr(args, 'reference_parquet_reader', False):
        from parquet_input import PackedRows
        with PackedRows(path, projection, key_positions, 0,
                        getattr(args, 'read_batch_size', 100000), args.memory_mb, getattr(args,'native_batch_bytes',None)) as source:
            yield iter(source)
    elif not parquet and not getattr(args, 'reference_csv_reader', False):
        from csv_input import PackedRows
        with PackedRows(path, source_names, order, key_positions, args, side=side) as source:
            yield iter(source)
    else:
        def packed(reader):
            for number, row in enumerate(reader, 1):
                if len(row) != len(source_names):
                    raise ValueError(f'{path}: record {number}: expected {len(source_names)} fields, got {len(row)}')
                yield tuple(row[i] for i in key_positions), pack_values([row[i] for i in order])
        with input_rows(path, args, 0, projection, side) as (_, reader):
            yield packed(reader)


class HashJoin:
    def __init__(self, left, right, samples, retained_bytes, budget_bytes):
        self.left = left
        self.right = right
        self.samples = samples
        self.retained_bytes = retained_bytes
        self.budget_bytes = budget_bytes

    def close(self):
        """Release the large row maps before report generation, even on errors."""
        self.left.clear()
        self.right.clear()
        self.samples.clear()
        self.retained_bytes = 0

    def _audit(self, stats, audit, args):
        from compare import unpack_values
        from file_io import atomic_write_text

        diagnostics = getattr(args, 'diagnostics_file', None)
        policy = getattr(args, 'duplicate_policy', 'first')
        for side, rows in (('left', self.left), ('right', self.right)):
            # Audit duplicate groups in insertion order; diagnostics keep the smallest key.
            for index, (key, entry) in enumerate(rows.items()):
                if entry[1] <= 1:
                    continue
                if index % 1000 == 0:
                    check_cancel(getattr(args, 'cancel_file', None))
                count = rows[key][1]
                stats[side + '_duplicate_keys'] = stats.get(side + '_duplicate_keys', 0) + 1
                stats[side + '_duplicate_rows_skipped'] = stats.get(side + '_duplicate_rows_skipped', 0) + count - 1
                if audit is not None:
                    audit.writerow([side, key.decode('utf-8'), count, count - 1, policy])
                if diagnostics and not Path(diagnostics).exists() and self.samples.get(side, (None,))[0] == key:
                    sample_key, payloads = self.samples[side]
                    assert sample_key == key
                    samples = [unpack_values(payload) for payload in payloads]
                    atomic_write_text(diagnostics, json.dumps(dict(
                        side=side, key=json.loads(key), count=count,
                        sample_columns=getattr(args, 'diagnostic_columns', [])[:20],
                        samples=[['[NULL]' if value is None else value[:500] for value in row[:20]] for row in samples],
                        note=f'Comparison continues using the {policy} source occurrence per key. Extra rows are skipped. This is the first duplicate group; download the duplicate audit for all keys.')))

    def iter_pairs(self, stats, audit, args):
        """Yield exact pairs in source insertion order, without sorting keys or rows."""
        self._audit(stats, audit, args)
        keys = itertools.chain(self.left, (key for key in self.right if key not in self.left))
        for index, key in enumerate(keys):
            if index % 1000 == 0:
                check_cancel(getattr(args, 'cancel_file', None))
            left, right = self.left.get(key), self.right.get(key)
            yield ((key, left[0]) if left is not None else None,
                   (key, right[0]) if right is not None else None)


def try_build(args, left_names, right_names, canonical, temp, stats, progress, adaptive=False):
    """Build exact maps or return None, leaving stats untouched on fallback.

    Twenty percent of the requested budget is reserved for reader buffers,
    transient dictionary growth and native comparison batches.
    The estimate covers owned Python objects rather than claiming an OS RSS cap.
    """
    temp = Path(temp)
    if not adaptive and any((temp / (side + '.json')).exists() for side in ('left', 'right')):
        progress(args, 'Reusing saved sort batches; using compressed sorting', stage='sort', engine='sort')
        return None
    with contextlib.ExitStack() as stack:
        if getattr(args, 'checkpoint_dir', None):
            from checkpoints import lock
            # An orphaned sorter may still be writing its first batch and have
            # no manifest yet. It must finish before this job can change engines.
            for side in ('left', 'right'):
                stack.enter_context(lock(temp / ('lock-' + side)))
        return _build(args, left_names, right_names, canonical, stats, progress, temp if adaptive else None)


def _build(args, left_names, right_names, canonical, stats, progress, spill_dir=None):
    from compare import encode

    budget = int(args.memory_mb * 1024 * 1024)
    limit = budget * 4 // 5
    maps = {'left': {}, 'right': {}}
    samples = {}
    counts = {}
    retained = 0
    spilled = None
    entry_overhead = sys.getsizeof((b'', 1)) + sys.getsizeof(1)
    policy = getattr(args, 'duplicate_policy', 'first')
    diagnostics = getattr(args, 'diagnostics_file', None)
    capture = bool(diagnostics and not Path(diagnostics).exists())
    started = time.monotonic()
    progress(args, 'Building in-memory key lookups', stage='index', engine='hash',
             memory_budget_mb=round(budget / (1024 * 1024), 2),
             retained_limit_mb=round(limit / (1024 * 1024), 2))
    try:
        for side, path, names in (('left', args.left, left_names), ('right', args.right, right_names)):
            check_cancel(getattr(args, 'cancel_file', None))
            rows = maps[side]
            count = excluded = 0
            file_started = time.monotonic()
            progress(args, f'Indexing {side} file', stage='index', engine='hash', rows=0, status='ongoing')
            with _packed_source(path, names, canonical, args.keys, args, side) as reader:
                for key_values, payload in reader:
                    count += 1
                    if count % 1000 == 0:
                        check_cancel(getattr(args, 'cancel_file', None))
                    key = encode(key_values)
                    if key in args.excluded_keys:
                        excluded += 1
                    else:
                        if not args.allow_empty_keys and any(value is None or value == '' for value in key_values):
                            raise ValueError(f'{path}: record {count}: empty key component')
                        if spilled is not None:
                            spilled.add(side, key, payload)
                            continue
                        previous = rows.get(key)
                        if previous is None:
                            rows[key] = (payload, 1)
                            retained += sys.getsizeof(key) + sys.getsizeof(payload) + entry_overhead
                        else:
                            occurrences = previous[1] + 1
                            if capture:
                                sample = samples.get(side)
                                if occurrences == 2 and (sample is None or key < sample[0]):
                                    samples[side] = (key, [previous[0], payload])
                                elif sample is not None and sample[0] == key and len(sample[1]) < 3:
                                    sample[1].append(payload)
                            if policy == 'last':
                                retained += sys.getsizeof(payload) - sys.getsizeof(previous[0])
                                rows[key] = (payload, occurrences)
                            else:
                                rows[key] = (previous[0], occurrences)
                        # Count diagnostic payloads conservatively even when a map owns
                        # the same bytes. At most three payloads per side are retained.
                        used = retained + sum(sys.getsizeof(table) for table in maps.values())
                        used += sum(sys.getsizeof(p) for _, values in samples.values() for p in values)
                        if used > limit:
                            if spill_dir is None:
                                raise _BudgetExceeded
                            from hash_spill import DiskJoin
                            spilled = DiskJoin(spill_dir, limit)
                            spilled.samples = dict(samples)
                            for saved_side, table in maps.items():
                                for saved_key, (saved_payload, occurrences) in table.items():
                                    spilled.add(saved_side, saved_key, saved_payload, occurrences)
                                table.clear()
                            retained = 0
                            progress(args, 'Memory lookup budget reached; continuing in hash buckets', stage='index', engine='hash', status='ongoing')
                    if count % 50000 == 0:
                        progress(args, f'Indexing {side} file', stage='index', engine='hash', rows=count,
                                 excluded=excluded, unique_keys=len(rows),
                                 retained_mb=round(retained / (1024 * 1024), 2), status='ongoing')
            counts[side + '_rows'] = count
            counts[side + '_excluded_rows'] = excluded
            progress(args, f'Indexing {side} file complete', stage='index', engine='hash', rows=count,
                     excluded=excluded, unique_keys=len(rows), status='completed',
                     elapsed_seconds=round(time.monotonic() - file_started, 3))
    except _BudgetExceeded:
        for table in maps.values():
            table.clear()
        samples.clear()
        progress(args, 'Memory lookup budget reached; caller must select hash spilling',
                 stage='index', engine='hash', memory_budget_mb=round(budget / (1024 * 1024), 2))
        return None
    except BaseException:
        if spilled is not None:
            spilled.close()
        raise
    if spilled is not None:
        spilled.finish()
        stats.update(counts)
        return spilled
    retained += sum(sys.getsizeof(table) for table in maps.values())
    retained += sum(sys.getsizeof(p) for _, values in samples.values() for p in values)
    stats.update(counts)
    progress(args, 'In-memory key lookups ready', stage='index', engine='hash', status='completed',
             retained_mb=round(retained / (1024 * 1024), 2), elapsed_seconds=round(time.monotonic() - started, 3))
    return HashJoin(maps['left'], maps['right'], samples, retained, budget)
