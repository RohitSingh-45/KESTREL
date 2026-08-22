"""Reports, SAHYOG export, NCRP webhook and system status."""

from __future__ import annotations

import hashlib
import io

import pytest

from tests.integration.test_case_workflow import SCAM_SEED, _await_job

pytestmark = pytest.mark.anyio


@pytest.fixture
async def attributed_case(client) -> dict:
    created = await client.post(
        "/api/v1/cases",
        json={
            "title": "Report packet fixture",
            "fraud_category": "investment_scam",
            "loss_amount_inr": 620000,
            "victim_name": "Packet Fixture",
            "police_station": "Cyber PS",
            "state": "Maharashtra",
            "addresses": [{"address": SCAM_SEED}],
        },
    )
    case = created.json()
    trace = await client.post(
        "/api/v1/trace",
        json={"address": SCAM_SEED, "chain": "tron", "case_id": case["id"], "max_hops": 6},
    )
    job = await _await_job(client, trace.json()["job_id"])
    assert job["status"] == "completed", job.get("error")
    return {"case": case, "job": job}


class TestReports:
    async def test_investigation_packet_is_hashed_and_downloadable(self, client, attributed_case):
        case_id = attributed_case["case"]["id"]
        created = await client.post(
            f"/api/v1/reports/{case_id}/generate",
            json={"kind": "investigation_packet"},
        )
        assert created.status_code == 200, created.text
        report = created.json()
        assert report["sha256"]
        assert report["size_bytes"] > 1000
        assert report["kind"] == "investigation_packet"
        assert "summary" in report["sections"]

        listed = await client.get("/api/v1/reports", params={"case_id": case_id})
        assert listed.json()["total"] >= 1

        download = await client.get(f"/api/v1/reports/{report['id']}/download")
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("application/pdf")
        digest = hashlib.sha256(download.content).hexdigest()
        assert digest == report["sha256"]
        assert download.headers.get("x-content-sha256") == report["sha256"]

        refreshed = (await client.get(f"/api/v1/reports/{report['id']}")).json()
        actions = [entry["action"] for entry in refreshed["custody_log"]]
        assert "generated" in actions
        assert "downloaded" in actions

    async def test_sahyog_package_is_json(self, client, attributed_case):
        case_id = attributed_case["case"]["id"]
        created = await client.post(
            f"/api/v1/reports/{case_id}/generate",
            json={"kind": "sahyog_package"},
        )
        assert created.status_code == 200
        download = await client.get(f"/api/v1/reports/{created.json()['id']}/download")
        body = download.json()
        assert body["schema"] == "chainwatch.sahyog.v1"
        assert body["case"]["case_number"]
        assert body["request"]["instrument"]


class TestNcrpIntegration:
    async def test_webhook_rejects_a_bad_token(self, client):
        response = await client.post(
            "/api/v1/integrations/ncrp/webhook",
            json={"title": "x", "addresses": [SCAM_SEED]},
            headers={"X-NCRP-Token": "wrong"},
        )
        assert response.status_code in (401, 403)

    async def test_mock_push_creates_a_case(self, client):
        before = (await client.get("/api/v1/cases")).json()["total"]
        response = await client.post("/api/v1/integrations/ncrp/mock-push", json={})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["created"] is True
        assert body["case_id"]
        after = (await client.get("/api/v1/cases")).json()["total"]
        assert after == before + 1

        detail = await client.get(f"/api/v1/cases/{body['case_id']}")
        assert detail.json()["intake_source"] == "ncrp_mock"

    async def test_system_status_reports_data_mode(self, client):
        response = await client.get("/api/v1/integrations/system-status")
        assert response.status_code == 200
        body = response.json()
        assert body["data_mode"] in {"live", "synthetic", "hybrid"}
        assert body["providers"]
        assert body["synthetic_scenarios"] >= 1

    async def test_admin_can_toggle_data_mode(self, client):
        patched = await client.patch(
            "/api/v1/integrations/settings", json={"data_mode": "synthetic"}
        )
        assert patched.status_code == 200
        assert patched.json()["data_mode"] == "synthetic"
        await client.patch("/api/v1/integrations/settings", json={"data_mode": "hybrid"})

    async def test_audit_log_records_report_generation(self, client, attributed_case):
        await client.post(
            f"/api/v1/reports/{attributed_case['case']['id']}/generate",
            json={"kind": "bnss_94_request"},
        )
        audit = await client.get("/api/v1/integrations/audit", params={"action": "report.generate"})
        assert audit.status_code == 200
        assert audit.json()["total"] >= 1


class TestCsvAlias:
    async def test_ncrp_csv_alias_matches_import(self, client):
        csv = (
            "FIR No,Wallet Address,Fraud Amount\n"
            f"FIR-ALIAS-01,{SCAM_SEED},10000\n"
        )
        response = await client.post(
            "/api/v1/cases/import/ncrp-csv",
            files={"file": ("ncrp.csv", io.BytesIO(csv.encode()), "text/csv")},
            params={"dry_run": True},
        )
        assert response.status_code == 200
        assert response.json()["accepted"] == 1
