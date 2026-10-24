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

    def test_shares_follow_input_size_and_are_released(self):
        with patch('runtime_resources.cpu_capacity', return_value=10):
            alone = self.app.allocate('big', 9_000)
            self.assertEqual((alone['cpus'], alone['memory_share']), (10, 1.0))
            small = self.app.allocate('small', 1_000)
            # The big run keeps all ten CPUs it started with, so the small one still gets at least one.
            self.assertEqual(small['cpus'], 1)
            self.assertEqual(small['memory_share'], 0.1)
            self.app.release('big')
            self.app.release('small')
            even = [self.app.allocate(name, 5_000) for name in ('a', 'b')]
            self.assertEqual((even[0]['cpus'], even[1]['cpus'], even[1]['memory_share']), (10, 1, 0.5))
            self.app.release('a')
            # Equal weights: the newcomer gets its half of the CPUs (5), not every free one, leaving room for others.
            self.assertEqual(self.app.allocate('c', 5_000)['cpus'], 5)
            self.assertEqual(set(self.app.allocations), {'b', 'c'})

    def test_light_work_has_its_own_lane(self):
        self.assertIsNot(self.app.pool, self.app.light_pool)


if __name__ == '__main__':
    unittest.main()
