"""Read/control facade: each media owner retains its durable JobService ledger."""
from pathlib import Path

def validate_media_roots(*roots):
    paths=[Path(p).resolve() for p in roots if p is not None]
    if any(a==b or a in b.parents or b in a.parents for i,a in enumerate(paths) for b in paths[i+1:]):
        raise ValueError('media storage roots must be disjoint')

def validate_readonly_boundaries(writable_roots, readonly_roots):
    """Reject each writable root intersecting any read-only root."""
    for writable in writable_roots:
        if writable is not None:
            for readonly in readonly_roots:
                if readonly is not None:
                    validate_media_roots(writable, readonly)

class MediaJobs:
    def __init__(self,*owners): self.owners=owners
    @property
    def faulted(self): return any(o.faulted for o in self.owners)
    @property
    def closed(self): return all(o.closed for o in self.owners)
    def snapshot(self):
        rows=tuple(j for owner in self.owners for j in owner.snapshot())
        if len({j.job_id for j in rows})!=len(rows): raise RuntimeError('ambiguous media job identity')
        return rows
    def _owner(self,job_id):
        matches=[o for o in self.owners if any(j.job_id==job_id for j in o.snapshot())]
        if len(matches)>1: raise RuntimeError('ambiguous media job identity')
        return matches[0] if matches else None
    def control(self,job_id,action,expected_revision=None,*,expected_control_revision=None):
        owner=self._owner(job_id)
        return False if owner is None else owner.control(job_id,action,expected_revision=expected_revision,expected_control_revision=expected_control_revision)
    def operation_ids(self,job_id):
        owner=self._owner(job_id)
        return () if owner is None else owner.operation_ids(job_id)
