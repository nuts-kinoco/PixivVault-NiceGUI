"""X02 original two source modes + X04 selection continuity, synthetic only."""
import asyncio
from dataclasses import replace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_duplicate_inspection import page, group, scan_of


def unresolved():
    return ({
        'ambiguous_group':'legacy-title-9','reason':'duplicate title',
        'candidates':(
            {'work_id':'101','title':'Shared Title','author_name':'Known Artist',
             'user_id':'9','pages':({'page':1,'store_path':'synthetic','entry_name':'a.png'},)},
            {'work_id':'102','title':'Shared Title','author_name':'Known Artist',
             'user_id':'9','pages':({'page':1,'store_path':'synthetic','entry_name':'a.png'},)},
        ),
    },)


class StubSource:
    undecidable_available=True

    def __init__(self):
        self.legacy=unresolved()
        self.near=scan_of([
            group([replace(page('201'),thumb_b64='AA=='),page('202')],'near-one'),
            group([page('301'),page('302')],'near-two'),
        ])
        self.reads=0
        self.scans=0
        self.plan_calls=0
        self.fail_legacy=False

    def undecidable_groups(self):
        self.reads+=1
        if self.fail_legacy:raise OSError('injected old DB read error')
        return self.legacy

    def scan(self):
        self.scans+=1
        return self.near

    def plan(self,*args,**kwargs):
        self.plan_calls+=1
        raise AssertionError('test must not execute quarantines or mutation')


async def loaded(view):
    task=view._undecidable_task
    if task is not None:await task


