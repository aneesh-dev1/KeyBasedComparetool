from pathlib import Path
import argparse,time,json,contextlib
import pyarrow as pa,pyarrow.compute as pc,pyarrow.parquet as pq
from compare import compare,parser
from reports import export_excel,export_html

def main():
 options=argparse.ArgumentParser(description='Benchmark shuffled wide integer Parquet with unequal row counts, checkpoints and reports.')
 options.add_argument('--output',required=True)
 root=Path(options.parse_args().output);root.mkdir(parents=True,exist_ok=False);n=500000
 schema=pa.schema([(name,pa.int64()) for name in ['id']+['c'+str(i) for i in range(693)]])
 for side,m in [('left',7919),('right',7907)]:
  path=root/(side+'.parquet')
  if path.exists():continue
  with pq.ParquetWriter(path,schema,compression='snappy') as writer:
   for start in range(0,n,10000):
    values=[(i*m)%n for i in range(start,min(n,start+10000))]
    if side=='right':values=[k for k in values if k<499000]
    keys=pa.array(values,type=pa.int64());arrays=[keys]+[pc.add(keys,i) for i in range(693)]
    if side=='right':arrays[10]=pc.add(arrays[10],1)
    writer.write_table(pa.Table.from_arrays(arrays,schema=schema))
    if start%100000==0:print('Generated',side,start,flush=True)
   if side=='right':
    keys=pa.array(range(500000,500200),type=pa.int64());writer.write_table(pa.Table.from_arrays([keys]+[pc.add(keys,i) for i in range(693)],schema=schema))
 print('Starting comparison',flush=True);out=root/('report-'+str(time.time_ns()))
 args=parser().parse_args([str(root/'left.parquet'),str(root/'right.parquet'),'--keys','id','--output',str(out),'--memory-mb','512','--sort-workers','2']);args.checkpoint_dir=root/('checkpoints-'+str(time.time_ns()))
 started=time.monotonic()
 with (root/'comparison.log').open('w') as log,contextlib.redirect_stdout(log):s=compare(args)
 compared=time.monotonic()-started
 assert (s['matched_keys'],s['changed_cells'],s['left_only'],s['right_only'])==(499000,499000,1000,200),s
 print('Comparison seconds',compared,flush=True)
 export_excel(out,root/'mismatches.xlsx',lambda m:None);excel=time.monotonic()-started-compared
 export_html(out,root/'comparison-report.html',lambda m:None);total=time.monotonic()-started
 result=dict(rows_left=500000,rows_right=499200,columns=694,format='integer Parquet',key_order='shuffled independently',memory_mb=512,sort_workers=2,comparison_seconds=round(compared,3),excel_seconds=round(excel,3),total_including_reports_seconds=round(total,3),changed_cells=s['changed_cells'],left_only=s['left_only'],right_only=s['right_only'],file_bytes={side:(root/(side+'.parquet')).stat().st_size for side in ('left','right')},note='Synthetic integer data, one changed value per matched row; local Mac storage; excludes fixture generation and upload. Not a timing guarantee for other data or hardware.')
 (root/'benchmark.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
if __name__=='__main__':main()
