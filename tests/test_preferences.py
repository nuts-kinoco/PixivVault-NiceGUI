"""Isolated preference sidecar / UI; no original settings, cookies or HTTP."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.preferences import PreferencesStore, PreferencesError, PreferencesConflict, RequestPreferences
from pixivvault_nicegui.ui.preferences_view import PreferencesView


def test_restart_defaults_and_atomic_format_updates(tmp_path):
    path=tmp_path/'preferences.sqlite3'
    store=PreferencesStore(path)
    assert store.get_setting('novel_save_format')=='epub'
    assert store.get_setting('ugoira_save_format')=='gif'
    result=store.update({'novel_save_format':'both','ugoira_save_format':'mp4'},expected_revision=0)
    assert result.revision==1
    store.close()
    reopened=PreferencesStore(path)
    assert reopened.snapshot().values['novel_save_format']=='both'
    assert reopened.get_setting('ugoira_save_format')=='mp4'
    reopened.close()


def test_existing_unmarked_database_unchanged(tmp_path):
    path=tmp_path/'original.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE settings(key TEXT,value TEXT)')
        conn.execute("INSERT INTO settings VALUES('novel_save_format','txt')")
    before=path.read_bytes()
    with pytest.raises(PreferencesError):PreferencesStore(path)
    assert path.read_bytes()==before


def test_invalid_batch_does_not_partially_commit(tmp_path):
    store=PreferencesStore(tmp_path/'settings.sqlite3')
    try:
        with pytest.raises(PreferencesError):
            store.update({'novel_save_format':'txt','api_retry_wait':'-1'},expected_revision=0)
        assert store.snapshot().revision==0
        assert store.get_setting('novel_save_format')=='epub'
        with pytest.raises(PreferencesError):store.set_setting('cookie','secret')
    finally:store.close()


def test_two_connections_cas_and_no_lost_update(tmp_path):
    path=tmp_path/'settings.sqlite3'
    first=PreferencesStore(path);second=PreferencesStore(path)
    def update(store,value):
        try:return store.update({'novel_save_format':value},expected_revision=0).revision
        except PreferencesConflict:return 'conflict'
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda args:update(*args),[(first,'txt'),(second,'both')]))
        assert sorted(results,key=str)==[1,'conflict']
        assert first.snapshot().revision==1
        assert first.snapshot().values==second.snapshot().values
    finally:first.close();second.close()


@pytest.mark.parametrize('key,value',[('api_retry_count','10'),('api_retry_wait','30.0'),('download_interval','0.5'),('pixiv_shared_image_cache_size_mb','20480'),('pixiv_disk_thumbnail_cache_size_gb','32'),('bookmark_download_mode','link')])
def test_exact_original_values_retained(tmp_path,key,value):
    store=PreferencesStore(tmp_path/'settings.sqlite3')
    try:
        store.set_setting(key,value)
        assert store.get_setting(key)==value
    finally:store.close()


def test_request_policy_never_executes_retries_and_respects_deadline(tmp_path):
    store=PreferencesStore(tmp_path/'settings.sqlite3')
    try:
        policy=RequestPreferences.from_store(store)
        assert (policy.interval_seconds,policy.retry_limit,policy.rate_limit_wait_seconds)==(0.6,3,5.0)
        assert policy.delay_before_request(last_started=1,now=1.2,deadline=3)==pytest.approx(0.4)
        assert policy.delay_before_request(last_started=None,now=1,deadline=2)==0
        with pytest.raises(PreferencesError):policy.delay_before_request(last_started=1,now=1.2,deadline=1.5)
    finally:store.close()


@pytest.mark.asyncio
async def test_two_ui_drafts_reject_stale_save_and_preserve_input(tmp_path):
    store=PreferencesStore(tmp_path/'settings.sqlite3')
    client=Client(ui.page('/_preferences_test'))
    applied=[]
    try:
        with client:
            first=PreferencesView(store,enabled_keys={'novel_save_format'},on_change=applied.append)
            second=PreferencesView(store,enabled_keys={'novel_save_format'})
            first.open();second.open()
            first.controls['novel_save_format'].set_value('txt')
            assert first.save()
            second.controls['novel_save_format'].set_value('both')
            assert not second.save()
            assert second.controls['novel_save_format'].value=='both'
            assert store.get_setting('novel_save_format')=='txt'
            assert len(applied)==1
            second.open()
            second.controls['novel_save_format'].set_value('both')
            assert second.save()
            first.close()
            assert not first.save()
    finally:client.delete();store.close()


@pytest.mark.asyncio
async def test_disabled_consumer_does_not_silently_persist(tmp_path):
    store=PreferencesStore(tmp_path/'settings.sqlite3');client=Client(ui.page('/_preferences_disabled'))
    try:
        with client:
            view=PreferencesView(store,enabled_keys={'novel_save_format'})
            view.open()
            assert not view.controls['api_retry_count'].enabled
            view.controls['api_retry_count'].set_value('10')
            assert view.save()
            assert store.get_setting('api_retry_count')=='3'
    finally:client.delete();store.close()
