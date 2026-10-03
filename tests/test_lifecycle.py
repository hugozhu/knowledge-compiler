import unittest

from kc.embed import NgramProvider, backfill
from kc.lifecycle import remove_document

from tests.support import DOC_TEXT, DOC_TEXT_2, FakeLLM, KCTestCase


class TestRemove(KCTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeLLM()
        self.write_inbox("a.md", DOC_TEXT)
        self.s1 = self.compile(self.fake)
        self.write_inbox("b.md", DOC_TEXT_2)
        self.s2 = self.compile(self.fake)
        backfill(self.conn, NgramProvider())
        self.doc1, self.doc2 = self.s1.doc_ids[0], self.s2.doc_ids[0]
        # 构造演化关系：doc1 的 claim 被 doc2 的 claim 取代
        self.c1 = self.claim_ids(self.doc1)[0]
        self.c2 = self.claim_ids(self.doc2)[0]
        self.conn.execute(
            "UPDATE claims SET status='superseded', superseded_by=? WHERE id=?",
            (self.c2, self.c1),
        )
        self.conn.commit()

    def test_cascade_clean(self):
        remove_document(self.conn, self.cfg, self.doc2)
        self.assertEqual(
            self.count("SELECT COUNT(*) FROM documents WHERE id=?", (self.doc2,)), 0
        )
        for table in ("chunks", "claims", "entity_mentions", "relations"):
            self.assertEqual(
                self.count(f"SELECT COUNT(*) FROM {table} WHERE document_id=?", (self.doc2,)),
                0,
                table,
            )
        self.assertEqual(
            self.count("SELECT COUNT(*) FROM compile_log WHERE document_id=?", (self.doc2,)), 0
        )
        self.assertFalse((self.cfg.dir("documents") / f"{self.doc2}.md").exists())

    def test_restores_superseded(self):
        remove_document(self.conn, self.cfg, self.doc2)
        row = self.conn.execute(
            "SELECT status, superseded_by FROM claims WHERE id=?", (self.c1,)
        ).fetchone()
        self.assertEqual(row["status"], "active")
        self.assertIsNone(row["superseded_by"])

    def test_raw_kept_by_default(self):
        self.assertTrue(list(self.cfg.dir("raw").glob(f"{self.doc2}*")))
        remove_document(self.conn, self.cfg, self.doc2)
        self.assertTrue(list(self.cfg.dir("raw").glob(f"{self.doc2}*")))  # immutable 默认保留

    def test_purge_raw(self):
        remove_document(self.conn, self.cfg, self.doc2, purge_raw=True)
        self.assertEqual(list(self.cfg.dir("raw").glob(f"{self.doc2}*")), [])

    def test_re_add_recompiles(self):
        remove_document(self.conn, self.cfg, self.doc2)
        self.write_inbox("b.md", DOC_TEXT_2)
        s3 = self.compile(self.fake)
        self.assertEqual(s3.compiled, 1)
        self.assertEqual(s3.skipped, 0)

    def test_embeddings_swept(self):
        remove_document(self.conn, self.cfg, self.doc2)
        orphans = self.count(
            "SELECT COUNT(*) FROM embeddings WHERE "
            "(scope='chunk' AND ref_id NOT IN (SELECT id FROM chunks)) OR "
            "(scope='claim' AND ref_id NOT IN (SELECT id FROM claims))"
        )
        self.assertEqual(orphans, 0)


if __name__ == "__main__":
    unittest.main()
