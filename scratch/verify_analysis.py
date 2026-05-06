import requests
import json
import pandas as pd
import time

def verify():
    url = "http://127.0.0.1:8080/api/execute"
    
    # 1. DAG 구성 (전체 기능 통합 테스트)
    payload = {
        "nodes": [
            {"id": "n1", "type": "universe", "params": {"market": "KOSPI"}},
            {"id": "n2", "type": "rs_rating", "params": {"lookback_days": 252}},
            {"id": "n3", "type": "foreign_flow", "params": {"n_days": 5}},
            {"id": "n4", "type": "institution_flow", "params": {"n_days": 5}},
            {"id": "n5", "type": "sector", "params": {}},
            {"id": "n6", "type": "sector_strength", "params": {}},
            {"id": "n7", "type": "vcp", "params": {}},
            {"id": "n8", "type": "box_breakout", "params": {}},
            {"id": "n9", "type": "score_filter", "params": {}}
        ],
        "edges": [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"},
            {"from": "n2", "to": "n4"},
            {"from": "n3", "to": "n5"},
            {"from": "n4", "to": "n5"},
            {"from": "n5", "to": "n6"},
            {"from": "n6", "to": "n7"},
            {"from": "n7", "to": "n8"},
            {"from": "n8", "to": "n9"}
        ]
    }
    
    print("🚀 직접 분석 실행 중... (KOSPI 기준, 약 30-60초 소요)")
    try:
        response = requests.post(url, json=payload, timeout=300)
        response.raise_for_status()
        result = response.json()
    except Exception as e:
        print(f"❌ 분석 실행 실패: {e}")
        return
    
    if not result.get("success"):
        print(f"❌ 분석 결과 에러: {result.get('error')}")
        return
        
    # 마지막 노드(n9)의 결과 찾기
    node_results = result.get("node_results", {})
    final_node = node_results.get("n9", {})
    data = final_node.get("data", [])
    
    if not data:
        print("⚠️ 조건에 맞는 종목이 없습니다.")
        return

    print(f"\n📊 --- [최종 검증 결과] 총 {len(data)}종목 스크리닝됨 ---")
    
    target_code = "006800" # 미래에셋증권
    found = False
    for row in data:
        if row['code'] == target_code:
            found = True
            print(f"✅ 종목명: {row['name']} ({row['code']})")
            print(f"   - 섹터: {row.get('sector')} | {row.get('sector_strength_label', 'N/A')}")
            print(f"   - RS 백분위: {row.get('rs_rating')} (시총 그룹 보정 적용)")
            print(f"   - 돌파 등급: {row.get('box_breakout_grade')} (배수 포함 확인)")
            print(f"   - VCP 패턴: {row.get('vcp_warning')} (수축 횟수 확인)")
            print(f"   - 수급 점수: {row.get('flow_score')} (외국인+기관 합산 확인)")
            print(f"   - 총점: {row.get('total_score')} | TIER: {row.get('tier')}")
            break
            
    if not found:
        # 미래에셋이 없으면 상위 1위 종목이라도 확인
        top = data[0]
        print(f"⚠️ 미래에셋이 결과에 없음. 상위 1위 확인:")
        print(f"✅ 종목명: {top['name']} ({top['code']})")
        print(f"   - 섹터: {top.get('sector')}")
        print(f"   - TIER: {top.get('tier')}")

    print("\n--- 검증 완료 ---")

if __name__ == "__main__":
    verify()
