"""Bounded contradiction detection against stored beliefs (Module 12)."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from app.logging import get_logger
from app.services.contradictions.models import (
    ContradictionAnalysisResult,
    ContradictionSignal,
)
from app.services.llm.base import LLMClient, UnavailableLLMClient
from app.services.memory.models import MemoryRecord
from app.services.research.base import normalize_url
from app.services.research.models import ResearchItem
from app.services.scoring import tokenize

logger = get_logger(__name__)

_POSITIVE = {
    "reduces",
    "reduce",
    "mitigates",
    "mitigate",
    "mitigation",
    "prevents",
    "prevent",
    "protects",
    "protect",
    "defends",
    "defend",
    "defense",
    "defences",
    "effective",
    "effectively",
    "blocks",
    "block",
    "stops",
    "secure",
    "hardened",
}
_NEGATIVE = {
    "bypass",
    "bypassed",
    "bypassing",
    "fails",
    "failed",
    "ineffective",
    "broken",
    "vulnerable",
    "exploited",
    "defeated",
    "insufficient",
    "circumvent",
    "circumvented",
    "weakness",
}


class ContradictionLLMItem(BaseModel):
    memory_key: str
    contradiction_type: str = "partial"
    explanation: str = ""
    confidence: float = 0.0
    evidence_urls: list[str] = Field(default_factory=list)


class ContradictionLLMResponse(BaseModel):
    contradictions: list[ContradictionLLMItem] = Field(default_factory=list)


class ContradictionDetector:
    """Detect contradictions; at most one optional LLM call per cycle."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        *,
        enabled: bool = True,
        min_confidence: float = 0.70,
        max_items: int = 10,
        use_llm: bool = True,
    ) -> None:
        self._llm = llm or UnavailableLLMClient()
        self._enabled = enabled
        self._min_confidence = min_confidence
        self._max_items = max(1, max_items)
        self._use_llm = use_llm
        self.llm_calls = 0

    async def analyze(
        self,
        *,
        memories: list[MemoryRecord],
        research_items: list[ResearchItem],
        agent_id: str | None = None,
        cycle_id: str | None = None,
    ) -> ContradictionAnalysisResult:
        started = time.perf_counter()
        logger.info(
            "contradiction_analysis_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            stage="contradiction",
            memory_count=len(memories),
            research_count=len(research_items),
        )
        if not self._enabled:
            return ContradictionAnalysisResult(status="skipped", reason="disabled")

        try:
            allowed = {
                (normalize_url(item.url) or item.url): item.url for item in research_items
            }
            beliefs = [
                m
                for m in memories
                if m.kind.value in {"belief", "stance", "theme"} and m.value.text
            ][: self._max_items]

            signals = self._deterministic(beliefs, research_items, allowed)
            llm_used = False

            # Optional single bounded LLM call when deterministic finds nothing strong.
            if (
                self._use_llm
                and getattr(self._llm, "available", False)
                and not any(s.confidence >= self._min_confidence for s in signals)
                and beliefs
                and research_items
            ):
                llm_signals = await self._llm_analyze(beliefs, research_items, allowed)
                llm_used = True
                signals.extend(llm_signals)

            grounded = []
            for signal in signals:
                urls = [
                    allowed[normalize_url(u) or u]
                    for u in signal.evidence_urls
                    if (normalize_url(u) or u) in allowed
                ]
                if not urls:
                    logger.info(
                        "contradiction_rejected",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        stage="contradiction",
                        reason="ungrounded_or_unknown_url",
                        memory_key=signal.memory_key,
                    )
                    continue
                signal.evidence_urls = list(dict.fromkeys(urls))
                if signal.confidence < self._min_confidence and signal.contradiction_type != "uncertain":
                    # Keep uncertain low-confidence for metadata only when partial evidence exists.
                    if signal.confidence < 0.4:
                        logger.info(
                            "contradiction_rejected",
                            agent_id=agent_id,
                            cycle_id=cycle_id,
                            reason="low_confidence",
                            memory_key=signal.memory_key,
                        )
                        continue
                grounded.append(signal)
                logger.info(
                    "contradiction_detected",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="contradiction",
                    memory_key=signal.memory_key,
                    contradiction_type=signal.contradiction_type,
                    confidence=signal.confidence,
                )

            return ContradictionAnalysisResult(
                signals=grounded[: self._max_items],
                llm_used=llm_used,
                status="success",
            )
        except Exception as exc:
            logger.exception(
                "contradiction_analysis_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="contradiction",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return ContradictionAnalysisResult(status="failed", reason=str(exc))

    def _deterministic(
        self,
        beliefs: list[MemoryRecord],
        research_items: list[ResearchItem],
        allowed: dict[str, str],
    ) -> list[ContradictionSignal]:
        signals: list[ContradictionSignal] = []
        for memory in beliefs:
            belief_tokens = tokenize(memory.value.text)
            key_tokens = tokenize(memory.key)
            for item in research_items:
                text = f"{item.title} {item.summary or ''}".lower()
                research_tokens = tokenize(text)
                if not (key_tokens & research_tokens) and not (
                    {t for t in belief_tokens if len(t) >= 5} & research_tokens
                ):
                    continue
                pos_in_belief = bool(belief_tokens & _POSITIVE)
                neg_in_research = bool(research_tokens & _NEGATIVE)
                neg_in_belief = bool(belief_tokens & _NEGATIVE)
                pos_in_research = bool(research_tokens & _POSITIVE)
                ctype: str | None = None
                confidence = 0.0
                if pos_in_belief and neg_in_research:
                    ctype = "direct" if len(key_tokens & research_tokens) >= 2 else "partial"
                    confidence = 0.86 if ctype == "direct" else 0.74
                elif neg_in_belief and pos_in_research:
                    ctype = "contextual"
                    confidence = 0.72
                elif ("bypass" in research_tokens or "bypassed" in research_tokens) and (
                    "defense" in belief_tokens
                    or "mitigation" in belief_tokens
                    or "reduces" in belief_tokens
                ):
                    ctype = "partial"
                    confidence = 0.78
                if ctype is None:
                    continue
                url_key = normalize_url(item.url) or item.url
                if url_key not in allowed:
                    continue
                signals.append(
                    ContradictionSignal(
                        memory_id=memory.id,
                        memory_key=memory.key,
                        evidence_urls=[allowed[url_key]],
                        contradiction_type=ctype,  # type: ignore[arg-type]
                        explanation=(
                            f"Research evidence may conflict with stored belief "
                            f"'{memory.key}': {item.title}"
                        )[:400],
                        confidence=confidence,
                    )
                )
        # Deduplicate by memory_id keeping highest confidence.
        best: dict[str, ContradictionSignal] = {}
        for signal in signals:
            prev = best.get(signal.memory_id)
            if prev is None or signal.confidence > prev.confidence:
                best[signal.memory_id] = signal
        return list(best.values())

    async def _llm_analyze(
        self,
        beliefs: list[MemoryRecord],
        research_items: list[ResearchItem],
        allowed: dict[str, str],
    ) -> list[ContradictionSignal]:
        belief_payload = [
            {
                "id": m.id,
                "key": m.key,
                "text": m.value.text[:240],
                "confidence": m.confidence,
            }
            for m in beliefs[: self._max_items]
        ]
        research_payload = [
            {
                "title": item.title,
                "summary": (item.summary or "")[:240],
                "url": item.url,
            }
            for item in research_items[:12]
        ]
        prompt = (
            "Identify contradictions between stored beliefs and research. "
            "Use only provided URLs. Return JSON.\n"
            f"Beliefs: {belief_payload}\n"
            f"Research: {research_payload}\n"
            "Schema: {\"contradictions\":[{\"memory_key\":\"\",\"contradiction_type\":"
            "\"direct|partial|contextual|uncertain\",\"explanation\":\"\","
            "\"confidence\":0.0,\"evidence_urls\":[\"...\"]}]}"
        )
        self.llm_calls += 1
        raw = await self._llm.structured_output(
            system_prompt=(
                "You detect contradictions for an AI Security persona. "
                "Never invent URLs. Prefer uncertain when evidence is weak."
            ),
            user_prompt=prompt,
            schema=ContradictionLLMResponse,
            temperature=0.0,
        )
        if not isinstance(raw, ContradictionLLMResponse):
            raw = ContradictionLLMResponse.model_validate(raw)
        by_key = {m.key: m for m in beliefs}
        signals: list[ContradictionSignal] = []
        for item in raw.contradictions:
            memory = by_key.get(item.memory_key)
            if memory is None:
                continue
            urls = [
                allowed[normalize_url(u) or u]
                for u in item.evidence_urls
                if (normalize_url(u) or u) in allowed
            ]
            if not urls:
                continue
            ctype = item.contradiction_type if item.contradiction_type in {
                "direct",
                "partial",
                "contextual",
                "uncertain",
            } else "uncertain"
            signals.append(
                ContradictionSignal(
                    memory_id=memory.id,
                    memory_key=memory.key,
                    evidence_urls=urls,
                    contradiction_type=ctype,  # type: ignore[arg-type]
                    explanation=(item.explanation or "")[:400],
                    confidence=max(0.0, min(1.0, float(item.confidence))),
                )
            )
        return signals


def challenge_payload(signals: list[ContradictionSignal]) -> list[dict[str, Any]]:
    """Shape contradiction signals for MemoryUpdateService forwarding."""
    return [
        {
            "memory_id": s.memory_id,
            "memory_key": s.memory_key,
            "relation": "contradicting",
            "source_urls": s.evidence_urls,
            "confidence": s.confidence,
            "text": s.explanation,
            "contradiction_type": s.contradiction_type,
        }
        for s in signals
    ]
