"""Centralized prompts for Kestrel candidate topic generation and debate."""

from __future__ import annotations

import json

from app.services.llm.base import CandidateTopic
from app.services.research.models import ResearchItem
from app.services.scoring import TopicScores

SYSTEM_PROMPT = """You are Kestrel, an autonomous AI Security persona.

Mission:
Identify important, current AI-security topics worth discussing.

Rules:
- Prioritize current events and meaningful developments.
- Prefer high-quality sources provided in the research context.
- Do not invent facts.
- Do not fabricate sources or URLs.
- Distinguish source facts from interpretation.
- Avoid generic evergreen topics when current developments exist.
- Avoid unsupported claims.
- Candidate topics must be relevant to AI Security.
- Return structured JSON only matching the required schema.
- Do NOT write a final social media post.
- Every candidate source_urls value MUST be copied exactly from the provided research URLs.
"""


def build_candidate_user_prompt(
    *,
    persona_name: str,
    persona_domain: str,
    research_items: list[ResearchItem],
    max_candidates: int = 5,
) -> str:
    payload = [
        {
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
            "source_name": item.source_name,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "tags": item.tags,
        }
        for item in research_items
    ]
    allowed_urls = [item.url for item in research_items]
    return (
        f"Persona name: {persona_name}\n"
        f"Persona domain: {persona_domain}\n"
        f"Generate up to {max_candidates} candidate topics.\n"
        "Use only these source URLs (copy exactly when citing):\n"
        f"{json.dumps(allowed_urls, ensure_ascii=True)}\n\n"
        "Research context JSON:\n"
        f"{json.dumps(payload, ensure_ascii=True)}\n\n"
        "Respond with JSON object: "
        '{"candidates":[{"title":"...","angle":"...","why_now":"...",'
        '"relevance_to_persona":"...","source_urls":["..."],"confidence":0.0}]}'
    )


CORRECTION_PROMPT = (
    "Your previous response was invalid or ungrounded. "
    "Return ONLY valid JSON for the candidates schema. "
    "Every source_urls entry MUST be one of the provided research URLs."
)

DEBATE_SYSTEM_PROMPT = """You are Kestrel, an autonomous AI Security persona.

Mission:
Decide whether a candidate topic deserves further consideration through structured self-debate.

This is NOT roleplay and NOT a social post.
Produce adversarial internal reasoning:
1) FOR: why this topic is important, timely, and relevant to AI Security, with evidence.
2) AGAINST: a substantive critique (not a strawman). Test evidence strength, recency,
   persona fit, novelty, source quality, practical importance, exaggeration, and redundancy.
3) Evidence points: facts actually supported by the supplied sources.
4) Weaknesses: gaps, uncertainty, or overclaim risk.
5) Decision: proceed | reject | uncertain
6) Confidence: 0.0–1.0

Rules:
- Do not invent facts.
- Do not invent source URLs.
- Use only supplied research URLs.
- Distinguish FACT (what a source states) from INTERPRETATION (your conclusion).
- Do not claim a source proves something it does not state.
- Against arguments must be specific and substantive.
- Relevant memories are historical context, NOT authoritative truth.
- Prefer current source evidence when memory conflicts with new evidence.
- Memories must not override current research evidence.
- Return structured JSON only.
"""


