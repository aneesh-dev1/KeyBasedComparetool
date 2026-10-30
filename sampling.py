"""Comparing a sample of keys, for project rounds that grow from a few keys to all of them.

A sample is drawn from File 1's keys. Each key gets a rank from a keyed hash of the round's seed, and the sample is
the ``size`` keys with the smallest ranks: exact, repeatable from the seed, independent of row order, and kept in
memory as at most ``size`` keys. A round can carry the previous round's keys, so earlier fixes are checked again,
and fill the rest with new random keys. Excluded keys are never drawn.

The engines are unchanged: each already skips any key found in its excluded-keys set, so a sampled comparison passes
a ``KeyFilter`` in that set's place, which also reports every key outside the sample as excluded.
"""
import hashlib
import heapq
import json
from pathlib import Path

FILE = 'sample_keys.json'
MODES = ('random', 'carry')


class KeyFilter:
    """Stands in for the excluded-keys set: a key is skipped when it is excluded or not in the sample."""
    __slots__ = ('excluded', 'sample')

    def __init__(self, excluded, sample):
        self.excluded, self.sample = excluded, sample

    def __contains__(self, key):
        return key in self.excluded or key not in self.sample

    def __len__(self):
        return len(self.excluded)

    def __iter__(self):
        return iter(self.excluded)


def _ranker(seed):
    salt = str(int(seed)).encode()
    return lambda key: int.from_bytes(hashlib.blake2b(key, digest_size=8, key=salt).digest(), 'big')


def draw(args, names, canonical, size, seed, carry=(), excluded=frozenset(), notify=lambda message: None):
    """The sample for one round, as encoded keys plus a description. ``carry`` holds the earlier round's keys
    (decoded key lists); they are always included and count towards ``size``."""
    from compare import encode
    from hash_join import _packed_source
    from invalid_rows import invalid
    carried = {encode(list(key)) for key in carry} - set(excluded)
    need, rank = max(0, int(size) - len(carried)), _ranker(seed)
    heap, chosen, rows = [], set(), 0
    with _packed_source(args.left, names, canonical, args.keys, args, 'left') as (_, reader):
        for key_values, payload in reader:
            rows += 1
            if rows % 1000000 == 0:
                notify(f'Drawing the sample: {rows:,} rows of File 1 read')
            if invalid(payload) or any(value in ('', None) for value in key_values):
                continue
            key = encode(list(key_values))
            if key in carried or key in chosen or key in excluded or not need:
                continue
            value = rank(key)
            if len(heap) < need:
                heapq.heappush(heap, (-value, key));chosen.add(key)
            elif value < -heap[0][0]:
                _, dropped = heapq.heapreplace(heap, (-value, key));chosen.discard(dropped);chosen.add(key)
    sample = carried | chosen
    keys = sorted(sample)
    info = dict(requested=int(size), size=len(sample), carried=len(carried), drawn=len(chosen), seed=int(seed),
                mode='carry' if carry else 'random', population_rows=rows,
                digest=hashlib.blake2b(b'\n'.join(keys), digest_size=16).hexdigest())
    return sample, info


def save(folder, info, sample):
    target = Path(folder) / FILE
    temporary = target.with_suffix('.part')
    temporary.write_text(json.dumps(dict(info, keys=[json.loads(key) for key in sorted(sample)]), ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    temporary.replace(target)


def load_keys(folder):
    """The keys an earlier round compared, or None when it was not a sampled round."""
    try:
        return json.loads((Path(folder) / FILE).read_text(encoding='utf-8'))['keys']
    except (OSError, ValueError, KeyError):
        return None
