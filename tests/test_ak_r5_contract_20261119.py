"""Control vs treatment contract. Add every new local-tag consumer to this table."""
import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui import local_tagger
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.learning_view import LearningView
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ak_tagger_20261119 import make_model
from test_ak_tagger_inference_20261119 import png
from z_learning_support_20261118 import FakeReader, candidate, seed_candidates


@pytest.mark.parametrize('transition', ['disabled', 'digest', 'nonlocal', 'unsaved', 'unreadable', 'api'])
def test_control_and_invalidated_treatment_are_identical(tmp_path, monkeypatch, transition):
    class Session:
        def get_inputs(self): return [SimpleNamespace(name='input')]
        def run(self, *_): return [[(.9,)]]
    class Reader(FakeReader):
        def __init__(self): super().__init__(); self.words = []
        def search_recommendation_illusts(self, word, **kwargs):
            self.words.append(word)
            return {'works': [], 'total': 0}
    original_cls = local_tagger.LocalTagger
    class Injected(original_cls):
        def __init__(self, path, **kw):
            super().__init__(path, session_factory=lambda _: Session(), tensor_factory=lambda x: x, **kw)
    monkeypatch.setattr(local_tagger, 'LocalTagger', Injected)
    real_resolve = Path.resolve
    template = tmp_path / 'identical-initial-db'
    initial = DiscoveryLearningService(template, Reader(), following=())
    try:
        seed_candidates(initial, [candidate(0, tags=('different',)), candidate(1, tags=('other',))])
        def base():
            db = initial._database()
            with db._transaction(): db._bump_tag('api_alpha', 'seed_saved', 5, now='2026-01-01')
        initial._submit(base).result(10)
    finally:
        initial.close()

    def execute(label, treatment):
        folder = tmp_path / (label + '-model')
        folder.mkdir()
        make_model(folder)
        store = PreferencesStore(tmp_path / (label + '-preferences.sqlite'))
        store.set_setting('local_tagger_path', str(folder))
        store.set_setting('local_tagger_enabled', '1' if treatment else '0')
        manager, reader = local_tagger.TaggerManager(store), Reader()
        works = [SimpleNamespace(work_id=str(i), tags=()) for i in range(1, 5)]
        saved = {w.work_id for w in works}
        images = [png()]
        root = tmp_path / (label + '-learning')
        root.mkdir()
        for source in template.iterdir():
            shutil.copy2(source, root / source.name)
        def create():
            return DiscoveryLearningService(root, reader, following=(), tagger=manager,
                local_image=lambda _: images[0], saved_works=lambda: works, saved_ids=lambda: saved)
        service = create()
        def dump():
            def read():
                db = service._database()
                tables = [r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                return {name: sorted((tuple(r) for r in db.conn.execute('SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}
            return service._submit(read).result(10)
        def results():
            batch, _ = asyncio.run(service.refill_snapshot())
            ids = tuple(a.work_id for a in batch.artworks)
            order, frames = asyncio.run(service.rank_pending(('1000', '1001')))
            trend = asyncio.run(service.trend_snapshot())
            preferred = service.preferred_tags()
            fake_services = SimpleNamespace(discovery=service, local_tag_view=service.local_tag_view,
                preferred_ranking_tags=service.preferred_tags, preferred_ranking_tags_base=service.base_preferred_tags)
            ranking = SimpleNamespace(services=fake_services)
            RankingView._rebuild_preferred(ranking)
            mark = RankingView.preferred_tags_in(ranking, ('cat',))
            client = Client(ui.page('/ak-r5-contract-' + label + '-' + transition))
            try:
                with client:
                    display = SimpleNamespace(trend_content=ui.column(), trend_label=ui.label())
                    LearningView._render_trend(display, trend)
                    texts = tuple(getattr(x, 'text', None) for x in display.trend_content.descendants())
            finally:
                client.delete()
            return ids, order, frames, trend, preferred, mark, texts
        try:
            service.refresh_saved_seeds().result(10)
            if treatment:
                assert service.local_tag_view.current_tags('1') == ('cat',)
            asyncio.run(service.fetch_candidates())
            asyncio.run(service.refill_snapshot())
            active = results()
            if treatment:
                assert active[3]['local_seed_rows'][0]['tag'] == 'cat'
                assert 'cat' in active[4] and active[5] is True
            if transition == 'disabled': store.set_setting('local_tagger_enabled', '0')
            elif transition == 'digest' and treatment: (folder / 'model.onnx').write_bytes(b'changed')
            elif transition == 'nonlocal' and treatment:
                target = folder / 'model.onnx'
                monkeypatch.setattr(Path, 'resolve', lambda p, *a, **kw: Path(r'\\server\share\model') if p == target else real_resolve(p, *a, **kw))
            elif transition == 'unsaved': saved.clear()
            elif transition == 'unreadable': images[0] = None
            elif transition == 'api': works[:] = [SimpleNamespace(work_id=w.work_id, tags=('api_arrived',)) for w in works]
            asyncio.run(service.fetch_candidates())
            live = results()
            data = dump()
            words = tuple(reader.words)
            assert words and set(words) == {'api_alpha'}
            service.close()
            service = create()
            asyncio.run(service.fetch_candidates())
            reopened = results()
            reopened_data = dump()
            words = tuple(reader.words)
            assert 'managed_local_seed' not in data and 'managed_local_seed' not in reopened_data
            assert 'cat' not in json.dumps((data, reopened_data), ensure_ascii=False)
            assert 'cat' not in words
            return data, reopened_data, words, live, reopened
        finally:
            service.close()
            manager.close()
            store.close()

    treatment = execute('treatment', True)
    control = execute('control', False)
    assert treatment == control
