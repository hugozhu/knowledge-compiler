"""Unit tests for kc.note.write_note (shared by CLI and POST /note)."""

import unittest

from kc.note import write_note

from tests.support import KCTestCase


class TestWriteNote(KCTestCase):
    def test_writes_markdown_with_title(self):
        p = write_note(self.cfg, "  内容 X  ", title="标题 Y")
        self.assertTrue(p.exists())
        self.assertTrue(p.name.startswith("note-"))
        self.assertTrue(p.name.endswith(".md"))
        body = p.read_text(encoding="utf-8")
        self.assertIn("# 标题 Y", body)
        self.assertIn("内容 X", body)

    def test_default_title_and_empty_rejected(self):
        p = write_note(self.cfg, "abc")
        self.assertIn("# 随手记", p.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            write_note(self.cfg, "   ")

    def test_never_overwrites_within_same_second(self):
        a = write_note(self.cfg, "one")
        b = write_note(self.cfg, "two")
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists() and b.exists())


if __name__ == "__main__":
    unittest.main()
