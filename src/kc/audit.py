"""Offline contradiction patrol among active claims.

Complements compile-time dedup: dedup judges NEW claims against the library;
audit sweeps the EXISTING library pairwise (vector candidates → LLM verdicts).

Verdicts: contradiction / duplicate / consistent / unrelated.
--apply: contradiction → older superseded by newer (history kept);
         duplicate    → newer marked duplicate_of older.
"""

from __future__ import annotations

from .llm import LLM, LLMUnavailable
from .util import extract_json
from .vectors import cosine

AUDIT_SYSTEM = "你是知识一致性审查员。只输出一个 JSON 数组，不要任何解释。"
VERDICTS = {"contradiction", "duplicate", "consistent", "unrelated"}


def candidate_pairs(
    conn,
    threshold: float = 0.3,
    neighbors: int = 1,
    max_pairs: int = 50,
) -> list[dict]:
    """Pairwise cosine among active claims → top pairs above threshold."""
    rows = conn.execute(
        """SELECT e.ref_id, e.vec FROM embeddings e
           JOIN claims c ON c.id=e.ref_id
           WHERE e.scope='claim' AND e.model='ngram-v1' AND c.status='active'"""
    ).fetchall()
    vecs = {r["ref_id"]: r["vec"] for r in rows}
    ids = sorted(vecs)
    best: dict[tuple[int, int], float] = {}
    for i, a in enumerate(ids):
        sims: list[tuple[float, int]] = []
        for b in ids[i + 1 :]:
            s = cosine(vecs[a], vecs[b])
            if s >= threshold:
                sims.append((s, b))
        sims.sort(reverse=True)
        for s, b in sims[:neighbors]:
            key = (a, b) if a < b else (b, a)
            best[key] = max(best.get(key, 0.0), s)
    ranked = sorted(best.items(), key=lambda kv: -kv[1])[:max_pairs]
    out: list[dict] = []
    for (a, b), s in ranked:
        ra = conn.execute("SELECT text FROM claims WHERE id=?", (a,)).fetchone()
        rb = conn.execute("SELECT text FROM claims WHERE id=?", (b,)).fetchone()
        if ra and rb:
            out.append(
                {"a": a, "b": b, "score": round(s, 3), "a_text": ra["text"], "b_text": rb["text"]}
            )
    return out


def _judge_prompt(pairs: list[dict]) -> str:
    parts = []
    for i, p in enumerate(pairs, 1):
        parts.append(
            f"第{i}对：\nA: {p['a_text']}\nB: {p['b_text']}"
        )
    return (
        "判断每对论断的关系。判定选项：\n"
        "- contradiction：两论断不能同时为真（直接矛盾）\n"
        "- duplicate：意思相同或几乎相同\n"
        "- consistent：相关但可以同时成立（一致/互补/细节差异）\n"
        "- unrelated：话题不同\n\n"
        '输出 JSON 数组，每项 {"index": 对序号, "verdict": "...", "reason": "一句话"}\n\n'
        + "\n\n".join(parts)
    )


def judge_pairs(llm: LLM, pairs: list[dict], batch: int = 4) -> list[dict]:
    """LLM verdicts for all pairs (batched). Falls back to 'consistent' per pair on failure."""
    out: list[dict] = []
    for i in range(0, len(pairs), batch):
        chunk = pairs[i : i + batch]
        verdicts: list[dict] = []
        try:
            raw = llm.chat(AUDIT_SYSTEM, _judge_prompt(chunk), temperature=0.0, max_tokens=512)
            parsed = extract_json(raw)
            if isinstance(parsed, list):
                for v in parsed:
                    if isinstance(v, dict) and v.get("verdict") in VERDICTS:
                        verdicts.append(
                            {
                                "index": int(v.get("index") or 0),
                                "verdict": v["verdict"],
                                "reason": str(v.get("reason") or "")[:200],
                            }
                        )
        except LLMUnavailable:
            raise
        for j in range(len(chunk)):
            v = next((x for x in verdicts if x["index"] == j + 1), None)
            out.append(v or {"index": j + 1, "verdict": "consistent", "reason": ""})
    return out


def apply_verdicts(conn, pairs: list[dict], verdicts: list[dict], progress=None) -> dict:
    progress = progress or (lambda msg: None)
    stats = {"contradiction": 0, "duplicate": 0, "consistent": 0, "unrelated": 0}
    for p, v in zip(pairs, verdicts):
        stats[v["verdict"]] = stats.get(v["verdict"], 0) + 1
        if v["verdict"] not in ("contradiction", "duplicate"):
            continue
        older_id, newer_id = (p["a"], p["b"]) if p["a"] < p["b"] else (p["b"], p["a"])
        if v["verdict"] == "contradiction":
            conn.execute(
                "UPDATE claims SET status='superseded', superseded_by=?, superseded_reason=? "
                "WHERE id=? AND status='active'",
                (newer_id, f"[audit:contradiction] {v['reason']}", older_id),
            )
            conn.execute("DELETE FROM embeddings WHERE scope='claim' AND ref_id=?", (older_id,))
            progress(f"  ⚡ #{older_id} 被 #{newer_id} 取代（矛盾）：{v['reason']}")
        else:
            conn.execute(
                "UPDATE claims SET status='duplicate', duplicate_of=?, superseded_reason=? "
                "WHERE id=? AND status='active'",
                (older_id, f"[audit:duplicate] {v['reason']}", newer_id),
            )
            conn.execute("DELETE FROM embeddings WHERE scope='claim' AND ref_id=?", (newer_id,))
            progress(f"  ≡ #{newer_id} ≡ #{older_id}（重复）：{v['reason']}")
    conn.commit()
    return stats
