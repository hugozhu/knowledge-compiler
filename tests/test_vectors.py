import unittest

from kc.vectors import cosine, embed_ngram, topk


class TestVectors(unittest.TestCase):
    def test_identical_is_one(self):
        a = embed_ngram("个人知识库的核心是把非结构化信息编译成结构化知识")
        self.assertAlmostEqual(cosine(a, a), 1.0, places=3)

    def test_paraphrase_beats_unrelated(self):
        a = embed_ngram("个人知识库的核心是把非结构化信息编译成结构化知识")
        b = embed_ngram("个人知识库把非结构化的信息编译为结构化的知识")
        c = embed_ngram("今天天气不错，适合出去散步")
        self.assertGreater(cosine(a, b), 0.3)
        self.assertLess(cosine(a, c), 0.05)

    def test_empty_is_zero_vector(self):
        z = embed_ngram("   ")
        self.assertAlmostEqual(cosine(z, z), 0.0, places=3)

    def test_topk_ordering(self):
        a = embed_ngram("知识编译器测试")
        cands = [
            (1, embed_ngram("今天天气")),
            (2, a),
            (3, embed_ngram("股票行情")),
        ]
        top = topk(a, cands, 2)
        self.assertEqual(top[0][1], 2)


if __name__ == "__main__":
    unittest.main()
