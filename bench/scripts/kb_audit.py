#!/usr/bin/env python3
"""kb_audit.py — reusable quality "physical" for a knowledge-compiler KB, plus the
weighted C1-vs-flash comparison used in the report.

Two modes:

  # 1) KB health check (read-only) on any KC_HOME
  python3 bench/scripts/kb_audit.py --home ~/knowledge
  python3 bench/scripts/kb_audit.py --home bench/work/kb-kc --label "4B" --json out.json
  # optional retrieval golden set: [{"query":"...","expected_doc_ids":["..."]}, ...]
  python3 bench/scripts/kb_audit.py --home ~/knowledge --golden bench/cases/golden_queries.json

  # 2) weighted quality+performance score from bench results
  python3 bench/scripts/kb_audit.py score
  python3 bench/scripts/kb_audit.py score --w-quality 45 --w-perf 20 --w-practical 35

Zero third-party deps. Never writes to the KB.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

from common import RESULTS, read_json, write_json  # noqa: E402
from kc.retrieve import search  # noqa: E402

# --------------------------------------------------------------------- scoring helpers
def lin(x, lo, hi) -> float:
    """Map x∈[lo,hi] → 1..5 stars (clamped)."""
    if hi == lo:
        return 5.0
    t = max(0.0, min(1.0, (x - lo) / (hi - lo)))
    return round(1 + 4 * t, 2)


def inv(x, lo, hi) -> float:
    """Inverse of lin: larger x → fewer stars."""
    if hi == lo:
        return 5.0
    t = max(0.0, min(1.0, (x - lo) / (hi - lo)))
    return round(5 - 4 * t, 2)


def one(conn, sql, *args):
    r = conn.execute(sql, args).fetchone()
    return r[0] if r else 0


def rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def has_table(conn, name) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)).fetchone())


def has_col(conn, table, col) -> bool:
    try:
        return col in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return False


ENTITY_NOISE = re.compile(r"(^\W+$)|(/)|(\\)|^(\.|raw$|inbox$|docs$)")


def is_noise_entity(name: str, type_: str) -> bool:
    n = (name or "").strip()
    if len(n) <= 1:
        return True
    if "/" in n or "\\" in n or n.startswith("."):
        return True
    if re.fullmatch(r"[A-Za-z0-9_\-\.]+", n) and len(n) <= 2:
        return True
    return False


# --------------------------------------------------------------------------- audit
def audit(home: Path, golden_path: Path | None, label: str) -> dict:
    db = home / "index.db"
    if not db.exists():
        raise SystemExit(f"没有数据库：{db}（先运行 ./kc init）")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    m: dict = {"home": str(home), "label": label}
    m["version"] = one(conn, "PRAGMA user_version")

    docs = rows(conn, "SELECT id,title,char_count,sha256,source_path,summary,model,compiled_at FROM documents")
    n_docs = len(docs)
    n_chunks = one(conn, "SELECT COUNT(*) FROM chunks")
    n_claims = one(conn, "SELECT COUNT(*) FROM claims")
    n_active = one(conn, "SELECT COUNT(*) FROM claims WHERE status='active'")
    n_ent = one(conn, "SELECT COUNT(*) FROM entities")
    n_rel = one(conn, "SELECT COUNT(*) FROM relations")
    n_emb = one(conn, "SELECT COUNT(*) FROM embeddings")
    status = {r["status"]: r["n"] for r in rows(conn, "SELECT status,COUNT(*) n FROM claims GROUP BY status")}

    # ---- traceability
    orphan = one(conn, "SELECT COUNT(*) FROM claims WHERE document_id NOT IN (SELECT id FROM documents)")
    meta_ok = sum(1 for d in docs if d["sha256"] and d["source_path"] and d["compiled_at"])
    log_ok = one(conn, "SELECT COUNT(*) FROM compile_log WHERE status='ok'")
    log_all = one(conn, "SELECT COUNT(*) FROM compile_log")
    trace = {
        "orphan_claims": orphan,
        "docs_with_meta": f"{meta_ok}/{n_docs}",
        "compile_log_ok": f"{log_ok}/{log_all}",
        "raw_files": len(list((home / "raw").glob("*"))) if (home / "raw").exists() else 0,
    }
    s_trace = round(
        (
            (5.0 if orphan == 0 else 1.0)
            + lin(meta_ok / n_docs, 0, 1)
            + lin(log_ok / log_all if log_all else 0, 0, 1)
        )
        / 3,
        2,
    )

    # ---- groundedness proxy (no LLM: source attached, confidence, no extract-failed)
    src_ok = one(conn, "SELECT COUNT(*) FROM claims WHERE source IS NOT NULL AND source<>''")
    avg_conf = one(conn, "SELECT AVG(confidence) FROM claims") or 0
    failed_docs = sum(1 for d in docs if (d["summary"] or "").startswith("[extract-failed]"))
    s_ground = round(
        (
            lin(src_ok / n_claims if n_claims else 0, 0, 1)
            + lin(avg_conf, 0.6, 1.0)
            + (5.0 if failed_docs == 0 else 2.0)
        )
        / 3,
        2,
    )

    # ---- structure
    emb_chunk = one(conn, "SELECT COUNT(DISTINCT ref_id) FROM embeddings WHERE scope='chunk'")
    emb_claim = one(conn, "SELECT COUNT(DISTINCT ref_id) FROM embeddings WHERE scope='claim' AND ref_id IN (SELECT id FROM claims WHERE status='active')")
    s_struct = round(
        (
            (5.0 if m["version"] >= 4 else 2.0)
            + lin(emb_chunk / n_chunks if n_chunks else 0, 0, 1)
            + lin(emb_claim / n_active if n_active else 0, 0, 1)
            + lin(n_rel / (n_docs * 5) if n_docs else 0, 0, 1)
        )
        / 4,
        2,
    )

    # ---- coverage
    tot_doc_chars = sum(d["char_count"] or 0 for d in docs)
    tot_chunk_chars = one(conn, "SELECT SUM(LENGTH(text)) FROM chunks") or 0
    avg_claim_len = one(conn, "SELECT AVG(LENGTH(text)) FROM claims") or 0
    density = (n_active / (tot_doc_chars / 1000)) if tot_doc_chars else 0
    s_cov = round(
        (
            lin(tot_chunk_chars / tot_doc_chars if tot_doc_chars else 0, 0.5, 1.0)
            + lin(density, 0, 6)
            + lin(avg_claim_len, 8, 60)
        )
        / 3,
        2,
    )

    # ---- entities
    singletons = sum(1 for r in rows(conn, "SELECT doc_count FROM entities") if (r["doc_count"] or 0) <= 1)
    ent_names = rows(conn, "SELECT name,type,doc_count FROM entities")
    noise = sum(1 for e in ent_names if is_noise_entity(e["name"], e["type"]))
    n_alias = one(conn, "SELECT COUNT(DISTINCT entity_id) FROM entity_aliases")
    typed = sum(1 for e in ent_names if e["type"] and e["type"] != "other")
    s_ent = round(
        (
            inv(singletons / n_ent if n_ent else 0, 0, 1)
            + inv(noise / n_ent if n_ent else 0, 0, 0.3)
            + lin(n_alias / n_ent if n_ent else 0, 0, 0.3)
            + lin(typed / n_ent if n_ent else 0, 0, 1)
        )
        / 4,
        2,
    )

    # ---- dedup / evolution
    checked = one(conn, "SELECT COUNT(*) FROM claims WHERE dedup_checked=1") if has_col(conn, "claims", "dedup_checked") else 0
    lifecycle = status.get("duplicate", 0) + status.get("superseded", 0)
    s_dedup = round(
        (
            lin(checked / n_claims if n_claims else 0, 0, 1)
            + lin(lifecycle / n_claims if n_claims else 0, 0, 0.15)
        )
        / 2,
        2,
    )

    # ---- retrieval (golden set if supplied, else structural proxy)
    ret = {"source": "proxy", "golden": 0}
    if golden_path and golden_path.exists():
        g = read_json(golden_path, [])
        recalls, mrrs = [], []
        for item in g:
            hits = search(conn, item["query"], limit=item.get("k", 6), mode=item.get("mode", "hybrid"))
            ids = [h["document_id"] for h in hits]
            exp = item.get("expected_doc_ids", [])
            found = [d for d in exp if d in ids]
            recalls.append(len(found) / len(exp) if exp else 0)
            mrrs.append(1 / (ids.index(found[0]) + 1) if found else 0)
        r_avg = sum(recalls) / len(recalls) if recalls else 0
        m_avg = sum(mrrs) / len(mrrs) if mrrs else 0
        s_ret = round((lin(r_avg, 0, 1) + lin(m_avg, 0, 1)) / 2, 2)
        ret = {"source": "golden", "golden": len(g), "recall": round(r_avg, 3), "mrr": round(m_avg, 3)}
    else:
        # without a golden query set we cannot measure retrieval quality → mark n/a
        s_ret = None
        ret = {"source": "proxy", "golden": 0, "note": "未提供 golden，可检索性不评分（n/a）"}

    # ---- hygiene / freshness
    inbox = len([p for p in (home / "inbox").glob("*") if p.is_file()]) if (home / "inbox").exists() else 0
    emb_orphan = 0
    if has_table(conn, "embeddings"):
        emb_orphan = one(conn, "SELECT COUNT(*) FROM embeddings WHERE (scope='chunk' AND ref_id NOT IN (SELECT id FROM chunks)) OR (scope='claim' AND ref_id NOT IN (SELECT id FROM claims))")
    expired_mem = one(conn, "SELECT COUNT(*) FROM memories WHERE status='expired'") if has_table(conn, "memories") else 0
    active_mem = one(conn, "SELECT COUNT(*) FROM memories WHERE status='active'") if has_table(conn, "memories") else 0
    s_hyg = round(
        (
            (5.0 if inbox == 0 else (4.0 if inbox <= 3 else 2.0))
            + (5.0 if emb_orphan == 0 else 1.0)
            + (5.0 if log_all - log_ok == 0 else 2.0)
        )
        / 3,
        2,
    )

    dims = {
        "可追溯性": s_trace,
        "有据性(代理)": s_ground,
        "结构规范": s_struct,
        "完整/覆盖": s_cov,
        "实体质量": s_ent,
        "判重/演化": s_dedup,
        "可检索性": s_ret,
        "卫生/时效": s_hyg,
    }
    weights = {
        "可追溯性": 15, "有据性(代理)": 10, "结构规范": 12, "完整/覆盖": 13,
        "实体质量": 15, "判重/演化": 13, "可检索性": 12, "卫生/时效": 10,
    }
    scored = {k: v for k, v in dims.items() if v is not None}
    overall = round(sum(scored[k] * weights[k] for k in scored) / sum(weights[k] for k in scored), 2)

    # ---- findings
    findings = []
    if orphan:
        findings.append(("高", f"{orphan} 条孤儿 claim（无对应文档）"))
    if checked < n_claims:
        findings.append(("高", f"判重覆盖不足：{checked}/{n_claims}，建议 ./kc dedup"))
    if n_ent and singletons / n_ent > 0.6:
        findings.append(("中", f"实体碎片化：{singletons}/{n_ent} 仅出现于 1 篇，建议 ./kc entities --suggest"))
    if noise:
        findings.append(("中", f"{noise} 个疑似噪声实体（路径/目录名/代码标识符）"))
    if emb_chunk < n_chunks or emb_claim < n_active:
        findings.append(("中", f"向量覆盖不全：chunk {emb_chunk}/{n_chunks}，active claim {emb_claim}/{n_active}"))
    if inbox:
        findings.append(("中", f"inbox 积压 {inbox} 个文件，未编译"))
    if failed_docs:
        findings.append(("高", f"{failed_docs} 篇抽取失败（[extract-failed]）"))
    if avg_claim_len and avg_claim_len < 15:
        findings.append(("低", f"claim 平均仅 {avg_claim_len:.0f} 字，偏碎片"))
    if m["version"] < 4:
        findings.append(("低", f"schema v{m['version']} 偏旧，可迁移"))
    if ret["source"] == "proxy":
        findings.append(("低", "未提供 golden 检索集，可检索性记 n/a（不计入综合分）"))

    m.update(
        {
            "scale": {
                "documents": n_docs, "chunks": n_chunks, "claims": n_claims, "claims_active": n_active,
                "entities": n_ent, "relations": n_rel, "embeddings": n_emb,
                "memories_active": active_mem, "memories_expired": expired_mem,
            },
            "claim_status": status,
            "claim_avg_len": round(avg_claim_len, 1),
            "claim_density_per_kchar": round(density, 2),
            "chunk_text_coverage": round(tot_chunk_chars / tot_doc_chars, 3) if tot_doc_chars else 0,
            "entity_singleton_ratio": round(singletons / n_ent, 3) if n_ent else 0,
            "entity_noise_ratio": round(noise / n_ent, 3) if n_ent else 0,
            "dedup_coverage": round(checked / n_claims, 3) if n_claims else 0,
            "inbox_pending": inbox,
            "embedding_orphans": emb_orphan,
            "dimensions": dims,
            "weights": weights,
            "overall": overall,
            "retrieval": ret,
            "findings": [{"severity": s, "message": t} for s, t in findings],
            "detail": trace,
        }
    )
    conn.close()
    return m


def print_audit(m: dict) -> None:
    print(f"\n知识库体检 · {m['label']}  ({m['home']}, schema v{m['version']})")
    print("=" * 60)
    sc = m["scale"]
    print(f"规模: {sc['documents']} docs · {sc['chunks']} chunks · {sc['claims']} claims"
          f"（active {sc['claims_active']}）· {sc['entities']} entities · {sc['relations']} relations · {sc['embeddings']} vectors")
    print(f"claim 均长 {m['claim_avg_len']} 字 · 密度 {m['claim_density_per_kchar']}/k字 · chunk 覆盖 {m['chunk_text_coverage']} ·"
          f" 实体孤岛 {m['entity_singleton_ratio']} · 判重覆盖 {m['dedup_coverage']}")
    print("\n维度评分 (1-5):")
    for k, v in m["dimensions"].items():
        if v is None:
            print(f"  {k:<12} n/a（需 --golden 检索集）  (w{m['weights'][k]})")
            continue
        bar = "█" * int(round(v)) + "░" * (5 - int(round(v)))
        print(f"  {k:<12} {bar}  {v}  (w{m['weights'][k]})")
    print(f"\n  综合: {m['overall']} / 5")
    if m.get("findings"):
        print("\n问题清单:")
        for f in m["findings"]:
            print(f"  [{f['severity']}] {f['message']}")


# --------------------------------------------------------------------------- score mode
def score_mode(args) -> None:
    ec = read_json(RESULTS / "eval_compile.json") or {}
    es = read_json(RESULTS / "eval_scenarios.json") or {}
    if not ec or not es:
        raise SystemExit("缺少 bench/results/eval_compile.json / eval_scenarios.json，先跑 eval 脚本")

    def avg(a):
        return sum(a) / len(a) if a else 0

    def cg(arm, doc, key):
        return ec["arms"][arm][doc]["claim_recall_judge"] if key == "claim" else 0

    def arm_compile(arm):
        ds = list(ec["arms"][arm].values())
        return {
            "claim_recall": avg([d["claim_recall_judge"] for d in ds]),
            "grounded": avg([d["claim_grounded_rate"] for d in ds]),
            "entity_f1": avg([d["entities"]["f1"] for d in ds]),
            "relation": avg([d["relations"]["recall_judge"] for d in ds]),
        }

    # scenario aggregates
    def arm_scen(arm):
        cov, hall, neg_pass, neg = [], 0, 0, 0
        for c in es["cases"]:
            a = c["arms"].get(arm, {}).get("ask")
            if not a:
                continue
            if c["category"] == "negative":
                neg += 1
                if a.get("judge", {}).get("declined") or a.get("declined_det"):
                    neg_pass += 1
            else:
                if a.get("coverage") is not None:
                    cov.append(a["coverage"])
                if a.get("judge", {}).get("hallucinated"):
                    hall += 1
        hall_rate = hall / len(cov) if cov else 0
        return {
            "coverage": avg(cov), "hallu_rate": hall_rate,
            "trust": 0.5 * (neg_pass / neg if neg else 0) + 0.5 * (1 - hall_rate),
        }

    # latency from compile_*.json (full pipeline) and scenario ask timings
    def arm_perf(arm):
        cj = read_json(RESULTS / f"compile_{'kc' if arm == 'C1' else 'flash'}.json") or {}
        ask = []
        for c in es["cases"]:
            if c["category"] == "negative":
                continue
            a = c["arms"].get("U1" if arm == "C1" else "U2", {}).get("ask")
            if a:
                ask.append(a["elapsed"])
        return {"compile_s": cj.get("total_s"), "ask_s": avg(ask)}

    comp = {"C1": arm_compile("kc"), "C2": arm_compile("flash")}
    scen = {"C1": arm_scen("U1"), "C2": arm_scen("U2"), "U3": arm_scen("U3")}
    perf = {"C1": arm_perf("C1"), "C2": arm_perf("C2")}

    def star_ground(s):
        if s >= 0.9:
            return round(4 + (s - 0.9) / 0.1, 2)
        if s >= 0.7:
            return round(2 + (s - 0.7) / 0.2 * 2, 2)
        return round(max(1.0, 2 * s / 0.7), 2)

    def star_compile_lat(seconds):
        m = (seconds or 0) / 60
        if m <= 5:
            return 5.0
        if m <= 10:
            return round(4 + (10 - m) / 5, 2)
        if m <= 20:
            return round(3 + (20 - m) / 10, 2)
        if m <= 30:
            return round(2 + (30 - m) / 10, 2)
        return 1.0

    def star_ask_lat(seconds):
        s = seconds or 0
        if s <= 3:
            return 5.0
        if s <= 5:
            return round(4 + (5 - s) / 2, 2)
        if s <= 15:
            return round(3 + (15 - s) / 10, 2)
        if s <= 30:
            return round(2 + (30 - s) / 15, 2)
        return 1.0

    def stars_compile(c):
        s_claim = lin(c["claim_recall"], 0.2, 0.8)
        s_ground = star_ground(c["grounded"])
        s_ent = lin(c["entity_f1"], 0, 1)
        s_rel = lin(c["relation"], 0, 1)
        return s_claim, s_ground, s_ent, s_rel

    def stars_scen(s):
        return lin(s["coverage"], 0, 1), lin(s["trust"], 0.5, 1.0)

    def stars_perf(p):
        return star_compile_lat(p["compile_s"]), star_ask_lat(p["ask_s"])

    # quality weighted (quality sub-weights, 60 total in balanced view)
    QW = {"claim": 15, "grounded": 10, "entity": 10, "relation": 5, "coverage": 10, "retrieval": 5, "trust": 5}
    P = {"C1": stars_perf(perf["C1"]), "C2": stars_perf(perf["C2"])}
    C = {"C1": stars_compile(comp["C1"]), "C2": stars_compile(comp["C2"])}
    S = {"C1": stars_scen(scen["C1"]), "C2": stars_scen(scen["C2"])}

    def quality(arm):
        c, s, p = C[arm], S[arm], P[arm]
        return round(
            (c[0] * QW["claim"] + c[1] * QW["grounded"] + c[2] * QW["entity"] + c[3] * QW["relation"]
             + s[0] * QW["coverage"] + 5.0 * QW["retrieval"] + s[1] * QW["trust"])
            / sum(QW.values()),
            2,
        )

    def perf_score(arm):
        return round((P[arm][0] * 15 + P[arm][1] * 10) / 25, 2)

    practical = {"C1": round((5.0 + 5.0) / 2, 2), "C2": round((3.0 + 2.5) / 2, 2)}
    wq, wp, wr = args.w_quality, args.w_perf, args.w_practical
    tot = wq + wp + wr
    comp_score = {}
    for arm in ("C1", "C2"):
        comp_score[arm] = round((quality(arm) * wq + perf_score(arm) * wp + practical[arm] * wr) / tot, 2)

    print(f"\n质量+性能综合评分（权重 质量{wq}/性能{wp}/落地{wr}，归一化到 5）")
    print("=" * 64)
    print(f"{'指标':<16}{'C1 本地4B':>14}{'C2 flash':>14}{'U3 直读':>12}")
    def line(name, a, b, c=None):
        print(f"{name:<16}{a:>14}{b:>14}{(c if c is not None else '—'):>12}")
    line("论断召回", round(comp['C1']['claim_recall'],3), round(comp['C2']['claim_recall'],3))
    line("有据率", round(comp['C1']['grounded'],3), round(comp['C2']['grounded'],3))
    line("实体F1", round(comp['C1']['entity_f1'],3), round(comp['C2']['entity_f1'],3))
    line("关系召回", round(comp['C1']['relation'],3), round(comp['C2']['relation'],3))
    line("问答覆盖", round(scen['C1']['coverage'],3), round(scen['C2']['coverage'],3), round(scen['U3']['coverage'],3))
    line("可信度", round(scen['C1']['trust'],3), round(scen['C2']['trust'],3), round(scen['U3']['trust'],3))
    line("编译秒", perf['C1']['compile_s'], perf['C2']['compile_s'])
    line("问答秒", round(perf['C1']['ask_s'],1), round(perf['C2']['ask_s'],1))
    print("-" * 64)
    line("质量分", quality('C1'), quality('C2'))
    line("性能分", perf_score('C1'), perf_score('C2'))
    line("落地分", practical['C1'], practical['C2'])
    print("-" * 64)
    line("综合", comp_score['C1'], comp_score['C2'])
    winner = "C2 flash" if comp_score['C2'] > comp_score['C1'] else "C1 本地4B"
    print(f"\n胜者：{winner}（差 {abs(comp_score['C2']-comp_score['C1']):.2f}）")
    # crossover: d(C1-C2) = -0.70*wq - 3.04*wp + 2.25*wr = 0  (anchors above)
    print("提示：把 --w-practical 提到约 45+（离线/隐私/成本）会反转，例如 "
          "--w-quality 40 --w-perf 15 --w-practical 45。")


def _delta(d):
    if d is None:
        return "n/a"
    if d > 0.05:
        return f"▲ +{d:.2f}"
    if d < -0.05:
        return f"▼ {d:.2f}"
    return "—"


def compare_mode(args) -> None:
    """Compare two KC_HOMEs (e.g. before/after a recompile) side by side."""
    golden = Path(args.golden) if args.golden else None
    pa, pb = Path(args.a).expanduser(), Path(args.b).expanduser()
    a = audit(pa, golden, args.label_a or pa.name)
    b = audit(pb, golden, args.label_b or pb.name)

    print(f"\n知识库体检对比 · {a['label']} → {b['label']}")
    print("=" * 66)
    sa, sb = a["scale"], b["scale"]
    print(f"{'规模':<12}{a['label']:>12}{b['label']:>12}{'Δ':>12}")
    for key, name in [
        ("documents", "documents"), ("chunks", "chunks"), ("claims", "claims"),
        ("claims_active", "claims_active"), ("entities", "entities"),
        ("relations", "relations"), ("embeddings", "embeddings"),
    ]:
        print(f"{name:<12}{sa[key]:>12}{sb[key]:>12}{(sb[key]-sa[key]):>+12}")

    print(f"\n{'维度':<14}{a['label']:>9}{b['label']:>9}{'Δ':>10}")
    for k in a["dimensions"]:
        va, vb = a["dimensions"].get(k), b["dimensions"].get(k)
        fa = "n/a" if va is None else f"{va:.2f}"
        fb = "n/a" if vb is None else f"{vb:.2f}"
        d = None if (va is None or vb is None) else round(vb - va, 2)
        print(f"{k:<14}{fa:>9}{fb:>9}{_delta(d):>12}")
    print("-" * 66)
    print(f"{'综合':<14}{a['overall']:>9.2f}{b['overall']:>9.2f}{_delta(round(b['overall']-a['overall'],2)):>12}")

    print(f"\n{'结构指标':<22}{a['label']:>10}{b['label']:>10}{'Δ':>10}")
    metrics = [
        ("chunk文本覆盖", "chunk_text_coverage"),
        ("claim均长", "claim_avg_len"),
        ("claim密度/k字", "claim_density_per_kchar"),
        ("实体孤岛率", "entity_singleton_ratio"),
        ("实体噪声率", "entity_noise_ratio"),
        ("判重覆盖率", "dedup_coverage"),
        ("inbox积压", "inbox_pending"),
    ]
    for name, key in metrics:
        va, vb = a.get(key), b.get(key)
        d = round(vb - va, 3) if isinstance(va, (int, float)) and isinstance(vb, (int, float)) else None
        print(f"{name:<22}{va:>10}{vb:>10}{(('%+.3f' % d) if d is not None else 'n/a'):>10}")

    improved = [k for k in a["dimensions"] if a["dimensions"].get(k) is not None and b["dimensions"].get(k) is not None and b["dimensions"][k] - a["dimensions"][k] > 0.05]
    worsened = [k for k in a["dimensions"] if a["dimensions"].get(k) is not None and b["dimensions"].get(k) is not None and a["dimensions"][k] - b["dimensions"][k] > 0.05]
    if improved:
        print(f"\n⬆ 改善：{', '.join(improved)}")
    if worsened:
        print(f"⬇ 退步：{', '.join(worsened)}")
    if b.get("findings"):
        print(f"\n{b['label']} 仍需处理：")
        for f in b["findings"]:
            print(f"  [{f['severity']}] {f['message']}")

    if args.json:
        write_json(Path(args.json), {"a": a, "b": b})
        print(f"\n结果已写入 {args.json}")


def main() -> int:
    ap = argparse.ArgumentParser(description="knowledge-compiler 知识库体检 / 综合评分 / 前后对比")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("score", help="基于 bench 结果做加权综合评分")
    s.add_argument("--w-quality", type=float, default=60)
    s.add_argument("--w-perf", type=float, default=25)
    s.add_argument("--w-practical", type=float, default=15)

    cp = sub.add_parser("compare", help="对比两个 KC_HOME（如重编译前/后）")
    cp.add_argument("--a", required=True, help="基准 KC_HOME")
    cp.add_argument("--b", required=True, help="对比 KC_HOME")
    cp.add_argument("--label-a", default=None)
    cp.add_argument("--label-b", default=None)
    cp.add_argument("--golden", default=None, help="检索 golden 集 JSON")
    cp.add_argument("--json", default=None, help="把对比结果写入 JSON")

    ap.add_argument("--home", default=None, help="KC_HOME（默认 $KC_HOME 或 ~/knowledge）")
    ap.add_argument("--label", default=None)
    ap.add_argument("--json", default=None, help="把体检结果写入 JSON")
    ap.add_argument("--golden", default=None, help="检索 golden 集 JSON")
    args = ap.parse_args()

    if args.cmd == "score":
        score_mode(args)
        return 0

    if args.cmd == "compare":
        compare_mode(args)
        return 0

    import os

    home = Path(args.home or os.environ.get("KC_HOME") or "~/knowledge").expanduser()
    label = args.label or home.name
    m = audit(home, Path(args.golden) if args.golden else None, label)
    print_audit(m)
    if args.json:
        write_json(Path(args.json), m)
        print(f"\n结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
