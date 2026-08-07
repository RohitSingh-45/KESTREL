"""DecisionService: deterministic orchestration gates (0 LLM calls)."""

from __future__ import annotations

import time
from typing import Any, Callable

from app.logging import get_logger
from app.services.decision.evaluator import build_decision_context
from app.services.decision.gates import (
    adaptive_learning_gate,
    candidate_gate,
    consistency_gate,
    debate_gate,
    generation_gate,
    intelligence_gate,
    memory_gate,
    publishing_gate,
    research_gate,
    selection_gate,
)
from app.services.decision.models import (
    DecisionContext,
    DecisionResult,
    DecisionTrace,
)
from app.services.decision.policy import DecisionPolicy
from app.services.decision.trace import empty_trace, serialize_trace
from app.services.reliability.secrets import sanitize_error

logger = get_logger(__name__)

GateFn = Callable[[DecisionContext, DecisionPolicy], DecisionResult]


class DecisionService:
    """Orchestrate pipeline checkpoints. Never publishes or mutates module results."""

    def __init__(self, policy: DecisionPolicy | None = None) -> None:
        self.policy = policy or DecisionPolicy()
        self.llm_calls = 0
        self._trace: DecisionTrace | None = None

    def begin(self, *, agent_id: str, cycle_id: str | None = None) -> DecisionTrace:
        self._trace = empty_trace(agent_id=agent_id, cycle_id=cycle_id)
        return self._trace

    @property
    def trace(self) -> DecisionTrace:
        if self._trace is None:
            self._trace = empty_trace(agent_id="unknown")
        return self._trace

    def evaluate_gate(
        self,
        gate: str,
        ctx: DecisionContext,
    ) -> DecisionResult:
        started = time.perf_counter()
        logger.info(
            "decision_gate_started",
            agent_id=ctx.agent_id,
            cycle_id=ctx.cycle_id,
            gate=gate,
            stage="decision",
        )
        if not self.policy.enabled:
            result = DecisionResult(
                action="proceed",
                gate=gate,  # type: ignore[arg-type]
                reason="Decision orchestration disabled; deferring to module authorities.",
                confidence=0.5,
            )
            self._record(result)
            return result

        fn = self._gate_fn(gate)
        try:
            result = fn(ctx, self.policy)
        except Exception as exc:
            logger.exception(
                "decision_gate_failed",
                agent_id=ctx.agent_id,
                cycle_id=ctx.cycle_id,
                gate=gate,
                stage="decision",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            # Prefer skip over publish when decision itself fails.
            safe_action = "stop" if gate == "publishing" else "skip"
            result = DecisionResult(
                action=safe_action,  # type: ignore[arg-type]
                gate=gate,  # type: ignore[arg-type]
                reason=f"Decision gate failed safely: {sanitize_error(exc)}",
                confidence=0.0,
                blocking=(gate == "publishing"),
                metadata={"status": "unavailable"},
            )

        event = (
            "decision_gate_passed"
            if result.action == "proceed"
            else "decision_gate_blocked"
            if result.action == "stop" or result.blocking
            else "decision_gate_skipped"
        )
        logger.info(
            event,
            agent_id=ctx.agent_id,
            cycle_id=ctx.cycle_id,
            gate=gate,
            action=result.action,
            reason=result.reason,
            duration_ms=int((time.perf_counter() - started) * 1000),
            stage="decision",
        )
        self._record(result)
        return result

    def build_context(self, **kwargs: Any) -> DecisionContext:
        return build_decision_context(**kwargs)

    def finalize(self) -> dict[str, Any]:
        summary = serialize_trace(self.trace)
        logger.info(
            "decision_cycle_completed",
            agent_id=self.trace.agent_id,
            cycle_id=self.trace.cycle_id,
            action=summary.get("final_action"),
            stage="decision",
            item_count=len(self.trace.decisions),
        )
        return summary

    def meta_payload(self) -> dict[str, Any]:
        try:
            return self.finalize()
        except Exception as exc:
            logger.exception(
                "decision_gate_failed",
                stage="decision",
                reason=sanitize_error(exc),
            )
            return {
                "final_action": "skip",
                "gates_passed": 0,
                "gates_skipped": 0,
                "gates_blocked": 0,
                "status": "unavailable",
                "reason": sanitize_error(exc),
                "trace": [],
            }

    def _record(self, result: DecisionResult) -> None:
        self.trace.add(result, max_items=self.policy.max_trace_items)

    @staticmethod
    def _gate_fn(gate: str) -> GateFn:
        mapping: dict[str, GateFn] = {
            "research": research_gate,
            "candidate": candidate_gate,
            "selection": selection_gate,
            "debate": debate_gate,
            "memory": memory_gate,
            "consistency": consistency_gate,
            "intelligence": intelligence_gate,
            "adaptive": adaptive_learning_gate,
            "learning": adaptive_learning_gate,
            "generation": generation_gate,
            "publishing": publishing_gate,
        }
        if gate not in mapping:
            raise ValueError(f"unknown decision gate: {gate}")
        return mapping[gate]
