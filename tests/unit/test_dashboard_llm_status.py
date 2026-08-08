"""Dashboard LLM badge and pipeline skip mapping (Module 19)."""

from __future__ import annotations

from app.services.dashboard.service import DashboardService


def test_llm_status_unavailable_without_key() -> None:
    assert (
        DashboardService._llm_status(None, {}, "skipped", "llm_unavailable")
        == "unavailable"
    )


def test_llm_status_unstable_after_llm_failure() -> None:
    meta = {"llm": {"status": "unstable", "reason": "timeout"}}
    assert DashboardService._llm_status("sk-test", meta, "failed", "llm_unstable") == "unstable"


def test_llm_status_ready_only_after_probe() -> None:
    assert (
        DashboardService._llm_status("sk-test", {}, "success", None, probed_status=None)
        == "unavailable"
    )
    assert (
        DashboardService._llm_status(
            "sk-test", {}, "success", None, probed_status="ready"
        )
        == "ready"
    )


def test_pipeline_marks_llm_dependent_stages_skipped() -> None:
    meta = {
        "research_count": 5,
        "candidate_count": 0,
        "decision": {"final_action": "skip", "reason": "llm_unavailable"},
        "observability": {
            "cycle_reason": "llm_unavailable",
            "events": [
                {
                    "stage": "research",
                    "status": "ok",
                    "event_type": "research_completed",
                    "reason": "5 research items gathered",
                },
                {
                    "stage": "candidate",
                    "status": "skipped",
                    "event_type": "candidate_skipped",
                    "reason": "LLM unavailable",
                },
            ],
        },
    }
    nodes = DashboardService(session=None)._pipeline_nodes(meta, "skipped")  # type: ignore[arg-type]
    by_id = {n["id"]: n["status"] for n in nodes}
    assert by_id["research"] == "done"
    assert by_id["candidate"] == "skipped"
    assert by_id["debate"] == "skipped"
    assert by_id["generation"] == "skipped"
    assert by_id["decision"] == "blocked"
