"""kc CLI: init / add / compile / search / ask / show / status / stats / watch."""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

from . import __version__, db as dbmod
from .compiler import Compiler
from .config import load_config
from .embed import backfill, load_provider
from .llm import LLM
from .retrieve import ask, search
from .util import now_iso


def _open_db(cfg, create: bool):
    if not cfg.db_path.exists() and not create:
        sys.exit(f"数据库不存在：{cfg.db_path}（先运行 kc init）")
    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)
    return conn


def _make_llm(cfg) -> LLM:
    return LLM(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model, cfg.llm_vlm_model, cfg.llm_timeout)


# ---------------------------------------------------------------- commands
def cmd_init(args) -> int:
    cfg = load_config(args.home)
    cfg.ensure_dirs()
    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)
    print(f"已初始化：{cfg.home}")
    print(f"数据库：  {cfg.db_path}")
    print(f"LLM：    {cfg.llm_base_url}（{cfg.llm_model} / {cfg.llm_vlm_model}）")
    return 0


def cmd_add(args) -> int:
    cfg = load_config(args.home)
    cfg.ensure_dirs()
    conn = _open_db(cfg, create=True)
    comp = Compiler(cfg, conn)

    def _progress(msg: str) -> None:
        print(f"  {msg}")

    targets: list[str] = []
    for p in args.paths:
        if p.startswith(("http://", "https://")):
            targets.append(p)
        else:
            targets.append(str(Path(p).expanduser()))

    added: list[Path] = []
    downloads = [t for t in targets if t.startswith(("http://", "https://"))]
    locals_ = [t for t in targets if not t.startswith(("http://", "https://"))]
    if locals_:
        added.extend(comp.add(locals_))
    for url in downloads:
        name = url.rstrip("/").split("/")[-1] or "download"
        if "." not in name:
            name += ".html"
        dest = cfg.dir("inbox") / name
        req = urllib.request.Request(url, headers={"User-Agent": "kc/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        added.append(dest)
        print(f"  ↓ {url} → {dest.name}")
    if added:
        print(f"已加入 inbox（{len(added)} 个文件）：")
        for p in added:
            print(f"  - {p.name}")
    else:
        print("没有新文件（重复或为空）")
    return 0


def cmd_compile(args) -> int:
    cfg = load_config(args.home)
    cfg.ensure_dirs()
    conn = _open_db(cfg, create=True)
    llm = None
    if not args.no_llm:
        cand = _make_llm(cfg)
        ok, info = cand.health()
        if ok:
            llm = cand
            print(f"LLM：{info}")
        else:
            print(f"⚠ LLM 服务不可用（{info}），降级为确定性编译（--no-llm 行为）")

    def _progress(msg: str) -> None:
        print(msg, flush=True)

    comp = Compiler(cfg, conn, llm, progress=_progress)
    stats = comp.compile_pending(
        force=args.force,
        use_llm=llm is not None,
        limit=args.limit,
        all_raw=args.all,
        ocr_pages=args.ocr_pages,
    )
    print(
        f"扫描 {stats.scanned}，编译 {stats.compiled}，跳过 {stats.skipped}，失败 {stats.failed}"
        f"（新增 chunks {stats.chunks}，claims {stats.claims}）"
    )
    for e in stats.errors:
        print(f"  ✗ {e}")
    if not args.no_dedup and stats.doc_ids and llm is not None:
        ph = ",".join("?" * len(stats.doc_ids))
        claim_ids = [
            r["id"]
            for r in conn.execute(
                f"SELECT id FROM claims WHERE document_id IN ({ph}) "
                f"AND status='active' AND dedup_checked=0 ORDER BY id",
                stats.doc_ids,
            )
        ]
        if claim_ids:
            print(f"判重（{len(claim_ids)} 条新 claim，含 LLM 批量判定）…")
            from .dedup import dedup_claims

            dstats = dedup_claims(conn, llm, claim_ids, progress=_progress)
            print(
                f"判重完成：new {dstats['new']}，duplicate {dstats['duplicate']}，"
                f"update {dstats['update']}，contradiction {dstats['contradiction']}"
            )
    if not args.no_embed and stats.compiled:
        try:
            provider = load_provider()
            backfill(conn, provider, progress=_progress)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ 向量回填失败（不影响编译结果）：{e}")
    if stats.failed:
        print("提示：运行 ./kc compile --all 可重试失败项（含 raw 中未完成的）")
    return 0 if stats.failed == 0 else 1


def cmd_embed(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    provider = load_provider()
    print(f"embedding provider: {provider.name}（dim {provider.dim}）")

    def _progress(msg: str) -> None:
        print(msg, flush=True)

    stats = backfill(conn, provider, force=args.force, progress=_progress)
    total = conn.execute("SELECT COUNT(*) AS c FROM embeddings").fetchone()["c"]
    print(f"完成：chunks {stats['chunks_done']}（skip {stats['chunks_skip']}），"
          f"claims {stats['claims_done']}（skip {stats['claims_skip']}），库里共 {total} 条向量")
    return 0


def cmd_search(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    hits = search(conn, args.query, limit=args.limit, scope=args.scope, mode=args.mode)
    if args.rerank and hits:
        from .retrieve import rerank as rr

        llm = _make_llm(cfg)
        ok, info = llm.health()
        if not ok:
            sys.exit(f"--rerank 需要 LLM 服务在线（{info}）")
        hits = rr(llm, args.query, hits, top_n=args.limit)
    if not hits:
        print("无命中")
        return 0
    for h in hits:
        if h["kind"] == "claim":
            print(f"[claim#{h['id']}] ({h['type']}｜conf {h['confidence']}) {h['text']}")
            print(f"             ↳ {h['title']} ({h['document_id']})")
        else:
            snippet = h["text"].replace("\n", " ")[:160]
            print(f"[chunk#{h['id']}] {snippet}…")
            print(f"             ↳ {h['title']} ({h['document_id']})")
    return 0


def cmd_ask(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    llm = _make_llm(cfg)
    ok, info = llm.health()
    if not ok:
        sys.exit(f"ask 需要 LLM 服务在线（{info}）")
    result = ask(conn, llm, args.question, limit=args.limit, rerank_top=args.limit if args.rerank else None)
    if result is None:
        print("知识库中没有相关内容。")
        return 0
    if result.get("keywords"):
        print(f"（检索关键词：{' '.join(result['keywords'])}）\n")
    print(result["answer"])
    print("\nSources:")
    for i, s in enumerate(result["sources"], 1):
        label = s["title"] or s["document_id"]
        print(f"  [{i}] {s['kind']}#{s['id']}｜{label}｜{s['document_id']}")
    return 0


def cmd_show(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    row = conn.execute("SELECT * FROM documents WHERE id=?", (args.doc_id,)).fetchone()
    if not row:
        sys.exit(f"未找到文档 {args.doc_id}")
    print(f"id:       {row['id']}")
    print(f"title:    {row['title']}")
    print(f"type:     {row['source_type']}")
    print(f"source:   {row['source_path']}")
    print(f"chars:    {row['char_count']}  lang: {row['lang']}")
    print(f"summary:  {row['summary']}")
    print(f"compiled: {row['compiled_at']}  model: {row['model'] or '—'}")
    n_chunks = conn.execute(
        "SELECT COUNT(*) AS c FROM chunks WHERE document_id=?", (args.doc_id,)
    ).fetchone()["c"]
    claims = conn.execute(
        "SELECT * FROM claims WHERE document_id=? ORDER BY id", (args.doc_id,)
    ).fetchall()
    print(f"chunks: {n_chunks}  claims: {len(claims)}")
    if claims:
        print("\nclaims:")
        for c in claims:
            mark = ""
            if c["status"] == "duplicate":
                mark = f"  [重复→#{c['duplicate_of']}]"
            elif c["status"] == "superseded":
                mark = f"  [被#{c['superseded_by']}取代]"
            print(f"  #{c['id']} [{c['type']}|{c['confidence']}] {c['text']}{mark}")
            if c["status"] == "superseded" and c["superseded_reason"]:
                print(f"      理由：{c['superseded_reason']}")
        # 演化链：本文档内被取代的 claim → 取代者
        supers = [c for c in claims if c["status"] == "superseded" and c["superseded_by"]]
        if supers:
            print("\n知识演化：")
            by_id = {c["id"]: c for c in claims}
            for c in supers:
                nxt = by_id.get(c["superseded_by"])
                if nxt is None:
                    row = conn.execute(
                        "SELECT text FROM claims WHERE id=?", (c["superseded_by"],)
                    ).fetchone()
                    nxt_txt = row["text"][:50] + "…" if row else "（跨文档 claim）"
                else:
                    nxt_txt = nxt["text"][:50] + "…"
                print(f"  #{c['id']} {c['text'][:40]}…")
                print(f"    └─ superseded_by → #{c['superseded_by']} {nxt_txt}")
    ents = conn.execute(
        "SELECT e.name, e.type FROM entities e "
        "JOIN entity_mentions m ON m.entity_id=e.id WHERE m.document_id=?",
        (args.doc_id,),
    ).fetchall()
    if ents:
        print("\nentities:")
        print("  " + ", ".join(f"{e['name']}({e['type']})" for e in ents))
    return 0


def cmd_entities(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)

    if args.merge:
        target, source = args.merge
        from .entities import merge_entities

        try:
            result = merge_entities(conn, target, source)
        except ValueError as e:
            sys.exit(f"合并失败：{e}")
        print(f"✓ 「{result['absorbed']}」已并入「{result['target']}」"
              f"（转移 {result['mentions_moved']} 个提及，现提及 {result['doc_count']} 个文档）")
        return 0

    if args.suggest:
        llm = _make_llm(cfg)
        ok, info = llm.health()
        if not ok:
            sys.exit(f"--suggest 需要 LLM 服务在线（{info}）")
        from .entities import suggest_groups

        names = [r["name"] for r in conn.execute("SELECT name FROM entities ORDER BY doc_count DESC")]
        groups = suggest_groups(llm, names)
        if not groups:
            print("没有发现可合并的实体分组")
            return 0
        name_to_id = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM entities")}
        print("建议合并（用 ./kc entities merge <目标id> <来源id> 执行）：\n")
        for g in groups:
            tgt = name_to_id.get(g["canonical"])
            merges = [m for m in g["merge"] if name_to_id.get(m) not in (None, tgt)]
            if not merges:
                continue
            print(f"  {g['canonical']} (#{tgt})  ←  {', '.join(merges)}")
            print(f"    理由：{g['reason']}")
            for m in merges:
                sid = name_to_id.get(m)
                if tgt and sid:
                    print(f"      → ./kc entities merge {tgt} {sid}")
        return 0

    where, params = "", []
    if args.query:
        where = "WHERE e.name LIKE ? OR EXISTS (SELECT 1 FROM entity_aliases ax WHERE ax.entity_id=e.id AND ax.alias LIKE ?)"
        q = f"%{args.query}%"
        params = [q, q]
    rows = conn.execute(
        f"""SELECT e.id, e.name, e.type, e.doc_count,
                   (SELECT group_concat(a.alias, ' / ') FROM entity_aliases a
                    WHERE a.entity_id = e.id) AS aliases
            FROM entities e {where}
            ORDER BY e.doc_count DESC, e.id""",
        params,
    ).fetchall()
    if not rows:
        print("无实体" + (f"匹配「{args.query}」" if args.query else ""))
        return 0
    for r in rows:
        alias = f"（别名：{r['aliases']}）" if r["aliases"] else ""
        print(f"#{r['id']:<4} {r['name']}  [{r['type']}]  docs={r['doc_count']} {alias}")
    return 0


def cmd_dedup(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    llm = None
    if not args.dry_run:
        llm = _make_llm(cfg)
        ok, info = llm.health()
        if not ok:
            sys.exit(f"dedup 需要 LLM 服务在线（{info}）；--dry-run 可离线查看候选")
    from .dedup import dedup_claims, find_candidates

    rows = conn.execute(
        "SELECT id FROM claims WHERE status='active' AND dedup_checked=0 ORDER BY id"
    ).fetchall()
    claim_ids = [r["id"] for r in rows]
    if not claim_ids:
        print("没有待判重的 claim（全部已检）")
        return 0
    print(f"待判重：{len(claim_ids)} 条")

    def _progress(msg: str) -> None:
        print(msg, flush=True)

    if args.dry_run:
        for cid in claim_ids:
            row = conn.execute("SELECT text FROM claims WHERE id=?", (cid,)).fetchone()
            cands = find_candidates(conn, row["text"], exclude_ids={cid})
            print(f"\nclaim#{cid}: {row['text'][:60]}")
            for c in cands:
                print(f"    ≈ #{c['id']} ({c['via']}, {c['score']}) {c['text'][:60]}")
        return 0

    stats = dedup_claims(conn, llm, claim_ids, progress=_progress)
    print(
        f"判重完成：new {stats['new']}，duplicate {stats['duplicate']}，"
        f"update {stats['update']}，contradiction {stats['contradiction']}"
    )
    return 0


def cmd_status(args) -> int:
    cfg = load_config(args.home)
    inbox = cfg.dir("inbox")
    pending = [p for p in inbox.iterdir() if p.is_file()] if inbox.exists() else []
    print(f"home:  {cfg.home}")
    print(f"inbox: {len(pending)} 个待处理文件")
    for p in pending:
        print(f"  - {p.name}")
    if cfg.db_path.exists():
        conn = _open_db(cfg, create=False)
        docs = conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
        claims = conn.execute("SELECT COUNT(*) AS c FROM claims WHERE status='active'").fetchone()["c"]
        print(f"docs:  {docs}  claims: {claims}")
    else:
        print("docs:  （未初始化，运行 kc init）")
    return 0


def cmd_stats(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    for label, sql in [
        ("documents", "SELECT COUNT(*) AS c FROM documents"),
        ("chunks", "SELECT COUNT(*) AS c FROM chunks"),
        ("claims(active)", "SELECT COUNT(*) AS c FROM claims WHERE status='active'"),
        ("entities", "SELECT COUNT(*) AS c FROM entities"),
        ("relations", "SELECT COUNT(*) AS c FROM relations"),
        ("compiled files", "SELECT COUNT(*) AS c FROM compile_log WHERE status='ok'"),
        ("errors", "SELECT COUNT(*) AS c FROM compile_log WHERE status='error'"),
    ]:
        print(f"{label:>16}: {conn.execute(sql).fetchone()['c']}")
    return 0


def cmd_watch(args) -> int:
    import time as _time

    cfg = load_config(args.home)
    cfg.ensure_dirs()
    conn = _open_db(cfg, create=True)
    llm = None
    if not args.no_llm:
        cand = _make_llm(cfg)
        ok, info = cand.health()
        if ok:
            llm = cand
            print(f"LLM：{info}")
        else:
            print(f"⚠ LLM 不可用（{info}），watch 只做确定性编译")

    def _progress(msg: str) -> None:
        print(f"[{now_iso()}] {msg}", flush=True)

    comp = Compiler(cfg, conn, llm, progress=_progress)
    print(f"watching {cfg.dir('inbox')}（每 {args.interval}s 轮询，Ctrl-C 退出）")
    try:
        while True:
            stats = comp.compile_pending(use_llm=llm is not None)
            if stats.compiled or stats.failed:
                print(
                    f"[{now_iso()}] 编译 {stats.compiled}，跳过 {stats.skipped}，失败 {stats.failed}"
                )
                for e in stats.errors:
                    print(f"  ✗ {e}")
            _time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n退出 watch")
    return 0


# -------------------------------------------------------------------- main
def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help="知识库根目录（默认 $KC_HOME 或 ~/knowledge）")

    ap = argparse.ArgumentParser(prog="kc", description="Personal Knowledge Compiler V0.1")
    ap.add_argument("--version", action="version", version=f"kc {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", parents=[common], help="初始化目录与数据库")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("add", parents=[common], help="把文件/目录/URL 放进 inbox")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("compile", parents=[common], help="编译 inbox（含 LLM 语义抽取）")
    p.add_argument("--all", action="store_true", help="同时扫描 raw 中未编译的文件（恢复中断）")
    p.add_argument("--force", action="store_true", help="忽略 compile_log 强制重编译")
    p.add_argument("--no-llm", action="store_true", help="只做确定性编译，不调用模型")
    p.add_argument("--limit", type=int, default=None, help="最多处理 N 个文件")
    p.add_argument("--ocr-pages", type=int, default=10, help="扫描 PDF OCR 的最大页数")
    p.add_argument("--no-embed", action="store_true", help="编译后不回填向量")
    p.add_argument("--no-dedup", action="store_true", help="编译后不对新 claims 做判重")
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("embed", parents=[common], help="增量回填向量（chunk/claim）")
    p.add_argument("--force", action="store_true", help="强制重算全部向量")
    p.set_defaults(func=cmd_embed)

    p = sub.add_parser("search", parents=[common], help="FTS5/LIKE 检索")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--scope", choices=["all", "chunks", "claims"], default="all")
    p.add_argument("--mode", choices=["hybrid", "fts"], default="hybrid",
                   help="hybrid=四路召回+RRF（默认），fts=V0.1 行为")
    p.add_argument("--rerank", action="store_true", help="LLM 对候选重排（慢，~30s）")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("ask", parents=[common], help="基于知识库问答（本地模型）")
    p.add_argument("question")
    p.add_argument("--limit", type=int, default=6)
    p.add_argument("--rerank", action="store_true", help="LLM 对检索结果重排（慢，~30s）")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("show", parents=[common], help="查看文档详情")
    p.add_argument("doc_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("entities", parents=[common], help="实体列表 / 归并建议 / 合并")
    p.add_argument("query", nargs="?", default=None, help="按名称或别名过滤")
    p.add_argument("--suggest", action="store_true", help="LLM 生成可合并分组建议")
    p.add_argument("--merge", nargs=2, type=int, metavar=("目标ID", "来源ID"), help="把来源实体并入目标")
    p.set_defaults(func=cmd_entities)

    p = sub.add_parser("dedup", parents=[common], help="对未检查的 claims 做判重（四态）")
    p.add_argument("--dry-run", action="store_true", help="只展示相似候选，不调用 LLM、不写库")
    p.set_defaults(func=cmd_dedup)

    p = sub.add_parser("status", parents=[common], help="inbox 与库状态")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("stats", parents=[common], help="库统计")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("watch", parents=[common], help="轮询 inbox 自动编译")
    p.add_argument("--interval", type=int, default=30)
    p.add_argument("--no-llm", action="store_true")
    p.set_defaults(func=cmd_watch)

    args = ap.parse_args(argv)
    return args.func(args)
