"""Celery application, used when JOB_BACKEND=celery (the docker-compose setup)."""

from __future__ import annotations

from celery import Celery

from app.config import settings

celery_app = Celery(
    "chainwatch",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # A trace is bounded by hop and node limits, but a slow upstream can still
    # drag; kill it well before it blocks a worker slot indefinitely.
    task_soft_time_limit=600,
    task_time_limit=900,
    worker_max_tasks_per_child=200,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
