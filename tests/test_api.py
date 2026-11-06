"""FastAPI 骨架测试（M0 CI）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bodydonor.app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_status_reports_deployment_mode(client: TestClient) -> None:
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["deployment_mode"] in ("cloud_demo", "on_prem")
    assert body["queue_backend"] == "inprocess"
    assert body["storage_backend"] == "local_fs"


def test_license_endpoint(client: TestClient) -> None:
    r = client.get("/license")
    assert r.status_code == 200
    body = r.json()
    assert body["reference_doc"] == "LICENSES-THIRD-PARTY.md"
    assert "total" in body["allowed_tasks"]
    assert "brain_aneurysm" in body["blocked_tasks"]
    facts = body["license_facts"]
    assert facts["nninteractive.weights"]["license"] == "CC BY-NC-SA 4.0"


def test_limits_endpoint(client: TestClient) -> None:
    r = client.get("/limits")
    assert r.status_code == 200
    body = r.json()
    assert body["extract_limits"]["max_compression_ratio"] == 100


def test_queue_enqueue_roundtrip(client: TestClient) -> None:
    r = client.post("/queue/enqueue", params={"case_id": "t-case", "stage": "ingest"})
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
    r2 = client.get("/queue/size")
    assert r2.json()["size"] >= 1


def test_agent_context_preview_no_phi(client: TestClient) -> None:
    r = client.post(
        "/agent-context/preview", params={"case_id": "c-1", "series_id": "s-1"}
    )
    assert r.status_code == 200
    assert r.json()["phi_violations"] == []
    prompt = r.json()["prompt"]
    assert "patient_name" not in prompt
