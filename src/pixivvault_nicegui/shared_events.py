"""Best-effort, fixed-text event delivery to the application-owned log."""


def emit_shared(services, message, *, level='normal'):
    log = getattr(services, 'shared_log', None)
    if log is not None:
        try:
            log.append(message, level=level)
        except Exception:
            # Logging must never change a completed operation's result.
            pass
