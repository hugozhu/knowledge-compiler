"""Embedding providers: deterministic ngram (default) + OpenAI-compatible endpoint.

Provider selection: KC_EMBED_PROVIDER=auto|ngram|openai (default auto —
probe the OpenAI endpoint once, fall back to ngram when unavailable).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .vectors import DIM, embed_ngram

NGRAM_MODEL = "ngram-v1"


class EmbeddingProvider:
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[bytes]:
        raise NotImplementedError

    def available(self) -> bool:
        return True


class NgramProvider(EmbeddingProvider):
    def __init__(self, dim: int = DIM):
        self.name = NGRAM_MODEL
        self.dim = dim

    def embed(self, texts: list[str]) -> list[bytes]:
        return [embed_ngram(t, self.dim) for t in texts]


class OpenAIEmbedding(EmbeddingProvider):
    def __init__(self, base_url: str, api_key: str, model: str, dim: int, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self._ok: bool | None = None

    def available(self) -> bool:
        if self._ok is None:
            try:
                self.embed(["probe"])
                self._ok = True
            except Exception:  # noqa: BLE001
                self._ok = False
        return self._ok

    def embed(self, texts: list[str]) -> list[bytes]:
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps({"model": self.model, "input": texts}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            raise RuntimeError(f"embedding endpoint failed: {e}") from e
        import struct

        vecs: list[bytes] = []
        for item in out.get("data", []):
            vals = item.get("embedding") or []
            vecs.append(struct.pack(f"<{len(vals)}f", *vals))
        if len(vecs) != len(texts):
            raise RuntimeError(f"embedding count mismatch: {len(vecs)} != {len(texts)}")
        return vecs


def load_provider() -> EmbeddingProvider:
    pref = os.environ.get("KC_EMBED_PROVIDER", "auto").lower()
    if pref in ("openai", "auto"):
        base = os.environ.get("KC_EMBED_BASE_URL", "").rstrip("/")
        if base:
            p = OpenAIEmbedding(
                base_url=base,
                api_key=os.environ.get("KC_EMBED_API_KEY", ""),
                model=os.environ.get("KC_EMBED_MODEL", "text-embedding-3-small"),
                dim=int(os.environ.get("KC_EMBED_DIM", "1536")),
            )
            if p.available():
                return p
            if pref == "openai":
                raise RuntimeError(f"KC_EMBED_BASE_URL={base} 不可用")
    return NgramProvider()


# ------------------------------------------------------------------ backfill
def backfill(conn, provider: EmbeddingProvider, force: bool = False, progress=None) -> dict:
    """Incrementally embed active chunks + claims; skip unchanged content_sha."""
    progress = progress or (lambda msg: None)
    stats = {"chunks_done": 0, "claims_done": 0, "chunks_skip": 0, "claims_skip": 0}

    def _rows(scope: str):
        if scope == "chunk":
            return conn.execute(
                "SELECT c.id AS rid, c.text AS text FROM chunks c "
                "JOIN documents d ON d.id=c.document_id ORDER BY c.id"
            ).fetchall()
        return conn.execute(
            "SELECT cl.id AS rid, cl.text AS text FROM claims cl "
            "WHERE cl.status='active' ORDER BY cl.id"
        ).fetchall()

    for scope in ("chunk", "claim"):
        rows = _rows(scope)
        todo = []
        for r in rows:
            sha = _sha(r["text"])
            if force:
                todo.append((r["rid"], r["text"], sha))
                continue
            hit = conn.execute(
                "SELECT 1 FROM embeddings WHERE scope=? AND ref_id=? AND model=? AND content_sha=?",
                (scope, r["rid"], provider.name, sha),
            ).fetchone()
            if hit:
                stats[f"{scope}s_skip"] += 1
            else:
                todo.append((r["rid"], r["text"], sha))
        if not todo:
            continue
        if force:  # rebuild everything for this scope+model
            conn.execute(
                "DELETE FROM embeddings WHERE scope=? AND model=?", (scope, provider.name)
            )
        # batch embed in slices of 64
        for i in range(0, len(todo), 64):
            batch = todo[i : i + 64]
            vecs = provider.embed([t for _, t, _ in batch])
            conn.executemany(
                """INSERT INTO embeddings(scope, ref_id, model, dim, vec, content_sha)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(scope, ref_id, model) DO UPDATE SET
                     dim=excluded.dim, vec=excluded.vec, content_sha=excluded.content_sha""",
                [
                    (scope, rid, provider.name, provider.dim, vec, sha)
                    for (rid, _, sha), vec in zip(batch, vecs)
                ],
            )
        stats[f"{scope}s_done"] = len(todo)
        progress(f"  ⤳ embed {scope}: {len(todo)}（skip {stats[f'{scope}s_skip']}）")
    # drop orphaned embeddings (claim superseded / chunk deleted)
    conn.execute(
        """DELETE FROM embeddings WHERE scope='chunk' AND ref_id NOT IN (SELECT id FROM chunks)"""
    )
    conn.execute(
        """DELETE FROM embeddings WHERE scope='claim' AND ref_id NOT IN (SELECT id FROM claims)"""
    )
    conn.commit()
    return stats


def _sha(text: str) -> str:
    from .vectors import content_hash

    return content_hash(text)
