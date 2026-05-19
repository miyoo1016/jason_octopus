import pandas as pd
import json
import os
from backend.performance_tracker import save_snapshots, get_performance_summary, SNAPSHOTS_FILE

def test_performance_tracker():
    # Setup dummy data
    if os.path.exists(SNAPSHOTS_FILE):
        os.remove(SNAPSHOTS_FILE)

    # 1. Create 10 past snapshots (dummy dates from May 9 to May 18 to allow offset 10 calculations)
    dates = [f"2026-05-{d:02d}" for d in range(9, 19)]
    
    # Base close prices path for Apple:
    # 9th: 100, 10th: 120 (Peak), 11th: 110, 12th: 90 (Trough: -25% from 120), 13-18th: 95
    prices_path = {
        "2026-05-09": 100,
        "2026-05-10": 120,
        "2026-05-11": 110,
        "2026-05-12": 90,
        "2026-05-13": 95,
        "2026-05-14": 95,
        "2026-05-15": 95,
        "2026-05-16": 95,
        "2026-05-17": 95,
        "2026-05-18": 95,
    }

    for d in dates:
        # Banana (A002) has close = 0.0 or missing close (None) to test exclusion
        close_b = 0.0 if d == "2026-05-09" else None
        
        # Insert a duplicate snapshot for Cherry on "2026-05-09" with close=999 FIRST
        if d == "2026-05-09":
            dup_df = pd.DataFrame([
                {"code": "A003", "name": "Cherry", "close": 999, "final_label": "REJECTED", "total_score": 40},
            ])
            save_snapshots(dup_df, d)
            
        past_df = pd.DataFrame([
            {"code": "A001", "name": "Apple", "close": prices_path[d], "final_label": "BUY_CANDIDATE", "total_score": 90},
            {"code": "A002", "name": "Banana", "close": close_b, "final_label": "NEAR_BUY", "total_score": 85},
            {"code": "A003", "name": "Cherry", "close": 50, "final_label": "REJECTED", "total_score": 40},
        ])
        save_snapshots(past_df, d)

    # 2. Create current snapshot
    current_date = "2026-05-19"
    current_df = pd.DataFrame([
        {"code": "A001", "name": "Apple", "close": 110, "final_label": "BUY_CANDIDATE"}, # +10% from 9th (100)
        {"code": "A002", "name": "Banana", "close": 190, "final_label": "NEAR_BUY"}, # close is missing in past, should skip return calculation!
        {"code": "A003", "name": "Cherry", "close": 45, "final_label": "REJECTED"}, # -10% from 9th (50), testing dup deduplication worked (didn't use 999)
    ])
    
    # 3. Calculate performance
    summary = get_performance_summary(current_df, current_date)
    
    print("--- Test Performance Tracker ---")
    print("Status:", summary["status"])
    print("Message:", summary["message"])
    
    if summary["status"] == "READY":
        by_label = summary["by_label"]
        
        # Test 10d returns
        print("BUY_CANDIDATE 10d Return:", by_label["BUY_CANDIDATE"]["return_10d"])
        assert abs(by_label["BUY_CANDIDATE"]["return_10d"] - 10.0) < 0.1
        
        # Test missing/0.0 close price exclusion
        print("NEAR_BUY 10d Return (Should be None due to missing close):", by_label["NEAR_BUY"]["return_10d"])
        assert by_label["NEAR_BUY"]["return_10d"] is None
        
        # Test deduplication (since 50 was written second, keeping last should mean return is -10% from 50 to 45)
        print("REJECTED 10d Return (Dup deduplication check):", by_label["REJECTED"]["return_10d"])
        assert abs(by_label["REJECTED"]["return_10d"] - (-10.0)) < 0.1
        
        # Test max_drawdown_10d_close (Apple path: 100 -> 120 -> 110 -> 90 -> 95 -> ... -> today 110. Peak is 120, trough is 90. DD = -25%)
        print("BUY_CANDIDATE 10d Close MDD:", by_label["BUY_CANDIDATE"]["max_drawdown_10d_close"])
        assert abs(by_label["BUY_CANDIDATE"]["max_drawdown_10d_close"] - (-25.0)) < 0.1
        
        print("✅ All stability reinforcement test cases passed successfully!")
    else:
        print("❌ Expected status READY, got", summary["status"])

    # Clean up
    if os.path.exists(SNAPSHOTS_FILE):
        os.remove(SNAPSHOTS_FILE)

if __name__ == "__main__":
    test_performance_tracker()
