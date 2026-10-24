"""Isolated comparison worker launched by server.py."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from compare import compare

if __name__ == '__main__':
    directory = Path(sys.argv[1])
    job = json.loads((directory / 'job.json').read_text())
    from runtime_resources import prepare_worker
    try:
        job.update(prepare_worker(job, directory))
    except Exception as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(2)
    from source_inputs import checked_path
    def source(side):
        value=job.get('source_paths',{}).get(side)
        if value and job['files'][side].get('format')!='excel':return checked_path(value)
        return directory/(side+'.parquet' if job['files'][side].get('format')=='parquet' else side+'.csv')
    args = SimpleNamespace(checkpoint_dir=directory/'checkpoints',left=source('left'), right=source('right'),
                           read_batch_size=job.get('read_batch_size',100000), compare_batch_size=job.get('compare_batch_size',10000), duplicate_policy=job.get('duplicate_policy','first'),
                           comparison_rules=job.get('comparison_rules',[]), cancel_file=directory/'cancel-comparison', diagnostics_file=directory/'duplicate-keys.json',
                           keys=job['keys'], output=directory / 'report',
                           memory_mb=job['memory_mb'], comparison_engine=job.get('comparison_engine','auto'), temp_dir=directory,
                           native_batch_bytes=job['native_batch_bytes'], io_buffer_bytes=job['io_buffer_bytes'], cpu_threads=job['cpu_threads'],
                           delimiter=job['delimiter'], encoding=job['encoding'], dialects=job.get('dialects') or {},
                           parallel_workers=job.get('parallel_workers'),
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
