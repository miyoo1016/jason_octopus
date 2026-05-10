import json
import math
import logging
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from enum import Enum
from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

def sanitize_for_json(obj: Any) -> Any:
    """
    JSON 직렬화가 불가능한 객체들을 재귀적으로 안전한 타입으로 변환합니다.
    - numpy.ndarray -> list
    - numpy scalar -> int/float/bool
    - pandas DataFrame -> list[dict]
    - pandas Series -> list
    - datetime/date -> ISO string
    - NaN, Inf, pandas.NA, NaT -> None
    - set, tuple -> list
    - pathlib.Path -> str
    - Decimal -> float
    - Enum -> value
    - Counter -> dict
    """
    # 1. Basic types that are already JSON safe
    if obj is None:
        return None
    if isinstance(obj, (str, bool)):
        return obj
    
    # 2. Number types (including numpy/pandas specials)
    if isinstance(obj, (int, float, np.integer, np.floating)):
        if isinstance(obj, (float, np.floating)):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return float(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        return obj

    # 3. Numpy bool
    if isinstance(obj, np.bool_):
        return bool(obj)

    # 4. Collections (Recursive) - Move this up to avoid pd.isna(ndarray) error
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, np.ndarray, pd.Index)):
        if isinstance(obj, np.ndarray):
            return [sanitize_for_json(v) for v in obj.tolist()]
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, Counter):
        return {str(k): int(v) for k, v in obj.items()}

    # 5. Pandas/Numpy Nulls (Only for scalars now)
    try:
        if pd.isna(obj): # Handles pd.NA, np.nan, NaT
            return None
    except (ValueError, TypeError):
        # In case of array-like objects that escaped above
        pass

    # 6. Date and Time
    if isinstance(obj, (datetime, date, pd.Timestamp)):
        return obj.isoformat()
    if isinstance(obj, pd.Timedelta):
        return obj.total_seconds()

    # 7. Pandas Objects
    if isinstance(obj, pd.DataFrame):
        return sanitize_for_json(obj.to_dict(orient="records"))
    if isinstance(obj, pd.Series):
        return sanitize_for_json(obj.tolist())

    # 8. Others
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, Enum):
        return obj.value

    # Fallback to string representation with a warning
    try:
        # Check if it's a numpy scalar
        if hasattr(obj, "item") and callable(obj.item):
            return sanitize_for_json(obj.item())
    except:
        pass

    logger.warning(f"Non-JSON serializable object of type {type(obj)} encountered. Falling back to string. Path candidate: {obj}")
    return str(obj)

def find_non_json_serializable(obj: Any, path: str = "$") -> list[str]:
    """
    JSON 직렬화가 불가능한 객체의 위치와 타입을 찾습니다. (디버깅용)
    """
    errors = []
    
    # Try direct serialization
    try:
        json.dumps(obj)
        return []
    except (TypeError, OverflowError, ValueError):
        pass

    if isinstance(obj, dict):
        for k, v in obj.items():
            errors.extend(find_non_json_serializable(v, f"{path}.{k}"))
    elif isinstance(obj, (list, tuple, set)):
        for i, v in enumerate(obj):
            errors.extend(find_non_json_serializable(v, f"{path}[{i}]"))
    else:
        errors.append(f"Path: {path}, Type: {type(obj)}, Value: {obj}")
    
    return errors

def validate_json_safety(obj: Any, context: str = "Unknown"):
    """
    객체가 JSON 직렬화 가능한지 검증하고, 실패 시 상세 로그를 남깁니다.
    """
    try:
        json.dumps(obj, allow_nan=False)
        return True
    except Exception as e:
        errors = find_non_json_serializable(obj)
        logger.error(f"JSON Serialization failed in {context}: {e}")
        for err in errors:
            logger.error(f"  -> {err}")
        return False
