import csv
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from analysis import build_index,query_index
from server import make_server
from test_team_server import Client

class AnalysisTests(unittest.TestCase):
    def test_exact_composite_keys_pagination_and_truncation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with (root/'differences.csv').open('w',newline='') as f:
                writer=csv.writer(f);writer.writerow(['key_json','column','left_value','right_value'])
                for i in range(75):writer.writerow([json.dumps(['001',str(i)]),'v','x'*2000,'y'])
            for side in ['left_only','right_only']:
                (root/(side+'.csv')).write_text('key_json\n')
            build_index(root,root/'index.sqlite',lambda _:None)
            a=query_index(root/'index.sqlite','keys');b=query_index(root/'index.sqlite','keys',50)
            self.assertEqual((a['total'],len(a['rows']),len(b['rows'])),(75,50,25))
            row=query_index(root/'index.sqlite','cells',key=['001','0'])['rows'][0]
            self.assertEqual(len(row[2]),1000);self.assertEqual(row[4],2000)
            self.assertEqual(query_index(root/'index.sqlite','cells',column="' OR 1=1 --")['total'],0)
    def test_common_columns_api_and_private_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            server=make_server(temp,0,public_url='http://compare.internal',max_sort_mb=64)
            thread=threading.Thread(target=server.serve_forever);thread.start()
            try:
                client,other=Client(server),Client(server)
                sources=dict(left=b'id,only_left,v\n001,skip,old\n002,skip,same\n003,skip,left\n',right=b'V,ID,only_right\nnew,001,skip\nsame,002,skip\nright,004,skip\n')
                job=client.request('/api/jobs',dict(files={side:dict(name=side+'.csv',size=len(data)) for side,data in sources.items()}));path='/api/jobs/'+job['id']
                for side,data in sources.items():client.request(path+'/files/'+side+'?offset=0',data,'PUT')
                ready=client.request(path+'/finalize',{})
                self.assertEqual(ready['columns'],['id','v'])
                client.request(path+'/headers',dict(column_headers=ready['column_headers']))
                client.request(path+'/start',dict(keys=['id'],memory_mb=64))
                job=client.wait(path,lambda j:j['state'] in ('complete','error'))
                self.assertEqual(job['state'],'complete',job)
                self.assertEqual(job['summary']['changed_cells_by_column'],{'v':1})
                self.assertEqual(job['summary']['unmatched_columns'],{'left':['only_left'],'right':['only_right']})
                client.request(path+'/analysis',{})
                result=client.wait(path+'/analysis',lambda j:j['state'] in ('complete','error'))
                self.assertEqual(result['state'],'complete',result)
                self.assertEqual(result['total'],3)
                from urllib.parse import urlencode
                details=client.request(path+'/analysis?'+urlencode(dict(mode='cells',key=json.dumps(['003']))))
                self.assertEqual(details['key_status'],'left_only');self.assertEqual(details['total'],0)
                with self.assertRaises(HTTPError) as error:other.request(path+'/analysis')
                self.assertEqual(error.exception.code,404)
                client.request(path,method='DELETE')
                self.assertFalse(server.app.directory(job['id']).exists())
            finally:
                server.shutdown();thread.join();server.server_close();server.app.pool.shutdown()

class PatternTests(unittest.TestCase):
    def test_classification_and_counts(self):
        from analysis import classify_pattern
        pairs=[('None','none','Case only'),('','value','Blank → value'),('value','','Value → blank'),(' x ','x','Whitespace only'),(' A ','a','Case and whitespace'),('001','1.0','Numeric formatting'),('NaN','nan','Case only'),('5','6','Other value change')]
        for left,right,label in pairs:self.assertEqual(classify_pattern(left,right),label)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with (root/'differences.csv').open('w',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['key_json','column','left_value','right_value'])
                for i in range(3):writer.writerow([json.dumps([str(i)]),'status','None','none'])
                writer.writerow(['["7"]','number','001','1.0'])
                writer.writerow(['["8"]','status','','value'])
            for name in ('left_only','right_only'):(root/(name+'.csv')).write_text('id\n')
            build_index(root,root/'index.sqlite',lambda _:None)
            result=query_index(root/'index.sqlite','patterns')
            self.assertEqual(result['total'],3);self.assertEqual(result['rows'][0][:5],('status','Case only','None','none',3))
            filtered=query_index(root/'index.sqlite','patterns',column='status')
            self.assertEqual(filtered['total'],2);self.assertEqual(dict(filtered['categories']),{'Case only':3,'Blank → value':1})
            self.assertEqual(query_index(root/'index.sqlite','patterns',column="' OR 1=1 --")['total'],0)
