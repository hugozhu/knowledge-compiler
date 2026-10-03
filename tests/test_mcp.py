import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from kc import db as dbmod
from kc.memory import add_memory
from kc.server import make_server

from tests.support import FakeLLM, KCTestCase

ROOT = Path(__file__).resolve().parents[1]


class TestMcpStdio(KCTestCase):
    """真实子进程 `kc mcp` + 线程内 HTTP 服务（FakeLLM）的协议级测试。"""

    def setUp(self):
        super().setUp()
        self.compile_doc()
        add_memory(self.conn, "MCP 测试记忆", kind="task")
        self.conn2 = dbmod.connect(self.cfg.db_path, check_same_thread=False)
        dbmod.init_db(self.conn2)
        self.addCleanup(self.conn2.close)
        self.srv = make_server(self.cfg, self.conn2, FakeLLM(), "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

        self.proc = subprocess.Popen(
            [sys.executable, "-m", "kc", "mcp"],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(ROOT / "src"),
                "KC_BASE_URL": f"http://127.0.0.1:{self.port}",
                "HOME": "/tmp",
            },
        )
        self.addCleanup(self._stop)

    def _stop(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.terminate()

    # ------------------------------------------------------------- driver
    _rid = 0

    def _send(self, method, params=None, notify=False):
        if not notify:
            TestMcpStdio._rid += 1
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        if not notify:
            msg["id"] = TestMcpStdio._rid
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        return json.loads(self.proc.stdout.readline())

    def _call(self, name, args=None):
        return self._send("tools/call", {"name": name, "arguments": args or {}})

    # --------------------------------------------------------------- tests
    def test_handshake_and_tools(self):
        r = self._send(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
        )
        self.assertEqual(r["result"]["serverInfo"]["name"], "kc")
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self._send("notifications/initialized", notify=True)
        r = self._send("tools/list")
        names = {t["name"] for t in r["result"]["tools"]}
        self.assertEqual(
            names, {"context", "search", "ask", "stats", "memory_list", "memory_add", "note"}
        )

    def test_search_tool(self):
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        r = self._call("search", {"query": "FTS5XYZ", "limit": 3})
        text = r["result"]["content"][0]["text"]
        self.assertIn("chunk#", text)
        self.assertNotIn("isError", r["result"])

    def test_context_tool(self):
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        r = self._call("context", {"task": "VENTUNO Q 知识库部署", "max_chars": 2000})
        text = r["result"]["content"][0]["text"]
        self.assertIn("# Task Context", text)
        self.assertIn("预算", text)

    def test_memory_tools(self):
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        r = self._call("memory_list", {})
        self.assertIn("MCP 测试记忆", r["result"]["content"][0]["text"])
        r = self._call("memory_add", {"text": "MCP 写入记忆", "kind": "project"})
        self.assertIn("memory#", r["result"]["content"][0]["text"])
        r = self._call("memory_list", {"kind": "project"})
        self.assertIn("MCP 写入记忆", r["result"]["content"][0]["text"])

    def test_ask_tool(self):
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        r = self._call("ask", {"question": "这个文档讲了什么？"})
        payload = r["result"]
        self.assertNotIn("isError", payload)
        self.assertIn("[1]", payload["content"][0]["text"])

    def test_stats_tool(self):
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        r = self._call("stats", {})
        self.assertIn("documents", r["result"]["content"][0]["text"])

    def test_note_tool_writes_and_compiles(self):
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        r = self._call("note", {"text": "MCP 对话式加知识 NOTEZZQ", "title": "MCP 笔记"})
        payload = r["result"]
        self.assertNotIn("isError", payload)
        self.assertIn("已加入知识库", payload["content"][0]["text"])
        # 编译进库后应可检索到
        r = self._call("search", {"query": "NOTEZZQ"})
        self.assertIn("NOTEZZQ", r["result"]["content"][0]["text"])

    def test_note_tool_inbox_only(self):
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        r = self._call("note", {"text": "仅入 inbox", "compile": False})
        self.assertIn("已写入 inbox", r["result"]["content"][0]["text"])

    def test_note_tool_missing_text_is_error(self):
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        r = self._call("note", {"text": ""})
        self.assertTrue(r["result"]["isError"])

    def test_unknown_method_returns_error(self):
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        r = self._send("bogus/method")
        self.assertEqual(r["error"]["code"], -32601)

    def test_unknown_tool_is_error_not_crash(self):
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        r = self._call("nope", {})
        self.assertTrue(r["result"]["isError"])
        # 服务仍在：紧跟着的正常调用应成功
        r = self._call("stats", {})
        self.assertNotIn("isError", r["result"])

    def test_serve_down_is_friendly_error(self):
        """serve 端口关掉 → 工具返回带启动指引的 isError，进程不崩。"""
        self._send("initialize", {"protocolVersion": "2025-06-18"})
        self.srv.shutdown()  # 关闭 HTTP 服务
        r = self._call("stats", {})
        self.assertTrue(r["result"]["isError"])
        self.assertIn("kc serve", r["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
