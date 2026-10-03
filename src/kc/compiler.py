"""Compiler orchestration: inbox → raw → parse → chunk → extract → persist."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .chunker import chunk_text
from .config import Config
from .ir import IR, extract_ir, ir_to_dict, merge_irs
from .llm import LLM, LLMUnavailable
from .parsers import ParserError, parse_file, render_pdf_pages
from .util import detect_lang, now_iso, norm_text, sha256_file


@dataclass
class CompileStats:
    scanned: int = 0
    compiled: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    claims: int = 0
    errors: list = field(default_factory=list)


class Compiler:
    def __init__(self, cfg: Config, conn, llm: LLM | None = None, progress=None):
        self.cfg = cfg
        self.conn = conn
        self.llm = llm
        self.progress = progress or (lambda msg: None)

    # ------------------------------------------------------------------ add
    def add(self, paths) -> list[Path]:
        added: list[Path] = []
        targets: list[Path] = []
        for p in paths:
            p = Path(p).expanduser()
            if p.is_dir():
                targets.extend(sorted(c for c in p.rglob("*") if c.is_file() and not c.name.startswith(".")))
            else:
                targets.append(p)
        for p in targets:
            added.extend(self._add_one(p))
        return added

    def _add_one(self, p: Path) -> list[Path]:
        p = p.resolve()
        if not p.is_file():
            self.progress(f"跳过（不存在）：{p}")
            return []
        if p.parent == self.cfg.dir("inbox").resolve():
            self.progress(f"已在 inbox：{p.name}")
            return []
        inbox = self.cfg.dir("inbox")
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / p.name
        if dest.exists():
            if sha256_file(dest) == sha256_file(p):
                self.progress(f"已在 inbox（相同内容）：{p.name}")
                return []
            dest = inbox / f"{p.stem}-{int(time.time())}{p.suffix}"
        shutil.copy2(p, dest)
        return [dest]

    # -------------------------------------------------------------- compile
    def compile_pending(
        self,
        force: bool = False,
        use_llm: bool = True,
        limit: int | None = None,
        all_raw: bool = False,
        ocr_pages: int = 10,
    ) -> CompileStats:
        inbox = self.cfg.dir("inbox")
        raw = self.cfg.dir("raw")
        sources: list[Path] = []
        for base in ([inbox] if inbox.exists() else []) + ([raw] if all_raw and raw.exists() else []):
            sources.extend(sorted(p for p in base.iterdir() if p.is_file() and not p.name.startswith(".")))
        stats = CompileStats(scanned=len(sources))
        processed = 0
        for src in sources:
            if limit is not None and processed >= limit:
                break
            sha = sha256_file(src)
            row = self.conn.execute(
                "SELECT status FROM compile_log WHERE sha256=?", (sha,)
            ).fetchone()
            if row and row["status"] == "ok" and not force:
                stats.skipped += 1
                if src.parent == inbox:
                    self._to_raw(src, sha)
                continue
            try:
                res = self._compile_file(src, sha, use_llm=use_llm, ocr_pages=ocr_pages)
                stats.compiled += 1
                stats.chunks += res["chunks"]
                stats.claims += res["claims"]
            except Exception as e:  # noqa: BLE001
                stats.failed += 1
                stats.errors.append(f"{src.name}: {e}")
                self._log(sha, None, str(src), "error", str(e)[:500])
            processed += 1
        return stats

    def _to_raw(self, src: Path, sha: str) -> Path:
        raw = self.cfg.dir("raw")
        raw.mkdir(parents=True, exist_ok=True)
        dest = raw / f"{sha[:16]}__{src.name}"
        if dest.exists():
            if sha256_file(dest) == sha:  # inbox copy is redundant
                src.unlink(missing_ok=True)
                return dest
            dest = raw / f"{sha[:16]}-{int(time.time())}__{src.name}"
        shutil.move(str(src), str(dest))
        return dest

    def _compile_file(self, src: Path, sha: str, use_llm: bool, ocr_pages: int) -> dict:
        doc_id = sha[:16]
        self.progress(f"⟳ {src.name}")
        orig_stem = src.stem
        raw_path = src
        if src.parent == self.cfg.dir("inbox"):
            raw_path = self._to_raw(src, sha)  # immutable original
        parsed = parse_file(raw_path)
        if parsed.title in (raw_path.stem, ""):
            parsed.title = orig_stem  # strip sha-prefix picked up after the move
        if parsed.meta.get("needs_ocr"):
            if not (use_llm and self.llm):
                raise ParserError(f"{parsed.source_type} needs OCR but LLM disabled/unavailable")
            parsed.text = self._ocr_text(raw_path, parsed.source_type, ocr_pages)
            parsed.meta["ocr"] = True
            if not parsed.text.strip():
                raise ParserError("OCR produced no text")
        text = parsed.text.strip()
        if not text:
            raise ParserError("no extractable text")
        chunks = chunk_text(text)
        if not chunks:
            raise ParserError("chunking produced nothing")

        ir = IR()
        model_used = ""
        if use_llm and self.llm:
            batches = self._batches(chunks, self.cfg.batch_chars)
            parts: list[IR] = []
            llm_failed = False
            for i, b in enumerate(batches, 1):
                if llm_failed:
                    break
                self.progress(f"  ⤳ 抽取 {i}/{len(batches)}")
                for attempt in (1, 2, 3):  # transient NPU model-switch failures
                    try:
                        parts.append(extract_ir(self.llm, b))
                        break
                    except LLMUnavailable as e:
                        if attempt == 3:
                            self.progress(f"  ⚠ LLM 不可用（已重试 2 次），降级：{e}")
                            llm_failed = True
                        else:
                            wait = 5 * attempt
                            self.progress(f"  ⚠ LLM 暂不可用，{wait}s 后重试（{attempt}/2）：{e}")
                            time.sleep(wait)
            if parts:
                ir = merge_irs(parts)
                model_used = self.llm.model
        if not ir.summary:
            ir.summary = chunks[0][:200]

        self._persist(doc_id, raw_path, parsed, text, chunks, ir, sha, model_used)
        self._log(sha, doc_id, str(raw_path), "ok", None)
        self.progress(f"  ✓ {doc_id} chunks={len(chunks)} claims={len(ir.claims)} entities={len(ir.entities)}")
        return {"doc_id": doc_id, "chunks": len(chunks), "claims": len(ir.claims), "entities": len(ir.entities)}

    def _ocr_text(self, raw_path: Path, source_type: str, ocr_pages: int) -> str:
        assert self.llm is not None
        import tempfile

        if source_type == "image":
            return self.llm.ocr(raw_path).strip()
        if source_type == "pdf":
            with tempfile.TemporaryDirectory(prefix="kc-ocr-") as td:
                pages = render_pdf_pages(raw_path, max_pages=ocr_pages, outdir=Path(td))
                texts: list[str] = []
                for page in pages:
                    self.progress(f"  ⤳ OCR {page.name}")
                    t = self.llm.ocr(page).strip()
                    if t:
                        texts.append(f"[page {len(texts) + 1}]\n{t}")
                return "\n\n".join(texts)
        raise ParserError(f"OCR not supported for {source_type}")

    @staticmethod
    def _batches(chunks: list[str], max_chars: int) -> list[str]:
        batches: list[str] = []
        cur = ""
        for c in chunks:
            if cur and len(cur) + len(c) + 2 > max_chars:
                batches.append(cur)
                cur = c
            else:
                cur = (cur + "\n\n" + c) if cur else c
        if cur:
            batches.append(cur)
        return batches

    # -------------------------------------------------------------- persist
    def _persist(self, doc_id, raw_path: Path, parsed, text, chunks, ir: IR, sha, model_used) -> None:
        conn = self.conn
        title = parsed.title or raw_path.stem
        compiled_at = now_iso()
        # idempotent recompile: cascade wipes chunks/claims/mentions/relations
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        conn.execute(
            """INSERT INTO documents
               (id,title,source_path,source_type,sha256,content,char_count,lang,
                summary,model,created_at,compiled_at,ir_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doc_id,
                title,
                str(raw_path),
                parsed.source_type,
                sha,
                text,
                len(text),
                detect_lang(text),
                ir.summary,
                model_used,
                compiled_at,
                compiled_at,
                json.dumps(ir_to_dict(ir), ensure_ascii=False),
            ),
        )
        for i, c in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunks(document_id, ord, text, char_count) VALUES(?,?,?,?)",
                (doc_id, i, c, len(c)),
            )
        for c in ir.claims:
            conn.execute(
                """INSERT INTO claims
                   (document_id, text, norm, type, confidence, source, status, created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (doc_id, c["text"], norm_text(c["text"]), c["type"], c["confidence"], raw_path.name, "active", compiled_at),
            )
        ent_ids: set[int] = set()
        for e in ir.entities:
            norm = norm_text(e["name"])
            if not norm:
                continue
            conn.execute(
                """INSERT INTO entities(name, norm, type) VALUES(?,?,?)
                   ON CONFLICT(norm) DO UPDATE SET name=excluded.name, type=excluded.type""",
                (e["name"], norm, e["type"]),
            )
            row = conn.execute("SELECT id FROM entities WHERE norm=?", (norm,)).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO entity_mentions(entity_id, document_id) VALUES(?,?)",
                (row["id"], doc_id),
            )
            ent_ids.add(row["id"])
        for eid in ent_ids:
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM entity_mentions WHERE entity_id=?", (eid,)
            ).fetchone()["c"]
            conn.execute("UPDATE entities SET doc_count=? WHERE id=?", (cnt, eid))
        for r in ir.relations:
            conn.execute(
                "INSERT INTO relations(document_id, subject, predicate, object, confidence) VALUES(?,?,?,?,?)",
                (doc_id, r["subject"], r["predicate"], r["object"], 0.6),
            )
        conn.commit()
        self._write_document_md(doc_id, title, raw_path, parsed, compiled_at)

    def _write_document_md(self, doc_id, title, raw_path: Path, parsed, compiled_at) -> None:
        outdir = self.cfg.dir("documents")
        outdir.mkdir(parents=True, exist_ok=True)
        safe_title = (title or "").replace('"', "'").replace("\\", "/")[:120]
        fm = "\n".join(
            [
                "---",
                f'id: "{doc_id}"',
                f'title: "{safe_title}"',
                f'source: "{raw_path.name}"',
                f'type: "{parsed.source_type}"',
                f'compiled_at: "{compiled_at}"',
                "---",
                "",
            ]
        )
        text = (parsed.text or "").strip()
        (outdir / f"{doc_id}.md").write_text(fm + text + "\n", encoding="utf-8")

    def _log(self, sha, doc_id, source_path, status, error) -> None:
        self.conn.execute(
            """INSERT INTO compile_log(sha256, document_id, source_path, status, error, compiled_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(sha256) DO UPDATE SET
                 document_id=excluded.document_id, source_path=excluded.source_path,
                 status=excluded.status, error=excluded.error, compiled_at=excluded.compiled_at""",
            (sha, doc_id, source_path, status, error, now_iso()),
        )
        self.conn.commit()
