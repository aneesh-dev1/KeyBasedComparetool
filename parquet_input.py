"""Projected Parquet reads. No dataframe and no intermediate CSV copy."""
import base64
import json
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path


def arrow():
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise ValueError('Parquet support requires PyArrow. Install requirements.txt with the server Python and restart.') from error
    return pq


def open_file(path):
    return arrow().ParquetFile(path, pre_buffer=False, memory_map=False)


def metadata(path):
    with open_file(path) as file:
        names=file.schema_arrow.names
        if len(set(names)) != len(names):
            raise ValueError('Parquet field names must be unique')
        if not names: raise ValueError('Parquet must contain at least one column')
        return dict(columns=names,rows=file.metadata.num_rows,row_groups=file.num_row_groups,
                    types=[str(f.type) for f in file.schema_arrow])


def text_value(value):
    if value is None: return None
    if isinstance(value,str): return value
    if isinstance(value,bool): return 'true' if value else 'false'
    if isinstance(value,(datetime,date,time)): return value.isoformat()
    if isinstance(value,bytes): return 'base64:'+base64.b64encode(value).decode('ascii')
    if isinstance(value,(dict,list,tuple)):
        return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=lambda v:text_value(v))
    return str(value)


class Rows:
    """Resume via a row offset, skipping complete row groups using metadata."""
    def __init__(self,path,indices,position=0,batch_rows=100000,memory_mb=64):
        self.file=open_file(path);self.position=position
        self.names=[self.file.schema_arrow.names[i] for i in indices]
        self.batch_rows=batch_rows;self.memory_mb=memory_mb
    def __enter__(self): return self
    def __exit__(self,*args): self.file.close()
    def tell(self): return self.position
    def __iter__(self):
        skipped=0
        for group in range(self.file.num_row_groups):
            meta=self.file.metadata.row_group(group)
            if skipped+meta.num_rows<=self.position:
                skipped+=meta.num_rows;continue
            offset=max(0,self.position-skipped)
            # Metadata estimate limits typical decompression + Python conversion working sets.
            name_set=set(self.names);selected_bytes=0
            for i in range(meta.num_columns):
                column=meta.column(i);parts=column.path_in_schema.split('.')
                if any('.'.join(parts[:j]) in name_set for j in range(1,len(parts)+1)):
                    selected_bytes+=column.total_uncompressed_size
            per_row=max(1,selected_bytes/max(1,meta.num_rows))
            target=min(16*1024**2,self.memory_mb*1024**2/4)
            batch=max(1,min(self.batch_rows,65536,int(target/(per_row*8))))
            for block in self.file.iter_batches(batch_size=batch,row_groups=[group],columns=self.names,use_threads=False):
                if offset>=block.num_rows: offset-=block.num_rows;continue
                if offset: block=block.slice(offset);offset=0
                import pyarrow as pa
                columns=[]
                for name in self.names:
                    column=block.column(block.schema.get_field_index(name));values=column.to_pylist()
                    if not (pa.types.is_string(column.type) or pa.types.is_large_string(column.type)):
                        values=[text_value(value) for value in values]
                    columns.append(values)
                for row in zip(*columns):
                    self.position+=1
                    yield list(row)
            skipped+=meta.num_rows


def is_parquet(path): return Path(path).suffix.lower()=='.parquet'
