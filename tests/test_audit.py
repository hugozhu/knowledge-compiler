import unittest

from kc.audit import apply_verdicts, candidate_pairs, judge_pairs
from kc.embed import NgramProvider, backfill

from tests.support import DOC_TEXT_2, FakeLLM, KCTestCase


class TestAudit(KCTestCase):
    def setUp(self):
        super().setUp()
        self.compile_doc()
        backfill(self.conn, NgramProvider())

    def test_candidate_pairs_finds_similar(self):
        self.write_inbox("b.md", DOC_TEXT_2)
        self.compile(FakeLLM())  # 相同 IR → 高相似 claims
        backfill(self.conn, NgramProvider())
        pairs = candidate_pairs(self.conn, threshold=0.3)
        self.assertTrue(pairs)
        self.assertGreaterEqual(pairs[0]["score"], 0.5)

    def test_candidate_threshold_filters(self):
        pairs = candidate_pairs(self.conn, threshold=0.99)
        self.assertEqual(pairs, [])  # 单文档内 claims 不会相似到 0.99

    def test_apply_duplicate(self):
        rows = self.conn.execute("SELECT id FROM claims ORDER BY id LIMIT 2").fetchall()
        a, b = rows[0]["id"], rows[1]["id"]
        pair = {"a": a, "b": b, "score": 0.9, "a_text": "x", "b_text": "y"}
        verdicts = [{"index": 1, "verdict": "duplicate", "reason": "测试重复"}]
        stats = apply_verdicts(self.conn, [pair], verdicts)
        self.assertEqual(stats["duplicate"], 1)
        newer, older = max(a, b), min(a, b)
        row = self.conn.execute(
            "SELECT status, duplicate_of FROM claims WHERE id=?", (newer,)
        ).fetchone()
        self.assertEqual(row["status"], "duplicate")
        self.assertEqual(row["duplicate_of"], older)

    def test_apply_contradiction_supersedes_older(self):
        rows = self.conn.execute("SELECT id FROM claims ORDER BY id LIMIT 2").fetchall()
        a, b = rows[0]["id"], rows[1]["id"]
        older, newer = min(a, b), max(a, b)
        pair = {"a": a, "b": b, "score": 0.6, "a_text": "x", "b_text": "y"}
        verdicts = [{"index": 1, "verdict": "contradiction", "reason": "测试矛盾"}]
        apply_verdicts(self.conn, [pair], verdicts)
        old_row = self.conn.execute(
            "SELECT status, superseded_by, superseded_reason FROM claims WHERE id=?", (older,)
        ).fetchone()
        self.assertEqual(old_row["status"], "superseded")
        self.assertEqual(old_row["superseded_by"], newer)
        self.assertIn("测试矛盾", old_row["superseded_reason"])
        orphan = self.count(
            "SELECT COUNT(*) FROM embeddings WHERE scope='claim' AND ref_id=?", (older,)
        )
        self.assertEqual(orphan, 0)  # 被取代者的向量已下线

    def test_judge_pairs_batching(self):
        fake = FakeLLM()
        pairs = [{"a": 1, "b": 2, "score": 0.5, "a_text": "t1", "b_text": "t2"}] * 5
        verdicts = judge_pairs(fake, pairs, batch=4)  # 2 批
        self.assertEqual(len(verdicts), 5)
        self.assertTrue(all(v["verdict"] == "consistent" for v in verdicts))


if __name__ == "__main__":
    unittest.main()
