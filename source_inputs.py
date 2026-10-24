"""Read-only server sources restricted to administrator configured directories."""
from pathlib import Path
import stat

SUPPORTED={'.csv','.dat','.txt','.tsv','.psv','.xlsx','.xlsm','.parquet','.json'}

def inspect_source(value, roots):
    if not roots:
        raise ValueError('Server file access is disabled. Ask the administrator to configure --source-root.')
    if not isinstance(value,str) or not value.strip() or not Path(value).is_absolute():
        raise ValueError('Enter an absolute file path on the server')
    try:
        path=Path(value).resolve(strict=True)
        if not any(path.is_relative_to(Path(root).resolve()) for root in roots):
            raise ValueError('Choose a file inside a configured server source directory')
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or path.suffix.lower() not in SUPPORTED:
            raise ValueError('Choose a regular CSV, DAT/TXT/TSV, Excel, JSON or Parquet file')
        if not 0<info.st_size<=100*1024**3:
            raise ValueError('Choose a nonempty file up to 100 GB')
        with path.open('rb') as stream:stream.read(1)
    except OSError as error:
        raise ValueError('Server file is unavailable or unreadable') from error
    return dict(path=str(path),size=info.st_size,mtime_ns=info.st_mtime_ns,device=info.st_dev,inode=info.st_ino)

def checked_path(source):
    path=Path(source['path'])
    try:info=path.stat()
    except OSError as error:raise ValueError('Server source is no longer available') from error
    if (info.st_size,info.st_mtime_ns,info.st_dev,info.st_ino)!=(source['size'],source['mtime_ns'],source['device'],source['inode']):
        raise ValueError('Server source changed since selection. Start a new comparison with the updated file.')
    return path
