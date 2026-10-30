"""Existing media views share navigation while keeping independent storage."""
from nicegui import ui
from .novel_save_view import NovelDownloadView, NovelLibraryView
from .ugoira_view import UgoiraDownloadView, UgoiraLibraryView


class MediaView:
    def __init__(self, services, *, library=False, navigate=None,
                 card_size='large', on_size_change=None):
        self.active = False
        self.closed = False
        self.views = {}
        available = []
        if services.managed_downloads is not None:
            available.append(('illustration', 'イラスト・漫画'))
        if services.novel_jobs is not None:
            available.append(('novel', '小説'))
        if services.ugoira is not None:
            available.append(('ugoira', 'うごイラ'))
        if not available:
            raise ValueError('no media owner configured')
        self.selected = available[0][0]
        with ui.column().classes('pv-gallery w-full').props(
                'data-testid=media-library' if library else 'data-testid=media-download') as self.root:
            with ui.row().classes('pv-toolbar'):
                for key, label in available:
                    ui.button(label, on_click=lambda _, key=key: self.select(key)).props(
                        f'flat data-testid=media-select-{key}')
            if services.managed_downloads is not None:
                if library:
                    from .library_view import LibraryView
                    self.views['illustration'] = LibraryView(
                        services, card_size=card_size, on_size_change=on_size_change)
                else:
                    from .managed_download_view import ManagedDownloadView
                    self.views['illustration'] = ManagedDownloadView(services)
            if services.novel_jobs is not None:
                self.views['novel'] = (NovelLibraryView(services) if library else
                                       NovelDownloadView(services, navigate=navigate))
            if services.ugoira is not None:
                self.views['ugoira'] = (UgoiraLibraryView(services) if library else
                                        UgoiraDownloadView(services, navigate=navigate))
        self.set_active(False)

    def select(self, key):
        if self.closed or key not in self.views or key == self.selected:
            return
        self.selected = key
        self.set_active(self.active)

    def set_active(self, active):
        self.active = bool(active) and not self.closed
        for key, view in self.views.items():
            visible = self.active and key == self.selected
            view.root.set_visibility(visible)
            view.set_active(visible)

    def apply_card_size(self, size):
        for view in self.views.values():
            if hasattr(view, 'apply_card_size'):
                view.apply_card_size(size)

    def close(self):
        self.closed = True
        self.active = False
        for view in self.views.values():
            view.close()


class MediaDownloadView(MediaView):
    pass


class MediaLibraryView(MediaView):
    def __init__(self, services, **kwargs):
        super().__init__(services, library=True, **kwargs)

    def open_saved_work(self, work_id, page=1):
        if not self.active or self.closed or 'illustration' not in self.views:
            return False
        self.select('illustration')
        return self.views['illustration'].open_saved_work(work_id, page)
