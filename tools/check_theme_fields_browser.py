"""Inspect real field theme inheritance on an already running isolated fixture."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
FIELDS={
    'illustration':('managed-download-view',('managed-download-search','managed-download-direct-id','managed-download-author-id','managed-download-follow-search')),
    'novel':('novel-download-view',('novel-work-id','novel-format')),
    'ugoira':('ugoira-download',('ugoira-work-id','ugoira-author-id','ugoira-format')),
}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True,help='owned isolated fixture loopback URL')
    parser.add_argument('--output',required=True,help='new directory under candidate artifacts')
    parser.add_argument('--width',type=int,default=1280)
    parser.add_argument('--height',type=int,default=720)
    args=parser.parse_args();url=urlsplit(args.url)
    if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost') or url.username or url.password:
        raise ValueError('loopback fixture URL required')
    output=Path(args.output).resolve();allowed=(ROOT/'artifacts').resolve()
    if not output.is_relative_to(allowed) or output==allowed:raise ValueError('output must be inside candidate artifacts')
    output.mkdir(parents=True,exist_ok=False)
    result={'samples':[], 'external_requests':[], 'page_errors':[]}
    with sync_playwright() as play:
        browser=play.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport={'width':args.width,'height':args.height})
        def guard(route):
            req=urlsplit(route.request.url)
            if (req.scheme,req.netloc)!=(url.scheme,url.netloc):
                result['external_requests'].append(req.scheme+'://'+req.netloc);route.abort()
            else:route.continue_()
        context.route('**/*',guard);page=context.new_page();page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
        try:
            page.goto(args.url,wait_until='domcontentloaded')
            page.get_by_test_id('tab-download').click()
            expect(page.get_by_test_id('media-download')).to_be_visible()
            for step,dark in enumerate((True,False,True)):
                expect(page.locator('body')).to_have_class(__import__('re').compile(r'body--dark' if dark else r'body--light'))
                for media,(root_id,ids) in FIELDS.items():
                    page.get_by_test_id('media-download').get_by_test_id('media-select-'+media).click()
                    panel=page.get_by_test_id(root_id);expect(panel).to_be_visible()
                    for field_id in ids:
                        field=panel.get_by_test_id(field_id);expect(field).to_be_visible()
                        sample=field.evaluate("""e => {const x=e.closest('.q-field')||e.querySelector('.q-field');
                          const input=x?.querySelector('input,textarea,.q-field__native');
                          const label=x?.querySelector('.q-field__label');
                          return {dark:!!x?.classList.contains('q-field--dark'),disabled:!!x?.classList.contains('q-field--disabled'),
                            text:input?getComputedStyle(input).color:null,label:label?getComputedStyle(label).color:null,
                            background:getComputedStyle(document.body).backgroundColor,value:input?.value||input?.textContent||''};}""")
                        assert sample['dark']==dark,(media,field_id,sample)
                        assert sample['text'] is not None,(media,field_id,sample)
                        if field_id=='ugoira-format':
                            def check_label():
                                info=field.evaluate("""e => {const x=e.closest('.q-field')||e.querySelector('.q-field');
                                  const label=x?.querySelector('.q-field__label');const r=label?.getBoundingClientRect();
                                  return label?{text:label.textContent.trim(),clientWidth:label.clientWidth,
                                    scrollWidth:label.scrollWidth,visible:!!r&&r.width>0&&r.height>0}:null;}""")
                                assert info is not None and info['text']=='保存形式' and info['visible'],info
                                assert info['scrollWidth']<=info['clientWidth'],info
                                return info
                            sample['format_label']=check_label()
                            for option in ('GIF','MP4','フォルダー'):
                                field.click()
                                item=page.get_by_role('option',name=option,exact=True).last
                                expect(item).to_be_visible();item.click()
                                sample.setdefault('selected_formats',[]).append(option)
                                check_label()
                        result['samples'].append(dict(theme='dark' if dark else 'light',media=media,field=field_id,**sample))
                    page.screenshot(path=str(output/f"{step}-{'dark' if dark else 'light'}-{media}.png"),animations='disabled')
                if step<2:page.get_by_test_id('theme-toggle').click()
            assert not result['external_requests'] and not result['page_errors']
            result['success']=True
        finally:
            (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            context.close();browser.close()

if __name__=='__main__':main()
