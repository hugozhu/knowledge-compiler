"""Entity backlink pages: deterministic views under entities/<type>/.

These pages are derived artifacts (not knowledge sources) — regenerated whole
on every run, never compiled back into the KB (avoids self-reference noise).
"""

from __future__ import annotations

import re
import shutil

from . import graph
from .util import now_iso


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", name).strip("-")
    return (s or "entity")[:60]


def _render(nb: dict) -> str:
    e = nb["entity"]
    lines = [
        "---",
        f'entity: "{e["name"]}"',
        f'id: {e["id"]}',
        f'type: "{e["type"]}"',
        f'doc_count: {e["doc_count"]}',
        f'generated_at: "{now_iso()}"',
        "---",
        "",
        f"# {e['name']}",
        "",
    ]
    if nb["aliases"]:
        lines.append(f"**别名**：{' / '.join(nb['aliases'])}")
        lines.append("")
    if nb["docs"]:
        lines.append("## 提及文档")
        lines.append("")
        for did, title in nb["docs"]:
            lines.append(f"- `{did}` {title}")
        lines.append("")
    if nb["claims"]:
        lines.append("## 相关论断")
        lines.append("")
        for cid, ctype, conf, text, title in nb["claims"]:
            lines.append(f"- claim#{cid} [{ctype}|{conf}] {text}（{title}）")
        lines.append("")
    if nb["out_rels"] or nb["in_rels"]:
        lines.append("## 关系")
        lines.append("")
        for s, p, o in nb["out_rels"]:
            lines.append(f"- {s} --{p}--> {o}")
        for s, p, o in nb["in_rels"]:
            lines.append(f"- {s} --{p}--> {o}")
        lines.append("")
    if nb["co_mentions"]:
        lines.append("## 共现实体")
        lines.append("")
        lines.append("，".join(f"{n}（{d} docs）" for n, d in nb["co_mentions"]))
        lines.append("")
    lines.append("> 本页面由 `kc backlinks` 自动生成（确定性视图，勿手改）。")
    lines.append("")
    return "\n".join(lines)


def generate_all(conn, cfg, progress=None) -> dict:
    progress = progress or (lambda msg: None)
    root = cfg.dir("entities")
    root.mkdir(parents=True, exist_ok=True)
    # entities/<type>/ subtree is owned by the generator → full rebuild
    for d in root.iterdir():
        if d.is_dir():
            shutil.rmtree(d)
    count = 0
    index: list[str] = [
        "---",
        f'generated_at: "{now_iso()}"',
        "---",
        "",
        "# 实体索引",
        "",
        "由 `kc backlinks` 自动生成；子目录按类型分组。",
        "",
    ]
    for ent in conn.execute("SELECT * FROM entities ORDER BY doc_count DESC, id").fetchall():
        nb = graph.neighborhood(conn, ent)
        if not nb["docs"] and not nb["claims"] and not nb["out_rels"] and not nb["in_rels"]:
            continue
        tdir = root / (ent["type"] or "other")
        tdir.mkdir(parents=True, exist_ok=True)
        path = tdir / f"{ent['id']}-{_slug(ent['name'])}.md"
        path.write_text(_render(nb), encoding="utf-8")
        count += 1
        alias = f"（别名 {'/'.join(nb['aliases'])}）" if nb["aliases"] else ""
        index.append(f"- [{ent['name']}](./{tdir.name}/{path.name}) — {ent['type']}，{ent['doc_count']} docs {alias}")
    index.append("")
    (root / "index.md").write_text("\n".join(index), encoding="utf-8")
    progress(f"  ⤳ backlinks：生成 {count} 个实体页 + index.md")
    return {"pages": count}
