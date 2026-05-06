import requests
import json

def debug_mirae():
    url = "http://127.0.0.1:8080/api/execute"
    payload = {
        "strategy_id": "test_mirae",
        "universe": "KOSPI",
        "as_of_date": "2026-05-06"
    }
    
    try:
        print("🚀 미래에셋증권 정밀 분석 시작...")
        res = requests.post(url, json=payload, timeout=600)
        data = res.json()
        stocks = data.get("results", [])
        
        mirae = next((s for s in stocks if s["code"] == "006800"), None)
        
        if mirae:
            print(f"✅ 미래에셋증권 발견!")
            print(f"   - 섹터: {mirae.get('sector')}")
            print(f"   - RS 점수: {mirae.get('rs_rating')}")
            print(f"   - 총점: {mirae.get('total_score')}")
            print(f"   - TIER: {mirae.get('tier')}")
        else:
            print("❌ 미래에셋증권이 최종 리스트에 없음. 탈락 원인 분석 중...")
            # 전체 종목 중 미래에셋의 RS 점수가 얼마인지 확인하기 위해 n2 노드 결과 추적 필요
            # 일단 상위 50종목의 RS 점수 분포 확인
            top_rs = sorted([s.get('rs_rating', 0) for s in stocks], reverse=True)[:10]
            print(f"   - 현재 상위권 RS 분포: {top_rs}")
            
    except Exception as e:
        print(f"❌ 분석 실패: {e}")

if __name__ == "__main__":
    debug_mirae()
