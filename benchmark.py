"""Generate a reproducible wide CSV pair and validate/timestamp the current engine.

Example: py -3 benchmark.py --output benchmark-100k --rows 100000
Use a new output directory. No user files are read or overwritten.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import platform
import threading
import time
from compare import compare, parser as comparison_parser


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--rows',type=int,default=10000)
    p.add_argument('--columns',type=int,default=2000)
    p.add_argument('--memory-mb',type=int,default=4096)
    p.add_argument('--sort-workers',type=int,choices=(1,2),default=2)
    args=p.parse_args()
    if not 1<=args.rows<=1000000 or not 2<=args.columns<=10000 or args.memory_mb<1:
        p.error('Use 1–1,000,000 rows, 2–10,000 columns, and a positive memory budget')
    root=Path(args.output);root.mkdir(parents=True,exist_ok=False)
    templates=[[str((seed+col)%97) for col in range(args.columns-1)] for seed in range(97)]
    original=[','.join(row)+'\n' for row in templates]
    altered=[]
    for template in templates:
        row=template.copy();row[(args.columns-1)//2]='changed';altered.append(','.join(row)+'\n')
    print('Generating CSV files; 1,000,000 × 2,000 produces about 5.8 GB per file.',flush=True)
    for side,multiplier in [('left',7919),('right',7907)]:
        while math.gcd(multiplier,args.rows)!=1:multiplier+=2
        with (root/f'{side}.csv').open('w',encoding='utf-8',newline='',buffering=1024*1024) as stream:
            stream.write(','.join(['id']+[f'c{i}' for i in range(args.columns-1)])+'\n')
            for index in range(args.rows):
                key=index*multiplier%args.rows
                pattern=altered if side=='right' and key%10==0 else original
                stream.write(f'{key:07},'+pattern[key%97])
    configuration=comparison_parser().parse_args([str(root/'left.csv'),str(root/'right.csv'),'--keys','id','--output',str(root/'report'),'--temp-dir',str(root),'--memory-mb',str(args.memory_mb),'--sort-workers',str(args.sort_workers),'--fan-in','32'])
    peak=[0];stop=threading.Event()
    def monitor():
        while not stop.is_set():
            size=0
            for directory in root.glob('csv-compare-*'):
                for file in directory.glob('*.run'):
                    try:size+=file.stat().st_size
                    except FileNotFoundError:pass
            peak[0]=max(peak[0],size)
            stop.wait(.25)
    sampler=threading.Thread(target=monitor,daemon=True);sampler.start()
    started=time.monotonic()
    try:summary=compare(configuration)
    finally:stop.set();sampler.join()
    expected=(args.rows+9)//10
    if summary['changed_cells']!=expected or summary['changed_rows']!=expected or summary['equal_rows']!=args.rows-expected or summary['left_only'] or summary['right_only']:
        raise AssertionError('Benchmark comparison result was incorrect')
    result=dict(platform=platform.platform(),python=platform.python_version(),rows_per_file=args.rows,columns=args.columns,
                input_bytes={side:(root/f'{side}.csv').stat().st_size for side in ('left','right')},
                total_sort_memory_mb=args.memory_mb,sort_workers=args.sort_workers,
                elapsed_seconds=round(time.monotonic()-started,3),sampled_peak_scratch_bytes=peak[0],
                expected_changed_cells=expected,correctness_verified=True,
                note='Elapsed time excludes generation and Excel/HTML export. Scratch is sampled every 250 ms, not an exact peak. RAM is not measured.')
    (root/'benchmark.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
