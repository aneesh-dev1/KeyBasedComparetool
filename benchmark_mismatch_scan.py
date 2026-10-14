"""Small, reproducible scanner benchmark; excludes input and report I/O.

Run with ``.venv/bin/python benchmark_mismatch_scan.py --rows 2000``.
The full comparison benchmark is benchmark.py. This isolates the bounded
sparse/dense scanner and verifies both implementations return the same counts.
"""
import argparse
import json
import statistics
import time

from mismatch_scan import differences_for_pairs


def make_pairs(rows, columns, percent):
    for row in range(rows):
        left = [str(100_000 + (row * 83 + column * 31) % 899_999)
                for column in range(columns)]
        right = [value if (column * 37 + row) % 100 >= percent else str(int(value) + 7)
                 for column, value in enumerate(left)]
        key = str(row).encode()
        yield ((key, b'\x00' + '\x1f'.join(left).encode()),
               (key, b'\x00' + '\x1f'.join(right).encode()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=2000)
    parser.add_argument('--columns', type=int, default=1780)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if min(args.rows, args.columns, args.repeats) < 1:
        parser.error('rows, columns, and repeats must be positive')
    results = []
    for density in (1, 60, 100):
        pairs = list(make_pairs(args.rows, args.columns, density))
        variants = {}
        for label, native in (('python', False), ('adaptive', True)):
            # Include a warmup so the optional Arrow import is not timed.
            durations, counts = [], []
            for repeat in range(args.repeats + 1):
                started = time.perf_counter()
                count = sum(1 for _, _, diffs in differences_for_pairs(
                    pairs, args.columns, use_native=native) for _ in diffs)
                elapsed = time.perf_counter() - started
                if repeat:
                    durations.append(elapsed)
                    counts.append(count)
            assert len(set(counts)) == 1
            variants[label] = {'seconds_median': round(statistics.median(durations), 6),
                               'mismatching_cells': counts[0]}
        assert variants['python']['mismatching_cells'] == variants['adaptive']['mismatching_cells']
        results.append({'mismatch_percent': density, **variants})
    print(json.dumps({'rows': args.rows, 'columns': args.columns,
                      'repeats': args.repeats, 'results': results}, indent=2))


if __name__ == '__main__':
    main()
