"""End-to-end case workflow: intake, trace, alerts, analytics."""

from __future__ import annotations

import asyncio
import io

import pytest

pytestmark = pytest.mark.anyio


async def _await_job(client, job_id: str) -> dict:
    for _ in range(200):
        await asyncio.sleep(0.05)
        job = (await client.get(f"/api/v1/trace/{job_id}")).json()
        if job["status"] in ("completed", "failed"):
            return job
    raise AssertionError(f"trace {job_id} did not finish in time")

# Seeds from the synthetic dataset, each with a known ground-truth VASP.
SCAM_SEED = "TGMkKG6YRdJ6Cgnt8czbtrMXAQWQcV7Whq"  # tron -> binance
PHISHING_SEED = "0x03504327f36829b3b0a5aac67ec0b09349a734f4"  # ethereum
SEXTORTION_SEED = "14pp5rKiJDTfPZn77C6sfF5o83nxpgDHRR"  # bitcoin -> wazirx


@pytest.fixture
async def traced_case(client) -> dict:
    """A case with one completed trace, for the views that read trace output."""
    created = await client.post(
        "/api/v1/cases",
        json={
            "title": "Investment scam - traced",
            "fraud_category": "investment_scam",
            "loss_amount_inr": 620000,
            "addresses": [{"address": SCAM_SEED}],
        },
    )
    case = created.json()
    trace = await client.post(
        "/api/v1/trace",
        json={
            "address": SCAM_SEED,
            "chain": "tron",
            "case_id": case["id"],
            "max_hops": 6,
        },
    )
    job = await _await_job(client, trace.json()["job_id"])
    assert job["status"] == "completed", job.get("error")
    return {"case": case, "job": job}


async def _first_seed_address(client) -> tuple[str, str]:
    """Pull a real seeded victim address out of the demo cases."""
    response = await client.get("/api/v1/cases", params={"limit": 50})
    response.raise_for_status()
    for case in response.json()["items"]:
        detail = await client.get(f"/api/v1/cases/{case['id']}")
        addresses = detail.json()["addresses"]
        if addresses:
            return addresses[0]["chain"], addresses[0]["address"]
    raise AssertionError("No seeded case addresses found")


