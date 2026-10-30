"""Project rounds: comparing a sample of keys, carrying a round's keys forward, and a project's status and issues."""
import contextlib
import csv
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import partitioned
import sampling
from compare import compare, parser
from projects import progress
from server import make_server
from test_team_server import Client


def write(path, rows):
    with open(path, 'w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['id', 'status', 'amount'])
        writer.writerows(rows)


def data(count=400, broken=('status',)):
    left = [[f'K{i:04d}', 'open', str(i)] for i in range(count)]
    right = [[f'K{i:04d}', 'void' if 'status' in broken and i % 4 == 0 else 'open', str(i + 1) if 'amount' in broken and i % 5 == 0 else str(i)] for i in range(count)]
    return left, right


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        left, right = data(3000, ('status', 'amount'))
        write(self.root / 'left.csv', left)
        write(self.root / 'right.csv', right)

    def run_engine(self, name, engine='auto', **sample):
        args = parser().parse_args([str(self.root / 'left.csv'), str(self.root / 'right.csv'), '--keys', 'id', '--output', str(self.root / name),
                                    '--temp-dir', str(self.root), '--memory-mb', '64', '--engine', engine])
        args.parallel_workers = 3
        args.sample = sample
        with patch.object(partitioned, 'RANGE_BYTES', 8 * 1024), contextlib.redirect_stdout(io.StringIO()):
            summary = compare(args)
        return summary, sampling.load_keys(self.root / name)

    def test_every_engine_compares_the_same_sample(self):
        results = {engine: self.run_engine('out-' + engine, engine, size=40, seed=11) for engine in ('auto', 'sort', 'partitioned')}
        keys = results['auto'][1]
        self.assertEqual(len(keys), 40)
        for engine, (summary, sample) in results.items():
            with self.subTest(engine=engine):
                self.assertEqual(sample, keys)
                self.assertEqual(summary['matched_keys'], 40)
                self.assertEqual((summary['sample']['size'], summary['sample']['seed'], summary['sample']['population_rows']), (40, 11, 3000))
        expected = sum(1 for (key,) in keys for broken in (int(key[1:]) % 4 == 0, int(key[1:]) % 5 == 0) if broken)
        self.assertEqual(results['auto'][0]['changed_cells'], expected)

    def test_seed_repeats_and_carry_keeps_earlier_keys(self):
        _, first = self.run_engine('first', size=25, seed=3)
        _, again = self.run_engine('again', size=25, seed=3)
        _, other = self.run_engine('other', size=25, seed=4)
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        summary, grown = self.run_engine('grown', size=60, seed=9, carry=first)
        self.assertTrue(all(key in grown for key in first))
        self.assertEqual((len(grown), summary['sample']['carried'], summary['sample']['drawn']), (60, 25, 35))

    def test_excluded_keys_are_never_drawn(self):
        args = parser().parse_args([str(self.root / 'left.csv'), str(self.root / 'right.csv'), '--keys', 'id', '--output', str(self.root / 'x'),
                                    '--temp-dir', str(self.root), '--memory-mb', '64'])
        args.ignore_keys = ','.join(f'K{i:04d}' for i in range(2990))
        args.sample = dict(size=50, seed=1)
        with contextlib.redirect_stdout(io.StringIO()):
            summary = compare(args)
        self.assertEqual(summary['sample']['size'], 10)
        self.assertEqual(summary['matched_keys'], 10)


def record(identity, created, failing=(), checked=None, state='complete', audit='Unreviewed', population=1000, carried=0, left_only=0):
    sample = dict(size=checked, population_rows=population, carried=carried, seed=1) if checked else None
    return dict(id=identity, kind='table', state=state, created=created, finished=created, title='a.csv ↔ b.csv', left_rows=population,
                matched_keys=checked or population, left_only=left_only, right_only=0, changed_cells=len(failing) * 3, changed_rows=len(failing),
                mismatch_columns={name: 3 for name in failing}, sample=sample, audit_status=audit, source_available=True)


class ProgressTests(unittest.TestCase):
    def test_rounds_compare_failing_columns_with_the_round_before(self):
        result = progress([record('a', 1, ('x', 'y', 'z'), 10), record('b', 2, ('y', 'z', 'w'), 50, carried=10), record('c', 3, ('w',), 500)])
        rounds = result['rounds']
        self.assertEqual([(r['n'], r['still'], r['new'], r['fixed']) for r in rounds], [(1, 0, 3, 0), (2, 2, 1, 1), (3, 1, 0, 2)])
        self.assertEqual([r['how'] for r in rounds], ['random', 'carry', 'random'])
        self.assertEqual((result['status'], result['population'], result['checked']), ('fixing', 1000, 500))
        issues = result['issues']
        self.assertEqual([item['column'] for item in issues['still']['items']], ['w'])
        self.assertEqual(issues['still']['items'][0]['since'], 2)
        self.assertEqual(issues['still']['items'][0]['trail'], ['c', 'f', 'f'])
        self.assertEqual(sorted(item['column'] for item in issues['fixed']['items']), ['y', 'z'])
        self.assertEqual(issues['fixed']['items'][0]['since'], 1)

    def test_status_follows_the_latest_round(self):
        self.assertEqual(progress([])['status'], 'empty')
        self.assertEqual(progress([record('a', 1, (), 10)])['status'], 'ready')
        self.assertEqual(progress([record('a', 1, ())])['status'], 'clean')
        self.assertEqual(progress([record('a', 1, (), left_only=2)])['status'], 'fixing')
        # The project is signed off by its own sign-off of the latest round, not by a round's audit status.
        self.assertEqual(progress([record('a', 1, ('x',), audit='Approved')])['status'], 'fixing')
        self.assertEqual(progress([record('a', 1, ('x',))], dict(round_id='a'))['status'], 'signed_off')
        self.assertEqual(progress([record('a', 1, ()), record('b', 2, ('x',))], dict(round_id='a'))['status'], 'fixing')
        self.assertEqual(progress([record('a', 1, ()), record('b', 2, state='running')])['status'], 'running')
        # Unfinished or failed comparisons are not rounds.
        self.assertEqual(len(progress([record('a', 1, ()), record('b', 2, state='error')])['rounds']), 1)


class ServerRoundTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.server = make_server(self.temporary.name, 0, public_url='http://compare.internal', max_sort_mb=64)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.client = Client(self.server)

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close(); self.server.app.pool.shutdown(); self.temporary.cleanup()

    def upload(self, broken):
        left, right = data(300, broken)
        files = {}
        for side, rows in (('left', left), ('right', right)):
            stream = io.StringIO();csv.writer(stream).writerows([['id', 'status', 'amount']] + rows);files[side] = stream.getvalue().encode()
        job = self.client.request('/api/jobs', dict(files={side: dict(name=side + '.csv', size=len(body)) for side, body in files.items()}))
        path = '/api/jobs/' + job['id']
        for side, body in files.items():
            self.client.request(path + '/files/' + side + '?offset=0', body, 'PUT')
        self.client.request(path + '/finalize', {})
        self.client.wait(path, lambda job: job['state'] == 'ready')
        return path

    def start(self, path, **config):
        self.client.request(path + '/start', dict(keys=['id'], memory_mb=64, sort_workers=1, **config))
        return self.client.wait(path, lambda job: job['state'] in ('complete', 'error'))

    def refused(self, path, **config):
        with self.assertRaises(HTTPError) as caught:
            self.client.request(path + '/start', dict(keys=['id'], memory_mb=64, sort_workers=1, **config))
        return json.loads(caught.exception.read())['error']

    def test_rounds_of_a_project(self):
        project = self.client.request('/api/projects', {'name': 'Ledger rounds'})['id']
        first = self.start(self.upload(('status', 'amount')), project_id=project, sample=dict(size=8, mode='random', seed=5))
        self.assertEqual(first['state'], 'complete', first.get('error'))
        self.assertEqual((first['summary']['sample']['size'], first['summary']['matched_keys']), (8, 8))
        second_path = self.upload(('amount',))
        self.assertIn('at least that many', self.refused(second_path, sample=dict(size=4, mode='carry', **{'from': first['id']})))
        self.assertIn('no longer exists', self.refused(second_path, project_id='nope'))
        self.assertIn('1 to 50,000,000', self.refused(second_path, sample=dict(size=0)))
        second = self.start(second_path, project_id=project, sample=dict(size=20, mode='carry', seed=6, **{'from': first['id']}))
        self.assertEqual((second['summary']['sample']['carried'], second['summary']['sample']['size']), (8, 20))
        # A round on every key cannot be carried forward: it has no sample.
        third = self.start(self.upload(()), project_id=project)
        self.assertNotIn('sample', third)
        self.assertIn('no sample to carry forward', self.refused(self.upload(()), sample=dict(size=50, mode='carry', **{'from': third['id']})))
        detail = self.client.request('/api/projects/' + project)
        rounds = detail['progress']['rounds']
        self.assertEqual([(r['n'], r['keys_checked'], r['how']) for r in rounds], [(1, 8, 'random'), (2, 20, 'carry'), (3, 300, 'all')])
        # Round 3 is clean, so every column that failed in round 2 is fixed in round 3.
        failing = {name for name, count in second['summary']['changed_cells_by_column'].items() if count}
        self.assertEqual({item['column'] for item in detail['progress']['issues']['fixed']['items']}, failing)
        self.assertEqual(detail['progress']['status'], 'clean')
        overview = next(item for item in self.client.request('/api/projects')['projects'] if item['id'] == project)
        self.assertEqual((overview['status'], overview['checked'], overview['population'], len(overview['rounds'])), ('clean', 300, 300, 3))

    def sign_off(self, project, **body):
        return self.client.request(f'/api/projects/{project}/signoff', dict(reviewer='Asha', **body))

    def test_project_sign_off(self):
        project = self.client.request('/api/projects', {'name': 'Sign-off project'})['id']
        first = self.start(self.upload(('status', 'amount')), project_id=project)
        failing = sorted(name for name, count in first['summary']['changed_cells_by_column'].items() if count)
        self.assertEqual(failing, ['amount', 'status'])
        # Mismatches remain: every column needs a reason (its own, or the shared one), and a name.
        with self.assertRaises(HTTPError) as caught:
            self.sign_off(project, reasons=dict(columns=dict(status='Remapped codes')))
        self.assertIn('Give a reason for amount', json.loads(caught.exception.read())['error'])
        with self.assertRaises(HTTPError) as caught:
            self.client.request(f'/api/projects/{project}/signoff', dict(reviewer=' ', reasons=dict(others='x')))
        self.assertIn('Enter your name', json.loads(caught.exception.read())['error'])
        detail = self.sign_off(project, note='Agreed with finance', reasons=dict(columns=dict(status='Remapped codes'), others='Rounding by design'))
        state = detail['progress']
        self.assertEqual((state['status'], state['signoff']['mode']), ('signed_off', 'exceptions'))
        self.assertEqual({item['column']: item['reason'] for item in state['signoff']['reasons']['columns']}, dict(status='Remapped codes', amount='Rounding by design'))
        report = self.client.request(f'/api/projects/{project}/signoff/report').decode()
        self.assertIn('Signed off with reasons', report)
        self.assertIn('Rounding by design', report)
        # Each round's own report is included in full (a viewer chip per round), with times in IST.
        self.assertEqual(report.count('class="round-chip"'), 1)
        self.assertIn(' IST', report)
        # A new round reopens the project; at 100% match it signs off without reasons.
        self.start(self.upload(()), project_id=project)
        self.assertEqual(self.client.request('/api/projects/' + project)['progress']['status'], 'clean')
        detail = self.sign_off(project)
        self.assertEqual((detail['progress']['status'], detail['progress']['signoff']['mode']), ('signed_off', 'clean'))
        report = self.client.request(f'/api/projects/{project}/signoff/report').decode()
        self.assertIn('Signed off at 100% match', report)
        self.assertEqual(report.count('class="round-chip"'), 2)
        overview = next(item for item in self.client.request('/api/projects')['projects'] if item['id'] == project)
        self.assertEqual((overview['status'], overview['signoff']['mode']), ('signed_off', 'clean'))


if __name__ == '__main__':
    unittest.main()
