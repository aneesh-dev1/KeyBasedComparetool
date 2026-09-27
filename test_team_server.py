import concurrent.futures
import http.cookiejar
import io
import json
import re
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
from server import make_server, lock_data_directory

class Client:
    def __init__(self, server):
        self.base=f'http://127.0.0.1:{server.server_address[1]}'
        self.cookies=http.cookiejar.CookieJar()
        self.opener=build_opener(HTTPCookieProcessor(self.cookies))
        page=self.opener.open(self.base+'/').read().decode()
        self.token=re.search(r'name="app-token" content="([^"]+)"',page)[1]
    def request(self,path,body=None,method=None,headers=None):
        if isinstance(body,dict):body=json.dumps(body).encode()
        req=Request(self.base+path,data=body,method=method,headers={'X-App-Token':self.token,**(headers or {})})
        with self.opener.open(req,timeout=15) as response:
            data=response.read()
            return json.loads(data) if response.headers.get_content_type()=='application/json' else data
    def wait(self,path,condition):
        until=time.monotonic()+20
        while time.monotonic()<until:
            value=self.request(path)
            if condition(value):return value
            time.sleep(.03)
        raise AssertionError('Job did not finish')

class TeamServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.server=make_server(self.temp.name,0,public_url='http://compare.internal',max_jobs=1,max_sort_mb=64)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.server.app.pool.shutdown();self.temp.cleanup()
    def test_json_array_discovery_endpoint(self):
        client = Client(self.server)
        arrays = client.request('/api/json-arrays', dict(left='{"students":[{"id":"A1"},{"id":"A2"}]}', right='{"students":[{"id":"A2"},{"id":"A1"}]}'))['arrays']
        self.assertEqual(arrays[0]['path'], '$.students')
        self.assertEqual(arrays[0]['fields'], ['id'])

    def test_five_users_isolated_uploads_queue_results_exports_and_containers(self):
        clients=[Client(self.server) for _ in range(5)]
        gate=threading.Event();active=[0,0];lock=threading.Lock()
        original=self.server.app.run_comparison
        def tracked(identity):
            with lock:active[0]+=1;active[1]=max(active[1],active[0])
            try:
                if not gate.wait(10):raise AssertionError('queue gate timeout')
                original(identity)
            finally:
                with lock:active[0]-=1
        self.server.app.run_comparison=tracked
        def upload(i):
            client=clients[i]
            left=f'ID,AT01S\n{i:03},old-{i}\n'.encode()
            right=f'at02s,id\nnew-{i},{i:03}\n'.encode()
            job=client.request('/api/jobs',{'files':dict(left=dict(name='left.csv',size=len(left)),right=dict(name='right.csv',size=len(right)))})
            path='/api/jobs/'+job['id']
            for side,data in [('left',left),('right',right)]:
                split=len(data)//2
                client.request(path+f'/files/{side}?offset=0',data[:split],'PUT')
                client.request(path+f'/files/{side}?offset={split}',data[split:],'PUT')
            client.request(path+'/finalize',{})
            client.request(path+'/headers',{'column_headers':dict(left=['ID','AT01S'],right=['at01s','id'])})
            client.request('/api/key-containers',dict(name='NO_HIT_KEYS',reason=f'User {i}',values='999',key_width=1))
            cid=client.request('/api/key-containers')['containers'][0]['id']
            client.request(path+'/start',dict(keys=['ID'],memory_mb=64,sort_workers=1,ignore_container_ids=[cid]))
            return path
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:paths=list(pool.map(upload,range(5)))
            self.assertEqual(len(set(paths)),5)
            for i,c in enumerate(clients):
                history=c.request('/api/jobs')['jobs'];self.assertEqual(len(history),1);self.assertTrue(paths[i].endswith(history[0]['id']))
                self.assertEqual(c.request('/api/key-containers')['containers'][0]['reason'],f'User {i}')
                other=paths[(i+1)%5]
                for suffix in ['', '/logs','/source-preview','/download/run.log']:
                    with self.assertRaises(HTTPError) as caught:c.request(other+suffix)
                    self.assertEqual(caught.exception.code,404)
                with self.assertRaises(HTTPError) as caught:c.request(other+'/config',{})
                self.assertEqual(caught.exception.code,404)
            self.assertEqual(active[1],1)
        finally:gate.set()
        for i,c in enumerate(clients):
            result=c.wait(paths[i],lambda j:j['state'] in ('complete','error'))
            self.assertEqual(result['state'],'complete',result)
            self.assertEqual(c.request(paths[i]+'/preview')['rows'],[[f'{i:03}','AT01S',f'old-{i}',f'new-{i}']])
            for kind,name in [('excel','mismatches.xlsx'),('html','html.zip')]:
                c.request(paths[i]+'/export/'+kind,{})
                done=c.wait(paths[i],lambda j:j['exports'][kind]['state'] in ('complete','error'))
                self.assertEqual(done['exports'][kind]['state'],'complete',done)
                with zipfile.ZipFile(io.BytesIO(c.request(paths[i]+'/download/'+name))) as archive:self.assertIsNone(archive.testzip())
        self.assertEqual(active[1],1)
        self.assertEqual(len(Client(self.server).request('/api/jobs')['jobs']),0)
    def test_allowed_host_origin_memory_and_json_capacity(self):
        c=Client(self.server)
        self.assertEqual(c.request('/health',headers={'Host':'compare.internal'})['status'],'ready')
        for headers in [{'Host':'untrusted.invalid'},{'Origin':'https://untrusted.invalid'}]:
            with self.assertRaises(HTTPError):c.request('/api/jobs',headers=headers)
        with self.server.app.json_lock:
            with self.assertRaises(HTTPError) as caught:c.request('/api/json-compare',dict(left='{}',right='{}'))
            self.assertEqual(caught.exception.code,429)
        self.assertTrue(c.request('/api/json-compare',dict(left='{}',right='{}'))['equal'])
        raw=b'id,v\n1,a\n';j=c.request('/api/jobs',{'files':{s:dict(name=s+'.csv',size=len(raw)) for s in ('left','right')}});path='/api/jobs/'+j['id']
        for side in ('left','right'):c.request(path+f'/files/{side}?offset=0',raw,'PUT')
        c.request(path+'/finalize',{})
        with self.assertRaises(HTTPError):c.request(path+'/start',dict(keys=['id'],memory_mb=4096))
    def test_configuration_and_data_lock(self):
        for url in ['ftp://host','http://user:pass@host','http://host/path','http://host?q=x']:
            with self.assertRaises(ValueError):make_server(self.temp.name,0,public_url=url)
        with self.assertRaises(ValueError):make_server(self.temp.name,0,host='0.0.0.0')
        first=lock_data_directory(self.temp.name)
        try:
            with self.assertRaises(ValueError):lock_data_directory(self.temp.name)
        finally:first.close()
        second=lock_data_directory(self.temp.name);second.close()

if __name__=='__main__':unittest.main()
