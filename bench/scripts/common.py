"""Shared helpers for the kc benchmark (stdlib only).

Endpoints:
  LOCAL   — qwen-server (NPU)          : qwen3-4b / qwen3-vl-4b
  GATEWAY — LiteLLM/MaaS OpenAI proxy  : deepseek-v4-flash (baseline), qwen3-8-max (judge)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# make `import kc` work regardless of cwd
_HERE = Path(__file__).resolve()
_KC_ROOT = _HERE.parents[2]
sys.path.insert(0, str(_KC_ROOT / "src"))

LOCAL_BASE = os.environ.get("KC_LOCAL_BASE", "http://127.0.0.1:8080/v1")
LOCAL_KEY = os.environ.get("KC_LOCAL_KEY", "sk-local")
GATEWAY_BASE = os.environ.get("KC_GATEWAY_BASE", "http://192.168.3.1:4000/v1")
GATEWAY_KEY = os.environ.get("KC_GATEWAY_KEY", "sk-1234")

FLASH_MODEL = "deepseek-v4-flash"
# qwen3-8-max was trialled as judge but returns HTTP 500 for prompts > ~8 items and
# takes 100s+ per tiny call — unusable for bulk judging. deepseek-v4-pro (≠ baseline,
# ≠ candidate) handles full IR comparisons reliably.
JUDGE_MODEL = "deepseek-v4-pro"
LOCAL_MODEL = "qwen3-4b"

BENCH = _KC_ROOT / "bench"
CASES = BENCH / "cases"
WORK = BENCH / "work"
RESULTS = BENCH / "results"

DOCS = [
    {
        "tag": "A",
        "id": "de43b976f6666a44",
        "name": "178-agent-model-plus-harness",
        "path": Path("/home/arduino/Projects/blog2/content/post/2026/178-agent-model-plus-harness.md"),
    },
    {
        "tag": "B",
        "id": "78d89a53782ce92b",
        "name": "263-loop-engineering",
        "path": Path("/home/arduino/Projects/blog2/content/post/2026/263-loop-engineering.md"),
    },
]


def doc_by_id(doc_id: str) -> dict:
    for d in DOCS:
        if d["id"] == doc_id:
            return d
    raise KeyError(doc_id)


def chat(
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    timeout: int = 600,
) -> dict:
    """One OpenAI-compatible chat call. Returns {content, usage, elapsed, error}."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        elapsed = time.time() - t0
        choice = body["choices"][0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        return {
            "content": content,
            "usage": body.get("usage") or {},
            "elapsed": elapsed,
            "error": None,
            "finish_reason": choice.get("finish_reason"),
            "reasoning_len": len(msg.get("reasoning_content") or ""),
        }
    except Exception as e:  # noqa: BLE001
        return {"content": "", "usage": {}, "elapsed": time.time() - t0, "error": str(e)}


NO_THINK = "\n\n（直接给出最终结果，不要输出思考过程。）"


class RecordingLLM:
    """Thin proxy over kc.llm.LLM that records every HTTP call's timing/usage."""

    def __init__(self, llm, name: str):
        self._llm = llm
        self.name = name
        self.calls: list[dict] = []
        # expose attributes kc may read
        self.model = llm.model
        self.base_url = llm.base_url

    def __getattr__(self, item):
        return getattr(self._llm, item)

    def _post(self, path, payload):
        return self._llm._post(path, payload)

    def chat(self, system, user, model=None, temperature=0.2, max_tokens=768):
        return self._call(system, user, model, temperature, max_tokens)

    def _call(self, system, user, model, temperature, max_tokens):
        import time as _t

        t0 = _t.time()
        try:
            out = self._llm.chat(system, user, model=model, temperature=temperature, max_tokens=max_tokens)
            err = None
        except Exception as e:  # noqa: BLE001
            out, err = "", str(e)
        self.calls.append(
            {
                "system": (system or "")[:24],
                "model": model or self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "elapsed": round(_t.time() - t0, 2),
                "input_chars": len(user or ""),
                "output_chars": len(out or ""),
                "error": err,
            }
        )
        if err:
            raise RuntimeError(err)
        return out


class FlashLLM(RecordingLLM):
    """Adapter: deepseek-v4-flash is a reasoning model.

    kc's prompts assume a non-reasoning 4B (max_tokens 64–768). Handed to flash
    unchanged, the CoT consumes the whole budget and `content` comes back empty.
    Minimal, documented adaptation: floor the output budget and ask for the final
    answer only. Prompts/schema/pipeline are otherwise unchanged.
    """

    MIN_TOKENS = 2048

    def _call(self, system, user, model, temperature, max_tokens):
        return super()._call(
            system,
            (user or "") + NO_THINK,
            model,
            temperature,
            max(int(max_tokens or 0), self.MIN_TOKENS),
        )


def judge(system: str, user: str, max_tokens: int = 16384, temperature: float = 0.0):
    """Call the judge model (qwen3-8-max) and parse a JSON reply. Returns (obj|None, raw_call)."""
    from kc.util import extract_json

    last = None
    for _ in range(2):
        r = chat(
            GATEWAY_BASE,
            GATEWAY_KEY,
            JUDGE_MODEL,
            system,
            user + NO_THINK,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        last = r
        if r["error"]:
            continue
        obj = extract_json(r["content"])
        if obj is not None:
            return obj, r
    return None, last


def as_ids(vals) -> list[int]:
    """Parse judge id lists that may be 1, \"1\", \"G1\", \"C12\" → [1, 12]."""
    import re

    out = []
    for v in vals or []:
        m = re.search(r"\d+", str(v))
        if m:
            out.append(int(m.group()))
    return sorted(set(out))


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def norm(s: str) -> str:
    """Loose normalisation for matching Chinese/English strings."""
    import re

    s = (s or "").lower()
    s = re.sub(r"[\s，。、；：,.;:!?()\[\]{}<>\"'`·—–_\-/\\|]+", "", s)
    return s
