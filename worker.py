"""Isolated comparison worker launched by server.py."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from compare import compare

if __name__ == '__main__':
    directory = Path(sys.argv[1])
    job = json.loads((directory / 'job.json').read_text())
    args = SimpleNamespace(left=directory / 'left.csv', right=directory / 'right.csv',
                           keys=job['keys'], output=directory / 'report',
                           memory_mb=job['memory_mb'], temp_dir=directory,
                           delimiter=job['delimiter'], encoding=job['encoding'],
                           fan_in=32, sort_workers=job.get('sort_workers', 1), max_field_mb=64, allow_empty_keys=False,
                           ignore_columns=job.get('ignore_columns', []), ignore_keys=job.get('ignore_keys', ''),
                           value_overrides=job.get('value_overrides', []),
                           column_headers=job.get('column_headers'),
                           ignore_key_containers=job.get('ignore_key_containers', []),
                           progress_file=directory / 'progress.json')
    try:
        compare(args)
    except Exception as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(2)
