import unittest
from pathlib import Path

from kc.digest import _period, collect_period, generate

from tests.support import FakeLLM, KCTestCase


class TestDigest(KCTestCase):
    def setUp(self):
        super().setUp()
        self.compile_doc()

    def test_collect_period(self):
        label, since, until = _period("daily", None)
        data = collect_period(self.conn, since, until)
        self.assertEqual(len(data["docs"]), 1)
        self.assertGreaterEqual(len(data["claims"]), 1)

    def test_generate_writes_report(self):
        res = generate(self.cfg, self.conn, FakeLLM())
        self.assertFalse(res["empty"])
        p = Path(res["path"])
        self.assertTrue(p.exists())
        content = p.read_text(encoding="utf-8")
        self.assertIn("新增文档", content)
        self.assertIn("要点综合", content)
        self.assertIn("论断清单", content)

    def test_empty_period(self):
        res = generate(self.cfg, self.conn, FakeLLM(), date="2020-01-01")
        self.assertTrue(res["empty"])

    def test_weekly_label_format(self):
        label, since, until = _period("weekly", "2026-10-03")
        self.assertRegex(label, r"^\d{4}-W\d{2}$")
        self.assertLess(since, until)

    def test_compile_flag_copies_to_inbox(self):
        res = generate(self.cfg, self.conn, FakeLLM(), compile_flag=True)
        self.assertFalse(res["empty"])
        self.assertTrue((self.cfg.dir("inbox") / f"daily-{res['label']}.md").exists())


if __name__ == "__main__":
    unittest.main()
