---
title: API Reference
---

# API Reference

This reference covers the public Python API for developers who want to integrate the autorouter into scripts or extend its functionality.

## Routing

### BatchAutorouter

The primary autorouting coordinator.

```python
from kicad_autorouter.autoroute.batch import BatchAutorouter, AutorouteConfig

config = AutorouteConfig(
    max_passes=20,
    time_limit_seconds=300,
    initial_ripup_cost=1.0,
    ripup_cost_increment=2.0,
    max_ripup_cost=100.0,
    min_improvement_pct=0.5,
    progress_callback=None,  # Callable[[str, float], None]
)
router = BatchAutorouter(board, rules, config)
result = router.run()

# AutorouteResult fields:
result.completed            # bool — all connections routed?
result.passes_run           # int
result.connections_routed   # int
result.connections_failed   # int
result.total_connections    # int
result.final_score          # BoardScore
result.elapsed_seconds      # float
result.completion_percentage  # float (property)
```

### ValidatedRouter

Routes with automatic DRC validation and rollback.

```python
from kicad_autorouter.autoroute.validated_router import ValidatedRouter, CommitPolicy

vr = ValidatedRouter(
    board, rules,
    route_config=AutorouteConfig(max_passes=10),
    policy=CommitPolicy.REJECT_ON_NEW_ERRORS,
)
result = vr.run()

# ValidatedResult fields:
result.route_result      # AutorouteResult
result.drc_before        # DrcResult (baseline)
result.drc_after         # DrcResult (after routing)
result.committed         # bool — was routing kept?
result.rollback_reason   # str — why it was rolled back
result.new_error_count   # int (property)
result.new_warning_count # int (property)
```

### SelectiveRouter

Routes or re-routes specific nets.

```python
from kicad_autorouter.autoroute.selective_router import SelectiveRouter

sr = SelectiveRouter(board, rules, config)

# Resolve nets from various selectors
net_codes = sr.resolve_nets(
    net_codes=[1, 5],
    net_names=["CLK", "SDA"],
    component_refs=["U1"],
)

# Route only selected nets (skip already-routed)
result = sr.route_nets(net_codes=[1, 5])

# Rip up and re-route specific nets
reroute_result = sr.reroute_nets(net_codes=[3, 7])
# RerouteResult fields:
reroute_result.nets_ripped      # int
reroute_result.traces_removed   # int
reroute_result.vias_removed     # int
reroute_result.route_result     # AutorouteResult
reroute_result.rolled_back      # bool

# Route by net class or area
result = sr.route_net_class(["Power", "Signal"])
result = sr.route_area(min_x=0, min_y=0, max_x=25_000_000, max_y=25_000_000)

# Reroute by net class or area
result = sr.reroute_net_class(["Signal"])
result = sr.reroute_area(0, 0, 50_000_000, 50_000_000)
```

### PreRouteAnalyzer

Analyzes board state before routing to catch issues early.

```python
from kicad_autorouter.autoroute.pre_route_analysis import PreRouteAnalyzer

analyzer = PreRouteAnalyzer(board, rules)
report = analyzer.analyze()

# PreRouteReport fields:
report.ready_to_route       # bool — no errors found
report.errors               # list[AnalysisIssue] — blocking issues
report.warnings             # list[AnalysisIssue] — potential problems
report.infos                # list[AnalysisIssue] — informational
report.total_nets           # int
report.total_pads           # int
report.total_connections    # int — unrouted pad pairs
report.copper_layers        # int
report.diff_pairs_detected  # int

# Formatted text output
print(report.format_text())
```

### RoutingStrategy & StrategyExecutor

Compose routing pipelines from named passes.

```python
from kicad_autorouter.autoroute.routing_strategy import (
    RoutingStrategy, StrategyExecutor, PassType, PassConfig,
)

# Use a built-in strategy
strategy = RoutingStrategy.default_two_layer()
strategy = RoutingStrategy.default_multi_layer()
strategy = RoutingStrategy.quick()
strategy = RoutingStrategy.thorough()

# Or build a custom strategy
strategy = RoutingStrategy("My Strategy")
strategy.add_pass(PassConfig(PassType.FANOUT, "Fanout", max_passes=3))
strategy.add_pass(PassConfig(PassType.MAIN, "Route", max_passes=20, time_limit=300.0))
strategy.add_pass(PassConfig(PassType.OPTIMIZE, "Optimize", max_passes=5))
strategy.add_pass(PassConfig(PassType.SPREAD, "Spread", max_passes=3))
strategy.add_pass(PassConfig(PassType.STRAIGHTEN, "Straighten"))
strategy.add_pass(PassConfig(PassType.MITER, "Miter Corners"))
strategy.add_pass(PassConfig(PassType.CLEAN_PAD_ENTRIES, "Clean Pad Entries"))
strategy.add_pass(PassConfig(PassType.HUG, "Hug Existing"))
strategy.add_pass(PassConfig(PassType.DRC_CLEANUP, "DRC Cleanup"))

# Execute
executor = StrategyExecutor(board, rules, progress_callback=my_callback)
result = executor.execute(strategy)

# StrategyResult fields:
result.completed              # bool
result.completion_percentage  # float
result.total_elapsed          # float (seconds)
result.pass_results           # list[PassResult] — per-pass details
```

