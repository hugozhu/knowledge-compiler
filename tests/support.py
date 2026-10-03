"""Shared fixtures: deterministic offline FakeLLM + KCTestCase base.

FakeLLM routes on system-prompt keywords so every LLM touchpoint in kc
(extraction / keywords / ask / rerank / dedup judge / audit / suggest /
digest synthesis) works without the NPU service.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from kc import db as dbmod
from kc.compiler import Compiler
from kc.config import Config

DOC_TEXT = """# 测试文档

个人知识库（knowledge base）的核心是把非结构化信息编译成结构化知识。
FTS5XYZ 是用于检索验证的标志词。Raw 原始资料永不覆盖，保证可追溯。
"""

DOC_TEXT_2 = DOC_TEXT + "\n第二篇文档的补充内容，主题略有不同。\n"

DEFAULT_IR = {
    "summary": "测试文档摘要：个人知识编译器与本地知识节点。",
    "topics": ["知识库", "测试"],
    "entities": [
        {"name": "Qwen", "type": "product"},
        {"name": "VENTUNO Q", "type": "place"},
    ],
    "claims": [
        {"text": "VENTUNO Q 承担本地知识节点的职责", "type": "fact", "confidence": 0.9},
        {"text": "个人知识库的核心是编译非结构化信息", "type": "fact", "confidence": 0.9},
        {"text": "Raw 原始资料永不覆盖", "type": "decision", "confidence": 0.95},
    ],
    "relations": [
        {"subject": "Qwen", "predicate": "runs on", "object": "VENTUNO Q"},
    ],
}


class FakeLLM:
    """Deterministic offline LLM double routed by system-prompt keywords."""

    def __init__(
        self,
        extract=None,
        keywords="知识库 测试",
        answer="这是基于资料的测试回答 [1]。",
        ocr_text="OCR 转录：Personal Knowledge Compiler 测试文本，包含知识库与 FTS5XYZ 关键词。",
    ):
        self.model = "fake-llm"
        self.vlm_model = "fake-vlm"
        self.calls = []
        self.extract = extract if extract is not None else json.loads(json.dumps(DEFAULT_IR))
        self.keywords = keywords
        self.answer = answer
        self.ocr_text = ocr_text
        self.judge = []   # dedup verdict queue: {"verdict","target","reason"}
        self.audit = []   # audit verdict queue: {"verdict","reason"}
        self.suggest = "[]"
        self.synthesis = "- 测试综合要点：本期以知识编译与本地检索为主。"

    def health(self):
        return True, "fake-llm, fake-vlm"

    def ocr(self, image_path, model=None, max_tokens=1024):
        return self.ocr_text

    def chat(self, system, user, model=None, temperature=0.2, max_tokens=768):
        self.calls.append((system, user))
        if "语义抽取模块" in system:
            ir = self.extract
            if isinstance(self.extract, list):
                ir = self.extract.pop(0) if self.extract else DEFAULT_IR
            return json.dumps(ir, ensure_ascii=False)
        if "检索查询优化器" in system:
            return self.keywords
        if "问答助手" in system:
            return self.answer
        if "相关性评估器" in system:  # rerank: ascending scores → reversed order
            ids = [int(m) for m in re.findall(r"^\((\d+)\)", user, re.M)]
            return json.dumps([{"index": i, "score": i} for i in ids])
        if "判重助手" in system:  # dedup judge
            n = len(re.findall(r"第\d+组", user))
            out = []
            for i in range(1, n + 1):
                v = dict(self.judge.pop(0)) if self.judge else {"verdict": "new", "target": 0, "reason": ""}
                v["index"] = i
                out.append(v)
            return json.dumps(out, ensure_ascii=False)
        if "一致性审查员" in system:  # audit judge
            n = len(re.findall(r"第\d+对", user))
            out = []
            for i in range(1, n + 1):
                v = dict(self.audit.pop(0)) if self.audit else {"verdict": "consistent", "reason": ""}
                v["index"] = i
                out.append(v)
            return json.dumps(out, ensure_ascii=False)
        if "实体消歧助手" in system:
            return self.suggest
        if "报告撰写助手" in system:
            return self.synthesis
        raise AssertionError(f"FakeLLM got unexpected system prompt: {system!r}")


class KCTestCase(unittest.TestCase):
    """Temp KC_HOME + initialized DB per test; helpers for inbox/compile."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kc-test-")
        self.addCleanup(tmp.cleanup)
        self.cfg = Config(
            home=Path(tmp.name),
            llm_base_url="http://127.0.0.1:9/v1",
            llm_api_key="",
            llm_model="fake-llm",
            llm_vlm_model="fake-vlm",
        )
        self.cfg.ensure_dirs()
        self.conn = dbmod.connect(self.cfg.db_path)
        dbmod.init_db(self.conn)
        self.addCleanup(self.conn.close)

    # -- helpers ----------------------------------------------------------
    def write_inbox(self, name: str, text: str) -> Path:
        p = self.cfg.dir("inbox") / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def compile(self, llm=None, **kw):
        kw.setdefault("use_llm", llm is not None)
        return Compiler(self.cfg, self.conn, llm).compile_pending(**kw)

    def compile_doc(self, name="a.md", text=DOC_TEXT, llm=None):
        """write_inbox + compile + 断言确实编译成功（防空转）。"""
        self.write_inbox(name, text)
        stats = self.compile(llm or FakeLLM())
        assert stats.compiled == 1, f"compile 失败: {stats.errors}"
        return stats

    def count(self, sql: str, params=()) -> int:
        return self.conn.execute(sql, params).fetchone()[0]

    def claim_ids(self, doc_id: str) -> list:
        return [
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM claims WHERE document_id=? ORDER BY id", (doc_id,)
            )
        ]
