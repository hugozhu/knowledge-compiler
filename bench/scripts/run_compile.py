#!/usr/bin/env python3
"""Run the kc compiler against one arm and record phase timings + LLM call metrics.

Arms:
  kc    — local qwen3-4b on NPU, pipeline unmodified (C1)
  flash — deepseek-v4-flash via gateway, same pipeline, reasoning-model adapter (C2)

Usage: python3 bench/scripts/run_compile.py kc|flash
Writes: bench/results/compile_<arm>.json, bench/work/kb-<arm>/ir_<docid>.json
"""

from __future__ import annotations

import json
import shutil
import sys
import time

from common import (  # noqa: E402
    DOCS,
    FLASH_MODEL,
    GATEWAY_BASE,
    GATEWAY_KEY,
    LOCAL_BASE,
    LOCAL_KEY,
    LOCAL_MODEL,
    RESULTS,
    WORK,
    FlashLLM,
    RecordingLLM,
    write_json,
)
from kc.backlinks import generate_all  # noqa: E402
from kc.compiler import Compiler  # noqa: E402
from kc import db as dbmod  # noqa: E402
from kc.config import load_config  # noqa: E402
from kc.dedup import dedup_claims  # noqa: E402
from kc.embed import backfill, load_provider  # noqa: E402
from kc.llm import LLM  # noqa: E402


def make_arm(arm: str):
    if arm == "kc":
        base = RecordingLLM(
            LLM(LOCAL_BASE, LOCAL_KEY, LOCAL_MODEL, "qwen3-vl-4b", timeout=600), "qwen3-4b"
        )
        return base, LOCAL_MODEL, LOCAL_BASE
    if arm == "flash":
        base = FlashLLM(
            LLM(GATEWAY_BASE, GATEWAY_KEY, FLASH_MODEL, "qwen3-vl-4b", timeout=900), "deepseek-v4-flash"
        )
        return base, FLASH_MODEL, GATEWAY_BASE
    raise SystemExit(f"unknown arm {arm}")


def db_snapshot(conn) -> dict:
    docs = []
    for r in conn.execute("SELECT id,title,char_count,model,summary,compiled_at FROM documents ORDER BY id"):
        did = r["id"]
        docs.append(
            {
                "doc_id": did,
                "title": r["title"],
                "chars": r["char_count"],
                "model": r["model"],
                "summary": r["summary"],
                "chunks": conn.execute("SELECT COUNT(*) c FROM chunks WHERE document_id=?", (did,)).fetchone()["c"],
                "claims": conn.execute(
                    "SELECT COUNT(*) c FROM claims WHERE document_id=? AND status='active'", (did,)
                ).fetchone()["c"],
            }
        )
    total = lambda t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]  # noqa: E731
    return {
        "documents": len(docs),
        "docs": docs,
        "chunks": total("chunks"),
        "claims": total("claims"),
        "claim_types": {
            r["type"]: r["c"]
            for r in conn.execute("SELECT type, COUNT(*) c FROM claims GROUP BY type ORDER BY c DESC")
        },
        "entities": total("entities"),
        "relations": total("relations"),
        "embeddings": total("embeddings"),
    }


def dump_irs(conn, home) -> None:
    home.mkdir(parents=True, exist_ok=True)
    for r in conn.execute("SELECT id, ir_json FROM documents"):
        if not r["ir_json"]:
            continue
        (home / f"ir_{r['id']}.json").write_text(
            json.dumps({"doc_id": r["id"], "ir": json.loads(r["ir_json"])}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def main() -> int:
    arm = sys.argv[1] if len(sys.argv) > 1 else "kc"
    home = WORK / f"kb-{arm}"
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True, exist_ok=True)

    llm, model, base_url = make_arm(arm)
    cfg = load_config(str(home))
    cfg.ensure_dirs()
    conn = dbmod.connect(cfg.db_path)
    dbmod.init_db(conn)

    log: list[str] = []

    def progress(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        log.append(line)
        print(line, flush=True)

    t_setup = time.time()
    Compiler(cfg, conn, llm, progress=progress).add([d["path"] for d in DOCS])
    setup_s = time.time() - t_setup

    phases: dict[str, float] = {}

    progress("▶ compile (extraction)")
    t = time.time()
    stats = Compiler(cfg, conn, llm, progress=progress).compile_pending(force=True, use_llm=True)
    phases["compile"] = round(time.time() - t, 2)
    progress(f"✔ compile done in {phases['compile']}s (chunks={stats.chunks} claims={stats.claims})")

    progress("▶ embed")
    t = time.time()
    try:
        backfill(conn, load_provider(), progress=progress)
    except Exception as e:  # noqa: BLE001
        progress(f"⚠ embed failed: {e}")
    phases["embed"] = round(time.time() - t, 2)

    progress("▶ dedup")
    t = time.time()
    try:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM claims WHERE status='active' AND dedup_checked=0 ORDER BY id"
            )
        ]
        if ids:
            d = dedup_claims(conn, llm, ids, progress=progress)
            progress(f"  dedup: {d}")
    except Exception as e:  # noqa: BLE001
        progress(f"⚠ dedup failed: {e}")
    phases["dedup"] = round(time.time() - t, 2)

    progress("▶ backlinks")
    t = time.time()
    try:
        generate_all(conn, cfg, progress=progress)
    except Exception as e:  # noqa: BLE001
        progress(f"⚠ backlinks failed: {e}")
    phases["backlinks"] = round(time.time() - t, 2)

    dump_irs(conn, home / "ir")
    snap = db_snapshot(conn)
    out = {
        "arm": arm,
        "model": model,
        "base_url": base_url,
        "home": str(home),
        "setup_s": round(setup_s, 2),
        "phases": phases,
        "total_s": round(sum(phases.values()), 2),
        "stats": snap,
        "llm_summary": {
            "calls": len(llm.calls),
            "total_llm_s": round(sum(c["elapsed"] for c in llm.calls), 2),
            "errors": sum(1 for c in llm.calls if c["error"]),
            "input_chars": sum(c["input_chars"] for c in llm.calls),
            "output_chars": sum(c["output_chars"] for c in llm.calls),
        },
        "llm_calls": llm.calls,
        "log": log,
    }
    write_json(RESULTS / f"compile_{arm}.json", out)
    print(
        f"\n=== {arm} / {model} ===\n"
        f"phases={phases} total={out['total_s']}s\n"
        f"claims={snap['claims']} entities={snap['entities']} relations={snap['relations']}\n"
        f"llm calls={out['llm_summary']['calls']} llm_s={out['llm_summary']['total_llm_s']} "
        f"errors={out['llm_summary']['errors']}"
    )
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
