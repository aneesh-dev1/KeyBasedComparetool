"""Several comparisons run at once; CPUs and memory are shared by input size."""
import tempfile
import unittest
from unittest.mock import patch

from server import Application


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = Application(self.temporary.name, max_jobs=4)
        self.addCleanup(self.app.pool.shutdown)
        self.addCleanup(self.app.light_pool.shutdown)

    def test_allocations_do_not_oversubscribe_and_release_capacity(self):
        with patch('runtime_resources.cpu_capacity', return_value=10):
            alone = self.app.allocate('big', 9_000)
            self.assertEqual((alone['cpus'], alone['memory_share']), (9, 1.0))
            self.assertIsNone(self.app.allocate('small', 1_000))
            self.assertNotIn('small', self.app.allocations)
            self.app.release('big')
            self.assertEqual(self.app.allocate('small', 1_000)['cpus'], 9)

    def test_large_host_allows_non_overlapping_concurrent_jobs(self):
        with patch('runtime_resources.cpu_capacity', return_value=40):
            allocations = [self.app.allocate(name, 1000) for name in ('a', 'b', 'c')]
            self.assertEqual([item['cpus'] for item in allocations], [16, 16, 7])
            self.assertAlmostEqual(sum(item['memory_share'] for item in allocations), 1)
            self.assertIsNone(self.app.allocate('d', 1000))
            self.app.release('b')
            self.assertEqual(self.app.allocate('d', 1000)['cpus'], 16)

    def test_single_core_runs_one_comparison_at_a_time(self):
        with patch('runtime_resources.cpu_capacity', return_value=1):
            self.assertEqual(self.app.allocate('a', 1)['cpus'], 1)
            self.assertIsNone(self.app.allocate('b', 1))

    def test_queued_comparison_wakes_after_release(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        started = threading.Event()
        job = {'files': {'left': {'size': 100}, 'right': {'size': 100}}}
        with patch('runtime_resources.cpu_capacity', return_value=4), patch.object(self.app, 'load', return_value=job), patch.object(self.app, '_run_comparison', side_effect=lambda *args: started.set()):
            self.app.allocate('active', 100)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.app.run_comparison, 'e' * 32)
                self.assertFalse(started.wait(.05))
                self.app.release('active')
                future.result(timeout=2)
            self.assertTrue(started.is_set())
            self.assertFalse(self.app.allocations)

    def test_queued_comparison_can_be_cancelled(self):
        from concurrent.futures import ThreadPoolExecutor
        from task_control import Cancelled
        from pathlib import Path
        job = {'files': {'left': {'size': 100}, 'right': {'size': 100}}}
        identity='f'*32
        folder=Path(self.temporary.name)/identity;folder.mkdir()
        with patch('runtime_resources.cpu_capacity', return_value=4), patch.object(self.app, 'load', return_value=job), patch.object(self.app, '_run_comparison') as launch:
            self.app.allocate('active', 100)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(self.app.run_comparison,identity)
                (folder/'cancel-comparison').touch()
                with self.assertRaises(Cancelled):future.result(timeout=2)
            launch.assert_not_called()
            self.assertNotIn(identity,self.app.allocations)
            self.app.release('active')

    def test_light_work_has_its_own_lane(self):
        self.assertIsNot(self.app.pool, self.app.light_pool)


if __name__ == '__main__':
    unittest.main()
