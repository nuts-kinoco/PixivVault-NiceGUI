from dataclasses import replace
from threading import Event
import pytest
from pixivvault_nicegui.managed_novel_jobs import FixtureNovelSource, ManagedNovelJobs, NovelJobIntent, _canonical, _hash
from pixivvault_nicegui.ugoira_practical import PracticalUgoiraJobs, PinnedUgoiraSource
from pixivvault_nicegui.ugoira_fixtures import FixtureUgoiraReader


def wait(owner,job,state):
    with owner.jobs._condition:
        assert owner.jobs._condition.wait_for(lambda:any(j.job_id==job.job_id and j.state==state for j in owner.jobs.snapshot()),15),owner.jobs.snapshot()


def test_novel_optional_pin_keeps_old_spec_and_rejects_changed_worker_source(tmp_path):
    body=dict(id='123',userId='91',userName='Author',title='Title',content='Body',seriesNavData=None,textEmbeddedImages={})
    old=NovelJobIntent('123','91','TXT')
    assert 'expected_source_sha256' not in old.spec()
    source=FixtureNovelSource({'123':body})
    owner=ManagedNovelJobs(tmp_path/'novel',source)
    try:
        intent=replace(old,expected_source_sha256=_hash(_canonical(dict(body,content='Earlier'))))
        job=owner.enqueue(intent,'pin')
        wait(owner,job,'error')
        assert owner.saved_entries()==()
        assert owner.jobs.operation_ids(job.job_id)==('pin',)
    finally: owner.close()
    reopened=ManagedNovelJobs(tmp_path/'novel',source)
    try: assert reopened.jobs.snapshot()[0].state=='error'
    finally: reopened.close()


def test_ugoira_revision_changes_during_zip_are_not_published(tmp_path):
    class Reader(FixtureUgoiraReader):
        date='first'
        def get_artwork_metadata_raw(self,wid,aid):
            return dict(super().get_artwork_metadata_raw(wid,aid),updateDate=self.date)
        def get_ugoira_archive(self,manifest):
            raw=super().get_ugoira_archive(manifest)
            self.date='changed'
            return raw
    owner=PracticalUgoiraJobs(tmp_path/'ugoira',PinnedUgoiraSource(Reader()))
    try:
        selection=owner.preview('123','91','frames')
        assert selection.spec['update_date']=='first'
        job=owner.enqueue_selection(selection,'pin')
        wait(owner,job,'error')
        assert owner.library()==()
    finally:owner.close()


def test_ugoira_old_spec_still_works_when_reader_now_supplies_revision(tmp_path):
    class Reader(FixtureUgoiraReader):
        def get_artwork_metadata_raw(self,wid,aid):
            return dict(super().get_artwork_metadata_raw(wid,aid),updateDate='new')
    owner=PracticalUgoiraJobs(tmp_path/'ugoira',PinnedUgoiraSource(Reader()))
    try:
        selection=owner.preview('123','91','frames')
        selection.spec.pop('update_date')
        job=owner.enqueue_selection(selection,'old')
        wait(owner,job,'done')
        assert len(owner.library())==1
    finally:owner.close()
