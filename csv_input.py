"""Pack ordinary unquoted CSV records without allocating every cell as a string.

Quoted, projected and unusually large records retain Python's strict CSV parser.
Text-stream positions are unchanged, including multiline checkpoint boundaries.
"""
import csv
import itertools
from operator import itemgetter


class PackedRows:
    def __init__(self,path,names,order,key_positions,args,position=0,count=0):
        self.path=path;self.names=names;self.order=order;self.key_positions=key_positions
        self.args=args;self.position=position;self.count=count
        self.direct_rows=0;self.parsed_rows=0
    def __enter__(self):
        self.stream=open(self.path,encoding=self.args.encoding,newline='',buffering=1024*1024)
        try:
            if self.position:self.stream.seek(self.position)
            else:next(csv.reader(iter(self.stream.readline,''),delimiter=self.args.delimiter,strict=True))
        except BaseException:
            self.stream.close();raise
        return self
    def __exit__(self,*args):self.stream.close()
    def tell(self):return self.stream.tell()
    def __iter__(self):
        from compare import pack_values
        delimiter=self.args.delimiter
        direct=self.order==list(range(len(self.names))) and delimiter not in ('"','\r','\n')
        full_order=self.order==list(range(len(self.names)))
        project=itemgetter(*self.order) if len(self.order)>1 else None
        largest_key=max(self.key_positions)
        first_key=min(self.key_positions)
        from_end=len(self.names)-first_key < largest_key+1
        limit=csv.field_size_limit()
        lines=iter(self.stream.readline,'')
        for line in lines:
            self.count+=1
            # A whole short record below the field limit cannot contain an oversized field.
            if direct and '"' not in line and '\x1f' not in line and len(line)<=limit:
                record=line[:-1] if line.endswith('\n') else line
                if record.endswith('\r'):record=record[:-1]
                if record and record.count(delimiter)==len(self.names)-1:
                    # Usually only the first few fields contain keys. Do not split all 1,780 cells.
                    if from_end:
                        suffix=record.rsplit(delimiter,len(self.names)-first_key)[1:]
                        key=tuple(suffix[i-first_key] for i in self.key_positions)
                    else:
                        prefix=record.split(delimiter,largest_key+1)
                        key=tuple(prefix[i] for i in self.key_positions)
                    self.direct_rows+=1
                    yield key,b'\x00'+record.replace(delimiter,'\x1f').encode('utf-8')
                    continue
            self.parsed_rows+=1
            row=next(csv.reader(itertools.chain((line,),lines),delimiter=delimiter,strict=True))
            if len(row)!=len(self.names):
                raise ValueError(f'{self.path}: record {self.count}: expected {len(self.names)} fields, got {len(row)}')
            values=row if full_order else project(row) if project is not None else [row[self.order[0]]]
            yield tuple(row[i] for i in self.key_positions),pack_values(values)
