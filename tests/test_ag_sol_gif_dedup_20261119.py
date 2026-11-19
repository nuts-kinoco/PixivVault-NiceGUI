import sys
import pytest

from pixivvault_nicegui.ugoira_fixtures import build_ugoira_fixture
from pixivvault_nicegui.ugoira_practical import PracticalUgoiraJobs
from pixivvault_nicegui.ugoira_mp4 import UgoiraMp4Error


def test_existing_gif_reused_for_mp4_fallback_and_restart(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise UgoiraMp4Error()
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', fail)
    root = tmp_path / 'ugoira'
    owner = PracticalUgoiraJobs(root, build_ugoira_fixture(), codec_python=sys.executable)
    try:
        gif_job_id = None
        for output in ('gif', 'mp4'):
            accepted = owner.enqueue_selection(owner.preview('123', '91', output), 'dedup-' + output)
            if output == 'gif':
                gif_job_id = accepted.job_id
            with owner.jobs._condition:
                assert owner.jobs._condition.wait_for(
                    lambda: owner.jobs.retained_job(accepted.job_id).state in {'done', 'error'}, timeout=15)
            job = owner.jobs.retained_job(accepted.job_id)
            assert job.state == 'done'
        assert len(list(root.glob('gifs/*/animation.gif'))) == 1
        assert len(owner.library()) == 1
        assert len(owner.playback(owner.library()[0])['frames']) == 3
        with monkeypatch.context() as patch:
            patch.setattr(owner, '_load', lambda *args, **kwargs: pytest.fail('notification must not decode on UI thread'))
            assert owner.fallback_message(job)
        with owner.jobs._condition:
            assert owner.jobs._condition.wait_for(lambda: owner.jobs._active is None, timeout=5)
        assert gif_job_id in owner.jobs.retire_jobs([gif_job_id])
        assert len(owner.library()) == 1
        assert owner.fallback_message(job)
    finally:
        owner.close()
    owner = PracticalUgoiraJobs(root, build_ugoira_fixture(), codec_python=sys.executable)
    try:
        assert len(owner.library()) == 1
        assert len(owner.playback(owner.library()[0])['frames']) == 3
        existing = next(root.glob('gifs/*/animation.gif'))
        existing.write_bytes(b'x' * existing.stat().st_size)
        with pytest.raises(ValueError):
            owner.library()
        assert not owner.fallback_message(job)
    finally:
        owner.close()


def test_previous_mp4_fallback_gif_is_reused(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise UgoiraMp4Error()
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', fail)
    root = tmp_path / 'ugoira'
    owner = PracticalUgoiraJobs(root, build_ugoira_fixture(), codec_python=sys.executable)
    try:
        for index in range(2):
            accepted = owner.enqueue_selection(owner.preview('123', '91', 'mp4'), 'repeat-' + str(index))
            with owner.jobs._condition:
                assert owner.jobs._condition.wait_for(
                    lambda: owner.jobs.retained_job(accepted.job_id).state in {'done', 'error'}, timeout=15)
            assert owner.jobs.retained_job(accepted.job_id).state == 'done'
        assert len(list(root.glob('gifs/*/animation.gif'))) == 1
        assert len(owner.library()) == 1
    finally:
        owner.close()


def test_unrelated_corrupt_gif_does_not_block_new_fallback(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise UgoiraMp4Error()
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', fail)
    root = tmp_path / 'ugoira'
    owner = PracticalUgoiraJobs(root, build_ugoira_fixture(), codec_python=sys.executable)
    try:
        foreign = root / 'gifs' / ('a' * 32)
        foreign.mkdir()
        (foreign / 'animation.gif').write_bytes(b'corrupt')
        (foreign / 'receipt.json').write_bytes(b'not json')
        accepted = owner.enqueue_selection(owner.preview('123', '91', 'mp4'), 'unrelated-corrupt')
        with owner.jobs._condition:
            assert owner.jobs._condition.wait_for(
                lambda: owner.jobs.retained_job(accepted.job_id).state in {'done', 'error'}, timeout=15)
        assert owner.jobs.retained_job(accepted.job_id).state == 'done'
        assert (foreign / 'animation.gif').read_bytes() == b'corrupt'
    finally:
        owner.close()
