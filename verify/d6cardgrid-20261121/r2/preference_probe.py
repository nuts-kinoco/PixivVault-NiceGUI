"""Time the real preference rebuild against an isolated synthetic SQLite profile."""
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
from types import SimpleNamespace

from nicegui import Client, ui

from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.ranking_view import RankingView


def main(root, evidence):
    root.mkdir()
    discovery = DiscoveryLearningService(root / 'discovery', object())
    services = ApplicationServices(1)
    services.discovery = discovery
    # Use the two-read path with a stable synthetic local-tag revision.
    services.local_tag_view = SimpleNamespace(revision=lambda: 1)
    client = Client(ui.page('/_preference_probe'))
    try:
        def seed():
            db = discovery._database()
            with db._transaction() as conn:
                conn.executemany('INSERT INTO discovery_preference(tag, display_tag, like_count) VALUES (?, ?, 12)',
                                 ((f'fixture-{index}', f'fixture-{index}') for index in range(1000)))

        discovery._submit(seed).result()
        with client:
            view = RankingView(services)
            view._rebuild_preferred()
            assert len(view._preferred) == 1000
            times = []
            for _ in range(30):
                start = perf_counter()
                view._rebuild_preferred()
                times.append((perf_counter() - start) * 1000)
        evidence.write_text(json.dumps(dict(tags=1000, median_ms=median(times), samples_ms=times),
                                       indent=2), encoding='ascii')
    finally:
        client.delete()
        services.local_tag_view = None
        services.close()


if __name__ == '__main__':
    main(*(Path(arg) for arg in sys.argv[1:]))
