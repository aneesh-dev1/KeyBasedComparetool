"""Atomic metadata updates resilient to short-lived Windows file locks."""
import os
from pathlib import Path
import tempfile
import time


def replace_retry(source, target):
    delays=(0.02,0.05,0.1,0.2,0.3,0.5,0.8,0.8)
    for attempt in range(len(delays)+1):
        try:
            os.replace(source,target)
            return
        except PermissionError as error:
            if attempt==len(delays):
                raise PermissionError(f'Cannot update {target}: the file is locked or the data folder is not writable. Existing data is preserved. Close programs holding this file, or use a writable local data folder.') from error
            time.sleep(delays[attempt])


def atomic_write_text(path, text):
    path=Path(path)
    fd,temporary=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='') as stream:
            stream.write(text)
        replace_retry(temporary,path)
    finally:
        try:Path(temporary).unlink(missing_ok=True)
        except OSError:pass  # Preserve the original error when cleanup is also locked.
