"""Focused checks for the CoS handoff tools."""
import unittest
from pathlib import Path

from cos_build_start_prompt import check_suffix, paste_sections
from cos_evidence_reuse import verdict


class StartPromptTest(unittest.TestCase):
    def test_requires_a_byte_exact_suffix(self):
        original = b'original\r\n\r\n'
        check_suffix(b'prefix\n' + original, original)
        with self.assertRaises(ValueError):
            check_suffix(b'prefix\n' + original.replace(b'\r\n', b'\n'), original)
        with self.assertRaises(ValueError):
            check_suffix(b'prefix\n' + original + b'\n', original)

    def test_paste_sections_leave_original_whole(self):
        original = b'body\n## marker\nmore\n'
        sections = paste_sections(original, 12)
        self.assertEqual(b''.join(sections), original)
        self.assertGreater(len(sections), 1)


class ReuseTest(unittest.TestCase):
    def test_evidence_requires_result_candidate_and_source(self):
        self.assertEqual(verdict(True, 'abc', {'src/a.py'}, set()), '適合（再利用）')
        self.assertEqual(verdict(True, 'abc', {'src/a.py'}, {'src/a.py'}), '要再試験')
        for result, candidate, paths in ((False, 'abc', {'src/a.py'}),
                                         (True, '', {'src/a.py'}), (True, 'abc', set())):
            self.assertEqual(verdict(result, candidate, paths, set()), '根拠未確定')


if __name__ == '__main__':
    unittest.main()
