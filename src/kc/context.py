"""Context Builder (proposal §18): Task → entities → claims → sources → pack.

    Task
      ↓ identify entities (name/alias containment)
      ↓ retrieve relevant claims (hybrid)
      ↓ retrieve supporting chunks (hybrid)
      ↓ active memories (personal state, §13)
      ↓ budget-limited assembly
    Task Context Pack (Markdown, unified Sources)

The pack is for EXTERNAL agents (human / OpenCode / cloud LLMs) — kc builds
context, agents execute. Local First, Cloud When Needed.
"""

from __future__ import annotations

from . import graph
from .llm import LLM
from .memory import active_for_context
from .retrieve import fallback_patterns, llm_keywords, search

DEFAULT_MAX_CHARS = 6000
CLAIM_BUDGET_SHARE = 0.5
MEMORY_LIMIT = 8
ENTITY_LIMIT = 12


def identify_entities(conn, text: str) -> list:
    """Entities (incl. aliases) whose name occurs in the text."""
    t = (text or "").lower()
    hits: dict[int, dict] = {}
    rows = conn.execute(
        """SELECT e.id, e.name, e.type, e.doc_count FROM entities e
           UNION ALL
           SELECT e.id, a.alias AS name, e.type, e.doc_count FROM entity_aliases a
           JOIN entities e ON e.id = a.entity_id"""
    ).fetchall()
    for r in rows:
        name = (r["name"] or "").strip()
        if len(name) >= 2 and name.lower() in t:
            ent = graph.resolve(conn, name)
            if ent is None:
                continue
            aliases = [
                a["alias"]
                for a in conn.execute(
                    "SELECT alias FROM entity_aliases WHERE entity_id=?", (ent["id"],)
                )
            ]
            hits[ent["id"]] = {
                "id": ent["id"],
                "name": ent["name"],
                "type": ent["type"],
                "doc_count": ent["doc_count"],
                "aliases": aliases,
            }
    return list(hits.values())[:ENTITY_LIMIT]


def build_context(
    conn,
    llm: LLM | None,
    task: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    include_memory: bool = True,
) -> dict:
    task = (task or "").strip()
    if not task:
        raise ValueError("task 不能为空")

    # 1) keywords (LLM with deterministic fallback → works offline)
    kws = llm_keywords(llm, task) if llm is not None else []
    if not kws:
        kws = fallback_patterns(task)
    query = " ".join(kws) if kws else task

    # 2) entities mentioned in the task (alias-aware)
    entities = identify_entities(conn, task)

    # 3) claims + chunks via hybrid search
    claim_hits = search(conn, query, limit=12, scope="claims", mode="hybrid")
    chunk_hits = search(conn, query, limit=12, scope="chunks", mode="hybrid")
    if not claim_hits and not chunk_hits and query != task:
        claim_hits = search(conn, task, limit=12, scope="claims", mode="hybrid")
        chunk_hits = search(conn, task, limit=12, scope="chunks", mode="hybrid")

    # 4) memories (personal state, clearly separated from knowledge)
    memories = active_for_context(conn, limit=MEMORY_LIMIT) if include_memory else []

    # 5) budgeted assembly
    header = [
        "# Task Context",
        "",
        f"**任务**：{task}",
        "",
        f"**关键词**：{' '.join(kws) if kws else '（未提取）'}",
        "",
    ]
    sections: list[str] = []
    used = sum(len(x) + 1 for x in header)
    src_ids: dict[str, str] = {}  # document_id → title

    if memories:
        lines = ["## 个人记忆（动态状态，非知识）", ""]
        for m in memories:
            exp = f"|至 {m['expires_at'][:10]}" if m["expires_at"] else ""
            lines.append(f"- [{m['kind']}|{m['created_at'][:10]}{exp}] {m['text']}")
        lines.append("")
        block = "\n".join(lines)
        if used + len(block) <= max_chars:  # 预算不够则整段跳过
            sections.append(block)
            used += len(block)

    if entities:
        lines = ["## 关键实体", ""]
        for e in entities:
            alias = f"（别名：{' / '.join(e['aliases'])}）" if e["aliases"] else ""
            lines.append(f"- {e['name']} [{e['type']}]，{e['doc_count']} docs{alias}")
        lines.append("")
        block = "\n".join(lines)
        if used + len(block) <= max_chars:
            sections.append(block)
            used += len(block)

    claim_budget = int(max_chars * CLAIM_BUDGET_SHARE)
    if claim_hits:
        lines = ["## 相关论断", ""]
        n_claim = 0
        for h in claim_hits:
            entry = f"- [c{n_claim + 1}] [{h['type']}|{h['confidence']}] {h['text']}"
            if sum(len(x) + 1 for x in lines) + len(entry) > claim_budget:
                break
            lines.append(entry)
            src_ids.setdefault(h["document_id"], h["title"] or h["document_id"])
            n_claim += 1
        if n_claim:
            lines.append("")
            sections.append("\n".join(lines))
            used += sum(len(x) + 1 for x in lines)

    if chunk_hits:
        lines = ["## 原文摘录", ""]
        block_used = sum(len(x) + 1 for x in lines)
        for h in chunk_hits:
            text = h["text"].replace("\n", " ")
            if len(text) > 400:
                text = text[:400] + "…"
            entry = f"- [s{h['id']}] {text}"
            if used + block_used + len(entry) + 20 > max_chars:
                break  # 摘录是最可裁的部分：预算尽即止
            lines.append(entry)
            block_used += len(entry) + 1
            src_ids.setdefault(h["document_id"], h["title"] or h["document_id"])
        if len(lines) > 2:
            lines.append("")
            sections.append("\n".join(lines))
            used += block_used

    sources = ["## Sources", ""]
    sources.append("论断编号 [cN] 对应上述文档；摘录编号 [sN] 为 chunk id。")
    sources.append("")
    for did, title in src_ids.items():
        sources.append(f"- {did}  {title[:80]}")
    sources.append("")
    sources.append("> 由 `kc context` 生成（预算 {}/{} 字符）。".format(used, max_chars))
    sources.append("")

    pack = "\n".join(header) + "\n" + "\n".join(sections) + "\n".join(sources)
    return {
        "pack": pack,
        "stats": {
            "keywords": kws,
            "entities": len(entities),
            "claims": sum(1 for s in sections if s.startswith("## 相关论断")),
            "memories": len(memories),
            "sources": len(src_ids),
            "chars": len(pack),
            "max_chars": max_chars,
        },
    }
