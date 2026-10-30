"""Explicit viewer save, bound to its visible generation and media dispatcher."""
from uuid import uuid4

class ViewerSave:
    def __init__(self,services):
        self.services=services;self.busy=False;self.operations={}
    async def save(self,work_id,still_current):
        if self.busy or not still_current() or self.services.managed_downloads is None:return False
        self.busy=True
        try:
            operation=self.operations.setdefault(work_id,uuid4().hex)
            job=await self.services.save_explicit_work(work_id,operation,is_current=still_current)
            return job is not None
        finally:self.busy=False

    async def save_gif(self,display,still_current):
        if self.busy or not still_current() or self.services.managed_downloads is None:return None
        self.busy=True
        try:return await self.services.save_viewer_gif(display,is_current=still_current)
        finally:self.busy=False

    async def save_page(self, work_id, page_no, page_count, author_id, still_current, *,
                        source_identity=None):
        if self.busy or not still_current() or self.services.managed_downloads is None:return None
        self.busy=True
        try:
            extra={}
            if source_identity is not None:
                from .managed_downloads import ManagedPageIdentity
                if (not isinstance(source_identity,ManagedPageIdentity)
                        or source_identity.work_id!=work_id
                        or author_id is not None and source_identity.author_id!=author_id):
                    raise ValueError('visible saved-page identity changed')
                page_no=source_identity.source_page_no
                page_count=source_identity.source_page_count
                author_id=source_identity.author_id
                extra['source_identity']=source_identity
            return await self.services.save_viewer_page(work_id,page_no,page_count,author_id,
                is_current=still_current,**extra)
        finally:self.busy=False
