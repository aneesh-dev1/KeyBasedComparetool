"""Pack ordinary unquoted CSV records without allocating every cell as a string.

Quoted, projected and unusually large records retain Python's strict CSV parser.
Text-stream positions are unchanged, including multiline checkpoint boundaries.
"""
import csv
import csv_nul  # noqa: F401  (Python 3.9/3.10 read NUL characters like 3.11)
import itertools
from operator import itemgetter


class PackedRows:
    def __init__(self,path,names,order,key_positions,args,position=0,count=0,side=None,opener=None):
        from compare import dialect
        self.path=path;self.names=names;self.order=order;self.key_positions=key_positions
        self.args=args;self.position=position;self.count=count;self.opener=opener
        self.delimiter,self.encoding,self.has_header=dialect(args,side)
        self.direct_rows=0;self.parsed_rows=0
    def __enter__(self):
        # opener: a ready text stream positioned at a record boundary (parallel byte ranges).
        self.stream=self.opener() if self.opener else open(self.path,encoding=self.encoding,newline='',buffering=getattr(self.args,'io_buffer_bytes',1024*1024))
        try:
            if self.opener:pass
            elif self.position:self.stream.seek(self.position)
            elif self.has_header:next(csv.reader(iter(self.stream.readline,''),delimiter=self.delimiter,strict=True))
        except BaseException:
            self.stream.close();raise
        return self
    def __exit__(self,*args):self.stream.close()
    def tell(self):return self.stream.tell()
    def __iter__(self):
        from compare import pack_values
        delimiter=self.delimiter
        full_order=self.order==list(range(len(self.names)))
        # Unquoted records split exactly like the csv module, so they never need the Python parser.
        direct=delimiter not in ('"','\r','\n')
        # \x1f separates packed cells; a record may contain it only when it is the delimiter itself.
        separator_safe=delimiter=='\x1f'
        project=itemgetter(*self.order) if len(self.order)>1 else None
        largest_key=max(self.key_positions)
        first_key=min(self.key_positions)
        from_end=len(self.names)-first_key < largest_key+1
        limit=csv.field_size_limit()
        width=len(self.names)
        quoted_separator='"'+delimiter+'"'
        quoted=True
        lines=iter(self.stream.readline,'')
        for line in lines:
            self.count+=1
            # A whole short record below the field limit cannot contain an oversized field.
            if direct and '"' not in line and (separator_safe or '\x1f' not in line) and len(line)<=limit:
                record=line[:-1] if line.endswith('\n') else line
                if record.endswith('\r'):record=record[:-1]
                if record and record.count(delimiter)==len(self.names)-1 and not full_order:
                    # Ignored or reordered columns: one C-level split, then pick the compared cells.
                    cells=record.split(delimiter)
                    key=tuple(cells[i] for i in self.key_positions)
                    values=project(cells) if project is not None else (cells[self.order[0]],)
                    self.direct_rows+=1
                    yield key,b'\x00'+'\x1f'.join(values).encode('utf-8')
                    continue
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
            # Fully quoted record ("a","b","c") with no other quotes: exactly 2n quotes means every field is
            # one quoted run with no escaped quotes, so splitting on '","' is identical to the csv module.
            if direct and quoted and line.startswith('"') and (separator_safe or '\x1f' not in line) and len(line)<=limit:
                record=line[:-1] if line.endswith('\n') else line
                if record.endswith('\r'):record=record[:-1]
                if len(record)>1 and record.endswith('"') and record.count('"')==2*width:
                    cells=record[1:-1].split(quoted_separator)
                    if len(cells)==width:
                        key=tuple(cells[i] for i in self.key_positions)
                        values=cells if full_order else project(cells) if project is not None else (cells[self.order[0]],)
                        self.direct_rows+=1
                        yield key,b'\x00'+'\x1f'.join(values).encode('utf-8')
                        continue
            self.parsed_rows+=1
            row=next(csv.reader(itertools.chain((line,),lines),delimiter=delimiter,strict=True))
            if len(row)!=len(self.names):
                raise ValueError(f'{self.path}: record {self.count}: expected {len(self.names)} fields, got {len(row)}')
            values=row if full_order else project(row) if project is not None else [row[self.order[0]]]
            yield tuple(row[i] for i in self.key_positions),pack_values(values)
