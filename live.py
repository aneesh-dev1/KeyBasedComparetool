"""Live comparison snapshots: counts and the latest mismatches, written while a comparison is still running.

The snapshot is a side channel for the Analysis page. It never changes what the engines write; the final
report files remain the source of truth once the comparison finishes.
"""
import json
import time
from collections import deque
from pathlib import Path

COUNTS = ('matched_keys', 'equal_rows', 'changed_rows', 'changed_cells', 'left_only', 'right_only',
          'override_equivalent_cells', 'rule_equivalent_cells')


class LiveSnapshot:
    """Throttled writer of ``live.json``. ``stats`` and ``columns`` are read when a snapshot is written."""

    def __init__(self, path, stats, columns, interval=1.0, samples=200):
        self.path = Path(path)
        self.stats, self.columns, self.interval = stats, columns, interval
        self.samples = deque(maxlen=samples)
        self.written = 0.0
        self.started = time.time()

    def sample(self, row):
        """Remember a changed cell as (key_json, column, left_value, right_value), values cut to 300 characters."""
        self.samples.append((row[0], row[1], row[2][:300], row[3][:300]))

    def tick(self, state='comparing', force=False, **extra):
        now = time.monotonic()
        if not force and now - self.written < self.interval:
            return
        self.written = now
        snapshot = dict(state=state, updated=time.time(), started=self.started,
                        **{name: int(self.stats.get(name, 0)) for name in COUNTS},
                        changed_cells_by_column={name: count for name, count in self.columns.items() if count},
                        samples=list(reversed(self.samples)), **extra)
        temporary = self.path.with_suffix('.part')
        try:
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            # A live snapshot is optional; a full or read-only disk must never fail the comparison.
            pass


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
