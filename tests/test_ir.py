import unittest

from kc.ir import MAX_CLAIMS, coerce_ir, extract_ir, merge_irs

from tests.support import FakeLLM


class TestCoerce(unittest.TestCase):
    def test_clamps_and_limits(self):
        raw = {
            "summary": "s",
            "claims": [
                {"text": f"论断{i}", "type": "bogus", "confidence": 5}
                for i in range(MAX_CLAIMS + 10)
            ],
            "entities": [{"name": f"e{i}", "type": "nope"} for i in range(30)],
            "relations": [{"subject": "a", "predicate": "p", "object": "b"}],
            "topics": [f"t{i}" for i in range(20)],
        }
        ir = coerce_ir(raw)
        self.assertEqual(len(ir.claims), MAX_CLAIMS)
        self.assertEqual(ir.claims[0]["type"], "fact")  # 非法类型 → fact
        self.assertEqual(ir.claims[0]["confidence"], 1.0)  # 置信度截断到 [0,1]
        self.assertLessEqual(len(ir.entities), 20)
        self.assertLessEqual(len(ir.topics), 12)

    def test_non_dict_returns_empty(self):
        ir = coerce_ir(["not", "a", "dict"])
        self.assertEqual(ir.claims, [])


class TestMerge(unittest.TestCase):
    def test_dedup_by_norm(self):
        a = coerce_ir(
            {
                "claims": [{"text": "Raw 永不覆盖", "type": "decision", "confidence": 0.9}],
                "entities": [{"name": "Qwen", "type": "product"}],
            }
        )
        b = coerce_ir(
            {
                "claims": [{"text": "raw 永不 覆盖", "type": "fact", "confidence": 0.8}],
                "entities": [{"name": "qwen", "type": "product"}],
            }
        )
        m = merge_irs([a, b])
        self.assertEqual(len(m.claims), 1)
        self.assertEqual(len(m.entities), 1)


class TestExtract(unittest.TestCase):
    def test_ok(self):
        ir = extract_ir(FakeLLM(), "任意文本")
        self.assertTrue(ir.claims)
        self.assertTrue(ir.summary)

    def test_degrades_on_bad_json(self):
        class Bad(FakeLLM):
            def chat(self, system, user, **kw):
                return "这不是 JSON"

        ir = extract_ir(Bad(), "任意文本")
        self.assertEqual(ir.claims, [])
        self.assertIn("extract-failed", ir.summary)


if __name__ == "__main__":
    unittest.main()
