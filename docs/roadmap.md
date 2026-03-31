---
title: Roadmap
---

# Roadmap

This page tracks features that have been implemented and features that remain unfinished. The autorouter is derived from [Freerouting](https://github.com/freerouting/freerouting), which has hundreds of classes — this roadmap tracks progress toward parity with the features that matter for a KiCad plugin.

Have a feature request? [Open an issue on GitHub](https://github.com/danwillis-aethl/kicad-autorouter/issues).

---

## What's Done (v1.1 — Situs-Inspired Improvements)

### Pre-Routing Analysis
- Pre-flight board analysis report before routing starts
- Checks design rules, layer setup, connectivity, component placement, net classes, differential pairs, routing feasibility
- Error/warning/info severity levels with actionable messages
- Formatted text report output

### Composable Routing Strategies
- Build routing pipelines from named passes (Fanout → Main → Optimize → Spread → Straighten → Miter → Clean Pad Entries → Hug → DRC Cleanup)
- Four built-in strategies: Quick, Two-Layer Default, Multi-Layer Default, Thorough
- Custom strategy builder with fluent API
- Progress callbacks for UI integration
- Per-pass timing and result tracking

### New Optimization Passes
- **Spread** — redistribute traces to use free space evenly
- **Straighten** — reduce corner count via probe-based path optimization
- **Miter** — convert 90° corners to 45° chamfered segments
- **Clean Pad Entries** — reroute trace entries along pad's longest axis
- **Hug** — consolidate traces to follow existing routing at minimum clearance

### Post-Route DRC Cleanup
- Automatic removal of traces and vias that violate DRC after routing
- Integrated as a strategy pass (DRC_CLEANUP)

### Extended Selective Routing
- Route by net class (all nets in one or more net classes)
- Route by area (all nets with pads inside a bounding region)
- Reroute by net class and by area with automatic rollback
- Combined selectors (net codes + net names + class names + area in one call)

### Differential Pair Fanout
- Diff pair pads escape-routed first, together on same layer and same side
- Automatic diff pair detection by naming convention (_P/_N, +/-, P/N)
- Maintains diff pair gap from net class during fanout

---

## What's Done (v1.0)

### Core Routing
- Maze-based A* autorouting with priority-queue search
- Multi-pass ripup-and-reroute with escalating costs
- Trace shoving (push existing traces aside)
- 45-degree routing with snap-to-45° segments
- BGA/QFP fanout with automatic pad escape routing
- Via type selection (through, blind, buried, micro)
- Segment-level collision detection using octagon geometry

### Board Model
- Full board model: traces, vias, pads, nets, layers, components, obstacles
- Trace splitting, combination, overlap detection, tail cleanup
- Undo/redo with snapshot-based board history
- Component movement with collision-aware obstacle shoving
- Differential pair detection and length matching
- Net routing priorities (signal-first, power-last, diff-pair boost)

### Optimization
- Pull-tight shortening (45° and 90°)
- Corner smoothing and acute angle improvement
- Via removal and via relocation
- Multi-threaded batch optimization

### DRC
- Clearance violations (trace, pad, via — all combinations)
- Hole clearance checking
- Connectivity and disconnected net detection
- Dangling trace and single-layer via detection
- Board edge clearance
- Report export in KiCad JSON and plain text

### Configuration
- RouterSettings with 25+ tunable parameters
- Via cost tuning, ripup cost configuration, layer direction preferences
- Board update strategies (greedy, global, hybrid)
- Connection selection strategies (sequential, shortest-first, random, prioritized)
- Settings serialization and factory presets
- Pre-commit DRC validation with commit policies and rollback

### Interactive & Selective Routing
- Route only selected nets (by code, name, or component)
- Rip up and re-route specific nets with automatic rollback
- ValidatedRouter with DRC-aware commit/reject

### Infrastructure
- KiCad 10 native integration via pcbnew API
- R-tree spatial index with STR bulk-loading
- Performance profiling with benchmark targets
- 265 tests (unit, integration, performance)
- PCM installable package
- Full documentation site

---

## Unfinished — Geometry

These are Freerouting geometry primitives not yet ported. Most routing scenarios work without them, but they would improve precision in edge cases.

- **RationalPoint / RationalVector** — exact rational arithmetic for intersection calculations that lose precision with floating point
- **BigIntDirection** — arbitrary-precision direction for near-parallel line intersections
- **Circle / Ellipse** — curved shape primitives (currently approximated by polygons)
- **IntBox** — axis-aligned rectangle as a distinct type (currently handled by BoundingBox)
- **Simplex** — geometric simplex for convex hull operations
- **FortyfiveDegreeBoundingDirections** — 45° bounding computations

## Unfinished — Board Model

- **Padstack-based via system** — different pad shapes per layer for a single via (currently uses uniform circles)
- **ConductionArea** — copper plane regions for power/ground flooding
- **ViaObstacleArea** — via-specific keepout zones
- **ComponentOutline / ComponentObstacleArea** — 3D-aware component clearances
- **BoardOutline as first-class item** — currently read as an obstacle, not a full board item
- **Cycle detection in traces** — detect routing loops
- **Shape-based trace clipping** — clip traces to arbitrary shapes
- **BoardObservers** — observer pattern for board state change notifications
- **ItemSelectionFilter** — configurable filtering for item queries

## Unfinished — Rules

- **Layer-specific clearance rules** — different clearances per copper layer
- **Hole clearance rules** — explicit via-to-trace spacing rules
- **ViaInfo / ViaInfos / ViaRule** — formal via type definitions and rule sets
- **DefaultItemClearanceClasses** — default clearance class assignments

## Unfinished — Data Structures

- **ShapeSearchTree45Degree** — spatial index optimized for 45° geometry queries
- **ShapeSearchTree90Degree** — spatial index optimized for Manhattan queries
- **ShapeTraceEntries** — trace-specific spatial query results
- **MinAreaTree** — minimum area search tree
- **PlanarDelaunayTriangulation** — Delaunay triangulation for mesh-based routing
- **UndoableObjects** — generic undo/redo data structure

## Unfinished — Autoroute

These are Freerouting's more advanced routing algorithms. The current maze search works, but these would improve routing quality on dense boards.

- **CompleteExpansionRoom / CompleteFreeSpaceExpansionRoom** — proper room completion with exact free-space boundaries
- **IncompleteFreeSpaceExpansionRoom** — incremental partial expansion
- **ObstacleExpansionRoom** — obstacle-aware room boundary generation
- **MazeShoveTraceAlgo** — shove traces during maze search (currently shoving is a separate pass)
- **LocateFoundConnectionAlgo variants** — 45°, 90°, and any-angle path location
- **ForcedPadAlgo / ForcedViaAlgo** — forced routing through specific pads and vias
- **AutorouteControl** — routing parameter management per connection
- **DestinationDistance** — improved heuristic distance metrics
- **DrillPage / DrillPageArray** — via placement coordinate grid
- **Small door validation** — perpendicular entry requirement for expansion doors
- **Neckdown adjustments** — trace narrowing at destination pins
- **Ripup cost based on trace properties** — width-aware and priority-aware ripup costs

## Unfinished — Optimization

- **PullTightAlgoAnyAngle** — unrestricted-angle pull-tight optimization
- **Keep-point enforcement** — preserve specific points during optimization
- **Zero-length segment cleanup** — remove degenerate segments with clearance validation

## Unfinished — Spatial & Collision

- **Proper convex decomposition** of free space around obstacles
- **Sutherland-Hodgman room partitioning** — polygon clipping for room boundaries

## Unfinished — Performance

- **Incremental expansion graph updates** — avoid full graph rebuild per connection
- **Shape caching** — cache expensive geometry operations
- **Resource usage monitoring** — memory and per-net time tracking
- **Routing failure diagnostics** — structured logging for failed routes

## Unfinished — DRC

- **Airline / ratsnest calculation** for incomplete connections
- **Length matching violation detection** — flag differential pairs that exceed tolerance

## Unfinished — I/O

- **Copper zone fill interaction** — respect and route around filled zones

---

## Not Planned

These Freerouting features are intentionally excluded because KiCad already provides them or they don't apply to a plugin:

- GUI application (72 Swing/AWT classes) — KiCad provides the UI
- Interactive state machine (RouteState, DragState, etc.) — KiCad has its own interactive router
- REST API / server mode — not needed for a plugin
- Specctra DSN/SES file I/O (40+ classes) — native plugin reads/writes via pcbnew API directly
- EAGLE ULP / script export — KiCad-specific plugin
- Configuration file persistence (GUIDefaultsFile) — uses KiCad's plugin settings
- Localization/i18n — follows KiCad's localization system
- Docker deployment — not applicable to a KiCad plugin
