"""Deterministic persona consistency and anti-drift analysis."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from app.logging import get_logger
from app.services.consistency.models import (
    ConsistencyDecisionEnum,
    ConsistencyResult,
    ConsistencySignals,
    HistoricalStyleProfile,
    PersonaProfile,
    clamp_unit,
)
from app.services.debate import DebateResult
from app.services.llm.base import CandidateTopic
from app.services.memory.models import MemoryRecord
from app.services.research.base import normalize_url
from app.services.research.models import ResearchItem
from app.services.scoring import PERSONA_CONCEPTS, ScoredCandidate

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_HYPE_PATTERNS = (
    r"\babsolutely\b",
    r"\bworst\b.+\bever\b",
    r"!!!+",
    r"\bguaranteed\b",
    r"\bunquestionably\b",
    r"\bcompletely safe\b",
    r"\bshocking\b",
    r"\bmind[- ]blowing\b",
    r"\bhuge breakthrough\b",
    r"THIS IS",
)
_ABSOLUTE_CERTAINTY = (
    r"\bunquestionably\b",
    r"\bdefinitely proven\b",
    r"\bno doubt\b",
    r"\b100%\b",
    r"\balways safe\b",
    r"\bnever risky\b",
)
_IRRELEVANT_DOMAINS = (
    "smartphone",
    "fashion",
    "cooking",
    "recipe",
    "celebrity",
    "sports betting",
    "crypto trading tips",
    "travel deals",
    "notebook tips",
    "productivity hacks",
)
_IDENTITY_REPLACEMENT = (
    r"\bi am (?!kestrel)\w+",
    r"\bas a marketing\b",
    r"\bas an influencer\b",
    r"\bthis is not kestrel\b",
)

_SIGNAL_WEIGHTS = {
    "identity_fit": 0.12,
    "domain_fit": 0.22,
    "tone_fit": 0.16,
    "stance_fit": 0.14,
    "technical_depth_fit": 0.12,
    "evidence_discipline": 0.16,
    "historical_style_fit": 0.08,
}


@dataclass(frozen=True)
class ConsistencyContext:
    profile: PersonaProfile
    research_items: list[ResearchItem]
    memories: list[MemoryRecord]
    historical_style: HistoricalStyleProfile
    previous_titles: list[str]
    approve_threshold: float = 0.80
    revise_threshold: float = 0.60


def tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) > 2}


def candidate_text(candidate: CandidateTopic, debate: DebateResult | None = None) -> str:
    parts = [
        candidate.title,
        candidate.angle,
        candidate.why_now,
        candidate.relevance_to_persona,
    ]
    if debate is not None:
        parts.extend(
            [
                debate.for_argument or "",
                debate.against_argument or "",
                " ".join(debate.evidence_points or []),
            ]
        )
    return " ".join(parts)


def build_historical_style(
    topics: list[Any],
    *,
    limit: int = 10,
) -> HistoricalStyleProfile:
    """Build style baseline from prior topic/debate metadata only (no fake posts)."""
    samples = list(topics)[: max(0, limit)]
    if not samples:
        return HistoricalStyleProfile()

    title_lengths: list[float] = []
    text_lengths: list[float] = []
    tech_densities: list[float] = []
    hype_rates: list[float] = []
    domain_densities: list[float] = []
    source_rates: list[float] = []

    for topic in samples:
        title = getattr(topic, "title", "") or ""
        title_lengths.append(float(len(title)))
        debate_json = getattr(topic, "debate_json", None)
        scores_json = getattr(topic, "scores_json", None)
        text = title
        source_count = 0
        if isinstance(debate_json, str) and debate_json:
            try:
                import json

                payload = json.loads(debate_json)
                text = " ".join(
                    [
                        title,
                        str(payload.get("for_argument") or ""),
                        str(payload.get("against_argument") or ""),
                    ]
                )
                source_count = len(payload.get("source_urls") or [])
            except Exception:
                pass
        elif isinstance(scores_json, str) and scores_json:
            try:
                import json

                payload = json.loads(scores_json)
                source_count = len(payload.get("source_urls") or [])
            except Exception:
                pass
        text_lengths.append(float(len(text)))
        tokens = tokenize(text)
        tech = len(tokens & set(_TOKEN_RE.findall(" ".join(PERSONA_CONCEPTS))))
        tech_densities.append(tech / max(1, len(tokens)))
        hype_hits = sum(1 for pat in _HYPE_PATTERNS if re.search(pat, text, re.I))
        hype_rates.append(min(1.0, hype_hits / 3.0))
        domain_hits = sum(1 for kw in PERSONA_CONCEPTS if kw in text.lower())
        domain_densities.append(min(1.0, domain_hits / 4.0))
        source_rates.append(1.0 if source_count > 0 else 0.0)

    n = len(samples)
    return HistoricalStyleProfile(
        sample_count=n,
        avg_title_length=sum(title_lengths) / n,
        avg_text_length=sum(text_lengths) / n,
        technical_keyword_density=sum(tech_densities) / n,
        hype_phrase_rate=sum(hype_rates) / n,
        domain_keyword_density=sum(domain_densities) / n,
        source_usage_rate=sum(source_rates) / n,
    )


class ConsistencyAnalyzer:
    """Explainable deterministic consistency analyzer (no LLM required)."""

    def analyze(
        self,
        scored: ScoredCandidate,
        *,
        debate: DebateResult | None,
        context: ConsistencyContext,
        agent_id: str | None = None,
        cycle_id: str | None = None,
        revision_count: int = 0,
        revised: bool = False,
    ) -> ConsistencyResult:
        started = time.perf_counter()
        topic_id = scored.topic_id
        logger.info(
            "consistency_analysis_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            topic_id=topic_id,
            stage="consistency",
        )
        try:
            candidate = scored.candidate
            # Candidate-only text for domain/tone hard rules (debate text is AI-security framed).
            candidate_only = candidate_text(candidate, None)
            text = candidate_text(candidate, debate)
            hard_reasons = self._hard_reject_reasons(candidate, debate, context)
            signals = self._compute_signals(
                candidate, debate, text, candidate_only, context
            )
            overall = self._overall_score(signals)
            drift_flags = self._detect_drift(candidate_only, signals, context)
            belief_change_accepted, stance_note = self._stance_evaluation(
                text, context.memories, debate
            )
            reasons: list[str] = list(hard_reasons)
            if stance_note:
                reasons.append(stance_note)

            if hard_reasons:
                decision = ConsistencyDecisionEnum.REJECT
                for reason in hard_reasons:
                    logger.info(
                        "consistency_rejected",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        topic_id=topic_id,
                        stage="consistency",
                        reason=reason,
                        overall_score=overall,
                    )
            elif overall >= context.approve_threshold:
                decision = ConsistencyDecisionEnum.APPROVE
                logger.info(
                    "consistency_approved",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=topic_id,
                    stage="consistency",
                    decision="approve",
                    overall_score=overall,
                )
            elif overall >= context.revise_threshold:
                decision = ConsistencyDecisionEnum.REVISE
                reasons.append("overall_score_in_revise_band")
                logger.info(
                    "consistency_revision_requested",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=topic_id,
                    stage="consistency",
                    decision="revise",
                    overall_score=overall,
                )
            else:
                decision = ConsistencyDecisionEnum.REJECT
                reasons.append("overall_score_below_revise_threshold")
                logger.info(
                    "consistency_rejected",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=topic_id,
                    stage="consistency",
                    reason="overall_score_below_revise_threshold",
                    overall_score=overall,
                )

            for flag in drift_flags:
                logger.info(
                    "persona_drift_detected",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=topic_id,
                    stage="consistency",
                    signal=flag,
                )
            if belief_change_accepted:
                logger.info(
                    "belief_change_accepted",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=topic_id,
                    stage="consistency",
                )

            result = ConsistencyResult(
                topic_id=topic_id,
                decision=decision,
                overall_score=overall,
                signals=signals,
                reasons=list(dict.fromkeys(reasons)),
                hard_reject=bool(hard_reasons),
                drift_flags=drift_flags,
                belief_change_accepted=belief_change_accepted,
                revised=revised,
                revision_count=revision_count,
                profile_version=context.profile.version,
            )
            logger.info(
                "consistency_analysis_success",
                agent_id=agent_id,
                cycle_id=cycle_id,
                topic_id=topic_id,
                stage="consistency",
                decision=result.decision.value,
                overall_score=result.overall_score,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return result
        except Exception as exc:
            logger.exception(
                "consistency_analysis_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                topic_id=topic_id,
                stage="consistency",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return ConsistencyResult(
                topic_id=topic_id,
                decision=ConsistencyDecisionEnum.REJECT,
                overall_score=0.0,
                signals=ConsistencySignals(),
                reasons=["consistency_analysis_failed"],
                hard_reject=True,
                error=str(exc),
                profile_version=context.profile.version,
                revision_count=revision_count,
                revised=revised,
            )

    def _hard_reject_reasons(
        self,
        candidate: CandidateTopic,
        debate: DebateResult | None,
        context: ConsistencyContext,
    ) -> list[str]:
        reasons: list[str] = []
        allowed = {
            (normalize_url(item.url) or item.url) for item in context.research_items
        }
        # Grounding requires candidate sources; debate URLs alone are insufficient.
        candidate_urls = [u for u in candidate.source_urls if u]
        grounded_candidate = [
            url
            for url in candidate_urls
            if (normalize_url(url) or url) in allowed
        ]
        if not candidate_urls or not grounded_candidate:
            reasons.append("no_grounded_source")

        invented = [
            url
            for url in candidate_urls
            if (normalize_url(url) or url) not in allowed
        ]
        if invented and not grounded_candidate:
            reasons.append("fabricated_source")

        text = candidate_text(candidate, None).lower()
        domain = context.profile.domain.lower()
        domain_tokens = tokenize(domain)
        if not any(tok in text for tok in domain_tokens) and not any(
            kw in text for kw in PERSONA_CONCEPTS
        ):
            reasons.append("wrong_persona_domain")

        if any(term in text for term in _IRRELEVANT_DOMAINS) and not any(
            kw in text for kw in ("security", "prompt", "jailbreak", "llm", "agent")
        ):
            reasons.append("wrong_persona_domain")

        if any(re.search(pat, text, re.I) for pat in _IDENTITY_REPLACEMENT):
            reasons.append("persona_identity_replacement")

        # Unsupported absolute factual claim without grounded candidate sources.
        if any(re.search(pat, text, re.I) for pat in _ABSOLUTE_CERTAINTY) and not grounded_candidate:
            reasons.append("unsupported_claim")

        return reasons

    def _compute_signals(
        self,
        candidate: CandidateTopic,
        debate: DebateResult | None,
        text: str,
        candidate_only: str,
        context: ConsistencyContext,
    ) -> ConsistencySignals:
        lower = candidate_only.lower()
        tokens = tokenize(candidate_only)

        # Identity: remains Kestrel / AI-security persona framing.
        identity = 1.0
        if any(re.search(pat, lower, re.I) for pat in _IDENTITY_REPLACEMENT):
            identity = 0.0
        elif "marketing" in lower or "influencer" in lower:
            identity = 0.3

        # Domain fit (candidate-primary).
        domain_hits = sum(1 for kw in PERSONA_CONCEPTS if kw in lower)
        domain_fit = clamp_unit(0.25 + 0.2 * min(domain_hits, 4))
        if any(term in lower for term in _IRRELEVANT_DOMAINS) and domain_hits == 0:
            domain_fit = 0.05

        # Tone fit: penalize hype / sensationalism on candidate expression.
        hype_hits = sum(1 for pat in _HYPE_PATTERNS if re.search(pat, candidate_only, re.I))
        tone_fit = clamp_unit(1.0 - 0.25 * hype_hits)
        if "!!!" in candidate_only or candidate_only.isupper():
            tone_fit = min(tone_fit, 0.2)

        # Evidence discipline.
        allowed = {
            (normalize_url(item.url) or item.url) for item in context.research_items
        }
        if not candidate.source_urls:
            evidence = 0.0
        else:
            cand_grounded = [
                (normalize_url(u) or u) in allowed for u in candidate.source_urls if u
            ]
            evidence = (
                sum(1 for g in cand_grounded if g) / len(cand_grounded)
                if cand_grounded
                else 0.0
            )
        if any(re.search(pat, candidate_only, re.I) for pat in _ABSOLUTE_CERTAINTY):
            evidence = min(evidence, 0.35)

        # Technical depth: avoid empty generic or unsupported jargon dump.
        tech_hits = sum(1 for kw in ("vulnerability", "exploit", "injection", "jailbreak", "model", "agent", "advisory", "cve", "defense") if kw in lower)
        depth = clamp_unit(0.35 + 0.15 * min(tech_hits, 4))
        if len(tokens) < 8 and domain_hits == 0:
            depth = 0.2
        if tech_hits >= 6 and evidence < 0.5:
            depth = min(depth, 0.45)

        # Stance fit vs memories (allow reasoned evolution).
        stance_fit, _ = self._stance_score(text, context.memories, debate)

        # Historical style fit (neutral when no history).
        hist = context.historical_style
        if hist.sample_count == 0:
            historical = 0.75
        else:
            title_delta = abs(len(candidate.title) - hist.avg_title_length) / max(
                1.0, hist.avg_title_length
            )
            length_fit = clamp_unit(1.0 - min(1.0, title_delta))
            hype_fit = clamp_unit(1.0 - abs((hype_hits / 3.0) - hist.hype_phrase_rate))
            domain_fit_hist = clamp_unit(
                1.0 - abs(min(1.0, domain_hits / 4.0) - hist.domain_keyword_density)
            )
            historical = clamp_unit(0.4 * length_fit + 0.3 * hype_fit + 0.3 * domain_fit_hist)

        return ConsistencySignals(
            identity_fit=identity,
            domain_fit=domain_fit,
            tone_fit=tone_fit,
            stance_fit=stance_fit,
            technical_depth_fit=depth,
            evidence_discipline=evidence,
            historical_style_fit=historical,
        )

    def _overall_score(self, signals: ConsistencySignals) -> float:
        data = signals.model_dump()
        total = sum(_SIGNAL_WEIGHTS[k] * float(data[k]) for k in _SIGNAL_WEIGHTS)
        return clamp_unit(total)

    def _detect_drift(
        self,
        text: str,
        signals: ConsistencySignals,
        context: ConsistencyContext,
    ) -> list[str]:
        flags: list[str] = []
        if signals.tone_fit < 0.55:
            flags.append("tone_drift")
        if signals.domain_fit < 0.45:
            flags.append("domain_drift")
        if signals.evidence_discipline < 0.5 and any(
            re.search(pat, text, re.I) for pat in _ABSOLUTE_CERTAINTY
        ):
            flags.append("unsupported_certainty")
        if any(term in text.lower() for term in _IRRELEVANT_DOMAINS) and signals.domain_fit < 0.5:
            flags.append("irrelevant_topic_drift")
        if signals.identity_fit < 0.5:
            flags.append("identity_drift")
        return flags

    def _stance_score(
        self,
        text: str,
        memories: list[MemoryRecord],
        debate: DebateResult | None,
    ) -> tuple[float, bool]:
        belief_memories = [
            m
            for m in memories
            if m.kind.value in {"belief", "stance", "theme"}
        ]
        if not belief_memories:
            return 0.8, False

        lower = text.lower()
        overlap = 0
        contradictions = 0
        evidenced_change = False
        for memory in belief_memories:
            key = memory.key.lower()
            if key and key in lower:
                overlap += 1
                # Absolute flip without hedging / evidence language.
                if memory.value.stance and "mixed" in (memory.value.stance or "").lower():
                    if any(re.search(pat, text, re.I) for pat in _ABSOLUTE_CERTAINTY):
                        if debate and debate.source_urls:
                            evidenced_change = True
                        else:
                            contradictions += 1
                if memory.value.contradicting_source_urls and debate and debate.source_urls:
                    # Memory already records contradiction path → reasoned evolution.
                    evidenced_change = True

        if evidenced_change:
            return 0.85, True
        if contradictions and not evidenced_change:
            return 0.35, False
        if overlap:
            return 0.9, False
        return 0.75, False

    def _stance_evaluation(
        self,
        text: str,
        memories: list[MemoryRecord],
        debate: DebateResult | None,
    ) -> tuple[bool, str | None]:
        score, evidenced = self._stance_score(text, memories, debate)
        if evidenced:
            return True, "legitimate_belief_change"
        if score < 0.4:
            return False, "unsupported_belief_contradiction"
        return False, None