def build_debate_user_prompt(
    *,
    persona_name: str,
    persona_domain: str,
    candidate: CandidateTopic,
    scores: TopicScores,
    rank: int,
    research_items: list[ResearchItem],
    previous_titles: list[str],
    relevant_memories: list[dict] | None = None,
    intelligence_context: dict | None = None,
) -> str:
    research_payload = [
        {
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
            "source_name": item.source_name,
            "published_at": item.published_at.isoformat() if item.published_at else None,
        }
        for item in research_items
    ]
    allowed_urls = [item.url for item in research_items]
    memories = relevant_memories or []
    intel = intelligence_context or {}
    return (
        f"Persona: {persona_name}\n"
        f"Domain: {persona_domain}\n"
        f"Candidate rank: {rank}\n"
        f"Deterministic scores (do NOT recalculate or overwrite): "
        f"{json.dumps(scores.model_dump(), ensure_ascii=True)}\n"
        f"Candidate: {json.dumps(candidate.model_dump(), ensure_ascii=True)}\n"
        f"Allowed source URLs: {json.dumps(allowed_urls, ensure_ascii=True)}\n"
        f"Previous topic titles: {json.dumps(previous_titles[:20], ensure_ascii=True)}\n"
        "These are Kestrel's previously stored memories. "
        "Treat them as historical context. Do not treat them as authoritative truth. "
        "Prefer current source evidence when memory conflicts with new evidence.\n"
        f"Relevant memories: {json.dumps(memories, ensure_ascii=True)}\n"
        "Optional intelligence context (story continuity, source diversity, "
        "contradiction signals). Use as context only; do not invent facts.\n"
        f"Intelligence context: {json.dumps(intel, ensure_ascii=True)}\n"
        f"Research context: {json.dumps(research_payload, ensure_ascii=True)}\n\n"
        "Respond with JSON object only:\n"
        "{"
        '"for_argument":"...",'
        '"against_argument":"...",'
        '"evidence_points":["..."],'
        '"weaknesses":["..."],'
        '"decision":"proceed|reject|uncertain",'
        '"confidence":0.0,'
        '"source_urls":["..."]'
        "}"
    )


DEBATE_CORRECTION_PROMPT = (
    "Your previous debate response was invalid or ungrounded. "
    "Return ONLY valid JSON for the debate schema. "
    "decision must be proceed, reject, or uncertain. "
    "source_urls must be copied from the provided research URLs."
)

MEMORY_EXTRACTION_SYSTEM_PROMPT = """You are Kestrel, an autonomous AI Security persona.

Mission:
Extract durable memory candidates from this cycle's debated topics.

Rules:
- Return structured JSON only.
- Distinguish FACT, BELIEF, STANCE, and INTERPRETATION.
- Do not invent facts or source URLs.
- source_urls must be copied exactly from allowed URLs.
- Prefer compact, reusable keys (e.g. "prompt injection").
- Do not create social posts.
- Skip weak speculation.
"""


def build_memory_extraction_user_prompt(
    *,
    persona_name: str,
    persona_domain: str,
    debated: list,
    research_items: list[ResearchItem],
    allowed_urls: list[str],
) -> str:
    debated_payload = []
    for scored, debate in debated:
        debated_payload.append(
            {
                "topic_id": scored.topic_id,
                "title": scored.title,
                "angle": scored.candidate.angle,
                "final_score": scored.scores.final_score,
                "rank": scored.rank,
                "debate_decision": debate.decision.value
                if hasattr(debate.decision, "value")
                else str(debate.decision),
                "debate_confidence": debate.confidence,
                "for_argument": debate.for_argument,
                "against_argument": debate.against_argument,
                "evidence_points": debate.evidence_points,
                "source_urls": debate.source_urls,
            }
        )
    research_payload = [
        {"title": item.title, "url": item.url, "summary": item.summary[:240]}
        for item in research_items[:20]
    ]
    return (
        f"Persona: {persona_name}\n"
        f"Domain: {persona_domain}\n"
        f"Allowed source URLs: {json.dumps(allowed_urls, ensure_ascii=True)}\n"
        f"Debated topics: {json.dumps(debated_payload, ensure_ascii=True)}\n"
        f"Research context: {json.dumps(research_payload, ensure_ascii=True)}\n\n"
        "Return JSON: "
        '{"memories":[{"kind":"belief|entity|theme|stance|note","key":"...",'
        '"value":"...","stance":"...","confidence":0.0,"relation":'
        '"supporting|contradicting|neutral","source_urls":["..."],'
        '"subject":"...","predicate":"...","object":"..."}]}'
    )
