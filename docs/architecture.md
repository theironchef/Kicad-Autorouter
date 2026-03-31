---
title: Architecture
---

# Architecture

This document describes how the kicad-autorouter is organized and how the routing algorithm works.

## Package Structure

```
kicad_autorouter/
├── geometry/        2D primitives (points, vectors, shapes, octagons)
├── board/           PCB model (traces, vias, pads, nets, layers, history)
├── rules/           Design rules, clearance matrix, router settings
├── datastructures/  Spatial search tree, R-tree, priority queue
├── autoroute/       Core engine: expansion rooms, maze search, batch router
├── optimize/        Post-route: pull-tight, via reduction, batch optimizer
├── drc/             Design rule checker: violations, checker, reports
├── io/              KiCad pcbnew API bridge (read/write)
├── utils/           Coordinate transforms, timing, profiling, ID generation
└── plugin.py        KiCad Action Plugin registration and UI
```

## Module Dependency Flow

```
io/kicad_reader → board/ → autoroute/ → optimize/ → drc/ → io/kicad_writer
                    ↑           ↑            ↑          ↑
                  rules/    geometry/   datastructures/  utils/
```

The flow is strictly left-to-right for the routing pipeline. Support modules (`geometry`, `rules`, `datastructures`, `utils`) are imported by multiple stages.

## How Routing Works

The autorouter follows an 8-step pipeline:

### 1. Read

`KiCadBoardReader` imports the board state from KiCad's pcbnew SWIG API. This includes footprints, pads, existing traces, vias, zones, the board outline, nets, net classes, and design rules. All coordinates are converted to nanometers (KiCad's internal unit).

### 2. Partition

The `AutorouteEngine` divides free board space into convex tile-shaped **expansion rooms** on each copper layer. Rooms represent areas where traces can be routed without immediately hitting obstacles.

### 3. Connect

The engine identifies **doors** (shared boundaries between rooms on the same layer) and **drills** (via opportunities connecting rooms on different layers). Together with rooms, these form the `ExpansionRoomGraph`.

### 4. Search

`MazeSearchAlgo` runs A* pathfinding from source pads through the room/door/drill graph to target pads. The priority queue scores paths by estimated total cost (current path cost + heuristic distance to target). Via placements incur additional cost based on `RouterSettings.via_cost`.

### 5. Insert

`InsertFoundConnectionAlgo` converts the found path (a sequence of rooms, doors, and drills) into physical traces and vias. Trace segments are snapped to 45° angles when `prefer_45_degree` is enabled. Endpoints are snapped to pad centers for reliable connectivity.

### 6. Iterate

The `BatchAutorouter` repeats steps 2-5 for all unrouted connections. Each pass increases the ripup cost multiplier, making the router less willing to disturb existing routes. Routing stops when all connections are made, no improvement is detected, or the time/pass limit is reached.

### 7. Optimize

Post-route optimization improves trace quality:

- **PullTightAlgo45** — find diagonal shortcuts that shorten 45° traces
- **PullTightAlgo90** — find orthogonal shortcuts for Manhattan traces
- **CornerSmoother** — replace acute angles with smoother bends
- **ViaOptimizer** — remove vias where traces can stay on one layer

The `BatchOptimizer` can run these in parallel across independent nets using `BatchOptimizerMultiThreaded`.

### 8. Write

`KiCadBoardWriter` adds the new tracks and vias back to the KiCad board via the pcbnew API. Only new items are added — existing board content is not modified.

## Key Data Structures

### RoutingBoard

The central data structure. Holds all board items (pads, traces, vias, obstacles), net definitions, layer structure, and design rules. Provides methods for querying items by net, adding/removing traces, computing board score, and finding unconnected pad pairs.

### SearchTree and RTreeIndex

Spatial indices for fast collision queries. `SearchTree` is a quadtree used during routing. `RTreeIndex` uses Sort-Tile-Recursive bulk-loading for efficient construction and is used for DRC and optimization.

### BoardHistory

Snapshot-based undo/redo system. Captures the full board item state before operations and allows rollback. Used by `ValidatedRouter` and `SelectiveRouter` to safely revert failed operations.

### ExpansionRoomGraph

The routing graph connecting expansion rooms via doors and drills. Rebuilt before each routing pass to reflect the current board state.

## Board Item Hierarchy

```
Item (abstract base)
├── Pad          — component connection point
├── Trace        — polyline with width on a single layer
├── Via          — vertical connection between layers
└── ObstacleArea — keepout zone or board outline
```

Every item has a unique ID, belongs to one or more nets, occupies one or more layers, and has a `FixedState` that determines whether the router can modify it.

## Threading Model

The autorouter uses Python's `concurrent.futures.ThreadPoolExecutor` for parallel optimization. Nets are partitioned so that each thread works on independent nets, avoiding the need for locks on board items.

Due to Python's GIL, CPU-bound computation doesn't get true parallelism, but the partitioned architecture is ready for future migration to multiprocessing or native extensions.
