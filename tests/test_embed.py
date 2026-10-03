import unittest

from kc.embed import NgramProvider, backfill

from tests.support import FakeLLM, KCTestCase


class TestBackfill(KCTestCase):
    def test_backfill_and_incremental(self):
        self.compile_doc()
        p = NgramProvider()
        s1 = backfill(self.conn, p)
        n_chunks = self.count("SELECT COUNT(*) FROM chunks")
        n_claims = self.count("SELECT COUNT(*) FROM claims WHERE status='active'")
        self.assertEqual(s1["chunks_done"], n_chunks)
        self.assertEqual(s1["claims_done"], n_claims)
        self.assertEqual(self.count("SELECT COUNT(*) FROM embeddings"), n_chunks + n_claims)

        s2 = backfill(self.conn, p)  # 全部跳过（增量）
        self.assertEqual((s2["chunks_done"], s2["claims_done"]), (0, 0))

    def test_force_rebuild(self):
        self.compile_doc()
        p = NgramProvider()
        backfill(self.conn, p)
        s = backfill(self.conn, p, force=True)
        self.assertEqual(s["chunks_done"], self.count("SELECT COUNT(*) FROM chunks"))

    def test_orphan_sweep(self):
        self.compile_doc()
        backfill(self.conn, NgramProvider())
        self.conn.execute("DELETE FROM claims WHERE id=(SELECT MIN(id) FROM claims)")
        self.conn.commit()
        backfill(self.conn, NgramProvider())
        orphans = self.count(
            "SELECT COUNT(*) FROM embeddings "
            "WHERE scope='claim' AND ref_id NOT IN (SELECT id FROM claims)"
        )
        self.assertEqual(orphans, 0)


if __name__ == "__main__":
    unittest.main()
