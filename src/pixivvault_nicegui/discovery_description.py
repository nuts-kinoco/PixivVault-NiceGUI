"""Pure D09/D10 pixiv description text, links and dynamic page layout.

The original Flet discovery_description contract is preserved: real prose,
newlines and visible link labels are retained, but decorative/HTML markup is
discarded. Source text is never inserted as HTML. Only an explicitly
validated HTTPS hyperlink may become clickable in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import ipaddress


EXTERNAL_MARK = "↗"
_PIXIV_HOSTS = ("pixiv.net", "pximg.net", "booth.pm", "pixiv.help")
MAX_DESCRIPTION = 131072


@dataclass(frozen=True, slots=True)
class Run:
    text: str
    url: str | None = None
    external: bool = False


def safe_href(href: str) -> str | None:
    """Whitelist user-clickable HTTPS targets. Never allow browser scripts."""
    if not isinstance(href, str) or not 0 < len(href) <= 4096:
        return None
    if href != href.strip() or any(ord(c) < 33 or ord(c) == 127 for c in href):
        return None
    if href.startswith("//"):
        return None
    if href.lower().startswith(("https://www.pixiv.net/jump.php?", "/jump.php?")):
        destination = href.split("?", 1)[1]
        if destination.lower().startswith("url="):
            destination = destination[4:]
        href = unquote(destination)
    elif href.startswith("/"):
        href = "https://www.pixiv.net" + href
    if any(ord(c) < 33 or ord(c) == 127 for c in href) or "\\" in href:
        return None
    try:
        parsed = urlsplit(href)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.port not in (None, 443) or len(href) > 4096
                or not parsed.netloc or parsed.netloc.endswith(".")
                or any(c in host for c in ("%", "@"))):
            return None
        if host.lower() in {"localhost", "localhost.localdomain"}:
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
    except ValueError:
        return None
    return href


def is_external(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    host = host.lower()
    return not any(host == name or host.endswith("." + name) for name in _PIXIV_HOSTS)


class _DescriptionParser(HTMLParser):
    _BLOCK = frozenset({"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"})
    _HIDDEN = frozenset({"script", "style", "template"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[list[Run]] = [[]]
        self.hidden = 0
        self.anchor: list[str | None] = []

    def _newline(self):
        self.lines.append([])

    def _append(self, text):
        if not text or self.hidden:
            return
        url = self.anchor[-1] if self.anchor else None
        for index, part in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
            if index:
                self._newline()
            if part:
                run = Run(part, url, bool(url and is_external(url)))
                previous = self.lines[-1]
                if previous and (previous[-1].url, previous[-1].external) == (run.url, run.external):
                    last = previous.pop()
                    previous.append(Run(last.text + part, last.url, last.external))
                else:
                    previous.append(run)

    def handle_starttag(self, tag, attrs):
        if tag in self._HIDDEN:
            self.hidden += 1
            return
        if self.hidden:
            return
        if tag == "br":
            self._newline()
        elif tag == "a":
            attributes = dict(attrs)
            self.anchor.append(safe_href(attributes.get("href", "")))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ("br", "img", "hr", "input", "source"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self._HIDDEN:
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag == "a":
            if self.anchor:
                self.anchor.pop()
        elif tag in self._BLOCK:
            self._newline()

    def handle_data(self, data):
        self._append(data)

    def handle_entityref(self, name):
        # HTMLParser(convert_charrefs=True) normally emits this through data.
        from html import unescape
        self._append(unescape("&" + name + ";"))

    def handle_charref(self, name):
        from html import unescape
        self._append(unescape("&#" + name + ";"))


def parse_description(raw: str) -> list[list[Run]]:
    if type(raw) is not str or len(raw) > MAX_DESCRIPTION:
        raise ValueError("invalid description length or type")
    if not raw:
        return []
    parser = _DescriptionParser()
    parser.feed(raw)
    parser.close()
    lines = parser.lines
    # Original contract: collapse 3+ *newlines* to 2, preserve one empty
    # logical line between prose paragraphs and ignore trailing empty lines.
    compact: list[list[Run]] = []
    for line in lines:
        if line or not compact or compact[-1]:
            compact.append(line)
    while compact and not "".join(run.text for run in compact[-1]).strip():
        compact.pop()
    return compact


def is_blank(raw: str) -> bool:
    return not any("".join(run.text for run in line).strip()
                   for line in parse_description(raw))


def text_width(text: str) -> float:
    return sum(0.5 if ord(char) < 0x2E80 else 1.0 for char in text)


def wrap_lines(lines: list[list[Run]], max_units: float) -> list[list[Run]]:
    if max_units <= 0:
        return [list(line) for line in lines]
    displayed: list[list[Run]] = []
    for line in lines:
        current: list[Run] = []
        used = 0.0
        for run in line:
            buffer = ""
            for char in run.text:
                width = text_width(char)
                if used + width > max_units and (buffer or current):
                    if buffer:
                        current.append(Run(buffer, run.url, run.external))
                        buffer = ""
                    displayed.append(current)
                    current, used = [], 0.0
                buffer += char
                used += width
            if buffer:
                current.append(Run(buffer, run.url, run.external))
        displayed.append(current)
    return displayed


def split_pages(lines: list[list[Run]], lines_per_page: int) -> list[list[list[Run]]]:
    if not lines:
        return []
    if lines_per_page <= 0:
        return [lines]
    return [lines[index:index + lines_per_page]
            for index in range(0, len(lines), lines_per_page)]


def layout(raw: str, max_units: float, lines_per_page: int) -> list[list[list[Run]]]:
    return split_pages(wrap_lines(parse_description(raw), max_units), lines_per_page)
