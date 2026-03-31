"""
Lightweight S-expression parser for KiCad .kicad_pcb files.

This parser reads KiCad board files without requiring the pcbnew module.
It's designed for CI benchmarking and local testing of the autorouter.

The parser extracts:
- Board layers and layer structure
- Electrical nets
- Footprints and pads (with absolute positioning)
- Board outline and bounding box
- Design rule defaults

No existing traces/vias are loaded — we parse unrouted boards only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kicad_autorouter.geometry.point import IntPoint
from kicad_autorouter.geometry.shape import BoundingBox
from kicad_autorouter.board.board import RoutingBoard
from kicad_autorouter.board.layer import Layer, LayerStructure, LayerType
from kicad_autorouter.board.net import Net, NetClass
from kicad_autorouter.board.pad import Pad, PadShape
from kicad_autorouter.board.component import Component
from kicad_autorouter.board.item import FixedState
from kicad_autorouter.rules.design_rules import DesignRules

logger = logging.getLogger(__name__)


class SexpParser:
    """Simple S-expression lexer and parser."""

    def __init__(self, text: str):
        """Initialize parser with S-expression text."""
        self.text = text
        self.pos = 0

    def parse(self) -> Any:
        """Parse the entire text as a single S-expression."""
        self.skip_whitespace()
        result = self._parse_value()
        self.skip_whitespace()
        if self.pos < len(self.text):
            raise ValueError(f"Unexpected characters at position {self.pos}")
        return result

    def _parse_value(self) -> Any:
        """Parse a single value: list, atom, or quoted string."""
        self.skip_whitespace()
        if self.pos >= len(self.text):
            raise ValueError("Unexpected end of input")

        c = self.text[self.pos]

        if c == "(":
            return self._parse_list()
        elif c == '"':
            return self._parse_quoted_string()
        else:
            return self._parse_atom()

    def _parse_list(self) -> list[Any]:
        """Parse a parenthesized list."""
        if self.text[self.pos] != "(":
            raise ValueError(f"Expected '(' at position {self.pos}")
        self.pos += 1
        self.skip_whitespace()

        result = []
        while self.pos < len(self.text) and self.text[self.pos] != ")":
            result.append(self._parse_value())
            self.skip_whitespace()

        if self.pos >= len(self.text):
            raise ValueError("Unclosed parenthesis")
        self.pos += 1  # Skip ')'
        return result

    def _parse_quoted_string(self) -> str:
        """Parse a double-quoted string."""
        if self.text[self.pos] != '"':
            raise ValueError(f"Expected '\"' at position {self.pos}")
        self.pos += 1

        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] != '"':
            if self.text[self.pos] == "\\":
                self.pos += 2  # Skip escaped character
            else:
                self.pos += 1

        if self.pos >= len(self.text):
            raise ValueError("Unclosed quoted string")

        result = self.text[start : self.pos]
        self.pos += 1  # Skip closing '"'
        return result

    def _parse_atom(self) -> str | int | float:
        """Parse an unquoted atom (identifier, number)."""
        start = self.pos
        while (
            self.pos < len(self.text)
            and self.text[self.pos] not in " \t\n\r()"
        ):
            self.pos += 1

        atom = self.text[start : self.pos]
        if not atom:
            raise ValueError(f"Empty atom at position {self.pos}")

        # Try to parse as number
        try:
            if "." in atom:
                return float(atom)
            return int(atom)
        except ValueError:
            return atom

    def skip_whitespace(self):
        """Skip whitespace characters."""
        while self.pos < len(self.text) and self.text[self.pos] in " \t\n\r":
            self.pos += 1


class SexpBoardParser:
    """Parse .kicad_pcb files and construct RoutingBoard objects."""

    # KiCad layer IDs to internal indices mapping
    KICAD_LAYER_MAP = {
        0: ("F.Cu", LayerType.SIGNAL),
        31: ("B.Cu", LayerType.SIGNAL),
        # Add more as needed for internal layers
    }

    def __init__(self):
        """Initialize the parser."""
        self.board = RoutingBoard()
        self.layer_id_to_index: dict[int, int] = {}
        self.net_codes_seen: set[int] = set()

    def read(self, filepath: str) -> RoutingBoard:
        """Read a .kicad_pcb file and return a RoutingBoard (unrouted).

        Args:
            filepath: Path to the .kicad_pcb file

        Returns:
            A RoutingBoard with layers, nets, pads, and geometry populated

        Raises:
            IOError: If file cannot be read
            ValueError: If S-expression parsing fails
        """
        path = Path(filepath)
        if not path.exists():
            raise IOError(f"File not found: {filepath}")

        logger.info(f"Reading KiCad board file: {filepath}")
        text = path.read_text(encoding="utf-8")

        parser = SexpParser(text)
        root = parser.parse()

        if not isinstance(root, list) or len(root) == 0:
            raise ValueError("Invalid .kicad_pcb: root is not a non-empty list")

        if root[0] != "kicad_pcb":
            raise ValueError(f"Invalid .kicad_pcb: expected root 'kicad_pcb', got '{root[0]}'")

        # Extract major sections
        self._extract_layers(root)
        self._extract_nets(root)
        self._extract_footprints(root)
        self._extract_board_outline(root)

        # Set design rules to sensible defaults
        self.board.design_rules = DesignRules(
            min_trace_width=150_000,
            min_clearance=150_000,
            min_via_diameter=500_000,
            min_via_drill=200_000,
        )

        # Ensure a default net class exists
        if "Default" not in self.board.net_classes:
            default_class = NetClass("Default")
            self.board.net_classes["Default"] = default_class
            self.board.default_net_class = default_class

        logger.info(
            f"Loaded board: {self.board.item_count} items, "
            f"{len(self.board.nets)} nets, "
            f"{self.board.layer_structure.copper_layer_count} layers"
        )

        return self.board

    def _extract_layers(self, root: list) -> None:
        """Extract layer definitions from (layers ...) section."""
        layers_section = self._find_section(root, "layers")
        if not layers_section:
            logger.warning("No layers section found; using default 2-layer structure")
            self.board.layer_structure = LayerStructure.create_default(2)
            self._update_layer_map()
            return

        layers = []
        layer_index = 0

        for item in layers_section[1:]:
            if not isinstance(item, list) or len(item) < 2:
                continue

            kicad_layer_id = item[0]
            layer_name = item[1]

            # Map KiCad layer ID to our internal index
            self.layer_id_to_index[kicad_layer_id] = layer_index

            # Determine layer type
            layer_type = LayerType.SIGNAL
            if "Cu" in str(layer_name):
                layer_type = LayerType.SIGNAL
            elif "Mask" in str(layer_name) or "SilkS" in str(layer_name):
                layer_type = LayerType.SIGNAL
            elif "Edge" in str(layer_name):
                layer_type = LayerType.SIGNAL

            layer = Layer(
                index=layer_index,
                name=str(layer_name),
                layer_type=layer_type,
                is_active=True,
            )
            layers.append(layer)
            layer_index += 1

        if layers:
            self.board.layer_structure = LayerStructure(layers)
        else:
            logger.warning("No copper layers found; using default structure")
            self.board.layer_structure = LayerStructure.create_default(2)

        self._update_layer_map()

    def _update_layer_map(self) -> None:
        """Update the layer ID mapping based on board's actual layer structure."""
        # Common KiCad copper layer mappings
        for idx, layer in enumerate(self.board.layer_structure.layers):
            if layer.name == "F.Cu":
                self.layer_id_to_index[0] = idx
            elif layer.name == "B.Cu":
                self.layer_id_to_index[31] = idx

    def _extract_nets(self, root: list) -> None:
        """Extract electrical net definitions from (net N "name") entries."""
        net_index = 0
        for item in root[1:]:
            if not isinstance(item, list) or len(item) < 3:
                continue

            if item[0] == "net":
                net_code = int(item[1]) if isinstance(item[1], int) else 0
                net_name = str(item[2]) if len(item) > 2 else f"Net{net_code}"

                self.net_codes_seen.add(net_code)

                net = Net(
                    net_code=net_code,
                    name=net_name,
                    net_class_name="Default",
                )
                self.board.nets[net_code] = net
                net_index += 1

        logger.debug(f"Extracted {net_index} nets")

    def _extract_footprints(self, root: list) -> None:
        """Extract footprints and their pads from (footprint ...) sections."""
        footprint_index = 0
        for item in root[1:]:
            if not isinstance(item, list) or len(item) < 2:
                continue

            if item[0] == "footprint":
                self._process_footprint(item)
                footprint_index += 1

        logger.debug(f"Extracted {footprint_index} footprints")

    def _process_footprint(self, footprint_list: list) -> None:
        """Process a single footprint: extract reference, position, and pads."""
        # Structure: (footprint "name" ... (at X Y [angle]) ... (pad ...) ...)

        # Get component reference from properties
        ref = self._find_property_value(footprint_list, "Reference")
        if not ref:
            ref = f"U{len(self.board.components) + 1}"

        value = self._find_property_value(footprint_list, "Value")
        if not value:
            value = ""

        # Get footprint position
        at_item = self._find_list_with_head(footprint_list, "at")
        if not at_item or len(at_item) < 3:
            logger.warning(f"Footprint {ref} has no valid position; skipping")
            return

        # Position is in millimeters in KiCad; convert to nanometers
        fp_x_mm = self._to_number(at_item[1])
        fp_y_mm = self._to_number(at_item[2])
        fp_x_nm = int(fp_x_mm * 1_000_000)
        fp_y_nm = int(fp_y_mm * 1_000_000)
        fp_pos = IntPoint(fp_x_nm, fp_y_nm)

        # Get rotation if present
        fp_rotation = 0.0
        if len(at_item) > 3:
            fp_rotation = self._to_number(at_item[3])

        # Create component
        comp_id = self.board._id_gen.next_id()
        component = Component(
            id=comp_id,
            reference=str(ref),
            value=str(value),
            position=fp_pos,
            rotation_deg=fp_rotation,
            is_on_front=True,
            pad_ids=[],
        )

        # Extract pads
        pad_index = 0
        for subitem in footprint_list[1:]:
            if isinstance(subitem, list) and len(subitem) > 0:
                if subitem[0] == "pad":
                    pad_id = self._process_pad(
                        subitem,
                        fp_pos,
                        ref,
                        comp_id,
                    )
                    if pad_id:
                        component.pad_ids.append(pad_id)
                        pad_index += 1

        self.board.components[comp_id] = component
        logger.debug(f"Component {ref}: {pad_index} pads at ({fp_x_mm}, {fp_y_mm})")

    def _process_pad(
        self,
        pad_list: list,
        footprint_pos: IntPoint,
        component_ref: str,
        component_id: int,
    ) -> int | None:
        """Process a single pad within a footprint.

        Structure: (pad "1" smd rect (at X Y) (size W H) (layers ...) (net N "name"))
        Pad positions are relative to footprint; we convert to absolute.
        """
        if len(pad_list) < 3:
            return None

        pad_name = str(pad_list[1])
        pad_type = pad_list[2] if len(pad_list) > 2 else "smd"  # smd or thru
        pad_shape_str = pad_list[3] if len(pad_list) > 3 else "rect"  # rect, circle, oval

        # Extract position (relative to footprint)
        at_item = self._find_list_with_head(pad_list, "at")
        if not at_item or len(at_item) < 3:
            logger.debug(f"Pad {pad_name} has no valid position")
            return None

        pad_x_mm = self._to_number(at_item[1])
        pad_y_mm = self._to_number(at_item[2])

        # Convert to absolute position in nanometers
        abs_x_nm = footprint_pos.x + int(pad_x_mm * 1_000_000)
        abs_y_nm = footprint_pos.y + int(pad_y_mm * 1_000_000)
        pad_pos = IntPoint(abs_x_nm, abs_y_nm)

        # Extract size
        size_item = self._find_list_with_head(pad_list, "size")
        if not size_item or len(size_item) < 3:
            logger.debug(f"Pad {pad_name} has no valid size")
            return None

        size_w_mm = self._to_number(size_item[1])
        size_h_mm = self._to_number(size_item[2])
        size_w_nm = int(size_w_mm * 1_000_000)
        size_h_nm = int(size_h_mm * 1_000_000)

        # Map pad shape
        pad_shape = self._map_pad_shape(str(pad_shape_str))

        # Extract layers
        layers_item = self._find_list_with_head(pad_list, "layers")
        layer_indices = []
        if layers_item:
            for layer_name in layers_item[1:]:
                layer_idx = self.board.layer_structure.get_layer_index(str(layer_name))
                if layer_idx >= 0:
                    layer_indices.append(layer_idx)

        # Extract net
        net_item = self._find_list_with_head(pad_list, "net")
        net_code = 0
        if net_item and len(net_item) >= 2:
            net_code = int(net_item[1]) if isinstance(net_item[1], int) else 0

        # Ensure net exists
        if net_code > 0 and net_code not in self.board.nets:
            net_name = f"Net{net_code}"
            if net_item and len(net_item) > 2:
                net_name = str(net_item[2])
            self.board.nets[net_code] = Net(
                net_code=net_code,
                name=net_name,
                net_class_name="Default",
            )

        # Create pad
        pad = Pad(
            id=0,  # Will be assigned by board.add_item()
            net_codes=[net_code] if net_code > 0 else [],
            layer_indices=layer_indices if layer_indices else [0],  # Default to F.Cu
            fixed_state=FixedState.SYSTEM_FIXED,  # Pads don't move
            position=pad_pos,
            size_x=size_w_nm,
            size_y=size_h_nm,
            drill_diameter=0,  # SMD by default
            pad_shape=pad_shape,
            rotation_deg=0.0,
            pad_name=pad_name,
            component_ref=component_ref,
            component_id=component_id,
        )

        pad_id = self.board.add_item(pad)
        return pad_id

    def _extract_board_outline(self, root: list) -> None:
        """Extract board outline from graphics sections (gr_rect, gr_line on Edge.Cuts).

        For simplicity, we look for rectangular outlines and compute a bounding box.
        """
        min_x, min_y = float("inf"), float("inf")
        max_x, max_y = float("-inf"), float("-inf")

        for item in root[1:]:
            if not isinstance(item, list) or len(item) < 2:
                continue

            if item[0] == "gr_rect":
                start = self._find_list_with_head(item, "start")
                end = self._find_list_with_head(item, "end")
                layer = self._find_list_with_head(item, "layer")

                # Only use Edge.Cuts layer outlines
                if not layer or str(layer[1]) != "Edge.Cuts":
                    continue

                if start and len(start) >= 3 and end and len(end) >= 3:
                    x1_mm = self._to_number(start[1])
                    y1_mm = self._to_number(start[2])
                    x2_mm = self._to_number(end[1])
                    y2_mm = self._to_number(end[2])

                    x1_nm = int(x1_mm * 1_000_000)
                    y1_nm = int(y1_mm * 1_000_000)
                    x2_nm = int(x2_mm * 1_000_000)
                    y2_nm = int(y2_mm * 1_000_000)

                    min_x = min(min_x, x1_nm, x2_nm)
                    max_x = max(max_x, x1_nm, x2_nm)
                    min_y = min(min_y, y1_nm, y2_nm)
                    max_y = max(max_y, y1_nm, y2_nm)

        # If we found an outline, use it; otherwise create a reasonable default
        if min_x != float("inf"):
            self.board.bounding_box = BoundingBox(min_x, min_y, max_x, max_y)
            logger.debug(
                f"Board outline: ({min_x}, {min_y}) to ({max_x}, {max_y}) nm"
            )
        else:
            # Default: 100mm x 100mm
            self.board.bounding_box = BoundingBox(0, 0, 100_000_000, 100_000_000)
            logger.warning("No board outline found; using default 100x100mm")

    def _find_section(self, root: list, section_name: str) -> list | None:
        """Find a top-level section by name. Returns the section list or None."""
        for item in root[1:]:
            if isinstance(item, list) and len(item) > 0:
                if item[0] == section_name:
                    return item
        return None

    def _find_list_with_head(self, container: list, head: str) -> list | None:
        """Find the first sublist starting with 'head' in a container."""
        for item in container:
            if isinstance(item, list) and len(item) > 0 and item[0] == head:
                return item
        return None

    def _find_property_value(self, container: list, prop_name: str) -> str | None:
        """Find a property value by name. Structure: (property "name" "value")"""
        for item in container:
            if (
                isinstance(item, list)
                and len(item) >= 3
                and item[0] == "property"
                and str(item[1]) == prop_name
            ):
                return str(item[2])
        return None

    def _map_pad_shape(self, shape_str: str) -> PadShape:
        """Map KiCad pad shape string to PadShape enum."""
        shape_lower = shape_str.lower()
        if shape_lower == "circle":
            return PadShape.CIRCLE
        elif shape_lower == "rect":
            return PadShape.RECTANGLE
        elif shape_lower == "oval":
            return PadShape.OVAL
        elif shape_lower == "roundrect":
            return PadShape.ROUNDRECT
        elif shape_lower == "trapezoid":
            return PadShape.TRAPEZOID
        else:
            return PadShape.CUSTOM

    @staticmethod
    def _to_number(val: Any) -> float:
        """Convert a value to a number (int or float)."""
        if isinstance(val, (int, float)):
            return float(val)
        try:
            return float(str(val))
        except (ValueError, TypeError):
            return 0.0
