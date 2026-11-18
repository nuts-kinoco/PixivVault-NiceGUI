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
