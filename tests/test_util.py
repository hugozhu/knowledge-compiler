import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

from kc.util import detect_lang, extract_json, norm_text, sha256_file


class TestExtractJson(unittest.TestCase):
    def test_fenced(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_surrounding_noise(self):
        self.assertEqual(extract_json('好的，以下是结果：{"a": 1} 希望有帮助'), {"a": 1})

    def test_trailing_comma(self):
        self.assertEqual(extract_json('{"a": [1, 2,]}'), {"a": [1, 2]})

    def test_nested_braces_in_strings(self):
        self.assertEqual(extract_json('{"t": "x{y}z"}'), {"t": "x{y}z"})

    def test_none_on_garbage(self):
        self.assertIsNone(extract_json("没有 JSON"))
        self.assertIsNone(extract_json('{"broken": '))
        self.assertIsNone(extract_json(""))


class TestNorm(unittest.TestCase):
    def test_norm_text(self):
        self.assertEqual(norm_text("  Hello  World "), "helloworld")
        self.assertEqual(norm_text("ＡＢＣ ａｂｃ"), "abcabc")  # NFKC 全角折叠

    def test_detect_lang(self):
        self.assertEqual(detect_lang("知识编译器知识编译器"), "zh")
        self.assertEqual(detect_lang("plain english text"), "en")


class TestSha(unittest.TestCase):
    def test_sha256_file_stable(self):
        with NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"hello")
            path = Path(f.name)
        try:
            self.assertEqual(sha256_file(path), sha256_file(path))
            self.assertEqual(len(sha256_file(path)), 64)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
