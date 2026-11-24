"""Build/check the CoS start prompt with the original prompt as a byte-exact suffix."""
import argparse
import hashlib
import sys
from pathlib import Path

import check_full_prompt_coverage as coverage

DOCS = Path(__file__).resolve().parents[1] / 'docs/cos'
STEM = 'COS_FULL_START'
DATE = '20261124'
SOURCE = DOCS / f'COS_FULL_USECASE_PROMPT_{DATE}.md'
OUTPUT = DOCS / f'{STEM}_PROMPT_{DATE}.md'
SUPPLEMENT = DOCS / f'{STEM}_CONDITIONS_{DATE}.md'
PASTE_TARGET = 30_000


def check_suffix(joined, original):
    if not original or len(joined) <= len(original) or not joined.endswith(original):
        raise ValueError('Original prompt must be present as a byte-exact suffix')


def paste_sections(original, limit):
    sections = []
    current = b''
    for line in original.splitlines(keepends=True):
        if len(line) > limit:
            raise ValueError('A line exceeds the paste target')
        if current and len(current) + len(line) > limit:
            sections.append(current)
            current = b''
        current += line
    return sections + [current]


def build(supplement, original):
    # The supplement and the original stay separately reproducible.
    prefix = supplement.rstrip() + b'\n\n'
    if len(prefix) + len(original) > PASTE_TARGET:
        sections = paste_sections(original, PASTE_TARGET)
        next_line = 1
        ranges = []
        for section in sections:
            last = next_line + len(section.splitlines()) - 1
            ranges.append(f'{next_line}–{last}行（{len(section):,} bytes）')
            next_line = last + 1
        note = (
            f'> 一括貼り付けが拒否された場合は、試験開始を保留し、先頭の補足を 1 通目に貼る。'
            f'続けて原文を元ファイルの {"、".join(ranges)} の順で貼る。'
            f'各通で受領のみを求め、原文末尾まで届いたことを確認してから開始する。\n\n'
        )
        heading, rest = prefix.split(b'\n', 1)
        prefix = heading + b'\n\n' + note.encode('utf-8') + rest.lstrip(b'\r\n')
    return prefix + original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    original = SOURCE.read_bytes()
    expected = build(SUPPLEMENT.read_bytes(), original)
    if args.write:
        OUTPUT.write_bytes(expected)
    actual = OUTPUT.read_bytes()
    check_suffix(actual, original)
    if actual != expected:
        raise ValueError('Run with --write to refresh the start conditions')
    sys.argv = [sys.argv[0]]
    if coverage.main():
        raise ValueError('Original coverage appendices are stale')
    print(f'original={len(original)} bytes sha256={hashlib.sha256(original).hexdigest()}')
    print(f'start={len(actual)} bytes chars={len(actual.decode("utf-8"))} prefix={len(actual)-len(original)} bytes')
    print('Verified byte-exact suffix and generated coverage (ledger 212 / OLD45 45 / inventory 750).')


if __name__ == '__main__':
    main()
