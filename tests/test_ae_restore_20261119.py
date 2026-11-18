from types import SimpleNamespace


def test_restore_event_resizes_owned_window_only():
    from pixivvault_nicegui.native_layout import bind_restore_size

    events = {}
    sizes = []
    native = SimpleNamespace(main_window=SimpleNamespace(resize=lambda *size: sizes.append(size)),
                             on=lambda name, callback: events.setdefault(name, callback))
    bind_restore_size(native)
    assert set(events) == {'restored'}
    events['restored']()
    assert sizes == [(1280, 720)]
    native.main_window = None
    events['restored']()
    assert sizes == [(1280, 720)]
