"""X02/X04 synthetic Edge checks with a dedicated owned local fixture.

Use --serve in a separately owned CoS terminal, and --check from another.
The fixture never opens Pixiv, a user DB, or any managed save root.
"""
import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import urlsplit


ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=ROOT/'artifacts/p3-dedup-mode-20261028'


def source_fixture():
    from PIL import Image
    from pixivvault_nicegui.duplicate_inspection import (
        DuplicateGroup, DuplicatePage, DuplicateScan, QuarantinePlan, validate_selection,
    )

    def image(color):
        buffer=BytesIO()
        Image.new('RGB',(32,24),color).save(buffer,format='JPEG')
        return base64.b64encode(buffer.getvalue()).decode('ascii')

    def item(work,author,*,width=80):
        return DuplicatePage(str(work),1,author,author+' name',
                             'synthetic/receipt','a'*64,'b'*64,
                             width,50,width*100,image('red' if author=='9' else 'blue'))

    near=DuplicateScan('fixture', 'a'*64, (
        DuplicateGroup('g9','9','Known Artist',(item(201,'9',width=100),item(202,'9',width=70)),
                       ((0,1,0),)),
        DuplicateGroup('g10','10','Other Artist',tuple(item(300+i,'10',width=100-i*3)
                                                       for i in range(6)),((0,1,2),)),
    ),8,(),())
    undecidable=({
        'ambiguous_group':'original-file-ledger-1','reason':'duplicate title',
        'candidates':(
            {'work_id':'901','title':'同じ題名','author_name':'Known Artist','user_id':'9',
             'pages':({'page':1,'store_path':'synthetic','entry_name':'one.jpg'},)},
            {'work_id':'902','title':'同じ題名','author_name':'Known Artist','user_id':'9',
             'pages':({'page':1,'store_path':'synthetic','entry_name':'one.jpg'},)},
        ),
    },)

    class Source:
        undecidable_available=True
        def __init__(self):self.plan_count=0
        def undecidable_groups(self):return undecidable
        def scan(self):return near
        def plan(self,scan,selections,query=''):
            if scan is not near:raise ValueError('stale synthetic scan')
            items,remaining=validate_selection(scan,selections,query)
            if not items:raise ValueError('nothing selected')
            self.plan_count+=1
            return QuarantinePlan(near.scan_id,near.snapshot_sha256,items,remaining)
    return Source()


def serve(port):
    if not 1024 <= port <= 65535 or port==8089:
        raise ValueError('explicit owned high localhost port required')
    from nicegui import ui
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    from pixivvault_nicegui.ui.theme import CSS
    source=source_fixture()

    @ui.page('/')
    def page():
        ui.add_css(CSS)
        dark=ui.dark_mode(True)
        with ui.column().classes('pv-shell'):
            with ui.row().classes('pv-header'):
                ui.label('合成 重複整理 X02/X04').classes('pv-title')
                ui.button('明暗を切替',on_click=dark.toggle).props('data-testid=fixture-theme')
            with ui.element('main').classes('pv-content'):
                view=DuplicateView(source)
                view.set_active(True)

    ui.run(host='127.0.0.1',port=port,show=False,reload=False,title='X02 X04 synthetic fixture')


