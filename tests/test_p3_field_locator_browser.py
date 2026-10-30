"""The shipped checker resolves a Quasar field around, at, or below a test id."""
import re
from pathlib import Path
from playwright.sync_api import sync_playwright


def test_shipped_checker_resolves_realistic_quasar_field_shapes():
    source=(Path(__file__).resolve().parents[1]/'tools/check_theme_fields_browser.py').read_text(encoding='utf-8')
    expressions=re.findall(r'const x=(.*?);',source)
    assert len(expressions)==2  # input sampling and the ugoira label each use it
    html="""<div class='q-field q-field--dark'><input data-testid='inside'></div>
    <div class='q-field q-field--dark' data-testid='self'><input></div>
    <div data-testid='outside'><div class='q-field q-field--dark'><input></div></div>
    <div data-testid='missing'></div>"""
    with sync_playwright() as play:
        browser=play.chromium.launch(channel='msedge',headless=True)
        try:
            page=browser.new_page();page.set_content(html)
            for expression in expressions:
                for marker in ('inside','self','outside'):
                    field=page.get_by_test_id(marker)
                    result=field.evaluate('e => {const x='+expression+'; return !!x && x.classList.contains("q-field--dark");}')
                    assert result,marker
                assert not page.get_by_test_id('missing').evaluate('e => {const x='+expression+'; return !!x;}')
        finally:browser.close()
