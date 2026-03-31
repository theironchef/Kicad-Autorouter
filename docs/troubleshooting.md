---
title: Troubleshooting
---

# Troubleshooting

Common issues and solutions when using the kicad-autorouter.

## Plugin Not Appearing in KiCad

**Symptom:** After installation, no Autorouter button appears in the toolbar.

**Solutions:**

1. **Restart KiCad** — plugins are loaded at startup; a restart is always required.

2. **Check KiCad version** — this plugin requires KiCad 10 or later. Earlier versions are not supported.

3. **Check the scripting console** — open **Tools → Scripting Console** and look for import errors. Common issues:
   - `ModuleNotFoundError` — the plugin files aren't in the right directory
   - `SyntaxError` — Python version mismatch (KiCad 10 uses Python 3.10+)

4. **Verify file placement** — ensure `kicad_autorouter/` is directly inside the plugins directory, not nested in an extra folder (e.g., don't have `plugins/kicad-autorouter/kicad_autorouter/`).

## Routing Doesn't Complete

**Symptom:** The autorouter runs but leaves many nets unrouted.

**Solutions:**

1. **Increase max passes** — the default 20 passes may not be enough for dense boards. Try 50-100.

2. **Increase time limit** — complex boards need more time. Try 600-1200 seconds.

3. **Check component placement** — if components are too tightly packed, there may not be enough routing channels. Spread components farther apart.

4. **Check design rules** — if clearances or trace widths are too large for the available space, routing will fail. Verify your net class settings are reasonable.

5. **Use two layers** — single-layer routing is significantly harder. Ensure your board has at least two copper layers.

6. **Route in stages** — use interactive routing to route critical nets first, then autoroute the rest.

## Routing is Slow

**Symptom:** The autorouter takes a very long time, especially on larger boards.

**Solutions:**

1. **Reduce max passes** — if the board routes in 5 passes, 20 more won't help much. Set `max_passes` lower.

2. **Use shortest-first selection** — this is the default and generally fastest. Avoid `RANDOM` unless you're stuck in a local minimum.

3. **Enable multi-threaded optimization** — set `opt_num_threads` to your CPU core count.

4. **Lower time limit** — a strict time limit forces the router to deliver its best result within a budget rather than exploring indefinitely.

## DRC Reports Errors After Routing

**Symptom:** Post-route DRC shows clearance violations or other errors.

**Solutions:**

1. **Use ValidatedRouter** — it automatically checks DRC and rolls back bad results. Set the policy to `REJECT_ON_NEW_ERRORS`.

2. **Check pre-existing violations** — run DRC on your unrouted board first. Some violations may exist before routing (e.g., components placed too close together).

3. **Review clearance settings** — ensure your board's design rules match what the autorouter reads. The autorouter respects KiCad's net class clearances.

4. **Selectively re-route** — if only a few nets have violations, use `SelectiveRouter.reroute_nets()` to fix just those nets.

## ValidatedRouter Always Rolls Back

**Symptom:** Every routing attempt is rolled back due to DRC failures.

**Solutions:**

1. **Switch to REJECT_ON_NEW_ERRORS** — this only rolls back if routing *added* errors. Pre-existing issues (like unrouted nets) won't trigger rollback.

2. **Switch to ALWAYS_COMMIT** — accept the results and fix violations manually afterward.

3. **Disable strict DRC checks** — if board edge clearance is causing issues, disable `check_board_edge` in `DrcConfig`.

## Traces Don't Connect to Pads

**Symptom:** Routed traces appear near pads but DRC reports them as unconnected.

**Explanation:** The autorouter uses tolerance-based connectivity checking. A trace endpoint must land within `pad_radius + trace_half_width` of the pad center. If traces are snapped to 45° angles, they may end up slightly off from the pad center.

**Solutions:**

1. This is usually cosmetic — the traces are electrically connected but the endpoints are off-center. Post-route optimization should clean this up.

2. Run the optimizer with `pull_tight=True` to snap endpoints closer to pads.

## Memory Usage is High

**Symptom:** KiCad uses a lot of memory during routing.

**Explanation:** The autorouter builds an expansion room graph and maintains spatial indices in memory. For very large boards (1000+ nets), this can use significant memory.

**Solutions:**

1. **Route in batches** — use interactive routing to route subsets of nets rather than everything at once.

2. **Reduce board complexity** — if possible, simplify the board (fewer components, wider spacing).

## How to Report Bugs

If you encounter a bug, please open an issue at [github.com/danwillis-aethl/kicad-autorouter/issues](https://github.com/danwillis-aethl/kicad-autorouter/issues) with:

1. Your KiCad version
2. Your operating system
3. A description of what happened vs. what you expected
4. If possible, a minimal .kicad_pcb file that reproduces the issue
5. Any error messages from KiCad's scripting console

## FAQ

**Q: Does this replace KiCad's built-in interactive router?**

No. KiCad's interactive router handles manual trace routing with real-time feedback. This plugin is a batch autorouter — it routes all (or selected) nets automatically without manual interaction.

**Q: Can I use this with KiCad 8 or 9?**

No. This plugin uses KiCad 10's pcbnew Python API, which has breaking changes from earlier versions. KiCad 10 is the minimum supported version.

**Q: Is this as good as Freerouting?**

This is a Python port of Freerouting's core algorithms. The routing quality should be comparable for supported features. However, some advanced Freerouting features (like full expansion room completion and maze-shove integration) are not yet implemented. See the roadmap in the README for details.

**Q: Why Python instead of Java?**

KiCad plugins must be written in Python (using the pcbnew SWIG API). A Python implementation integrates natively with KiCad without requiring an external Java runtime or Specctra file export/import.

**Q: Can I route only specific nets?**

Yes. Use the `SelectiveRouter` to route by net code, net name, or component reference. See the [User Guide](user-guide) for details.

**Q: Does it support differential pairs?**

Yes. Differential pairs are auto-detected by naming convention (P/N, +/-, _P/_N suffixes) and receive priority routing and length matching checks. See the [User Guide](user-guide#differential-pairs) for supported naming patterns.
