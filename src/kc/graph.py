"""Lightweight knowledge-graph views over SQLite (Principle 9: no Graph DB).

Relations extracted by the IR are name-based (not FK); entity resolution maps
aliases so neighborhood lookups match "Qwen" / "通义千问" alike.
"""

from __future__ import annotations

from .util import norm_text


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve(conn, query: str):
    """Entity by exact norm → alias → best LIKE match. Returns row or None."""
    q = norm_text(query)
    if q:
        row = conn.execute("SELECT * FROM entities WHERE norm=?", (q,)).fetchone()
        if row:
            return row
        row = conn.execute(
            "SELECT e.* FROM entities e JOIN entity_aliases a ON a.entity_id=e.id "
            "WHERE a.alias_norm=?",
            (q,),
        ).fetchone()
        if row:
            return row
    return conn.execute(
        "SELECT * FROM entities WHERE name LIKE ? ESCAPE '\\' ORDER BY doc_count DESC LIMIT 1",
        (f"%{_like(query)}%",),
    ).fetchone()


def names_of(conn, entity_row) -> list[str]:
    names = [entity_row["name"]]
    names.extend(
        r["alias"]
        for r in conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id=?", (entity_row["id"],)
        )
    )
    return names


def overview(conn) -> dict:
    def count(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    out = {
        "entities": count("SELECT COUNT(*) FROM entities"),
        "relations": count("SELECT COUNT(*) FROM relations"),
        "active_claims": count("SELECT COUNT(*) FROM claims WHERE status='active'"),
        "superseded": count("SELECT COUNT(*) FROM claims WHERE status='superseded'"),
        "duplicates": count("SELECT COUNT(*) FROM claims WHERE status='duplicate'"),
        "documents": count("SELECT COUNT(*) FROM documents"),
        "predicates": [
            (r["predicate"], r["c"])
            for r in conn.execute(
                "SELECT predicate, COUNT(*) AS c FROM relations "
                "GROUP BY predicate ORDER BY c DESC LIMIT 8"
            )
        ],
        "top_entities": [
            (r["name"], r["doc_count"])
            for r in conn.execute(
                "SELECT name, doc_count FROM entities ORDER BY doc_count DESC, id LIMIT 8"
            )
        ],
        "degree": [
            (r["name"], r["c"])
            for r in conn.execute(
                "SELECT name, COUNT(*) AS c FROM ("
                "  SELECT subject AS name FROM relations "
                "  UNION ALL SELECT object AS name FROM relations) "
                "GROUP BY name ORDER BY c DESC LIMIT 8"
            )
        ],
    }
    return out


def neighborhood(conn, entity_row) -> dict:
    ent = entity_row
    names = names_of(conn, ent)
    out_rels, in_rels = [], []
    for field, sink in (("subject", "object"), ("object", "subject")):
        conds = " OR ".join([f"{field} LIKE ? ESCAPE '\\'"] * len(names))
        params = [f"%{_like(n)}%" for n in names]
        rows = conn.execute(
            f"SELECT subject, predicate, object FROM relations WHERE ({conds}) LIMIT 12", params
        ).fetchall()
        for r in rows:
            (out_rels if field == "subject" else in_rels).append(
                (r["subject"], r["predicate"], r["object"])
            )

    co_mentions = [
        (r["name"], r["docs"])
        for r in conn.execute(
            """SELECT e2.name AS name, COUNT(DISTINCT m2.document_id) AS docs
               FROM entity_mentions m1
               JOIN entity_mentions m2 ON m2.document_id=m1.document_id AND m2.entity_id<>m1.entity_id
               JOIN entities e2 ON e2.id=m2.entity_id
               WHERE m1.entity_id=?
               GROUP BY m2.entity_id ORDER BY docs DESC LIMIT 8""",
            (ent["id"],),
        )
    ]

    docs = [
        (r["id"], r["title"])
        for r in conn.execute(
            """SELECT d.id, d.title FROM documents d
               JOIN entity_mentions m ON m.document_id=d.id
               WHERE m.entity_id=? ORDER BY d.compiled_at DESC""",
            (ent["id"],),
        )
    ]

    claim_conds = " OR ".join(["text LIKE ? ESCAPE '\\'"] * len(names))
    claim_params = [f"%{_like(n)}%" for n in names]
    claims = [
        (r["id"], r["type"], r["confidence"], r["text"], r["title"])
        for r in conn.execute(
            f"""SELECT cl.id, cl.type, cl.confidence, cl.text, d.title FROM claims cl
                JOIN documents d ON d.id=cl.document_id
                WHERE ({claim_conds}) AND cl.status='active'
                ORDER BY cl.confidence DESC LIMIT 8""",
            claim_params,
        )
    ]

    return {
        "entity": dict(ent),
        "aliases": [n for n in names[1:]],
        "out_rels": out_rels,
        "in_rels": in_rels,
        "co_mentions": co_mentions,
        "docs": docs,
        "claims": claims,
    }
