import contextlib
import io
import unittest
from types import SimpleNamespace

from kc.cli import cmd_evolution
from kc.graph import neighborhood, overview, resolve

from tests.support import FakeLLM, KCTestCase


class TestGraph(KCTestCase):
    def setUp(self):
        super().setUp()
        self.compile_doc()

    def test_overview(self):
        o = overview(self.conn)
        self.assertEqual(o["documents"], 1)
        self.assertGreaterEqual(o["entities"], 2)
        self.assertGreaterEqual(o["relations"], 1)
        self.assertGreaterEqual(o["active_claims"], 1)
        self.assertTrue(o["predicates"])

    def test_resolve(self):
        ent = resolve(self.conn, "VENTUNO Q")
        self.assertIsNotNone(ent)
        self.assertEqual(ent["name"], "VENTUNO Q")

    def test_neighborhood(self):
        ent = resolve(self.conn, "VENTUNO Q")
        nb = neighborhood(self.conn, ent)
        self.assertTrue(nb["docs"])
        self.assertTrue(any("VENTUNO" in c[3] for c in nb["claims"]))
        self.assertTrue(nb["in_rels"])  # Qwen --runs on--> VENTUNO Q
        self.assertTrue(any(n == "Qwen" for n, _ in nb["co_mentions"]))


class TestEvolutionCli(KCTestCase):
    def setUp(self):
        super().setUp()
        self.compile_doc()

    def _seed_chain(self):
        rows = self.conn.execute("SELECT id FROM claims ORDER BY id LIMIT 2").fetchall()
        self.conn.execute(
            "UPDATE claims SET status='superseded', superseded_by=?, "
            "superseded_reason='[test] 矛盾' WHERE id=?",
            (rows[1]["id"], rows[0]["id"]),
        )
        self.conn.commit()

    def test_evolution_output(self):
        self._seed_chain()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_evolution(SimpleNamespace(claim_id=None, home=str(self.cfg.home)))
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("知识演化", out)
        self.assertIn("理由", out)

    def test_evolution_single_claim_chain(self):
        self._seed_chain()
        first = self.conn.execute("SELECT id FROM claims ORDER BY id LIMIT 1").fetchone()["id"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_evolution(SimpleNamespace(claim_id=first, home=str(self.cfg.home)))
        self.assertEqual(rc, 0)
        self.assertIn("演化链", buf.getvalue())

    def test_evolution_empty_friendly(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_evolution(SimpleNamespace(claim_id=None, home=str(self.cfg.home)))
        self.assertEqual(rc, 0)
        self.assertIn("暂无知识演化记录", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
