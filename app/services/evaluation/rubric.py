"""Deterministic post-quality rubric (Module 14).

Module 5 answers: should we select this topic?
Module 14 answers: how good was the published output?
Weights are independent of Module 5.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from app.services.adaptive_learning.coverage import classify_angle, normalize_concept
from app.services.evaluation.models import DimensionScores, QualityClass, clamp_unit
from app.services.research.base import normalize_url
from app.services.scoring import jaccard_similarity, tokenize

EVAL_WEIGHTS: dict[str, float] = {
    "evidence_quality": 0.20,
    "relevance_score": 0.20,
    "novelty_score": 0.15,
    "reasoning_quality": 0.15,
    "source_diversity_score": 0.10,
    "consistency_score": 0.10,
    "rationale_quality": 0.10,
}
assert abs(sum(EVAL_WEIGHTS.values()) - 1.0) < 1e-9

_HYPE_PATTERNS = (
    r"\babsolutely\b",
    r"\bguaranteed\b",
    r"\bunquestionably\b",
    r"\bshocking\b",
    r"\bmind[- ]blowing\b",
    r"\bhuge breakthrough\b",
    r"!!!+",
)
_CERTAINTY = (
    r"\bdefinitely proven\b",
    r"\bno doubt\b",
    r"\b100%\b",
    r"\balways safe\b",
)
_SECURITY_TERMS = (
    "security",
    "agent",
    "agents",
    "prompt",
    "injection",
    "adversarial",
    "malware",
    "vulnerability",
    "exploit",
    "threat",
    "defense",
    "safety",
    "attack",
    "llm",
    "model",
)
_IRRELEVANT = (
    "smartphone review",
    "celebrity",
    "sports score",
    "recipe",
    "fashion week",
)
_PIPELINE_LEAK = (
    "pipeline_run",
    "scores_json",
    "debate_json",
    "consistency_json",
    "module 5",
    "module 8",
    "generation cycle",
    "openai_api_key",
)


def classify_quality(
    overall: float,
    *,
    excellent: float = 0.85,
    good: float = 0.70,
    acceptable: float = 0.55,
    weak: float = 0.40,
) -> QualityClass:
    score = clamp_unit(overall)
    if score >= excellent:
        return "excellent"
    if score >= good:
        return "good"
    if score >= acceptable:
        return "acceptable"
    if score >= weak:
        return "weak"
    return "failed"


def weighted_overall(scores: DimensionScores) -> float:
    data = scores.to_dict()
    total = 0.0
    for key, weight in EVAL_WEIGHTS.items():
        total += float(data.get(key, 0.0)) * weight
    return clamp_unit(total)


def _valid_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def score_evidence_quality(
    *,
    post_urls: list[str],
    grounded_urls: set[str] | list[str],
    research_urls: set[str] | list[str] | None = None,
) -> tuple[float, list[str], list[str]]:
    strengths: list[str] = []
    weaknesses: list[str] = []
    if not post_urls:
        weaknesses.append("no_source_urls")
        return 0.0, strengths, weaknesses

    grounded_norm = {
        normalize_url(u) or u.strip() for u in grounded_urls if str(u).strip()
    }
    research_norm = {
        normalize_url(u) or u.strip()
        for u in (research_urls or grounded_urls)
        if str(u).strip()
    }
    valid = 0
    grounded_hits = 0
    research_hits = 0
    fabricated = 0
    for raw in post_urls:
        if not _valid_http_url(raw):
            continue
        valid += 1
        key = normalize_url(raw) or raw.strip()
        if key in grounded_norm:
            grounded_hits += 1
        else:
            fabricated += 1
        if key in research_norm:
            research_hits += 1

    if valid == 0:
        weaknesses.append("invalid_source_urls")
        return 0.1, strengths, weaknesses

    ratio_grounded = grounded_hits / max(1, len(post_urls))
    ratio_research = research_hits / max(1, len(post_urls))
    score = 0.35 * (valid / max(1, len(post_urls)))
    score += 0.45 * ratio_grounded
    score += 0.20 * ratio_research
    if fabricated:
        score *= max(0.2, 1.0 - 0.35 * fabricated)
        weaknesses.append("ungrounded_source_urls")
    else:
        strengths.append("sources_grounded")
    if ratio_grounded >= 0.8:
        strengths.append("strong_evidence_linkage")
    return clamp_unit(score), strengths, weaknesses


def score_relevance(
    *,
    text: str,
    topic: str | None,
    persona_domain: str = "AI Security",
) -> tuple[float, list[str], list[str]]:
    strengths: list[str] = []
    weaknesses: list[str] = []
    blob = f"{topic or ''} {text}".lower()
    if any(term in blob for term in _IRRELEVANT):
        weaknesses.append("domain_drift")
        return 0.15, strengths, weaknesses
    hits = sum(1 for term in _SECURITY_TERMS if term in blob)
    domain_token = persona_domain.lower().split()
    domain_hits = sum(1 for t in domain_token if t in blob)
    score = clamp_unit(0.2 + 0.12 * min(hits, 5) + 0.1 * min(domain_hits, 2))
    if hits >= 3:
        strengths.append("ai_security_relevance")
    else:
        weaknesses.append("weak_security_relevance")
    if "agent" in blob or "agentic" in blob:
        strengths.append("agentic_focus")
        score = clamp_unit(score + 0.08)
    return score, strengths, weaknesses


def score_novelty(
    *,
    text: str,
    topic: str | None,
    previous_posts: list[object],
) -> tuple[float, list[str], list[str]]:
    strengths: list[str] = []
    weaknesses: list[str] = []
    if not previous_posts:
        strengths.append("first_post_novelty")
        return 0.85, strengths, weaknesses

    concept = normalize_concept(topic or text[:160])
    angle = classify_angle(f"{topic or ''} {text[:200]}")
    max_title_sim = 0.0
    concept_hits = 0
    angle_hits = 0
    phrase_hits = 0
    text_tokens = tokenize(text)
    for post in previous_posts[:20]:
        prev_topic = str(getattr(post, "topic", None) or getattr(post, "text", "")[:120])
        prev_text = str(getattr(post, "text", "") or "")
        max_title_sim = max(
            max_title_sim,
            jaccard_similarity(topic or text[:120], prev_topic),
        )
        if normalize_concept(prev_topic) == concept:
            concept_hits += 1
        if classify_angle(prev_topic) == angle:
            angle_hits += 1
        prev_tokens = tokenize(prev_text)
        if text_tokens and prev_tokens:
            overlap = len(text_tokens & prev_tokens) / max(1, len(text_tokens))
            if overlap >= 0.55:
                phrase_hits += 1

    score = 1.0
    score -= 0.55 * max_title_sim
    score -= 0.08 * min(concept_hits, 4)
    score -= 0.06 * min(angle_hits, 4)
    score -= 0.12 * min(phrase_hits, 3)
    if max_title_sim >= 0.7:
        weaknesses.append("repeated_title")
    if concept_hits >= 2:
        weaknesses.append("repeated_topic")
    if angle_hits >= 3:
        weaknesses.append("repeated_angle")
    if phrase_hits:
        weaknesses.append("repeated_phrasing")
    if score >= 0.7:
        strengths.append("sufficient_novelty")
    return clamp_unit(score), strengths, weaknesses


def score_reasoning_quality(
    *,
    text: str,
    rationale: str,
) -> tuple[float, list[str], list[str]]:
    strengths: list[str] = []
    weaknesses: list[str] = []
    if not rationale.strip():
        weaknesses.append("missing_rationale")
        return 0.1, strengths, weaknesses

    score = 0.4
    lower = rationale.lower()
    if "select" in lower or "chose" in lower or "chosen" in lower:
        score += 0.15
        strengths.append("selection_explained")
    else:
        weaknesses.append("selection_not_explained")
    if "evidence" in lower or "source" in lower:
        score += 0.15
        strengths.append("evidence_referenced")
    else:
        weaknesses.append("evidence_not_referenced")
    if "security" in lower:
        score += 0.1
    if len(rationale.strip()) >= 80:
        score += 0.1
    else:
        weaknesses.append("rationale_too_short")
    if any(leak in lower for leak in _PIPELINE_LEAK):
        score *= 0.4
        weaknesses.append("pipeline_details_exposed")
    if any(re.search(pat, text, re.I) for pat in _CERTAINTY):
        score *= 0.85
        weaknesses.append("unsupported_certainty")
    return clamp_unit(score), strengths, weaknesses


def score_rationale_quality(*, rationale: str, text: str) -> tuple[float, list[str], list[str]]:
    strengths: list[str] = []
    weaknesses: list[str] = []
    if not rationale.strip():
        return 0.0, strengths, ["empty_rationale"]
    score = 0.5
    if len(rationale) >= 60:
        score += 0.2
    if "security" in rationale.lower():
        score += 0.15
        strengths.append("domain_aligned_rationale")
    if any(re.search(pat, rationale, re.I) for pat in _HYPE_PATTERNS):
        score *= 0.7
        weaknesses.append("hype_in_rationale")
    # Soft coherence: rationale tokens overlap post a bit but not copy-paste.
    r_tokens = tokenize(rationale)
    t_tokens = tokenize(text)
    if r_tokens and t_tokens:
        overlap = len(r_tokens & t_tokens) / max(1, len(r_tokens))
        if 0.05 <= overlap <= 0.85:
            score += 0.1
            strengths.append("coherent_rationale")
        elif overlap > 0.9:
            weaknesses.append("rationale_copies_post")
            score *= 0.7
    return clamp_unit(score), strengths, weaknesses


def score_content_quality_adjustments(*, text: str) -> tuple[float, list[str], list[str]]:
    """Return a multiplier-like adjustment signals for content hygiene."""
    strengths: list[str] = []
    weaknesses: list[str] = []
    if not text.strip():
        weaknesses.append("empty_post")
        return 0.0, strengths, weaknesses
    length = len(text.strip())
    score = 1.0
    if length < 40:
        score *= 0.5
        weaknesses.append("too_short")
    elif length > 1200:
        score *= 0.85
        weaknesses.append("too_long")
    else:
        strengths.append("reasonable_length")
    if any(re.search(pat, text, re.I) for pat in _HYPE_PATTERNS):
        score *= 0.75
        weaknesses.append("excessive_hype")
    return clamp_unit(score), strengths, weaknesses


def reuse_consistency_score(consistency_meta: dict[str, Any] | None) -> float:
    if not consistency_meta:
        return 0.5
    if consistency_meta.get("decision") == "approve":
        raw = consistency_meta.get("overall_score")
        if raw is None:
            return 0.85
        return clamp_unit(float(raw))
    if consistency_meta.get("decision") == "reject":
        return 0.2
    raw = consistency_meta.get("overall_score")
    if raw is None:
        return 0.5
    return clamp_unit(float(raw))


def reuse_source_diversity_score(intelligence_meta: dict[str, Any] | None) -> float:
    if not intelligence_meta:
        return 0.5
    raw = intelligence_meta.get("source_diversity_score")
    if raw is None:
        nested = intelligence_meta.get("source_diversity")
        if isinstance(nested, dict):
            raw = nested.get("source_diversity")
    if raw is None:
        return 0.5
    return clamp_unit(float(raw))


def extract_post_urls(post: object) -> list[str]:
    sources = getattr(post, "sources", None)
    if isinstance(sources, list):
        return [str(u) for u in sources if str(u).strip()]
    raw = getattr(post, "sources_json", None)
    if isinstance(raw, str) and raw.strip():
        try:
            payload = json.loads(raw)
            if isinstance(payload, list):
                return [str(u) for u in payload if str(u).strip()]
        except Exception:
            return []
    return []
