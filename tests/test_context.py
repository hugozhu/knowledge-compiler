import unittest

from kc.context import build_context, identify_entities
from kc.memory import add_memory

from tests.support import FakeLLM, KCTestCase


class TestIdentifyEntities(KCTestCase):
    def setUp(self):
        super().setUp()
        self.compile_doc()

    def test_identifies_with_alias(self):
        from kc.entities import add_alias
        from kc.graph import resolve

        ent = resolve(self.conn, "VENTUNO Q")
        add_alias(self.conn, ent["id"], "开发板")
        self.conn.commit()
        hits = identify_entities(self.conn, "在开发板上部署本地知识节点")
        names = [h["name"] for h in hits]
        self.assertIn("VENTUNO Q", names)

    def test_no_match(self):
        self.assertEqual(identify_entities(self.conn, "完全无关的文本量子涨落"), [])


class TestBuildContext(KCTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeLLM()
        self.compile_doc(llm=self.fake)

    def test_pack_structure(self):
        add_memory(self.conn, "当前任务：写 V1.0 测试", kind="task")
        res = build_context(self.conn, self.fake, "如何在 VENTUNO Q 上跑 Qwen 模型")
        pack = res["pack"]
        for section in ("# Task Context", "个人记忆", "关键实体", "相关论断", "原文摘录", "## Sources"):
            self.assertIn(section, pack)
        stats = res["stats"]
        self.assertGreaterEqual(stats["entities"], 1)
        self.assertGreaterEqual(stats["claims"], 1)
        self.assertGreaterEqual(stats["memories"], 1)
        self.assertGreater(stats["chars"], 400)

    def test_budget_respected(self):
        res = build_context(self.conn, self.fake, "知识库检索方案", max_chars=2000)
        self.assertLessEqual(res["stats"]["chars"], 2000 + 600)  # 正文预算 + 尾部 Sources
        self.assertEqual(res["stats"]["max_chars"], 2000)

    def test_no_memory_flag(self):
        add_memory(self.conn, "不该出现", kind="task")
        res = build_context(self.conn, self.fake, "知识库", include_memory=False)
        self.assertEqual(res["stats"]["memories"], 0)
        self.assertNotIn("个人记忆", res["pack"])

    def test_llm_none_degrades(self):
        res = build_context(self.conn, None, "知识库 FTS5XYZ")
        self.assertTrue(res["stats"]["keywords"])  # 确定性分段兜底
        self.assertGreaterEqual(res["stats"]["claims"] + res["stats"]["sources"], 1)

    def test_empty_task_raises(self):
        with self.assertRaises(ValueError):
            build_context(self.conn, self.fake, "  ")

    def test_jsonable(self):
        import json

        res = build_context(self.conn, self.fake, "知识库检索")
        round_trip = json.loads(json.dumps(res, ensure_ascii=False))
        self.assertIn("pack", round_trip)
        self.assertIn("stats", round_trip)


if __name__ == "__main__":
    unittest.main()
