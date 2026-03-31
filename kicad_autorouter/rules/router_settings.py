"""
RouterSettings — Comprehensive routing configuration.

Centralises all tunable parameters that control routing behaviour:
via costs, ripup penalties, preferred/undesired layer directions,
neckdown settings, pull-tight accuracy, and allowed via types.

This replaces the scattered config dataclasses with a single,
authoritative settings object that can be serialised and edited
through the KiCad plugin dialog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class LayerDirection(Enum):
    """Preferred trace direction on a copper layer."""

    HORIZONTAL = auto()
    VERTICAL = auto()
    ANY = auto()


class ViaCostLevel(Enum):
    """Relative cost of placing different via types (for A* scoring)."""

    CHEAP = 1
    NORMAL = 5
    EXPENSIVE = 10
    VERY_EXPENSIVE = 20


class UpdateStrategy(Enum):
    """How the optimizer processes changed items after routing."""

    GREEDY = auto()     # Optimise each item immediately after routing
    GLOBAL = auto()     # Batch optimise all changed areas at end of pass
    HYBRID = auto()     # Greedy for small changes, global for large ones


class SelectionStrategy(Enum):
    """Order in which unrouted connections are attempted."""

    SEQUENTIAL = auto()      # In the order they appear
    SHORTEST_FIRST = auto()  # Shortest Manhattan distance first
    RANDOM = auto()          # Random shuffle each pass
    PRIORITIZED = auto()     # Use net priority scores


@dataclass
class LayerPreference:
    """Per-layer routing preferences."""

    layer_index: int
    preferred_direction: LayerDirection = LayerDirection.ANY
    direction_cost: float = 1.0       # Multiplier for routing against preferred dir
    is_routing_enabled: bool = True    # False = don't route on this layer


@dataclass
class RouterSettings:
    """All tunable routing parameters.

    Attributes are grouped by category. Default values are sensible for
    a typical 2-layer board.

    Usage::

        settings = RouterSettings()
        settings.via_cost = ViaCostLevel.EXPENSIVE
        settings.max_passes = 30
        autorouter = BatchAutorouter(board, rules, settings=settings)
    """

    # ----- Pass control -----
    max_passes: int = 20
    time_limit_seconds: float = 300.0
    min_improvement_pct: float = 0.5

    # ----- Via costs -----
    via_cost: ViaCostLevel = ViaCostLevel.NORMAL
    blind_via_cost: ViaCostLevel = ViaCostLevel.EXPENSIVE
    buried_via_cost: ViaCostLevel = ViaCostLevel.EXPENSIVE
    micro_via_cost: ViaCostLevel = ViaCostLevel.CHEAP

    # ----- Allowed via types -----
    allow_through_vias: bool = True
    allow_blind_vias: bool = False
    allow_buried_vias: bool = False
    allow_micro_vias: bool = False

    # ----- Ripup configuration -----
    initial_ripup_cost: float = 1.0
    ripup_cost_increment: float = 2.0
    max_ripup_cost: float = 100.0

    # ----- Layer preferences -----
    layer_preferences: list[LayerPreference] = field(default_factory=list)

    # ----- Routing style -----
    prefer_45_degree: bool = True
    allow_any_angle: bool = False
    automatic_neckdown: bool = True    # Narrow trace at destination pins
    pull_tight_accuracy: int = 10      # Max iterations for pull-tight

    # ----- Strategies -----
    update_strategy: UpdateStrategy = UpdateStrategy.HYBRID
    selection_strategy: SelectionStrategy = SelectionStrategy.SHORTEST_FIRST
    hybrid_threshold: int = 5          # Items changed before switching to global

    # ----- Optimisation -----
    optimize_after_routing: bool = True
    num_optimizer_threads: int = 0     # 0 = sequential
    remove_redundant_vias: bool = True
    relocate_vias: bool = True
    smooth_corners: bool = True

    # ----- Derived helpers -----

    def get_via_cost_value(self, via_type: str = "through") -> int:
        """Get the numeric via cost for A* scoring."""
        mapping = {
            "through": self.via_cost,
            "blind": self.blind_via_cost,
            "buried": self.buried_via_cost,
            "micro": self.micro_via_cost,
        }
        level = mapping.get(via_type, self.via_cost)
        return level.value

    def get_layer_preference(self, layer_index: int) -> LayerPreference:
        """Get the preference for a specific layer."""
        for lp in self.layer_preferences:
            if lp.layer_index == layer_index:
                return lp
        return LayerPreference(layer_index=layer_index)

    def get_direction_cost(self, layer_index: int, is_horizontal: bool) -> float:
        """Get the routing cost multiplier for a direction on a layer.

        Returns 1.0 if direction matches preference, or direction_cost
        if routing against the preferred direction.
        """
        pref = self.get_layer_preference(layer_index)
        if pref.preferred_direction == LayerDirection.ANY:
            return 1.0
        if is_horizontal and pref.preferred_direction == LayerDirection.HORIZONTAL:
            return 1.0
        if not is_horizontal and pref.preferred_direction == LayerDirection.VERTICAL:
            return 1.0
        return pref.direction_cost

    def is_layer_enabled(self, layer_index: int) -> bool:
        """Check if routing is enabled on a layer."""
        pref = self.get_layer_preference(layer_index)
        return pref.is_routing_enabled

    def to_dict(self) -> dict:
        """Serialise to a dict (for saving to KiCad plugin settings)."""
        return {
            "max_passes": self.max_passes,
            "time_limit_seconds": self.time_limit_seconds,
            "min_improvement_pct": self.min_improvement_pct,
            "via_cost": self.via_cost.name,
            "blind_via_cost": self.blind_via_cost.name,
            "buried_via_cost": self.buried_via_cost.name,
            "micro_via_cost": self.micro_via_cost.name,
            "allow_through_vias": self.allow_through_vias,
            "allow_blind_vias": self.allow_blind_vias,
            "allow_buried_vias": self.allow_buried_vias,
            "allow_micro_vias": self.allow_micro_vias,
            "initial_ripup_cost": self.initial_ripup_cost,
            "ripup_cost_increment": self.ripup_cost_increment,
            "max_ripup_cost": self.max_ripup_cost,
            "prefer_45_degree": self.prefer_45_degree,
            "allow_any_angle": self.allow_any_angle,
            "automatic_neckdown": self.automatic_neckdown,
            "pull_tight_accuracy": self.pull_tight_accuracy,
            "update_strategy": self.update_strategy.name,
            "selection_strategy": self.selection_strategy.name,
            "hybrid_threshold": self.hybrid_threshold,
            "optimize_after_routing": self.optimize_after_routing,
            "num_optimizer_threads": self.num_optimizer_threads,
            "remove_redundant_vias": self.remove_redundant_vias,
            "relocate_vias": self.relocate_vias,
            "smooth_corners": self.smooth_corners,
            "layer_preferences": [
                {
                    "layer_index": lp.layer_index,
                    "preferred_direction": lp.preferred_direction.name,
                    "direction_cost": lp.direction_cost,
                    "is_routing_enabled": lp.is_routing_enabled,
                }
                for lp in self.layer_preferences
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> RouterSettings:
        """Deserialise from a dict (for loading from KiCad plugin settings)."""
        settings = cls()

        for key in ("max_passes", "time_limit_seconds", "min_improvement_pct",
                     "initial_ripup_cost", "ripup_cost_increment", "max_ripup_cost",
                     "pull_tight_accuracy", "hybrid_threshold", "num_optimizer_threads"):
            if key in data:
                setattr(settings, key, type(getattr(settings, key))(data[key]))

        for key in ("allow_through_vias", "allow_blind_vias", "allow_buried_vias",
                     "allow_micro_vias", "prefer_45_degree", "allow_any_angle",
                     "automatic_neckdown", "optimize_after_routing",
                     "remove_redundant_vias", "relocate_vias", "smooth_corners"):
            if key in data:
                setattr(settings, key, bool(data[key]))

        # Enum fields
        if "via_cost" in data:
            settings.via_cost = ViaCostLevel[data["via_cost"]]
        if "blind_via_cost" in data:
            settings.blind_via_cost = ViaCostLevel[data["blind_via_cost"]]
        if "buried_via_cost" in data:
            settings.buried_via_cost = ViaCostLevel[data["buried_via_cost"]]
        if "micro_via_cost" in data:
            settings.micro_via_cost = ViaCostLevel[data["micro_via_cost"]]
        if "update_strategy" in data:
            settings.update_strategy = UpdateStrategy[data["update_strategy"]]
        if "selection_strategy" in data:
            settings.selection_strategy = SelectionStrategy[data["selection_strategy"]]

        # Layer preferences
        if "layer_preferences" in data:
            settings.layer_preferences = [
                LayerPreference(
                    layer_index=lp["layer_index"],
                    preferred_direction=LayerDirection[lp.get("preferred_direction", "ANY")],
                    direction_cost=lp.get("direction_cost", 1.0),
                    is_routing_enabled=lp.get("is_routing_enabled", True),
                )
                for lp in data["layer_preferences"]
            ]

        return settings

    @classmethod
    def for_two_layer(cls) -> RouterSettings:
        """Sensible defaults for a 2-layer board."""
        settings = cls()
        settings.layer_preferences = [
            LayerPreference(0, LayerDirection.HORIZONTAL),
            LayerPreference(1, LayerDirection.VERTICAL),
        ]
        return settings

    @classmethod
    def for_four_layer(cls) -> RouterSettings:
        """Sensible defaults for a 4-layer board."""
        settings = cls()
        settings.allow_blind_vias = True
        settings.layer_preferences = [
            LayerPreference(0, LayerDirection.HORIZONTAL),     # F.Cu - signals H
            LayerPreference(1, LayerDirection.VERTICAL),       # In1.Cu - signals V
            LayerPreference(2, LayerDirection.ANY, is_routing_enabled=False),  # In2.Cu - power
            LayerPreference(3, LayerDirection.HORIZONTAL),     # B.Cu - signals H
        ]
        return settings
