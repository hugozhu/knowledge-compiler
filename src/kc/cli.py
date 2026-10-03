"""kc CLI: init / add / compile / search / ask / show / status / stats / watch."""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

from . import __version__, db as dbmod
from .compiler import Compiler
from .config import load_config
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
    if stats.failed:
        print("提示：运行 ./kc compile --all 可重试失败项（含 raw 中未完成的）")
    return 0 if stats.failed == 0 else 1


def cmd_search(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    hits = search(conn, args.query, limit=args.limit, scope=args.scope)
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
    result = ask(conn, llm, args.question, limit=args.limit)
    if result is None:
        print("知识库中没有相关内容。")
        return 0
    print(result["answer"])
    print("\nSources:")
    for i, s in enumerate(result["sources"], 1):
        label = s["title"] or s["document_id"]
        print(f"  [{i}] {s['kind']}｜{label}｜{s['document_id']}")
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
            print(f"  #{c['id']} [{c['type']}|{c['confidence']}] {c['text']}")
    ents = conn.execute(
        "SELECT e.name, e.type FROM entities e "
        "JOIN entity_mentions m ON m.entity_id=e.id WHERE m.document_id=?",
        (args.doc_id,),
    ).fetchall()
    if ents:
        print("\nentities:")
        print("  " + ", ".join(f"{e['name']}({e['type']})" for e in ents))
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
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("search", parents=[common], help="FTS5/LIKE 检索")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--scope", choices=["all", "chunks", "claims"], default="all")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("ask", parents=[common], help="基于知识库问答（本地模型）")
    p.add_argument("question")
    p.add_argument("--limit", type=int, default=6)
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("show", parents=[common], help="查看文档详情")
    p.add_argument("doc_id")
    p.set_defaults(func=cmd_show)

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
