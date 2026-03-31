"""
Net and NetClass definitions.

Net       - A logical electrical net connecting pads
NetClass  - A group of nets sharing design rules (clearance, trace width, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Net:
    """A logical electrical net."""

    net_code: int          # Unique net identifier (matches KiCad net code)
    name: str              # Net name (e.g., "GND", "VCC", "Net-(U1-PA0)")
    net_class_name: str = "Default"  # Name of the NetClass this net belongs to

    # Routing state
    is_routed: bool = False

    def is_power(self) -> bool:
        """Heuristic: detect power/ground nets by name."""
        upper = self.name.upper()
        return any(kw in upper for kw in ("GND", "VCC", "VDD", "VSS", "PWR", "+3V", "+5V", "+12V"))


@dataclass
class NetClass:
    """Design rules shared by a group of nets.

    Defines clearance, trace width, via dimensions, and other routing
    constraints for all nets in this class.
    """

    name: str
    description: str = ""

    # Routing constraints (in nanometers, matching KiCad internal units)
    clearance: int = 200_000          # 0.2mm default clearance
    track_width: int = 250_000        # 0.25mm default track width
    via_diameter: int = 800_000       # 0.8mm default via diameter
    via_drill: int = 400_000          # 0.4mm default via drill
    uvia_diameter: int = 300_000      # Micro via diameter
    uvia_drill: int = 100_000         # Micro via drill
    diff_pair_width: int = 250_000    # Differential pair track width
    diff_pair_gap: int = 250_000      # Differential pair gap

    # Net membership
    net_names: list[str] = field(default_factory=list)

    def get_half_clearance(self) -> int:
        """Half the clearance value (used in offset calculations)."""
        return self.clearance // 2

    def get_trace_half_width(self) -> int:
        return self.track_width // 2
