"""Deterministic source diversity analysis (Module 12)."""

from __future__ import annotations

import time
from collections import Counter
from urllib.parse import urlsplit

from app.logging import get_logger
from app.services.llm.base import CandidateTopic
from app.services.research.base import normalize_url
from app.services.source_diversity.models import SourceDiversityResult

logger = get_logger(__name__)


def extract_domain(url: str) -> str | None:
    """Normalize URL to registrable-ish host (e.g. arxiv.org)."""
    key = normalize_url(url) or (url or "").strip()
    if not key:
        return None
    try:
        host = urlsplit(key).netloc.lower()
    except Exception:
        return None
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


class SourceDiversityAnalyzer:
    """Analyze candidate source diversity against recent agent posts."""

    def __init__(self, *, lookback: int = 10) -> None:
        self._lookback = max(1, lookback)

    def recent_domains_from_posts(self, posts: list[object]) -> list[str]:
        """Extract domains from newest-first posts (capped by lookback)."""
        domains: list[str] = []
        for post in posts[: self._lookback]:
            raw = getattr(post, "sources_json", None)
            urls: list[str] = []
            if isinstance(raw, str) and raw.strip():
                import json

                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        urls = [str(u) for u in parsed]
                except Exception:
                    urls = []
            elif isinstance(getattr(post, "sources", None), list):
                urls = [str(u) for u in post.sources]
            for url in urls:
                domain = extract_domain(url)
                if domain:
                    domains.append(domain)
        return domains

    def analyze_candidate(
        self,
        candidate: CandidateTopic,
        *,
        recent_domains: list[str],
        agent_id: str | None = None,
        cycle_id: str | None = None,
    ) -> SourceDiversityResult:
        started = time.perf_counter()
        cand_domains = []
        for url in candidate.source_urls:
            domain = extract_domain(url)
            if domain:
                cand_domains.append(domain)
        cand_domains = list(dict.fromkeys(cand_domains))
        recent_counter = Counter(recent_domains)
        overlap = [d for d in cand_domains if d in recent_counter]
        repetition = 0.0
        if cand_domains:
            # Weight by how often each candidate domain appeared recently.
            weighted = sum(min(recent_counter[d], self._lookback) for d in cand_domains)
            repetition = min(1.0, weighted / (len(cand_domains) * max(1, self._lookback)))
        overlap_ratio = (len(overlap) / len(cand_domains)) if cand_domains else 0.0
        # Diversity: high when few repeats and multiple domains.
        diversity = max(0.0, min(1.0, 1.0 - (0.7 * repetition + 0.3 * overlap_ratio)))
        if len(cand_domains) >= 2:
            diversity = min(1.0, diversity + 0.1)

        result = SourceDiversityResult(
            source_diversity=diversity,
            source_repetition=repetition,
            source_domain_count=len(cand_domains),
            recent_source_overlap=overlap_ratio,
            domains=cand_domains,
            repeated_domains=overlap,
        )
        logger.info(
            "source_diversity_analyzed",
            agent_id=agent_id,
            cycle_id=cycle_id,
            stage="source_diversity",
            duration_ms=int((time.perf_counter() - started) * 1000),
            source_diversity=result.source_diversity,
            source_repetition=result.source_repetition,
            domain_count=result.source_domain_count,
        )
        if overlap:
            logger.info(
                "source_repetition_detected",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="source_diversity",
                repeated_domains=overlap,
            )
        return result
