import sqlite3
import tempfile
import unittest
from pathlib import Path

from kc import db as dbmod


class TestDb(unittest.TestCase):
    def test_init_creates_all_tables(self):
        with tempfile.TemporaryDirectory() as td:
            conn = dbmod.connect(Path(td) / "t.db")
            dbmod.init_db(conn)
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for t in (
                "documents", "chunks", "claims", "entities", "entity_mentions",
                "relations", "compile_log", "chunks_fts", "claims_fts",
                "embeddings", "entity_aliases",
            ):
                self.assertIn(t, names)
            conn.close()

    def test_init_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.db"
            for _ in range(2):
                conn = dbmod.connect(p)
                dbmod.init_db(conn)
                conn.close()

    def test_migration_v0_to_latest(self):
        """模拟 V0.1 旧库（仅 SCHEMA、user_version=0）→ 自动迁移到最新版且数据不丢。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "old.db"
            c1 = sqlite3.connect(p)
            c1.executescript(dbmod.SCHEMA)
            c1.execute("PRAGMA user_version=0")
            c1.execute("INSERT INTO documents(id,title,sha256) VALUES('d1','旧文档','x')")
            c1.execute("INSERT INTO claims(document_id,text,norm) VALUES('d1','旧论断','旧论断')")
            c1.commit()
            c1.close()

            c2 = dbmod.connect(p)
            dbmod.init_db(c2)
            latest = max(dbmod.MIGRATIONS)
            self.assertEqual(c2.execute("PRAGMA user_version").fetchone()[0], latest)
            cols = {r["name"] for r in c2.execute("PRAGMA table_info(claims)")}
            self.assertTrue({"duplicate_of", "superseded_reason", "dedup_checked"} <= cols)
            tables = {r[0] for r in c2.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("memories", tables)
            self.assertEqual(c2.execute("SELECT COUNT(*) FROM claims").fetchone()[0], 1)
            self.assertEqual(c2.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            c2.close()


if __name__ == "__main__":
    unittest.main()
