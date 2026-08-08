"""Deterministic AI-security research filter (Module 20).

Never invents findings, CVEs, URLs, or severity. Classifies real research
items using lexical evidence only; rejects generic cybersecurity noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.logging import get_logger
from app.services.ai_security.domains import (
    AI_ANCHORS,
    AI_SECURITY_DOMAINS,
    DOMAIN_SIGNALS,
    GENERIC_CYBER_REJECT,
    AiSecurityDomain,
    is_valid_domain,
)
from app.services.research.models import ResearchItem

logger = get_logger(__name__)

AI_SECURITY_NO_FINDING_REASON = "No relevant AI-security finding discovered."


@dataclass(frozen=True)
class ClassifiedItem:
    item: ResearchItem
    domain: AiSecurityDomain
    relevance: float
    matched_signals: tuple[str, ...] = ()


@dataclass
class FilterResult:
    accepted: list[ResearchItem] = field(default_factory=list)
    rejected_count: int = 0
    accepted_by_domain: dict[str, int] = field(default_factory=dict)
    reason: str | None = None

    def to_meta(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.accepted else "empty",
            "accepted_count": len(self.accepted),
            "rejected_count": self.rejected_count,
            "accepted_by_domain": dict(self.accepted_by_domain),
            "reason": self.reason,
            "domains": list(AI_SECURITY_DOMAINS),
        }


class AISecurityFilter:
    """Keep only research items that are genuinely AI/ML/LLM security related."""

    def filter(self, items: list[ResearchItem]) -> FilterResult:
        accepted: list[ResearchItem] = []
        rejected = 0
        by_domain: dict[str, int] = {d: 0 for d in AI_SECURITY_DOMAINS}

        for item in items:
            classified = self.classify(item)
            if classified is None:
                rejected += 1
                logger.info(
                    "ai_security_item_rejected",
                    title=item.title[:120],
                    source=item.source_name,
                    url=item.url,
                )
                continue
            annotated = item.model_copy(
                update={
                    "ai_security_domain": classified.domain,
                    "ai_security_relevance": classified.relevance,
                    "tags": list(
                        dict.fromkeys(
                            list(item.tags)
                            + [f"ai_security:{classified.domain}"]
                            + list(classified.matched_signals[:3])
                        )
                    ),
                }
            )
            accepted.append(annotated)
            by_domain[classified.domain] = by_domain.get(classified.domain, 0) + 1
            logger.info(
                "ai_security_item_accepted",
                title=item.title[:120],
                domain=classified.domain,
                relevance=classified.relevance,
                source=item.source_name,
            )

        reason = None if accepted else AI_SECURITY_NO_FINDING_REASON
        logger.info(
            "ai_security_filter_completed",
            input_count=len(items),
            accepted_count=len(accepted),
            rejected_count=rejected,
            reason=reason,
        )
        return FilterResult(
            accepted=accepted,
            rejected_count=rejected,
            accepted_by_domain={k: v for k, v in by_domain.items() if v},
            reason=reason,
        )

    def classify(self, item: ResearchItem) -> ClassifiedItem | None:
        blob = _blob(item)
        domain, relevance, signals = _best_domain(blob)

        # Domain-specific AI-security phrases (prompt injection, jailbreak, …)
        # are sufficient evidence — they are not generic cyber topics.
        if domain is not None:
            if _looks_like_generic_cyber(blob):
                # Do not promote ransomware/phishing/etc. just because the text
                # mentions "LLM vulnerability" in passing/negation.
                strong_domains = {
                    "prompt_injection",
                    "jailbreak",
                    "data_leakage",
                    "model_poisoning",
                    "ai_supply_chain",
                }
                if domain not in strong_domains:
                    return None
            return ClassifiedItem(
                item=item,
                domain=domain,
                relevance=relevance,
                matched_signals=signals,
            )

        # Soft path requires an AI/ML/LLM anchor + security language.
        if not _has_ai_anchor(blob):
            return None
        if _looks_like_generic_cyber(blob):
            return None
        if _has_ai_security_soft(blob):
            return ClassifiedItem(
                item=item,
                domain="ai_vulnerability",
                relevance=0.55,
                matched_signals=("ai security soft match",),
            )
        return None


def assign_domain_from_research(
    source_urls: list[str],
    research_items: list[ResearchItem],
) -> str | None:
    """Pick domain from grounded research sources (never invent)."""
    by_url = {(item.url or "").strip(): item for item in research_items}
    votes: dict[str, float] = {}
    for url in source_urls:
        item = by_url.get(url.strip())
        if item is None:
            continue
        domain = getattr(item, "ai_security_domain", None)
        if not is_valid_domain(domain):
            classified = AISecurityFilter().classify(item)
            domain = classified.domain if classified else None
        if not is_valid_domain(domain):
            continue
        rel = float(getattr(item, "ai_security_relevance", None) or 0.5)
        votes[str(domain)] = votes.get(str(domain), 0.0) + rel
    if not votes:
        return None
    return max(votes.items(), key=lambda kv: (kv[1], kv[0]))[0]


def _blob(item: ResearchItem) -> str:
    parts = [
        item.title or "",
        item.summary or "",
        " ".join(item.tags or []),
        item.source_name or "",
    ]
    return " ".join(parts).lower()


def _has_ai_anchor(blob: str) -> bool:
    padded = f" {blob} "
    return any(anchor in padded or anchor in blob for anchor in AI_ANCHORS)


def _best_domain(
    blob: str,
) -> tuple[AiSecurityDomain | None, float, tuple[str, ...]]:
    best_domain: AiSecurityDomain | None = None
    best_score = 0.0
    best_signals: tuple[str, ...] = ()
    for domain, signals in DOMAIN_SIGNALS.items():
        hits = [s for s in signals if s in blob]
        if not hits:
            continue
        # More/longer matches → higher relevance (deterministic, capped).
        score = min(1.0, 0.55 + 0.12 * len(hits) + 0.02 * sum(len(h) for h in hits) / 40.0)
        if score > best_score or (
            score == best_score
            and best_domain is not None
            and domain < best_domain
        ):
            best_score = score
            best_domain = domain
            best_signals = tuple(hits[:5])
    return best_domain, best_score, best_signals


def _looks_like_generic_cyber(blob: str) -> bool:
    return any(term in blob for term in GENERIC_CYBER_REJECT)


def _has_ai_security_soft(blob: str) -> bool:
    security_bits = (
        "security",
        "vulnerability",
        "exploit",
        "attack",
        "threat",
        "advisory",
        "cve",
        "risk",
        "bypass",
        "adversarial",
    )
    ai_security_phrases = (
        "ai security",
        "llm security",
        "model security",
        "agent security",
        "ml security",
        "ai risk",
        "ai threat",
        "secure llm",
        "secure ai",
    )
    if any(p in blob for p in ai_security_phrases):
        return True
    return any(s in blob for s in security_bits) and (
        "llm" in blob or "language model" in blob or "ai agent" in blob
    )
