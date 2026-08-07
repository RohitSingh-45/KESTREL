"""Bounded event collector with priority-aware eviction."""

from __future__ import annotations

from app.services.observability.models import ObservabilityEvent


class EventCollector:
    """In-memory, cycle-scoped event buffer. Never raises on capacity."""

    def __init__(self, *, max_events: int = 100) -> None:
        self.max_events = max(1, int(max_events))
        self._events: list[ObservabilityEvent] = []
        self._dropped = 0
        self._next_order = 1

    @property
    def events(self) -> list[ObservabilityEvent]:
        return list(self._events)

    @property
    def dropped(self) -> int:
        return self._dropped

    def add(self, event: ObservabilityEvent) -> ObservabilityEvent:
        if event.order <= 0:
            event.order = self._next_order
            self._next_order += 1
        else:
            self._next_order = max(self._next_order, event.order + 1)

        if len(self._events) < self.max_events:
            self._events.append(event)
            return event

        # Prefer keeping higher-priority / later terminal events.
        lowest_idx = min(
            range(len(self._events)),
            key=lambda i: (self._events[i].priority, self._events[i].order),
        )
        if event.priority >= self._events[lowest_idx].priority:
            self._events.pop(lowest_idx)
            self._events.append(event)
            self._dropped += 1
        else:
            self._dropped += 1
        # Keep chronological order by `order` for stable traces.
        self._events.sort(key=lambda e: e.order)
        return event

    def clear(self) -> None:
        self._events.clear()
        self._dropped = 0
        self._next_order = 1
