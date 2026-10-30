"""Reject reparse points using no-follow attributes, including Windows junctions.

Synthetic attribute injection avoids changing host ACLs or creating system
junctions. Root ownership checks must not use a target-following stat call.
"""
from pathlib import Path

import pytest

from pixivvault_nicegui.legacy_metadata_export import (
    _reject_links, LegacyMetadataExportError,
)
from pixivvault_nicegui.library_repository import require_frozen_library_copy


class InjectedReparseAttributes:
    def __init__(self, real):
        self.real=real
    def __getattr__(self, key):
        if key=='st_file_attributes':
            return 0x400  # Windows FILE_ATTRIBUTE_REPARSE_POINT
        return getattr(self.real,key)


@pytest.mark.parametrize('kind',('owned-export-dir','original-source-parent'))
def test_reparse_source_or_output_parent_is_rejected_before_following(monkeypatch,tmp_path,kind):
    source_root=tmp_path/'frozen-copy'
    source_root.mkdir()
    old=source_root/'old.sqlite3'
    old.write_bytes(b'synthetic input only')
    output=tmp_path/'owned-export'
    output.mkdir()
    target=output if kind=='owned-export-dir' else source_root
    true_lstat=Path.lstat

    def marked_lstat(path, *args, **kwargs):
        stat=true_lstat(path,*args,**kwargs)
        return InjectedReparseAttributes(stat) if path==target else stat

    with monkeypatch.context() as patch:
        patch.setattr(Path,'lstat',marked_lstat)
        if kind=='owned-export-dir':
            with pytest.raises(LegacyMetadataExportError,match='リンク'):
                _reject_links(output)
        else:
            with pytest.raises(ValueError,match='reparse'):
                require_frozen_library_copy(old)
    assert old.read_bytes()==b'synthetic input only'
    assert list(output.iterdir())==[]
