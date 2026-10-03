import unittest

from kc.chunker import chunk_text

ZH = "\n\n".join(
    f"第{i}段。" + "这是关于知识编译器的测试文本，用来验证分块逻辑。" * 8 for i in range(1, 15)
)


class TestChunker(unittest.TestCase):
    def test_sizes_and_coverage(self):
        chunks = chunk_text(ZH)
        self.assertGreaterEqual(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(len(c), 1200)
        joined = "".join(chunks)
        self.assertIn("第1段", joined)
        self.assertIn("第14段", joined)

    def test_empty(self):
        self.assertEqual(chunk_text("  \n\n "), [])

    def test_single_long_paragraph(self):
        text = "长段落测试。" * 500  # 无空行
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 1200)

    def test_english(self):
        text = "The quick brown fox. " * 300 + "\n\n" + "Another paragraph here. " * 50
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)


if __name__ == "__main__":
    unittest.main()
