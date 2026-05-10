import requests
import json
import os
import time

def test_analysis():
    url = "http://127.0.0.1:8000"
    
    # 1. Start Job
    print("Starting analysis job...")
    payload = {
        "nodes": [
            {"id": "n1", "type": "universe", "params": {"market": "KOSPI"}},
            {"id": "n2", "type": "liquidity_filter", "params": {"min_trading_value_krw": 2000000000}},
            {"id": "n3", "type": "score_filter", "params": {}}
        ],
        "edges": [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"}
        ],
        "max_symbols": 30
    }
    
    try:
        resp = requests.post(f"{url}/api/analysis/jobs", json=payload, timeout=10)
        resp.raise_for_status()
        job_id = resp.json()["job_id"]
        print(f"Job started: {job_id}")
    except Exception as e:
        print(f"Failed to start job: {e}")
        return

    # 2. Wait for completion
    for _ in range(30):
        resp = requests.get(f"{url}/api/analysis/jobs/{job_id}")
        status = resp.json()["status"]
        print(f"Status: {status} ({resp.json().get('progress', 0)*100:.0f}%)")
        if status == "completed":
            break
        if status == "failed":
            print(f"Job failed: {resp.json().get('error')}")
            return
        time.sleep(2)
    else:
        print("Timeout waiting for job")
        return

    # 3. Get Result
    print("Fetching result...")
    resp = requests.get(f"{url}/api/analysis/jobs/{job_id}/result")
    resp.raise_for_status()
    result = resp.json()
    
    # 4. Verify Summary Labels
    summary = result.get("summary", {})
    print("\n[Summary Verification]")
    labels = [
        "total_analyzed_count", "classification_completed_count", 
        "core_candidate_count", "final_rejected_count",
        "intermediate_filtered_count", "intermediate_filtered_rate",
        "final_rejected_rate", "watch_alert_rate"
    ]
    for label in labels:
        print(f"{label}: {summary.get(label)}")

    # 5. Verify Diagnostics
    diagnostics = result.get("diagnostics", {})
    print("\n[Diagnostics Verification]")
    print(f"NaN Columns: {len(diagnostics.get('nan_columns', []))}")
    print(f"Liquidity Status Dist: {diagnostics.get('liquidity_status_distribution')}")
    print(f"Suspicious Records: {len(diagnostics.get('suspicious_liquidity_records', []))}")
    
    # 6. Verify Primary Bucket
    results = result.get("results", {})
    all_stocks = []
    for bucket in ["tier1", "tier2", "tier3", "watchlist", "rejected"]:
        all_stocks.extend(results.get(bucket, []))
    
    print(f"\nTotal stocks classified: {len(all_stocks)}")
    if len(all_stocks) > 0:
        first_stock = all_stocks[0]
        print(f"First stock: {first_stock.get('name')} | Bucket: {first_stock.get('primary_bucket')} | Liquidity: {first_stock.get('liquidity_status')}")
    
    # 삼성전기(009150) 확인 (KOSPI 30개 안에 있다면)
    sem = next((s for s in all_stocks if s["code"] == "009150"), None)
    if sem:
        print(f"\n[Samsung Electro-Mechanics 009150 Verification]")
        print(f"Close: {sem.get('liquidity_close')}")
        print(f"Volume: {sem.get('liquidity_volume')}")
        print(f"Calculated Trading Value: {sem.get('calculated_trading_value')}")
        print(f"Liquidity Status: {sem.get('liquidity_status')}")
        print(f"Liquidity Source: {sem.get('liquidity_trading_value_source')}")
    else:
        print("\n[Samsung Electro-Mechanics 009150 not in Top 30]")

if __name__ == "__main__":
    test_analysis()
