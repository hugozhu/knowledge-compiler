"""Daily / weekly digest: period collection → LLM synthesis → markdown report."""

from __future__ import annotations

from datetime import datetime, timedelta

DIGEST_SYSTEM = "你是个人知识库的报告撰写助手。只输出报告正文（Markdown），不要任何解释。"


def _period(kind: str, date: str | None) -> tuple[str, str, str]:
    """Returns (label, since_iso, until_iso)."""
    if kind == "weekly":
        base = datetime.fromisoformat(date) if date else datetime.now()
        monday = base - timedelta(days=base.weekday())
        start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        until = start + timedelta(days=7)
        iso = start.isocalendar()
        label = f"{iso[0]}-W{iso[1]:02d}"
        return label, start.isoformat(), until.isoformat()
    base = datetime.fromisoformat(date) if date else datetime.now()
    start = base.replace(hour=0, minute=0, second=0, microsecond=0)
    until = start + timedelta(days=1)
    return start.strftime("%Y-%m-%d"), start.isoformat(), until.isoformat()


def collect_period(conn, since_iso: str, until_iso: str) -> dict:
    docs = [
        (r["id"], r["title"], r["compiled_at"])
        for r in conn.execute(
            "SELECT id, title, compiled_at FROM documents "
            "WHERE compiled_at >= ? AND compiled_at < ? ORDER BY compiled_at",
            (since_iso, until_iso),
        )
    ]
    claims = [
        (r["id"], r["text"], r["type"], r["confidence"], r["title"])
        for r in conn.execute(
            """SELECT cl.id, cl.text, cl.type, cl.confidence, d.title FROM claims cl
               JOIN documents d ON d.id=cl.document_id
               WHERE cl.created_at >= ? AND cl.created_at < ? AND cl.status='active'
               ORDER BY cl.confidence DESC""",
            (since_iso, until_iso),
        )
    ]
    supers = [
        (r["id"], r["superseded_reason"])
        for r in conn.execute(
            "SELECT id, superseded_reason FROM claims "
            "WHERE created_at >= ? AND created_at < ? AND status='superseded'",
            (since_iso, until_iso),
        )
    ]
    return {"docs": docs, "claims": claims, "superseded": supers}


def _synthesis(llm, claims: list, batch_chars: int = 1800) -> list[str]:
    """LLM synthesis of new claims in small batches."""
    parts: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for cid, text, ctype, conf, title in claims:
        line = f"- [{ctype}|{conf}] {text}（{title[:40]}）"
        if cur and cur_len + len(line) > batch_chars:
            parts.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line)
    if cur:
        parts.append("\n".join(cur))
    out: list[str] = []
    for i, chunk in enumerate(parts, 1):
        prompt = (
            f"以下是个人知识库本期（第{i}/{len(parts)}批）新增的论断清单。"
            "写 3-6 条要点综合（Markdown 无序列表），归纳主题与趋势，不要逐条复述：\n\n" + chunk
        )
        try:
            out.append(llm.chat(DIGEST_SYSTEM, prompt, temperature=0.3, max_tokens=512).strip())
        except Exception:  # noqa: BLE001 — synthesis is best-effort
            out.append("（本批综合生成失败，原始清单如下）\n\n" + chunk)
    return out


def generate(
    cfg,
    conn,
    llm,
    weekly: bool = False,
    date: str | None = None,
    compile_flag: bool = False,
    progress=None,
) -> dict | None:
    progress = progress or (lambda msg: None)
    kind = "weekly" if weekly else "daily"
    label, since, until = _period(kind, date)
    data = collect_period(conn, since, until)
    if not data["docs"] and not data["claims"]:
        return {"label": label, "kind": kind, "empty": True}

    outdir = cfg.dir(kind)
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{label}.md"

    lines = [
        "---",
        f'kind: "{kind}"',
        f'period: "{label}"',
        f'since: "{since}"',
        f'until: "{until}"',
        "---",
        "",
        f"# {'周报' if weekly else '日报'} {label}",
        "",
        f"本期新增：{len(data['docs'])} 文档，{len(data['claims'])} 活跃论断，"
        f"{len(data['superseded'])} 条观点被取代。",
        "",
    ]
    if data["docs"]:
        lines.append("## 新增文档")
        lines.append("")
        for did, title, ts in data["docs"]:
            lines.append(f"- `{did}` {title}（{ts[:16]}）")
        lines.append("")
    if data["claims"]:
        lines.append("## 要点综合")
        lines.append("")
        progress(f"  ⤳ digest 综合（{len(data['claims'])} 条论断）…")
        for block in _synthesis(llm, data["claims"]):
            lines.append(block)
            lines.append("")
    if data["superseded"]:
        lines.append("## 观点演化")
        lines.append("")
        for cid, reason in data["superseded"]:
            lines.append(f"- claim#{cid} 被取代：{(reason or '')[:100]}")
        lines.append("")
    if data["claims"]:
        lines.append("## 本期论断清单")
        lines.append("")
        for cid, text, ctype, conf, title in data["claims"]:
            lines.append(f"- claim#{cid} [{ctype}|{conf}] {text}（{title[:40]}）")
        lines.append("")
    lines.append(f"> 由 `kc digest{' --weekly' if weekly else ''}` 生成。")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    progress(f"  ⤳ 已写入 {path}")

    if compile_flag:
        inbox = cfg.dir("inbox")
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / f"{kind}-{label}.md"
        dest.write_text("\n".join(lines), encoding="utf-8")
        progress(f"  ⤳ 已复制到 inbox/{dest.name}（等待编译）")
    return {"label": label, "kind": kind, "empty": False, "path": str(path),
            "docs": len(data["docs"]), "claims": len(data["claims"])}
