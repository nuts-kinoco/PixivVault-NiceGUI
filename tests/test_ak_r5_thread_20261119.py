import asyncio
import threading
from types import SimpleNamespace
import pytest
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from z_learning_support_20261118 import FakeReader


@pytest.mark.asyncio
async def test_service_snapshot_reads_images_on_owned_validation_worker(tmp_path):
    class Tagger:
        def current_fingerprint(self): return 'model'
        def try_current_fingerprint(self): return 'model'
        def predict_with_fingerprint(self, data): return 'model', [('local', .9)]
    threads = []
    def image(_):
        threads.append(threading.current_thread().name)
        return b'image'
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=Tagger(),
        local_image=image, saved_works=lambda: (SimpleNamespace(work_id='1', tags=()),), saved_ids=lambda: ('1',))
    try:
        await asyncio.wrap_future(service.refresh_saved_seeds())
        threads.clear()
        await service.trend_snapshot()
        assert not threads  # Cached trend access performs no image reads.
        await asyncio.wrap_future(service.refresh_saved_seeds())
        assert threads and all(name.startswith('pv-local-tag-view') for name in threads)
    finally:
        service.close()
