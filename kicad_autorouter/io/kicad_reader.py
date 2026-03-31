"""
KiCadBoardReader — Read board data from KiCad 10's pcbnew Python API.

Converts KiCad's internal BOARD object into our RoutingBoard representation.
All KiCad coordinates are in nanometers (internal units).

Targets KiCad 10 only — no backwards-compat fallbacks.
"""

from __future__ import annotations

import logging

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.board.trace import Trace
from kicad_autorouter.board.via import Via
from kicad_autorouter.board.component import Component
from kicad_autorouter.board.obstacle import ObstacleArea, ObstacleType
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.rules.clearance import ClearanceMatrix
from kicad_autorouter.rules.design_rules import DesignRules

logger = logging.getLogger(__name__)

import pcbnew


class KiCadBoardReader:
    """Read a KiCad board into our internal representation.

    Can read from:
    1. Live KiCad editor (pcbnew.GetBoard())
    2. A KiCad board file (.kicad_pcb)
    """

    def __init__(self):
        self._pcb_board = None
        self._layer_map: dict[int, int] = {}       # KiCad layer ID -> our layer index
        self._layer_map_rev: dict[int, int] = {}    # our layer index -> KiCad layer ID
        self._next_item_id = 1

    @property
    def layer_map_reverse(self) -> dict[int, int]:
        """Our layer index -> KiCad layer ID. Used by the writer."""
        return dict(self._layer_map_rev)

    def read_from_editor(self) -> RoutingBoard:
        """Read the currently open board from KiCad's editor."""
        self._pcb_board = pcbnew.GetBoard()
        return self._convert_board()

    def read_from_file(self, filepath: str) -> RoutingBoard:
        """Read a board from a .kicad_pcb file."""
        self._pcb_board = pcbnew.LoadBoard(filepath)
        return self._convert_board()

    # ------------------------------------------------------------------
    # Internal conversion
    # ------------------------------------------------------------------

    def _convert_board(self) -> RoutingBoard:
        """Convert pcbnew BOARD to our RoutingBoard."""
        board = RoutingBoard()

        # Layer structure
        board.layer_structure = self._read_layers()

        # Bounding box
        board.bounding_box = self._read_bounding_box()

        # Nets and net classes
        board.nets = self._read_nets()
        board.net_classes, board.default_net_class = self._read_net_classes()

        # Design rules (stored on the board for easy access)
        board.design_rules = self._read_design_rules()

        # Components, pads
        self._read_footprints(board)

        # Existing routing
        self._read_traces(board)
        self._read_vias(board)

        # Zones & keepouts
        self._read_zones(board)

        # Board outline as an obstacle
        self._read_board_outline(board)

        logger.info(
            "Read KiCad board: %d items, %d nets, %d copper layers",
            board.item_count, len(board.nets),
            board.layer_structure.copper_layer_count,
        )
        return board

    # ------------------------------------------------------------------
    # Layers
    # ------------------------------------------------------------------

    def _read_layers(self) -> LayerStructure:
        """Read copper layer structure using KiCad 10 API."""
        layers = []
        copper_ids = []

        # Use GetEnabledLayers or iterate the known copper range
        for layer_id in range(pcbnew.B_Cu + 1):  # 0 (F.Cu) through 31 (B.Cu)
            if self._pcb_board.IsLayerEnabled(layer_id):
                name = self._pcb_board.GetLayerName(layer_id)
                if name and ".Cu" in name:
                    copper_ids.append(layer_id)

        if not copper_ids:
            copper_ids = [pcbnew.F_Cu, pcbnew.B_Cu]

        for idx, kicad_id in enumerate(sorted(copper_ids)):
            name = self._pcb_board.GetLayerName(kicad_id)
            self._layer_map[kicad_id] = idx
            self._layer_map_rev[idx] = kicad_id

            # Guess preferred routing direction from layer position
            layer_type = LayerType.SIGNAL
            layers.append(Layer(index=idx, name=name, layer_type=layer_type))

        return LayerStructure(layers)

    # ------------------------------------------------------------------
    # Bounding box
    # ------------------------------------------------------------------

    def _read_bounding_box(self) -> BoundingBox:
        """Read board bounding box from edge cuts."""
        bbox = self._pcb_board.GetBoardEdgesBoundingBox()
        if bbox.GetWidth() > 0 and bbox.GetHeight() > 0:
            return BoundingBox(
                x_min=bbox.GetX(),
                y_min=bbox.GetY(),
                x_max=bbox.GetX() + bbox.GetWidth(),
                y_max=bbox.GetY() + bbox.GetHeight(),
            )
        # Fallback to item bounding box
        bbox = self._pcb_board.GetBoundingBox()
        return BoundingBox(
            x_min=bbox.GetX(),
            y_min=bbox.GetY(),
            x_max=bbox.GetX() + bbox.GetWidth(),
            y_max=bbox.GetY() + bbox.GetHeight(),
        )

    # ------------------------------------------------------------------
    # Nets
    # ------------------------------------------------------------------

    def _read_nets(self) -> dict[int, Net]:
        """Read all nets from the board."""
        nets: dict[int, Net] = {}
        netinfo = self._pcb_board.GetNetInfo()
        for net_code, net_item in netinfo.NetsByNetcode().items():
            nets[net_code] = Net(
                net_code=net_code,
                name=net_item.GetNetname(),
            )
        return nets

    # ------------------------------------------------------------------
    # Net classes
    # ------------------------------------------------------------------

    def _read_net_classes(self) -> tuple[dict[str, NetClass], NetClass]:
        """Read net classes and their rules from KiCad 10 design settings."""
        net_classes: dict[str, NetClass] = {}
        settings = self._pcb_board.GetDesignSettings()

        # Default net class
        default_nc = settings.GetDefault()
        default = NetClass(
            name="Default",
            clearance=default_nc.GetClearance(),
            track_width=default_nc.GetTrackWidth(),
            via_diameter=default_nc.GetViaDiameter(),
            via_drill=default_nc.GetViaDrill(),
        )
        net_classes["Default"] = default

        # Additional net classes
        try:
            for nc_name, nc in self._pcb_board.GetNetClasses().items():
                net_classes[nc_name] = NetClass(
                    name=nc_name,
                    clearance=nc.GetClearance(),
                    track_width=nc.GetTrackWidth(),
                    via_diameter=nc.GetViaDiameter(),
                    via_drill=nc.GetViaDrill(),
                )
        except Exception:
            logger.debug("Could not read additional net classes, using Default only")

        return net_classes, default

    # ------------------------------------------------------------------
    # Design rules
    # ------------------------------------------------------------------

    def _read_design_rules(self) -> DesignRules:
        """Read design rules from KiCad 10 board settings."""
        rules = DesignRules()
        settings = self._pcb_board.GetDesignSettings()

        default_nc = settings.GetDefault()
        rules.min_trace_width = default_nc.GetTrackWidth()
        rules.min_clearance = default_nc.GetClearance()

        # Via constraints
        rules.min_via_diameter = default_nc.GetViaDiameter()
        rules.min_via_drill = default_nc.GetViaDrill()

        return rules

    # ------------------------------------------------------------------
    # Footprints & pads
    # ------------------------------------------------------------------

    def _read_footprints(self, board: RoutingBoard):
        """Read all footprints and their pads."""
        # Map KiCad pad shape enums to ours
        _shape_map = {
            pcbnew.PAD_SHAPE_CIRCLE: PadShape.CIRCLE,
            pcbnew.PAD_SHAPE_RECT: PadShape.RECTANGLE,
            pcbnew.PAD_SHAPE_OVAL: PadShape.OVAL,
            pcbnew.PAD_SHAPE_TRAPEZOID: PadShape.TRAPEZOID,
            pcbnew.PAD_SHAPE_ROUNDRECT: PadShape.ROUNDRECT,
        }
        # PAD_SHAPE_CHAMFERED_RECT and PAD_SHAPE_CUSTOM may also exist
        try:
            _shape_map[pcbnew.PAD_SHAPE_CHAMFERED_RECT] = PadShape.ROUNDRECT
        except AttributeError:
            pass
        try:
            _shape_map[pcbnew.PAD_SHAPE_CUSTOM] = PadShape.CUSTOM
        except AttributeError:
            pass

        for fp in self._pcb_board.GetFootprints():
            comp_id = self._next_id()
            pos = fp.GetPosition()

            component = Component(
                id=comp_id,
                reference=fp.GetReference(),
                value=fp.GetValue(),
                footprint=str(fp.GetFPID().GetLibItemName()),
                position=IntPoint(pos.x, pos.y),
                rotation_deg=fp.GetOrientationDegrees(),
                is_on_front=not fp.IsFlipped(),
                is_locked=fp.IsLocked(),
            )

            for kicad_pad in fp.Pads():
                pad_pos = kicad_pad.GetPosition()
                pad_size = kicad_pad.GetSize()
                pad_id = self._next_id()

                pad_shape = _shape_map.get(kicad_pad.GetShape(), PadShape.CIRCLE)

                # Determine which copper layers this pad is on
                layer_indices = []
                pad_layers = kicad_pad.GetLayerSet()
                for kicad_layer_id, our_idx in self._layer_map.items():
                    if pad_layers.Contains(kicad_layer_id):
                        layer_indices.append(our_idx)

                pad = Pad(
                    id=pad_id,
                    net_codes=[kicad_pad.GetNetCode()],
                    layer_indices=layer_indices,
                    fixed_state=FixedState.USER_FIXED,
                    component_id=comp_id,
                    position=IntPoint(pad_pos.x, pad_pos.y),
                    size_x=pad_size.x,
                    size_y=pad_size.y,
                    drill_diameter=kicad_pad.GetDrillSize().x,
                    pad_shape=pad_shape,
                    rotation_deg=kicad_pad.GetOrientationDegrees(),
                    pad_name=kicad_pad.GetName(),
                    component_ref=fp.GetReference(),
                )
                board.add_item(pad)
                component.pad_ids.append(pad_id)

            board.components[comp_id] = component

    # ------------------------------------------------------------------
    # Traces
    # ------------------------------------------------------------------

    def _read_traces(self, board: RoutingBoard):
        """Read existing traces from the board."""
        for track in self._pcb_board.GetTracks():
            if isinstance(track, pcbnew.PCB_VIA):
                continue  # vias handled separately

            kicad_layer = track.GetLayer()
            if kicad_layer not in self._layer_map:
                continue

            start = track.GetStart()
            end = track.GetEnd()

            trace = Trace(
                id=self._next_id(),
                net_codes=[track.GetNetCode()],
                layer_indices=[self._layer_map[kicad_layer]],
                corners=[IntPoint(start.x, start.y), IntPoint(end.x, end.y)],
                width=track.GetWidth(),
                layer_index=self._layer_map[kicad_layer],
                fixed_state=FixedState.USER_FIXED,
            )
            board.add_item(trace)

    # ------------------------------------------------------------------
    # Vias
    # ------------------------------------------------------------------

    def _read_vias(self, board: RoutingBoard):
        """Read existing vias from the board."""
        for track in self._pcb_board.GetTracks():
            if not isinstance(track, pcbnew.PCB_VIA):
                continue

            pos = track.GetPosition()
            top_layer = track.TopLayer()
            bot_layer = track.BottomLayer()

            start_idx = self._layer_map.get(top_layer, 0)
            end_idx = self._layer_map.get(bot_layer, max(self._layer_map.values(), default=1))

            via = Via(
                id=self._next_id(),
                net_codes=[track.GetNetCode()],
                position=IntPoint(pos.x, pos.y),
                diameter=track.GetWidth(),
                drill=track.GetDrillValue(),
                start_layer=start_idx,
                end_layer=end_idx,
                fixed_state=FixedState.USER_FIXED,
            )
            board.add_item(via)

    # ------------------------------------------------------------------
    # Zones / keepouts
    # ------------------------------------------------------------------

    def _read_zones(self, board: RoutingBoard):
        """Read keepout zones and copper fills."""
        for zone in self._pcb_board.Zones():
            if zone.GetIsRuleArea():
                obstacle_type = ObstacleType.KEEPOUT
            else:
                obstacle_type = ObstacleType.COPPER_FILL

            outline = zone.Outline()
            if outline is None or outline.OutlineCount() == 0:
                continue

            vertices = []
            contour = outline.Outline(0)
            for i in range(contour.PointCount()):
                pt = contour.GetPoint(i)
                vertices.append(IntPoint(pt.x, pt.y))

            if len(vertices) < 3:
                continue

            layer_indices = []
            for kicad_layer_id, our_idx in self._layer_map.items():
                if zone.IsOnLayer(kicad_layer_id):
                    layer_indices.append(our_idx)

            obstacle = ObstacleArea(
                id=self._next_id(),
                net_codes=[zone.GetNetCode()],
                layer_indices=layer_indices,
                fixed_state=FixedState.USER_FIXED,
                vertices=vertices,
                obstacle_type=obstacle_type,
            )
            board.add_item(obstacle)

    # ------------------------------------------------------------------
    # Board outline (Edge.Cuts) as obstacle
    # ------------------------------------------------------------------

    def _read_board_outline(self, board: RoutingBoard):
        """Read Edge.Cuts drawings and create a board outline obstacle.

        The outline constrains routing to stay within the board boundary.
        """
        edge_cuts_layer = pcbnew.Edge_Cuts
        vertices = []

        for drawing in self._pcb_board.GetDrawings():
            if drawing.GetLayer() != edge_cuts_layer:
                continue

            # Handle line segments on Edge.Cuts
            if drawing.GetClass() == "PCB_SHAPE":
                shape = drawing
                shape_type = shape.GetShape()

                # Lines
                if shape_type == pcbnew.SHAPE_T_SEGMENT:
                    start = shape.GetStart()
                    end = shape.GetEnd()
                    vertices.append(IntPoint(start.x, start.y))
                    vertices.append(IntPoint(end.x, end.y))

                # Rectangles
                elif shape_type == pcbnew.SHAPE_T_RECT:
                    corners = shape.GetRectCorners()
                    for c in corners:
                        vertices.append(IntPoint(c.x, c.y))

                # Polygons
                elif shape_type == pcbnew.SHAPE_T_POLY:
                    poly = shape.GetPolyShape()
                    if poly and poly.OutlineCount() > 0:
                        outline = poly.Outline(0)
                        for i in range(outline.PointCount()):
                            pt = outline.GetPoint(i)
                            vertices.append(IntPoint(pt.x, pt.y))

        if len(vertices) >= 3:
            # Create an obstacle on all copper layers representing the outline
            all_layers = list(range(board.layer_structure.copper_layer_count))
            obstacle = ObstacleArea(
                id=self._next_id(),
                net_codes=[],
                layer_indices=all_layers,
                fixed_state=FixedState.SYSTEM_FIXED,
                vertices=vertices,
                obstacle_type=ObstacleType.BOARD_OUTLINE,
            )
            board.add_item(obstacle)
            logger.info("Read board outline with %d vertices", len(vertices))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        id_val = self._next_item_id
        self._next_item_id += 1
        return id_val
