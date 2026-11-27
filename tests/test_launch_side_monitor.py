import pytest

from tools import launch_side_monitor as launcher


def monitor(handle, rect, primary=False, work=None):
    return launcher.Monitor(handle, rect, work or rect, primary)


def test_selects_leftmost_matching_nonprimary_landscape_monitor():
    monitors = [
        launcher.Monitor(1, (0, 0, 1920, 1080), (0, 0, 1920, 1040), True),
        launcher.Monitor(2, (1920, 0, 3000, 1920), (1920, 0, 3000, 1880), False),
        launcher.Monitor(3, (1920, 0, 3840, 1080), (1920, 0, 3840, 1040), False),
        launcher.Monitor(4, (-1920, 0, 0, 1080), (-1920, 0, 0, 1040), False),
    ]
    assert launcher.select_target(monitors).handle == 4
    assert launcher.select_target(monitors[:2]) is None


def test_discover_maps_physical_target_to_system_coordinates(monkeypatch):
    from contextlib import contextmanager
    contexts = []
    @contextmanager
    def context(user, mode):
        contexts.append(mode)
        yield
    physical = [monitor(1, (0, 0, 1920, 1080), True), monitor(2, (-1920, 0, 0, 1080))]
    virtual = [monitor(2, (-1536, 0, 0, 864), work=(-1536, 0, 0, 832))]
    batches = iter([physical, virtual])
    monkeypatch.setattr(launcher, 'dpi_context', context)
    monkeypatch.setattr(launcher, 'enumerate_monitors', lambda user: next(batches))
    class User:
        def GetDpiForSystem(self):
            return 120
    assert launcher.discover(User()) == ((-1536, 0, 0, 832), 120)
    assert contexts == [launcher.PER_MONITOR_AWARE_V2, launcher.SYSTEM_AWARE]


def test_main_patches_real_layout_before_entry_without_opening_window(monkeypatch):
    from pixivvault_nicegui import native_layout
    observed = []
    monkeypatch.setattr(native_layout, 'primary_work_area', native_layout.primary_work_area)
    monkeypatch.setattr(launcher.runpy, 'run_module', lambda name, run_name: observed.append(
        (name, run_name, native_layout.primary_work_area(api=object()))))
    launcher.main(discover_target=lambda: ((-1536, 0, 0, 832), 120))
    assert observed == [('pixivvault_nicegui', '__main__', ((-1536, 0, 0, 832), 120))]


def test_missing_target_does_not_start_product(monkeypatch):
    monkeypatch.setattr(launcher.runpy, 'run_module', lambda *a, **k: pytest.fail('started'))
    with pytest.raises(SystemExit, match='target_monitor_missing'):
        launcher.main(discover_target=lambda: None)
