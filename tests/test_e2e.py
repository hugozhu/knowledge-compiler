"""End-to-end: V0.1–V0.3 手工验收的自动化复刻（FakeLLM，全离线）。"""

import unittest

from kc.backlinks import generate_all
from kc.dedup import dedup_claims
from kc.digest import generate as digest_generate
from kc.embed import NgramProvider, backfill
from kc.lifecycle import remove_document
from kc.retrieve import ask, search

from tests.support import DOC_TEXT, DOC_TEXT_2, FakeLLM, KCTestCase


class TestEndToEnd(KCTestCase):
    def test_full_loop(self):
        fake = FakeLLM()

        # 1. capture + compile（含 LLM 抽取）
        self.write_inbox("e2e.md", DOC_TEXT)
        stats = self.compile(fake)
        self.assertEqual(stats.compiled, 1)
        self.assertEqual(stats.claims, 3)
        doc_id = stats.doc_ids[0]
        self.assertTrue((self.cfg.dir("raw") / f"{doc_id}__e2e.md").exists())
        self.assertTrue((self.cfg.dir("documents") / f"{doc_id}.md").exists())

        # 2. 增量：同内容再编译 → skip（sha256 去重）
        self.write_inbox("e2e.md", DOC_TEXT)
        s2 = self.compile(fake)
        self.assertEqual((s2.compiled, s2.skipped), (0, 1))

        # 3. --force --all 幂等重编译
        s3 = self.compile(fake, force=True, all_raw=True)
        self.assertEqual(s3.compiled, 1)

        # 4. embed 增量回填（含孤儿清理，总数对账）
        backfill(self.conn, NgramProvider())
        self.assertEqual(
            self.count("SELECT COUNT(*) FROM embeddings"),
            self.count("SELECT COUNT(*) FROM chunks")
            + self.count("SELECT COUNT(*) FROM claims WHERE status='active'"),
        )

        # 5. search：FTS / 两字中文 LIKE 兜底 / hybrid
        self.assertTrue(search(self.conn, "FTS5XYZ", mode="fts"))
        self.assertTrue(search(self.conn, "知识", mode="fts"))
        self.assertTrue(search(self.conn, "知识库", mode="hybrid"))

        # 6. ask 带引用
        fake.keywords = "FTS5XYZ 知识库"
        res = ask(self.conn, fake, "这个文档讲了什么？")
        self.assertIsNotNone(res)
        self.assertIn("[1]", res["answer"])
        self.assertTrue(res["sources"])

        # 7. 第二份同 IR 文档 → 判重全部 duplicate（exact norm）
        self.write_inbox("e2e2.md", DOC_TEXT_2)
        s4 = self.compile(fake)
        doc2 = s4.doc_ids[0]
        backfill(self.conn, NgramProvider())
        dstats = dedup_claims(self.conn, fake, self.claim_ids(doc2))
        self.assertEqual(dstats["duplicate"], 3)

        # 8. backlinks 实体页
        generate_all(self.conn, self.cfg)
        self.assertTrue((self.cfg.dir("entities") / "index.md").exists())

        # 9. digest 日报
        dres = digest_generate(self.cfg, self.conn, fake)
        self.assertFalse(dres["empty"])

        # 10. remove + re-add 重编译
        remove_document(self.conn, self.cfg, doc2, purge_raw=True)
        self.assertEqual(self.count("SELECT COUNT(*) FROM claims WHERE document_id=?", (doc2,)), 0)
        self.write_inbox("e2e2.md", DOC_TEXT_2)
        s5 = self.compile(fake)
        self.assertEqual(s5.compiled, 1)

    def test_image_ocr_path(self):
        p = self.cfg.dir("inbox") / "fake.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")  # 只验证管线，OCR 由替身返回
        stats = self.compile(FakeLLM())
        self.assertEqual(stats.compiled, 1)
        row = self.conn.execute("SELECT content FROM documents LIMIT 1").fetchone()
        self.assertIn("OCR 转录", row["content"])

    def test_no_llm_deterministic_path(self):
        self.write_inbox("a.md", DOC_TEXT)
        stats = self.compile(None)
        self.assertEqual(stats.compiled, 1)
        self.assertEqual(stats.claims, 0)
        self.assertGreaterEqual(self.count("SELECT COUNT(*) FROM chunks"), 1)

    def test_note_feedback_loop(self):
        """kc note → inbox → compile → 知识回流（反馈闭环）。"""
        from kc.context import build_context
        from kc.memory import add_memory, list_memories

        # note（低摩擦随手记）
        note_text = "观察：实体页的自动生成让 backlinks 维护成本为零。"
        (self.cfg.dir("inbox") / "note-20261003-120000.md").write_text(
            f"# 随手记\n\n{note_text}\n", encoding="utf-8"
        )
        stats = self.compile(FakeLLM())
        self.assertEqual(stats.compiled, 1)
        self.assertGreaterEqual(stats.claims, 1)  # 随手记编译成知识

        # memory（动态状态）与知识分离
        add_memory(self.conn, "正在验证 V1.0 闭环", kind="task")
        self.assertEqual(len(list_memories(self.conn)), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM memories"), 1)

        # context 打包含知识 + 记忆
        res = build_context(self.conn, FakeLLM(), "backlinks 维护成本")
        self.assertEqual(res["stats"]["memories"], 1)
        self.assertGreaterEqual(res["stats"]["claims"] + res["stats"]["sources"], 1)


if __name__ == "__main__":
    unittest.main()
