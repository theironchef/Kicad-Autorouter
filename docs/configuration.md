---
title: Configuration Reference
---

# Configuration Reference

The autorouter exposes a comprehensive set of tunable parameters through `RouterSettings`. These control routing behavior, optimization strategy, and resource usage.

## RouterSettings Fields

### Pass Control

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_passes` | int | 20 | Maximum routing iterations |
| `time_limit_seconds` | float | 300.0 | Total time budget in seconds |
| `min_improvement_pct` | float | 0.5 | Stop if improvement drops below this percentage per pass |

### Ripup Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `initial_ripup_cost` | float | 1.0 | Starting ripup cost multiplier |
| `ripup_cost_increment` | float | 2.0 | Multiplier increase per pass |
| `max_ripup_cost` | float | 100.0 | Cap on ripup cost |

Higher ripup costs make the router more reluctant to disturb existing routes. The cost escalates each pass so early passes explore freely while later passes settle.

### Via Costs

Via costs influence the A* pathfinding — higher costs discourage via placement.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `via_cost` | ViaCostLevel | MEDIUM | Cost for standard through-hole vias |
| `blind_via_cost` | ViaCostLevel | HIGH | Cost for blind vias |
| `buried_via_cost` | ViaCostLevel | HIGH | Cost for buried vias |
| `micro_via_cost` | ViaCostLevel | LOW | Cost for micro-vias |

**ViaCostLevel values:** `ZERO`, `LOW`, `MEDIUM`, `HIGH`, `VERY_HIGH`, `FORBIDDEN`

Setting a via cost to `FORBIDDEN` prevents the router from using that via type entirely.

### Allowed Via Types

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `allow_through_vias` | bool | True | Allow standard through-hole vias |
| `allow_blind_vias` | bool | False | Allow blind vias |
| `allow_buried_vias` | bool | False | Allow buried vias |
| `allow_micro_vias` | bool | False | Allow micro-vias |

### Routing Style

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prefer_45_degree` | bool | True | Prefer 45° routed segments |
| `allow_any_angle` | bool | False | Allow free-angle routing |
| `neckdown_at_pins` | bool | True | Narrow trace width at pad connections |
| `pull_tight_accuracy` | int | 500 | Pull-tight optimization precision (nm) |

### Strategy Selection

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `update_strategy` | UpdateStrategy | HYBRID | When to run optimization |
| `selection_strategy` | SelectionStrategy | SHORTEST_FIRST | Connection ordering |
| `hybrid_threshold` | int | 50 | Items routed before hybrid switches to global mode |

**UpdateStrategy values:** `GREEDY`, `GLOBAL`, `HYBRID`

**SelectionStrategy values:** `SEQUENTIAL`, `SHORTEST_FIRST`, `RANDOM`, `PRIORITIZED`

### Optimization

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `opt_max_passes` | int | 5 | Maximum optimization passes |
| `opt_improvement_threshold` | float | 0.1 | Stop optimizing below this improvement % |
| `opt_num_threads` | int | 1 | Thread count for parallel optimization |

### Layer Preferences

Per-layer routing direction preferences reduce conflicts and improve routing density. Set via `layer_preferences`:

```python
settings.layer_preferences[0] = LayerPreference(
    direction=LayerDirection.HORIZONTAL,
    enabled=True,
)
settings.layer_preferences[1] = LayerPreference(
    direction=LayerDirection.VERTICAL,
    enabled=True,
)
```

**LayerDirection values:** `HORIZONTAL`, `VERTICAL`, `ANY`

When routing against the preferred direction, the A* cost is multiplied by 2.0 (configurable via `get_direction_cost()`).

## Factory Presets

Two convenience methods create sensible defaults:

### `RouterSettings.for_two_layer()`

Configured for typical two-layer boards:
- Layer 0 (F.Cu): horizontal preference
- Layer 1 (B.Cu): vertical preference
- Only through-hole vias allowed
- Medium via cost

### `RouterSettings.for_four_layer()`

Configured for four-layer boards:
- Layer 0 (F.Cu): horizontal
- Layer 1 (In1.Cu): vertical
- Layer 2 (In2.Cu): horizontal
- Layer 3 (B.Cu): vertical
- Blind and buried vias allowed
- Lower via costs to encourage layer transitions

## Serialization

Settings can be saved and loaded for KiCad plugin persistence:

```python
# Save
settings_dict = settings.to_dict()
# Store settings_dict as JSON in KiCad plugin config

# Load
settings = RouterSettings.from_dict(settings_dict)
```

The serialization handles all fields including enum values and layer preferences.

## DRC Configuration

The `DrcConfig` controls which checks run:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `check_clearances` | bool | True | Run clearance violation checks |
| `check_hole_clearance` | bool | True | Run hole spacing checks |
| `check_connectivity` | bool | True | Check for unconnected nets |
| `check_dangles` | bool | True | Find dangling trace endpoints |
| `check_single_layer_vias` | bool | True | Find useless single-layer vias |
| `check_board_edge` | bool | True | Check board edge clearance |
| `deduplicate` | bool | True | Remove duplicate violations |

## Commit Policies

The `ValidatedRouter` uses these policies to decide whether to keep routing results:

| Policy | Behavior |
|--------|----------|
| `ALWAYS_COMMIT` | Keep results regardless of DRC |
| `REJECT_ON_ERROR` | Roll back if any DRC error exists |
| `REJECT_ON_WARNING` | Roll back if any error or warning exists |
| `REJECT_ON_NEW_ERRORS` | Roll back only if routing *introduced* new errors (default) |
