"""Retrieval: FTS5 (trigram, bm25) with LIKE fallback; ask() context builder.

ask() 流程（Query Formulation）：
  问题 --LLM--> 检索关键词（失败则确定性分段兜底）--> search() --> 上下文 --> 本地模型作答
"""

from __future__ import annotations

import re
import sqlite3

from .llm import LLM, LLMUnavailable

ASK_SYSTEM = (
    "你是个人知识库问答助手。只依据提供的资料回答；引用资料时标注 [编号]；"
    "资料中没有相关内容就直说没有。回答使用中文，简洁。"
)

KEYWORD_SYSTEM = "你是检索查询优化器。只输出检索关键词，不要任何解释。"


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


# ------------------------------------------------------------------ search
def _tokens(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", (query or "").strip()) if t]


def _fts_query(query: str) -> str | None:
    usable = [t for t in _tokens(query) if len(t) >= 3]
    if not usable:
        return None
    return " OR ".join('"' + t.replace('"', " ") + '"' for t in usable)


def _fts_rows(conn, fts_table: str, fts_q: str, limit: int) -> list[sqlite3.Row]:
    sql = (
        f"SELECT rowid AS rid, bm25({fts_table}) AS rank "
        f"FROM {fts_table} WHERE {fts_table} MATCH ? ORDER BY rank LIMIT ?"
    )
    try:
        return conn.execute(sql, (fts_q, limit)).fetchall()
    except sqlite3.OperationalError:
        return []


def _like_scored(conn, table: str, patterns: list[str], limit: int) -> list[tuple[float, sqlite3.Row]]:
    """OR-match fallback, ranked by number of matched patterns (more = better)."""
    pats = [p for p in patterns if p]
    if not pats:
        return []
    where = " OR ".join(["text LIKE ?"] * len(pats))
    params = [f"%{p}%" for p in pats]
    if table == "chunks":
        sql = (
            "SELECT c.id AS rid, c.document_id, d.title, c.text FROM chunks c "
            f"JOIN documents d ON d.id=c.document_id WHERE ({where}) "
        )
    else:
        sql = (
            "SELECT cl.id AS rid, cl.document_id, d.title, cl.text, cl.type, cl.confidence "
            "FROM claims cl JOIN documents d ON d.id=cl.document_id "
            f"WHERE ({where}) AND cl.status='active' "
        )
    rows = conn.execute(sql + "LIMIT ?", [*params, limit * 3]).fetchall()
    scored = [(-sum(1 for p in pats if p in r["text"]), r) for r in rows]
    scored.sort(key=lambda x: x[0])
    return scored[:limit]


def _search_chunks(conn, query: str, limit: int) -> list[dict]:
    fts_q = _fts_query(query)
    if fts_q:
        rows = _fts_rows(conn, "chunks_fts", fts_q, limit)
        if rows:
            rids = [r["rid"] for r in rows]
            rank = {r["rid"]: r["rank"] for r in rows}
            ph = ",".join("?" * len(rids))
            hits = []
            for r in conn.execute(
                f"SELECT c.id, c.document_id, c.text, d.title FROM chunks c "
                f"JOIN documents d ON d.id=c.document_id WHERE c.id IN ({ph})",
                rids,
            ):
                hits.append(
                    {
                        "kind": "chunk",
                        "id": r["id"],
                        "document_id": r["document_id"],
                        "title": r["title"],
                        "text": r["text"],
                        "score": rank.get(r["id"], 999),
                    }
                )
            return hits
    return [
        {
            "kind": "chunk",
            "id": r["rid"],
            "document_id": r["document_id"],
            "title": r["title"],
            "text": r["text"],
            "score": score,
        }
        for score, r in _like_scored(conn, "chunks", _tokens(query) or _segments(query), limit)
    ]


def _search_claims(conn, query: str, limit: int) -> list[dict]:
    fts_q = _fts_query(query)
    if fts_q:
        rows = _fts_rows(conn, "claims_fts", fts_q, limit)
        if rows:
            rids = [r["rid"] for r in rows]
            rank = {r["rid"]: r["rank"] for r in rows}
            ph = ",".join("?" * len(rids))
            hits = []
            for r in conn.execute(
                f"SELECT cl.id, cl.document_id, cl.text, cl.type, cl.confidence, d.title FROM claims cl "
                f"JOIN documents d ON d.id=cl.document_id WHERE cl.id IN ({ph}) AND cl.status='active'",
                rids,
            ):
                hits.append(
                    {
                        "kind": "claim",
                        "id": r["id"],
                        "document_id": r["document_id"],
                        "title": r["title"],
                        "text": r["text"],
                        "type": r["type"],
                        "confidence": r["confidence"],
                        "score": rank.get(r["id"], 999),
                    }
                )
            return hits
    out = []
    for score, r in _like_scored(conn, "claims", _tokens(query) or _segments(query), limit):
        out.append(
            {
                "kind": "claim",
                "id": r["rid"],
                "document_id": r["document_id"],
                "title": r["title"],
                "text": r["text"],
                "type": r["type"],
                "confidence": r["confidence"],
                "score": score,
            }
        )
    return out


def search(conn, query: str, limit: int = 10, scope: str = "all") -> list[dict]:
    hits: list[dict] = []
    if scope in ("all", "chunks"):
        hits.extend(_search_chunks(conn, query, limit))
    if scope in ("all", "claims"):
        hits.extend(_search_claims(conn, query, limit))
    order = {"claim": 0, "chunk": 1}
    hits.sort(key=lambda h: (h["score"], order[h["kind"]]))
    return hits[:limit]


# -------------------------------------------------------------------- ask
def ask(conn, llm: LLM, question: str, limit: int = 6, max_context_chars: int = 3500) -> dict | None:
    kws = llm_keywords(llm, question)
    if not kws:
        kws = fallback_patterns(question)
    hits = search(conn, " ".join(kws), limit=limit) if kws else []
    if not hits:  # last resort: verbatim substring search of the raw question
        hits = search(conn, question, limit=limit)
    if not hits:
        return None
    lines: list[str] = []
    sources: list[dict] = []
    total = 0
    for i, h in enumerate(hits, 1):
        text = h["text"]
        if len(text) > 500:
            text = text[:500] + "…"
        entry = f"[{i}] ({h['kind']}｜{h['title'] or h['document_id']}) {text}"
        if total + len(entry) > max_context_chars:
            break
        lines.append(entry)
        total += len(entry)
        sources.append(h)
    context = "\n\n".join(lines)
    user = f"问题：{question}\n\n资料：\n{context}\n\n请依据以上资料回答，引用标注 [编号]。"
    answer = llm.chat(ASK_SYSTEM, user, temperature=0.3, max_tokens=512)
    return {"answer": answer.strip(), "sources": sources, "context_chars": total, "keywords": kws}
