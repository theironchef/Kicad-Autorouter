"""
kicad-autorouter — Pure Python Autorouter Plugin for KiCad

Derived from the Freerouting Java autorouter (https://github.com/freerouting/freerouting),
forked as of 2026-03-30. Reimplemented in Python as a native KiCad Action Plugin.
Provides maze-based autorouting with multi-pass ripup-and-reroute optimization.

Architecture Overview:
    geometry/       - 2D geometric primitives (points, vectors, shapes, lines)
    board/          - PCB board model (items, traces, vias, nets, layers)
    rules/          - Design rules and clearance constraints
    datastructures/ - Spatial search trees and priority queues
    autoroute/      - Core autorouting engine (maze search, expansion rooms)
    optimize/       - Post-route optimization (pull-tight, via reduction)
    io/             - KiCad pcbnew API bridge (read/write board data)
    utils/          - Coordinate transforms, ID generation, helpers

License: GPL-3.0 (inherited from Freerouting)
"""

__version__ = "0.1.0"
__author__ = "Dan Willis"
