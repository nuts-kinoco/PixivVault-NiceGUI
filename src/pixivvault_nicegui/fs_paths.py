"""Native Windows path spelling for owned filesystem IO; no OS setting changes."""
import os
from pathlib import Path


def native_io_path(path):
    value=str(Path(path).absolute())
    slash=chr(92);prefix=slash*2+'?'+slash
    if os.name=='nt' and not value.startswith(prefix):
        value=prefix+'UNC'+slash+value[2:] if value.startswith(slash*2) else prefix+value
    return Path(value)


def logical_path(path):
    value=str(path);slash=chr(92);prefix=slash*2+'?'+slash
    if os.name=='nt' and value.startswith(prefix):
        value=slash*2+value[len(prefix+'UNC'+slash):] if value.startswith(prefix+'UNC'+slash) else value[len(prefix):]
    return Path(value)


def sqlite_readonly_uri(path, *, immutable=True):
    """Keep native Windows spelling as filename data, not URI authority/query.

    SQLite decodes the file: payload before passing it to its VFS. A quoted
    extended path retains the long-path prefix; Path.as_uri treats its '?' as
    an authority, while stripping that prefix loses long-path IO support.
    """
    from urllib.parse import quote
    filename = ('file:' + quote(str(native_io_path(path)), safe='')
                if os.name == 'nt' else Path(path).absolute().as_uri())
    return filename + ('?mode=ro&immutable=1' if immutable else '?mode=ro')


def record_io_failure(root,job,error,operation):
    """Bounded typed diagnostic beside owned job manifests; never raw paths/data."""
    import json
    from .managed_downloads import _write
    payload=dict(operation=operation,error_type=type(error).__name__,
                 errno=getattr(error,'errno',None),winerror=getattr(error,'winerror',None),
                 job_id=getattr(job,'job_id','unknown'),attempt=getattr(job,'attempt',0))
    target=Path(root)/(payload['job_id']+'-'+str(payload['attempt'])+'.io-error.json')
    try:
        if not target.exists():_write(target,json.dumps(payload,sort_keys=True).encode())
    except (OSError,ValueError):
        # Diagnostics must not replace the original failure or report success.
        pass
