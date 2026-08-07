"""Deterministic validation for GeneratedPost objects."""

from __future__ import annotations

import re

from app.logging import get_logger
from app.services.final_post.models import GeneratedPost, is_valid_http_url
from app.services.research.base import normalize_url
from app.services.scoring import PERSONA_CONCEPTS

logger = get_logger(__name__)

_HYPE_PATTERNS = (
    r"!!!+",
    r"\bABSOLUTELY\b",
    r"\bTHE WORST\b.+\bEVER\b",
    r"\bGAME CHANGER!*\b",
    r"\bmind[- ]blowing\b",
    r"AI is changing everything",
    r"The future is here",
    r"Stay safe in the AI era",
)
_UNSUPPORTED_CERTAINTY = (
    r"\bdefinitely proves\b",
    r"\bEveryone knows\b",
    r"\b100%\s*guaranteed\b",
    r"\bunquestionably\b",
    r"\bno doubt\b",
)
_WRONG_DOMAIN = (
    "fashion",
    "cooking",
    "recipe",
    "smartphone",
    "celebrity",
    "entertainment",
    "sports betting",
    "travel deals",
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) > 2}


class FinalPostValidator:
    """Explainable deterministic final-post validator."""

    def validate(
        self,
        post: GeneratedPost,
        *,
        grounded_urls: set[str],
        previous_titles: list[str] | None = None,
        url_by_normalized: dict[str, str] | None = None,
    ) -> list[str]:
        errors: list[str] = []
        text = post.text.strip()
        rationale = post.rationale.strip()

        if not text:
            errors.append("empty_text")
        if not rationale:
            errors.append("empty_rationale")

        lower = f"{text} {rationale}".lower()
        domain_hits = sum(1 for kw in PERSONA_CONCEPTS if kw in lower)
        if any(term in lower for term in _WRONG_DOMAIN) and domain_hits == 0:
            errors.append("wrong_domain")
        if domain_hits == 0 and "security" not in lower and "ai" not in lower:
            errors.append("wrong_domain")

        if "as a marketing" in lower or "as an influencer" in lower:
            errors.append("persona_drift")

        grounded_norm = {
            normalize_url(url) or url for url in grounded_urls if url
        }
        mapping = url_by_normalized or {}
        cleaned_urls: list[str] = []
        for raw in post.source_urls:
            if not is_valid_http_url(raw):
                errors.append("invalid_url")
                logger.info("final_generation_grounding_rejected", url=raw, reason="invalid_url")
                continue
            key = normalize_url(raw) or raw
            if key not in grounded_norm:
                errors.append("unknown_source_url")
                logger.info(
                    "final_generation_grounding_rejected",
                    url=raw,
                    reason="unknown_source",
                )
                continue
            cleaned_urls.append(mapping.get(key, raw))

        if not cleaned_urls:
            errors.append("no_grounded_source")

        if any(re.search(pat, text, re.I) for pat in _HYPE_PATTERNS):
            errors.append("excessive_hype")
        if any(re.search(pat, text, re.I) for pat in _UNSUPPORTED_CERTAINTY):
            errors.append("unsupported_certainty")

        # Rationale quality: must cover selection / why-now / evidence signals.
        rationale_lower = rationale.lower()
        required_markers = ("select", "evidence", "security")
        if not all(marker in rationale_lower for marker in required_markers):
            # Soft check: require at least evidence + security relevance language.
            if "evidence" not in rationale_lower or "security" not in rationale_lower:
                errors.append("rationale_incomplete")

        if previous_titles:
            text_tokens = tokenize(text)
            for title in previous_titles[:10]:
                title_tokens = tokenize(title)
                if not title_tokens:
                    continue
                overlap = len(text_tokens & title_tokens) / len(title_tokens)
                if overlap >= 0.85 and len(title_tokens) >= 4:
                    errors.append("excessive_repetition")
                    break

        # Deduplicate while preserving order.
        return list(dict.fromkeys(errors))

    def ground_urls(
        self,
        urls: list[str],
        grounded_urls: set[str],
        url_by_normalized: dict[str, str],
    ) -> list[str]:
        allowed = {normalize_url(url) or url for url in grounded_urls}
        result: list[str] = []
        for raw in urls:
            key = normalize_url(raw) or raw
            if key in allowed:
                result.append(url_by_normalized.get(key, raw))
            else:
                logger.info(
                    "final_generation_grounding_rejected",
                    url=raw,
                    reason="unknown_source",
                )
        return list(dict.fromkeys(result))
