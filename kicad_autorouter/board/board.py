"""
RoutingBoard - Central PCB board representation.

This is the primary data structure for the autorouter. It holds all board
items (pads, traces, vias, obstacles), net definitions, layer structure,
and design rules. The autorouter reads from and writes to this board.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterator

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.component import Component
from kicad_autorouter.board.item import Item, FixedState
from kicad_autorouter.board.layer import LayerStructure
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.pad import Pad
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.via import Via
from kicad_autorouter.board.obstacle import ObstacleArea
from kicad_autorouter.utils.id_generator import IdGenerator

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from kicad_autorouter.rules.design_rules import DesignRules

logger = logging.getLogger(__name__)


@dataclass
class RoutingBoard:
    """Central PCB board data structure.

    Holds all physical items, net definitions, layer structure, and
    design rules. Provides methods for querying and modifying the board
    state during autorouting.
    """

    # Board geometry
    bounding_box: BoundingBox = BoundingBox(0, 0, 0, 0)
    layer_structure: LayerStructure = field(default_factory=lambda: LayerStructure.create_default())

    # Items
    _items: dict[int, Item] = field(default_factory=dict)
    _id_gen: IdGenerator = field(default_factory=IdGenerator)

    # Nets and rules
    nets: dict[int, Net] = field(default_factory=dict)
    net_classes: dict[str, NetClass] = field(default_factory=dict)
    default_net_class: NetClass = field(default_factory=lambda: NetClass("Default"))

    # Components
    components: dict[int, Component] = field(default_factory=dict)

    # Design rules (set by reader; lazy import avoids circular dependency)
    design_rules: DesignRules = field(default=None)  # type: ignore[assignment]

    def __post_init__(self):
        if self.design_rules is None:
            from kicad_autorouter.rules.design_rules import DesignRules
            self.design_rules = DesignRules()

    # Routing state
    _routed_count: int = 0
    _total_connections: int = 0

    # ----- Item management -----

    def add_item(self, item: Item) -> int:
        """Add an item to the board. Returns the item's ID."""
        if item.id <= 0:
            item.id = self._id_gen.next_id()
        self._items[item.id] = item
        return item.id

    def remove_item(self, item_id: int) -> Item | None:
        """Remove an item by ID. Returns the removed item or None."""
        return self._items.pop(item_id, None)

    def get_item(self, item_id: int) -> Item | None:
        return self._items.get(item_id)

    @property
    def item_count(self) -> int:
        return len(self._items)

    def all_items(self) -> Iterator[Item]:
        return iter(self._items.values())

    # ----- Typed item access -----

    def get_pads(self) -> list[Pad]:
        return [i for i in self._items.values() if isinstance(i, Pad)]

    def get_traces(self) -> list[Trace]:
        return [i for i in self._items.values() if isinstance(i, Trace)]

    def get_vias(self) -> list[Via]:
        return [i for i in self._items.values() if isinstance(i, Via)]

    def get_obstacles(self) -> list[ObstacleArea]:
        return [i for i in self._items.values() if isinstance(i, ObstacleArea)]

    # ----- Net queries -----

    def get_net(self, net_code: int) -> Net | None:
        return self.nets.get(net_code)

    def get_net_by_name(self, name: str) -> Net | None:
        for net in self.nets.values():
            if net.name == name:
                return net
        return None

    def get_net_class_for_net(self, net_code: int) -> NetClass:
        """Get the NetClass for a given net."""
        net = self.nets.get(net_code)
        if net:
            nc = self.net_classes.get(net.net_class_name)
            if nc:
                return nc
        return self.default_net_class

    def get_items_on_net(self, net_code: int) -> list[Item]:
        """Get all items belonging to a net."""
        return [i for i in self._items.values() if net_code in i.net_codes]

    def get_pads_on_net(self, net_code: int) -> list[Pad]:
        """Get all pads belonging to a net."""
        return [i for i in self._items.values()
                if isinstance(i, Pad) and net_code in i.net_codes]

    def get_traces_on_net(self, net_code: int) -> list[Trace]:
        return [i for i in self._items.values()
                if isinstance(i, Trace) and net_code in i.net_codes]

    def get_vias_on_net(self, net_code: int) -> list[Via]:
        return [i for i in self._items.values()
                if isinstance(i, Via) and net_code in i.net_codes]

    # ----- Layer queries -----

    def get_items_on_layer(self, layer_index: int) -> list[Item]:
        return [i for i in self._items.values() if i.is_on_layer(layer_index)]

    # ----- Spatial queries -----

    def get_items_in_bbox(self, bbox: BoundingBox) -> list[Item]:
        """Get items whose bounding box overlaps the given bbox.

        Note: This is a brute-force scan. For performance, use the
        SearchTree from datastructures/ instead.
        """
        result = []
        for item in self._items.values():
            if item.bounding_box().intersects(bbox):
                result.append(item)
        return result

    def get_items_near_point(self, point: IntPoint, radius: int) -> list[Item]:
        """Get items near a point within a radius."""
        bbox = BoundingBox.from_center_and_radius(point, radius)
        return self.get_items_in_bbox(bbox)

    # ----- Unrouted connections -----

    def get_unrouted_nets(self) -> list[int]:
        """Get net codes that have pads but no complete routing."""
        unrouted = []
        for net_code, net in self.nets.items():
            if net_code <= 0:
                continue
            pads = self.get_pads_on_net(net_code)
            if len(pads) < 2:
                continue
            traces = self.get_traces_on_net(net_code)
            if not traces:
                unrouted.append(net_code)
        return unrouted

    def get_unconnected_pad_pairs(self, net_code: int) -> list[tuple[Pad, Pad]]:
        """Find pairs of pads on a net that are not yet connected by traces.

        Uses a simple connectivity analysis: starts from each pad and
        follows traces/vias to find connected subgroups. Returns pairs
        of pads from different subgroups.
        """
        pads = self.get_pads_on_net(net_code)
        if len(pads) < 2:
            return []

        # Build connectivity groups using union-find
        parent: dict[int, int] = {p.id: p.id for p in pads}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Connect pads that share trace/via connectivity
        traces = self.get_traces_on_net(net_code)
        vias = self.get_vias_on_net(net_code)

        # Check if trace endpoints land within pad radius + trace half-width
        for trace in traces:
            connected_pads = []
            if trace.first_corner is None or trace.last_corner is None:
                continue
            hw = trace.width // 2
            for pad in pads:
                tolerance = (max(pad.size_x, pad.size_y) // 2 + hw) ** 2
                for corner in [trace.first_corner, trace.last_corner]:
                    if pad.position.distance_squared(corner) <= tolerance:
                        connected_pads.append(pad)
                        break
            for i in range(1, len(connected_pads)):
                union(connected_pads[0].id, connected_pads[i].id)

        # Find disconnected groups
        groups: dict[int, list[Pad]] = {}
        for pad in pads:
            root = find(pad.id)
            groups.setdefault(root, []).append(pad)

        if len(groups) <= 1:
            return []  # Fully connected

        # Return pairs between groups (MST-style: connect nearest pads between groups)
        group_list = list(groups.values())
        pairs = []
        for i in range(len(group_list) - 1):
            best_pair = None
            best_dist = float('inf')
            for p1 in group_list[i]:
                for p2 in group_list[i + 1]:
                    d = p1.position.distance_squared(p2.position)
                    if d < best_dist:
                        best_dist = d
                        best_pair = (p1, p2)
            if best_pair:
                pairs.append(best_pair)

        return pairs

    # ----- Trace operations -----

    def find_tails(self) -> list[tuple[Trace, list[IntPoint]]]:
        """Find trace endpoints that don't connect to pads, vias, or other traces.

        Returns list of (trace, [uncontacted_endpoints]).
        """
        # Build list of all contact points: pad centers + via centers + trace endpoints
        contact_points: list[IntPoint] = []
        for item in self._items.values():
            if isinstance(item, Pad):
                contact_points.append(item.position)
            elif isinstance(item, Via):
                contact_points.append(item.position)

        results = []
        traces = self.get_traces()

        # Also add all trace endpoints as potential contacts
        trace_endpoints: list[IntPoint] = []
        for t in traces:
            if t.first_corner:
                trace_endpoints.append(t.first_corner)
            if t.last_corner:
                trace_endpoints.append(t.last_corner)

        all_contacts = contact_points + trace_endpoints

        for trace in traces:
            if trace.is_fixed:
                continue
            # Exclude this trace's own endpoints from the contact list
            own = set()
            if trace.first_corner:
                own.add((trace.first_corner.x, trace.first_corner.y))
            if trace.last_corner:
                own.add((trace.last_corner.x, trace.last_corner.y))

            # Filter: contacts minus this trace's own endpoints
            filtered = [p for p in all_contacts
                        if (p.x, p.y) not in own or p in contact_points]

            tails = trace.get_uncontacted_endpoints(
                filtered,
                tolerance=max(trace.half_width + 1000, 50_000),
            )
            if tails:
                results.append((trace, tails))

        return results

    def combine_traces(self) -> int:
        """Merge adjacent same-net traces where possible. Returns count merged."""
        merged_count = 0
        changed = True
        while changed:
            changed = False
            traces = self.get_traces()
            for i, t1 in enumerate(traces):
                if t1.is_fixed or t1.id not in self._items:
                    continue
                for t2 in traces[i + 1:]:
                    if t2.is_fixed or t2.id not in self._items:
                        continue
                    combined = t1.combine_with(t2)
                    if combined is not None:
                        self._items[t1.id] = combined
                        del self._items[t2.id]
                        merged_count += 1
                        changed = True
                        break
                if changed:
                    break
        return merged_count

    def remove_tails(self) -> int:
        """Remove dangling trace tails (trace endpoints not connected to anything).

        For traces with one uncontacted endpoint, trims from that end.
        For traces with both endpoints uncontacted, removes the entire trace.
        Returns count of traces removed or trimmed.
        """
        tails = self.find_tails()
        removed_count = 0
        for trace, tail_points in tails:
            if trace.id not in self._items:
                continue
            if len(tail_points) >= 2:
                # Both endpoints dangling — remove entire trace
                del self._items[trace.id]
                removed_count += 1
            elif len(tail_points) == 1 and trace.segment_count >= 2:
                # One endpoint dangling — trim from that end
                tp = tail_points[0]
                if trace.first_corner and tp.distance_squared(trace.first_corner) < 1000 ** 2:
                    trace.corners = trace.corners[1:]
                elif trace.last_corner and tp.distance_squared(trace.last_corner) < 1000 ** 2:
                    trace.corners = trace.corners[:-1]
                removed_count += 1
        return removed_count

    # ----- Board modification -----

    def add_trace(self, corners: list[IntPoint], width: int,
                  layer_index: int, net_code: int) -> Trace:
        """Create and add a new trace to the board."""
        trace = Trace(
            id=self._id_gen.next_id(),
            net_codes=[net_code],
            layer_indices=[layer_index],
            corners=corners,
            width=width,
            layer_index=layer_index,
        )
        self._items[trace.id] = trace
        return trace

    def add_via(self, position: IntPoint, diameter: int, drill: int,
                start_layer: int, end_layer: int, net_code: int) -> Via:
        """Create and add a new via to the board."""
        via = Via(
            id=self._id_gen.next_id(),
            net_codes=[net_code],
            position=position,
            diameter=diameter,
            drill=drill,
            start_layer=start_layer,
            end_layer=end_layer,
        )
        self._items[via.id] = via
        return via

    def remove_traces_on_net(self, net_code: int):
        """Remove all traces on a given net (for rip-up)."""
        to_remove = [item_id for item_id, item in self._items.items()
                     if isinstance(item, Trace) and net_code in item.net_codes
                     and not item.is_fixed]
        for item_id in to_remove:
            del self._items[item_id]

    def remove_vias_on_net(self, net_code: int):
        """Remove all vias on a given net (for rip-up)."""
        to_remove = [item_id for item_id, item in self._items.items()
                     if isinstance(item, Via) and net_code in item.net_codes
                     and not item.is_fixed]
        for item_id in to_remove:
            del self._items[item_id]

    # ----- Scoring -----

    def compute_score(self) -> BoardScore:
        """Compute a routing quality score for the current board state."""
        traces = self.get_traces()
        vias = self.get_vias()

        total_trace_length = sum(t.total_length() for t in traces)
        total_vias = len(vias)

        unrouted = self.get_unrouted_nets()

        return BoardScore(
            unrouted_count=len(unrouted),
            total_trace_length=total_trace_length,
            via_count=total_vias,
            trace_count=len(traces),
        )

    def __repr__(self) -> str:
        return (
            f"RoutingBoard(items={self.item_count}, "
            f"layers={self.layer_structure.copper_layer_count}, "
            f"nets={len(self.nets)})"
        )


@dataclass(frozen=True)
class BoardScore:
    """Quantitative measure of routing quality.

    Used by the batch autorouter to track improvement across passes.
    Lower scores are better.
    """

    unrouted_count: int
    total_trace_length: float
    via_count: int
    trace_count: int

    def is_better_than(self, other: BoardScore) -> bool:
        """Compare scores: fewer unrouted first, then fewer vias, then shorter traces."""
        if self.unrouted_count != other.unrouted_count:
            return self.unrouted_count < other.unrouted_count
        if self.via_count != other.via_count:
            return self.via_count < other.via_count
        return self.total_trace_length < other.total_trace_length

    def __str__(self) -> str:
        return (
            f"Score(unrouted={self.unrouted_count}, "
            f"vias={self.via_count}, "
            f"trace_len={self.total_trace_length:.0f}nm, "
            f"traces={self.trace_count})"
        )
