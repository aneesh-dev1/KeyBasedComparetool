"""Disk housekeeping: the analysis index is trimmed when unused, rebuilds on demand, and sizes are reported."""
import os
import time
import unittest

import test_ui


class HousekeepingTests(unittest.TestCase):
    setUpClass = classmethod(test_ui.ServerTests.setUpClass.__func__)
    tearDownClass = classmethod(test_ui.ServerTests.tearDownClass.__func__)
    request = test_ui.ServerTests.request
    wait = test_ui.ServerTests.wait

    def completed(self):
        left, right = b'id,v\n1,a\n2,b\n', b'id,v\n1,a\n2,c\n'
        job = self.request('/api/jobs', {'files': {'left': {'name': 'a.csv', 'size': len(left)}, 'right': {'name': 'b.csv', 'size': len(right)}}})
        path = '/api/jobs/' + job['id']
        for side, content in (('left', left), ('right', right)):
            self.request(path + f'/files/{side}?offset=0', content, 'PUT')
        self.request(path + '/finalize', {})
        self.request(path + '/start', {'keys': ['id'], 'memory_mb': 64})
        self.wait(path, lambda j: j['state'] == 'complete')
        self.request(path + '/analysis', {})
        self.wait(path + '/analysis', lambda j: j['state'] == 'complete')
        return path, job['id']

    def test_unused_analysis_is_trimmed_and_rebuilds(self):
        path, identity = self.completed()
        index = self.server.app.directory(identity) / 'analysis.sqlite'
        history = {item['id']: item for item in self.request('/api/jobs?offset=0')['jobs']}
        self.assertGreater(history[identity]['sizes']['analysis'], 0)
        self.assertGreater(history[identity]['sizes']['uploads'], 0)
        old = time.time() - 4 * 86400
        os.utime(index, (old, old))
        self.server.app.cleanup()
        self.assertFalse(index.exists())
        job = self.request(path)
        self.assertEqual((job['state'], job['analysis']['state']), ('complete', 'not_started'))
        self.request(path + '/analysis', {})
        self.assertEqual(self.wait(path + '/analysis', lambda j: j['state'] == 'complete')['state'], 'complete')

    def test_free_analysis_space_now(self):
        path, identity = self.completed()
        freed = self.request(path + '/analysis', method='DELETE')['freed']
        self.assertGreater(freed, 0)
        self.assertFalse((self.server.app.directory(identity) / 'analysis.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
