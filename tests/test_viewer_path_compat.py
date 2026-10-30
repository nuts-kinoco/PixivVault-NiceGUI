"""Desktop ownership compares logical identity; filesystem reads retain long paths."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from pixivvault_nicegui.desktop_integration import DesktopIntegration,DesktopActionRejected
from pixivvault_nicegui.fs_paths import logical_path,native_io_path


@pytest.mark.parametrize('root_native',[False,True])
@pytest.mark.parametrize('target_native',[False,True])
def test_long_saved_target_inside_same_root_opens_once_for_both_spellings(tmp_path,root_native,target_native):
    root=tmp_path
    while len(str(root))<285:root/='synthetic-desktop-owner'
    native=native_io_path(root);native.mkdir(parents=True)
    folder=native/'作者 #1(456)';folder.mkdir()
    image=folder/'test %.png';image.write_bytes(b'owned synthetic bytes')
    opened=[]
    desktop=DesktopIntegration(allowed_roots=(native if root_native else root,),environment={},
        launcher=SimpleNamespace(open_default=lambda target:opened.append(target)))
    target=folder if target_native else logical_path(folder)
    desktop.open_saved(target)
    assert len(opened)==1 and native_io_path(opened[0])==folder
    assert opened[0].name=='作者 #1(456)' and image.read_bytes()==b'owned synthetic bytes'
    # Similar prefix / unknown target / unsupported file are still refused.
    outside=native_io_path(str(logical_path(native))+'-outside');outside.mkdir()
    executable=folder/'not-an-image.exe';executable.write_bytes(b'not executed')
    for invalid in (outside,folder/'missing.png',executable):
        with pytest.raises(DesktopActionRejected):desktop.open_saved(invalid)
    assert len(opened)==1


@pytest.mark.parametrize('where',['root','parent','target'])
def test_native_spelling_keeps_reparse_rejection_and_never_calls_launcher(tmp_path,monkeypatch,where):
    root=native_io_path(tmp_path/'saved');folder=root/'author(456)';folder.mkdir(parents=True)
    image=folder/'page.png';image.write_bytes(b'owned synthetic bytes')
    blocked={'root':root,'parent':folder,'target':image}[where]
    original=Path.stat;fired=[];opened=[]
    def stat(path,*args,**kwargs):
        value=original(path,*args,**kwargs)
        if logical_path(path)==logical_path(blocked):
            fired.append(path)
            return SimpleNamespace(st_mode=value.st_mode,st_nlink=value.st_nlink,
                st_size=value.st_size,st_file_attributes=getattr(value,'st_file_attributes',0)|0x400)
        return value
    monkeypatch.setattr(Path,'stat',stat)
    with pytest.raises(DesktopActionRejected,match='リンク'):
        desktop=DesktopIntegration(allowed_roots=(logical_path(root),),environment={},
            launcher=SimpleNamespace(open_default=lambda target:opened.append(target)))
        desktop.open_saved(logical_path(image))
    assert fired and opened==[]
    assert image.read_bytes()==b'owned synthetic bytes'
