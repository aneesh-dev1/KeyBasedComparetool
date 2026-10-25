"""Reproducible warm-cache engine benchmark; run against two checkout paths.

python benchmarks/adaptive_hash.py --checkout PATH --output measurements.json
Counts and order-independent SHA-256 report digests are checked outside timing.
No claim about HDD/network speed or full 6 GB inputs is inferred from this test.
"""
import argparse
import contextlib
import csv
import hashlib
import io
import json
import platform
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--checkout', required=True)
    cli.add_argument('--output', required=True)
    cli.add_argument('--repeats', type=int, default=3)
    options = cli.parse_args()
    sys.path.insert(0, str(Path(options.checkout).resolve()))
    from compare import compare, parser
    results = dict(python=platform.python_version(), platform=platform.platform(), cases=[])
    with tempfile.TemporaryDirectory(prefix='hash-benchmark-') as directory:
        root = Path(directory)
        for label, count, width, density in (('wide_equal', 1200, 1780, 0), ('wide_dense', 1200, 1780, 55), ('many_keys', 150000, 12, 1)):
            expected = 0
            for side in ('left', 'right'):
                keys = list(range(count)) if side == 'left' else list(range(8, count + 8))
                random.Random(19 if side == 'left' else 31).shuffle(keys)
                with (root / (side + '.csv')).open('w', newline='') as stream:
                    writer = csv.writer(stream)
                    writer.writerow(['id'] + [f'c{i}' for i in range(width - 1)])
                    for key in keys:
                        values = []
                        for col in range(width - 1):
                            changed = side == 'right' and (key + col * 37) % 100 < density
                            values.append(str(100000 + (key * 83 + col * 31) % 899999 + (7 if changed else 0)))
                            if changed and key < count:
                                expected += 1
                        writer.writerow([f'K{key:07d}', *values])
            for engine in ('auto', 'partitioned', 'sort'):
                durations = []
                digest = None
                for repeat in range(options.repeats):
                    out = root / 'report'
                    args = parser().parse_args([str(root/'left.csv'), str(root/'right.csv'), '--keys', 'id', '--output', str(out),
                        '--memory-mb', '256', '--engine', engine, '--workers', '2', '--temp-dir', str(root)])
                    with contextlib.redirect_stdout(io.StringIO()):
                        start = time.perf_counter(); summary = compare(args); durations.append(time.perf_counter() - start)
                    assert summary['changed_cells'] == expected
                    assert summary['matched_keys'] == count - 8
                    assert summary['left_only'] == summary['right_only'] == 8
                    if repeat == 0:
                        digests = {}
                        for path in out.glob('*.csv'):
                            with path.open(newline='', encoding='utf-8') as stream:
                                rows = list(csv.reader(stream))
                            digests[path.name] = hashlib.sha256(json.dumps([rows[:1], sorted(rows[1:])], ensure_ascii=False).encode()).hexdigest()
                        digest = digests
                    shutil.rmtree(out)
                item = dict(case=label, rows_per_side=count, columns=width, mismatch_percent=density, engine=engine,
                            input_bytes=sum((root/(s+'.csv')).stat().st_size for s in ('left','right')),
                            seconds=durations, median_seconds=statistics.median(durations), changed_cells=expected, report_digests=digest)
                results['cases'].append(item)
                print(label, engine, round(item['median_seconds'], 4), flush=True)
    Path(options.output).write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
