"""Small atomic snapshots for live review; never a second copy of the result set."""
from collections import deque
import json
from pathlib import Path
import time

from file_io import atomic_write_text

MAX_SAMPLES = 60
MAX_SNAPSHOT_BYTES = 256 * 1024


def shortened(value, limit=240):
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit] + '…'


class LivePreview:
    def __init__(self, path):
        self.path = Path(path) if path else None
        self.samples = deque(maxlen=MAX_SAMPLES)
        self.last_write = 0
        self.sequence = 0

    def add(self, key, column, left, right):
        if self.path:
            # Keep the exact full values in the CSV evidence, not in browser updates.
            self.samples.append(dict(key=shortened(key, 320), column=shortened(column),
                                     left=shortened(left), right=shortened(right)))

    def publish(self, stats, columns, processed, *, phase='compare', force=False):
        if not self.path:
            return
        now = time.monotonic()
        if not force and now - self.last_write < 1.5:
            return
        self.last_write = now
        self.sequence += 1
        counts = {key: stats.get(key, 0) for key in
                  ('matched_keys', 'changed_rows', 'equal_rows', 'changed_cells', 'left_only', 'right_only')}
        busiest = sorted(((name, count) for name, count in columns.items() if count),
                         key=lambda item: -item[1])[:20]
        data = dict(sequence=self.sequence, updated=time.time(), phase=phase,
                    processed_keys=processed, counts=counts, sample_limit=MAX_SAMPLES,
                    columns_with_differences=sum(bool(count) for count in columns.values()),
                    columns=[dict(name=shortened(name), count=count) for name, count in busiest],
                    samples=list(self.samples), sampled=True)
        try:
            encoded = json.dumps(data, ensure_ascii=False)
            while len(encoded.encode('utf-8')) > MAX_SNAPSHOT_BYTES and data['samples']:
                data['samples'].pop(0)
                encoded = json.dumps(data, ensure_ascii=False)
            atomic_write_text(self.path, encoded)
        except OSError as error:
            # A locked preview file must not discard a multi-hour comparison.
            print(f'Live preview unavailable: {error}; comparison continues.', flush=True)
            self.path = None


def read_preview(directory, state):
    path = Path(directory) / 'live-preview.json'
    try:
        with path.open('rb') as stream:
            payload = stream.read(MAX_SNAPSHOT_BYTES + 1)
        if len(payload) > MAX_SNAPSHOT_BYTES:
            raise ValueError('Live preview exceeds size limit')
        result = json.loads(payload)
    except (FileNotFoundError, PermissionError, ValueError):
        result = dict(sequence=0, phase='prepare', samples=[], counts={}, processed_keys=0)
    result['job_state'] = state
    result['provisional'] = state != 'complete'
    return result
