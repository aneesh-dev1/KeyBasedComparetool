import csv,io,json,zipfile
from unittest.mock import patch
from test_team_features import FeatureTests

class ReviewAiTests(FeatureTests):
    def config(self,job,**updates):
        config={k:job.get(k,[] if k in ('keys','ignore_columns','ignore_container_ids','comparison_rules','value_overrides') else '' if k=='ignore_keys' else 'first') for k in ('keys','ignore_columns','ignore_keys','ignore_container_ids','comparison_rules','value_overrides','duplicate_policy')}
        config.update(updates);return config
    def review(self,path,config):
        decision=self.client.request(path+'/review-plan',config);self.assertEqual(decision['mode'],'review',decision)
        copy=self.client.request(path+'/review-copy',dict(config,execution_mode='review'));url='/api/jobs/'+copy['id']
        with patch('server.subprocess.run',side_effect=AssertionError('Source comparison must not run')):
            self.client.request(url+'/start',config)
            result=self.client.wait(url,lambda j:j['state'] in ('complete','error'))
        self.assertEqual(result['state'],'complete',result)
        for k in ('html','excel'):self.assertEqual(result['exports'][k]['state'],'complete')
        return url,result
    def test_review_rules_scope_restore_and_comments(self):
        path=self.upload(b'id,v,x\n1,0,a\n2,-11,b\n3,same,c\n4,only,d\n',b'id,v,x\n1,0.00,a\n2,-11.00,B\n3,same,c\n5,only,e\n')
        original=self.complete(path);self.assertEqual(original['summary']['changed_cells'],3)
        self.client.request(path+'/annotations',dict(column='',key=['2'],comment='Investigate key',status='Needs investigation'))
        config=self.config(original,comparison_rules=[{'column':'v','tolerance':'0'}])
        first,result=self.review(path,config)
        self.assertEqual(result['summary']['changed_cells'],1)
        self.assertEqual(result['summary']['review_revision']['accepted_by_rules'],2)
        self.assertEqual(result['summary']['equal_rows'],2)
        self.assertEqual(self.client.request(first+'/annotations')['notes'][0]['comment'],'Investigate key')
        second,restored=self.review(first,self.config(result,comparison_rules=[]))
        self.assertEqual(restored['summary']['changed_cells'],3)
        third,filtered=self.review(second,self.config(restored,ignore_keys='2,3,4',ignore_columns=['v']))
        self.assertEqual(filtered['summary']['matched_keys'],1);self.assertEqual(filtered['summary']['equal_rows'],1)
        self.assertEqual(filtered['summary']['left_only'],0);self.assertEqual(filtered['summary']['right_only'],1)
        self.assertEqual(filtered['summary']['changed_cells'],0)
        self.assertEqual(filtered['summary']['review_revision']['excluded_from_scope'],3)
        self.assertEqual(self.client.request(path)['summary']['changed_cells'],3)
        # Restoring exclusions added only in a revision is safe from its unchanged baseline.
        _,again=self.review(third,self.config(filtered,ignore_keys='',ignore_columns=[]))
        self.assertEqual(again['summary']['changed_cells'],3)
        full=self.client.request(path+'/review-plan',self.config(original,keys=['x']))
        self.assertEqual(full['mode'],'full')
    def test_remove_original_rules_restores_raw_differences(self):
        path=self.upload(b'id,v\n1,0\n',b'id,v\n1,0.00\n')
        job=self.complete(path,comparison_rules=[dict(column='v',tolerance='0')])
        self.assertEqual(job['summary']['changed_cells'],0)
        _,review=self.review(path,self.config(job,comparison_rules=[]))
        self.assertEqual(review['summary']['changed_cells'],1)
        self.assertEqual(review['summary']['review_revision']['raw_differences'],1)

    def test_old_and_unretained_scope_require_full_run(self):
        path=self.upload(b'id,v,x\n1,a,old\n',b'id,v,x\n1,b,new\n');job=self.complete(path,ignore_columns=['x'])
        self.assertEqual(self.client.request(path+'/review-plan',self.config(job,ignore_columns=[]))['mode'],'full')
        (self.server.app.directory(job['id'])/'report/raw_differences.csv').unlink()
        self.assertEqual(self.client.request(path+'/review-plan',self.config(job))['mode'],'full')
    def test_optional_supporting_fields_follow_duplicate_policy(self):
        path=self.upload(b'id,v,status\n1,old,first\n1,old,last\n',b'id,v,status\n1,new,first\n1,new,last\n')
        job=self.complete(path,duplicate_policy='last')
        self.client.request(path+'/export/ai',dict(token_budget=8000,examples=1,supporting_columns=['status']))
        ready=self.client.wait(path,lambda j:j.get('exports',{}).get('ai',{}).get('state') in ('complete','error'))
        self.assertEqual(ready['exports']['ai']['state'],'complete',ready)
        with zipfile.ZipFile(self.server.app.directory(job['id'])/'ai-analysis.zip') as z:
            rows=list(csv.DictReader(io.StringIO(z.read('supporting.csv').decode())))
        self.assertEqual(len(rows),2);self.assertTrue(all(r['value']=='last' for r in rows))

    def test_ai_column_scope_filters_evidence_and_comments(self):
        path=self.upload(b'id,v,x,y\n1,a,same,a\n2,same,a,a\n',b'id,v,x,y\n1,b,same,b\n2,same,b,b\n');job=self.complete(path)
        for column,key,comment in [('v',None,'v note'),('x',None,'x note'),('', ['1'],'key one'),('', ['2'],'key two')]:
            self.client.request(path+'/annotations',dict(column=column,key=key,comment=comment,status='Needs investigation'))
        for selected,expected in [(['v'],{'v'}),(['v','x'],{'v','x'}),(None,{'v','x','y'})]:
            self.client.request(path+'/export/ai',dict(token_budget=8000,examples=1,selected_columns=selected))
            ready=self.client.wait(path,lambda j:j.get('exports',{}).get('ai',{}).get('state') in ('complete','error'))
            self.assertEqual(ready['exports']['ai']['state'],'complete',ready)
            with zipfile.ZipFile(self.server.app.directory(job['id'])/'ai-analysis.zip') as z:
                patterns=list(csv.DictReader(io.StringIO(z.read('patterns.csv').decode())))
                comments=list(csv.DictReader(io.StringIO(z.read('comments.csv').decode())))
            self.assertEqual({r['column'] for r in patterns},expected)
            if selected==['v']:self.assertEqual({r['comment'] for r in comments},{'v note','key one'})
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError):self.client.request(path+'/export/ai',dict(selected_columns=[]))
        with self.assertRaises(HTTPError):self.client.request(path+'/export/ai',dict(selected_columns=['missing']))

    def test_ai_package_and_findings(self):
        path=self.upload(b'id,v\n1,0\n2,0\n3,0\n',b'id,v\n1,0.00\n2,0.00\n3,0.00\n');job=self.complete(path)
        self.client.request(path+'/annotations',dict(column='',key=['1'],comment='Hypothesis, not confirmed',status='Needs investigation'))
        self.client.request(path+'/export/ai',dict(token_budget=8000,examples=1))
        ready=self.client.wait(path,lambda j:j.get('exports',{}).get('ai',{}).get('state') in ('complete','error'))
        self.assertEqual(ready['exports']['ai']['state'],'complete',ready)
        with zipfile.ZipFile(self.server.app.directory(job['id'])/'ai-analysis.zip') as z:
            content={n:z.read(n).decode() for n in z.namelist()}
        self.assertLessEqual(sum(map(len,content.values())),32000)
        patterns=list(csv.DictReader(io.StringIO(content['patterns.csv'])))
        self.assertEqual(patterns[0]['count'],'3');self.assertEqual(patterns[0]['pattern'],'Numeric formatting')
        self.assertEqual(len(list(csv.DictReader(io.StringIO(content['samples.csv'])))),1)
        comments=list(csv.DictReader(io.StringIO(content['comments.csv'])))
        self.assertEqual(comments[0]['scope'],'key');self.assertEqual(comments[0]['comment'],'Hypothesis, not confirmed')
        self.client.request(path+'/ai-findings',dict(findings=[dict(column='v',reason='Formatting only',confidence='high')]))
        findings=self.client.request(path+'/ai-findings')['findings'];self.assertIn('unreviewed',findings[0]['review_status'])
        self.assertEqual(self.client.request(path)['summary']['changed_cells'],3)
        # Saving a human comment invalidates the AI package as well as both standard reports.
        self.client.request(path+'/annotations',dict(column='v',key=None,comment='New note',status='Expected'))
        self.assertEqual(self.client.request(path)['exports']['ai']['state'],'outdated')

for name in dir(FeatureTests):
    if name.startswith('test_') and name not in ReviewAiTests.__dict__:setattr(ReviewAiTests,name,None)
del FeatureTests
