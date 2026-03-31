---
title: User Guide
---

# User Guide

This guide covers how to use the kicad-autorouter for common PCB routing tasks.

## Basic Autorouting

The simplest workflow routes all unconnected nets on your board.

1. Open your PCB in KiCad's PCB Editor
2. Ensure all components are placed and the board outline is defined
3. Click the **Autorouter** button in the toolbar
4. In the dialog, set:
   - **Max passes** — how many routing iterations to attempt (default: 20)
   - **Time limit** — maximum seconds before stopping (default: 300)
5. Click **Route**

The autorouter will attempt to connect all unconnected pad pairs using maze-based A* pathfinding with multi-pass ripup-and-reroute. Progress is shown in the dialog.

## Routing Strategies

The autorouter supports several strategies that affect routing order and optimization timing.

### Connection Selection

Controls the order in which unrouted connections are attempted:

- **Sequential** — route in the order connections appear
- **Shortest-first** — route shortest connections first, power nets last (default)
- **Random** — shuffle order each pass to avoid local minima
- **Prioritized** — use net priority scores (signal > diff-pair > power)

### Board Update Strategy

Controls when post-route optimization runs:

- **Greedy** — optimize each trace immediately after insertion
- **Global** — defer optimization to the end of each pass
- **Hybrid** — start greedy, switch to global after a threshold (default)

## Interactive Routing

Instead of routing the entire board, you can route only specific nets. This is useful when you want manual control over critical signals.

The `SelectiveRouter` supports selecting nets by:

- **Net code** — KiCad's internal net identifiers
- **Net name** — exact net name match (e.g., "CLK", "SDA")
- **Component reference** — all nets touching a component (e.g., "U1" routes all nets connected to U1)

## Selective Re-routing

If you're unhappy with how certain nets were routed, you can rip them up and re-route without touching the rest of the board.

The re-route process:

1. Saves a snapshot of the current board state
2. Removes all traces and vias on the selected nets
3. Re-routes those nets from scratch
4. If no new routes are created, automatically rolls back to the saved state

This is safe — your existing routing is preserved if the re-route fails.

## Pre-Commit DRC Validation

The `ValidatedRouter` adds automatic design rule checking before and after routing. It establishes a DRC baseline on your unrouted board, routes, then checks DRC again. Based on the commit policy, it either keeps the routing or rolls back.

Available policies:

- **Always commit** — keep routing results regardless of DRC outcome
- **Reject on error** — roll back if any DRC errors exist after routing
- **Reject on warning** — roll back if any DRC errors or warnings exist
- **Reject on new errors** — roll back only if routing introduced *new* errors (default)

The "reject on new errors" policy is recommended because it allows routing to succeed even if the board had pre-existing DRC issues (like unrouted nets that the router couldn't complete).

## Post-Route Optimization

After routing completes, the optimizer improves trace quality:

- **Pull-tight 45°** — shorten traces by finding diagonal shortcuts
- **Pull-tight 90°** — shorten Manhattan (orthogonal) traces
- **Corner smoothing** — replace acute angles with smoother bends
- **Via removal** — eliminate redundant vias where traces can stay on one layer

Optimization runs automatically after routing. It can also be configured to run in parallel across independent nets for faster completion.

## Design Rule Checking

The built-in DRC engine checks for:

- **Clearance violations** — trace-trace, trace-pad, trace-via, via-via, via-pad, pad-pad spacing
- **Hole clearance** — drill-to-copper and drill-to-drill spacing
- **Connectivity** — unconnected pad pairs and incomplete nets
- **Dangling traces** — trace endpoints not connected to any pad
- **Single-layer vias** — vias with traces on only one layer (useless)
- **Board edge** — items too close to the board outline

DRC reports can be exported as plain text or KiCad-compatible JSON format, with measurements in mm, mil, inch, or micrometers.

## Differential Pairs

The autorouter automatically detects differential pairs by naming convention. Supported patterns:

- `CLK+` / `CLK-` (plus/minus suffix)
- `USB_DP` / `USB_DN` (P/N suffix)
- `HDMI_D0_P` / `HDMI_D0_N` (underscore P/N suffix)

Detected pairs are boosted in routing priority and checked for length matching with configurable tolerance.

## Pre-Routing Analysis

Before routing, you can run a pre-flight analysis to catch issues early — missing design rules, impossible via configurations, dangling nets, overlapping components, and more.

The analyzer produces a report with errors (blocking), warnings (may cause problems), and informational items. If any errors are present, fix them before routing to avoid wasting time.

The analysis checks: board geometry, layer setup, design rules, connectivity, component placement, net classes, differential pairs, and routing feasibility. It also estimates routing complexity based on net count, layer count, and connection density.

## Composable Routing Strategies

Instead of running the autorouter with a single set of parameters, you can compose a routing strategy from named passes that execute in sequence. This gives you fine-grained control over the routing pipeline.

Available pass types: **Fanout** (escape routing for BGAs), **Main** (maze-based autorouting), **Optimize** (pull-tight and via optimization), **Spread** (redistribute traces into free space), **Straighten** (reduce corner count), **Miter** (convert 90° corners to 45°), **Clean Pad Entries** (align trace entries with pad axes), **Hug** (consolidate traces near existing routing), **DRC Cleanup** (remove violating traces).

Four built-in strategies are provided: **Quick** for fast results, **Two-Layer Default** and **Multi-Layer Default** for typical boards, and **Thorough** for maximum quality with all passes enabled. You can also build custom strategies by selecting specific passes and tuning their parameters.

## Route by Net Class

Route or re-route all nets belonging to one or more net classes at once. This is useful when you want to route all signal nets first, then power nets separately with different settings.

## Route by Area

Select a region of the board and route only the nets that have pads inside that area. This is helpful for routing one section of a dense board at a time, or re-routing a specific region after a component move.

## Post-Route DRC Cleanup

After routing completes, you can run a DRC cleanup pass that automatically removes any traces or vias that violate design rules. This ensures a clean board even when routing wasn't perfect, without having to manually identify and delete violating items.

## Differential Pair Fanout

When fanout is configured with differential pair awareness enabled, the autorouter detects differential pair nets by naming convention (_P/_N, +/-, P/N suffixes) and escape-routes them first. Both nets in a pair fan out together on the same layer and same side, maintaining the configured differential pair gap. This produces cleaner, more symmetrical routing for high-speed differential signals.

## Tips for Best Results

1. **Place components thoughtfully** — the autorouter works best when components are well-placed with clear routing channels between them.

2. **Define your board outline** — the autorouter uses the board outline for edge clearance checking and spatial partitioning.

3. **Set net classes** — assign appropriate clearances and trace widths to your net classes in KiCad before routing. The autorouter reads these from the board.

4. **Start with fewer passes** — try 5-10 passes first to see if the board routes cleanly. Increase if needed.

5. **Use selective re-routing** — if a few nets route poorly, re-route just those nets rather than starting over.

6. **Check DRC after routing** — always review the DRC report before accepting routing results.
