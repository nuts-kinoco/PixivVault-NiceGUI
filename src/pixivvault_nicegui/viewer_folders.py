"""Resolve viewer folders from the saved library catalog without creating paths."""
from .fs_paths import logical_path, native_io_path
from .legacy_backup_history import author_storage_target
from .local_media_adapter import MediaMissing


def viewer_work_folder(services, art, page_no):
    """Return a physical folder for a verified saved page of the current work."""
    images = services.acquire_library_images()
    try:
        if not images.can_open_external(art.work_id):
            return None
        try:
            source = images.external_target(art.work_id, page_no)
        except MediaMissing:
            if page_no == 0:
                return None
            source = images.external_target(art.work_id, 0)
    finally:
        images.close()

    source = logical_path(source)
    legacy_root = getattr(services, 'library_image_root', None)
    owner = getattr(services, 'managed_downloads', None)
    if owner is not None:
        managed_root = logical_path(owner.root)
        if source.is_relative_to(managed_root) and source.parent != managed_root:
            return source.parent if native_io_path(source.parent).is_dir() else None
    if legacy_root is None or not art.author_id:
        return None
    author_folder = author_storage_target(native_io_path(legacy_root), art.author_id, native_io_path(source))
    if author_folder is None or not native_io_path(author_folder).is_dir():
        return None
    author_folder = logical_path(author_folder)
    return source.parent if source.is_relative_to(author_folder) else author_folder
