"""Knowledge IR: structure, validation, merging, and LLM extraction prompt."""

from __future__ import annotations

from dataclasses import dataclass, field

from .llm import LLM, LLMUnavailable
from .util import extract_json, norm_text

CLAIM_TYPES = {"fact", "opinion", "hypothesis", "decision", "observation", "prediction"}
ENTITY_TYPES = {"person", "org", "project", "product", "technology", "place", "other"}

MAX_CLAIMS = 40
MAX_ENTITIES = 20
MAX_RELATIONS = 30
MAX_TOPICS = 12


@dataclass
class IR:
    summary: str = ""
    topics: list = field(default_factory=list)
    entities: list = field(default_factory=list)  # {"name","type"}
    claims: list = field(default_factory=list)  # {"text","type","confidence"}
    relations: list = field(default_factory=list)  # {"subject","predicate","object"}


def ir_to_dict(ir: IR) -> dict:
    return {
        "summary": ir.summary,
        "topics": ir.topics,
        "entities": ir.entities,
        "claims": ir.claims,
        "relations": ir.relations,
    }


def _clamp_conf(v) -> float:
    try:
        f = float(v) if v is not None else 0.6
    except (TypeError, ValueError):
        f = 0.6
    return round(min(max(f, 0.0), 1.0), 2)


def coerce_ir(raw) -> IR:
    """Validate/normalize a parsed JSON payload into an IR; drop bad entries."""
    ir = IR()
    if not isinstance(raw, dict):
        return ir
    ir.summary = str(raw.get("summary") or "").strip()[:500]
    topics = raw.get("topics")
    if isinstance(topics, list):
        seen = set()
        for t in topics:
            s = str(t).strip()[:40]
            if s and s not in seen:
                seen.add(s)
                ir.topics.append(s)
        ir.topics = ir.topics[:MAX_TOPICS]
    ents = raw.get("entities")
    if isinstance(ents, list):
        for e in ents:
            if isinstance(e, dict):
                name = str(e.get("name") or "").strip()
                if not name:
                    continue
                t = str(e.get("type") or "other").strip().lower()
                ir.entities.append({"name": name[:80], "type": t if t in ENTITY_TYPES else "other"})
            elif isinstance(e, str) and e.strip():
                ir.entities.append({"name": e.strip()[:80], "type": "other"})
        ir.entities = ir.entities[:MAX_ENTITIES]
    claims = raw.get("claims")
    if isinstance(claims, list):
        for c in claims:
            if isinstance(c, dict):
                text = str(c.get("text") or "").strip()
                if not text:
                    continue
                t = str(c.get("type") or "").strip().lower()
                ir.claims.append(
                    {
                        "text": text[:300],
                        "type": t if t in CLAIM_TYPES else "fact",
                        "confidence": _clamp_conf(c.get("confidence")),
                    }
                )
            elif isinstance(c, str) and c.strip():
                ir.claims.append({"text": c.strip()[:300], "type": "fact", "confidence": 0.6})
        ir.claims = ir.claims[:MAX_CLAIMS]
    rels = raw.get("relations")
    if isinstance(rels, list):
        for r in rels:
            if isinstance(r, dict):
                s = str(r.get("subject") or "").strip()
                p = str(r.get("predicate") or "").strip()
                o = str(r.get("object") or "").strip()
                if s and p and o:
                    ir.relations.append({"subject": s[:80], "predicate": p[:60], "object": o[:80]})
        ir.relations = ir.relations[:MAX_RELATIONS]
    return ir


def merge_irs(parts: list[IR]) -> IR:
    """Merge batch IRs: keep first summary, concat + dedup by norm text."""
    out = IR()
    seen_claims: set[str] = set()
    seen_ents: set[str] = set()
    seen_rels: set[tuple] = set()
    for ir in parts:
        if ir.summary and not out.summary:
            out.summary = ir.summary
        for t in ir.topics:
            if t not in out.topics:
                out.topics.append(t)
        for e in ir.entities:
            key = norm_text(e["name"])
            if key and key not in seen_ents:
                seen_ents.add(key)
                out.entities.append(e)
        for c in ir.claims:
            key = norm_text(c["text"])
            if key and key not in seen_claims:
                seen_claims.add(key)
                out.claims.append(c)
        for r in ir.relations:
            key = (norm_text(r["subject"]), norm_text(r["predicate"]), norm_text(r["object"]))
            if key not in seen_rels:
                seen_rels.add(key)
                out.relations.append(r)
    out.topics = out.topics[:MAX_TOPICS]
    out.entities = out.entities[:MAX_ENTITIES]
    out.claims = out.claims[:MAX_CLAIMS]
    out.relations = out.relations[:MAX_RELATIONS]
    return out


SYSTEM_EXTRACT = "你是知识编译器的语义抽取模块。只输出一个 JSON 对象，不要任何解释、不要 Markdown 代码块。"

# 解析失败时的兜底：小模型输出超 max_tokens 会被截断成非法 JSON，
# 用严格限额重试一次，把输出压进预算内。
COMPACT_LIMITS = (
    "严格限制输出规模（必须适配小模型上下文）：summary 不超过 2 句；"
    "topics 最多 3 个；entities 最多 4 个；claims 最多 4 条（每条不超过 30 字）；"
    "relations 最多 3 条。只输出 JSON 对象本身。"
)


def extraction_user_prompt(batch_text: str) -> str:
    return f"""阅读下面的文本，抽取结构化知识。只输出一个 JSON 对象，字段：
{{
  "summary": "1-3 句话概括本段内容",
  "topics": ["主题词"],
  "entities": [{{"name": "实体名", "type": "person|org|project|product|technology|place|other"}}],
  "claims": [{{"text": "一句可独立理解的论断", "type": "fact|opinion|hypothesis|decision|observation|prediction", "confidence": 0.0到1.0}}],
  "relations": [{{"subject": "实体", "predicate": "关系", "object": "实体"}}]
}}
要求：claims 是文中最重要的论断，具体、可独立理解；没有内容的字段用空数组。最多 8 条 claims。

文本：
\"\"\"
{batch_text}
\"\"\""""


def extract_ir(llm: LLM, batch_text: str, retries: int = 1) -> IR:
    """One extraction call with retries; degrades to empty IR, never raises.

    尝试顺序：常规提示 →（可重试）仅 JSON 提示 → 严格限额提示。最后一步专治
    「模型输出过长被 max_tokens 截断 → 非法 JSON」——空数组/正常输出不受影响。
    """
    prompt = extraction_user_prompt(batch_text)
    attempts = [prompt]
    for _ in range(max(retries, 0)):
        attempts.append("只输出 JSON 对象本身，不要任何其他文字。\n" + prompt)
    attempts.append(prompt + "\n\n" + COMPACT_LIMITS)
    last_err = ""
    for p in attempts:
        try:
            content = llm.chat(SYSTEM_EXTRACT, p, temperature=0.2, max_tokens=768)
        except LLMUnavailable:
            raise  # service-level failure → caller decides degradation
        raw = extract_json(content)
        if raw is not None:
            return coerce_ir(raw)
        last_err = (content or "")[:80]
    return IR(summary=f"[extract-failed] {last_err}")
