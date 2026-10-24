"""Conservative per-worker sizing from resources available when a queued job starts."""
import ctypes
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

MIB = 1024 ** 2
GIB = 1024 ** 3


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ''


def _cgroup_memory(total, available):
    """Respect container limits when host MemAvailable would overstate headroom."""
    roots = [Path('/sys/fs/cgroup')]
    for line in _read('/proc/self/cgroup').splitlines():
        pieces = line.split(':', 2)
        if len(pieces) == 3 and pieces[0] == '0':
            roots.append(Path('/sys/fs/cgroup') / pieces[2].lstrip('/'))
    for root in roots:
        for limit_name, used_name in [('memory.max', 'memory.current'), ('memory/memory.limit_in_bytes', 'memory/memory.usage_in_bytes')]:
            limit, used = _read(root / limit_name), _read(root / used_name)
            if limit.isdigit() and used.isdigit() and 0 < int(limit) < total:
                total = int(limit)
                available = min(available, max(0, total - int(used)))
    return total, available


def memory_snapshot():
    system = platform.system()
    try:
        if system == 'Windows':
            class Status(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in ('total_phys', 'available_phys', 'total_page', 'available_page', 'total_virtual', 'available_virtual', 'extended')]
            status = Status()
            status.length = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                raise OSError('GlobalMemoryStatusEx failed')
            return status.total_phys, status.available_phys, 'Windows available physical RAM'
        if system == 'Linux':
            fields = {name: int(value) * 1024 for name, value in re.findall(r'^(\w+):\s+(\d+)\s+kB', _read('/proc/meminfo'), re.M)}
            total = fields['MemTotal']
            available = fields.get('MemAvailable', fields.get('MemFree', 0) + fields.get('Buffers', 0) + fields.get('Cached', 0))
            total, available = _cgroup_memory(total, available)
            return total, min(total, available), 'Linux available RAM / container limit'
        if system == 'Darwin':
            total = int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.memsize'], timeout=2, text=True, stderr=subprocess.DEVNULL).strip())
            output = subprocess.check_output(['/usr/bin/vm_stat'], timeout=2, text=True, stderr=subprocess.DEVNULL)
            page_size = int(re.search(r'page size of (\d+) bytes', output)[1])
            pages = {name: int(value) for name, value in re.findall(r'^(Pages [\w ]+):\s+(\d+)', output, re.M)}
            available = sum(pages.get(name, 0) for name in ('Pages free', 'Pages inactive', 'Pages speculative')) * page_size
            return total, min(total, available), 'macOS free and reclaimable RAM'
    except (AttributeError, KeyError, OSError, ValueError, TypeError, subprocess.SubprocessError):
        pass
    # Failure to inspect a machine must not assume that its full installed RAM is free.
    return 4 * GIB, 3 * GIB, 'Conservative fallback (RAM probe unavailable)'


def cpu_capacity():
    count = os.cpu_count() or 1
    try:
        count = min(count, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        pass
    if platform.system() == 'Linux':
        values = _read('/sys/fs/cgroup/cpu.max').split()
        if len(values) == 2 and all(value.isdigit() for value in values) and int(values[1]) > 0:
            count = min(count, max(1, math.floor(int(values[0]) / int(values[1]))))
    return max(1, count)


def size_worker(job, directory, snapshot=None):
    """Return actual settings; legacy per-job and workspace tuning is not consulted."""
    if snapshot is None:
        total, available, probe = memory_snapshot()
        snapshot = dict(total_memory_bytes=total, available_memory_bytes=available,
                        cpu_count=cpu_capacity(), free_disk_bytes=shutil.disk_usage(directory).free,
                        memory_probe=probe)
    available = int(snapshot['available_memory_bytes'])
    total = int(snapshot['total_memory_bytes'])
    free_disk = int(snapshot['free_disk_bytes'])
    if free_disk < 128 * MIB:
        raise ValueError('Less than 128 MB of free workspace storage remains. Free storage before comparing.')
    if available < 128 * MIB:
        raise ValueError('Not enough available RAM to start safely. Close another application or retry later.')
    cap = max(0, int(job.get('memory_cap_mb') or 0))
    reserve = max(2 * GIB, available // 4)
    parallel_slots = max(1, int(job.get('memory_concurrency') or 1))
    memory_mb = max(64, (available - reserve) // (MIB * parallel_slots))
    if cap:
        memory_mb = min(memory_mb, cap)
    columns = max(1, len(job.get('columns') or job.get('source_headers', {}).get('left') or ['']))
    # Wide tables need smaller blocks even when their source compresses very well.
    row_estimate = max(128, columns * 16)
    read_rows = max(1, min(100000, memory_mb * MIB // (row_estimate * 8)))
    compare_rows = max(100, min(10000, 2_000_000 // columns))
    return dict(snapshot, memory_mb=memory_mb, memory_cap_mb=cap, comparison_engine='auto',
                sort_workers=1, read_batch_size=read_rows, compare_batch_size=compare_rows,
                native_batch_bytes=max(MIB, min(64 * MIB, memory_mb * MIB // 64)),
                io_buffer_bytes=max(64 * 1024, min(4 * MIB, memory_mb * MIB // 256)),
                cpu_threads=max(1, min(4, int(snapshot['cpu_count']) // parallel_slots)),
                # Partitioned comparisons split the CPUs between concurrently running jobs.
                parallel_workers=max(1, min(16, int(snapshot['cpu_count']) // parallel_slots)), automatic=True)


def prepare_worker(job, directory):
    """Persist the effective policy separately so the server retains ownership of job.json."""
    import json
    from file_io import atomic_write_text
    settings = size_worker(job, directory)
    atomic_write_text(Path(directory) / 'runtime-settings.json', json.dumps(settings))
    try:
        import pyarrow as pa
        pa.set_cpu_count(settings['cpu_threads'])
        pa.set_io_thread_count(settings['cpu_threads'])
    except ImportError:
        pass
    print(f"Automatic sizing: {settings['memory_mb']} MB comparison budget; {settings['read_batch_size']:,} read/sort rows; "
          f"{settings['compare_batch_size']:,} comparison keys; {settings['cpu_threads']} native CPU threads; up to {settings.get('parallel_workers', 1)} parallel workers for inputs of 64 MB or more. "
          f"Available RAM {settings['available_memory_bytes'] / GIB:.1f} GB; free storage {settings['free_disk_bytes'] / GIB:.1f} GB. "
          "Storage is not reserved; full mismatch output may require substantially more space.", flush=True)
    return settings