## Board Model

### RoutingBoard

```python
from kicad_autorouter.board.board import RoutingBoard

board = RoutingBoard()
board.bounding_box = BoundingBox(0, 0, width_nm, height_nm)
board.layer_structure = LayerStructure.create_default(copper_count=2)

# Add items
board.add_item(pad)
trace = board.add_trace(corners, width, layer_index, net_code)
via = board.add_via(position, diameter, drill, start_layer, end_layer, net_code)

# Query
pads = board.get_pads_on_net(net_code)
traces = board.get_traces_on_net(net_code)
vias = board.get_vias_on_net(net_code)
pairs = board.get_unconnected_pad_pairs(net_code)
score = board.compute_score()

# Modify
board.remove_traces_on_net(net_code)
board.remove_vias_on_net(net_code)
count = board.combine_traces()
count = board.remove_tails()
```

### BoardHistory

```python
from kicad_autorouter.board.history import BoardHistory

history = BoardHistory(board, max_entries=50)
history.snapshot("before routing")
# ... make changes ...
history.undo()   # restore previous state
history.redo()   # re-apply undone change

# Named checkpoints
history.save("checkpoint_1")
history.restore("checkpoint_1")
history.list_saves()  # list checkpoint names
```

## Design Rule Checking

### DrcChecker

```python
from kicad_autorouter.drc.checker import DrcChecker, DrcConfig

config = DrcConfig(
    check_clearances=True,
    check_hole_clearance=True,
    check_connectivity=True,
    check_dangles=True,
    check_single_layer_vias=True,
    check_board_edge=True,
    deduplicate=True,
)
checker = DrcChecker(board, config)
result = checker.run()

# DrcResult fields:
result.violations           # list[DrcViolation]
result.error_count          # int
result.warning_count        # int
result.board_items_checked  # int
result.nets_checked         # int
result.elapsed_ms           # float

# Filter violations
clearance_issues = result.violations_of_type(ViolationType.TRACE_TRACE_CLEARANCE)
result.deduplicate()  # remove duplicates in-place
```

### DRC Reports

```python
from kicad_autorouter.drc.report import export_text, export_kicad_json, LengthUnit

# Human-readable text
text = export_text(result, unit=LengthUnit.MM)

# KiCad-compatible JSON
json_str = export_kicad_json(result, unit=LengthUnit.MM)
```

## Optimization

### BatchOptimizer

```python
from kicad_autorouter.optimize.batch_optimizer import (
    BatchOptimizer,
    BatchOptimizerMultiThreaded,
    BatchOptConfig,
)

config = BatchOptConfig(
    max_passes=5,
    pull_tight=True,
    remove_vias=True,
    time_limit_seconds=60,
    num_threads=4,
)

# Sequential
opt = BatchOptimizer(board, rules, config)
result = opt.run()

# Multi-threaded
opt_mt = BatchOptimizerMultiThreaded(board, rules, config)
result = opt_mt.run()

# BatchOptResult fields:
result.traces_improved  # int
result.vias_removed     # int
result.passes_run       # int
result.elapsed_seconds  # float
```

## Configuration

### RouterSettings

```python
from kicad_autorouter.rules.router_settings import (
    RouterSettings,
    LayerPreference,
    LayerDirection,
    ViaCostLevel,
    UpdateStrategy,
    SelectionStrategy,
)

# Create with defaults
settings = RouterSettings()

# Use factory presets
settings = RouterSettings.for_two_layer()
settings = RouterSettings.for_four_layer()

# Customize
settings.max_passes = 30
settings.via_cost = ViaCostLevel.HIGH
settings.update_strategy = UpdateStrategy.GLOBAL
settings.selection_strategy = SelectionStrategy.PRIORITIZED

# Layer preferences
settings.layer_preferences[0] = LayerPreference(
    direction=LayerDirection.HORIZONTAL
)

# Direction cost for pathfinding
cost = settings.get_direction_cost(layer_index=0, is_horizontal=True)

# Serialization
d = settings.to_dict()
settings = RouterSettings.from_dict(d)
```

## Spatial Index

### RTreeIndex

```python
from kicad_autorouter.datastructures.rtree import RTreeIndex

tree = RTreeIndex(bounding_box)
tree.bulk_load(items)          # STR bulk-loading
tree.insert(item)              # Single insert
tree.remove(item)              # Remove by item
hits = tree.query_region(bbox) # Region query
hits = tree.query_point(point) # Point query
conflicts = tree.get_conflicting_items(item, clearance)
height = tree.tree_height()
```

## Profiling

### RoutingProfiler

```python
from kicad_autorouter.utils.profiler import RoutingProfiler, BenchmarkTarget

profiler = RoutingProfiler()

with profiler.phase("routing", item_count=100):
    # ... routing work ...
    pass

with profiler.phase("optimization"):
    # ... optimization work ...
    pass

print(profiler.summary())
data = profiler.to_dict()  # for JSON export

# Benchmark checking
targets = [BenchmarkTarget("routing", max_ms=5000)]
failures = check_benchmarks(profiler, targets)
```
