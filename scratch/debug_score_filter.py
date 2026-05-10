import pandas as pd
from nodes.score_filter import ScoreFilterNode, ScoreFilterParams
from engine.node_base import ExecutionContext

ctx = ExecutionContext(as_of_date="2026-05-09", run_id="debug")
node = ScoreFilterNode()
df = pd.DataFrame([
    {"code": "1", "rs_rating": 95, "ma_alignment_flag": "ALIGNED", "liquidity_status": "LIQUID", "vcp_status": "VCP_STRICT", "breakout_status": "BREAKOUT_CONFIRMED", "breakout_distance_pct": 0.0, "total_score": 190, "flow_total_score": 30}
])

out = node.run([df], ScoreFilterParams(), ctx)
print(out[["code", "primary_bucket", "rejected_reasons"]].to_dict(orient="records"))
