"""Theme field inheritance for real multi-media download controls."""
import ast
import re
from pathlib import Path
import pytest
from fastapi import FastAPI
from nicegui import Client, ui
from tools.p3_residuals_fixtures import create_services
from pixivvault_nicegui.ui.media_views import MediaDownloadView

@pytest.mark.asyncio
async def test_real_media_download_fields_do_not_force_dark(tmp_path):
    services,_=create_services(FastAPI(),tmp_path/'fixture')
    client=Client(ui.page('/_theme_field_inheritance'))
    try:
        with client:view=MediaDownloadView(services,navigate=lambda _:None)
        managed=view.views['illustration'];novel=view.views['novel'];ugoira=view.views['ugoira']
        controls=(managed.search_input,managed.direct_input,managed.author_input,managed.follow_search_input,
                  novel.work_input,novel.format_select,ugoira.work,ugoira.author,ugoira.format)
        assert len(controls)==9
        for control in controls:
            assert 'dark' not in control.props
        assert novel.format_select.value in ('EPUB','TXT','BOTH')
        assert ugoira.format.value in ('gif','mp4','frames')
        assert 'pv-ugoira-format' in ugoira.format.classes
        assert ugoira.format.options=={'gif':'GIF','mp4':'MP4','frames':'フォルダー'}
    finally:client.delete();services.close()

def test_ui_props_do_not_force_dark_in_any_literal_or_fstring():
    ui_dir=Path(__file__).resolve().parents[1]/'src/pixivvault_nicegui/ui'
    offenders=[]
    for path in ui_dir.glob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        for call in ast.walk(tree):
            if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Attribute) or call.func.attr!='props':continue
            for arg in call.args:
                for node in ast.walk(arg):
                    if isinstance(node,ast.Constant) and isinstance(node.value,str) and re.search(r'(?<![\w-])dark(?:\s|=|$)',node.value):
                        offenders.append((path.name,call.lineno))
    assert offenders==[]
