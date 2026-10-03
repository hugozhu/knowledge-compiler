"""Paragraph-aware chunker: target ~900 chars, hard max ~1200, overlap ~120."""

from __future__ import annotations

import re

SENTENCE_ENDS = "。！？!?…；;\n"


def split_paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text)]
    return [p for p in paras if p]


def _split_sentences(p: str) -> list[str]:
    sents, cur = [], ""
    for ch in p:
        cur += ch
        if ch in SENTENCE_ENDS:
            sents.append(cur)
            cur = ""
    if cur:
        sents.append(cur)
    return sents


def _split_long(p: str, maximum: int) -> list[str]:
    if len(p) <= maximum:
        return [p]
    parts: list[str] = []
    cur = ""
    for sent in _split_sentences(p):
        if len(sent) > maximum:  # pathological: hard cut
            if cur:
                parts.append(cur)
                cur = ""
            for i in range(0, len(sent), maximum):
                parts.append(sent[i : i + maximum])
            continue
        if cur and len(cur) + len(sent) > maximum:
            parts.append(cur)
            cur = ""
        cur += sent
    if cur:
        parts.append(cur)
    return parts


def chunk_text(
    text: str,
    target: int = 900,
    maximum: int = 1200,
    overlap: int = 120,
) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    unit_max = maximum - overlap - 2
    units: list[str] = []
    for para in split_paragraphs(text):
        units.extend(_split_long(para, unit_max))
    chunks: list[str] = []
    cur = ""
    for u in units:
        if cur and (len(cur) >= target or len(cur) + len(u) + 2 > maximum):
            chunks.append(cur)
            tail = cur[-overlap:]
            cur = (tail + "\n" + u) if tail else u
        else:
            cur = (cur + "\n\n" + u) if cur else u
    if cur:
        chunks.append(cur)
    return chunks
