import unittest

from kc.audit import apply_verdicts  # 四态批量落库在 audit 模块
from kc.dedup import dedup_claims, find_candidates
from kc.embed import NgramProvider, backfill

from tests.support import DOC_TEXT, DOC_TEXT_2, FakeLLM, KCTestCase


class TestDedup(KCTestCase):
    def _compile_two(self, second_ir=None):
        fake = FakeLLM()
        self.write_inbox("a.md", DOC_TEXT)
        s1 = self.compile(fake)
        if second_ir is not None:
            fake.extract = second_ir
        self.write_inbox("b.md", DOC_TEXT_2)
        s2 = self.compile(fake)
        backfill(self.conn, NgramProvider())
        return s1.doc_ids[0], s2.doc_ids[0]

    def test_exact_norm_duplicate(self):
        doc1, doc2 = self._compile_two()  # 相同 IR → 同 norm claims
        ids2 = self.claim_ids(doc2)
        stats = dedup_claims(self.conn, None, ids2)  # llm=None：纯确定性路径
        self.assertEqual(stats["duplicate"], len(ids2))
        for cid in ids2:
            row = self.conn.execute(
                "SELECT status, duplicate_of FROM claims WHERE id=?", (cid,)
            ).fetchone()
            self.assertEqual(row["status"], "duplicate")
            self.assertIsNotNone(row["duplicate_of"])

    def test_judge_new_default(self):
        other = {
            "summary": "s",
            "topics": [],
            "entities": [{"name": "Qwen", "type": "product"}],
            "claims": [{"text": "完全不同的新论断内容甲乙丙", "type": "fact", "confidence": 0.7}],
            "relations": [],
        }
        doc1, doc2 = self._compile_two(second_ir=other)
        ids2 = self.claim_ids(doc2)
        stats = dedup_claims(self.conn, FakeLLM(), ids2)
        self.assertEqual(stats["new"], len(ids2))
        for cid in ids2:
            row = self.conn.execute("SELECT status FROM claims WHERE id=?", (cid,)).fetchone()
            self.assertEqual(row["status"], "active")

    def test_contradiction_forms_chain_via_dedup(self):
        contradicting = {
            "summary": "s",
            "topics": [],
            "entities": [],
            "claims": [{"text": "Raw 原始资料可以被随意覆盖修改", "type": "opinion", "confidence": 0.8}],
            "relations": [],
        }
        doc1, doc2 = self._compile_two(second_ir=contradicting)
        ids2 = self.claim_ids(doc2)
        fake = FakeLLM()
        fake.judge = [{"verdict": "contradiction", "target": 1, "reason": "立场相反"}]
        stats = dedup_claims(self.conn, fake, ids2)
        self.assertEqual(stats["contradiction"], 1)

        old_row = self.conn.execute(
            "SELECT status, superseded_by FROM claims WHERE text=?", ("Raw 原始资料永不覆盖",)
        ).fetchone()
        self.assertEqual(old_row["status"], "superseded")
        self.assertEqual(old_row["superseded_by"], ids2[0])

    def test_find_candidates_similarity(self):
        doc1, doc2 = self._compile_two()
        cid = self.claim_ids(doc2)[0]
        text = self.conn.execute("SELECT text FROM claims WHERE id=?", (cid,)).fetchone()["text"]
        cands = find_candidates(self.conn, text, exclude_ids={cid})
        self.assertTrue(cands)
        self.assertGreaterEqual(cands[0]["score"], 0.5)  # 同文本 → 高相似


if __name__ == "__main__":
    unittest.main()
