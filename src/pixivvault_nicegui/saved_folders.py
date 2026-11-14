"""Explicit L18 locations, pinned to active owners; never derives a root from an allowlist."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from .desktop_integration import DesktopActionRejected, DesktopUnavailable
from .fs_paths import logical_path


@dataclass(frozen=True, slots=True, eq=False)
class SavedFolderChoice:
    key: str
    label: str
    path: Path = field(repr=False)
    _service: object = field(repr=False)
    _desktop: object = field(repr=False)
    _profile: object = field(repr=False)
    _revision: int | None = field(repr=False)
    _owners: tuple = field(repr=False)


def desktop_saved_roots(native_profile, *configured_roots):
    """Startup-only authority: fixed marked native child plus explicit CLI roots."""
    roots = [logical_path(value) for value in configured_roots if value is not None]
    if native_profile is not None:
        base, _ = native_profile.saved_folder_context()
        if base is not None:
            roots.append(base)
    return tuple(dict.fromkeys(roots))


def _members(services):
    # Use the owner which actually accepted this launch configuration. Settings,
    # caches and allowed_roots are not alternative guesses at a media root.
    result = []
    for key, label, attribute, native_child in (
        ('artwork', 'イラスト・漫画', 'managed_downloads', 'artwork-originals'),
        ('novel', '小説', 'novel_jobs', 'novels'),
        ('ugoira', 'うごイラ', 'ugoira', 'ugoira'),
        ('images', '保存した画像', 'download_writer', None),
        ('legacy-zip', '互換ZIP', 'legacy_zip_exporter', 'legacy-zip-exports'),
        ('exports', 'バックアップ・出力', 'save_exports', 'export-staging'),
    ):
        owner = getattr(services, attribute, None)
        if owner is not None:
            result.append((key, label, owner, logical_path(owner.root), native_child))
    repository = getattr(services, 'library_repository', None)
    root = getattr(services, 'library_image_root', None)
    if repository is not None and root is not None:
        result.append(('readonly', '旧ライブラリ（読取専用）',
                       repository, logical_path(root), None))
    return tuple(result)


def saved_folder_choices(services):
    if services.closing or services.closed:
        raise DesktopActionRejected('終了処理中は保存先を開けません。')
    desktop = services.desktop
    profile = getattr(services, 'native_profile', None)
    base, revision = profile.saved_folder_context() if profile is not None else (None, None)
    members = _members(services)
    if base is None and not members:
        return ()
    if desktop is None or desktop.launcher is None:
        raise DesktopUnavailable('保存フォルダはデスクトップ起動時に開けます。')
    if base is not None:
        for key, _, _, root, child in members:
            if key == 'readonly':
                launched = profile.launch_snapshot.library_image_root
                if launched is None or root != logical_path(launched):
                    raise DesktopActionRejected('起動時の保存先を確認できません。')
            elif child is None or root != base / child:
                raise DesktopActionRejected('起動時の保存先を確認できません。')
        owners = tuple((key, owner, root) for key, _, owner, root, _ in members)
        result = (SavedFolderChoice('native', '保存フォルダ', base,
                  services, desktop, profile, revision, owners),)
    else:
        result = tuple(SavedFolderChoice(key, label, root,
                       services, desktop, profile, revision, ((key, owner, root),))
                       for key, label, owner, root, _ in members)
    if services.closing or services.closed or services.desktop is not desktop:
        raise DesktopActionRejected('保存先が変更されました。もう一度選択してください。')
    return result


def _same_selection(first, second):
    return (first.key == second.key and first.path == second.path
            and first._service is second._service and first._desktop is second._desktop
            and first._profile is second._profile and first._revision == second._revision
            and len(first._owners) == len(second._owners)
            and all(a_key == b_key and a_owner is b_owner and a_root == b_root
                    for (a_key, a_owner, a_root), (b_key, b_owner, b_root)
                    in zip(first._owners, second._owners)))


async def open_saved_folder(services, choice, *, is_current=None):
    def current():
        try:
            return is_current is None or is_current() is True
        except Exception:
            return False

    def open_target():
        if (not isinstance(choice, SavedFolderChoice) or choice._service is not services
                or not current() or services.closing or services.closed):
            raise DesktopActionRejected('保存先の選択は無効になりました。')
        # Closing must not dispose the selected owner between admission and the
        # single OS action. All work, including this fence, runs off the UI loop.
        with services._close_lock:
            if not current() or services.closing or services.closed:
                raise DesktopActionRejected('保存先の選択は無効になりました。')
            available = saved_folder_choices(services)
            if not any(_same_selection(choice, value) for value in available):
                raise DesktopActionRejected('保存先が変更されました。もう一度選択してください。')
            if not current():
                raise DesktopActionRejected('保存先の選択は無効になりました。')
            # This is the final admission point. DesktopIntegration repeats its
            # existing lstat/reparse/allowed-root checks; it never creates paths.
            return choice._desktop.open_saved(choice.path)

    return await asyncio.to_thread(open_target)
