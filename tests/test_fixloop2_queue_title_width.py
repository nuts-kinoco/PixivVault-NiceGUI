"""The queue must reserve visible space for a known author name."""
from playwright.sync_api import sync_playwright
from pixivvault_nicegui.ui.theme import CSS


def test_queue_author_is_not_squeezed_out_at_common_window_width():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 800, 'height': 600})
            page.set_content('''<style> .row{display:flex} .column{display:flex;flex-direction:column}
               .q-btn{display:inline-block} ''' + CSS + '''</style>
               <div class="pv-queue-view" style="width:600px">
                 <div class="pv-job-description"><div class="row pv-job-head">
                   <span class="pv-job-title">Synthetic Author</span>
                   <span class="pv-job-state">queued</span>
                   <span class="pv-job-progress-count">0/1</span>
                   <span class="pv-job-id-seat">author 123 · work 456</span>
                   <span class="row pv-job-actions"><button class="q-btn">pause</button>
                     <button class="q-btn">resume</button><button class="q-btn">stop</button>
                     <button class="q-btn">delete</button></span>
                 </div></div></div>''')
            author = page.locator('.pv-job-title')
            assert author.bounding_box()['width'] >= 64
            assert page.locator('.pv-job-actions').bounding_box()['x'] + page.locator('.pv-job-actions').bounding_box()['width'] <= 610
        finally:
            browser.close()
