import unittest

from kc.backlinks import generate_all

from tests.support import FakeLLM, KCTestCase


class TestBacklinks(KCTestCase):
    def setUp(self):
        super().setUp()
        self.compile_doc()

    def test_generate_idempotent(self):
        r1 = generate_all(self.conn, self.cfg)
        self.assertGreaterEqual(r1["pages"], 2)
        self.assertTrue((self.cfg.dir("entities") / "index.md").exists())
        r2 = generate_all(self.conn, self.cfg)
        self.assertEqual(r2["pages"], r1["pages"])  # 整树重建 → 幂等

    def test_page_content(self):
        generate_all(self.conn, self.cfg)
        pages = list(self.cfg.dir("entities").rglob("*.md"))
        ventuno = [p for p in pages if "VENTUNO" in p.name]
        self.assertTrue(ventuno)
        content = ventuno[0].read_text(encoding="utf-8")
        self.assertIn("提及文档", content)
        self.assertIn("相关论断", content)
        self.assertIn("勿手改", content)  # 生成器声明


if __name__ == "__main__":
    unittest.main()
