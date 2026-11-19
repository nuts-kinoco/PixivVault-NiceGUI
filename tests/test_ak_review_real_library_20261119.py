from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.library_models import LibraryWork
from pixivvault_nicegui.models import Artwork
from z_learning_support_20261118 import FakeReader


def test_seed_accepts_real_library_work_and_preserves_api_tags(tmp_path):
    class Tagger:
        def current_fingerprint(self): return 'fingerprint'
        def try_current_fingerprint(self): return 'fingerprint'
        def predict_with_fingerprint(self, data): return ('fingerprint', [('local_tag', .9)])
    works = (LibraryWork(Artwork('1000', 'empty', 'author', tags=()), '10'),
             LibraryWork(Artwork('1001', 'tagged', 'author', tags=('api',)), '10'))
    looked_up = []
    def image(wid):
        looked_up.append(wid)
        return b'image'
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=Tagger(),
        local_image=image, saved_works=lambda: works, saved_ids=lambda: ('1000', '1001'))
    try:
        service.refresh_saved_seeds().result(10)
        prefs = service._submit(lambda: service._effective_preferences(service._database())).result(10)
        assert looked_up == ['1000']
        assert prefs['local_tag']['seed_saved'] == 1
        assert works[1].artwork.tags == ('api',)
    finally:
        service.close()
