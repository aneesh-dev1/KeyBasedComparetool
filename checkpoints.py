"""Validated restart checkpoints. Source hashes are checked before reusing any work."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
from file_io import atomic_write_text
from task_control import check_cancel


def save_manifest(path,value):
    atomic_write_text(path,json.dumps(value))
    with Path(path).open('r+b') as stream:os.fsync(stream.fileno())
    if os.name!='nt':
        descriptor=os.open(str(Path(path).parent),os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)


def verify_sources(directory):
    for item in json.loads((Path(directory)/'identity.json').read_text())['sources']:
        stat=Path(item['path']).stat()
        if (stat.st_size,stat.st_mtime_ns)!=(item['size'],item['mtime']):raise ValueError('Source changed during comparison. Start a new comparison.')


def digest(path, args=None, notify=lambda text: None):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        count=0
        while block:=stream.read(4*1024*1024):
            check_cancel(getattr(args,'cancel_file',None));value.update(block);count+=len(block)
            if count%(256*1024*1024)==0:notify(f'Validated {count//(1024*1024):,} MB')
    return value.hexdigest()


@contextlib.contextmanager
def lock(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    with (directory/'worker.lock').open('a+b') as stream:
        if stream.seek(0,2)==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:raise ValueError('A comparison worker still owns this job. Wait for it to stop before resuming.') from error
        try:yield
        finally:
            if os.name=='nt':stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def validate(directory,args,configuration,notify):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    sources=[]
    for path in (args.left,args.right):
        path=Path(path);before=path.stat();notify('Validating source '+path.name)
        sha=digest(path,args,notify);after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed during validation. Start a new comparison.')
        sources.append(dict(path=str(path.resolve()),size=after.st_size,mtime=after.st_mtime_ns,sha256=sha))
    import platform,sys
    identity=dict(version=1,runtime=[platform.python_implementation(),list(sys.version_info[:2])],sources=sources,configuration=configuration)
    target=directory/'identity.json'
    if target.exists():
        if json.loads(target.read_text())!=identity:raise ValueError('Source files or comparison settings changed. Checkpoints cannot be reused; start a new comparison.')
    else:
        if any((directory/(side+'.json')).exists() for side in ('left','right')):raise ValueError('Checkpoint identity is missing. Start a new comparison.')
        save_manifest(target,identity)


class SortCheckpoint:
    def __init__(self,directory,side,args):
        self.directory=Path(directory);self.target=self.directory/(side+'.json');self.args=args;self.metadata={}
        self.state=json.loads(self.target.read_text()) if self.target.exists() else None
        if self.state:
            checksum=self.state.pop('checksum',None)
            if checksum!=hashlib.sha256(json.dumps(self.state,sort_keys=True).encode('utf-8')).hexdigest():raise ValueError('Saved sort checkpoint metadata is damaged. Start a new comparison.')
            for item in self.state['runs']:
                path=self.directory/item['name']
                if Path(item['name']).name!=item['name'] or not path.is_file() or path.stat().st_size!=item['size'] or digest(path,args)!=item['sha256']:
                    raise ValueError('Saved sort batch is missing or damaged. Start a new comparison.')
                self.metadata[item['name']]=item
    def save(self,paths,count,excluded,serial,position,complete):
        runs=[]
        for path in paths:
            name=path.name
            if name not in self.metadata:self.metadata[name]=dict(name=name,size=path.stat().st_size,sha256=digest(path,self.args))
            runs.append(self.metadata[name])
        state=dict(runs=runs,count=count,excluded=excluded,serial=serial,position=position,complete=complete)
        state['checksum']=hashlib.sha256(json.dumps(state,sort_keys=True).encode('utf-8')).hexdigest()
        save_manifest(self.target,state)
