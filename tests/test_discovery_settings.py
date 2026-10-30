import asyncio
import pytest
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.learning_repository import LearningConflict
from test_discovery_learning import Reader
from test_discovery_seed import legacy_fixture

async def test_original_defaults_cas_idempotency_restart(tmp_path):
    root=tmp_path/'settings';service=DiscoveryLearningService(root,Reader())
    try:
        before=await service.settings_snapshot()
        assert before.values=={'discovery_search_mode':'all','discovery_ratio_subject':'50.0','discovery_ratio_style':'20.0'}
        values={'discovery_search_mode':'r18','discovery_ratio_subject':100,'discovery_ratio_style':100}
        result=await service._wait(service.submit_settings_update(before.revision,values,'set'))
        assert result.effective_ratios['sure']==pytest.approx(.05)
        assert result.effective_ratios['subject']==pytest.approx(.475)
        assert result.effective_ratios['style']==pytest.approx(.475)
        assert result.revision>before.revision
        assert await service._wait(service.submit_settings_update(before.revision,values,'set'))==result
        with pytest.raises(LearningConflict):await service._wait(service.submit_settings_update(before.revision,values,'other'))
        with pytest.raises(ValueError):await service._wait(service.submit_settings_update(result.revision,values,'set'))
        assert (await asyncio.to_thread(service.recommendation_snapshot))['search_mode']=='r18'
    finally:service.close()
    service=DiscoveryLearningService(root,Reader())
    try:
        restored=await service.settings_snapshot()
        assert restored==result
        assert await service._wait(service.submit_settings_update(before.revision,values,'set'))==result
    finally:service.close()

@pytest.mark.parametrize('values',[
    {'discovery_following_cursor':'0'},
    {'discovery_search_mode':'unknown','discovery_ratio_subject':50,'discovery_ratio_style':20},
    {'discovery_search_mode':'all','discovery_ratio_subject':-1,'discovery_ratio_style':20},
    {'discovery_search_mode':'all','discovery_ratio_subject':float('nan'),'discovery_ratio_style':20},
    {'discovery_search_mode':'all','discovery_ratio_subject':50,'discovery_ratio_style':101},
])
async def test_only_original_public_setting_values_accepted(tmp_path,values):
    service=DiscoveryLearningService(tmp_path/'invalid',Reader())
    try:
        before=await service.settings_snapshot()
        with pytest.raises(ValueError):await service._wait(service.submit_settings_update(before.revision,values,'bad'))
        assert await service.settings_snapshot()==before
    finally:service.close()

async def test_seed_settings_retained_and_updated_only_in_managed_ledger(tmp_path):
    import hashlib
    source=legacy_fixture(tmp_path/'seed');before=hashlib.sha256(source.read_bytes()).hexdigest()
    service=DiscoveryLearningService(tmp_path/'managed',Reader(),seed_readonly_db=source)
    try:
        snapshot=await service.settings_snapshot()
        assert snapshot.values['discovery_search_mode']=='safe'
        values=dict(snapshot.values,discovery_search_mode='all')
        await service._wait(service.submit_settings_update(snapshot.revision,values,'change'))
    finally:service.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
