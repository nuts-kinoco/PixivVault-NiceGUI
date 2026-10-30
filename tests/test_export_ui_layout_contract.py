"""Static guard for the CSS flow fix; real hit testing remains CoS browser acceptance."""
from pathlib import Path
import re


def test_novel_scroll_flow_does_not_shrink_catalog_or_preview():
    root = Path(__file__).resolve().parents[1]
    css = (root / 'src/pixivvault_nicegui/ui/theme.py').read_text(encoding='utf-8')
    view = (root / 'src/pixivvault_nicegui/ui/novel_save_view.py').read_text(encoding='utf-8')
    for selector in ('.pv-novel-download > *', '.pv-novel-catalog > .q-btn'):
        rule = re.search(re.escape(selector) + r'\s*\{([^}]+)\}', css)
        assert rule and re.search(r'flex-shrink\s*:\s*0\s*;', rule.group(1))
    assert "classes('w-full pv-novel-catalog')" in view
    # This structural guard is not proof of pixel geometry or ordinary click.
    # The browser checker clicks both real catalog buttons and acknowledges IDs.
