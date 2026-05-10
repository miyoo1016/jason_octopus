import pandas as pd
from nodes.score_filter import ScoreFilterNode, ScoreFilterParams
from engine.node_base import ExecutionContext

ctx = ExecutionContext(as_of_date="2026-05-09", run_id="debug")
node = ScoreFilterNode()

# Mock B
df_b = pd.DataFrame([{
    "code": "B", "rs_rating": 95, "vcp_status": "REVERSE_EXPANSION", "breakout_status": "NOT_READY",
    "flow_total_score": 5, "ma_alignment_flag": "NOT_ALIGNED", "liquidity_status": "LIQUID",
    "vcp_score": 20, "breakout_score": 5, "rs_score": 50, "macro_score": 50
}])

out_b = node.run([df_b], ScoreFilterParams(), ctx)
print(f"B Bucket: {out_b.loc[0, 'primary_bucket']}")
print(f"B Rejected Reasons: {out_b.loc[0, 'rejected_reasons']}")
print(f"B Count: {len(out_b.loc[0, 'rejected_reasons'].split(',')) if out_b.loc[0, 'rejected_reasons'] else 0}")
print(f"B Reason: {out_b.loc[0, 'tier_reason']}")
