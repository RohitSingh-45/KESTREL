"""Deterministic topic coverage and angle/source pattern analysis."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Any

from app.services.scoring import tokenize
from app.services.source_diversity.analyzer import extract_domain

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Canonical AI Security concept buckets (lightweight, no embeddings).
_CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "prompt_injection": ("prompt", "injection", "jailbreak"),
    "agent_security": ("agent", "agents", "agentic", "autonomous"),
    "model_poisoning": ("poison", "poisoning", "supply"),
    "adversarial_ml": ("adversarial", "attack", "evasion"),
    "privacy": ("privacy", "membership", "inference"),
    "governance": ("policy", "governance", "regulation", "compliance"),
    "mitigation": ("mitigation", "defense", "defenses", "patch", "control"),
}

_ANGLE_BUCKETS: dict[str, tuple[str, ...]] = {
    "defense": ("defense", "defenses", "mitigation", "protect", "hardening"),
    "attack": ("attack", "exploit", "offensive", "adversary"),
    "vulnerability": ("vulnerability", "cve", "flaw", "weakness"),
    "research_finding": ("research", "study", "paper", "arxiv", "findings"),
    "enterprise_risk": ("enterprise", "business", "operational", "risk"),
    "practical_mitigation": ("practical", "playbook", "checklist", "ops"),
    "policy_governance": ("policy", "governance", "regulation", "compliance"),
}

_STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "about",
    "over",
    "under",
    "ai",
    "llm",
}


def normalize_concept(text: str) -> str:
    """Map free text to a compact concept key."""
    tokens = {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) >= 3}
    best_key = ""
    best_hits = 0
    for key, aliases in _CONCEPT_ALIASES.items():
        hits = len(tokens & set(aliases))
        if hits > best_hits:
            best_hits = hits
            best_key = key
    if best_key:
        return best_key
    # Fallback: top meaningful tokens joined
    ranked = sorted(tokens, key=lambda t: (-len(t), t))[:3]
    return "_".join(ranked) if ranked else "general"


def classify_angle(text: str) -> str:
    tokens = tokenize(text)
    best = "research_finding"
    best_hits = 0
    for bucket, aliases in _ANGLE_BUCKETS.items():
        hits = len(tokens & set(aliases))
        if hits > best_hits:
            best_hits = hits
            best = bucket
    return best


class TopicCoverageAnalyzer:
    """Build bounded coverage maps from recent posts/topics."""

    def __init__(self, *, lookback_posts: int = 20) -> None:
        self._lookback = max(1, lookback_posts)

    def analyze_posts(
        self,
        posts: list[object],
        *,
        topics: list[object] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        recent_posts = list(posts)[: self._lookback]
        concepts: Counter[str] = Counter()
        concept_last: dict[str, datetime | None] = {}
        angles: Counter[str] = Counter()
        domains: Counter[str] = Counter()

        for post in recent_posts:
            title = str(getattr(post, "topic", None) or getattr(post, "text", "")[:120])
            concept = normalize_concept(title)
            concepts[concept] += 1
            created = getattr(post, "created_at", None)
            if isinstance(created, datetime):
                prev = concept_last.get(concept)
                if prev is None or created > prev:
                    concept_last[concept] = created
            angles[classify_angle(title)] += 1
            raw = getattr(post, "sources_json", "[]")
            try:
                urls = json.loads(raw) if isinstance(raw, str) else []
            except Exception:
                urls = []
            for url in urls if isinstance(urls, list) else []:
                domain = extract_domain(str(url))
                if domain:
                    domains[domain] += 1

        # Merge topic titles for richer coverage when available.
        for topic in (topics or [])[: self._lookback]:
            title = str(getattr(topic, "title", "") or "")
            if not title:
                continue
            concept = normalize_concept(title)
            concepts[concept] += 1
            angles[classify_angle(title)] += 1
            # Angle from candidate metadata when present.
            scores_raw = getattr(topic, "scores_json", None)
            if isinstance(scores_raw, str) and scores_raw.strip():
                try:
                    payload = json.loads(scores_raw)
                    angle_text = str(payload.get("angle") or "")
                    if angle_text:
                        angles[classify_angle(angle_text)] += 1
                except Exception:
                    pass

        total = max(1, sum(concepts.values()))
        coverage = {
            key: {
                "count": count,
                "last_seen_at": (
                    concept_last.get(key).isoformat()
                    if isinstance(concept_last.get(key), datetime)
                    else None
                ),
                "recent_frequency": round(count / total, 4),
            }
            for key, count in concepts.most_common()
        }
        angle_total = max(1, sum(angles.values()))
        angle_freq = {
            key: round(count / angle_total, 4) for key, count in angles.most_common()
        }
        domain_total = max(1, sum(domains.values()))
        domain_freq = {
            key: {
                "recent_count": count,
                "share": round(count / domain_total, 4),
                "dependency": (
                    "high" if count / domain_total >= 0.5 else
                    "moderate" if count / domain_total >= 0.3 else
                    "low"
                ),
            }
            for key, count in domains.most_common()
        }
        overused = [k for k, v in coverage.items() if v["recent_frequency"] >= 0.25]
        undercovered = [
            k
            for k in _CONCEPT_ALIASES
            if coverage.get(k, {}).get("count", 0) == 0
        ][:10]
        overused_angles = [k for k, v in angle_freq.items() if v >= 0.35]
        return {
            "coverage": coverage,
            "overused_topics": overused,
            "undercovered_topics": undercovered,
            "angles": angle_freq,
            "overused_angles": overused_angles,
            "domains": domain_freq,
            "post_count": len(recent_posts),
        }
