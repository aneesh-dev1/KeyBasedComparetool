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
    def __init__(self,path,indices,position=0,batch_rows=100000,memory_mb=64,native_batch_bytes=None):
        self.file=open_file(path);self.position=position
        schema_names=self.file.schema_arrow.names
        self.names=[schema_names[i] for i in indices]
        self.batch_rows=batch_rows;self.memory_mb=memory_mb;self.native_batch_bytes=native_batch_bytes
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
            target=min(self.native_batch_bytes or 16*1024**2,self.memory_mb*1024**2/4)
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

class PackedRows(Rows):
    """Assemble row payloads inside Arrow; materialize only keys and packed rows."""
    def __init__(self,path,indices,key_positions,position=0,batch_rows=100000,memory_mb=64,native_batch_bytes=None):
        super().__init__(path,indices,position,batch_rows,memory_mb,native_batch_bytes)
        self.key_positions=key_positions
    def __iter__(self):
        import pyarrow as pa
        import pyarrow.compute as pc
        from compare import pack_values
        skipped=0
        for group in range(self.file.num_row_groups):
            meta=self.file.metadata.row_group(group)
            if skipped+meta.num_rows<=self.position:
                skipped+=meta.num_rows;continue
            offset=max(0,self.position-skipped)
            # Native packing avoids the reference reader's per-cell Python objects.
            # Allow a larger bounded working set to amortize wide-schema kernel calls.
            selected=set(self.names);size=0
            for i in range(meta.num_columns):
                column=meta.column(i);parts=column.path_in_schema.split('.')
                if any('.'.join(parts[:j]) in selected for j in range(1,len(parts)+1)):size+=column.total_uncompressed_size
            per_row=max(1,size/max(1,meta.num_rows));target=min(self.native_batch_bytes or 64*1024**2,self.memory_mb*1024**2/4)
            batch=max(1,min(self.batch_rows,65536,int(target/(per_row*8))))
            for block in self.file.iter_batches(batch_size=batch,row_groups=[group],columns=self.names,use_threads=False):
                if offset>=block.num_rows:offset-=block.num_rows;continue
                if offset:block=block.slice(offset);offset=0
                columns=[]
                for name in self.names:
                    array=block.column(block.schema.get_field_index(name));kind=array.type
                    if pa.types.is_string(kind):text=array
                    elif pa.types.is_large_string(kind):text=pc.cast(array,pa.string())
                    elif pa.types.is_integer(kind) or pa.types.is_boolean(kind) or (pa.types.is_decimal(kind) and kind.scale>=0):text=pc.cast(array,pa.string())
                    elif pa.types.is_floating(kind):
                        # Python's float formatting is part of the text comparison contract.
                        text=pa.array([None if v is None else str(v) for v in array.to_pylist()],type=pa.string())
                    else:
                        # Preserve exact existing float/date/binary/nested formatting.
                        text=pa.array([text_value(v) for v in array.to_pylist()],type=pa.string())
                    columns.append(text)
                joined=pc.binary_join_element_wise(*columns,'\x1f',null_handling='replace',null_replacement='')
                special=pc.not_equal(pc.count_substring(joined,'\x1f'),len(columns)-1).to_pylist()
                if any(c.null_count for c in columns):
                    flags=pc.binary_join_element_wise(*[pc.if_else(pc.is_null(c),'1','0') for c in columns],'')
                    packed=pc.if_else(pc.greater(pc.count_substring(flags,'1'),0),pc.binary_join_element_wise('\x02',flags,'\x1e',joined,''),pc.binary_join_element_wise('\x00',joined,''))
                else:packed=pc.binary_join_element_wise('\x00',joined,'')
                payloads=pc.cast(packed,pa.binary()).to_pylist()
                keys=[columns[i].to_pylist() for i in self.key_positions]
                for i,key in enumerate(zip(*keys)):
                    if special[i]:payloads[i]=pack_values([c[i].as_py() for c in columns])
                    self.position+=1
                    yield key,payloads[i]
            skipped+=meta.num_rows
