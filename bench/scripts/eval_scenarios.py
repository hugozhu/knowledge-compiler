#!/usr/bin/env python3
"""Use-case eval across three arms.

Arms:
  U1 — kc KB (qwen3-4b compile) + local qwen3-4b for ask/context   [final local product]
  U2 — flash KB (deepseek-v4-flash compile) + flash for ask/context [same pipeline, strong model]
  U3 — flash reading both raw docs directly (long-context ceiling; ask only)

For every case: retrieval recall/MRR (fts vs hybrid), ask fact-coverage (deterministic + qwen3-8-max
judge), negative-question handling, context-pack coverage, and wall-clock timings.

Usage: python3 bench/scripts/eval_scenarios.py
Writes: bench/results/eval_scenarios.json
"""

from __future__ import annotations

import json
import sys
import time

from common import (  # noqa: E402
    CASES,
    DOCS,
    FLASH_MODEL,
    GATEWAY_BASE,
    GATEWAY_KEY,
    JUDGE_MODEL,
    LOCAL_BASE,
    LOCAL_KEY,
    LOCAL_MODEL,
    RESULTS,
    WORK,
    FlashLLM,
    RecordingLLM,
    as_ids,
    chat,
    judge,
    norm,
    read_json,
    write_json,
)
from kc import db as dbmod  # noqa: E402
from kc.config import load_config  # noqa: E402
from kc.context import build_context  # noqa: E402
from kc.llm import LLM  # noqa: E402
from kc.parsers import parse_file  # noqa: E402
from kc.retrieve import ask, search  # noqa: E402

DECLINE_MARKERS = [
    "没有相关", "无相关", "未提及", "没有提到", "无法回答", "无法确定", "信息不足",
    "没有足够", "知识库里没有", "知识库中没有", "资料中没有", "没有涉及", "cannot answer",
    "no information", "not mention", "no relevant",
]
COVER_SYS = "你是严谨的问答评测员。只输出一个 JSON 对象，不要解释。"


def det_coverage(answer: str, facts: list[str]) -> list[int]:
    na = norm(answer)
    return [i for i, f in enumerate(facts, 1) if norm(f) and norm(f) in na]


def judge_answer(question: str, facts: list[str], answer: str) -> dict:
    fl = "\n".join(f"{i}. {f}" for i, f in enumerate(facts, 1))
    user = (
        f"问题：{question}\n\n参考答案要点：\n{fl}\n\n待评回答：\n{answer}\n\n"
        "请判断待评回答在语义上覆盖了哪些要点（措辞不同也算），以及回答是否含有要点之外、疑似编造的内容。"
        '只输出 JSON：{"covered":[要点编号],"hallucinated":true/false}'
    )
    obj, raw = judge(COVER_SYS, user)
    if obj is None:
        return {"covered": [], "hallucinated": None, "error": raw.get("error") if raw else "unknown"}
    return {
        "covered": as_ids(obj.get("covered", [])),
        "hallucinated": obj.get("hallucinated"),
    }


def judge_negative(question: str, answer: str) -> dict:
    user = (
        f"问题：{question}\n\n待评回答：\n{answer}\n\n"
        "该问题在给定知识库中没有答案。请判断待评回答是正确承认信息不足/无法回答（declined），"
        '还是编造了内容（hallucinated）。只输出 JSON：{"declined":true/false,"hallucinated":true/false}'
    )
    obj, raw = judge(COVER_SYS, user)
    if obj is None:
        return {"declined": None, "hallucinated": None, "error": raw.get("error") if raw else "unknown"}
    return {"declined": bool(obj.get("declined")), "hallucinated": bool(obj.get("hallucinated"))}


def retrieval_metrics(conn, query: str, expected: list[str], k: int = 6) -> dict:
    out = {}
    for mode in ("fts", "hybrid"):
        t = time.time()
        hits = search(conn, query, limit=k, mode=mode)
        el = round(time.time() - t, 3)
        ids = [h["document_id"] for h in hits]
        found = [d for d in expected if d in ids]
        ranks = [ids.index(d) + 1 for d in expected if d in ids]
        out[mode] = {
            "elapsed": el,
            "top_doc_ids": ids,
            "recall": round(len(found) / len(expected), 3) if expected else None,
            "mrr": round(1 / min(ranks), 3) if ranks else (0.0 if expected else None),
            "hits": len(hits),
        }
    return out


def flash_direct(question: str, raw_text: str) -> dict:
    system = (
        "你是知识助手。只依据用户提供的资料回答问题，使用中文，简洁；"
        "资料中没有相关内容就直说没有，不要编造。引用时标注来源文档名。"
    )
    user = f"资料：\n\"\"\"\n{raw_text}\n\"\"\"\n\n问题：{question}\n\n（直接给出最终答案，不要输出思考过程。）"
    return chat(GATEWAY_BASE, GATEWAY_KEY, FLASH_MODEL, system, user, temperature=0.2, max_tokens=4096)


