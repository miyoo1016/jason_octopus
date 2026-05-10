import time
import requests
import json

def verify_speed():
    url = "http://localhost:8080/api/execute"
    # Mirror user's "OR" + "All options"
    payload = {
        "nodes": [
            {"id": "n1", "type": "universe", "params": {"market": "ALL"}},
            {"id": "n2", "type": "liquidity_filter", "params": {"min_trading_value_krw": 2000000000}},
            {"id": "n3", "type": "vcp", "params": {"lookback_days": 120, "min_score": 70}},
            {"id": "n4", "type": "box_breakout", "params": {"box_period": 60, "breakout_pct": 1.0, "vol_C": 1.5}},
            {"id": "n5", "type": "ma_alignment", "params": {}},
            {"id": "n6", "type": "or_filter", "params": {}},
            {"id": "n7", "type": "foreign_flow", "params": {"n_days": 5}},
            {"id": "n8", "type": "institution_flow", "params": {"n_days": 5}},
            {"id": "n9", "type": "rs_rating", "params": {"min_rating": 80}},
            {"id": "n10", "type": "score_filter", "params": {}},
            {"id": "n11", "type": "top_n", "params": {"n": 50, "sort_column": "total_score", "ascending": False}}
        ],
        "edges": [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"}, {"from": "n2", "to": "n4"}, {"from": "n2", "to": "n5"},
            {"from": "n3", "to": "n6"}, {"from": "n4", "to": "n6"}, {"from": "n5", "to": "n6"},
            {"from": "n6", "to": "n7"},
            {"from": "n7", "to": "n8"},
            {"from": "n8", "to": "n9"},
            {"from": "n9", "to": "n10"},
            {"from": "n10", "to": "n11"}
        ]
    }
    
    print(">>> Starting heavy OR query simulation...")
    t0 = time.time()
    try:
        resp = requests.post(url, json=payload, timeout=300)
        elapsed = time.time() - t0
        print(f">>> Finished! Status: {resp.status_code}")
        print(f">>> Elapsed Time: {elapsed:.2f} seconds")
        
        if resp.status_code == 200:
            data = resp.json()
            print(f">>> Result Count: {len(data.get('results', []))}")
        else:
            print(f">>> Error: {resp.text}")
            
    except Exception as e:
        print(f">>> Simulation Failed: {e}")

if __name__ == "__main__":
    verify_speed()