@pytest.mark.asyncio
async def test_two_original_modes_read_distinct_evidence_keep_size_and_near_selection():
    source=StubSource()
    client=Client(ui.page('/_x02_dual_modes'))
    try:
        with client:
            view=DuplicateView(source)
            view.set_active(True)
        await loaded(view)
        assert view.mode=='undecidable'
        assert view.undecidable_state=='ready' and source.reads==1
        assert '決められないもの（1）' in view.mode_buttons['undecidable'].text
        assert '近似重複の候補（–）' in view.mode_buttons['near'].text
        assert not view.mode_buttons['near'].enabled
        assert view.undecidable_panel.visible and not view.near_panel.visible
        assert '1 / 1 組' in view.undecidable_notice.text
        assert list(view.undecidable_rows.descendants())
        assert not any(isinstance(node,ui.button) for node in view.undecidable_rows.descendants())
        assert not view.low_button.enabled
        view.set_mode('near')
        assert view.mode=='undecidable'  # Cannot represent unscanned as zero.

        await view.scan()
        assert view.mode=='undecidable' and view.mode_buttons['near'].enabled
        assert source.scans==1 and source.reads==2
        view.set_mode('near')
        assert view.mode_buttons['near']._props['aria-pressed']=='true'
        assert view.near_panel.visible and not view.undecidable_panel.visible
        assert view.low_button.enabled
        group_id=source.near.groups[0].group_id
        first=source.near.groups[0].items[0]
        view.toggle(group_id,first.key,True,view.selection_revision)
        assert view.selections[group_id]=={first.key}
        element_ids=[thumb.id for thumb in view.thumbnails]
        revision=view.selection_revision
        view.change_thumb_size('large')
        assert view.thumb_size=='large' and view.selections[group_id]=={first.key}
        assert [thumb.id for thumb in view.thumbnails]==element_ids
        view.set_mode('undecidable')
        assert view.selections[group_id]=={first.key}
        assert view.selection_revision==revision
        assert '決められないもの（1）' in view.mode_buttons['undecidable'].text
        view.filter('102')  # Original undecidable search is candidate work_id.
        assert '1 / 1 組' in view.undecidable_notice.text
        view.filter('known artist')
        assert '1 / 1 組' in view.undecidable_notice.text
        view.filter('')
        view.set_mode('near')
        assert view.thumb_size=='large'
        # Original gui.py:5405-5409 explicitly drops near selections excluded
        # by the shared search, even when that section is temporarily hidden.
        assert not view.selections[group_id]
        assert view.selection_revision>revision  # Search rerenders, not mode/size.
        assert all(thumb.style['width']=='200px' for thumb in view.thumbnails)
        view.filter('nonexistent')
        assert not any(view.selections.values())
        view.set_mode('undecidable')
        assert '一致する候補はありません' in [label.text for label in view.undecidable_rows.descendants()
                                                  if isinstance(label,ui.label)]
        assert source.plan_calls==0
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_managed_mode_discloses_missing_legacy_db_and_keeps_near_available():
    client=Client(ui.page('/_x02_managed_unavailable'))
    class ManagedOnly:
        undecidable_available=False
        def undecidable_groups(self):
            raise AssertionError('must not be invoked without a linked old DB')
        def scan(self):
            return scan_of([group([page('1'),page('2')])])
    try:
        with client:
            view=DuplicateView(ManagedOnly())
            view.set_active(True)
        assert view.mode=='undecidable'
        assert view.undecidable_state=='unavailable'
        assert '未接続' in view.undecidable_notice.text
        assert not list(view.undecidable_rows.descendants())
        assert '（–）' in view.mode_buttons['undecidable'].text
        await view.scan()
        assert view.mode_buttons['near'].enabled
        view.set_mode('near')
        assert view.mode=='near' and view.near_panel.visible
        assert len(view.thumbnails)==2
        assert not view.undecidable_panel.visible
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_explicit_old_ledger_with_no_unresolved_rows_reports_verified_zero():
    source=StubSource()
    source.legacy=()
    client=Client(ui.page('/_x02_real_empty_ledger'))
    try:
        with client:
            view=DuplicateView(source)
            view.set_active(True)
        await loaded(view)
        assert view.undecidable_state=='ready'
        assert view.undecidable_groups==()
        assert view.mode_buttons['undecidable'].text.endswith('（0）')
        assert '決められないものはありません' in [
            node.text for node in view.undecidable_rows.descendants() if isinstance(node,ui.label)
        ]
        assert view.mode_buttons['near'].text.endswith('（–）')
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_bad_legacy_snapshot_never_becomes_zero_and_retry_recovers():
    source=StubSource()
    source.fail_legacy=True
    client=Client(ui.page('/_x02_failed_old_ledger'))
    try:
        with client:
            view=DuplicateView(source)
            view.set_active(True)
        await loaded(view)
        assert view.undecidable_state=='error' and '読み込めませんでした' in view.undecidable_notice.text
        assert view.undecidable_groups is None
        assert '（–）' in view.mode_buttons['undecidable'].text
        await view.scan()
        view.set_mode('near')
        assert len(view.thumbnails)==4
        source.fail_legacy=False
        await view.refresh_undecidable()
        assert view.undecidable_state=='ready'
        view.set_mode('undecidable')
        assert '1 / 1 組' in view.undecidable_notice.text
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_inactive_stale_undecidable_read_cannot_repaint_new_visit():
    from threading import Event
    source=StubSource()
    started=Event()
    release=Event()
    original=source.undecidable_groups
    def delayed():
        started.set()
        assert release.wait(5)
        return original()
    source.undecidable_groups=delayed
    client=Client(ui.page('/_x02_undecidable_stale'))
    try:
        with client:
            view=DuplicateView(source)
            view.set_active(True)
        task=view._undecidable_task
        assert await asyncio.to_thread(started.wait,5)
        view.set_active(False)
        assert view.undecidable_state=='unloaded'
        view.set_active(True)
        newer=view._undecidable_task
        release.set()
        await asyncio.gather(task,newer)
        assert view.undecidable_state=='ready'
        assert '1 / 1 組' in view.undecidable_notice.text
        view.close()
        assert not view.active and view.closed
    finally:
        release.set()
        client.delete()
