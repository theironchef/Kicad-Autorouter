"""
Core autorouting engine.

Implements Freerouting's multi-pass ripup-and-reroute autorouter:

    engine.py     - AutorouteEngine: manages expansion rooms and maze search state
    expansion.py  - ExpansionRoom, ExpansionDoor, ExpansionDrill: spatial partitioning
    maze.py       - MazeSearchAlgo: priority-queue A*-like pathfinding
    batch.py      - BatchAutorouter: multi-pass coordination with ripup cost escalation
    locate.py     - LocateFoundConnectionAlgo: converts maze search result to path
    insert.py     - InsertFoundConnectionAlgo: adds routed path to board
"""

from kicad_autorouter.autoroute.engine import AutorouteEngine
from kicad_autorouter.autoroute.maze import MazeSearchAlgo
from kicad_autorouter.autoroute.batch import BatchAutorouter
from kicad_autorouter.autoroute.pre_route_analysis import (
    PreRouteAnalyzer,
    PreRouteReport,
    AnalysisIssue,
    IssueSeverity,
)
from kicad_autorouter.autoroute.routing_strategy import (
    RoutingStrategy,
    StrategyExecutor,
    StrategyResult,
    PassType,
    PassConfig,
    PassResult,
)

__all__ = [
    "AutorouteEngine",
    "MazeSearchAlgo",
    "BatchAutorouter",
    "PreRouteAnalyzer",
    "PreRouteReport",
    "AnalysisIssue",
    "IssueSeverity",
    "RoutingStrategy",
    "StrategyExecutor",
    "StrategyResult",
    "PassType",
    "PassConfig",
    "PassResult",
]
