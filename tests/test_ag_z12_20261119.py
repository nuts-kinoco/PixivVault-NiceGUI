import sys

from pixivvault_nicegui.managed_ugoira import ManagedUgoiraJobs
from pixivvault_nicegui.ugoira_mp4 import UgoiraMp4Error
from test_q01_ugoira_reasons import archive


def test_failed_mp4_falls_back_and_survives_recovery(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise UgoiraMp4Error()
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', fail)
    root = tmp_path / 'ugoira'
    owner = ManagedUgoiraJobs(root, codec_python=sys.executable)
    try:
        owner.enqueue('fallback', *archive(), output='mp4')
        with owner.jobs._condition:
            assert owner.jobs._condition.wait_for(lambda: owner.jobs.snapshot() and owner.jobs.snapshot()[0].state in {'done', 'error'}, timeout=10)
        job = owner.jobs.snapshot()[0]
        assert job.state == 'done'
        assert job.result.startswith('gifs/')
        assert (root / job.result).is_file()
    finally:
        owner.close()
    owner = ManagedUgoiraJobs(root, codec_python=sys.executable)
    try:
        assert owner.jobs.snapshot()[0].state == 'done'
        assert owner.jobs.snapshot()[0].result == job.result
    finally:
        owner.close()


def test_practical_fallback_playback_uses_gif(tmp_path, monkeypatch):
    from pixivvault_nicegui.ugoira_fixtures import build_ugoira_fixture
    from pixivvault_nicegui.ugoira_practical import PracticalUgoiraJobs
    def fail(*args, **kwargs):
        raise UgoiraMp4Error()
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', fail)
    owner = PracticalUgoiraJobs(tmp_path / 'practical', build_ugoira_fixture(), codec_python=sys.executable)
    try:
        selection = owner.preview('123', '91', 'mp4')
        owner.enqueue_selection(selection, 'practical-fallback')
        with owner.jobs._condition:
            assert owner.jobs._condition.wait_for(lambda: owner.jobs.snapshot() and owner.jobs.snapshot()[0].state in {'done', 'error'}, timeout=15)
        job = owner.jobs.snapshot()[0]
        assert job.state == 'done'
        entry = owner.library()[0]
        assert entry['actual_output'] == 'gif'
        assert len(owner.playback(entry)['frames']) == 3
    finally:
        owner.close()


def test_practical_missing_codec_falls_back(tmp_path):
    from pixivvault_nicegui.ugoira_fixtures import build_ugoira_fixture
    from pixivvault_nicegui.ugoira_practical import PracticalUgoiraJobs
    owner = PracticalUgoiraJobs(tmp_path / 'no-codec', build_ugoira_fixture())
    try:
        assert owner.codec_python is None
        owner.enqueue_selection(owner.preview('123', '91', 'mp4'), 'no-codec')
        with owner.jobs._condition:
            assert owner.jobs._condition.wait_for(lambda: owner.jobs.snapshot() and owner.jobs.snapshot()[0].state in {'done', 'error'}, timeout=15)
        assert owner.jobs.snapshot()[0].state == 'done'
        assert owner.library()[0]['actual_output'] == 'gif'
    finally:
        owner.close()
