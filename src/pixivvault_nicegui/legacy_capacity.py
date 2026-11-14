"""Read-only legacy capacity against an explicitly injected isolated root."""
from pathlib import Path
import os
import re
from threading import RLock
from .capacity_selection import choose_author_entry
from .managed_downloads import _safe_path
from .fs_paths import native_io_path

class LegacyCapacityReader:
    def __init__(self, root):
        root=Path(root)
        if not root.is_absolute() or root.parent==root:raise ValueError('explicit root required')
        _safe_path(root)
        if not root.is_dir():raise ValueError('capacity root unavailable')
        self.root=root

    def measure(self, author_id):
        if type(author_id) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}',author_id):raise ValueError('invalid author')
        _safe_path(self.root)
        entries=[]
        for item in native_io_path(self.root).iterdir():
            name=item.name
            if name.endswith(f'({author_id}).zip'):
                _safe_path(item,file=True);entries.append((name,'zip',item.stat().st_size))
            elif name.endswith(f'({author_id})') and item.is_dir():
                _safe_path(item);entries.append((name,'folder',item.stat().st_size))
        chosen=choose_author_entry(author_id,entries)
        if chosen is None:return 0
        selected=native_io_path(self.root)/chosen
        if chosen.endswith('.zip'):
            _safe_path(selected,file=True);return selected.stat().st_size
        _safe_path(selected)
        total=0
        def raise_error(error):raise error
        for folder,dirs,files in os.walk(selected,topdown=True,onerror=raise_error,followlinks=False):
            folder=Path(folder);_safe_path(folder)
            for name in dirs:_safe_path(folder/name)
            for name in files:
                path=folder/name;_safe_path(path,file=True);total+=path.stat().st_size
        return total

class LegacyCapacityService:
    def __init__(self, reader, cache):
        cache.bind_root(reader.root)
        self.reader=reader;self.cache=cache
    def cached(self, author_id):return self.cache.snapshot(author_id)
    def measure(self, author_ids):
        result={}
        for author_id in author_ids:
            try:result[author_id]=self.reader.measure(author_id)
            except (OSError,ValueError):result[author_id]=None
        return result

class MeasurementLease:
    """Cancel versus commit decision; SEALED is not commit success."""
    OPEN='open';CANCELLED='cancelled';SEALED='sealed';COMMITTED='committed';FAILED='failed';UNKNOWN='unknown';EMPTY='empty'
    def __init__(self):self._lock=RLock();self._state=self.OPEN
    @property
    def state(self):
        with self._lock:return self._state
    def cancel(self):
        with self._lock:
            if self._state==self.OPEN:self._state=self.CANCELLED
            return self._state
    def seal(self):
        with self._lock:
            if self._state!=self.OPEN:return False
            self._state=self.SEALED
            return True
    def publish(self,cache,values,revisions):
        from .capacity_cache import CommitOutcomeUnknown
        try:result=cache.commit_batch(values,revisions,decide=self.seal)
        except BaseException as error:
            with self._lock:
                if isinstance(error,CommitOutcomeUnknown):self._state=self.UNKNOWN
                elif self._state!=self.CANCELLED:self._state=self.FAILED
            raise
        with self._lock:
            if self._state==self.SEALED:self._state=self.COMMITTED
            elif self._state==self.OPEN:self._state=self.EMPTY
        return result
