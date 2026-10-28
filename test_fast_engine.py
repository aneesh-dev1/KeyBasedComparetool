import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from compare import compare, parser, pack_values, unpack_values, write_run, records

class CompactRunTests(unittest.TestCase):
    def test_100k_sort_batch_has_two_read_updates_and_one_sort_per_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            body='id,v\n'+''.join(f'{i},value\n' for i in range(100000))
            for side in ('left','right'):(root/f'{side}.csv').write_text(body)
            args=parser().parse_args(['--engine','sort',str(root/'left.csv'),str(root/'right.csv'),'--keys','id','--output',str(root/'out'),'--read-batch-size','100000','--memory-mb','64','--sort-workers','1'])
            events=[]
            with patch('compare.progress',side_effect=lambda args,phase,**values:events.append((phase,values))):
                summary=compare(args)
            self.assertEqual(summary['equal_rows'],100000)
            for side in ('left','right'):
                reads=[v for phase,v in events if phase==f'{side} read progress']
                sorts=[v for phase,v in events if phase==f'{side} sort batch sorting']
                self.assertEqual([v['rows'] for v in reads],[50000,100000])
                self.assertEqual([v['batch_rows'] for v in reads],[50000,100000])
                self.assertEqual([v['batch_rows'] for v in sorts],[100000])

    def test_all_texts_round_trip(self):
        cases=[[''],['',''],['001','1','雪','😀','\r\n','\x00','\t','"', '\\'],['x\x1fy',''],['\x1f','\x1f']]
        for values in cases:
            with self.subTest(values=values):self.assertEqual(unpack_values(pack_values(values)),values)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'run'
            rows=[(b'["1"]',pack_values(values)) for values in cases]
            write_run(path,rows)
            with path.open('rb') as f:self.assertEqual(list(records(f)),rows)
            for payload in [b'1',path.read_bytes()[:-1]]:
                with self.assertRaisesRegex(ValueError,'Truncated'):list(records(io.BytesIO(payload)))
    def test_parallel_matches_sequential_and_cleans_scratch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            import csv
            rows=[[str(i), 'x\x1fy' if i%3==0 else '雪\n\t\x00', 'None', 'ignored'] for i in range(100)]
            for side,names,data in [('left',['id','v','status','skip'],rows),('right',['skip','status','v','id'],[[r[3],'none',r[1]+'x' if int(r[0])%2 else r[1],r[0]] for r in reversed(rows)])]:
                with (root/f'{side}.csv').open('w',newline='',encoding='utf-8') as f:w=csv.writer(f);w.writerow(names);w.writerows(data)
            summaries=[]
            for workers in [1,2]:
                args=parser().parse_args(['--engine','sort',str(root/'left.csv'),str(root/'right.csv'),'--keys','id','--output',str(root/f'out{workers}'),'--temp-dir',str(root),'--ignore-columns','skip','--ignore-keys','1, 2','--sort-workers',str(workers)])
                args.memory_mb=.002;args.fan_in=2
                args.value_overrides=[dict(column='status',left='None',right='none')]
                with contextlib.redirect_stdout(io.StringIO()):s=compare(args)
                s.pop('elapsed_seconds');summaries.append(s)
            self.assertEqual(summaries[0],summaries[1])
            for name in ['differences.csv','left_only.csv','right_only.csv']:
                self.assertEqual((root/'out1'/name).read_bytes(),(root/'out2'/name).read_bytes())
            self.assertFalse(list(root.glob('csv-compare-*')))
    def test_parallel_large_field_and_key_only_projection(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            text='x'*200000
            (root/'a.csv').write_text('id,v\n001,'+text+'\n')
            (root/'b.csv').write_text('v,id\n'+text+',001\n')
            for index,extra in enumerate([[],['--ignore-columns','v']]):
                args=parser().parse_args(['--engine','sort',str(root/'a.csv'),str(root/'b.csv'),'--keys','id','--output',str(root/f'out{index}'),'--sort-workers','2',*extra])
                with contextlib.redirect_stdout(io.StringIO()):result=compare(args)
                self.assertEqual(result['equal_rows'],1)

    def test_parallel_bad_input_cleans_scratch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'a.csv').write_text('id,v\n1,x,extra\n');(root/'b.csv').write_text('id,v\n1,x\n')
            args=parser().parse_args(['--engine','sort',str(root/'a.csv'),str(root/'b.csv'),'--keys','id','--output',str(root/'out'),'--temp-dir',str(root),'--sort-workers','2'])
            with contextlib.redirect_stdout(io.StringIO()):result=compare(args)
            self.assertEqual(result['malformed_records'],1)
            self.assertFalse(list(root.glob('csv-compare-*')))
            self.assertTrue((root/'out'/'summary.json').exists())
