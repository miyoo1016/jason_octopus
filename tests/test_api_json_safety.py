import json
import os
import unittest
from datetime import datetime
import numpy as np
from fastapi.testclient import TestClient
from backend.main import app
from backend.analysis_jobs import analysis_jobs
from backend.utils.json_safety import sanitize_for_json

class TestApiJsonSafety(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_job_result_with_ndarray(self):
        """job.result에 ndarray가 포함되어 있어도 API 응답이 성공해야 함."""
        # Mock job with ndarray in result
        def mock_runner(progress_callback):
            return {
                "success": True,
                "data": np.array([1, 2, 3]),
                "nested": {
                    "vals": np.array([1.1, 2.2]),
                    "inf": np.inf
                },
                "diagnostics": {
                    "primary_bucket_distribution": np.array([10, 5, 2])
                }
            }
        
        job = analysis_jobs.create(mock_runner)
        # Force execution in sync for test
        job.result = mock_runner(None)
        job.status = "completed"
        
        response = self.client.get(f"/api/analysis/jobs/{job.job_id}/result")
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        self.assertEqual(data["data"], [1, 2, 3])
        self.assertEqual(data["nested"]["vals"], [1.1, 2.2])
        self.assertEqual(data["nested"]["inf"], None)
        self.assertEqual(data["diagnostics"]["primary_bucket_distribution"], [10, 5, 2])

    def test_result_storage_safety(self):
        """분석 결과 저장 시 ndarray가 포함되어 있어도 파일 저장이 성공해야 함."""
        as_of_date = "2023-05-09"
        timestamp = "999999"
        filename = f"screening_{as_of_date}_{timestamp}.json"
        save_path = os.path.join("data", "results", filename)
        
        # Ensure dir exists
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        
        payload = {
            "as_of_date": as_of_date,
            "data": np.array([10, 20]),
            "timestamp": timestamp
        }
        
        try:
            sanitized = sanitize_for_json(payload)
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(sanitized, f, ensure_ascii=False, indent=2)
            
            self.assertTrue(os.path.exists(save_path))
            with open(save_path, "r", encoding="utf-8") as f:
                saved_data = json.load(f)
            self.assertEqual(saved_data["data"], [10, 20])
        finally:
            if os.path.exists(save_path):
                os.remove(save_path)

if __name__ == "__main__":
    unittest.main()
