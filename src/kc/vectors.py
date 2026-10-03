"""Pure-Python float32 vectors: ngram embedding, cosine, top-k scan.

No numpy on this box; personal-KB scale (thousands × 512-dim) makes
brute-force pure Python entirely adequate (tens of milliseconds).
"""

from __future__ import annotations

import hashlib
import math
import struct
from array import array

DIM = 512
NGRAM_SIZES = (2, 3, 4)


def _hash(gram: str) -> int:
    h = 2166136261
    for ch in gram:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def embed_ngram(text: str, dim: int = DIM) -> bytes:
    """Deterministic char-ngram hashing embedding (signed hashing trick).

    TF weighting (1+log tf), ±1 bucket direction from a hash bit,
    L2-normalized, float32 little-endian.

    NOTE: this is lexical-fuzzy similarity, not true semantics — see
    docs/v0.2-plan.md §0. Its value: recall + infrastructure that a real
    /v1/embeddings endpoint can drop into unchanged.
    """
    t = "".join((text or "").lower().split())
    if len(t) < min(NGRAM_SIZES):
        return bytes(dim * 4)
    counts: dict[int, float] = {}
    for n in NGRAM_SIZES:
        for i in range(len(t) - n + 1):
            h = _hash(t[i : i + n])
            counts[h] = counts.get(h, 0.0) + 1.0
    vec = [0.0] * dim
    for h, tf in counts.items():
        bucket = h % dim
        sign = 1.0 if (h >> 20) & 1 else -1.0
        vec[bucket] += sign * (1.0 + math.log(tf))
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return struct.pack(f"<{dim}f", *(x / norm for x in vec))


def content_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def cosine(a: bytes, b: bytes) -> float:
    va, vb = unpack(a), unpack(b)
    n = min(len(va), len(vb))
    if n == 0:
        return 0.0
    dot = 0.0
    for i in range(n):
        dot += va[i] * vb[i]
    return dot  # vectors are L2-normalized at build time


def topk(query_vec: bytes, candidates: list[tuple[int, bytes]], k: int) -> list[tuple[float, int]]:
    """candidates: [(id, vec)] → top-k [(score, id)] descending."""
    scored = [(cosine(query_vec, vec), cid) for cid, vec in candidates]
    scored.sort(key=lambda x: -x[0])
    return scored[:k]
