"""Memory layer (proposal §13): dynamic personal context — NOT knowledge.

Differences from claims:
  - has a lifecycle (active/done/expired) and an expires_at
  - never compiled into the knowledge base
  - injected into ask()/context packs as clearly-marked personal state
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .util import now_iso

KINDS = {"project", "decision", "preference", "task", "discussion", "goal"}
MAX_ASK_MEMORIES = 5


def add_memory(
    conn,
    text: str,
    kind: str = "task",
    source: str | None = None,
    confidence: float = 0.8,
    expires_days: float | None = None,
) -> int:
    text = (text or "").strip()
    if not text:
        raise ValueError("memory text 不能为空")
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {'/'.join(sorted(KINDS))}，收到 {kind!r}")
    now = now_iso()
    expires_at = None
    if expires_days is not None:
        expires_at = (
            datetime.now().astimezone() + timedelta(days=float(expires_days))
        ).isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO memories(kind, text, source, confidence, status, created_at, updated_at, expires_at)
           VALUES(?,?,?,?, 'active', ?, ?, ?)""",
        (kind, text[:500], source, confidence, now, now, expires_at),
    )
    conn.commit()
    return cur.lastrowid


def expire_stale(conn) -> int:
    """Lazy expiry: mark overdue active memories as expired. Returns count."""
    now = now_iso()
    cur = conn.execute(
        "UPDATE memories SET status='expired', updated_at=? "
        "WHERE status='active' AND expires_at IS NOT NULL AND expires_at < ?",
        (now, now),
    )
    conn.commit()
    return cur.rowcount


def list_memories(conn, active_only: bool = True, kind: str | None = None) -> list:
    expire_stale(conn)
    where, params = [], []
    if active_only:
        where.append("status='active'")
    if kind:
        where.append("kind=?")
        params.append(kind)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return conn.execute(
        f"SELECT * FROM memories {clause} ORDER BY updated_at DESC, id DESC", params
    ).fetchall()


def complete_memory(conn, mem_id: int) -> None:
    cur = conn.execute(
        "UPDATE memories SET status='done', updated_at=? WHERE id=?", (now_iso(), mem_id)
    )
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError(f"未找到 memory#{mem_id}")


def active_for_context(conn, limit: int = MAX_ASK_MEMORIES) -> list:
    """Most recent active memories for ask()/context packs."""
    return list_memories(conn, active_only=True)[:limit]
