import threading
import time

from fastapi.testclient import TestClient

from armor.backend import StubBackend
from armor.config import Settings
from armor.server import create_app


def completed(client, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/v1/scans/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.01)
    raise AssertionError("Background scan did not finish")


def test_stub_guard_and_ready(settings, guard_body):
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json()["backend"] == "stub"
        assert client.get("/readyz").status_code == 200
        assert client.get("/v1/models").json()["data"][0]["id"] == "kai"
        response = client.post("/v1/guard", json=guard_body)
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "deny"
        assert body["backend"] == "stub"
        assert body["experimental"] is True
        assert body["evidence_available"] is False


def test_auth_covers_guard_and_scan_routes(guard_body, scan_body):
    settings = Settings(_env_file=None, backend="stub", api_key="example-secret")
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        for url in ("/readyz", "/v1/models", "/v1/scans/nonexistent"):
            assert client.get(url).status_code == 401
        assert client.post("/v1/guard", json=guard_body).status_code == 401
        assert client.post("/v1/scans", json=scan_body).status_code == 401
        assert (
            client.get("/readyz", headers={"Authorization": "Bearer example-secret"}).status_code
            == 200
        )


def test_async_scan_submit_status_and_result(settings, scan_body):
    with TestClient(create_app(settings)) as client:
        submitted = client.post("/v1/scans", json=scan_body)
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]
        job = completed(client, job_id)
        assert job["status"] == "completed"
        result = client.get(f"/v1/scans/{job_id}/result").json()
        assert result["suspicious"] is True
        assert result["chunks_scanned"] == 1
        assert result["backend"] == "stub"
        assert result["evidence_available"] is False
        assert client.get("/v1/scans/unknown").status_code == 404


def test_failed_scan_does_not_report_clear(settings, scan_body):
    class BrokenBackend(StubBackend):
        def decide(self, request):
            raise ValueError("Input cannot fit in the context")

    with TestClient(create_app(settings, BrokenBackend())) as client:
        job_id = client.post("/v1/scans", json=scan_body).json()["id"]
        job = completed(client, job_id)
        assert job["status"] == "failed"
        assert job["result"] is None
        assert client.get(f"/v1/scans/{job_id}/result").status_code == 503


def test_bad_policy_is_rejected(settings, guard_body):
    guard_body["policy"] = "untiered policy prose"
    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/guard", json=guard_body).status_code == 422


def test_limits_and_unknown_model(settings, scan_body):
    with TestClient(create_app(settings)) as client:
        scan_body["text"] = "a" * 1_000_001
        assert client.post("/v1/scans", json=scan_body).status_code == 422
        response = client.post(
            "/v1/systemone",
            json={
                "model": "unknown",
                "state": "x",
                "questions": {"is_xpia_attempt": {"type": "noul"}},
            },
        )
        assert response.status_code == 404


def test_scan_submission_is_nonblocking_and_queue_backpressure(settings, scan_body):
    started, release = threading.Event(), threading.Event()

    class BlockingBackend(StubBackend):
        def decide(self, request):
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("Test failed to release scan worker")
            return super().decide(request)

    configured = settings.model_copy(update={"scan_queue_size": 1})
    with TestClient(create_app(configured, BlockingBackend())) as client:
        try:
            first = client.post("/v1/scans", json=scan_body)
            assert first.status_code == 202
            assert started.wait(timeout=1)
            job_id = first.json()["id"]
            assert client.get(f"/v1/scans/{job_id}").json()["status"] == "running"
            assert client.get(f"/v1/scans/{job_id}/result").status_code == 409
            assert client.post("/v1/scans", json=scan_body).status_code == 202
            assert client.post("/v1/scans", json=scan_body).status_code == 429
        finally:
            release.set()
        assert completed(client, job_id)["status"] == "completed"
