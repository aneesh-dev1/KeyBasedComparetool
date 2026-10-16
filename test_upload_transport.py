import gzip
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from server import Application, CHUNK, Handler, make_server
from test_team_server import Client


class UploadTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(self.temp.name)
        self.job = self.app.create({'files': {side: {'name': side + '.csv', 'size': CHUNK * 2}
                                              for side in ('left', 'right')}})
        self.identity = self.job['id']

    def tearDown(self):
        self.app.pool.shutdown()
        self.temp.cleanup()

    def send(self, data, offset=0, original_size=None, encoding='identity', reader=None):
        return self.app.receive_upload(self.identity, 'left', offset, len(data),
                                       len(data) if original_size is None else original_size,
                                       encoding, reader or io.BytesIO(data))

    def test_gzip_preserves_exact_original_bytes_and_bounds_reads(self):
        original = (b'id,value\r\n001,"a\nquoted \"\"value\"\""\r\n'
                    + '002,caf\u00e9 \u20b9\r\n'.encode() + bytes(range(256))) * 7000
        encoded = gzip.compress(original)

        class BoundedReader(io.BytesIO):
            def read(inner, count):
                self.assertLessEqual(count, 1024 * 1024)
                return super().read(count)

        uploaded = self.send(encoded, original_size=len(original), encoding='gzip', reader=BoundedReader(encoded))
        self.assertEqual(uploaded, len(original))
        self.assertEqual(self.app.upload_path(self.job, 'left').read_bytes(), original)
        self.assertEqual(self.app.load(self.identity)['files']['left']['uploaded'], len(original))
        self.assertFalse(self.app.upload_journal(self.identity, 'left').exists())
        self.assertEqual(self.send(b'tail', offset=uploaded), uploaded + 4)

    def test_invalid_or_bomb_gzip_rolls_back_only_current_chunk(self):
        self.send(b'prefix')
        encoded = gzip.compress(b'abc' * 1000)
        cases = [(b'not gzip', 3000), (encoded[:-3], 3000), (encoded + b'extra', 3000),
                 (encoded + encoded, 6000), (encoded, 2999), (encoded, 3001),
                 (gzip.compress(b'x' * (CHUNK + 1)), 10)]
        for data, declared in cases:
            with self.subTest(declared=declared, bytes=len(data)):
                with self.assertRaises(ValueError):
                    self.send(data, offset=6, original_size=declared, encoding='gzip')
                self.assertEqual(self.app.upload_path(self.job, 'left').read_bytes(), b'prefix')
                self.assertEqual(self.app.load(self.identity)['files']['left']['uploaded'], 6)
                self.assertFalse(self.app.upload_journal(self.identity, 'left').exists())

    def test_disconnect_and_duplicate_offset_preserve_acknowledged_data(self):
        self.send(b'prefix')
        with self.assertRaisesRegex(ValueError, 'disconnected'):
            self.send(b'complete chunk', offset=6, reader=io.BytesIO(b'partial'))
        with self.assertRaisesRegex(ValueError, 'Expected offset 6'):
            self.send(b'prefix', offset=0)
        self.assertEqual(self.app.upload_path(self.job, 'left').read_bytes(), b'prefix')
        self.assertEqual(self.send(b'retried', offset=6), 13)

    def test_restart_rolls_back_uncommitted_bytes_and_keeps_completed_chunks(self):
        self.send(b'prefix')
        target = self.app.upload_path(self.job, 'left')
        target.write_bytes(b'prefixpartial')
        self.app.upload_journal(self.identity, 'left').write_text(json.dumps({'offset': 6}))
        reopened = Application(self.temp.name)
        try:
            self.assertEqual(reopened.load(self.identity)['files']['left']['uploaded'], 6)
            self.assertEqual(target.read_bytes(), b'prefix')
            self.assertEqual(reopened.receive_upload(self.identity, 'left', 6, 4, 4, 'identity', io.BytesIO(b'done')), 10)
        finally:
            reopened.pool.shutdown()
        reopened = Application(self.temp.name)
        try:
            self.assertEqual(reopened.load(self.identity)['files']['left']['uploaded'], 10)
        finally:
            reopened.pool.shutdown()

    def test_restart_ignores_incomplete_unpublished_journal(self):
        self.send(b'prefix')
        target = self.app.upload_path(self.job, 'left')
        directory = self.app.directory(self.identity)
        abandoned = directory / ('.upload-left.pending.' + 'a' * 32 + '.tmp')
        abandoned.write_text('{"offset":')
        unrelated = directory / '.upload-left.pending.unrelated.tmp'
        unrelated.write_text('Keep unrelated files')
        reopened = Application(self.temp.name)
        try:
            self.assertEqual(reopened.load(self.identity)['files']['left']['uploaded'], 6)
            self.assertEqual(target.read_bytes(), b'prefix')
            self.assertFalse(abandoned.exists())
            self.assertTrue(unrelated.exists())
            self.assertEqual(reopened.receive_upload(self.identity, 'left', 6, 4, 4, 'identity', io.BytesIO(b'done')), 10)
        finally:
            reopened.pool.shutdown()

    def test_failed_checkpoint_publication_never_appends_data(self):
        self.send(b'prefix')
        with patch('server.replace_retry', side_effect=OSError('Publication interrupted')):
            with self.assertRaisesRegex(OSError, 'Publication interrupted'):
                self.send(b'new bytes', offset=6)
        self.assertEqual(self.app.upload_path(self.job, 'left').read_bytes(), b'prefix')
        self.assertFalse(self.app.upload_journal(self.identity, 'left').exists())
        self.assertEqual(list(self.app.directory(self.identity).glob('.upload-*.pending.*.tmp')), [])
        self.assertEqual(self.app.load(self.identity)['files']['left']['uploaded'], 6)

    def test_finalized_job_does_not_expose_persisted_inflight_state(self):
        self.job['state'] = 'ready'
        self.job['files']['left']['in_flight'] = True
        self.app.save(self.job)
        self.assertFalse(self.app.load(self.identity)['files']['left']['in_flight'])


class UploadTransportAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = make_server(cls.temp.name, 0, public_url='http://compare.internal')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server.app.pool.shutdown()
        cls.temp.cleanup()

    def new_job(self, size=CHUNK):
        client = Client(self.server)
        job = client.request('/api/jobs', {'files': {side: {'name': side + '.csv', 'size': size}
                                                    for side in ('left', 'right')}})
        self.assertEqual(job['upload_compression'], ['gzip'])
        return client, job, '/api/jobs/' + job['id']

    def test_gzip_api_and_lost_response_reconcile_original_offset(self):
        client, job, path = self.new_job()
        original = b'id,value\n001,hello\n' * 1000
        compressed = gzip.compress(original)
        response = Handler.json_response

        def drop_response(handler, value, status=200):
            if handler.command == 'PUT':
                raise BrokenPipeError('Simulated lost acknowledgement')
            return response(handler, value, status)

        with patch.object(Handler, 'json_response', drop_response):
            with self.assertRaises(http.client.RemoteDisconnected):
                client.request(path + '/files/left?offset=0', compressed, 'PUT',
                               {'Content-Encoding': 'gzip', 'X-Upload-Size': str(len(original))})
        status = client.request(path + '/upload-status')
        self.assertEqual(status['files']['left']['uploaded'], len(original))
        self.assertFalse(status['files']['left']['in_flight'])
        with self.assertRaises(HTTPError):
            client.request(path + '/files/left?offset=0', compressed, 'PUT',
                           {'Content-Encoding': 'gzip', 'X-Upload-Size': str(len(original))})
        self.assertEqual(client.request(path + f'/files/left?offset={len(original)}', b'tail', 'PUT')['uploaded'], len(original) + 4)
        self.assertEqual(self.server.app.upload_path(job, 'left').read_bytes(), original + b'tail')

    def test_inflight_status_is_committed_offset_and_other_users_are_isolated(self):
        client, job, path = self.new_job(size=2 * 1024 * 1024)
        other = Client(self.server)
        gate, started = threading.Event(), threading.Event()
        errors = []

        class SlowReader(io.BytesIO):
            def read(inner, count):
                if inner.tell():
                    started.set()
                    if not gate.wait(5):
                        raise TimeoutError('Test upload stalled')
                return super().read(count)

        def upload():
            try:
                self.server.app.receive_upload(job['id'], 'left', 0, 2 * 1024 * 1024,
                                               2 * 1024 * 1024, 'identity', SlowReader(b'x' * (2 * 1024 * 1024)))
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=upload)
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            self.assertEqual(self.server.app.upload_path(job, 'left').stat().st_size, 1024 * 1024)
            self.assertEqual(client.request(path)['files']['left']['uploaded'], 0)
            status = client.request(path + '/upload-status')['files']['left']
            self.assertEqual(status['uploaded'], 0)
            self.assertTrue(status['in_flight'])
            with self.assertRaises(HTTPError) as caught:
                client.request(path + '/finalize', {})
            self.assertIn(b'active upload chunks', caught.exception.read())
            for suffix, body, method in [('/upload-status', None, None), ('/files/left?offset=0', b'x', 'PUT')]:
                with self.assertRaises(HTTPError) as caught:
                    other.request(path + suffix, body, method)
                self.assertEqual(caught.exception.code, 404)
        finally:
            gate.set()
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(client.request(path + '/upload-status')['files']['left']['uploaded'], 2 * 1024 * 1024)

    def test_gzip_requires_original_size_and_legacy_raw_still_works(self):
        raw = b'id,value\n001,test\n'
        client, job, path = self.new_job(size=len(raw))
        with self.assertRaises(HTTPError):
            client.request(path + '/files/left?offset=0', gzip.compress(raw), 'PUT', {'Content-Encoding': 'gzip'})
        client.request(path + '/files/left?offset=0', raw, 'PUT')
        client.request(path + '/files/right?offset=0', gzip.compress(raw), 'PUT',
                       {'Content-Encoding': 'gzip', 'X-Upload-Size': str(len(raw))})
        ready = client.request(path + '/finalize', {})
        self.assertEqual(ready['state'], 'ready')
        self.assertEqual(ready['columns'], ['id', 'value'])


if __name__ == '__main__':
    unittest.main()
