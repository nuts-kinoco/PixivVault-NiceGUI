"""Install diagnostics before importing the main bootstrap module."""


def main():
    try:
        from .crash_evidence import install
        install()
    except Exception:
        # Diagnostics must not prevent the application from starting.
        pass
    from .bootstrap import main as bootstrap_main
    return bootstrap_main()
