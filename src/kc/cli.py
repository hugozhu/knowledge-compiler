"""kc CLI: init / add / compile / search / ask / show / status / stats / watch."""

from __future__ import annotations

import argparse
import json
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
    if not args.no_backlinks and stats.compiled:
        try:
            from .backlinks import generate_all

            generate_all(conn, cfg, progress=_progress)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ 实体页生成失败（不影响编译结果）：{e}")
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


def cmd_backlinks(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    from .backlinks import generate_all

    result = generate_all(conn, cfg, progress=lambda m: print(m))
    print(f"完成：{result['pages']} 个实体页 → {cfg.dir('entities')}/")
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


def cmd_remove(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    from .lifecycle import remove_document

    try:
        result = remove_document(
            conn, cfg, args.doc_id, purge_raw=args.purge_raw,
            progress=lambda m: print(f"  {m}"),
        )
    except ValueError as e:
        sys.exit(str(e))
    print(
        f"✓ 已删除 {result['doc_id']}「{result['title']}」："
        f"恢复跨文档 claim {result['restored']} 条，回收孤儿实体 {len(result['orphan_entities'])} 个"
        + ("，raw 已清除" if not result["raw_kept"] else "，raw 保留")
    )
    for name in result["orphan_entities"]:
        print(f"    - {name}")
    return 0


def cmd_graph(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    from . import graph

    if not args.name:
        o = graph.overview(conn)
        print(
            f"知识图谱概览：{o['entities']} 实体 / {o['relations']} 关系 / "
            f"{o['active_claims']} 活跃论断（superseded {o['superseded']}，duplicate {o['duplicates']}）/ "
            f"{o['documents']} 文档"
        )
        if o["predicates"]:
            print("\n关系谓词 Top：" + "，".join(f"{p}×{c}" for p, c in o["predicates"]))
        if o["degree"]:
            print("\n关系连接度 Top：")
            for name, c in o["degree"]:
                print(f"  {name}（{c} 条边）")
        if o["top_entities"]:
            print("\n文档覆盖 Top 实体：")
            for name, dc in o["top_entities"]:
                print(f"  {name}（{dc} docs）")
        return 0

    ent = graph.resolve(conn, args.name)
    if ent is None:
        sys.exit(f"未找到实体「{args.name}」")
    nb = graph.neighborhood(conn, ent)
    e = nb["entity"]
    alias = f"（别名：{' / '.join(nb['aliases'])}）" if nb["aliases"] else ""
    print(f"{e['name']}  #{e['id']}  [{e['type']}]  docs={e['doc_count']} {alias}")
    if nb["out_rels"]:
        print("\n关系（出边）：")
        for s, p, o in nb["out_rels"]:
            print(f"  {s} --{p}--> {o}")
    if nb["in_rels"]:
        print("\n关系（入边）：")
        for s, p, o in nb["in_rels"]:
            print(f"  {s} --{p}--> {o}")
    if nb["co_mentions"]:
        print("\n共现实体：" + "，".join(f"{n}({d})" for n, d in nb["co_mentions"]))
    if nb["docs"]:
        print("\n提及文档：")
        for did, title in nb["docs"]:
            print(f"  {did}  {title[:60]}")
    if nb["claims"]:
        print("\n相关论断：")
        for cid, ctype, conf, text, title in nb["claims"]:
            print(f"  #{cid} [{ctype}|{conf}] {text[:70]}")
            print(f"      ↳ {title[:50]}")
    return 0


def cmd_evolution(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)

    def claim_row(cid: int):
        return conn.execute(
            """SELECT cl.id, cl.text, cl.status, cl.superseded_by, cl.superseded_reason,
                      cl.created_at, d.title FROM claims cl
               JOIN documents d ON d.id=cl.document_id WHERE cl.id=?""",
            (cid,),
        ).fetchone()

    if args.claim_id:
        row = claim_row(args.claim_id)
        if not row:
            sys.exit(f"未找到 claim#{args.claim_id}")
        chain = [row]
        while chain[-1]["superseded_by"]:
            nxt = claim_row(chain[-1]["superseded_by"])
            if not nxt:
                break
            chain.append(nxt)
        print(f"claim#{args.claim_id} 演化链：\n")
        for i, c in enumerate(chain):
            mark = "（已失效）" if c["status"] != "active" else "（当前有效）"
            print(f"  {'└─ ' if i else ''}#{c['id']} [{c['created_at'][:10]}] {c['text']}")
            if i and chain[i - 1]["superseded_reason"]:
                print(f"      理由：{chain[i - 1]['superseded_reason']}")
            print(f"      来源：{c['title'][:50]} {mark}")
        return 0

    supers = conn.execute(
        """SELECT cl.id, cl.text, cl.superseded_by, cl.superseded_reason, cl.created_at, d.title
           FROM claims cl JOIN documents d ON d.id=cl.document_id
           WHERE cl.status='superseded' ORDER BY cl.id"""
    ).fetchall()
    dups = conn.execute(
        """SELECT cl.id, cl.text, cl.duplicate_of, d.title FROM claims cl
           JOIN documents d ON d.id=cl.document_id
           WHERE cl.status='duplicate' ORDER BY cl.id"""
    ).fetchall()
    if not supers and not dups:
        print("暂无知识演化记录（还没有 claim 被取代或判重）。")
        print("产生演化的方式：编译含矛盾观点的文档（自动判重）或运行 ./kc audit --apply")
        return 0
    if supers:
        print(f"知识演化（{len(supers)} 条 superseded 链）：\n")
        for c in supers:
            nxt = claim_row(c["superseded_by"]) if c["superseded_by"] else None
            print(f"◆ #{c['id']}「{c['text'][:50]}…」({c['title'][:30]}, {c['created_at'][:10]})")
            if nxt:
                print(f"   └─[{nxt['title'][:30]}] # {nxt['id']}「{nxt['text'][:50]}…」")
            if c["superseded_reason"]:
                print(f"      理由：{c['superseded_reason']}")
    if dups:
        if supers:
            print()
        print(f"重复记录（{len(dups)} 条 duplicate）：")
        for c in dups:
            tgt = claim_row(c["duplicate_of"]) if c["duplicate_of"] else None
            tgt_txt = f" ≡ #{tgt['id']}「{tgt['text'][:40]}…」" if tgt else ""
            print(f"  #{c['id']}「{c['text'][:40]}…」{tgt_txt}")
    return 0


def cmd_audit(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    from .audit import apply_verdicts, candidate_pairs, judge_pairs

    pairs = candidate_pairs(conn, threshold=args.threshold, max_pairs=args.max_pairs)
    if not pairs:
        print(f"没有相似度 ≥ {args.threshold} 的论断对，无需巡检。")
        return 0
    print(f"候选论断对：{len(pairs)}（cosine ≥ {args.threshold}）")
    if args.dry_run:
        for p in pairs:
            print(f"  [{p['score']}] #{p['a']}「{p['a_text'][:45]}…」 vs #{p['b']}「{p['b_text'][:45]}…」")
        return 0

    llm = _make_llm(cfg)
    ok, info = llm.health()
    if not ok:
        sys.exit(f"audit 需要 LLM 服务在线（{info}）；--dry-run 可离线看候选")

    def _progress(msg: str) -> None:
        print(msg, flush=True)

    print(f"LLM 判定（{len(pairs)} 对，4 对/批）…")
    verdicts = judge_pairs(llm, pairs)
    for p, v in zip(pairs, verdicts):
        mark = {"contradiction": "⚡矛盾", "duplicate": "≡重复", "consistent": "✓一致", "unrelated": "·无关"}[v["verdict"]]
        print(f"  {mark} [{p['score']}] #{p['a']} vs #{p['b']}" + (f"（{v['reason']}）" if v["reason"] else ""))
    if args.apply:
        stats = apply_verdicts(conn, pairs, verdicts, progress=_progress)
        print(
            f"已落库：矛盾 {stats['contradiction']}，重复 {stats['duplicate']}，"
            f"一致 {stats['consistent']}，无关 {stats['unrelated']}（详见 ./kc evolution）"
        )
    else:
        n_c = sum(1 for v in verdicts if v["verdict"] == "contradiction")
        n_d = sum(1 for v in verdicts if v["verdict"] == "duplicate")
        print(f"报告模式（未写库）：矛盾 {n_c}，重复 {n_d}。加 --apply 落库。")
    return 0


def cmd_digest(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    from .digest import generate

    if args.dry_run:
        from .digest import _period, collect_period

        label, since, until = _period("weekly" if args.weekly else "daily", args.date)
        data = collect_period(conn, since, until)
        print(f"周期 {label}（{since} ~ {until}）："
              f"新增文档 {len(data['docs'])}，活跃论断 {len(data['claims'])}，"
              f"被取代 {len(data['superseded'])}")
        for did, title, _ in data["docs"][:10]:
            print(f"  - {did} {title[:50]}")
        return 0

    llm = _make_llm(cfg)
    ok, info = llm.health()
    if not ok:
        sys.exit(f"digest 需要 LLM 服务在线（{info}）")

    result = generate(
        cfg, conn, llm, weekly=args.weekly, date=args.date,
        compile_flag=args.compile, progress=lambda m: print(m),
    )
    if result is None:
        print("生成失败")
        return 1
    if result.get("empty"):
        print(f"周期 {result['label']} 内没有新增内容，未生成报告。")
        return 0
    print(f"完成：{result['path']}（文档 {result['docs']}，论断 {result['claims']}）")
    return 0


def cmd_test(args) -> int:
    import unittest

    root = Path(__file__).resolve().parents[2]
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        sys.exit(f"未找到测试目录：{tests_dir}")
    loader = unittest.TestLoader()
    suite = loader.discover(str(tests_dir), top_level_dir=str(root))
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def cmd_memory(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    from .memory import KINDS, add_memory, complete_memory, list_memories

    if args.action == "add":
        try:
            mem_id = add_memory(
                conn,
                args.text,
                kind=args.kind or "task",
                source=args.source,
                expires_days=args.expires,
            )
        except ValueError as e:
            sys.exit(str(e))
        print(f"✓ memory#{mem_id} [{args.kind}]{f'，{args.expires} 天后过期' if args.expires else ''}")
        return 0

    if args.action == "done":
        mem_id = args.mem_id
        if mem_id is None and args.text is not None:  # `kc memory done 2` 的 text 位兜底
            try:
                mem_id = int(args.text)
            except ValueError:
                mem_id = None
        if mem_id is None:
            sys.exit("用法：kc memory done <编号>")
        try:
            complete_memory(conn, mem_id)
        except ValueError as e:
            sys.exit(str(e))
        print(f"✓ memory#{mem_id} 已完成归档")
        return 0

    # list
    rows = list_memories(conn, active_only=not args.all, kind=args.kind)
    if not rows:
        print("没有记忆" + (f"（kind={args.kind}）" if args.kind else ""))
        return 0
    for m in rows:
        exp = f"  [至 {m['expires_at'][:10]}]" if m["expires_at"] else ""
        print(f"#{m['id']:<3} [{m['status']}|{m['kind']}] {m['text']}{exp}")
        print(f"      {m['created_at'][:16]} 更新 {m['updated_at'][:16]}"
              + (f"  来源：{m['source']}" if m["source"] else ""))
    return 0


def cmd_note(args) -> int:
    cfg = load_config(args.home)
    text = (args.text or "").strip()
    if not text:
        sys.exit("note 内容不能为空")
    from .note import write_note

    dest = write_note(cfg, text, title=args.title)
    print(f"✓ 已写入 {dest.name}（{len(text)} 字），等待 ./kc compile")
    return 0


def cmd_context(args) -> int:
    cfg = load_config(args.home)
    conn = _open_db(cfg, create=False)
    from .context import build_context

    llm = None
    if not args.no_llm:
        cand = _make_llm(cfg)
        ok, _ = cand.health()
        if ok:
            llm = cand
        else:
            print("⚠ LLM 不可用，使用确定性关键词（context 仍可生成）")
    try:
        result = build_context(
            conn, llm, args.task, max_chars=args.max_chars, include_memory=not args.no_memory
        )
    except ValueError as e:
        sys.exit(str(e))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["pack"])
    if args.out:
        Path(args.out).write_text(result["pack"], encoding="utf-8")
        print(f"\n（已写入 {args.out}）", file=sys.stderr)
    s = result["stats"]
    print(
        f"（关键词 {len(s['keywords'])} · 实体 {s['entities']} · 论断 {s['claims']} · "
        f"记忆 {s['memories']} · 来源 {s['sources']} · {s['chars']}/{s['max_chars']} 字符）",
        file=sys.stderr,
    )
    return 0


def cmd_serve(args) -> int:
    cfg = load_config(args.home)
    cfg.ensure_dirs()
    from .server import serve_forever

    llm = _make_llm(cfg)
    ok, info = llm.health()
    if not ok:
        print(f"⚠ LLM 不可用（{info}）：/ask /context 关键词将走降级路径，其余端点正常")
    serve_forever(cfg, llm if ok else llm, args.host, args.port)
    return 0


def cmd_mcp(args) -> int:
    """MCP stdio 服务器：stdout 只输出 JSON-RPC，绝不能有其他打印。"""
    from .mcp import serve_stdio

    return serve_stdio()


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
                if stats.compiled:
                    try:
                        from .backlinks import generate_all

                        generate_all(conn, cfg, progress=_progress)
                    except Exception as e:  # noqa: BLE001
                        print(f"  ⚠ 实体页生成失败：{e}")
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
    p.add_argument("--no-backlinks", action="store_true", help="编译后不刷新实体页")
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

    p = sub.add_parser("backlinks", parents=[common], help="重新生成实体反向链接页")
    p.set_defaults(func=cmd_backlinks)

    p = sub.add_parser("dedup", parents=[common], help="对未检查的 claims 做判重（四态）")
    p.add_argument("--dry-run", action="store_true", help="只展示相似候选，不调用 LLM、不写库")
    p.set_defaults(func=cmd_dedup)

    p = sub.add_parser("remove", parents=[common], help="删除文档（级联+演化恢复；raw 默认保留）")
    p.add_argument("doc_id")
    p.add_argument("--purge-raw", action="store_true", help="同时删除 raw 原件（覆盖 immutable 默认）")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("graph", parents=[common], help="知识图谱概览 / 实体邻域")
    p.add_argument("name", nargs="?", default=None, help="实体名或别名（缺省为全局概览）")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("evolution", parents=[common], help="知识演化链视图")
    p.add_argument("claim_id", nargs="?", type=int, default=None, help="只看指定 claim 的链")
    p.set_defaults(func=cmd_evolution)

    p = sub.add_parser("audit", parents=[common], help="矛盾巡检（存量 claims 两两审查）")
    p.add_argument("--apply", action="store_true", help="把矛盾/重复判定落库（形成演化链）")
    p.add_argument("--threshold", type=float, default=0.3, help="cosine 候选阈值（默认 0.3）")
    p.add_argument("--max-pairs", type=int, default=50, help="最多送 LLM 的论断对数")
    p.add_argument("--dry-run", action="store_true", help="只列候选对，不调 LLM 不写库")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("digest", parents=[common], help="生成日报/周报（LLM 综合）")
    p.add_argument("--weekly", action="store_true", help="周报（默认日报）")
    p.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD（默认今天）")
    p.add_argument("--dry-run", action="store_true", help="只统计周期内容，不调 LLM")
    p.add_argument("--compile", action="store_true", help="把报告复制进 inbox 喂回知识库")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("test", parents=[common], help="运行自动化测试套件（unittest，离线 FakeLLM）")
    p.add_argument("-v", "--verbose", action="store_true", help="逐用例输出")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("memory", parents=[common], help="个人记忆（动态状态，非知识，可过期）")
    p.add_argument("action", choices=["add", "list", "done"])
    p.add_argument("text", nargs="?", default=None, help="add：记忆内容")
    p.add_argument("--kind", default=None, help="project/decision/preference/task/discussion/goal（add 默认 task）")
    p.add_argument("--source", default=None, help="来源标注")
    p.add_argument("--expires", type=float, default=None, dest="expires", help="N 天后过期")
    p.add_argument("--all", action="store_true", help="list 含已完成/已过期")
    p.add_argument("mem_id", nargs="?", type=int, default=None, help="done：记忆编号")
    p.set_defaults(func=cmd_memory)

    p = sub.add_parser("note", parents=[common], help="随手记（写入 inbox，编译后成为知识）")
    p.add_argument("text")
    p.add_argument("--title", default=None, help="标题（默认「随手记」）")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("context", parents=[common], help="Context Builder：任务 → Task Context Pack")
    p.add_argument("task")
    p.add_argument("--max-chars", type=int, default=6000, dest="max_chars")
    p.add_argument("--out", default=None, help="写入文件")
    p.add_argument("--json", action="store_true", help="输出机器可读 JSON（pack+stats）")
    p.add_argument("--no-memory", action="store_true", help="不注入个人记忆")
    p.add_argument("--no-llm", action="store_true", help="不用 LLM 提关键词（纯确定性）")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("serve", parents=[common], help="Web API（本地知识节点服务）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8300)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("mcp", help="MCP stdio 服务器（代理到 kc serve，供 OpenCode 等 MCP 客户端使用）")
    p.set_defaults(func=cmd_mcp)

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