class TestCaseIntake:
    async def test_creating_a_case_auto_detects_the_chain(self, client):
        response = await client.post(
            "/api/v1/cases",
            json={
                "title": "Investment scam via fake trading app",
                "fraud_category": "investment_scam",
                "loss_amount_inr": 850000,
                "victim_name": "Test Complainant",
                "state": "Maharashtra",
                "addresses": [
                    {"address": SCAM_SEED},
                    {"address": PHISHING_SEED},
                    {"address": SEXTORTION_SEED},
                ],
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["case_number"].startswith("CFA-")
        chains = {entry["chain"] for entry in body["addresses"]}
        assert chains == {"tron", "ethereum", "bitcoin"}

    async def test_duplicate_addresses_are_collapsed(self, client):
        response = await client.post(
            "/api/v1/cases",
            json={
                "title": "Duplicate address handling",
                "addresses": [
                    {"address": SCAM_SEED},
                    {"address": SCAM_SEED},
                    {"address": f"  {SCAM_SEED}  "},
                ],
            },
        )
        assert response.status_code == 201
        assert response.json()["address_count"] == 1

    async def test_case_numbers_do_not_collide(self, client):
        numbers = set()
        for index in range(3):
            response = await client.post(
                "/api/v1/cases", json={"title": f"Sequential case {index}"}
            )
            numbers.add(response.json()["case_number"])
        assert len(numbers) == 3


class TestBulkIntake:
    CSV = (
        "FIR No,Complainant,Wallet Address,Network,Fraud Amount,Crime Type,State\n"
        f"FIR-2026-001,Asha R,{SCAM_SEED},TRON,\"4,50,000\",Investment fraud,Kerala\n"
        f"FIR-2026-001,Asha R,{PHISHING_SEED},,150000,Investment fraud,Kerala\n"
        f"FIR-2026-002,Bilal K,{SEXTORTION_SEED},Bitcoin,90000,Sextortion,Delhi\n"
        "FIR-2026-003,Chandra M,not-a-wallet,,10000,Phishing,Goa\n"
    )

    async def _upload(self, client, csv_text: str, **params):
        return await client.post(
            "/api/v1/cases/import",
            files={"file": ("ncrp_export.csv", io.BytesIO(csv_text.encode()), "text/csv")},
            params=params,
        )

    async def test_dry_run_reports_per_row_verdicts_without_writing(self, client):
        before = (await client.get("/api/v1/cases")).json()["total"]
        response = await self._upload(client, self.CSV, dry_run=True)
        assert response.status_code == 200

        body = response.json()
        assert body["dry_run"] is True
        assert body["accepted"] == 3
        assert body["rejected"] == 1
        assert body["cases_created"] == 0

        bad = [row for row in body["rows"] if not row["ok"]]
        assert len(bad) == 1
        assert bad[0]["error"]

        after = (await client.get("/api/v1/cases")).json()["total"]
        assert after == before, "dry run must not create cases"

    async def test_import_groups_rows_by_case_number(self, client):
        response = await self._upload(client, self.CSV)
        assert response.status_code == 200
        body = response.json()

        # Three good rows across two FIR numbers.
        assert body["cases_created"] == 2
        assert body["addresses_added"] == 3

        detail = await client.get(f"/api/v1/cases/{body['case_ids'][0]}")
        case = detail.json()
        assert case["case_number"] == "FIR-2026-001"
        assert case["address_count"] == 2
        assert case["intake_source"] == "ncrp_csv"
        # Amount written as "4,50,000" in the Indian style must still parse.
        assert case["loss_amount_inr"] == 450000

    async def test_crime_type_maps_to_a_fraud_category(self, client):
        response = await self._upload(
            client,
            "Case No,Wallet,Crime Type\n"
            f"FIR-CAT-01,{SCAM_SEED},Sextortion and blackmail\n",
        )
        case_id = response.json()["case_ids"][0]
        detail = await client.get(f"/api/v1/cases/{case_id}")
        assert detail.json()["fraud_category"] == "sextortion"

    async def test_a_csv_with_no_address_column_is_rejected(self, client):
        response = await self._upload(client, "name,amount\nfoo,100\n")
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] == 0
        assert body["rejected"] == 1


class TestCaseTraceIntegration:
    async def test_trace_result_surfaces_on_the_case(self, client):
        created = await client.post(
            "/api/v1/cases",
            json={
                "title": "Trace wiring check",
                "addresses": [{"address": SCAM_SEED}],
            },
        )
        case = created.json()
        address = case["addresses"][0]

        trace = await client.post(
            "/api/v1/trace",
            json={
                "address": address["address"],
                "chain": address["chain"],
                "case_id": case["id"],
                "max_hops": 6,
            },
        )
        assert trace.status_code == 202
        job_id = trace.json()["job_id"]
        assert (await _await_job(client, job_id))["status"] == "completed"

        refreshed = (await client.get(f"/api/v1/cases/{case['id']}")).json()
        entry = refreshed["addresses"][0]
        assert entry["last_job_id"] == job_id
        assert entry["last_job_status"] == "completed"
        assert entry["attributed_vasp"], "the case view should name the exchange"

        graph = await client.get(f"/api/v1/cases/{case['id']}/graph")
        assert graph.status_code == 200
        assert len(graph.json()["nodes"]) >= 2

    async def test_alerts_are_raised_and_can_be_worked(self, client, traced_case):
        alerts = await client.get("/api/v1/alerts", params={"limit": 5})
        assert alerts.status_code == 200
        items = alerts.json()["items"]
        assert items, "tracing should have produced alerts"

        # Severity ordering is what makes the inbox a queue.
        severities = [item["severity"] for item in items]
        rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        assert severities == sorted(severities, key=lambda s: rank[s])

        target = items[0]
        assert target["recommended_action"]

        updated = await client.patch(
            f"/api/v1/alerts/{target['id']}",
            json={"status": "acknowledged", "note": "Assigned to cyber cell"},
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "acknowledged"
        assert updated.json()["acknowledged_by_name"]

        counts = await client.get("/api/v1/alerts/counts")
        assert counts.json()["acknowledged"] >= 1


class TestWalletProfile:
    async def test_profile_returns_risk_labels_and_counterparties(self, client):
        chain, address = await _first_seed_address(client)
        response = await client.get(f"/api/v1/wallets/{chain}/{address}")
        assert response.status_code == 200

        body = response.json()
        assert body["address"] == address
        assert body["risk_level"] in {"low", "medium", "high", "critical"}
        assert body["risk_reasons"]
        assert body["recent_transfers"]
        assert body["counterparties"]
        assert body["native_asset"]


class TestVaspDirectory:
    async def test_directory_lists_indian_entities_first(self, client):
        response = await client.get("/api/v1/vasps", params={"limit": 100})
        assert response.status_code == 200
        items = response.json()["items"]
        assert items

        indian = [index for index, row in enumerate(items) if row["is_indian_entity"]]
        overseas = [index for index, row in enumerate(items) if not row["is_indian_entity"]]
        if indian and overseas:
            assert max(indian) < min(overseas)

    async def test_a_vasp_has_the_contact_details_needed_to_serve_a_request(self, client):
        response = await client.get("/api/v1/vasps", params={"indian_only": True})
        row = response.json()["items"][0]
        detail = await client.get(f"/api/v1/vasps/{row['slug']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["compliance_email"]
        assert body["response_sla_hours"] > 0


class TestAnalytics:
    async def test_overview_counts_cases_and_attributions(self, client, traced_case):
        response = await client.get("/api/v1/analytics/overview")
        assert response.status_code == 200
        body = response.json()
        assert body["cases_total"] >= 1
        assert body["traces_run"] >= 1
        assert body["cases_by_status"]
        assert 0.0 <= body["attribution_rate"] <= 1.0

    async def test_typology_distribution_is_populated(self, client, traced_case):
        response = await client.get("/api/v1/analytics/typologies")
        assert response.status_code == 200
        body = response.json()
        assert "distribution" in body
        assert body["total_hits"] >= 0
        assert all(bucket["label"] for bucket in body["distribution"])

    async def test_vasp_analytics_ranks_receiving_exchanges(self, client, traced_case):
        response = await client.get("/api/v1/analytics/vasps")
        assert response.status_code == 200
        top = response.json()["top_receiving"]
        assert top
        assert top == sorted(top, key=lambda b: b["count"], reverse=True)

    async def test_performance_reports_trace_timings(self, client, traced_case):
        response = await client.get("/api/v1/analytics/performance")
        assert response.status_code == 200
        body = response.json()
        assert body["traces_total"] >= 1
        assert body["median_trace_ms"] > 0
        assert body["by_chain"]
