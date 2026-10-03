"""Retrieval: lexical FTS + LIKE, vector cosine, entity-hop → RRF hybrid.

search modes:
  fts    — V0.1 behaviour (FTS trigram bm25, LIKE fallback)
  hybrid — four recall channels fused with Reciprocal Rank Fusion:
           A. FTS bm25   B. LIKE OR   C. vector cosine   D. entity-hop

ask() additionally formulates keywords via LLM (falls back to deterministic
segmentation), then searches hybrid, then answers with citations.
"""

from __future__ import annotations

import re
import sqlite3

from .llm import LLM, LLMUnavailable
from .util import extract_json
from .vectors import embed_ngram, topk

ASK_SYSTEM = (
    "你是个人知识库问答助手。只依据提供的资料回答；引用资料时标注 [编号]；"
    "资料中没有相关内容就直说没有。回答使用中文，简洁。"
)

KEYWORD_SYSTEM = "你是检索查询优化器。只输出检索关键词，不要任何解释。"

RRF_K = 60


# --------------------------------------------------------- query formulation
def _keyword_prompt(question: str) -> str:
    return (
        "从下面的问题中提取用于知识库检索的关键词（3-8 个，保留专有名词与术语原文，"
        "中文为主，可含英文标识符），用空格分隔，只输出关键词本身。\n\n"
        f"问题：{question}"
    )


def llm_keywords(llm: LLM, question: str) -> list[str]:
    """Semantic query formulation: LLM extracts search keywords from the question."""
    try:
        out = llm.chat(KEYWORD_SYSTEM, _keyword_prompt(question), temperature=0.0, max_tokens=64)
    except LLMUnavailable:
        return []
    toks = [t.strip("，。、；：,.;:!?()[]{}\"'·") for t in re.split(r"[\s,，]+", out or "")]
    seen: set[str] = set()
    kws: list[str] = []
    for t in toks:
        if 2 <= len(t) <= 24 and t not in seen:
            seen.add(t)
            kws.append(t)
    return kws[:8]


def _segments(question: str) -> list[str]:
    return [s for s in re.split(r"[，。？！、；：\s,.?!;:()\[\]{}\"'·—–]+", question) if s]


def fallback_patterns(question: str, max_patterns: int = 8) -> list[str]:
    """Deterministic keyword fallback: punctuation segments; long ones → 4-char windows."""
    pats: list[str] = []
    for seg in _segments(question):
        if 2 <= len(seg) <= 12:
            pats.append(seg)
        elif len(seg) > 12:
            for i in range(0, len(seg) - 3, 2):
                pats.append(seg[i : i + 4])
    out: list[str] = []
    seen: set[str] = set()
    for p in pats:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:max_patterns]


