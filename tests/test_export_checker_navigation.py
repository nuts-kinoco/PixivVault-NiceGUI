"""Checker navigation regressions; no server or browser is started here."""
import re
import pytest
from tools.check_p3_save_exports_browser import visible_job


class Element:
    def __init__(self, page, kind):
        self.page, self.kind = page, kind
    def get_by_test_id(self, key):
        return Element(self.page, key.rsplit('-', 1)[-1])
    def locator(self, query):
        return Element(self.page, re.search(r'data-job-id="([^"]+)"', query).group(1))
    def inner_text(self):
        return str(self.page.number) + ' / ' + str(len(self.page.jobs))
    def is_enabled(self):
        return self.page.number > 1 if self.kind == 'previous' else self.page.number < len(self.page.jobs)
    def click(self):
        assert self.is_enabled()
        self.page.clicks.append(self.kind)
        self.page.number += -1 if self.kind == 'previous' else 1
    def count(self):
        return int(self.kind in self.page.jobs[self.page.number - 1])


class Page:
    def __init__(self, jobs, number=1):
        self.jobs, self.number, self.clicks, self.roots = jobs, number, [], []
    def get_by_test_id(self, key):
        self.roots.append(key)
        return Element(self, key)


class Expected:
    def __init__(self, item): self.item = item
    def to_be_visible(self):
        if not self.item.kind.endswith('-view'): assert self.item.count()
    def to_have_text(self, value):
        actual = self.item.inner_text()
        assert value.fullmatch(actual) if hasattr(value, 'fullmatch') else value == actual
    def not_to_have_text(self, value): assert self.item.inner_text() != value
    def to_be_enabled(self): assert self.item.is_enabled()


@pytest.mark.parametrize('prefix', ['queue', 'failed'])
def test_exact_job_on_second_page_uses_normal_next(prefix):
    page = Page([['first'] * 8, ['accepted']])
    row = visible_job(page, prefix, 'accepted', Expected)
    assert row.kind == 'accepted' and page.number == 2
    assert page.clicks == ['next'] and page.roots == [prefix + '-view']


def test_existing_later_page_is_reset_before_finding_earlier_job():
    page = Page([['accepted'], ['other']], number=2)
    assert visible_job(page, 'queue', 'accepted', Expected).kind == 'accepted'
    assert page.clicks == ['previous'] and page.number == 1


def test_missing_job_does_not_fall_back_to_another_row_or_internal_api():
    page = Page([['first'], ['other']])
    with pytest.raises(AssertionError, match='exact accepted job is absent'):
        visible_job(page, 'failed', 'missing', Expected)
    assert page.clicks == ['next']
