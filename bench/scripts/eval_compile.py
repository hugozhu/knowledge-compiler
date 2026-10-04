#!/usr/bin/env python3
"""Compile-quality eval: score C1 (kc/qwen3-4b) and C2 (flash) IRs against C3 gold.

Metrics per (arm, doc):
  entities  : deterministic precision / recall / F1 (normalised name matching)
  claims    : recall vs gold (judge) + groundedness (judge, vs source text)
  relations : recall vs gold (judge)
  lexical   : deterministic claim recall (substring / bigram Jaccard) as a sanity check

Usage: python3 bench/scripts/eval_compile.py
Writes: bench/results/eval_compile.json
"""

from __future__ import annotations

import sys

from common import (  # noqa: E402
    CASES,
    DOCS,
    RESULTS,
    WORK,
    as_ids,
    judge,
    norm,
    read_json,
    write_json,
)
from kc.parsers import parse_file  # noqa: E402

COVER_SYS = "你是严谨的知识抽取评测员。只输出一个 JSON 对象，不要解释。"
GROUND_SYS = "你是事实核查员。只输出一个 JSON 对象，不要解释。"


def load_ir(path) -> dict:
    d = read_json(path)
    return (d or {}).get("ir", {}) if d else {}


def ent_match(g: str, c: str) -> bool:
    ng, nc = norm(g), norm(c)
    if not ng or not nc:
        return False
    if ng == nc:
        return True
    return len(ng) >= 2 and len(nc) >= 2 and (ng in nc or nc in ng)


def ent_scores(gold: list, cand: list) -> dict:
    gnames = [e.get("name", "") for e in gold]
    cnames = [e.get("name", "") for e in cand]
    ghit = sum(1 for g in gnames if any(ent_match(g, c) for c in cnames))
    chit = sum(1 for c in cnames if any(ent_match(g, c) for g in gnames))
    prec = chit / len(cnames) if cnames else 0.0
    rec = ghit / len(gnames) if gnames else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {
        "gold": len(gnames),
        "cand": len(cnames),
        "matched_gold": ghit,
        "matched_cand": chit,
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(f1, 3),
    }


def jaccard(a: str, b: str) -> float:
    A = {a[i : i + 2] for i in range(len(a) - 1)} or {a}
    B = {b[i : i + 2] for i in range(len(b) - 1)} or {b}
    return len(A & B) / len(A | B) if (A | B) else 0.0


def claim_lex_recall(gold: list, cand: list) -> float:
    gl = [norm(c.get("text", "")) for c in gold]
    cl = [norm(c.get("text", "")) for c in cand]
    hit = 0
    for g in gl:
        if not g:
            continue
        if any(g in c or c in g or jaccard(g, c) >= 0.6 for c in cl):
            hit += 1
    return round(hit / len(gl), 3) if gl else 0.0


def judge_cover(gold: list[str], cand: list[str], kind: str) -> dict:
    gold_list = "\n".join(f"G{i}: {t}" for i, t in enumerate(gold, 1))
    cand_list = "\n".join(f"C{i}: {t}" for i, t in enumerate(cand, 1))
    user = (
        f"下面是「金标准{kind}」和另一系统对同一文档抽取的「候选{kind}」。\n"
        f"请判断每条金标准{kind}是否被某条候选在语义上覆盖（含义相同，或候选表达了该含义；措辞不同也算）。\n\n"
        f"金标准：\n{gold_list}\n\n候选：\n{cand_list}\n\n"
        '只输出 JSON：{"covered": [被覆盖的 G 编号], "uncovered": [未被覆盖的 G 编号]}'
    )
    obj, raw = judge(COVER_SYS, user)
    if obj is None:
        return {"covered": [], "raw_error": raw.get("error") if raw else "unknown"}
    return {"covered": as_ids(obj.get("covered", [])), "uncovered": obj.get("uncovered", [])}


def judge_grounded(text: str, claims: list[str]) -> dict:
    body = "\n".join(f"C{i}: {t}" for i, t in enumerate(claims, 1))
    user = (
        "下面是原文和候选论断列表。请判断每条候选论断能否在原文中找到明确依据"
        "（直接陈述或合理同义归纳）。原文较长，请只依据所给内容判断。\n\n"
        f"原文：\n\"\"\"\n{text[:14000]}\n\"\"\"\n\n候选论断：\n{body}\n\n"
        '只输出 JSON：{"grounded": [有依据的 C 编号], "unsupported": [无依据的 C 编号]}'
    )
    obj, raw = judge(GROUND_SYS, user)
    if obj is None:
        return {"grounded": [], "raw_error": raw.get("error") if raw else "unknown"}
    return {"grounded": as_ids(obj.get("grounded", []))}


def main() -> int:
    arms = ["kc", "flash"]
    out = {"arms": {}}
    for arm in arms:
        out["arms"][arm] = {}
        for d in DOCS:
            gold = load_ir(CASES / f"gold_ir_{d['id']}.json")
            cand = load_ir(WORK / f"kb-{arm}" / "ir" / f"ir_{d['id']}.json")
            gcl = [c.get("text", "") for c in gold.get("claims", [])]
            ccl = [c.get("text", "") for c in cand.get("claims", [])]
            grel = [f"{r.get('subject')} | {r.get('predicate')} | {r.get('object')}" for r in gold.get("relations", [])]
            crel = [f"{r.get('subject')} | {r.get('predicate')} | {r.get('object')}" for r in cand.get("relations", [])]

            ent = ent_scores(gold.get("entities", []), cand.get("entities", []))
            cov = judge_cover(gcl, ccl, "论断")
            grounded = judge_grounded(parse_file(d["path"]).text, ccl)
            rel = judge_cover(grel, crel, "关系") if grel else {"covered": []}

            rec = {
                "gold_claims": len(gcl),
                "cand_claims": len(ccl),
                "claim_recall_judge": round(len(cov["covered"]) / len(gcl), 3) if gcl else 0.0,
                "claim_recall_lexical": claim_lex_recall(gold.get("claims", []), cand.get("claims", [])),
                "claim_grounded_rate": round(len(grounded["grounded"]) / len(ccl), 3) if ccl else 0.0,
                "entities": ent,
                "relations": {
                    "gold": len(grel),
                    "cand": len(crel),
                    "recall_judge": round(len(rel["covered"]) / len(grel), 3) if grel else 0.0,
                },
                "claim_cover_detail": cov,
                "ground_detail": grounded,
                "summary": cand.get("summary", ""),
                "gold_summary": gold.get("summary", ""),
            }
            out["arms"][arm][d["id"]] = rec
            print(
                f"[{arm}/{d['tag']}] claims {len(ccl)}/{len(gcl)} "
                f"recall={rec['claim_recall_judge']} lex={rec['claim_recall_lexical']} "
                f"grounded={rec['claim_grounded_rate']} entF1={ent['f1']} "
                f"rel={rec['relations']['recall_judge']}",
                flush=True,
            )
    write_json(RESULTS / "eval_compile.json", out)
    print(f"wrote {RESULTS / 'eval_compile.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