def check(url,output,*,width,height,light):
    from playwright.sync_api import expect, sync_playwright
    parsed=urlsplit(url)
    if (parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost')
            or not parsed.port or parsed.port==8089 or parsed.username or parsed.password):
        raise ValueError('owned fixture loopback URL required')
    output=output.absolute()
    if output==ARTIFACTS or not output.is_relative_to(ARTIFACTS) or output.exists():
        raise ValueError('use a fresh candidate-local evidence directory')
    output.mkdir(parents=True)
    result=dict(success=False,viewport=[width,height],light=light,
                external_requests=[],page_errors=[],console_errors=[],checks=[])
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport={'width':width,'height':height})
        def guard(route):
            dest=urlsplit(route.request.url)
            if (dest.scheme,dest.netloc)!=(parsed.scheme,parsed.netloc):
                result['external_requests'].append(dest.scheme+'://'+dest.netloc)
                route.abort()
            else:route.continue_()
        context.route('**/*',guard)
        page=context.new_page()
        page.on('pageerror',lambda error:result['page_errors'].append(str(error)))
        page.on('console',lambda message:result['console_errors'].append(message.text)
                if message.type=='error' else None)
        try:
            page.goto(url,wait_until='domcontentloaded')
            if light:page.get_by_test_id('fixture-theme').click()
            undecidable=page.get_by_test_id('duplicate-mode-undecidable')
            near=page.get_by_test_id('duplicate-mode-near')
            expect(undecidable).to_have_attribute('aria-pressed','true')
            expect(undecidable).to_contain_text('（1）')
            expect(near).to_be_disabled()
            expect(near).to_contain_text('（–）')
            expect(page.get_by_test_id('duplicate-undecidable-rows')).to_contain_text('候補: 901 / 902')
            expect(page.get_by_test_id('duplicate-near-panel')).not_to_be_visible()
            page.screenshot(path=str(output/'undecidable-initial.png'))
            result['checks'].append('original undecidable mode and near-not-scanned state')

            page.get_by_test_id('duplicate-scan').click()
            expect(near).to_be_enabled()
            expect(near).to_contain_text('（2）')
            near.click()
            expect(near).to_have_attribute('aria-pressed','true')
            expect(page.get_by_test_id('duplicate-undecidable-panel')).not_to_be_visible()
            thumbs=page.get_by_test_id('duplicate-thumbnail')
            expect(thumbs).to_have_count(7)  # six-member group shows first five
            assert all(abs(size-144)<=1 for size in thumbs.evaluate_all(
                "nodes => nodes.map(node=>node.getBoundingClientRect().width)"))
            expect(page.get_by_test_id('duplicate-size-medium')).to_have_attribute('aria-pressed','true')
            # A q-img's box can be sized before its real image has faded in;
            # wait on mounted pixels, not a fixed delay, before the final capture.
            page.wait_for_function("""() => {
                const tile=document.querySelector('[data-testid="duplicate-thumbnail"]');
                const image=tile?.querySelector('img');
                return !!image && image.complete && image.naturalWidth>0 &&
                    Number(getComputedStyle(image).opacity)>=0.99;
            }""")
            result['checks'].append('two near groups, five-item display cap, medium=144px')

            # The user's choice remains checked while the thumbnail nodes are resized.
            checks=page.get_by_role('checkbox',name='隔離候補')
            expect(checks).to_have_count(7)
            checks.first.click()
            expect(page.get_by_test_id('duplicate-selection')).to_have_text('1枚選択中')
            identities=thumbs.evaluate_all("nodes=>nodes.map(node=>node.id)")
            for size,pixels in (('small',88),('large',200),('medium',144)):
                page.get_by_test_id('duplicate-size-'+size).click()
                expect(page.get_by_test_id('duplicate-size-'+size)).to_have_attribute('aria-pressed','true')
                assert thumbs.evaluate_all("nodes=>nodes.map(node=>node.id)")==identities
                assert all(abs(value-pixels)<=1 for value in thumbs.evaluate_all(
                    "nodes=>nodes.map(node=>node.getBoundingClientRect().width)"))
                expect(checks.first).to_be_checked()
                expect(page.get_by_test_id('duplicate-selection')).to_have_text('1枚選択中')
            result['checks'].append('three size controls preserve checkbox, geometry and DOM identity')

            undecidable.click()
            expect(page.get_by_test_id('duplicate-selection')).not_to_be_visible()
            near.click()
            expect(checks.first).to_be_checked()
            expect(page.get_by_test_id('duplicate-selection')).to_have_text('1枚選択中')
            result['checks'].append('mode round-trip preserves near selection')

            # Selecting every member of a two-image group must undo the last click.
            checks.nth(1).click()
            expect(page.get_by_test_id('duplicate-selection')).to_have_text('1枚選択中')
            expect(page.get_by_test_id('duplicate-status')).to_contain_text('1枚以上')
            assert checks.first.is_checked() and not checks.nth(1).is_checked()
            page.get_by_test_id('duplicate-select-low').click()
            expect(page.get_by_test_id('duplicate-selection')).to_have_text('5枚選択中')
            expect(page.get_by_test_id('duplicate-move')).to_be_enabled()
            page.get_by_test_id('duplicate-plan').click()
            expect(page.get_by_test_id('duplicate-selection')).to_contain_text('ファイルは移動していません')
            # Current X07 opens confirmation only for a mutation-authorized
            # managed plan. This older X02 fixture remains read-only: it has no
            # executor and QuarantinePlan.mutation_allowed remains False.
            expect(page.get_by_test_id('duplicate-move')).to_be_enabled()
            page.get_by_test_id('duplicate-move').click()
            expect(page.get_by_test_id('duplicate-status')).to_contain_text('再スキャンして確認してください')
            expect(page.get_by_test_id('duplicate-confirm-dialog')).not_to_be_visible()
            result['checks'].append('one-image safety, visible-only bulk select, read-only preview and unauthorized-plan confirmation rejection')

            undecidable.click()
            search=page.get_by_test_id('duplicate-search')
            search.fill('902')
            expect(page.get_by_test_id('duplicate-undecidable-rows')).to_contain_text('候補: 901 / 902')
            search.fill('absent')
            expect(page.get_by_test_id('duplicate-undecidable-empty')).to_be_visible()
            search.fill('')
            near.click()
            expect(page.get_by_test_id('duplicate-selection')).to_have_text('0枚選択中')
            result['checks'].append('undecidable work-id search and original filtered-selection disposal')

            # The search remounts the thumbnail components; capture after their
            # fade-in, so a valid image isn't photographed as an empty rectangle.
            page.wait_for_function("""() => {
                const tile=document.querySelector('[data-testid="duplicate-thumbnail"]');
                const image=tile?.querySelector('img');
                return !!image && image.complete && image.naturalWidth>0 &&
                    Number(getComputedStyle(image).opacity)>=0.99;
            }""")
            page.screenshot(path=str(output/'final.png'))
            assert not result['external_requests'] and not result['page_errors'] and not result['console_errors']
            result['success']=True
        except Exception as error:
            result['error']=f'{type(error).__name__}: {error}'
        finally:
            context.close();browser.close()
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if not result['success']:
        raise AssertionError(result['error'])
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--port',type=int)
    parser.add_argument('--url')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--width',type=int,default=1280)
    parser.add_argument('--height',type=int,default=720)
    parser.add_argument('--light',action='store_true')
    args=parser.parse_args()
    if args.serve==args.check:parser.error('select exactly --serve or --check')
    if args.serve:
        if args.port is None:parser.error('--port required')
        serve(args.port)
    else:
        if args.url is None or args.output is None:parser.error('--url and --output required')
        print(json.dumps(check(args.url,args.output,width=args.width,height=args.height,
                               light=args.light),ensure_ascii=False))
