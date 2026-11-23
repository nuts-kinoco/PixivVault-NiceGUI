"""Offline cache fixture and first-open measurements for p6_responsiveness."""

import hashlib
import io
import json
import sqlite3
from collections import Counter
from datetime import datetime


BOOKMARKS, RECOMMENDATIONS = 20, 12


def thumbnail(name):
    return f'https://i.pximg.net/d6-fixture/{name}'


class ImageFixture:
    def __init__(self, files):
        self.files = files
        self.denials = 0
        self.reads = 0

    def get_image_bytes(self, url):
        try:
            file = self.files[url]
        except KeyError:
            self.denials += 1
            raise ValueError('image outside the offline fixture') from None
        self.reads += 1
        return file.read_bytes()


def seed_caches(database, works, authors, learning):
    names = dict(authors)
    selected = works[:BOOKMARKS]
    database.save_bookmark_remote_cache([
        dict(id=wid, title=title, user_id=uid, user_name=names[uid],
             tags=json.loads(tags), thumb_url=thumbnail(f'{wid}-1.png'), rest_type='show',
             create_date='2026-10-23' if wid == selected[0][0] else '2026-10-22',
             is_novel=False, x_restrict=0)
        for wid, title, _, tags, uid, *_ in selected
    ])
    stamp = datetime.now().isoformat()
    database.set_setting('bookmark_cache_fetched_at', stamp)
    database.set_setting('recommend_section_order',
                         '["superlike","trend_added","trend","certain","boosted"]')
    with database.conn as conn:
        conn.executemany('UPDATE works SET is_bookmarked=1 WHERE work_id=?',
                         [(row[0],) for row in selected])
        for row in learning[-RECOMMENDATIONS:]:
            wid = row['work_id']
            url = thumbnail(f'{wid}.png')
            conn.execute('INSERT INTO discovery_swipe_history'
                         '(work_id,verdict,judged_at,source,author_id,tags) VALUES (?,\'superLike\',?,\'ranking\',?,\'[]\')',
                         (wid, f'{stamp}+{wid}', row['author_id']))
            conn.execute('INSERT INTO superlike_info_cache VALUES (?,?,?,?,?,?)',
                         (wid, row['title'], row['author_id'], row['author_name'], url, stamp))


def install_recommendation_reader(base):
    from pixivvault_nicegui.services import ApplicationServices

    reader = ImageFixture({thumbnail(file.name): file for file in (base / 'learning').glob('*.png')})
    initialize = ApplicationServices.__init__

    def with_reader(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        self._vault_images = reader  # image-only fixture; no account or acquisition client

    ApplicationServices.__init__ = with_reader
    return reader


def verify_copies(base, database_paths, image_roots):
    def fingerprint(root):
        digest = hashlib.sha256()
        count = 0
        for image in sorted(root.rglob('*.png')):
            count += 1
            digest.update(image.relative_to(root).as_posix().encode() + b'\0')
            digest.update(hashlib.sha256(image.read_bytes()).digest())
        return {'files': count, 'sha256': digest.hexdigest()}

    tables = ('works', 'following_users', 'discovery_swipe_history',
              'superlike_info_cache', 'bookmark_remote_cache')
    db_files = [hashlib.sha256(path.read_bytes()).hexdigest() for path in database_paths]
    if len(set(db_files)) != 1:
        raise RuntimeError('database copies differ')
    with sqlite3.connect(database_paths[0]) as db:
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise RuntimeError('fixture database failed integrity_check')
        rows = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in tables}
    library = fingerprint(image_roots[0])
    learning = fingerprint(base / 'learning')
    for root in image_roots[1:]:
        if fingerprint(root) != library:
            raise RuntimeError('library image copies differ')
    if len(database_paths) > 1 and fingerprint(base / 'original' / 'learning') != learning:
        raise RuntimeError('candidate image copies differ')
    return {'data': 'synthetic offline control', 'db_sha256': db_files[0], 'db_rows': rows,
            'library_images': library, 'candidate_images': learning}


def measure_first_open(page, base, original, repetitions, cases, elapsed, color):
    """Use each new page's first visit. The application servers stay running."""
    from PIL import Image

    if not {'recommend', 'bookmarks'}.intersection(cases):
        return
    context, url = page.context, page.url
    failures = {}
    targets = {
        'recommend': ('Bench Learn 800059', color(800059), 'おすすめ', 'tab-recommend'),
        'bookmarks': ('Bench Work 100000', color(100000), 'マイブックマーク',
                      'library-subview-bookmarks'),
    }
    for _ in range(repetitions):
        trial = context.new_page()
        trial.set_default_timeout(6000)
        try:
            trial.goto(url, timeout=12000)
            if original:
                trial.locator('flt-semantics-placeholder').evaluate('e => e.click()', timeout=12000)
            else:
                trial.wait_for_function('window.did_handshake === true', timeout=12000)
            for operation, (title, expected, label, testid) in targets.items():
                if operation not in cases or operation in failures:
                    continue
                try:
                    if operation == 'bookmarks':
                        tab = (trial.get_by_role('button', name='ライブラリ', exact=True).first
                               if original else trial.get_by_test_id('tab-library'))
                        tab.click()
                    button = (trial.get_by_role('button', name=label, exact=True).first
                              if original else trial.get_by_test_id(testid))
                    button.wait_for()
                    if original and operation == 'bookmarks':
                        card = trial.locator(f'flt-semantics[role=group][aria-label*="{title}"]').first
                    elif original:
                        card = trial.locator('flt-semantics[role=button]').filter(has_text=title).last
                    else:
                        card = trial.get_by_text(title, exact=True).first
                    if card.is_visible():
                        raise RuntimeError('candidate card already visible before click')

                    def ready():
                        card.wait_for()
                        for _ in range(35):
                            result = trial.screenshot(timeout=3000, animations='disabled')
                            with Image.open(io.BytesIO(result)) as image:
                                frame = image.convert('RGB').resize((320, 200), Image.Resampling.NEAREST)
                                if Counter(frame.getdata())[expected] >= 30:
                                    return
                        raise RuntimeError('expected cache thumbnail did not paint')

                    elapsed(operation, button.click, ready, active_page=trial)
                except Exception as exc:
                    failures[operation] = f'{type(exc).__name__}: {str(exc).splitlines()[0]}'
                    trial.screenshot(path=base /
                                     f'{"original" if original else "nice"}-{operation}-failed.png')
                    dump = trial.evaluate("""() => [...document.querySelectorAll('flt-semantics')]
                        .map(e => [e.getAttribute('role'), e.getAttribute('aria-label'), e.textContent.slice(0, 100)])""")
                    if not original:
                        dump = trial.locator('body').inner_text()
                    (base /
                     f'{"original" if original else "nice"}-{operation}-failed.txt').write_text(
                        json.dumps(dump, ensure_ascii=False), encoding='utf-8')
        finally:
            trial.close()
        page.bring_to_front()
        if set(cases).intersection(targets) <= failures.keys():
            break
    return failures
