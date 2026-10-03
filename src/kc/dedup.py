"""Claim lifecycle: duplicate / update / contradiction / new (proposal §16–17).

Candidates come from exact-norm hit → vector cosine → FTS trigram.
LLM judges in batches (several claims per call to spare NPU throughput).
Superseded/duplicate claims stay in the DB — knowledge evolution is a feature.
"""

from __future__ import annotations

import re

from .llm import LLM, LLMUnavailable
from .util import extract_json, norm_text
from .vectors import embed_ngram, topk

JUDGE_SYSTEM = "你是知识库论断判重助手。只输出一个 JSON 数组，不要任何解释。"

VERDICTS = {"duplicate", "update", "contradiction", "new"}


def _fts_tokens(text: str, min_len: int = 3) -> list[str]:
    toks = [t for t in re.split(r"[\s，。、；：,.:;()（）]+", text or "") if len(t) >= min_len]
    return toks[:8]


def find_candidates(conn, text: str, exclude_ids: set[int], k: int = 3) -> list[dict]:
    """Active claims similar to `text`: vector cosine + FTS, excluding self."""
    out: dict[int, dict] = {}

    vec = embed_ngram(text)
    rows = conn.execute(
        """SELECT e.ref_id, e.vec FROM embeddings e
           JOIN claims c ON c.id = e.ref_id
           WHERE e.scope='claim' AND e.model='ngram-v1' AND c.status='active'"""
    ).fetchall()
    if rows:
        cands = [(r["ref_id"], r["vec"]) for r in rows if r["ref_id"] not in exclude_ids]
        for score, cid in topk(vec, cands, k):
            out[cid] = {"id": cid, "score": round(score, 3), "via": "vector"}

    toks = _fts_tokens(text)
    if toks:
        fts_q = " OR ".join('"' + t.replace('"', " ") + '"' for t in toks)
        try:
            rows = conn.execute(
                """SELECT cl.id, cl.text, bm25(claims_fts) AS rank
                   FROM claims_fts JOIN claims cl ON cl.id = claims_fts.rowid
                   WHERE claims_fts MATCH ? AND cl.status='active'
                   ORDER BY rank LIMIT ?""",
                (fts_q, k),
            ).fetchall()
            for r in rows:
                if r["id"] in exclude_ids:
                    continue
                if r["id"] in out:
                    out[r["id"]]["via"] = "vector+fts"
                else:
                    out[r["id"]] = {"id": r["id"], "score": round(-r["rank"], 3), "via": "fts"}
        except Exception:  # noqa: BLE001 — FTS quirks must not break dedup
            pass

    ids = list(out)
    for cid in ids:
        row = conn.execute("SELECT text FROM claims WHERE id=?", (cid,)).fetchone()
        if row:
            out[cid]["text"] = row["text"]
        else:
            out.pop(cid)
    ranked = sorted(out.values(), key=lambda c: -c.get("score", 0))
    return ranked[:k]


def _judge_prompt(groups: list[dict]) -> str:
    parts = []
    for i, g in enumerate(groups, 1):
        cands = "\n".join(f"  ({j}) {c['text']}" for j, c in enumerate(g["candidates"], 1))
        parts.append(
            f"第{i}组：\n新论断：{g['text']}\n候选论断：\n{cands if cands else '  （无候选）'}"
        )
    listing = "\n\n".join(parts)
    return f"""判断每组中新论断与候选论断的关系。判定选项：
- duplicate：与某候选意思相同（新论断记为重复）
- update：新论断是某候选的更新或更具体版本（新论断保留，旧候选被取代）
- contradiction：新论断与某候选矛盾（新论断保留，旧候选被取代）
- new：与所有候选都不同

输出 JSON 数组，每项：{{"index": 组序号, "verdict": "duplicate|update|contradiction|new", "target": 候选编号或0, "reason": "一句话"}}

{listing}"""


def judge_batch(llm: LLM, groups: list[dict]) -> list[dict]:
    """One LLM call for up to N groups. Returns per-group verdict dicts."""
    raw = llm.chat(JUDGE_SYSTEM, _judge_prompt(groups), temperature=0.0, max_tokens=512)
    parsed = extract_json(raw)
    verdicts: list[dict] = []
    if isinstance(parsed, list):
        for v in parsed:
            if isinstance(v, dict) and v.get("verdict") in VERDICTS:
                verdicts.append(
                    {
                        "index": int(v.get("index") or 0),
                        "verdict": v["verdict"],
                        "target": int(v.get("target") or 0),
                        "reason": str(v.get("reason") or "")[:200],
                    }
                )
    # align: default 'new' for groups without a usable verdict
    out: list[dict] = []
    for i in range(len(groups)):
        v = next((x for x in verdicts if x["index"] == i + 1), None)
        out.append(v or {"index": i + 1, "verdict": "new", "target": 0, "reason": ""})
    return out


