"""Subprojects (one level under a parent that only groups them) and the date a user gives a comparison."""
import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError

from server import make_server
import test_project_rounds
from test_team_server import Client


class SubprojectTests(unittest.TestCase):
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
            self.client.request(path, body, method)
        return json.loads(caught.exception.read())['error']

    def create(self, name, parent=None):
        return self.client.request('/api/projects', dict(name=name, **({'parent_id': parent} if parent else {})))['id']

    def test_parent_groups_subprojects(self):
        parent = self.create('Card migration')
        cards, loans = self.create('Cards', parent), self.create('Loans', parent)
        self.assertIn('can’t have subprojects', self.error('/api/projects', dict(name='Deeper', parent_id=cards)))
        # A parent holds no comparisons: rounds and attachments go to a subproject.
        path = self.upload(())
        self.assertIn('groups subprojects', json.loads(self._refused(path, project_id=parent))['error'])
        first = self.start(path, project_id=cards, as_of='2026-09-30')
        self.assertIn('groups subprojects', self.error('/api/projects/records', dict(comparison_id=first['id'], project_id=parent)))
        # A project with comparisons can't become a parent.
        standalone = self.create('Standalone')
        self.start(self.upload(()), project_id=standalone)
        self.assertIn('already has comparisons', self.error('/api/projects', dict(name='Under standalone', parent_id=standalone)))
        self.client.request(f'/api/projects/{standalone}', {'comparisons': True}, 'DELETE')
        self.client.request(f'/api/projects/{cards}/signoff', dict(reviewer='Asha'))

        row = next(p for p in self.client.request('/api/projects')['projects'] if p['id'] == parent)
        self.assertEqual((row['signed_off'], [s['name'] for s in row['subprojects']], row['status'], row['status_label']), (1, ['Cards', 'Loans'], 'empty', 'In progress'))
        detail = self.client.request('/api/projects/' + parent)
        self.assertEqual([(s['name'], s['status']) for s in detail['subprojects']], [('Cards', 'signed_off'), ('Loans', 'empty')])
        self.assertEqual(self.client.request('/api/projects/' + cards)['parent'], dict(id=parent, name='Card migration'))
        self.assertIsNone(self.client.request('/api/projects/' + cards)['subprojects'])
        self.assertIn('exported with their project', self.error(f'/api/projects/{cards}/export', None))

        # Signing off the last subproject signs off the parent.
        self.start(self.upload(()), project_id=loans)
        self.client.request(f'/api/projects/{loans}/signoff', dict(reviewer='Asha'))
        row = next(p for p in self.client.request('/api/projects')['projects'] if p['id'] == parent)
        self.assertEqual((row['status'], row['signed_off']), ('signed_off', 2))

        # The parent exports with its subprojects and imports whole, next to the original: subproject names only
        # need to be unique within their parent.
        bundle = self.client.request(f'/api/projects/{parent}/export')
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            self.assertIn('subprojects/1/rounds/1/job.json', archive.namelist())
            self.assertIn('subprojects/2/rounds/1/report/differences.csv', archive.namelist())
        preview = self.client.request('/api/projects/import', bundle, 'POST', {'Content-Type': 'application/zip'})
        self.assertEqual((preview['subprojects'], preview['rounds'], preview['signoffs'], preview['exists']), (['Cards', 'Loans'], 2, 2, True))
        copy = self.client.request('/api/projects/import/' + preview['upload_id'], {'name': 'Card migration copy'})['id']
        imported = self.client.request('/api/projects/' + copy)
        self.assertEqual([(s['name'], s['status']) for s in imported['subprojects']], [('Cards', 'signed_off'), ('Loans', 'signed_off')])
        copied_cards = self.client.request('/api/projects/' + imported['subprojects'][0]['id'])
        self.assertEqual((copied_cards['parent']['name'], copied_cards['progress']['rounds'][0]['as_of']), ('Card migration copy', '2026-09-30'))
        self.assertEqual(self.client.request(f"/api/projects/{imported['subprojects'][0]['id']}/signoff/report").count(b'class="round-chip"'), 1)
        self.assertIn('already exists in this project', self.error('/api/projects', dict(name='cards', parent_id=parent)))

        result = self.client.request('/api/projects/' + parent, {'comparisons': True}, 'DELETE')
        self.assertEqual(result['comparisons'], 2)
        self.assertEqual([p['name'] for p in self.client.request('/api/projects')['projects'] if not p.get('parent_id')], ['Card migration copy'])
        self.assertFalse((self.server.app.root / first['id']).exists())

    def test_older_database_drops_app_wide_unique_names(self):
        import sqlite3
        from pathlib import Path
        from projects import Store
        path = Path(self.temporary.name) / 'old.sqlite'
        db = sqlite3.connect(path)
        db.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL COLLATE NOCASE UNIQUE,description TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE records(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),snapshot TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'Unreviewed',note TEXT NOT NULL DEFAULT '',reviewer TEXT NOT NULL DEFAULT '',updated REAL NOT NULL);
            INSERT INTO projects VALUES('a','Ledger','',1);
            INSERT INTO records VALUES('r','a','{"id":"r","state":"complete","created":1}','Approved','','',1);''')
        db.commit(); db.close()
        store = Store(path)
        parent = store.create(dict(name='Group'))
        store.create(dict(name='Ledger', parent_id=parent))
        with store.connect() as connection:
            self.assertNotIn('UNIQUE', connection.execute("SELECT sql FROM sqlite_master WHERE name='projects'").fetchone()[0].upper())
            self.assertEqual(connection.execute("SELECT project_id,status FROM records WHERE id='r'").fetchone()[:], ('a', 'Approved'))
        with self.assertRaises(ValueError):
            store.create(dict(name='ledger'))

    def _refused(self, path, **config):
        with self.assertRaises(HTTPError) as caught:
            self.client.request(path + '/start', dict(keys=['id'], memory_mb=64, sort_workers=1, **config))
        return caught.exception.read()

    def test_comparison_date(self):
        project = self.create('Dated')
        self.assertIn('YYYY-MM-DD', json.loads(self._refused(self.upload(()), project_id=project, as_of='30/09/2026'))['error'])
        first = self.start(self.upload(('status',)), project_id=project, as_of='2026-09-30')
        second = self.start(self.upload(()), project_id=project)
        rounds = self.client.request('/api/projects/' + project)['progress']['rounds']
        self.assertEqual([r['as_of'] for r in rounds], ['2026-09-30', None])
        # Set, change and clear a date from the project; the activity says so.
        record = dict(comparison_id=second['id'], project_id=project, status='Unreviewed', note='', reviewer='')
        self.assertIn('YYYY-MM-DD', self.error('/api/projects/records', dict(record, as_of='tomorrow')))
        self.client.request('/api/projects/records', dict(record, as_of='2026-10-31'))
        detail = self.client.request('/api/projects/' + project)
        self.assertEqual(detail['progress']['rounds'][1]['as_of'], '2026-10-31')
        self.assertEqual(detail['events'][0]['message'], 'Date set: 2026-10-31')
        # Saving an audit without a date keeps the date.
        self.client.request('/api/projects/records', dict(comparison_id=second['id'], project_id=project, status='Approved', note='', reviewer='R'))
        self.assertEqual(self.client.request('/api/projects/' + project)['progress']['rounds'][1]['as_of'], '2026-10-31')
        self.client.request('/api/projects/records', dict(record, as_of=None))
        self.assertIsNone(self.client.request('/api/projects/' + project)['progress']['rounds'][1]['as_of'])
        # The sign-off report shows the user's date and the run date; exports carry the date.
        self.client.request(f'/api/projects/{project}/signoff', dict(reviewer='Asha'))
        report = self.client.request(f'/api/projects/{project}/signoff/report').decode()
        self.assertIn('30 Sep 2026<br><small class="muted">ran ', report)
        bundle = self.client.request(f'/api/projects/{project}/export')
        upload = self.client.request('/api/projects/import', bundle, 'POST', {'Content-Type': 'application/zip'})['upload_id']
        copy = self.client.request('/api/projects/import/' + upload, {'name': 'Dated copy'})['id']
        self.assertEqual([r['as_of'] for r in self.client.request('/api/projects/' + copy)['progress']['rounds']], ['2026-09-30', None])


if __name__ == '__main__':
    unittest.main()
