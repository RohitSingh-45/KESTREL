"""End-to-end: start a trace over the API, stream it, read the graph back."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from app.db.models import SyntheticGroundTruth


async def ground_truth(session, scenario: str) -> SyntheticGroundTruth:
    return (
        await session.execute(
            select(SyntheticGroundTruth).where(
                SyntheticGroundTruth.scenario == scenario
            )
        )
    ).scalar_one()


async def run_trace(client, address, chain=None, **overrides) -> dict:
    """Start a trace and wait for the inline worker to finish it."""
    payload = {"address": address, **overrides}
    if chain:
        payload["chain"] = chain
    response = await client.post("/api/v1/trace", json=payload)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    for _ in range(200):
        await asyncio.sleep(0.05)
        job = (await client.get(f"/api/v1/trace/{job_id}")).json()
        if job["status"] in ("completed", "failed"):
            return job
    raise AssertionError(f"trace {job_id} did not finish in time")


class TestTraceApi:
    async def test_direct_deposit_case_identifies_the_exchange(
        self, client, seeded_session
    ):
        truth = await ground_truth(seeded_session, "direct_tron")
        job = await run_trace(client, truth.seed_address)

        assert job["status"] == "completed"
        finding = job["summary"]["attribution"]
        assert finding["found"] is True
        assert finding["vasp_slug"] == truth.expected_vasp_slug
        assert finding["address"] == truth.expected_deposit_address
        assert finding["confidence"] >= 0.75
        assert truth.expected_vasp_slug in job["summary"]["vasps"][0]["vasp_slug"]

    async def test_multi_hop_case_reports_hops_and_taint(self, client, seeded_session):
        truth = await ground_truth(seeded_session, "task_fraud_tron")
        job = await run_trace(client, truth.seed_address)

        finding = job["summary"]["attribution"]
        assert finding["vasp_slug"] == truth.expected_vasp_slug
        assert finding["hop"] == truth.expected_hops
        assert 0 < finding["taint_pct"] <= 100
        assert finding["elapsed_human"]

    async def test_mixer_case_stops_and_says_why(self, client, seeded_session):
        truth = await ground_truth(seeded_session, "phishing_eth")
        job = await run_trace(client, truth.seed_address)

        summary = job["summary"]
        assert summary["counts"]["mixers"] >= 1
        assert summary["attribution"]["terminal_reason"] == "mixer_entry"
        # The break in the chain of custody must be stated, not glossed over.
        assert any(r["kind"] == "mixer" for r in summary["recommendations"])
        assert any(r["kind"] == "sanctions" for r in summary["recommendations"])

    async def test_recommendations_include_a_freeze_request(
        self, client, seeded_session
    ):
        truth = await ground_truth(seeded_session, "investment_scam_tron")
        job = await run_trace(client, truth.seed_address)

        actions = job["summary"]["recommendations"]
        lea = next(r for r in actions if r["kind"] == "lea_request")
        assert "Section 94 BNSS" in lea["action"]
        assert truth.expected_deposit_address in lea["action"]

    async def test_graph_endpoint_returns_a_connected_graph(
        self, client, seeded_session
    ):
        truth = await ground_truth(seeded_session, "layering_eth")
        job = await run_trace(client, truth.seed_address)

        graph = (await client.get(f"/api/v1/trace/{job['id']}/graph")).json()
        node_ids = {n["id"] for n in graph["nodes"]}
        assert truth.seed_address in node_ids
        assert any(n["is_seed"] for n in graph["nodes"])
        assert any(n["is_terminal"] for n in graph["nodes"])
        # Every edge must connect two returned nodes or the canvas breaks.
        for edge in graph["edges"]:
            assert edge["source"] in node_ids
            assert edge["target"] in node_ids
        assert any(e["is_on_primary_path"] for e in graph["edges"])

    async def test_graph_can_be_filtered_to_the_primary_path(
        self, client, seeded_session
    ):
        truth = await ground_truth(seeded_session, "layering_eth")
        job = await run_trace(client, truth.seed_address)

        full = (await client.get(f"/api/v1/trace/{job['id']}/graph")).json()
        primary = (
            await client.get(
                f"/api/v1/trace/{job['id']}/graph", params={"primary_path_only": True}
            )
        ).json()
        assert 0 < len(primary["edges"]) < len(full["edges"])
        assert all(e["is_on_primary_path"] for e in primary["edges"])

    async def test_chain_is_detected_when_not_supplied(self, client, seeded_session):
        truth = await ground_truth(seeded_session, "sextortion_btc")
        response = await client.post(
            "/api/v1/trace", json={"address": truth.seed_address}
        )
        assert response.status_code == 202
        assert response.json()["chain"] == "bitcoin"

    async def test_garbage_address_is_rejected(self, client):
        response = await client.post("/api/v1/trace", json={"address": "not-an-address"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_chain"

    async def test_evm_chain_is_probed_not_assumed(self, client, seeded_session):
        # rapid_bsc lives on BSC, but its 0x address is indistinguishable from
        # an Ethereum one, so the engine has to find where the activity is.
        truth = await ground_truth(seeded_session, "rapid_bsc")
        response = await client.post(
            "/api/v1/trace", json={"address": truth.seed_address}
        )
        body = response.json()
        assert body["chain"] == "bsc"
        assert body["chain_detected"] is True
        assert body["chain_activity"]["bsc"] > 0
        assert body["chain_activity"]["ethereum"] == 0

    async def test_resolve_reports_ambiguity_for_evm(self, client, seeded_session):
        truth = await ground_truth(seeded_session, "dormant_polygon")
        body = (
            await client.get(
                "/api/v1/attribution/resolve",
                params={"address": truth.seed_address, "probe": True},
            )
        ).json()
        assert body["ambiguous"] is True
        assert set(body["candidates"]) >= {"ethereum", "bsc", "polygon"}
        assert body["chain"] == "polygon"

    async def test_unknown_job_is_404(self, client):
        response = await client.get("/api/v1/trace/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    async def test_trace_requires_authentication(self, client, seeded_session):
        truth = await ground_truth(seeded_session, "direct_tron")
        response = await client.post(
            "/api/v1/trace",
            json={"address": truth.seed_address},
            headers={"Authorization": ""},
        )
        assert response.status_code == 401


class TestTraceStream:
    async def test_stream_replays_progress_and_ends_with_completion(
        self, client, seeded_session
    ):
        truth = await ground_truth(seeded_session, "rapid_bsc")
        job = await run_trace(client, truth.seed_address)

        # The log is replayable after the fact, which is what lets a browser
        # reconnect mid-trace without losing the earlier hops.
        events = []
        async with client.stream(
            "GET", f"/api/v1/trace/{job['id']}/stream"
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        assert events[0]["event"] == "started"
        assert events[-1]["event"] == "completed"
        assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
        assert any(e["event"] == "hop_completed" for e in events)

    async def test_stream_resumes_from_a_sequence_number(self, client, seeded_session):
        truth = await ground_truth(seeded_session, "rapid_bsc")
        job = await run_trace(client, truth.seed_address)

        events = []
        async with client.stream(
            "GET", f"/api/v1/trace/{job['id']}/stream", params={"from": 2}
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        assert events
        assert min(e["seq"] for e in events) == 3


class TestTraceAccuracy:
    @pytest.mark.parametrize(
        "scenario",
        [
            "direct_tron",
            "investment_scam_tron",
            "task_fraud_tron",
            "sextortion_btc",
            "darknet_btc",
            "rapid_bsc",
            "layering_eth",
            "dormant_polygon",
        ],
    )
    async def test_scenario_reaches_its_ground_truth_vasp(
        self, client, seeded_session, scenario
    ):
        truth = await ground_truth(seeded_session, scenario)
        job = await run_trace(
            client, truth.seed_address, max_hops=max(5, truth.expected_hops)
        )
        finding = job["summary"]["attribution"]
        assert finding["vasp_slug"] == truth.expected_vasp_slug, finding["statement"]
        assert finding["address"] == truth.expected_deposit_address
        assert finding["hop"] == truth.expected_hops
