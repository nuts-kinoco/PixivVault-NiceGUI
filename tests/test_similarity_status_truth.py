"""Single-page GPU execution is not proof a bulk worker was started."""
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.ui.similar_view import SimilarView


@pytest.mark.asyncio
async def test_manual_gpu_inference_does_not_enable_pause_of_nonexistent_bulk_worker():
    client=Client(ui.page('/_manual_gpu_status'))
    class Source:
        def search(self, *args):
            return dict(status='ok', results=[],pool_size=1,excluded_same_work=0)
    state=dict(phase='idle',started=False,provider='cuda',requested='auto',
               indexed=0,resumed=0,errors=0,examined=0,fallback_reason=None)
    try:
        with client:
            view=SimilarView(Source(),lambda *args:None,analysis_status=lambda:dict(state),
                             start_analysis=lambda:None,pause_analysis=lambda:None)
            # No loop-backed timer is needed for a deterministic status render.
            view.dialog.open()
            await view.refresh_analysis_status()
            assert 'GPU (CUDA)' in view.analysis_text.text
            assert '全ページ解析は未開始' in view.analysis_text.text
            assert 'ワーカー累計' in view.analysis_text.text
            assert view.pause_button.props.get('disable') is True
            assert view.start_button.text=='全保存ページの解析'
            state['started']=True
            await view.refresh_analysis_status()
            assert '待機中' in view.analysis_text.text
            assert not view.pause_button.props.get('disable')
    finally:
        client.delete()