def apply_verdict(conn, claim_id: int, verdict: dict, candidates: list[dict]) -> str:
    """Persist a verdict; returns a short human-readable outcome."""
    if verdict["verdict"] == "new":
        conn.execute(
            "UPDATE claims SET dedup_checked=1 WHERE id=?", (claim_id,)
        )
        return "new"
    target = None
    if 1 <= verdict["target"] <= len(candidates):
        target = candidates[verdict["target"] - 1]
    if target is None:  # verdict named no valid candidate → treat as new
        conn.execute("UPDATE claims SET dedup_checked=1 WHERE id=?", (claim_id,))
        return "new"
    if verdict["verdict"] == "duplicate":
        conn.execute(
            "UPDATE claims SET status='duplicate', duplicate_of=?, dedup_checked=1, "
            "superseded_reason=? WHERE id=?",
            (target["id"], verdict["reason"], claim_id),
        )
        conn.execute("DELETE FROM embeddings WHERE scope='claim' AND ref_id=?", (claim_id,))
        return f"duplicate → #{target['id']}"
    # update / contradiction: new claim wins, old one is superseded (history kept)
    conn.execute(
        "UPDATE claims SET status='superseded', superseded_by=?, superseded_reason=? "
        "WHERE id=? AND status='active'",
        (claim_id, f"[{verdict['verdict']}] {verdict['reason']}", target["id"]),
    )
    conn.execute("DELETE FROM embeddings WHERE scope='claim' AND ref_id=?", (target["id"],))
    conn.execute("UPDATE claims SET dedup_checked=1 WHERE id=?", (claim_id,))
    return f"{verdict['verdict']} → 取代 #{target['id']}"


def dedup_claims(
    conn,
    llm: LLM | None,
    claim_ids: list[int],
    progress=None,
    batch_groups: int = 4,
) -> dict:
    """Dedup specific claims (compile-time hook). Exact-norm shortcut first."""
    progress = progress or (lambda msg: None)
    stats = {"checked": 0, "duplicate": 0, "update": 0, "contradiction": 0, "new": 0, "skipped": 0}
    pending: list[dict] = []
    for cid in claim_ids:
        row = conn.execute(
            "SELECT id, text, norm FROM claims WHERE id=? AND status='active'", (cid,)
        ).fetchone()
        if not row:
            stats["skipped"] += 1
            continue
        stats["checked"] += 1
        exact = conn.execute(
            "SELECT id FROM claims WHERE norm=? AND status='active' AND id<>?",
            (row["norm"], cid),
        ).fetchone()
        if exact:
            conn.execute(
                "UPDATE claims SET status='duplicate', duplicate_of=?, dedup_checked=1 WHERE id=?",
                (exact["id"], cid),
            )
            conn.execute("DELETE FROM embeddings WHERE scope='claim' AND ref_id=?", (cid,))
            stats["duplicate"] += 1
            progress(f"  ⤳ claim#{cid} ≡ #{exact['id']}（norm 精确重复）")
            continue
        if llm is None:
            continue  # deterministic pass done; LLM pass skipped
        pending.append({"id": cid, "text": row["text"]})

    if llm is None or not pending:
        conn.commit()
        return stats

    for i in range(0, len(pending), batch_groups):
        batch = pending[i : i + batch_groups]
        for g in batch:
            g["candidates"] = find_candidates(
                conn, g["text"], exclude_ids={x["id"] for x in batch}
            )
        try:
            verdicts = judge_batch(llm, batch)
        except LLMUnavailable as e:
            progress(f"  ⚠ 判重 LLM 不可用，剩余 claim 保持未检状态：{e}")
            break
        for g, v in zip(batch, verdicts):
            outcome = apply_verdict(conn, g["id"], v, g["candidates"])
            if outcome.startswith("duplicate"):
                stats["duplicate"] += 1
            elif outcome.startswith("update"):
                stats["update"] += 1
            elif outcome.startswith("contradiction"):
                stats["contradiction"] += 1
            else:
                stats["new"] += 1
            progress(f"  ⤳ claim#{g['id']}: {outcome}" + (f"（{v['reason']}）" if v["reason"] else ""))
    conn.commit()
    return stats
