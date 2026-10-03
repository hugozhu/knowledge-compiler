"""MCP stdio adapter: expose kc serve's REST API as Model Context Protocol tools.

OpenCode (or any MCP client) launches this via `kc mcp`; every tool call is
proxied to the HTTP API (default http://127.0.0.1:8300), so a running
`kc serve` stays the single source of truth for knowledge + memory.

Transport: stdio, JSON-RPC 2.0, newline-delimited (classic initialize
handshake — matches OpenCode's default `legacy` protocol mode).

Nothing but JSON-RPC ever goes to stdout; diagnostics go to stderr.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8300"
SERVER_NAME = "kc"
SERVER_VERSION = "1.0.0"
SLOW_TIMEOUT = 360  # ask/context involve NPU LLM calls (~1-2 min)
FAST_TIMEOUT = 30

TOOLS = [
    {
        "name": "context",
        "description": (
            "为任务构建知识上下文包：关键词 → 实体 → 相关论断 → 原文摘录 → 个人记忆，"
            "在字符预算内组装成带 Sources 的 Markdown。开始研究/执行任务前的推荐入口。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "任务描述（自然语言）"},
                "max_chars": {"type": "integer", "default": 6000, "description": "上下文包字符预算"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "search",
        "description": "在个人知识库中检索论断与原文片段（hybrid：FTS+LIKE+向量+实体，秒回、无 LLM）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "scope": {"type": "string", "enum": ["all", "claims", "chunks"], "default": "all"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask",
        "description": "基于知识库问答（本地 NPU 模型作答，带来源引用；较慢，约 1-2 分钟）",
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "stats",
        "description": "知识库统计（文档/论断/实体/向量/记忆数量）",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_list",
        "description": "列出活跃的个人记忆（动态状态：进行中项目/偏好/决策，非知识）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["project", "decision", "preference", "task", "discussion", "goal"],
                },
            },
        },
    },
    {
        "name": "memory_add",
        "description": "记录一条个人记忆（动态状态，可过期；不进入知识库）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["project", "decision", "preference", "task", "discussion", "goal"],
                    "default": "task",
                },
                "expires_days": {"type": "number", "description": "N 天后自动过期"},
            },
            "required": ["text"],
        },
    },
]

INSTRUCTIONS = (
    "个人知识库 knowledge-compiler（VENTUNO Q 本地节点）。"
    "研究类任务先用 context 取上下文包；search 秒回适合精确检索；"
    "ask 走本地模型较慢（约 1-2 分钟），仅在需要综合回答时用；"
    "memory_* 维护用户的动态个人状态。所有知识均带来源可追溯。"
)


class ToolError(RuntimeError):
    pass


def _base_url() -> str:
    return os.environ.get("KC_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str:
    return os.environ.get("KC_API_KEY", "")


def _request(method: str, path: str, payload: dict | None = None, timeout: int = FAST_TIMEOUT) -> dict:
    url = _base_url() + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    if _api_key():
        req.add_header("Authorization", f"Bearer {_api_key()}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise ToolError(f"HTTP {e.code} {path}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise ToolError(
            f"无法连接 {_base_url()}（{e}）。请先启动知识库服务："
            "cd ~/Projects/knowledge-compiler && nohup ./kc serve --port 8300 >/dev/null 2>&1 &"
        ) from e


# ---------------------------------------------------------------- formatting
def _fmt_search(out: dict) -> str:
    lines: list[str] = []
    for h in out.get("hits", []):
        if h.get("kind") == "claim":
            lines.append(f"[claim#{h['id']}] ({h.get('type')}|conf {h.get('confidence')}) {h['text']}")
            lines.append(f"    ↳ {h.get('title')} ({h.get('document_id')})")
        else:
            snippet = (h.get("text") or "").replace("\n", " ")[:200]
            lines.append(f"[chunk#{h['id']}] {snippet}")
            lines.append(f"    ↳ {h.get('title')} ({h.get('document_id')})")
    return "\n".join(lines) if lines else "无命中"


def _fmt_sources(sources: list) -> str:
    lines = []
    for i, s in enumerate(sources or [], 1):
        label = s.get("title") or s.get("document_id")
        lines.append(f"  [{i}] {s.get('kind')}#{s.get('id')}｜{label}｜{s.get('document_id')}")
    return "\n".join(lines)


def _fmt_memories(rows: list) -> str:
    lines = []
    for m in rows or []:
        exp = f"  [至 {m['expires_at'][:10]}]" if m.get("expires_at") else ""
        lines.append(f"#{m['id']} [{m.get('kind')}] {m['text']}{exp}")
    return "\n".join(lines) if lines else "（无活跃记忆）"


# ---------------------------------------------------------------- dispatch
def dispatch(name: str, args: dict) -> str:
    if name == "context":
        task = str(args.get("task") or "").strip()
        if not task:
            raise ToolError("参数 task 不能为空")
        payload = {"task": task}
        if args.get("max_chars"):
            payload["max_chars"] = int(args["max_chars"])
        out = _request("POST", "/context", payload, timeout=SLOW_TIMEOUT)
        s = out.get("stats", {})
        return (
            out.get("pack", "")
            + f"\n\n（预算 {s.get('chars')}/{s.get('max_chars')} 字符 · 实体 {s.get('entities')} · "
            f"论断 {s.get('claims')} · 记忆 {s.get('memories')} · 来源 {s.get('sources')}）"
        )

    if name == "search":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("参数 query 不能为空")
        q = {"q": query, "limit": str(int(args.get("limit", 10)))}
        if args.get("scope"):
            q["scope"] = str(args["scope"])
        out = _request("GET", "/search?" + urllib.parse.urlencode(q), timeout=FAST_TIMEOUT)
        return _fmt_search(out)

    if name == "ask":
        question = str(args.get("question") or "").strip()
        if not question:
            raise ToolError("参数 question 不能为空")
        out = _request("POST", "/ask", {"question": question}, timeout=SLOW_TIMEOUT)
        answer = out.get("answer") or "（知识库中没有相关内容）"
        sources = _fmt_sources(out.get("sources"))
        kws = " ".join(out.get("keywords") or [])
        head = f"（检索关键词：{kws}）\n\n" if kws else ""
        return f"{head}{answer}\n\nSources:\n{sources}" if sources else f"{head}{answer}"

    if name == "stats":
        out = _request("GET", "/stats", timeout=FAST_TIMEOUT)
        return "\n".join(f"{k}: {v}" for k, v in out.items())

    if name == "memory_list":
        path = "/memory"
        if args.get("kind"):
            path += "?" + urllib.parse.urlencode({"kind": str(args["kind"])})
        out = _request("GET", path, timeout=FAST_TIMEOUT)
        return _fmt_memories(out.get("memories"))

    if name == "memory_add":
        text = str(args.get("text") or "").strip()
        if not text:
            raise ToolError("参数 text 不能为空")
        payload = {"text": text, "kind": str(args.get("kind") or "task")}
        if args.get("expires_days") is not None:
            payload["expires_days"] = args["expires_days"]
        out = _request("POST", "/memory", payload, timeout=FAST_TIMEOUT)
        return f"✓ memory#{out.get('id')} [{out.get('kind')}] {out.get('text')}"

    raise ToolError(f"未知工具 {name!r}")


# ------------------------------------------------------------------ protocol
def _initialize_result(params: dict) -> dict:
    return {
        "protocolVersion": params.get("protocolVersion") or "2025-06-18",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": INSTRUCTIONS,
    }


def _call_tool(params: dict) -> dict:
    try:
        text = dispatch(str(params.get("name") or ""), params.get("arguments") or {})
        return {"content": [{"type": "text", "text": text}]}
    except ToolError as e:
        return {"content": [{"type": "text", "text": f"kc 工具调用失败：{e}"}], "isError": True}


def serve_stdio() -> int:
    for raw in iter(sys.stdin.readline, ""):
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue  # 非法帧 → 忽略（不应答），协议流不中断
        if not isinstance(msg, dict) or "id" not in msg:
            continue  # notification（如 notifications/initialized）→ 无应答
        mid, method = msg["id"], msg.get("method", "")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                result = _initialize_result(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = _call_tool(params)
            elif method == "prompts/list":
                result = {"prompts": []}
            elif method == "resources/list":
                result = {"resources": []}
            else:
                result = None
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "error": {"code": -32601, "message": f"unknown method {method!r}"},
                    }
                )
                continue
        except Exception as e:  # noqa: BLE001 — 单次请求异常不得杀死服务
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"},
                }
            )
            continue
        _write({"jsonrpc": "2.0", "id": mid, "result": result})
    return 0


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()
