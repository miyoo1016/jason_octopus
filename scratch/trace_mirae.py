import requests
import json

def trace_mirae():
    url = "http://127.0.0.1:8080/api/execute"
    payload = {
        "nodes": [
            {"id": "n1", "type": "universe", "params": {"market": "KOSPI"}},
            {"id": "n2", "type": "rs_rating", "params": {"lookback_days": 252}},
            {"id": "n3", "type": "foreign_flow", "params": {"n_days": 5}},
            {"id": "n4", "type": "sector", "params": {}},
            {"id": "n5", "type": "score_filter", "params": {}}
        ],
        "edges": [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"},
            {"from": "n3", "to": "n4"},
            {"from": "n4", "to": "n5"}
        ]
    }
    
    try:
        print("🚀 미래에셋증권 전 노드 추적 분석 시작...")
        res = requests.post(url, json=payload, timeout=300)
        result = res.json()
        node_results = result.get("node_results", {})
        
        target = "006800" # 미래에셋증권
        
        for nid in ["n1", "n2", "n3", "n4", "n5"]:
            data = node_results.get(nid, {}).get("data", [])
            found = next((s for s in data if s["code"] == target), None)
            if found:
                print(f"✅ [{nid}] 미래에셋 생존 - RS: {found.get('rs_rating')}, 섹터: {found.get('sector')}, 총점: {found.get('total_score')}")
            else:
                print(f"❌ [{nid}] 미래에셋 탈락!")
                if nid == "n2":
                    print("   - RS 필터에서 탈락했을 가능성 큼 (RS 점수 미달)")
                break
                
    except Exception as e:
        print(f"❌ 분석 실패: {e}")

if __name__ == "__main__":
    trace_mirae()
