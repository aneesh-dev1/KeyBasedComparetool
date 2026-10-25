"""Comparing two runs of one project: totals, columns, settings and the cells that are new, resolved or changed."""
import csv
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError

import run_diff
from server import make_server
from test_team_server import Client


def report(folder, rows):
    folder.mkdir(parents=True)
    with (folder / 'differences.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['key_json', 'column', 'left_value', 'right_value'])
        writer.writerows([json.dumps(key), column, left, right] for key, column, left, right in rows)
    return folder


class CellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        long_a, long_b = 'x' * 400 + 'a', 'x' * 400 + 'b'
        self.base = report(root / 'base', [(['1'], 'v', 'a', 'b'), (['2'], 'v', 'a', 'b'), (['3'], 'w', 'p', 'q'), (['4'], 'w', long_a, 'z')])
        self.target = report(root / 'target', [(['2'], 'v', 'a', 'c'), (['3'], 'w', 'p', 'q'), (['4'], 'w', long_b, 'z'), (['5'], 'v', 'm', 'n'), (['5'], 'w', 'm', 'n')])
        self.path = run_diff.build(self.base, self.target, root / 'target' / 'run-diffs' / 'base.sqlite')

    def test_cells_are_paired_by_key_and_column(self):
        result = run_diff.cells(self.path, 'new')
        self.assertEqual(result['totals'], dict(new=2, resolved=1, changed=2, same=1))
        self.assertEqual(result['by_column'], dict(v=dict(new=1, resolved=1, changed=1, same=0), w=dict(new=1, resolved=0, changed=1, same=1)))
        # Key 5 is newly mismatching, key 1 no longer mismatches anywhere.
        self.assertEqual((result['new_keys'], result['resolved_keys']), (1, 1))
        self.assertEqual([(cell['key'], cell['column'], cell['base'], cell['target']) for cell in result['cells']],
                         [(['5'], 'v', None, ['m', 'n']), (['5'], 'w', None, ['m', 'n'])])
        resolved = run_diff.cells(self.path, 'resolved')['cells']
        self.assertEqual([(cell['key'], cell['base'], cell['target']) for cell in resolved], [(['1'], ['a', 'b'], None)])

    def test_long_values_compare_in_full_and_show_shortened(self):
        changed = run_diff.cells(self.path, 'changed', column='w')
        self.assertEqual(changed['total'], 1)
        cell = changed['cells'][0]
        self.assertEqual(cell['key'], ['4'])
        self.assertEqual(len(cell['base'][0]), run_diff.SHOWN)
        self.assertEqual(cell['base'][0], cell['target'][0])

    def test_built_once_and_paged(self):
        stamp = self.path.stat().st_mtime_ns
        self.assertEqual(run_diff.build(self.base, self.target, self.path), self.path)
        self.assertEqual(self.path.stat().st_mtime_ns, stamp)
        self.assertEqual(run_diff.cells(self.path, 'new', offset=1)['cells'][0]['column'], 'w')
        with self.assertRaises(ValueError):
            run_diff.cells(self.path, 'everything')

    def test_setting_changes_are_described(self):
        base = dict(keys=['id'], ignore_columns=['a'], comparison_rules=[], value_overrides=[])
        target = dict(keys=['id'], ignore_columns=['b'], comparison_rules=[dict(column='v', trim=True)], value_overrides=[], ignore_keys='7')
        changes = run_diff.setting_changes(base, target)
        self.assertEqual(changes, ['Ignored columns changed: 1 more ignored (b); 1 no longer ignored (a).', 'Excluded keys changed.',
                                   'Comparison rules changed (0 → 1 columns with rules).'])
        self.assertEqual(run_diff.setting_changes(base, base), [])


class ProjectCompareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.server = make_server(self.temporary.name, 0, public_url='http://compare.internal', max_sort_mb=64)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.client = Client(self.server)

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close(); self.server.app.pool.shutdown(); self.temporary.cleanup()

    def upload(self, left, right):
        job = self.client.request('/api/jobs', dict(files={side: dict(name=side + '.csv', size=len(data)) for side, data in [('left', left), ('right', right)]}))
        path = '/api/jobs/' + job['id']
        for side, data in [('left', left), ('right', right)]:
            self.client.request(path + '/files/' + side + '?offset=0', data, 'PUT')
        self.client.request(path + '/finalize', {})
        return path

    def complete(self, path):
        self.client.request(path + '/start', dict(keys=['id'], memory_mb=64, sort_workers=1))
        result = self.client.wait(path, lambda job: job['state'] in ('complete', 'error', 'cancelled'))
        self.assertEqual(result['state'], 'complete', result)
        return result

    def test_compare_two_runs_of_a_project(self):
        first = self.complete(self.upload(b'id,v,w\n1,a,p\n2,a,p\n3,a,p\n', b'id,v,w\n1,b,p\n2,b,p\n3,a,p\n'))
        second = self.complete(self.upload(b'id,v,w\n1,a,p\n2,a,p\n3,a,p\n', b'id,v,w\n1,b,p\n2,a,p\n3,a,q\n'))
        project = self.client.request('/api/projects', {'name': 'Monthly ledger'})['id']
        path = '/api/projects/' + project + '/compare'
        self.client.request('/api/projects/records', dict(comparison_id=first['id'], project_id=project))
        with self.assertRaises(HTTPError):
            self.client.request(path)
        self.client.request('/api/projects/records', dict(comparison_id=second['id'], project_id=project))
        # With no runs chosen, the latest run is compared with the one before it.
        result = self.client.request(path)
        self.assertEqual((result['base'], result['target']), (first['id'], second['id']))
        self.assertEqual(result['counts']['changed_cells'], dict(base=2, target=2))
        self.assertEqual({item['column']: (item['base'], item['target']) for item in result['columns']}, dict(v=(2, 1), w=(0, 1)))
        self.assertEqual(result['settings'], [])
        self.assertEqual(result['cells']['totals'], dict(new=1, resolved=1, changed=0, same=1))
        self.assertEqual(result['cells']['cells'][0]['key'], ['3'])
        resolved = self.client.request(path + f'?base={first["id"]}&target={second["id"]}&status=resolved&column=v')
        self.assertEqual([(cell['key'], cell['base']) for cell in resolved['cells']['cells']], [(['2'], ['a', 'b'])])
        self.assertEqual([run['id'] for run in result['runs']], [first['id'], second['id']])
        # Runs from another project, or the same run twice, are refused.
        for query in (f'?base={first["id"]}&target={first["id"]}', f'?base={first["id"]}&target={"0" * 32}'):
            with self.assertRaises(HTTPError):
                self.client.request(path + query)
        # Once a run's results are deleted, its totals can still be compared but its cells cannot.
        self.client.request('/api/jobs/' + first['id'], method='DELETE')
        totals = self.client.request(path)
        self.assertIn('cells_unavailable', totals)
        self.assertEqual(totals['counts']['changed_cells'], dict(base=2, target=2))


if __name__ == '__main__':
    unittest.main()