def main() -> int:
    scen = read_json(CASES / "eval_scenarios.json")
    cases = scen["cases"]
    raw_all = "\n\n===== 文档A: 178 Agent=Model+Harness =====\n" + parse_file(DOCS[0]["path"]).text
    raw_all += "\n\n===== 文档B: 263 Loop Engineering =====\n" + parse_file(DOCS[1]["path"]).text

    kc_conn = dbmod.connect(load_config(str(WORK / "kb-kc")).db_path)
    fl_conn = dbmod.connect(load_config(str(WORK / "kb-flash")).db_path)
    kc_llm = RecordingLLM(LLM(LOCAL_BASE, LOCAL_KEY, LOCAL_MODEL, "qwen3-vl-4b", timeout=600), "qwen3-4b")
    fl_llm = FlashLLM(LLM(GATEWAY_BASE, GATEWAY_KEY, FLASH_MODEL, "qwen3-vl-4b", timeout=900), "deepseek-v4-flash")

    results = []
    for c in cases:
        rec = {"id": c["id"], "category": c["category"], "query": c["query"], "arms": {}}
        print(f"\n### {c['id']} [{c['category']}] {c['query']}", flush=True)
        expected = c.get("expected_doc_ids", [])
        negative = c["category"] == "negative"

        # ---------------- retrieval (U1, U2) ----------------
        if "retrieval" in c["type"] and expected and not negative:
            for arm, conn in (("U1", kc_conn), ("U2", fl_conn)):
                m = retrieval_metrics(conn, c["query"], expected)
                rec["arms"].setdefault(arm, {})["retrieval"] = m
                print(f"  {arm} retrieval fts={m['fts']['recall']}/{m['fts']['mrr']} hybrid={m['hybrid']['recall']}/{m['hybrid']['mrr']}", flush=True)

        # ---------------- ask (U1, U2, U3) ----------------
        if "ask" in c["type"]:
            facts = c.get("expected_facts", [])
            arms_ask = {}
            # U1 / U2 via kc retrieve.ask
            for arm, conn, llm in (("U1", kc_conn, kc_llm), ("U2", fl_conn, fl_llm)):
                t = time.time()
                try:
                    res = ask(conn, llm, c["query"])
                except Exception as e:  # noqa: BLE001
                    res = {"answer": f"[error] {e}", "sources": []}
                el = round(time.time() - t, 2)
                answer = (res or {}).get("answer", "(无相关内容)")
                if res is None:
                    answer = "(知识库中没有相关内容)"
                arms_ask[arm] = {"answer": answer, "elapsed": el, "sources": [s["document_id"] for s in (res or {}).get("sources", [])] if res else []}
            # U3 flash direct over raw docs
            t = time.time()
            r3 = flash_direct(c["query"], raw_all)
            arms_ask["U3"] = {"answer": r3["content"], "elapsed": round(time.time() - t, 2), "sources": ["raw A+B"], "error": r3["error"]}

            for arm, a in arms_ask.items():
                if negative:
                    det = any(m in a["answer"] for m in DECLINE_MARKERS)
                    jd = judge_negative(c["query"], a["answer"])
                    a["declined_det"] = det
                    a["judge"] = jd
                    rec["arms"].setdefault(arm, {})["ask"] = a
                    print(f"  {arm} ask declined det={det} judge={jd.get('declined')} halluc={jd.get('hallucinated')} ({a['elapsed']}s)", flush=True)
                else:
                    det = det_coverage(a["answer"], facts)
                    jd = judge_answer(c["query"], facts, a["answer"])
                    a["covered_det"] = det
                    a["judge"] = jd
                    cov = set(det) | set(jd.get("covered", []))
                    a["coverage"] = round(len(cov) / len(facts), 3) if facts else None
                    rec["arms"].setdefault(arm, {})["ask"] = a
                    print(f"  {arm} ask coverage={a['coverage']} hallu={jd.get('hallucinated')} ({a['elapsed']}s)", flush=True)

        # ---------------- context (U1, U2) ----------------
        if "context" in c["type"]:
            for arm, conn, llm in (("U1", kc_conn, kc_llm), ("U2", fl_conn, fl_llm)):
                t = time.time()
                try:
                    pack = build_context(conn, llm, c["query"], max_chars=6000)
                except Exception as e:  # noqa: BLE001
                    pack = {"pack": f"[error] {e}", "stats": {}}
                el = round(time.time() - t, 2)
                npack = norm(pack["pack"])
                cov = [f for f in c.get("expected_facts", []) if norm(f) in npack]
                rec["arms"].setdefault(arm, {})["context"] = {
                    "elapsed": el,
                    "chars": len(pack["pack"]),
                    "stats": pack.get("stats", {}),
                    "facts_covered": cov,
                    "coverage": round(len(cov) / len(c["expected_facts"]), 3),
                    "pack": pack["pack"],
                }
                print(f"  {arm} context chars={len(pack['pack'])} coverage={len(cov)}/{len(c['expected_facts'])} ({el}s)", flush=True)

        results.append(rec)

    write_json(RESULTS / "eval_scenarios.json", {"judge_model": JUDGE_MODEL, "cases": results})
    print(f"\nwrote {RESULTS / 'eval_scenarios.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
