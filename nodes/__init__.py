"""
모든 노드를 한 곳에 등록하고 내보내는 레지스트리.
"""
from engine.node_base import BaseNode

from .universe import UniverseNode
from .vcp import VcpNode
from .box_breakout import BoxBreakoutNode
from .ma_alignment import MaAlignmentNode
from .rs_rating import RsRatingNode
from .sector import SectorNode
from .macro_filter import MacroFilterNode
from .foreign_flow import ForeignFlowNode
from .institution_flow import InstitutionFlowNode
from .liquidity_filter import LiquidityFilterNode
from .and_filter import AndFilterNode
from .or_filter import OrFilterNode
from .score_filter import ScoreFilterNode
from .top_n import TopNNode
from .ai_analysis import AiAnalysisNode
from .news_search import NewsSearchNode
from .sector_strength import SectorStrengthNode

NODE_REGISTRY: dict[str, type[BaseNode]] = {
    UniverseNode.NODE_TYPE: UniverseNode,
    VcpNode.NODE_TYPE: VcpNode,
    BoxBreakoutNode.NODE_TYPE: BoxBreakoutNode,
    MaAlignmentNode.NODE_TYPE: MaAlignmentNode,
    RsRatingNode.NODE_TYPE: RsRatingNode,
    SectorNode.NODE_TYPE: SectorNode,
    MacroFilterNode.NODE_TYPE: MacroFilterNode,
    ForeignFlowNode.NODE_TYPE: ForeignFlowNode,
    InstitutionFlowNode.NODE_TYPE: InstitutionFlowNode,
    LiquidityFilterNode.NODE_TYPE: LiquidityFilterNode,
    AndFilterNode.NODE_TYPE: AndFilterNode,
    OrFilterNode.NODE_TYPE: OrFilterNode,
    ScoreFilterNode.NODE_TYPE: ScoreFilterNode,
    TopNNode.NODE_TYPE: TopNNode,
    AiAnalysisNode.NODE_TYPE: AiAnalysisNode,
    NewsSearchNode.NODE_TYPE: NewsSearchNode,
    SectorStrengthNode.NODE_TYPE: SectorStrengthNode,
}
