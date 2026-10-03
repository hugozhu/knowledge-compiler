import json
import threading
import unittest
import urllib.error
import urllib.request

from kc import db as dbmod
from kc.memory import add_memory
from kc.server import make_server

from tests.support import FakeLLM, KCTestCase


class TestServer(KCTestCase):
    def setUp(self):
        super().setUp()
        self.compile_doc()
        add_memory(self.conn, "服务测试记忆", kind="task")
        self.conn2 = dbmod.connect(self.cfg.db_path, check_same_thread=False)
        dbmod.init_db(self.conn2)
        self.addCleanup(self.conn2.close)
        self.srv = make_server(self.cfg, self.conn2, FakeLLM(), "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

    # ------------------------------------------------------------- helpers
    def get(self, path: str):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def post(self, path: str, payload: dict, headers: dict | None = None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    # --------------------------------------------------------------- tests
    def test_health(self):
        out = self.get("/health")
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["llm"])

    def test_stats(self):
        out = self.get("/stats")
        self.assertEqual(out["documents"], 1)
        self.assertEqual(out["active_memories"], 1)

    def test_search(self):
        out = self.get("/search?q=FTS5XYZ")
        self.assertTrue(out["hits"])
        self.assertEqual(out["query"], "FTS5XYZ")

    def test_search_missing_q(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/search")
        self.assertEqual(cm.exception.code, 400)

    def test_ask(self):
        out = self.post("/ask", {"question": "这个文档讲了什么？"})
        self.assertIn("answer", out)
        self.assertIn("[1]", out["answer"])
        self.assertTrue(out["sources"])

    def test_context(self):
        out = self.post("/context", {"task": "如何在 VENTUNO Q 上跑 Qwen"})
        self.assertIn("pack", out)
        self.assertIn("# Task Context", out["pack"])
        self.assertGreaterEqual(out["stats"]["entities"], 1)

    def test_memory_get_and_post(self):
        out = self.get("/memory")
        self.assertEqual(len(out["memories"]), 1)
        created = self.post("/memory", {"text": "API 写入的记忆", "kind": "task"})
        self.assertIn("id", created)
        out = self.get("/memory")
        self.assertEqual(len(out["memories"]), 2)

    def test_memory_bad_kind(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/memory", {"text": "x", "kind": "bogus"})
        self.assertEqual(cm.exception.code, 400)

    def test_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/nope")
        self.assertEqual(cm.exception.code, 404)


class TestServerAuth(unittest.TestCase):
    def test_bearer_required(self):
        import os
        import tempfile
        from pathlib import Path

        from kc.config import Config

        tmp = tempfile.TemporaryDirectory(prefix="kc-auth-")
        self.addCleanup(tmp.cleanup)
        cfg = Config(home=Path(tmp.name), llm_base_url="http://x/v1", llm_api_key="",
                     llm_model="f", llm_vlm_model="f")
        cfg.ensure_dirs()
        conn = dbmod.connect(cfg.db_path, check_same_thread=False)
        dbmod.init_db(conn)
        self.addCleanup(conn.close)

        os.environ["KC_API_KEY"] = "secret-key-1"
        self.addCleanup(os.environ.pop, "KC_API_KEY", None)
        srv = make_server(cfg, conn, FakeLLM(), "127.0.0.1", 0)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)

        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=10)
        self.assertEqual(cm.exception.code, 401)

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/stats", headers={"Authorization": "Bearer secret-key-1"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(json.loads(r.read().decode())["documents"], 0)


if __name__ == "__main__":
    unittest.main()
