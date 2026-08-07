"""Unit tests for Module 12 story continuity."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.orchestration.locks import utc_now
from app.services.llm.base import CandidateTopic
from app.services.story_tracking import StoryCluster, StoryTracker


def _cand(title: str, why: str = "Fresh disclosure", url: str = "https://arxiv.org/abs/1") -> CandidateTopic:
    return CandidateTopic.model_validate(
        {
            "title": title,
            "angle": "Agent security",
            "why_now": why,
            "relevance_to_persona": "AI Security",
            "source_urls": [url],
            "confidence": 0.9,
        }
    )


def test_new_story_when_unrelated() -> None:
    tracker = StoryTracker(match_threshold=0.70)
    result = tracker.match_candidate(
        _cand("Cloud billing optimization tips"),
        clusters=[
            StoryCluster(
                id="s1",
                title="Prompt injection in AI agents",
                canonical_topic="Prompt injection in AI agents",
                latest_summary="Researchers found prompt injection weakness.",
            )
        ],
        previous_titles=["Prompt injection in AI agents"],
    )
    assert result.classification == "new_story"


def test_continuing_story_match() -> None:
    tracker = StoryTracker(match_threshold=0.55)
    cluster = StoryCluster(
        id="s1",
        title="Prompt injection technique against AI agents",
        canonical_topic="Prompt injection technique against AI agents",
        latest_summary="Researchers reported prompt injection weakness in agents.",
        source_urls=["https://arxiv.org/abs/1"],
        update_count=1,
    )
    result = tracker.match_candidate(
        _cand(
            "Prompt injection technique mitigation bypassed for AI agents",
            why="Vendor mitigation bypassed under new attack",
        ),
        clusters=[cluster],
        previous_titles=[cluster.canonical_topic],
        research_summaries={
            "https://arxiv.org/abs/1": "A proposed mitigation was bypassed in evaluation."
        },
    )
    assert result.classification in {"continuing_story", "uncertain"}
    assert result.match_score >= 0.55
    if result.classification == "continuing_story":
        assert result.what_changed
        assert result.previous_context


def test_threshold_behavior() -> None:
    tracker_high = StoryTracker(match_threshold=0.95)
    tracker_low = StoryTracker(match_threshold=0.40)
    cluster = StoryCluster(
        id="s1",
        title="Jailbreak defenses for enterprise LLMs",
        canonical_topic="Jailbreak defenses for enterprise LLMs",
        latest_summary="Enterprise jailbreak guidance published.",
    )
    cand = _cand("Enterprise jailbreak defense guidance update")
    high = tracker_high.match_candidate(
        cand, clusters=[cluster], previous_titles=[cluster.title]
    )
    low = tracker_low.match_candidate(
        cand, clusters=[cluster], previous_titles=[cluster.title]
    )
    assert low.classification in {"continuing_story", "uncertain"}
    # Stricter threshold is less likely to classify as continuing.
    assert high.match_score == low.match_score


def test_story_tracker_failure_returns_uncertain() -> None:
    tracker = StoryTracker(match_threshold=0.70)

    class BadCluster:
        canonical_topic = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    result = tracker.match_candidate(
        _cand("Anything"),
        clusters=[BadCluster()],  # type: ignore[list-item]
        previous_titles=[],
    )
    assert result.classification == "uncertain"
    assert result.match_score == 0.0


def test_clusters_from_topics_meta() -> None:
    tracker = StoryTracker()
    now = utc_now()
    topics = [
        SimpleNamespace(
            id="t1",
            title="Prompt injection weakness",
            created_at=now,
            scores_json=json.dumps(
                {
                    "meta": {
                        "story": {
                            "classification": "new_story",
                            "source_urls": ["https://arxiv.org/abs/1"],
                            "previous_context": "Initial disclosure",
                            "story": {
                                "id": "story-abc",
                                "canonical_topic": "Prompt injection weakness",
                                "update_count": 1,
                            },
                        }
                    }
                }
            ),
        )
    ]
    clusters = tracker.clusters_from_topics(topics)
    assert clusters
    assert clusters[0].id == "story-abc"
