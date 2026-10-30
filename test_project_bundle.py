"""Exporting a project as data and importing it again, with its template."""
import io
import json
import tempfile
import threading
import unittest
import zipfile
from urllib.error import HTTPError

from server import make_server
import test_project_rounds
from test_team_server import Client


class ProjectBundleTests(unittest.TestCase):
    upload, start = test_project_rounds.ServerRoundTests.upload, test_project_rounds.ServerRoundTests.start

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.server = make_server(self.temporary.name, 0, public_url='http://compare.internal', max_sort_mb=64)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.client = Client(self.server)

    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close(); self.server.app.pool.shutdown(); self.temporary.cleanup()

    def error(self, path, body, method=None):
        with self.assertRaises(HTTPError) as caught:
            self.client.request(path, body, method, {'Content-Type': 'application/zip'} if isinstance(body, bytes) else None)
        return caught.exception.code, json.loads(caught.exception.read())

    def project(self):
        project = self.client.request('/api/projects', {'name': 'Ledger rounds', 'description': 'Card ledger migration'})['id']
        first = self.start(self.upload(('status',)), project_id=project, sample=dict(size=8, mode='random', seed=5))
        second = self.start(self.upload(()), project_id=project)
        self.client.request('/api/projects/records', dict(comparison_id=first['id'], project_id=project, status='Approved', note='Checked', reviewer='Ravi'))
        self.client.request(f'/api/projects/{project}/signoff', dict(reviewer='Asha', note='All keys match'))
        saved = self.client.request(f'/api/projects/{project}/template', dict(name='Ledger settings'))
        self.assertEqual(saved['profile']['name'], 'Ledger settings')
        return project, first, second

    def test_export_and_import(self):
        project, first, second = self.project()
        bundle = self.client.request(f'/api/projects/{project}/export')
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read('manifest.json'))
        # Data only: every round's result files, no HTML.
        self.assertIn('rounds/1/report/differences.csv', names)
        self.assertIn('rounds/1/report/sample_keys.json', names)
        self.assertFalse([name for name in names if name.endswith('.html')])
        self.assertEqual((len(manifest['rounds']), manifest['template']['name'], manifest['signoffs'][0]['round']), (2, 'Ledger settings', 2))
        self.assertEqual(manifest['rounds'][0]['audit_status'], 'Approved')

        preview = self.client.request('/api/projects/import', bundle, 'POST', {'Content-Type': 'application/zip'})
        self.assertEqual((preview['name'], preview['rounds'], preview['signoffs'], preview['template'], preview['exists']),
                         ('Ledger rounds', 2, 1, 'Ledger settings', True))
        # The same name is refused with a notice, so the user can choose another.
        code, body = self.error('/api/projects/import/' + preview['upload_id'], {'name': 'ledger rounds'})
        self.assertEqual(code, 409); self.assertTrue(body['exists']); self.assertIn('already exists', body['error'])
        copy = self.client.request('/api/projects/import/' + preview['upload_id'], {'name': 'Ledger rounds (copy)'})['id']

        original, imported = (self.client.request('/api/projects/' + identity) for identity in (project, copy))
        shape = lambda detail: [(r['n'], r['keys_checked'], r['how'], r['mismatching'], r['clean']) for r in detail['progress']['rounds']]
        self.assertEqual(shape(imported), shape(original))
        self.assertEqual((imported['progress']['status'], imported['template']['name'], imported['project']['description']),
                         ('signed_off', 'Ledger settings', 'Card ledger migration'))
        self.assertEqual(imported['progress']['signoff']['reviewer'], 'Asha')
        rounds = sorted(imported['records'], key=lambda record: record['created'])
        self.assertEqual((rounds[0]['audit_status'], rounds[0]['reviewer'], rounds[0]['created']), ('Approved', 'Ravi', first['created']))
        messages = [event['message'] for event in imported['events']]
        self.assertIn('Project signed off at 100% match · Asha', messages)
        self.assertTrue(messages[0].startswith('Imported from an export of “Ledger rounds”'))
        # Reports are rebuilt from the data with this app's templates.
        job = self.client.request('/api/jobs/' + rounds[0]['id'])
        self.assertEqual(job['exports']['html']['state'], 'complete')
        self.assertEqual(job['summary']['changed_cells_by_column'], first['summary']['changed_cells_by_column'])
        report = self.client.request(f'/api/projects/{copy}/signoff/report').decode()
        self.assertEqual(report.count('class="round-chip"'), 2)
        # The template is reused, not copied.
        self.assertEqual([p['name'] for p in self.client.request('/api/profiles')['profiles']], ['Ledger settings'])

    def test_restore_after_deleting_everything(self):
        """Export, delete the comparisons, the template and the project, then bring it all back from the file."""
        project, first, second = self.project()
        before = self.client.request('/api/projects/' + project)
        template = before['template']['id']
        bundle = self.client.request(f'/api/projects/{project}/export')
        for job in (first, second):
            self.client.request('/api/jobs/' + job['id'], None, 'DELETE')
        self.client.request('/api/profiles', {'id': template}, 'DELETE')
        # The app has no project delete yet; remove the project the way one would.
        from projects import Store, database
        Store(database(self.server.app.root)).remove(project)
        self.assertNotIn('Ledger rounds', [item['name'] for item in self.client.request('/api/projects')['projects']])

        preview = self.client.request('/api/projects/import', bundle, 'POST', {'Content-Type': 'application/zip'})
        self.assertFalse(preview['exists'])
        restored = self.client.request('/api/projects/import/' + preview['upload_id'], {'name': 'Ledger rounds'})['id']
        after = self.client.request('/api/projects/' + restored)
        shape = lambda detail: [(r['n'], r['keys_checked'], r['how'], r['mismatching'], r['clean'], r['created']) for r in detail['progress']['rounds']]
        self.assertEqual(shape(after), shape(before))
        self.assertEqual((after['progress']['status'], after['template']['name']), ('signed_off', 'Ledger settings'))
        self.assertEqual(self.client.request('/api/profiles')['profiles'][0]['name'], 'Ledger settings')
        round_one = min(after['records'], key=lambda record: record['created'])
        self.assertEqual(round_one['audit_status'], 'Approved')
        self.assertEqual(self.client.request(f'/api/projects/{restored}/signoff/report').decode().count('class="round-chip"'), 2)
        # Cell differences came back: the round's differences file matches, and Recompare works from it alone.
        job = self.client.request('/api/jobs/' + round_one['id'])
        self.assertEqual(job['summary']['changed_cells_by_column'], first['summary']['changed_cells_by_column'])
        failing = next(name for name, count in job['summary']['changed_cells_by_column'].items() if count)
        change = dict(ignore_columns=[failing])
        plan = self.client.request(f"/api/jobs/{round_one['id']}/review-plan", change)
        self.assertEqual(plan['mode'], 'review')
        copy = self.client.request(f"/api/jobs/{round_one['id']}/review-copy", dict(change, execution_mode='review'))
        self.client.request(f"/api/jobs/{copy['id']}/start", dict(keys=['id'], execution_mode='review', memory_mb=64, sort_workers=1, **change))
        rerun = self.client.wait('/api/jobs/' + copy['id'], lambda job: job['state'] in ('complete', 'error'))
        self.assertEqual(rerun['state'], 'complete', rerun.get('error'))
        self.assertEqual(rerun['summary']['changed_cells_by_column'].get(failing, 0), 0)
        # Changing the key columns needs the original files, which an export doesn't carry.
        self.assertEqual(self.client.request(f"/api/jobs/{round_one['id']}/review-plan", dict(keys=['status']))['mode'], 'full')

    def test_refuses_files_that_are_not_exports(self):
        code, body = self.error('/api/projects/import', b'not a zip', 'POST')
        self.assertIn('isn’t a project export', body['error'])
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(dict(format='datacompare-project', version=1, project=dict(name='x'), rounds=[])))
            archive.writestr('rounds/1/report/../../../escape.txt', 'x')
        code, body = self.error('/api/projects/import', stream.getvalue(), 'POST')
        self.assertIn('unexpected file', body['error'])

    def test_new_project_from_a_template(self):
        project, _, _ = self.project()
        template = self.client.request('/api/projects/' + project)['template']
        fresh = self.client.request('/api/projects', {'name': 'Next ledger', 'profile_id': template['id']})['id']
        self.assertEqual(self.client.request('/api/projects/' + fresh)['template']['name'], 'Ledger settings')
        code, body = self.error('/api/projects', {'name': 'Bad', 'profile_id': 'f' * 32})
        self.assertIn('no longer exists', body['error'])


if __name__ == '__main__':
    unittest.main()
