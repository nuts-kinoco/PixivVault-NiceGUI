"""Repeated explicit searches retry a page, not a full media-index rebuild."""
import pytest

from pixivvault_nicegui import managed_similarity as mod
from pixivvault_nicegui.local_media_adapter import MediaUnavailable
from test_similarity_preparation_lifecycle import replaced_cpu


def test_media_index_refresh_is_rate_limited_but_page_recovery_is_immediate(tmp_path, monkeypatch):
    service=replaced_cpu(tmp_path)
    source=service.similarity_source
    legacy=service._similarity_legacy_inputs
    real_token=legacy._token
    real_refresh=legacy.refresh_media_index
    clock=[100.0]
    unavailable=[True]
    token_attempts=[]
    rescans=[]
    monkeypatch.setattr(mod,'monotonic',lambda:clock[0])
    def token(wid,page):
        if str(wid)=='101':
            token_attempts.append((clock[0],page))
            if unavailable[0]:
                raise MediaUnavailable('synthetic removable media offline')
        return real_token(wid,page)
    def refresh():
        rescans.append(clock[0])
        return real_refresh()
    monkeypatch.setattr(legacy,'_token',token)
    monkeypatch.setattr(legacy,'refresh_media_index',refresh)
    try:
        assert source.search('102',1)['status']=='ok'
        assert source._legacy_cpu_retry==frozenset({('101',1)})
        assert rescans==[]
        for _ in range(3):
            assert source.search('102',1)['status']=='ok'
        assert rescans==[100.0], 'full private index must not rescan on every click'
        assert len(token_attempts)>=4, 'page-level proof still retries every explicit search'
        clock[0]+=mod.MEDIA_REFRESH_SECONDS
        assert source.search('102',1)['status']=='ok'
        assert rescans==[100.0,130.0]
        unavailable[0]=False
        assert source.search('101',1)['status']=='ok'
        assert source._legacy_cpu_retry==frozenset()
        assert rescans==[100.0,130.0], 'restored known page need not wait for index rescan'
        assert service._egara_inference is None
    finally:
        service.close()


def test_failed_refresh_does_not_create_a_rescan_storm(tmp_path,monkeypatch):
    service=replaced_cpu(tmp_path)
    source=service.similarity_source
    legacy=service._similarity_legacy_inputs
    real_token=legacy._token
    clock=[100.0]
    calls=[]
    monkeypatch.setattr(mod,'monotonic',lambda:clock[0])
    def token(wid,page):
        if str(wid)=='101':raise MediaUnavailable('synthetic missing file')
        return real_token(wid,page)
    def failed_refresh():
        calls.append(clock[0])
        raise OSError('synthetic scan temporarily failed')
    monkeypatch.setattr(legacy,'_token',token)
    monkeypatch.setattr(legacy,'refresh_media_index',failed_refresh)
    try:
        assert source.search('102',1)['status']=='ok'
        with pytest.raises(OSError,match='synthetic scan temporarily failed'):
            source.search('102',1)
        assert source.search('102',1)['status']=='ok'
        assert calls==[100.0]
        assert not source._legacy_cpu_failure
    finally:
        service.close()
