import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from server import Application, make_server
from test_team_server import Client

CONFIG = dict(files={side: dict(name='data.csv', size=10) for side in ('left', 'right')})

class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(self.tmp.name)
    def tearDown(self):
        self.app.pool.shutdown()
        self.tmp.cleanup()
    def old_job(self, state='complete'):
        job = self.app.create(CONFIG)
        job.update(state=state, created=1, finished=1)
        self.app.save(job)
        folder = self.app.directory(job['id'])
        (folder/'left.csv').write_text('source')
        (folder/'report').mkdir()
        (folder/'report'/'changes.csv').write_text('report')
        os.utime(folder/'job.json', (1, 1))
        return job, folder
    def test_expiry_removes_all_job_data_only(self):
        old, folder = self.old_job()
        fresh = self.app.create(CONFIG)
        library = Path(self.tmp.name)/'key-containers.json'
        library.write_text('[]')
        self.app.cleanup()
        self.assertFalse(folder.exists())
        self.assertTrue(self.app.directory(fresh['id']).exists())
        self.assertTrue(library.exists())
    def test_protects_active_work_and_transfers(self):
        for state in ('queued', 'running', 'preparing'):
            job, folder = self.old_job(state)
            with self.assertRaises(ValueError): self.app.remove_job(job['id'])
        exporting, export_folder = self.old_job()
        exporting['exports'] = {'excel': {'state': 'queued'}}
        self.app.save(exporting)
        os.utime(export_folder/'job.json', (1, 1))
        with self.assertRaises(ValueError): self.app.remove_job(exporting['id'])
        downloading, download_folder = self.old_job()
        self.app.requests[downloading['id']] = 1
        with self.assertRaises(ValueError): self.app.remove_job(downloading['id'])
        self.app.cleanup()
        self.assertEqual(len(list(Path(self.tmp.name).glob('*/job.json'))), 5)
        self.app.requests.clear()
        self.app.cleanup()
        self.assertFalse(download_folder.exists())
    def test_abandoned_upload_recent_activity_and_disabled_policy(self):
        old, folder = self.old_job('uploading')
        recent, recent_folder = self.old_job('ready')
        os.utime(recent_folder/'job.json', None)
        self.app.retention_days = 0
        self.app.cleanup()
        self.assertTrue(folder.exists())
        self.app.retention_days = 7
        self.app.cleanup()
        self.assertFalse(folder.exists())
        self.assertTrue(recent_folder.exists())
    def test_analysis_preparation_protects_job(self):
        job,folder=self.old_job()
        job['analysis']={'state':'running'}
        self.app.save(job)
        os.utime(folder/'job.json',(1,1))
        with self.assertRaises(ValueError): self.app.remove_job(job['id'])
        self.app.cleanup()
        self.assertTrue(folder.exists())

    def test_retries_interrupted_deletion(self):
        job, folder = self.old_job()
        trash = folder.with_name('.deleting-'+job['id'])
        folder.rename(trash)
        self.app.cleanup()
        self.assertFalse(trash.exists())
    def test_delete_is_workspace_scoped(self):
        server = make_server(Path(self.tmp.name)/'http', port=0, public_url='http://compare.internal')
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            a, b = Client(server), Client(server)
            job = a.request('/api/jobs', CONFIG)
            path = '/api/jobs/'+job['id']
            with self.assertRaises(HTTPError) as error: b.request(path, method='DELETE')
            self.assertEqual(error.exception.code, 404)
            server.app.patch(job['id'], state='running')
            with self.assertRaises(HTTPError): a.request(path, method='DELETE')
            server.app.patch(job['id'], state='error')
            self.assertTrue(a.request(path, method='DELETE')['deleted'])
            self.assertEqual(a.request('/api/jobs')['jobs'], [])
            self.assertFalse(server.app.directory(job['id']).exists())
        finally:
            server.shutdown(); thread.join(); server.server_close(); server.app.pool.shutdown()
