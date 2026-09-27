from pathlib import Path
class Cancelled(ValueError): pass

def check_cancel(path):
    if path and Path(path).exists(): raise Cancelled('Cancelled by user')
