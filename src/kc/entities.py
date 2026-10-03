"""Entity resolution: canonical entities + aliases, compile-time auto-linking,
LLM-assisted merge suggestions, and explicit merge."""

from __future__ import annotations

import json

from .llm import LLM, LLMUnavailable
from .util import extract_json, norm_text

SUGGEST_SYSTEM = "你是实体消歧助手。只输出 JSON，不要任何解释。"


def resolve_entity(conn, name: str, etype: str = "other") -> int:
    """Find entity by exact norm or alias → reuse; otherwise create. Returns id."""
    norm = norm_text(name)
    if not norm:
        raise ValueError("empty entity name")
    row = conn.execute("SELECT id FROM entities WHERE norm=?", (norm,)).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "SELECT entity_id FROM entity_aliases WHERE alias_norm=?", (norm,)
    ).fetchone()
    if row:
        return row["entity_id"]
    conn.execute(
        "INSERT INTO entities(name, norm, type) VALUES(?,?,?) "
        "ON CONFLICT(norm) DO UPDATE SET name=excluded.name",
        (name, norm, etype),
    )
    return conn.execute("SELECT id FROM entities WHERE norm=?", (norm,)).fetchone()["id"]


def link_mention(conn, entity_id: int, document_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entity_mentions(entity_id, document_id) VALUES(?,?)",
        (entity_id, document_id),
    )
    cnt = conn.execute(
        "SELECT COUNT(*) AS c FROM entity_mentions WHERE entity_id=?", (entity_id,)
    ).fetchone()["c"]
    conn.execute("UPDATE entities SET doc_count=? WHERE id=?", (cnt, entity_id))


def add_alias(conn, entity_id: int, alias: str) -> None:
    norm = norm_text(alias)
    if not norm:
        return
    hit = conn.execute("SELECT entity_id FROM entity_aliases WHERE alias_norm=?", (norm,)).fetchone()
    if hit:
        if hit["entity_id"] != entity_id:
            raise ValueError(f"alias '{alias}' already bound to entity #{hit['entity_id']}")
        return
    owner = conn.execute(
        "SELECT id FROM entities WHERE norm=? AND id<>?", (norm, entity_id)
    ).fetchone()
    if owner:
        raise ValueError(f"alias '{alias}' collides with entity #{owner['id']} 的 norm")
    conn.execute(
        "INSERT INTO entity_aliases(alias_norm, alias, entity_id) VALUES(?,?,?)",
        (norm, alias, entity_id),
    )


def merge_entities(conn, target_id: int, source_id: int) -> dict:
    """Fold source entity into target: mentions move, aliases move, name becomes alias."""
    if target_id == source_id:
        raise ValueError("cannot merge an entity into itself")
    tgt = conn.execute("SELECT * FROM entities WHERE id=?", (target_id,)).fetchone()
    src = conn.execute("SELECT * FROM entities WHERE id=?", (source_id,)).fetchone()
    if not tgt or not src:
        raise ValueError("entity id 不存在")
    moved_mentions = 0
    for m in conn.execute(
        "SELECT document_id FROM entity_mentions WHERE entity_id=?", (source_id,)
    ).fetchall():
        conn.execute(
            "INSERT OR IGNORE INTO entity_mentions(entity_id, document_id) VALUES(?,?)",
            (target_id, m["document_id"]),
        )
        moved_mentions += 1
    conn.execute("DELETE FROM entity_mentions WHERE entity_id=?", (source_id,))
    src_aliases = [
        a["alias"] for a in conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id=?", (source_id,)
        ).fetchall()
    ]
    conn.execute("DELETE FROM entity_aliases WHERE entity_id=?", (source_id,))
    conn.execute("DELETE FROM entities WHERE id=?", (source_id,))
    for alias in src_aliases:
        if norm_text(alias) == norm_text(tgt["name"]):
            continue
        try:
            add_alias(conn, target_id, alias)
        except ValueError:
            pass  # alias collides elsewhere; skip it
    add_alias(conn, target_id, src["name"])
    cnt = conn.execute(
        "SELECT COUNT(*) AS c FROM entity_mentions WHERE entity_id=?", (target_id,)
    ).fetchone()["c"]
    conn.execute("UPDATE entities SET doc_count=? WHERE id=?", (cnt, target_id))
    conn.commit()
    return {
        "target": tgt["name"],
        "absorbed": src["name"],
        "mentions_moved": moved_mentions,
        "doc_count": cnt,
    }


def suggest_groups(llm: LLM, names: list[str], batch: int = 60) -> list[dict]:
    """LLM grouping suggestions: [{'canonical':..., 'merge': [...], 'reason':...}]."""
    out: list[dict] = []
    for i in range(0, len(names), batch):
        part = names[i : i + batch]
        if len(part) < 2:
            continue
        listing = "\n".join(f"- {n}" for n in part)
        prompt = (
            "以下是个人知识库的实体名列表。找出**可能是同一实体不同称呼**的分组"
            "（同名缩写、中英文别名、新旧名称等）。只对高置信的分组输出。\n"
            '输出 JSON 数组，每项 {"canonical": "应保留的名称", "merge": ["并入的名称"], "reason": "简短理由"}；'
            "没有可合并的就输出 []。\n\n"
            f"实体列表：\n{listing}"
        )
        try:
            content = llm.chat(SUGGEST_SYSTEM, prompt, temperature=0.0, max_tokens=512)
        except LLMUnavailable:
            break
        raw = extract_json(content)
        if isinstance(raw, list):
            for g in raw:
                if isinstance(g, dict) and g.get("canonical") and g.get("merge"):
                    out.append(
                        {
                            "canonical": str(g["canonical"])[:80],
                            "merge": [str(m)[:80] for m in g["merge"] if str(m).strip()],
                            "reason": str(g.get("reason") or "")[:120],
                        }
                    )
    return out
