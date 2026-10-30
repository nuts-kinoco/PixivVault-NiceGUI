"""Offline M4 presentation adapter over the frozen public M3 interfaces.

All methods that fetch/verify/read/enqueue run off the UI loop. No live client,
private M3 journal, database, operation map or filesystem scanning is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import json
import re
from xml.etree import ElementTree
import zipfile

from .managed_downloads import _read_bounded
from .managed_novel_jobs import FixtureNovelSource, PinnedNovelSource, NovelJobIntent
from .media_parity_policy import NovelDocument, parse_novel_detail
from .novel_storage import SavedNovel

FORMATS = {"EPUB": "EPUBのみ (推奨)", "TXT": "TXTのみ", "BOTH": "EPUBとTXT両方"}
# Explicit synthetic identity pins; never infer accepted authorship from an
# arbitrary response's userId. These are fixture IDs, not real Pixiv lookups.
FIXTURE_AUTHORS = {"123": "91", "124": "92"}
_MAX_OUTPUT = 32 * 1024 * 1024
_MAX_CHAPTER = 16 * 1024 * 1024


class NovelPresentationError(ValueError):
    def __init__(self):
        super().__init__("novel presentation operation failed")


def build_novel_ui_fixture() -> FixtureNovelSource:
    return FixtureNovelSource({
        "123": {"id": "123", "userId": "91", "userName": "架空の作者 A",
                "title": "架空小説：青い栞", "content": "これは小説保存の合成データです。\n次の行も本文です。\n<script> と & は文字として表示します。",
                "seriesNavData": None, "textEmbeddedImages": None},
        "124": {"id": "124", "userId": "92", "userName": "架空の作者 B",
                "title": "架空小説：窓辺の一日", "content": "窓を開けると、風がページをめくりました。\n" + "これは合成の長文です。\n" * 900,
                "seriesNavData": None, "textEmbeddedImages": {}},
    })


def _identifier(value):
    if type(value) is not str or re.fullmatch(r"[1-9][0-9]{0,19}", value) is None:
        raise NovelPresentationError()
    return value


def _fingerprint(document, author_name):
    value = [document.work_id, document.author_id, document.title,
             document.raw_content, document.series_id, author_name]
    return hashlib.sha256(json.dumps(value, ensure_ascii=True,
                                    separators=(",", ":")).encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class NovelSelection:
    intent: NovelJobIntent = field(repr=False)
    title: str = field(repr=False)
    author: str = field(repr=False)
    excerpt: str = field(repr=False)
    fingerprint: str = field(repr=False)
    saved: SavedNovel | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class NovelLibraryEntry:
    work_id: str = field(repr=False)
    output_format: str
    title: str = field(repr=False)
    author: str = field(repr=False)
    saved: SavedNovel = field(repr=False)


class NovelSavePresentation:
    def __init__(self, services):
        if services.novel_jobs is None or type(services.novel_fixture_source) not in (FixtureNovelSource, PinnedNovelSource):
            raise NovelPresentationError()
        self.services = services
        self.authors = (services.novel_fixture_source.authors if type(services.novel_fixture_source) is PinnedNovelSource else FIXTURE_AUTHORS)

    @property
    def practical(self):
        return type(self.services.novel_fixture_source) is PinnedNovelSource

    def select(self, work_id, author_id):
        if self.practical:
            self.services.novel_fixture_source.select(work_id, author_id)
            self.authors = self.services.novel_fixture_source.authors

    def _document(self, work_id) -> tuple[NovelDocument, str]:
        _identifier(work_id)
        author_id = self.authors.get(work_id)
        if author_id is None:
            raise NovelPresentationError()
        body = self.services.novel_fixture_source.fetch(work_id)
        document = parse_novel_detail(body, work_id=work_id, expected_author_id=author_id)
        author = body.get("userName")
        if (type(author) is not str or not 0 < len(author) <= 256
                or any(ord(c) < 32 or ord(c) == 127 for c in author)
                or (not self.practical and body.get("textEmbeddedImages"))):
            raise NovelPresentationError()
        return document, author

    def preview(self, work_id, output_format, chapter_ids=(), series_title=None):
        if self.services.closing or type(output_format) is not str or output_format not in FORMATS:
            raise NovelPresentationError()
        document, author = self._document(work_id)
        if chapter_ids:
            for wid in chapter_ids:
                self.select(wid, document.author_id)
        if chapter_ids:
            # Validate every selected chapter before enabling queue acceptance.
            docs = [self._document(wid)[0] for wid in chapter_ids]
            from .novel_series_export import render_novel_series_epub
            render_novel_series_epub(docs, author_name=author, series_title=series_title)
        saved = self.services.novel_jobs.current(work_id, output_format, chapter_ids=chapter_ids)
        intent = NovelJobIntent(work_id, document.author_id, output_format,
                                saved.relative_receipt if saved else None,
                                saved.receipt_sha256 if saved else None, tuple(chapter_ids), series_title)
        intent.spec()
        return NovelSelection(intent, series_title or document.title, author, document.raw_content[:512],
                              _fingerprint(document, author), saved)

    def enqueue(self, selection, operation_id):
        if type(selection) is not NovelSelection or selection.saved is not None:
            raise NovelPresentationError()
        document, author = self._document(selection.intent.work_id)
        if _fingerprint(document, author) != selection.fingerprint:
            raise NovelPresentationError()
        # Do not rebase a stale preview onto a new generation. M3 owns the
        # operation ledger and generation CAS; another client may win first.
        return self.services.enqueue_novel_export(selection.intent, operation_id)

    def library(self):
        if self.services.closing:
            raise NovelPresentationError()
        entries = []
        for saved in self.services.novel_jobs.saved_entries():
            title, author = self._saved_metadata(saved)
            entries.append(NovelLibraryEntry(saved.work_id, saved.output_format, title, author, saved))
        return tuple(entries)

    def _saved_metadata(self, saved):
        if saved.metadata is not None:
            return saved.metadata["title"], saved.metadata["author"]
        folder = (self.services.novel_jobs.store.root / saved.relative_receipt).parent
        if "novel.txt" in saved.output_names:
            text = _read_bounded(folder / "novel.txt", _MAX_OUTPUT).decode("utf-8", errors="strict")
            rows = text.split("\n", 2)
            if len(rows) < 3:
                raise NovelPresentationError()
            title, author = rows[:2]
        else:
            data = _read_bounded(folder / "novel.epub", _MAX_OUTPUT)
            with zipfile.ZipFile(BytesIO(data)) as archive:
                member = archive.getinfo("OEBPS/content.opf")
                if member.file_size > 64 * 1024:
                    raise NovelPresentationError()
                with archive.open(member) as stream:
                    raw = stream.read(64 * 1024 + 1)
                if len(raw) > 64 * 1024 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                    raise NovelPresentationError()
                tree = ElementTree.fromstring(raw)
                title = tree.findtext(".//{http://purl.org/dc/elements/1.1/}title")
                author = tree.findtext(".//{http://purl.org/dc/elements/1.1/}creator")
        if (type(title) is not str or not 0 < len(title) <= 256
                or type(author) is not str or not 0 < len(author) <= 256):
            raise NovelPresentationError()
        return title, author

    def read_saved(self, entry):
        if type(entry) is not NovelLibraryEntry:
            raise NovelPresentationError()
        chapters = (entry.saved.metadata or {}).get('chapters', [])
        current = self.services.novel_jobs.current(entry.work_id, entry.output_format,
                                                  chapter_ids=chapters if len(chapters) > 1 else ())
        if current != entry.saved:
            raise NovelPresentationError()
        # Use only exact names returned by verified M3 storage. No URL, path or
        # ZIP member name is supplied by a browser event or source document.
        folder = self.services.novel_jobs.store.root / current.relative_receipt
        folder = folder.parent
        if current.metadata is not None:
            from .novel_saved_reader import read_bundle
            text, _ = read_bundle(folder, current)
        elif "novel.txt" in current.output_names:
            text = _read_bounded(folder / "novel.txt", _MAX_OUTPUT).decode("utf-8", errors="strict")
        elif current.output_names == ("novel.epub",):
            data = _read_bounded(folder / "novel.epub", _MAX_OUTPUT)
            with zipfile.ZipFile(BytesIO(data)) as archive:
                chapter = archive.getinfo("OEBPS/Text/chapter.xhtml")
                if chapter.file_size > _MAX_CHAPTER:
                    raise NovelPresentationError()
                with archive.open(chapter) as stream:
                    raw = stream.read(_MAX_CHAPTER + 1)
                if len(raw) > _MAX_CHAPTER or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                    raise NovelPresentationError()
                tree = ElementTree.fromstring(raw)
                text = "\n".join("".join(node.itertext()) for node in
                                 tree.findall(".//{http://www.w3.org/1999/xhtml}p"))
        else:
            raise NovelPresentationError()
        # Detect a pointer change while the off-loop read was running.
        if self.services.novel_jobs.current(entry.work_id, entry.output_format, chapter_ids=chapters if len(chapters) > 1 else ()) != entry.saved:
            raise NovelPresentationError()
        return text

    def read_images(self, entry):
        self.read_saved(entry)  # verify the active generation before local asset reads
        from .novel_saved_reader import read_bundle
        folder = (self.services.novel_jobs.store.root / entry.saved.relative_receipt).parent
        _, images = read_bundle(folder, entry.saved) if entry.saved.metadata else ('', ())
        self.read_saved(entry)  # reject a generation change during the read
        return images