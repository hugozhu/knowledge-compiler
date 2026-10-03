import unittest

from kc.entities import add_alias, link_mention, merge_entities, resolve_entity, suggest_groups

from tests.support import FakeLLM, KCTestCase


class TestEntities(KCTestCase):
    def setUp(self):
        super().setUp()
        self.compile_doc()

    def test_resolve_create_and_reuse(self):
        a = resolve_entity(self.conn, "SQLite", "technology")
        b = resolve_entity(self.conn, "sqlite", "other")  # norm 相同 → 复用
        self.assertEqual(a, b)

    def test_resolve_via_alias(self):
        eid = resolve_entity(self.conn, "FTS5", "technology")
        add_alias(self.conn, eid, "全文索引")
        self.conn.commit()
        self.assertEqual(resolve_entity(self.conn, "全文索引"), eid)

    def test_add_alias_collision_raises(self):
        resolve_entity(self.conn, "甲", "other")
        b = resolve_entity(self.conn, "乙", "other")
        with self.assertRaises(ValueError):
            add_alias(self.conn, b, "甲")  # 「甲」是别人的 norm

    def test_merge(self):
        target = resolve_entity(self.conn, "目标实体", "other")
        source = resolve_entity(self.conn, "来源实体", "other")
        doc = self.conn.execute("SELECT id FROM documents LIMIT 1").fetchone()["id"]
        link_mention(self.conn, source, doc)
        self.conn.commit()

        result = merge_entities(self.conn, target, source)
        self.assertEqual(result["absorbed"], "来源实体")
        self.assertEqual(result["doc_count"], 1)
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM entities WHERE id=?", (source,)).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM entity_aliases WHERE alias_norm=?", ("来源实体",)
            ).fetchone()
        )

    def test_suggest_groups(self):
        fake = FakeLLM()
        fake.suggest = '[{"canonical": "Qwen", "merge": ["qwen3-4b"], "reason": "同族模型"}]'
        groups = suggest_groups(fake, ["Qwen", "qwen3-4b", "VENTUNO Q"])
        self.assertEqual(groups[0]["canonical"], "Qwen")
        self.assertIn("qwen3-4b", groups[0]["merge"])


if __name__ == "__main__":
    unittest.main()
