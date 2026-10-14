import unittest
from unittest.mock import patch

from runtime_resources import GIB, MIB, memory_snapshot, size_worker


class RuntimeResourcesTests(unittest.TestCase):
    def snapshot(self, available=24, total=32, cpu=8):
        return dict(total_memory_bytes=total*GIB, available_memory_bytes=available*GIB,
                    cpu_count=cpu, free_disk_bytes=100*GIB, memory_probe='test')

    def test_no_fixed_ceiling_and_resource_headroom(self):
        for available, total in [(24,32), (48,64), (96,128)]:
            with self.subTest(total=total):
                result=size_worker({'columns':['id','v']}, '.', self.snapshot(available,total))
                self.assertEqual(result['memory_mb'],available*1024*3//4)
                self.assertGreater(result['memory_mb'],15360)
                self.assertEqual(result['comparison_engine'],'auto')
                self.assertEqual(result['sort_workers'],1)

    def test_admin_limit_and_parallel_workers(self):
        snap=self.snapshot()
        capped=size_worker({'memory_cap_mb':64},'.',snap)
        self.assertEqual(capped['memory_mb'],64)
        slots=size_worker({'memory_concurrency':3},'.',snap)
        self.assertEqual(slots['memory_mb'],6144)
        self.assertEqual(slots['cpu_threads'],2)

    def test_width_and_memory_drive_batch_and_buffer_sizes(self):
        thin=size_worker({'columns':['id']},'.',self.snapshot())
        wide=size_worker({'columns':['c']*2000,'memory_cap_mb':64},'.',self.snapshot())
        self.assertLess(wide['read_batch_size'],thin['read_batch_size'])
        self.assertLess(wide['compare_batch_size'],thin['compare_batch_size'])
        self.assertLess(wide['native_batch_bytes'],thin['native_batch_bytes'])
        self.assertLess(wide['io_buffer_bytes'],thin['io_buffer_bytes'])
        # Legacy browser settings must not become a hidden resource ceiling.
        old=size_worker({'memory_mb':64,'read_batch_size':1,'comparison_engine':'sort'},'.',self.snapshot())
        self.assertEqual(old['memory_mb'],thin['memory_mb'])
        self.assertEqual(old['comparison_engine'],'auto')

    def test_insufficient_disk_and_memory_stop_safely(self):
        for field in ('available_memory_bytes','free_disk_bytes'):
            snap=self.snapshot();snap[field]=127*MIB
            with self.subTest(field=field), self.assertRaises(ValueError):size_worker({},'.',snap)

    def test_linux_container_memory_overrides_host_capacity(self):
        content={'/proc/meminfo':'MemTotal: 67108864 kB\nMemAvailable: 50331648 kB',
                 '/sys/fs/cgroup/memory.max':str(8*GIB),'/sys/fs/cgroup/memory.current':str(3*GIB)}
        with patch('runtime_resources.platform.system',return_value='Linux'), patch('runtime_resources._read',side_effect=lambda path:content.get(str(path),'')):
            total,available,_=memory_snapshot()
        self.assertEqual((total,available),(8*GIB,5*GIB))

    def test_mac_free_reclaimable_pages_and_probe_fallback(self):
        vm='Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 1024.\nPages inactive: 2048.\nPages speculative: 1024.\n'
        with patch('runtime_resources.platform.system',return_value='Darwin'), patch('runtime_resources.subprocess.check_output',side_effect=[str(8*GIB),vm]):
            total,available,_=memory_snapshot()
        self.assertEqual(total,8*GIB)
        self.assertEqual(available,4096*16384)
        with patch('runtime_resources.platform.system',return_value='Darwin'), patch('runtime_resources.subprocess.check_output',side_effect=OSError('unavailable')):
            total,available,source=memory_snapshot()
        self.assertIn('fallback',source)
        self.assertLess(size_worker({},'.',dict(self.snapshot(),total_memory_bytes=total,available_memory_bytes=available))['memory_mb'],4096)


if __name__=='__main__':unittest.main()
