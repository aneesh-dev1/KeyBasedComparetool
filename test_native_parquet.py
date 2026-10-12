import contextlib,csv,io,tempfile,unittest
from pathlib import Path
from decimal import Decimal
from datetime import datetime,date
import pyarrow as pa
import pyarrow.parquet as pq
from parquet_input import Rows,PackedRows
from compare import unpack_values,compare,parser

class NativeParquetTests(unittest.TestCase):
    def test_exact_types_nulls_separators_and_resume(self):
        table=pa.table({'id':['a','b','c','d'],'text':pa.array(['a\x1fb','',None,'雪\x1e'],type=pa.large_string()),'integer':pa.array([0,-11,None,2**63-1],type=pa.int64()),'boolean':[True,False,None,True],'float':[0.0,-0.0,float('inf'),1e-7],'decimal':pa.array([Decimal('1.00'),None,Decimal('-11.00'),Decimal('0.00')]),'date':[date(2026,1,1),None,date(2026,1,3),date(2026,1,4)],'binary':[b'x',b'\xff',None,b''],'nested':[[1,2],[],None,[3]],'null':pa.nulls(4)})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'data.parquet';pq.write_table(table,path,row_group_size=2)
            for position in (0,1,2,3,4):
                with Rows(path,list(range(table.num_columns)),position,batch_rows=1) as source:expected=list(source)
                with PackedRows(path,list(range(table.num_columns)),[0],position,batch_rows=1) as source:
                    actual=list(source);self.assertEqual(source.tell(),4 if position<4 else position)
                self.assertEqual([unpack_values(p) for _,p in actual],expected)
                self.assertEqual([list(k) for k,_ in actual],[[r[0]] for r in expected])
    def test_native_equals_reference_with_reordered_missing_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            pq.write_table(pa.table({'id':['a','b','c','d','d'],'v':[None,1,2,3,4],'w':['x','y','z','q','r']}),root/'left.parquet',row_group_size=2)
            pq.write_table(pa.table({'w':['r','z','new','extra'],'v':[4,2,0,9],'id':['d','c','a','e']}),root/'right.parquet',row_group_size=3)
            for reference in (True,False):
                args=parser().parse_args([str(root/'left.parquet'),str(root/'right.parquet'),'--keys','id','--output',str(root/str(reference)),'--sort-workers','1','--duplicate-policy','last']);args.reference_parquet_reader=reference
                with contextlib.redirect_stdout(io.StringIO()):compare(args)
            for file in ('differences.csv','raw_differences.csv','matched_keys.csv','left_only.csv','right_only.csv','duplicate_keys.csv'):
                self.assertEqual((root/'True'/file).read_bytes(),(root/'False'/file).read_bytes(),file)
