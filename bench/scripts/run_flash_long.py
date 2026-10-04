#!/usr/bin/env python3
"""C3 (Oracle): deepseek-v4-flash extracts a whole document in one long-context call.

Same IR schema as kc, but no 2000-char batching and no 8-claim cap — this is the
quality ceiling / gold standard used to score C1 (qwen3-4b) and C2 (flash, same kc pipeline).

Usage: python3 bench/scripts/run_flash_long.py
Writes: bench/cases/gold_ir_<docid>.json, bench/results/c3_flash_long.json
"""

from __future__ import annotations

import sys

from common import (  # noqa: E402
    CASES,
    DOCS,
    FLASH_MODEL,
    GATEWAY_BASE,
    GATEWAY_KEY,
    RESULTS,
    chat,
    write_json,
)
from kc.ir import SYSTEM_EXTRACT, coerce_ir, ir_to_dict  # noqa: E402
from kc.parsers import parse_file  # noqa: E402
from kc.util import extract_json  # noqa: E402

GOLD_PROMPT = """阅读下面这篇完整文档，尽可能完整地抽取结构化知识。只输出一个 JSON 对象，字段：
{{
  "summary": "3-5 句话概括全文核心",
  "topics": ["主题词"],
  "entities": [{{"name": "实体名", "type": "person|org|project|product|technology|place|other"}}],
  "claims": [{{"text": "一句可独立理解的重要论断", "type": "fact|opinion|hypothesis|decision|observation|prediction", "confidence": 0.0到1.0}}],
  "relations": [{{"subject": "实体", "predicate": "关系", "object": "实体"}}]
}}
要求：claims 覆盖全文所有重要论断，具体、可独立理解，最多 40 条；entities 最多 20 个；
relations 最多 30 条；没有内容的字段用空数组。

文本：
\"\"\"
{text}
\"\"\""""


def main() -> int:
    out = {"model": FLASH_MODEL, "endpoint": GATEWAY_BASE, "docs": []}
    for d in DOCS:
        parsed = parse_file(d["path"])
        text = parsed.text.strip()
        print(f"[C3] {d['name']} id={d['id']} chars={len(text)} …", flush=True)
        r = chat(
            GATEWAY_BASE,
            GATEWAY_KEY,
            FLASH_MODEL,
            SYSTEM_EXTRACT,
            GOLD_PROMPT.format(text=text) + "\n\n直接输出最终 JSON 对象，不要输出任何思考过程。",
            temperature=0.2,
            max_tokens=16384,
        )
        raw = extract_json(r["content"])
        ir = coerce_ir(raw) if raw is not None else None
        rec = {
            "tag": d["tag"],
            "doc_id": d["id"],
            "name": d["name"],
            "chars": len(text),
            "elapsed": round(r["elapsed"], 2),
            "usage": r["usage"],
            "error": r["error"],
            "finish_reason": r.get("finish_reason"),
            "reasoning_len": r.get("reasoning_len", 0),
            "json_ok": raw is not None,
            "claims": len(ir.claims) if ir else 0,
            "entities": len(ir.entities) if ir else 0,
            "relations": len(ir.relations) if ir else 0,
        }
        out["docs"].append(rec)
        if ir is not None:
            write_json(CASES / f"gold_ir_{d['id']}.json", {"doc_id": d["id"], "name": d["name"], "ir": ir_to_dict(ir)})
            print(
                f"    ok elapsed={rec['elapsed']}s claims={rec['claims']} "
                f"entities={rec['entities']} relations={rec['relations']}",
                flush=True,
            )
        else:
            print(f"    FAILED err={r['error']} head={r['content'][:120]!r}", flush=True)
    write_json(RESULTS / "c3_flash_long.json", out)
    print(f"wrote {RESULTS / 'c3_flash_long.json'}")
    return 0 if all(x["json_ok"] for x in out["docs"]) else 1


if __name__ == "__main__":
    sys.exit(main())
