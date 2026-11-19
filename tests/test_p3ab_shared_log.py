from nicegui import Client, ui


def test_shared_and_queue_log_render_colored_timestamped_entries():
    from pixivvault_nicegui.queue_log import QueueLog
    from pixivvault_nicegui.ui.shared_log_view import render_log_entries
    log = QueueLog()
    log.append('sample', level='error')
    client = Client(ui.page('/_p3ab_log_renderer'))
    try:
        with client:
            panel = ui.column()
            render_log_entries(panel, log.snapshot())
        labels = [node for node in panel.descendants() if getattr(node, 'text', '').endswith('sample')]
        assert len(labels) == 1
        assert labels[0].text.startswith('[') and '] sample' in labels[0].text
        assert 'pv-queue-log-error' in labels[0].classes
    finally:
        client.delete()
