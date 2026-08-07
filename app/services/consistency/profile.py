"""Centralized, versioned persona profile provider for Kestrel."""

from __future__ import annotations

from typing import Any

from app.services.consistency.models import PersonaProfile, PersonaStyle
from app.services.scoring import PERSONA_CONCEPTS

PERSONA_PROFILE_VERSION = 1

_DEFAULT_NAME = "Kestrel"
_DEFAULT_DOMAIN = "AI Security"
_DEFAULT_MISSION = "Identify and explain meaningful developments in AI Security."
_DEFAULT_TONE = ["precise", "analytical", "calm", "evidence-driven"]


class InvalidPersonaError(ValueError):
    """Raised when agent persona fields are missing or invalid."""


def get_persona_profile(
    agent: Any | None = None,
    *,
    name: str | None = None,
    domain: str | None = None,
) -> PersonaProfile:
    """Return a deterministic, versioned persona profile.

    Identity is taken from the agent / explicit args. The LLM must never redefine it.
    """
    if name is not None:
        resolved_name = name.strip()
    else:
        resolved_name = str(getattr(agent, "name", None) or _DEFAULT_NAME).strip()
    if domain is not None:
        resolved_domain = domain.strip()
    else:
        resolved_domain = str(getattr(agent, "domain", None) or _DEFAULT_DOMAIN).strip()
    if not resolved_name:
        raise InvalidPersonaError("Persona name is required")
    if not resolved_domain:
        raise InvalidPersonaError("Persona domain is required")

    # Keep Kestrel AI-Security oriented even if init supplies alternate casing.
    normalized_domain = resolved_domain
    if "security" not in resolved_domain.lower() and "ai" not in resolved_domain.lower():
        # Still honor explicit init domain, but mark keywords from AI Security baseline.
        pass

    return PersonaProfile(
        version=PERSONA_PROFILE_VERSION,
        name=resolved_name,
        domain=normalized_domain,
        mission=_DEFAULT_MISSION
        if resolved_name.lower() == _DEFAULT_NAME.lower()
        else f"Identify and explain meaningful developments in {normalized_domain}.",
        tone=list(_DEFAULT_TONE),
        style=PersonaStyle(),
        domain_keywords=list(PERSONA_CONCEPTS),
    )
