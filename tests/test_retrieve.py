import unittest

from kc.retrieve import ask, fallback_patterns, llm_keywords, rerank, search

from tests.support import FakeLLM, KCTestCase


class TestSearch(KCTestCase):
    def setUp(self):
        super().setUp()
        self.compile_doc()

    def test_fts_ascii_token(self):
        hits = search(self.conn, "FTS5XYZ", limit=5, mode="fts")
        self.assertTrue(hits)
        self.assertTrue(any("FTS5XYZ" in h["text"] for h in hits))

    def test_two_char_cjk_like_fallback(self):
        hits = search(self.conn, "知识", limit=5, mode="fts")  # 2 字 → trigram 不可用，走 LIKE
        self.assertTrue(hits)

    def test_hybrid_returns_hits(self):
        hits = search(self.conn, "知识库", limit=5, mode="hybrid")
        self.assertTrue(hits)

    def test_scope_claims_only(self):
        hits = search(self.conn, "Raw", limit=5, scope="claims", mode="fts")
        self.assertTrue(hits)
        self.assertTrue(all(h["kind"] == "claim" for h in hits))

    def test_entity_hop_via_alias(self):
        from kc.entities import add_alias
        from kc.graph import resolve

        ent = resolve(self.conn, "VENTUNO Q")
        add_alias(self.conn, ent["id"], "开发板")
        self.conn.commit()
        # 不做 embedding：词法通道（FTS/LIKE）全部 miss，只有 entity-hop 能命中
        hits = search(self.conn, "开发板性能怎么样", limit=10, mode="hybrid")
        self.assertTrue(hits)
        doc_ids = {r["id"] for r in self.conn.execute("SELECT id FROM documents")}
        self.assertTrue(any(h["document_id"] in doc_ids for h in hits))


class TestAsk(KCTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeLLM()
        self.compile_doc(llm=self.fake)

    def test_ask_answer_with_citations(self):
        self.fake.keywords = "FTS5XYZ 知识库"
        res = ask(self.conn, self.fake, "这个文档讲了什么？")
        self.assertIsNotNone(res)
        self.assertIn("[1]", res["answer"])
        self.assertTrue(res["sources"])
        self.assertTrue(res["sources"][0]["id"])

    def test_keywords_extraction(self):
        kws = llm_keywords(FakeLLM(keywords="FTS5 知识库"), "任意问题")
        self.assertIn("FTS5", kws)
        self.assertIn("知识库", kws)

    def test_fallback_patterns(self):
        pats = fallback_patterns("这份文档认为个人知识库的核心是什么？")
        self.assertTrue(pats)
        self.assertTrue(all(2 <= len(p) <= 12 for p in pats))

    def test_rerank_reverses_order(self):
        hits = search(self.conn, "知识库", limit=4, mode="fts")
        self.assertGreaterEqual(len(hits), 2)
        out = rerank(FakeLLM(), "知识库", hits, top_n=len(hits))
        self.assertEqual([h["id"] for h in out], [h["id"] for h in reversed(hits)])


if __name__ == "__main__":
    unittest.main()
