import os
import pandas as pd

GOLDEN_DIR = os.path.dirname(os.path.abspath(__file__))

def save_golden_set(df: pd.DataFrame, test_name: str) -> None:
    """테스트 결과를 골든셋 파일로 저장합니다."""
    path = os.path.join(GOLDEN_DIR, f"{test_name}.parquet")
    df.to_parquet(path, index=False)
    
def load_golden_set(test_name: str) -> pd.DataFrame:
    """저장된 골든셋 파일을 불러옵니다."""
    path = os.path.join(GOLDEN_DIR, f"{test_name}.parquet")
    if not os.path.exists(path):
        raise FileNotFoundError(f"골든셋 파일이 없습니다: {path}. 먼저 생성해야 합니다.")
    return pd.read_parquet(path)
