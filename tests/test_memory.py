import unittest

from kc.memory import (
    KINDS,
    active_for_context,
    add_memory,
    complete_memory,
    expire_stale,
    list_memories,
)
from kc.retrieve import ask

from tests.support import FakeLLM, KCTestCase


class TestMemory(KCTestCase):
    def test_add_and_list(self):
        mid = add_memory(self.conn, "当前在做 knowledge-compiler V1.0", kind="project")
        rows = list_memories(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], mid)
        self.assertEqual(rows[0]["status"], "active")

    def test_add_validates_kind_and_text(self):
        with self.assertRaises(ValueError):
            add_memory(self.conn, "x", kind="bogus")
        with self.assertRaises(ValueError):
            add_memory(self.conn, "   ")

    def test_done_archives(self):
        mid = add_memory(self.conn, "临时任务", kind="task")
        complete_memory(self.conn, mid)
        self.assertEqual(list_memories(self.conn), [])
        rows = list_memories(self.conn, active_only=False)
        self.assertEqual(rows[0]["status"], "done")
        with self.assertRaises(ValueError):
            complete_memory(self.conn, 99999)

    def test_lazy_expiry(self):
        add_memory(self.conn, "三天后过期", kind="task", expires_days=3)
        add_memory(self.conn, "永不过期", kind="preference")
        self.conn.execute(
            "UPDATE memories SET expires_at='2020-01-01T00:00:00+08:00' WHERE text='三天后过期'"
        )
        self.conn.commit()
        n = expire_stale(self.conn)
        self.assertEqual(n, 1)
        active = [m["text"] for m in list_memories(self.conn)]
        self.assertIn("永不过期", active)
        self.assertNotIn("三天后过期", active)

    def test_active_for_context_limit(self):
        for i in range(8):
            add_memory(self.conn, f"记忆{i}", kind="task")
        self.assertEqual(len(active_for_context(self.conn, limit=5)), 5)

    def test_list_kind_filter_and_default_all_kinds(self):
        add_memory(self.conn, "任务记忆", kind="task")
        add_memory(self.conn, "偏好记忆", kind="preference")
        self.assertEqual(len(list_memories(self.conn)), 2)  # 默认不过滤 kind
        only_pref = list_memories(self.conn, kind="preference")
        self.assertEqual(len(only_pref), 1)
        self.assertEqual(only_pref[0]["text"], "偏好记忆")

    def test_kinds_complete(self):
        self.assertEqual(
            KINDS, {"project", "decision", "preference", "task", "discussion", "goal"}
        )


class TestAskMemory(KCTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeLLM()
        self.compile_doc(llm=self.fake)

    def test_ask_includes_active_memory(self):
        add_memory(self.conn, "正在测试记忆注入", kind="task")
        self.fake.keywords = "FTS5XYZ 知识库"
        res = ask(self.conn, self.fake, "这个文档讲了什么？")
        self.assertIsNotNone(res)
        self.assertEqual(len(res["memories"]), 1)
        self.assertEqual(res["memories"][0]["text"], "正在测试记忆注入")

    def test_ask_excludes_expired_memory(self):
        add_memory(self.conn, "过期记忆", kind="task", expires_days=1)
        self.conn.execute("UPDATE memories SET expires_at='2020-01-01T00:00:00+08:00'")
        self.conn.commit()
        self.fake.keywords = "FTS5XYZ 知识库"
        res = ask(self.conn, self.fake, "这个文档讲了什么？")
        self.assertEqual(res["memories"], [])

    def test_ask_no_memory_flag(self):
        add_memory(self.conn, "不应出现", kind="task")
        self.fake.keywords = "FTS5XYZ 知识库"
        res = ask(self.conn, self.fake, "这个文档讲了什么？", include_memory=False)
        self.assertEqual(res["memories"], [])


if __name__ == "__main__":
    unittest.main()
