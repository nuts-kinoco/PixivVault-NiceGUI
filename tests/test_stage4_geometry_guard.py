"""Stage4 acceptance checker self-tests using fully generated HTML only."""
from playwright.sync_api import sync_playwright
from tools.check_stage4_visual_geometry_browser import JS_GEOMETRY, TABS


def test_fixed_overlay_blocks_navigable_tab():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.set_content(
                '<button data-testid="tab-download" style="position:absolute;left:20px;top:20px;width:200px;height:50px">Go</button>'
                '<div style="position:fixed;inset:0;z-index:9999;background:transparent"></div>'
            )
            metrics = page.evaluate(JS_GEOMETRY)
            assert "tab-download" in metrics["blockedNav"], metrics
        finally:
            browser.close()


def test_missing_required_tab_is_not_accepted():
    from tools.check_stage4_visual_geometry_browser import require_tabs
    valid = [{"tab": name, "elapsed_ms": 1, "geometry": {
        "horizontalOverflow": False, "navOverlap": [], "blockedNav": [],
        "blockedControls": [], "unreachableControls": [],
        "clippedTextLabels": []}} for name in TABS]
    assert require_tabs(valid) is True
    invalid = [dict(row) for row in valid]
    invalid[0] = {"tab": TABS[0], "skipped": True, "reason": "not enabled"}
    try:
        require_tabs(invalid)
    except AssertionError:
        pass
    else:
        raise AssertionError("missing/disabled required tab was allowed")


def test_partially_clipped_fixed_button_cannot_pass():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.set_content(
                '<button data-testid="tab-download" style="position:fixed;left:1000px;top:200px;width:100px;height:45px">Go</button>'
            )
            metrics = page.evaluate(JS_GEOMETRY)
            assert "tab-download" in metrics["unreachableControls"], metrics
        finally:
            browser.close()


def test_transparent_overlay_on_input_is_a_blocker():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.set_content(
                '<input data-testid="ranking-date" style="position:absolute;left:80px;top:70px;width:300px;height:45px">'
                '<div style="position:absolute;left:75px;top:65px;width:315px;height:55px;z-index:9999;background:transparent"></div>'
            )
            metrics = page.evaluate(JS_GEOMETRY)
            assert "ranking-date" in metrics["blockedControls"], metrics
        finally:
            browser.close()


def test_vertical_scroll_parent_does_not_hide_horizontal_clipping():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.set_content(
                '<section style="position:relative;width:200px;height:100px;overflow-y:auto;overflow-x:hidden">'
                '<div style="height:400px">scrolling content</div>'
                '<button data-testid="tab-ranking" style="position:fixed;left:1000px;top:200px;width:100px;height:45px">Go</button>'
                '</section>'
            )
            metrics = page.evaluate(JS_GEOMETRY)
            assert "tab-ranking" in metrics["unreachableControls"], metrics
        finally:
            browser.close()


def test_truncated_form_label_is_ux_failure_even_with_valid_bounds():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.set_content(
                '<div data-testid="ranking-mode">'
                '<span class="q-field__label" style="display:block;width:26px;'
                'overflow:hidden;white-space:nowrap">ランキング</span></div>'
            )
            metrics = page.evaluate(JS_GEOMETRY)
            assert "ランキング" in metrics["clippedTextLabels"], metrics
        finally:
            browser.close()


def test_transparent_overlay_blocks_visible_text_input():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.set_content(
                '<input data-testid="search-field" style="position:absolute;left:10px;'
                'top:10px;width:200px;height:40px">'
                '<div style="position:fixed;left:0;top:0;width:230px;height:65px;'
                'z-index:99;opacity:0"></div>'
            )
            metrics = page.evaluate(JS_GEOMETRY)
            assert "search-field" in metrics["blockedControls"], metrics
        finally:
            browser.close()


def test_vertical_scroll_cannot_rescue_horizontal_fixed_clip():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.set_content(
                '<div style="height:65px;width:250px;overflow-y:auto">'
                '<div style="height:240px">generated</div>'
                '<button data-testid="offscreen-fixed" style="position:fixed;'
                'left:1000px;top:200px;width:100px;height:45px">Go</button></div>'
            )
            metrics = page.evaluate(JS_GEOMETRY)
            assert "offscreen-fixed" in metrics["unreachableControls"], metrics
        finally:
            browser.close()


def test_same_quasar_field_overlay_must_not_mask_input():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.set_content(
                '<div class="q-field" style="position:relative;width:260px;height:80px">'
                '<input data-testid="ranking-date" style="position:absolute;left:10px;top:10px;width:200px;height:40px">'
                '<div onclick="event.stopPropagation()" style="position:absolute;left:0;top:0;'
                'width:220px;height:60px;z-index:9999;background:transparent"></div>'
                '</div>'
            )
            metrics = page.evaluate(JS_GEOMETRY)
            assert "ranking-date" in metrics["blockedControls"], metrics
        finally:
            browser.close()
