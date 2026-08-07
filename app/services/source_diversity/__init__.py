"""Source diversity engine (Module 12)."""

from app.services.source_diversity.analyzer import SourceDiversityAnalyzer, extract_domain
from app.services.source_diversity.models import SourceDiversityResult

__all__ = ["SourceDiversityAnalyzer", "SourceDiversityResult", "extract_domain"]
