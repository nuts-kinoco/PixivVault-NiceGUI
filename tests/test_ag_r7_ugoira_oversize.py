import sys

from pixivvault_nicegui import ugoira_storage as storage
from pixivvault_nicegui.ugoira_fixtures import build_ugoira_fixture
from pixivvault_nicegui.ugoira_mp4 import UgoiraMp4Error
from pixivvault_nicegui.ugoira_practical import PracticalUgoiraJobs


def test_independent_oversize_gif_completion(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise UgoiraMp4Error()
    monkeypatch.setattr('pixivvault_nicegui.ugoira_mp4.render_mp4', fail)
    root = tmp_path / 'ugoira'
    owner = PracticalUgoiraJobs(root, build_ugoira_fixture(), codec_python=sys.executable)

    def save(output):
        accepted = owner.enqueue_selection(owner.preview('123', '91', output), 'review-' + output)
        with owner.jobs._condition:
            assert owner.jobs._condition.wait_for(
                lambda: owner.jobs.retained_job(accepted.job_id).state in {'done', 'error'}, timeout=15)
        return owner.jobs.retained_job(accepted.job_id)

    try:
        assert save('gif').state == 'done'
        original, = root.glob('gifs/*/animation.gif')
        marker = original.parent / storage.COMPLETION
        damage = b'x' * 1025
        marker.write_bytes(damage)
        job = save('mp4')
        retained = marker.read_bytes() == damage and original.is_file()
        count = len(list(root.glob('gifs/*/animation.gif')))
        print(f'fallback_state={job.state} reason={job.error_code} retained={retained} gif_count={count}')
        assert retained and job.state == 'done' and count == 2
    finally:
        owner.close()
