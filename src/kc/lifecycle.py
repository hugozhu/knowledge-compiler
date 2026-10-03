"""Document removal: cascade-clean, evolution-aware, re-add friendly.

Semantics (docs/v0.3-plan.md §2):
  - FK cascade wipes chunks/claims/mentions/relations; embeddings swept manually.
  - Cross-doc claims whose superseded_by/duplicate_of pointed INTO this doc are
    restored to active (that knowledge is still valid).
  - Orphan entities (lost their last mention) are reclaimed; doc_count refreshed.
  - compile_log rows dropped → the same file can be re-compiled after re-add.
  - raw/ is immutable by default; --purge-raw deletes it explicitly.
"""

from __future__ import annotations

from pathlib import Path


def remove_document(conn, cfg, doc_id: str, purge_raw: bool = False, progress=None) -> dict:
    progress = progress or (lambda msg: None)
    row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        raise ValueError(f"未找到文档 {doc_id}")
    title, sha = row["title"], row["sha256"]

    # 1) restore cross-doc claims whose lifecycle pointers target this doc's claims
    doc_claim_ids = [
        r["id"] for r in conn.execute("SELECT id FROM claims WHERE document_id=?", (doc_id,))
    ]
    restored = 0
    if doc_claim_ids:
        ph = ",".join("?" * len(doc_claim_ids))
        cur = conn.execute(
            f"UPDATE claims SET status='active', superseded_by=NULL, superseded_reason=NULL, "
            f"dedup_checked=1 WHERE status='superseded' AND superseded_by IN ({ph})",
            doc_claim_ids,
        )
        restored += cur.rowcount
        cur = conn.execute(
            f"UPDATE claims SET status='active', duplicate_of=NULL "
            f"WHERE status='duplicate' AND duplicate_of IN ({ph})",
            doc_claim_ids,
        )
        restored += cur.rowcount

    # 2) delete document row (FK cascade: chunks/claims/mentions/relations)
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    # 3) embeddings carry no FK → manual orphan sweep
    conn.execute(
        "DELETE FROM embeddings WHERE scope='chunk' AND ref_id NOT IN (SELECT id FROM chunks)"
    )
    conn.execute(
        "DELETE FROM embeddings WHERE scope='claim' AND ref_id NOT IN (SELECT id FROM claims)"
    )

    # 4) orphan entities + doc_count refresh
    orphan_names: list[str] = []
    for r in conn.execute(
        "SELECT id, name FROM entities WHERE id NOT IN "
        "(SELECT DISTINCT entity_id FROM entity_mentions)"
    ).fetchall():
        conn.execute("DELETE FROM entity_aliases WHERE entity_id=?", (r["id"],))
        conn.execute("DELETE FROM entities WHERE id=?", (r["id"],))
        orphan_names.append(r["name"])
    conn.execute(
        "UPDATE entities SET doc_count="
        "(SELECT COUNT(*) FROM entity_mentions m WHERE m.entity_id=entities.id)"
    )

    # 5) compile_log: drop so re-add of the same file compiles again
    conn.execute("DELETE FROM compile_log WHERE document_id=? OR sha256=?", (doc_id, sha))
    conn.commit()

    # 6) derived artifacts
    md = cfg.dir("documents") / f"{doc_id}.md"
    md.unlink(missing_ok=True)
    progress(f"已删除 documents/{doc_id}.md")

    raw_path = Path(row["source_path"]) if row["source_path"] else None
    raw_removed = False
    if purge_raw and raw_path and raw_path.exists():
        raw_path.unlink()
        raw_removed = True
        progress(f"已删除 raw 原件 {raw_path.name}（--purge-raw）")
    elif raw_path and raw_path.exists():
        progress(f"raw 原件保留（immutable）：{raw_path.name}")

    return {
        "doc_id": doc_id,
        "title": title,
        "restored": restored,
        "orphan_entities": orphan_names,
        "raw_kept": not raw_removed,
        "raw_path": str(raw_path) if raw_path else "",
    }
