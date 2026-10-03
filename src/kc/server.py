"""Zero-dependency Web API (stdlib http.server) — the node's service surface.

    GET  /health            → {"status","llm"}
    GET  /stats             → library stats
    GET  /search?q=&limit=&scope=&mode=
    POST /ask               {"question","limit"?,"rerank"?}
    POST /context           {"task","max_chars"?,"include_memory"?}
    GET  /memory?all=1&kind=
    POST /memory            {"text","kind"?,"expires_days"?,"source"?}

Auth: KC_API_KEY env → require `Authorization: Bearer <key>` (default: none,
loopback binding). SQLite is opened with check_same_thread=False; writes are
serialized by a lock. LLM failures map to 503.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db as dbmod
from .context import build_context
from .llm import LLM, LLMUnavailable
from .memory import KINDS, add_memory, list_memories
from .retrieve import ask, search


class _Router:
    def __init__(self, cfg, conn, llm: LLM | None):
        self.cfg = cfg
        self.conn = conn
        self.llm = llm
        self.lock = threading.Lock()
        self.api_key = os.environ.get("KC_API_KEY", "")


def make_handler(router: _Router):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kc/1.0"

        # ---------------------------------------------------------- plumbing
        def log_message(self, fmt, *args):  # quiet default access log
            pass

        def _send(self, code: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            if not router.api_key:
                return True
            got = self.headers.get("Authorization", "")
            return got == f"Bearer {router.api_key}"

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}

        # ------------------------------------------------------------- routes
        def do_GET(self):
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/health":
                    ok, info = (True, "no-llm") if router.llm is None else router.llm.health()
                    return self._send(200, {"status": "ok", "llm": ok, "llm_info": info})
                if url.path == "/stats":
                    conn = router.conn
                    payload = {}
                    for label, sql in (
                        ("documents", "SELECT COUNT(*) FROM documents"),
                        ("chunks", "SELECT COUNT(*) FROM chunks"),
                        ("active_claims", "SELECT COUNT(*) FROM claims WHERE status='active'"),
                        ("entities", "SELECT COUNT(*) FROM entities"),
                        ("relations", "SELECT COUNT(*) FROM relations"),
                        ("embeddings", "SELECT COUNT(*) FROM embeddings"),
                        ("active_memories", "SELECT COUNT(*) FROM memories WHERE status='active'"),
                    ):
                        payload[label] = conn.execute(sql).fetchone()[0]
                    return self._send(200, payload)
                if url.path == "/search":
                    query = q.get("q", "").strip()
                    if not query:
                        return self._send(400, {"error": "missing q"})
                    hits = search(
                        router.conn,
                        query,
                        limit=int(q.get("limit", 10)),
                        scope=q.get("scope", "all"),
                        mode=q.get("mode", "hybrid"),
                    )
                    return self._send(200, {"query": query, "hits": hits})
                if url.path == "/memory":
                    rows = list_memories(
                        router.conn,
                        active_only=q.get("all") != "1",
                        kind=q.get("kind"),
                    )
                    return self._send(200, {"memories": [dict(r) for r in rows]})
                return self._send(404, {"error": f"no route {url.path}"})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_POST(self):
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            url = urlparse(self.path)
            body = self._body()
            try:
                if url.path == "/ask":
                    question = str(body.get("question") or "").strip()
                    if not question:
                        return self._send(400, {"error": "missing question"})
                    if router.llm is None:
                        return self._send(503, {"error": "LLM not configured"})
                    result = ask(
                        router.conn,
                        router.llm,
                        question,
                        limit=int(body.get("limit", 6)),
                        rerank_top=int(body.get("limit", 6)) if body.get("rerank") else None,
                        include_memory=body.get("include_memory", True),
                    )
                    if result is None:
                        return self._send(200, {"answer": "", "sources": [], "keywords": []})
                    return self._send(200, result)
                if url.path == "/context":
                    task = str(body.get("task") or "").strip()
                    if not task:
                        return self._send(400, {"error": "missing task"})
                    result = build_context(
                        router.conn,
                        router.llm,
                        task,
                        max_chars=int(body.get("max_chars", 6000)),
                        include_memory=body.get("include_memory", True),
                    )
                    return self._send(200, result)
                if url.path == "/memory":
                    text = str(body.get("text") or "").strip()
                    if not text:
                        return self._send(400, {"error": "missing text"})
                    kind = str(body.get("kind", "task"))
                    if kind not in KINDS:
                        return self._send(400, {"error": f"kind must be one of {sorted(KINDS)}"})
                    with router.lock:
                        mem_id = add_memory(
                            router.conn,
                            text,
                            kind=kind,
                            source=str(body.get("source") or "api"),
                            expires_days=body.get("expires_days"),
                        )
                    return self._send(200, {"id": mem_id, "kind": kind, "text": text})
                return self._send(404, {"error": f"no route {url.path}"})
            except LLMUnavailable as e:
                return self._send(503, {"error": f"llm unavailable: {e}"})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    return Handler


def make_server(cfg, conn, llm: LLM | None, host: str, port: int) -> ThreadingHTTPServer:
    router = _Router(cfg, conn, llm)
    return ThreadingHTTPServer((host, port), make_handler(router))


def serve_forever(cfg, llm: LLM | None, host: str, port: int) -> None:
    conn = dbmod.connect(cfg.db_path, check_same_thread=False)
    dbmod.init_db(conn)
    srv = make_server(cfg, conn, llm, host, port)
    print(f"kc serve → http://{host}:{port}（API key: {'on' if os.environ.get('KC_API_KEY') else 'off'}）")
    print("routes: GET /health /stats /search?q= /memory · POST /ask /context /memory")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n退出 serve")
    finally:
        srv.server_close()
        conn.close()
