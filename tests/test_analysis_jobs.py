import time

from fastapi.testclient import TestClient

from backend.analysis_jobs import AnalysisJobStore
from backend.main import app


def test_analysis_job_store_reports_progress_and_result():
    store = AnalysisJobStore(max_workers=1)

    def runner(progress):
        progress({
            "event": "node_complete",
            "node_id": "n1",
            "node_type": "universe",
            "progress": 0.5,
        })
        return {"success": True, "node_results": {}}

    job = store.create(runner)
    deadline = time.time() + 3
    while time.time() < deadline:
        current = store.get(job.job_id)
        if current and current.status == "completed":
            break
        time.sleep(0.02)

    current = store.get(job.job_id)
    assert current is not None
    assert current.status == "completed"
    assert current.progress == 1.0
    assert current.current_node == "n1"
    assert current.result == {"success": True, "node_results": {}}


def test_analysis_job_api_returns_job_status_and_result(monkeypatch):
    def fake_execute(body, target_code=None, is_single=False, progress_callback=None):
        assert body["max_symbols"] == 30
        progress_callback({
            "event": "node_complete",
            "node_id": "n1",
            "node_type": "universe",
            "progress": 1.0,
        })
        return {"success": True, "node_results": {"n1": {"node_type": "universe", "total_count": 30}}}

    monkeypatch.setattr("backend.routes.api._execute_sync", fake_execute)
    client = TestClient(app)

    created = client.post("/api/analysis/jobs", json={"nodes": [], "edges": [], "max_symbols": 30})
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    deadline = time.time() + 3
    status = {}
    while time.time() < deadline:
        resp = client.get(f"/api/analysis/jobs/{job_id}")
        assert resp.status_code == 200
        status = resp.json()
        if status["status"] == "completed":
            break
        time.sleep(0.02)

    assert status["status"] == "completed"
    assert status["current_node"] == "n1"

    result = client.get(f"/api/analysis/jobs/{job_id}/result")
    assert result.status_code == 200
    assert result.json()["success"] is True


def test_analysis_job_api_returns_error_when_node_fails(monkeypatch):
    def fake_execute(body, target_code=None, is_single=False, progress_callback=None):
        progress_callback({
            "event": "node_complete",
            "node_id": "n2",
            "node_type": "vcp",
            "progress": 1.0,
            "status": "error",
            "error": "forced node failure",
        })
        return {"success": False, "error": "forced node failure", "node_results": {}}

    monkeypatch.setattr("backend.routes.api._execute_sync", fake_execute)
    client = TestClient(app)

    created = client.post("/api/analysis/jobs", json={"nodes": [], "edges": []})
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    deadline = time.time() + 3
    status = {}
    while time.time() < deadline:
        status = client.get(f"/api/analysis/jobs/{job_id}").json()
        if status["status"] == "failed":
            break
        time.sleep(0.02)

    assert status["status"] == "failed"
    assert status["error"] == "forced node failure"

    result = client.get(f"/api/analysis/jobs/{job_id}/result")
    assert result.status_code == 500
    assert result.json()["success"] is False