# ------------------------------------------------------------------ channels
def _tokens(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", (query or "").strip()) if t]


def _fts_query(query: str) -> str | None:
    usable = [t for t in _tokens(query) if len(t) >= 3]
    if not usable:
        return None
    return " OR ".join('"' + t.replace('"', " ") + '"' for t in usable)


def _fts_refs(conn, fts_table: str, query: str, limit: int, scope: str) -> list[tuple[str, int]]:
    fts_q = _fts_query(query)
    if not fts_q:
        return []
    sql = (
        f"SELECT rowid AS rid, bm25({fts_table}) AS rank "
        f"FROM {fts_table} WHERE {fts_table} MATCH ? ORDER BY rank LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (fts_q, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(scope, r["rid"]) for r in rows]


def _like_refs(conn, table: str, query: str, limit: int, scope: str) -> list[tuple[str, int]]:
    pats = [t for t in _tokens(query) if len(t) >= 2]
    if not pats:
        return []
    where = " OR ".join(["text LIKE ?"] * len(pats))
    if table == "chunks":
        sql2 = "SELECT id, text FROM chunks WHERE (" + where + ") "
    else:
        sql2 = "SELECT id, text FROM claims WHERE (" + where + ") AND status='active' "
    rows = conn.execute(sql2 + "LIMIT ?", [f"%{p}%" for p in pats] + [limit * 2]).fetchall()
    scored = sorted(rows, key=lambda r: -sum(1 for p in pats if p in r["text"]))
    return [(scope, r["id"]) for r in scored[:limit]]


def _vector_refs(conn, query: str, limit: int) -> list[tuple[str, int]]:
    if not _tokens(query):
        return []
    qvec = embed_ngram(query)
    out: list[tuple[str, int]] = []
    for scope, table in (("chunk", "chunks"), ("claim", "claims")):
        if scope == "claim":
            rows = conn.execute(
                """SELECT e.ref_id, e.vec FROM embeddings e
                   JOIN claims c ON c.id = e.ref_id
                   WHERE e.scope='claim' AND e.model='ngram-v1' AND c.status='active'"""
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ref_id, vec FROM embeddings WHERE scope='chunk' AND model='ngram-v1'"
            ).fetchall()
        if not rows:
            continue
        cands = [(r[0], r[1]) for r in rows]
        out.extend((scope, cid) for _, cid in topk(qvec, cands, limit))
    return out


def _entity_hop_refs(conn, query: str, limit: int) -> list[tuple[str, int]]:
    """Entities named in the query → their documents' claims (+ top chunks)."""
    q = (query or "").lower()
    ents = conn.execute(
        """SELECT e.id, e.name FROM entities e
           UNION ALL SELECT a.entity_id, a.alias FROM entity_aliases a"""
    ).fetchall()
    doc_ids: set[str] = set()
    for r in ents:
        name = (r["name"] or "").strip()
        if len(name) >= 2 and name.lower() in q:
            for d in conn.execute(
                "SELECT document_id FROM entity_mentions WHERE entity_id=?", (r["id"],)
            ).fetchall():
                doc_ids.add(d["document_id"])
    if not doc_ids:
        return []
    ph = ",".join("?" * len(doc_ids))
    claims = conn.execute(
        f"SELECT id FROM claims WHERE document_id IN ({ph}) AND status='active' "
        f"ORDER BY confidence DESC LIMIT ?",
        [*doc_ids, limit],
    ).fetchall()
    chunks = conn.execute(
        f"SELECT id FROM chunks WHERE document_id IN ({ph}) ORDER BY id LIMIT ?",
        [*doc_ids, max(2, limit // 2)],
    ).fetchall()
    return [("claim", r["id"]) for r in claims] + [("chunk", r["id"]) for r in chunks]


# -------------------------------------------------------------------- search
def _fetch_hit(conn, ref: tuple[str, int]) -> dict | None:
    kind, rid = ref
    if kind == "chunk":
        r = conn.execute(
            "SELECT c.id, c.document_id, c.text, d.title FROM chunks c "
            "JOIN documents d ON d.id=c.document_id WHERE c.id=?",
            (rid,),
        ).fetchone()
        if not r:
            return None
        return {
            "kind": "chunk", "id": r["id"], "document_id": r["document_id"],
            "title": r["title"], "text": r["text"], "score": 0.0,
        }
    r = conn.execute(
        "SELECT cl.id, cl.document_id, cl.text, cl.type, cl.confidence, d.title FROM claims cl "
        "JOIN documents d ON d.id=cl.document_id WHERE cl.id=? AND cl.status='active'",
        (rid,),
    ).fetchone()
    if not r:
        return None
    return {
        "kind": "claim", "id": r["id"], "document_id": r["document_id"],
        "title": r["title"], "text": r["text"], "type": r["type"],
        "confidence": r["confidence"], "score": 0.0,
    }


def _fts_search(conn, query: str, limit: int, scope: str) -> list[dict]:
    """V0.1 behaviour: FTS first, LIKE fallback, claim-priority ordering."""
    refs: list[tuple[str, int]] = []
    if scope in ("all", "chunks"):
        refs.extend(_fts_refs(conn, "chunks_fts", query, limit, "chunk"))
    if scope in ("all", "claims"):
        refs.extend(_fts_refs(conn, "claims_fts", query, limit, "claim"))
    if not refs:
        if scope in ("all", "chunks"):
            refs.extend(_like_refs(conn, "chunks", query, limit, "chunk"))
        if scope in ("all", "claims"):
            refs.extend(_like_refs(conn, "claims", query, limit, "claim"))
    hits = [h for h in (_fetch_hit(conn, ref) for ref in refs) if h]
    order = {"claim": 0, "chunk": 1}
    hits.sort(key=lambda h: order[h["kind"]])
    return hits[:limit]


def _hybrid_search(conn, query: str, limit: int, scope: str) -> list[dict]:
    channels: list[list[tuple[str, int]]] = []
    if scope in ("all", "chunks"):
        channels.append(_fts_refs(conn, "chunks_fts", query, limit, "chunk"))
    if scope in ("all", "claims"):
        channels.append(_fts_refs(conn, "claims_fts", query, limit, "claim"))
    like_refs: list[tuple[str, int]] = []
    if scope in ("all", "chunks"):
        like_refs.extend(_like_refs(conn, "chunks", query, limit, "chunk"))
    if scope in ("all", "claims"):
        like_refs.extend(_like_refs(conn, "claims", query, limit, "claim"))
    channels.append(like_refs)
    channels.append(_vector_refs(conn, query, limit))
    channels.append(_entity_hop_refs(conn, query, limit))

    rrf: dict[tuple[str, int], float] = {}
    for channel in channels:
        for rank, ref in enumerate(channel, 1):
            rrf[ref] = rrf.get(ref, 0.0) + 1.0 / (RRF_K + rank)
    ranked = sorted(rrf.items(), key=lambda kv: -kv[1])
    hits: list[dict] = []
    for ref, score in ranked:
        h = _fetch_hit(conn, ref)
        if h:
            h["score"] = round(score, 5)
            h["channels"] = sum(1 for ch in channels if ref in ch)
            hits.append(h)
        if len(hits) >= limit:
            break
    return hits


def search(conn, query: str, limit: int = 10, scope: str = "all", mode: str = "hybrid") -> list[dict]:
    if mode == "fts":
        return _fts_search(conn, query, limit, scope)
    return _hybrid_search(conn, query, limit, scope)


# ------------------------------------------------------------------- rerank
RERANK_SYSTEM = "你是检索结果相关性评估器。只输出一个 JSON 数组，不要任何解释。"


def rerank(llm: LLM, query: str, hits: list[dict], top_n: int = 6) -> list[dict]:
    """Pointwise LLM scoring of candidate hits (one batched call), 0–10 each."""
    if not hits:
        return []
    listing = []
    for i, h in enumerate(hits, 1):
        text = h["text"].replace("\n", " ")[:200]
        listing.append(f"({i}) [{h['kind']}] {text}")
    prompt = (
        f"查询：{query}\n\n候选资料：\n" + "\n".join(listing) +
        "\n\n逐条评估每份资料对回答该查询的有用程度（0-10 分，10 为完全相关，0 为无关）。"
        '输出 JSON 数组，每项 {"index": 编号, "score": 分数}。'
    )
    try:
        raw = llm.chat(RERANK_SYSTEM, prompt, temperature=0.0, max_tokens=256)
    except LLMUnavailable:
        return hits[:top_n]
    parsed = extract_json(raw)
    scores: dict[int, float] = {}
    if isinstance(parsed, list):
        for v in parsed:
            if isinstance(v, dict) and isinstance(v.get("index"), (int, float)):
                try:
                    scores[int(v["index"])] = max(0.0, min(10.0, float(v.get("score", 0))))
                except (TypeError, ValueError):
                    continue
    scored = []
    for i, h in enumerate(hits, 1):
        scored.append((scores.get(i, 0.0), h))
    scored.sort(key=lambda x: -x[0])
    out = [h for s, h in scored[:top_n] if s > 0]
    return out or hits[:top_n]


# ---------------------------------------------------------------------- ask
def ask(
    conn,
    llm: LLM,
    question: str,
    limit: int = 6,
    max_context_chars: int = 3500,
    rerank_top: int | None = None,
) -> dict | None:
    kws = llm_keywords(llm, question)
    if not kws:
        kws = fallback_patterns(question)
    hits = search(conn, " ".join(kws), limit=limit * 2, mode="hybrid") if kws else []
    if not hits:  # last resort: verbatim substring search of the raw question
        hits = search(conn, question, limit=limit * 2, mode="hybrid")
    if not hits:
        return None
    if rerank_top:
        hits = rerank(llm, question, hits, top_n=rerank_top)
    else:
        hits = hits[:limit]
    lines: list[str] = []
    sources: list[dict] = []
    total = 0
    for i, h in enumerate(hits, 1):
        text = h["text"]
        if len(text) > 500:
            text = text[:500] + "…"
        entry = f"[{i}] ({h['kind']}#{h['id']}｜{h['title'] or h['document_id']}) {text}"
        if total + len(entry) > max_context_chars:
            break
        lines.append(entry)
        total += len(entry)
        sources.append(h)
    context = "\n\n".join(lines)
    user = f"问题：{question}\n\n资料：\n{context}\n\n请依据以上资料回答，引用标注 [编号]。"
    answer = llm.chat(ASK_SYSTEM, user, temperature=0.3, max_tokens=512)
    return {"answer": answer.strip(), "sources": sources, "context_chars": total, "keywords": kws}
