"""Install diagnostics before importing the main bootstrap module."""


def main():
    import sys
    # The frozen application is also the owned codec executable. Never enter
    # the GUI/bootstrap path for a malformed internal worker invocation.
    if '--internal-ugoira-codec' in sys.argv[1:]:
        if (not getattr(sys, 'frozen', False) or len(sys.argv) != 3
                or sys.argv[1] != '--internal-ugoira-codec'):
            return 1
        try:
            from .ugoira_mp4_worker import run as codec_run
            return codec_run(sys.argv[2])
        except Exception:
            # The codec log must not expose paths or image metadata.
            return 1
    try:
        from .crash_evidence import install
        install()
    except Exception:
        # Diagnostics must not prevent the application from starting.
        pass
    from .bootstrap import main as bootstrap_main
    return bootstrap_main()
